#!/usr/bin/env python3
"""Explicit local evidence adapter for bundled Skills; no model loop.

The only network access is `fetch`: one GET per signed storage.googleapis.com link that a Kabo tool
result returned, checked against the declared bytes and sha256 before anything is written.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import ssl
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

sys.dont_write_bytecode = True
SKILL = Path(__file__).resolve().parent.parent
MAX_BYTES = 32 * 1024 * 1024
# 一次产物下载的总量上限：单次最多 120 个 ref，转写与抽帧远小于此；超过就分批请求。
MAX_ARTIFACT_TOTAL = 4 * MAX_BYTES
PENDING = {"queued", "running", "pending", "in_progress", "accepted"}
# 下载交付：只认 Kabo 工具结果给出的 GCS 签名链接，只 GET、不跟随重定向。
# 这是 Skill 脚本里唯一的网络访问（Kabo Skills 规范 KS7.3）。
DOWNLOAD_HOST = "storage.googleapis.com"
DOWNLOAD_PREFIX = "https://" + DOWNLOAD_HOST + "/"
DOWNLOAD_TIMEOUT = 60
# 抽帧请求的帧数上限，由 job-call 补进入参；空表示不补。
FRAME_CAPS = {}
# 产物下载声明的类型闭集 → 落盘扩展名。文本与 stage 的内联文本一样落成 .txt：.json 会被
# persist_envelope 当成 envelope 候选读入。闭集之外（含 application/octet-stream）不落盘。
ARTIFACT_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp",
                  "text/vtt": ".txt", "application/json": ".txt"}
ARTIFACT_REF_META = "kabo.sh/object_ref"
# 同步数据结果的链接过期后按 evidence_ref 重签：读回库里那份 envelope，不重跑、不占额度。
RESIGN_TOOL = "get_research_data_download"


# 本 Skill 用到的数据操作与 /mcp-pub 工具名一一对应；只接受这些工具的结果作为证据。
CONNECTOR_TOOLS = {
    "youtube-public/get_video": "youtube_get_video",
    "youtube-public/search_videos": "youtube_search_videos",
}
# 数据工具返回延迟任务时，任务终态由这个工具返回，同样可以保存为证据。
# 本 Skill 不读任务产物字节（逐字稿、帧），所以不接受 read_research_data_artifacts。
JOB_TOOLS = {"get_research_data_job"}
# 长窗口扫描的采集与补全是聚合工具：直接传字段，结果是带 schema_version 的类型化结果。
AGGREGATE_TOOLS = {"collect_youtube_trend_candidates", "enrich_youtube_shortlist"}
# 聚合工具出参（内联与下载那份同形）的 schema_version；按它判形状，不按 connector_id。
AGGREGATE_SCHEMAS = {"collect_youtube_trend_candidates": "youtube-trend-candidate-set.v1", "enrich_youtube_shortlist": "youtube-trend-enrichment.v1"}
# 本 Skill 没有返回固定投影的业务级工具；共用的过期提示据此决定能否重签。
PROJECTIONS: dict[str, str] = {}


def run_dir():
    raw = os.environ.get("KABO_STATIC_RUN_DIR")
    if raw:
        run = Path(raw).resolve(strict=True)
    else:
        # 宿主不一定在命令之间保留环境变量；命令都在运行目录内执行，从当前目录向上找本次运行的标记。
        here = Path.cwd().resolve()
        run = next((path for path in (here, *here.parents) if (path / ".static-run.json").is_file()), None)
        if run is None:
            raise ValueError("Set KABO_STATIC_RUN_DIR to this request's reserved directory, or run the command from inside it")
    if not (run / ".static-run.json").is_file():
        raise ValueError("Not a reserved static Skill run")
    return run


def require_target(target):
    run = run_dir()
    target = Path(target).resolve()
    if run not in target.parents:
        raise ValueError("Evidence destination must belong to the current run")
    return target


def public_calls(call):
    # drive.py 仍按 connector/operation 组织调用；交给模型前逐个换成 /mcp-pub 的具名工具，
    # 批量请求拆成逐个调用；聚合工具原样交出，其余未声明的工具（包括注册表工具）一律拒绝。
    tool, args = call["tool"], call["arguments"]
    if tool == "data_connector_batch_run":
        return [mapped for request in args["requests"] for mapped in public_calls(
            {"tool": "data_connector_run", "arguments": request})]
    if tool == "data_connector_run":
        key = args["connector_id"] + "/" + args["operation"]
        name = CONNECTOR_TOOLS.get(key)
        if name is None:
            raise ValueError("No reviewed public MCP tool for " + key)
        return [{"tool": name, "arguments": {"parameters": args["params"], "delivery": "download"}}]
    if tool not in AGGREGATE_TOOLS:
        raise ValueError("Unsupported public MCP operation: " + tool)
    if tool in AGGREGATE_SCHEMAS:
        # 聚合工具的 delivery 只多给一份供脚本下载的完整副本，items 照旧内联；
        # 宿主截断内联结果时，脚本靠这份副本拿到完整数据。
        return [{"tool": tool, "arguments": {**args, "delivery": "download"}}]
    return [call]


def init(workspace):
    base = Path(workspace).resolve(strict=True) / ".kabo-runs"
    if SKILL == base or SKILL in base.parents:
        raise ValueError("Runs must be outside the installed Skill")
    base.mkdir(mode=0o700, exist_ok=True)
    name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + secrets.token_hex(6)
    run = base / name
    run.mkdir(mode=0o700)
    # host-results/ 放宿主导出的原始工具结果，它们是 fetch / stage 的输入；与放置写出的 envelope 分开，
    # persist_envelope.py 统计已放置时不读这里。
    for name in ("snapshot", "analysis", "report", "owner", "host-results"):
        (run / name).mkdir(mode=0o700)
    (run / "snapshot/.host-evidence").mkdir(mode=0o700)
    write_new(run / ".static-run.json", json.dumps({"format": "kabo.static-run.v1", "skill": SKILL.name}).encode())
    return run


def write_new(path, body):
    # 独占写入，重试和并发不能覆盖已有证据。
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(body)


def complete_result(tool, raw, extra_blocks=False):
    # stage 与 fetch 共用的整份结果读取：大小、工具名、MCP 包装、错误结果与证据对象的定位。
    if len(raw) > MAX_BYTES:
        raise ValueError("Tool result exceeds the evidence limit")
    if tool not in {RESIGN_TOOL} | set(CONNECTOR_TOOLS.values()) | JOB_TOOLS | AGGREGATE_TOOLS:
        raise ValueError("Not a declared evidence tool")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Complete MCP result must be an object")
    if value.get("isError") and "structuredContent" not in value:
        # 纯文本错误不是 JSON；原样带出工具的报错，模型才能分清输入错误与暂时故障。
        blocks = value.get("content") if isinstance(value.get("content"), list) else []
        text = " ".join(block["text"] for block in blocks
                        if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)).strip()
        try:
            json.loads(text)
        except ValueError:
            raise ValueError("MCP error contains no terminal connector evidence: " + (text[:300] or "no message")) from None
    structured = value.get("structuredContent", value)
    if "content" in value and "structuredContent" not in value:
        blocks = value["content"]
        # 下载模式的产物结果在 JSON 文本之后可能跟着回退正文块，只有 fetch 认得它们。
        if (not isinstance(blocks, list) or not blocks or (len(blocks) != 1 and not extra_blocks) or
                not isinstance(blocks[0], dict) or blocks[0].get("type") != "text" or
                not isinstance(blocks[0].get("text"), str)):
            raise ValueError("Complete structured result is required")
        structured = json.loads(blocks[0]["text"])
    if not isinstance(structured, dict):
        raise ValueError("Complete structured result is required")
    # 任务未到终局时 envelope 为 null；先给出轮询提示，不报「证据对象不完整」。
    if structured.get("envelope", {}) is None and structured.get("status") in PENDING:
        raise ValueError("Poll the data job before staging terminal evidence")
    envelope = structured["envelope"] if "envelope" in structured else structured.get("result", structured)
    if not isinstance(envelope, dict):
        raise ValueError("Complete evidence object is required")
    if value.get("isError") and not (envelope.get("status") == "failed" and
                                    isinstance(envelope.get("connector_id"), str) and
                                    isinstance(envelope.get("operation"), str) and
                                    isinstance(envelope.get("limitations"), list)):
        raise ValueError("MCP error contains no terminal connector evidence")
    return value, structured, envelope


def check_envelope(tool, envelope):
    # 仍在排队或运行的任务先给出正确提示，不要被下面的操作名或字段检查抢先拦下。
    if envelope.get("status") in PENDING:
        raise ValueError("Poll the data job before staging terminal evidence")
    operation = next((key for key, name in CONNECTOR_TOOLS.items() if name == tool), None)
    if operation:
        connector, expected = operation.split("/")
        actual = envelope.get("connector_id", "") + "/" + envelope.get("operation", "")
        # 抽帧允许提供方返回显式声明的计费身份；不能放宽到任意媒体操作。
        aliases = {"public-video-media/extract_keyframes_hosted_media", "public-video-media/extract_keyframes_hosted_stream"}
        if actual != operation and not (operation == "public-video-media/extract_keyframes" and actual in aliases):
            raise ValueError("Tool result does not match its declared operation")
        if not isinstance(envelope.get("limitations"), list) or not isinstance(envelope.get("status"), str):
            raise ValueError("Incomplete connector envelope")
    if tool == RESIGN_TOOL:
        # 重签读回的是那次调用自己记下的 envelope；只认本 Skill 声明的数据操作。
        actual = envelope.get("connector_id", "") + "/" + envelope.get("operation", "")
        if actual not in CONNECTOR_TOOLS:
            raise ValueError("Re-signed result is not from one of this Skill's data tools")
        if not isinstance(envelope.get("limitations"), list) or not isinstance(envelope.get("status"), str):
            raise ValueError("Incomplete connector envelope")
    if tool == "get_research_data_job":
        actual = envelope.get("connector_id", "") + "/" + envelope.get("operation", "")
        if actual not in CONNECTOR_TOOLS:
            raise ValueError("Job returned an undeclared data operation")
    if tool in AGGREGATE_SCHEMAS and envelope.get("schema_version") != AGGREGATE_SCHEMAS[tool]:
        raise ValueError("Tool result does not carry its declared schema")
    if not operation and tool not in ("get_research_data_job", RESIGN_TOOL) and not envelope.get("schema_version") and tool != "read_research_data_artifacts":
        raise ValueError("Tool result has no supported evidence schema")
    data = envelope.get("data")
    if isinstance(data, dict) and isinstance(data.get("rows"), list):
        import re
        claimed = re.search(r"\bFound\s+(\d+)\b", str(envelope.get("summary", "")), re.I)
        if claimed and int(claimed[1]) > 0 and not data["rows"]:
            raise ValueError("Truncated evidence has no claimed rows")


def evidence_id():
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f") + secrets.token_hex(6)


def block_body(artifact, block):
    # 内联产物正文（stage 的整份结果，或下载模式里标了 object_ref 的回退块）解码并定扩展名。
    body_key = "text" if block["type"] == "text" else "data"
    if not isinstance(block.get(body_key), str):
        raise ValueError("Artifact body must be a string")
    body = (block["text"].encode() if block["type"] == "text"
            else base64.b64decode(block["data"], validate=True))
    check_artifact(artifact, body)
    extension = ".txt"
    if block["type"] == "image":
        mime = artifact.get("content_type")
        extension = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}.get(mime)
        if extension is None or block.get("mimeType") != mime:
            raise ValueError("Unsupported or mismatched image content type")
    return body, extension


def check_artifact(artifact, body):
    if hashlib.sha256(body).hexdigest() != artifact.get("sha256") or artifact.get("bytes", len(body)) != len(body):
        raise ValueError("Artifact checksum mismatch")


def artifact_meta(artifact, job_id, **extra):
    # 下载描述含签名链接，不进旁注文件；其余登记字段（object_ref、sha256、bytes…）照旧保留。
    return {**{key: item for key, item in artifact.items() if key != "download"}, "job_id": job_id, **extra}


def store(tool, envelope, payloads):
    destination = run_dir() / "snapshot/.host-evidence"
    require_target(destination / "pending.json")
    if len(payloads) == 1 and payloads[0][0].endswith(".json"):
        fingerprint = hashlib.sha256(payloads[0][1]).hexdigest()
        for previous in destination.glob("*.meta"):
            metadata = json.loads(previous.read_text())
            if metadata.get("tool") == tool and metadata.get("sha256") == fingerprint:
                if hashlib.sha256(previous.with_suffix(".json").read_bytes()).hexdigest() != fingerprint:
                    raise ValueError("Previously staged evidence was altered")
                return {"staged": 0, "duplicate": True, "directory": str(destination)}
    for name, body, extra in payloads:
        target = destination / name
        meta = {**extra, "sha256": hashlib.sha256(body).hexdigest(), "tool": tool,
                "staged_at": datetime.now(timezone.utc).isoformat()}
        if "connector_id" in envelope:
            meta.update(connector_id=envelope["connector_id"], operation=envelope["operation"])
        elif "schema_version" in envelope:
            meta["schema_version"] = envelope["schema_version"]
        write_new(target, body)
        write_new(target.with_suffix(".meta"), json.dumps(meta).encode())
    return {"staged": len(payloads), "directory": str(destination)}


# 产物在下载模式下没有可用文件时的唯一出路；fetch 与 stage 说同一句话，不互相指回对方。
# 宿主会把内联图片另存进它自己的会话目录（Claude Code 2.1.308 实测），那不是可依赖的接口，明说不要去拼。
REREAD_INLINE = ("call read_research_data_artifacts again for those object_refs without delivery, export the complete "
                 "result through the host and run static_host.py stage on it; never rebuild it from the host's own "
                 "session or tool-result folders")
REREAD = ("Items marked not_found are missing evidence: record them as gaps. For the other unavailable items, "
          + REREAD_INLINE)


def artifact_downloads(structured):
    artifacts = structured.get("artifacts")
    return [item["download"] for item in artifacts if isinstance(item, dict) and "download" in item] if isinstance(artifacts, list) else []


def typed_result(envelope):
    # 聚合工具（collect_*、enrich_*）的类型化出参：带 schema_version、不带 connector_id。它的下载只是给
    # 脚本的完整副本，items 照旧完整内联，所以内联那份同样可以 stage。
    return "connector_id" not in envelope and isinstance(envelope.get("schema_version"), str)


def download_mode(structured):
    # 只有确实有就绪链接时才要求 fetch；全部不可用时 fetch 也拿不到文件，要求它会让两条命令互相指回。
    return any(isinstance(item, dict) and item.get("status") == "ready"
               for item in [structured.get("download"), *artifact_downloads(structured)])


def stage(tool, raw):
    value, structured, envelope = complete_result(tool, raw)
    # 下载就绪的结果省略了 data；当作内联证据保存会得到一份没有数据的 envelope。聚合工具例外：内联照旧完整。
    if download_mode(structured) and not typed_result(envelope):
        raise ValueError("This result carries a download instead of inline data; run static_host.py fetch on it")
    # 宿主保存时丢了 download、只剩摘要（status 完成却没有 data）：入库后要到后面的脚本才报错，而且这一轮已算放置，
    # 补下载的完整结果也不会再被读到（插件实测 TikTok 榜单）。在这里拒收，指回完整结果。
    if (envelope.get("status") in ("completed", "completed_partial") and "data" not in envelope
            and not typed_result(envelope) and tool != "read_research_data_artifacts"):
        raise ValueError("This result has a completed status but no data: it is a summary of a download, not the "
                         "evidence. Save the tool's complete result, including its download, and run "
                         "static_host.py fetch on it")
    if typed_result(envelope):
        # 下载描述含签名链接，不落盘；去掉它之后与下载到的那份是同一个对象。
        envelope = {key: item for key, item in envelope.items() if key != "download"}
    # 下载模式的产物结果不带内联正文（回退块只有 fetch 认得）；没有就绪链接时指向内联重读。
    if artifact_downloads(structured):
        raise ValueError("This artifact result has no ready download and no inline bodies to stage. " + REREAD)
    check_envelope(tool, envelope)
    identifier = evidence_id()
    payloads = []
    if tool == "read_research_data_artifacts":
        artifacts = structured.get("artifacts", [])
        raw_blocks = value.get("content", [])
        if (not isinstance(artifacts, list) or not isinstance(raw_blocks, list) or
                not all(isinstance(item, dict) for item in artifacts) or
                not all(isinstance(item, dict) for item in raw_blocks)):
            raise ValueError("Artifact bodies and metadata must be complete and paired")
        blocks = [item for item in raw_blocks if item.get("type") in {"text", "image"}]
        if not artifacts or len(artifacts) != len(blocks) or not structured.get("job_id"):
            raise ValueError("Artifact bodies and metadata must be complete and paired")
        for index, (artifact, block) in enumerate(zip(artifacts, blocks)):
            body, extension = block_body(artifact, block)
            payloads.append((f"{identifier}-{index}{extension}", body, artifact_meta(artifact, structured["job_id"])))
    else:
        payloads.append((identifier + ".json", json.dumps(envelope, ensure_ascii=False).encode(), {}))
    return store(tool, envelope, payloads)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    # 签名链接只指向一个对象；任何 3xx 都按失败处理，不让链接把脚本带到别的主机。
    def redirect_request(self, *args, **kwargs):
        return None


def hashed_capath(path):
    # OpenSSL 只从目录里读 <8 位十六进制>.<数字> 形式的证书链接；目录非空但没有这类文件时等于没有信任库。
    try:
        with os.scandir(path) as entries:
            return any(re.fullmatch(r"[0-9a-f]{8}\.\d+", entry.name) for entry in entries)
    except (OSError, TypeError):
        return False


def tls_context():
    # macOS 上 python.org 装的 Python 没跑过 Install Certificates 时默认信任库是空的，每个 HTTPS 下载都报证书失败
    # （插件实测 T-20261028-04）。只在默认信任库确实不存在时换用 certifi 或系统 CA 包，证书照常校验；
    # 某个候选读不了或格式不对就试下一个。
    # Windows 的默认上下文读系统证书库，文件路径为空不代表没有信任库，保持默认。
    if sys.platform == "win32":
        return None
    paths = ssl.get_default_verify_paths()
    if (paths.cafile and os.path.isfile(paths.cafile)) or hashed_capath(paths.capath):
        return None
    candidates = []
    try:
        import certifi
        candidates.append(certifi.where())
    except Exception:
        pass
    candidates += ["/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt"]
    for path in candidates:
        if not os.path.isfile(path):
            continue
        try:
            return ssl.create_default_context(cafile=path)
        except (OSError, ssl.SSLError):
            continue
    return None


def build_opener():
    # 测试在进程内替换这个函数以注入假的 HTTPS 处理器；命令行没有入口能改它。
    context = tls_context()
    handlers = [NoRedirect()] + ([urllib.request.HTTPSHandler(context=context)] if context else [])
    return urllib.request.build_opener(*handlers)


def renew_hint(tool, envelope):
    # 链接过期后的三条路：任务与产物再读一次即可；同步数据结果按 evidence_ref 重签，
    # 不重跑、不占额度，新链接的 sha256 与 bytes 可能和旧的不同，只按新返回的值校验；聚合工具的内联结果本来完整。
    if tool in JOB_TOOLS:
        return f"Call {tool} again with delivery \"download\" (it only reads; nothing is re-run), then run fetch right away"
    if typed_result(envelope):
        return ("This tool's inline result is complete: export it through the host and run static_host.py stage "
                f"--tool {tool} on it (calling the tool again would re-run it)")
    original = tool
    if tool == RESIGN_TOOL:
        # 重签工具的入参只有 evidence_ref（additionalProperties: false），带 delivery 再调它会被拒、也不重跑任何东西；
        # 最后的退路是信封里实际作答路线所属的原数据工具。ROUTE_ALIASES 只在部分副本里有，共享段不能直接引用。
        actual = f"{envelope.get('connector_id', '')}/{envelope.get('operation', '')}"
        aliases = globals().get("ROUTE_ALIASES", {})
        original = next((name for key, name in CONNECTOR_TOOLS.items()
                         if actual == key or actual in aliases.get(key, ())), "the original data tool")
    ref = envelope.get("evidence_ref")
    # 投影工具重签拿回的是作答路线的原始信封、不是投影，只能再调一次原工具。
    if isinstance(ref, str) and ref and original not in PROJECTIONS:
        return ("Call get_research_data_download with {\"evidence_ref\": " + json.dumps(ref) + "} (no allowance cost; "
                "nothing is re-run), then run fetch --tool get_research_data_download on its complete result right "
                "away; it checks the new sha256 and bytes, which can differ from this link's. If get_research_data_download "
                f"itself fails, call {original} again with the same parameters and delivery \"download\" (this re-runs it), "
                "then run fetch right away")
    return (f"Call {original} again with the same parameters and delivery \"download\" (for a data tool this re-runs "
            "it, the only way to get a new link), then run fetch right away")


def expired(descriptor):
    # expires_at 已过就不必再发请求：结论与 GCS 回 400/403 相同。时间格式不对时交给下载本身判断。
    try:
        stamp = datetime.fromisoformat(str(descriptor.get("expires_at")).replace("Z", "+00:00"))
    except ValueError:
        return False
    return stamp.tzinfo is not None and stamp <= datetime.now(timezone.utc)


def download(tool, descriptor, kind, hint):
    """GET one Kabo-issued signed link; return its bytes only if size, sha256 and content type all match."""
    url, digest, size = descriptor.get("url"), descriptor.get("sha256"), descriptor.get("bytes")
    if (descriptor.get("kind") != kind or not isinstance(url, str) or not isinstance(digest, str) or
            len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest) or
            isinstance(size, bool) or not isinstance(size, int) or not 0 <= size <= MAX_BYTES):
        raise ValueError("Download descriptor is incomplete or over the evidence limit")
    if expired(descriptor):
        raise ValueError("Download link expired (expires_at has passed). " + hint)
    # 只认 Kabo 工具结果给出的 GCS 签名链接：协议与主机逐字匹配（不带端口、用户名），不含空白或控制字符。
    if (not url.startswith(DOWNLOAD_PREFIX) or urlsplit(url).netloc != DOWNLOAD_HOST or
            any(ord(char) <= 32 or ord(char) == 127 for char in url)):
        raise ValueError("Download link is not a Kabo signed storage link; refusing to fetch it")
    request = urllib.request.Request(url, method="GET", headers={"Accept-Encoding": "identity"})
    # 报错只说类别与状态码：异常文本可能带链接、签名或对象名，一律不转述。
    hasher, chunks, total = hashlib.sha256(), [], 0
    try:
        with build_opener().open(request, timeout=DOWNLOAD_TIMEOUT) as response:
            status = response.status
            # 签名链接按声明的类型签发（response-content-type），GCS 原样回这个头；对不上说明拿到的不是那件产物。
            # 没有这个头时不拦：大小与 sha256 已经钉住字节。
            served = response.headers.get("Content-Type") if status == 200 else None
            if served and served.split(";")[0].strip().lower() != str(descriptor.get("content_type")).lower():
                status = "type"
            while status == 200 and total <= size:
                chunk = response.read(min(1024 * 1024, size + 1 - total))
                if not chunk:
                    break
                hasher.update(chunk)
                chunks.append(chunk)
                total += len(chunk)
    except urllib.error.HTTPError as error:
        status = error.code
        error.close()
    except Exception as error:
        # 下载模式的产物结果 stage 不收（它会指回 fetch）；脚本不能联网时产物只能内联重读，否则两条命令互相指回。
        fallback = (REREAD_INLINE if kind == "artifact" else
                    "export the complete tool result through the host and run static_host.py stage instead")
        raise ValueError(f"Download failed ({type(error).__name__}); if this host blocks network access for scripts, "
                         + fallback) from None
    if status == "type":
        raise ValueError("Download content type does not match the declared content_type; nothing was written")
    if 300 <= status < 400:
        raise ValueError(f"Download was redirected (HTTP {status}); redirects are not followed")
    if status in (400, 401, 403):
        raise ValueError(f"Download link expired or was refused (HTTP {status}). " + hint)
    if status != 200:
        raise ValueError(f"Download failed (HTTP {status}). " + hint)
    if total != size:
        raise ValueError("Download size does not match the declared bytes; nothing was written")
    if hasher.hexdigest() != digest:
        raise ValueError("Download sha256 does not match the declared value; nothing was written")
    return b"".join(chunks)


def summary(descriptor, status="fetched"):
    return {"kind": descriptor.get("kind"), "sha256": descriptor.get("sha256"), "bytes": descriptor.get("bytes"),
            "status": status}


def no_download(tool, descriptors):
    reasons = sorted({str(item.get("reason")) for item in descriptors if isinstance(item, dict) and item.get("reason")})
    return {"fetched": 0, "download": reasons or "absent",
            "next": ("No ready download in this result. Fall back: export the complete tool result through the host "
                     f"and run static_host.py stage --tool {tool} --input <file>")}


def fetch(tool, raw):
    """Download the complete evidence a Kabo tool result links to, then store it the way stage does."""
    value, structured, envelope = complete_result(tool, raw, extra_blocks=True)
    if tool == "read_research_data_artifacts":
        return fetch_artifacts(tool, value, structured)
    descriptor = structured.get("download")
    if not (isinstance(descriptor, dict) and descriptor.get("status") == "ready"):
        if envelope.get("status") in PENDING:
            raise ValueError("Poll the data job before staging terminal evidence")
        return no_download(tool, [descriptor])
    if descriptor.get("content_type") != "application/json":
        raise ValueError("Envelope download must be JSON")
    full = json.loads(download(tool, descriptor, "envelope", renew_hint(tool, envelope)).decode("utf-8"))
    if not isinstance(full, dict):
        raise ValueError("Downloaded evidence must be an object")
    # 下载的是本次结果那一份 envelope（只多了 data），聚合工具则是去掉 download 的整份出参；
    # 身份字段对不上说明拿错了文件。
    for key in ("connector_id", "operation", "status", "evidence_ref", "raw_sha256", "schema_version"):
        if key in envelope and full.get(key) != envelope[key]:
            raise ValueError("Downloaded evidence does not match this tool result")
    check_envelope(tool, full)
    body = json.dumps(full, ensure_ascii=False).encode()
    result = store(tool, full, [(evidence_id() + ".json", body, {"delivery": "download"})])
    return {**result, "fetched": [summary(descriptor)]}


def fetch_artifacts(tool, value, structured):
    artifacts, job_id = structured.get("artifacts"), structured.get("job_id")
    if (not isinstance(artifacts, list) or not artifacts or not isinstance(job_id, str) or not job_id or
            not all(isinstance(item, dict) for item in artifacts)):
        raise ValueError("Artifact metadata must be complete")
    # 签名失败的项退回内联正文，按 _meta 里的 object_ref 配对，不按位置（回退读取可能逐项失败）。
    fallback = {}
    for block in value.get("content") if isinstance(value.get("content"), list) else []:
        meta = block.get("_meta") if isinstance(block, dict) else None
        ref = meta.get(ARTIFACT_REF_META) if isinstance(meta, dict) else None
        if isinstance(ref, str) and block.get("type") in {"text", "image"}:
            if ref in fallback:
                raise ValueError("Artifact bodies and metadata must be complete and paired")
            fallback[ref] = block
    identifier, payloads, fetched, missing, images, total = evidence_id(), [], [], [], [], 0
    # 先全部下载并校验，最后一次写入：任何一项对不上都不留下半套证据。
    for index, artifact in enumerate(artifacts):
        descriptor = artifact.get("download")
        if isinstance(descriptor, dict) and descriptor.get("status") == "ready":
            extension = ARTIFACT_TYPES.get(descriptor.get("content_type"))
            if extension is None:
                raise ValueError("Unsupported artifact content type")
            if (descriptor.get("sha256") != artifact.get("sha256") or descriptor.get("bytes") != artifact.get("bytes") or
                    descriptor.get("content_type") != artifact.get("content_type")):
                raise ValueError("Download does not match its artifact record")
            total += descriptor["bytes"] if isinstance(descriptor.get("bytes"), int) else 0
            if total > MAX_ARTIFACT_TOTAL:
                raise ValueError("Artifact downloads exceed the evidence limit; request fewer object_refs per call")
            body = download(tool, descriptor, "artifact", renew_hint(tool, structured))
            check_artifact(artifact, body)
            payloads.append((f"{identifier}-{index}{extension}", body, artifact_meta(artifact, job_id, delivery="download")))
            fetched.append({"index": index, "file": f"{identifier}-{index}{extension}", **summary(descriptor)})
            if extension != ".txt":
                images.append(index)
        elif artifact.get("object_ref") in fallback:
            body, extension = block_body(artifact, fallback[artifact["object_ref"]])
            payloads.append((f"{identifier}-{index}{extension}", body, artifact_meta(artifact, job_id)))
            fetched.append({"index": index, "file": f"{identifier}-{index}{extension}", "kind": "artifact",
                            "sha256": artifact.get("sha256"),
                            "bytes": artifact.get("bytes"), "status": "inline"})
            if extension != ".txt":
                images.append(index)
        else:
            reason = descriptor.get("reason") if isinstance(descriptor, dict) else None
            missing.append({"index": index, "status": "unavailable", "reason": reason or "absent"})
    if not payloads:
        if artifact_downloads(structured):
            return {"fetched": 0, "missing": missing, "next": "Nothing could be fetched. " + REREAD}
        return no_download(tool, [item.get("download") for item in artifacts])
    result = {**store(tool, structured, payloads), "fetched": fetched}
    # 下载下来的帧只在磁盘上，模型看不到像素；给出绝对路径，让模型用宿主自己的看图或读文件工具打开。
    for item in fetched:
        item["path"] = str(Path(result["directory"]) / item["file"])
    if images:
        result["view"] = ("Downloaded images are files, not yet seen: open each image's path with the host's own "
                          "file or image viewer before describing it")
    if missing:
        result["missing"] = missing
        result["next"] = "Items in missing have no file. " + REREAD
    return result


# 延迟媒体工具的幂等键：同一运行、同一工具、同一组入参、同一尝试序号得到同一把键。
# 宿主超时后整条重发时，服务端凭它交回原来的 job，不再入队、不再计费。只在同一调用没拿到响应时复用；拿到错误结果后
# 再试、入参改了或要重跑已终局的任务，都换下一个尝试序号（入队失败会留下一行永远不开始的 queued job，复用旧键会拿回它）。
KEY_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789:._-")
KEY_LIMIT = 128


def job_call(tool, raw_parameters, attempt):
    """The arguments of one deferred media call, with its idempotency_key beside `parameters`."""
    if not (tool.startswith("public_video_") and tool in CONNECTOR_TOOLS.values()):
        raise ValueError("Not a deferred media tool of this Skill: " + tool)
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
        raise ValueError("--attempt must be a positive integer")
    parameters = json.loads(raw_parameters)
    if not isinstance(parameters, dict):
        raise ValueError("--parameters must be a JSON object")
    cap = FRAME_CAPS.get(tool)
    if cap is not None:
        # 缺省时补上本 Skill 的帧数上限；显式传入只能更小。
        frames = parameters.setdefault("max_frames", cap)
        if isinstance(frames, bool) or not isinstance(frames, int) or not 1 <= frames <= cap:
            raise ValueError(f"max_frames must be an integer from 1 to {cap}")
    digest = hashlib.sha256(json.dumps(parameters, sort_keys=True, ensure_ascii=False,
                                       separators=(",", ":")).encode()).hexdigest()[:16]
    key = "".join(char if char in KEY_CHARS else "-" for char in f"{SKILL.name}:{run_dir().name}:{tool}:{digest}:a{attempt}")
    if len(key) > KEY_LIMIT:
        # 安装目录名过长时整体取摘要：仍然稳定，只是不再可读。
        key = "kabo:" + hashlib.sha256(key.encode()).hexdigest()[:48] + f":a{attempt}"
    return {"tool": tool, "arguments": {"parameters": parameters, "delivery": "download", "idempotency_key": key}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init").add_argument("--workspace", required=True)
    # 两个子命令都只从文件或 stdin 读完整结果；下载链接不进 argv，不出现在进程列表里。
    for name in ("stage", "fetch"):
        command = commands.add_parser(name)
        command.add_argument("--tool", required=True)
        command.add_argument("--input", default="-")
    command = commands.add_parser("job-call")
    command.add_argument("--tool", required=True)
    command.add_argument("--parameters", required=True)
    command.add_argument("--attempt", type=int, default=1)
    args = parser.parse_args()
    if args.command == "init":
        print(init(args.workspace))
    elif args.command == "job-call":
        print(json.dumps(job_call(args.tool, args.parameters, args.attempt), ensure_ascii=False))
    else:
        if args.input == "-":
            raw = sys.stdin.buffer.read(MAX_BYTES + 1)
        else:
            with open(args.input, "rb") as stream:
                raw = stream.read(MAX_BYTES + 1)
        print(json.dumps((stage if args.command == "stage" else fetch)(args.tool, raw)))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError, TypeError) as error:
        print("Static evidence operation failed: " + str(error), file=sys.stderr)
        sys.exit(1)
