#!/usr/bin/env python3
"""Local run adapter for the remake-viral-video Skill; no model loop.

Commands:
  init        reserve one run directory under <workspace>/.kabo-runs/
  start-call  check the inputs and print the start_remake_video arguments with a stable idempotency_key
  fetch       save the brief bundle, the final MP4 or (voice-over) the picture review that a
              get_remake_video / render_remake_video result links to, after checking host, size,
              sha256 and content type
  fetch-preview  save the pages of a voice-over preview (render_remake_video with preview)

The only network access is `fetch` / `fetch-preview`: one GET per signed storage.googleapis.com link that a Kabo tool
result returned, checked against the declared bytes and sha256 before anything is kept.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import ssl
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.dont_write_bytecode = True
SKILL = Path(__file__).resolve().parent.parent
# 工具结果本身很小（只有链接与摘要）；简报 JSON 远小于 32 MiB，成片按 60 秒短视频留足余量。
MAX_RESULT_BYTES = 4 * 1024 * 1024
MAX_BRIEF_BYTES = 32 * 1024 * 1024
MAX_VIDEO_BYTES = 512 * 1024 * 1024
# 旁白的预览页与逐句对照页：runner 每页压到 200 KB 以内，这里留余量
MAX_PAGE_BYTES = 1024 * 1024
# 下载交付：只认 Kabo 工具结果给出的 GCS 签名链接，只 GET、不跟随重定向（Kabo Skills 规范 KS7.3）。
DOWNLOAD_HOST = "storage.googleapis.com"
DOWNLOAD_PREFIX = "https://" + DOWNLOAD_HOST + "/"
DOWNLOAD_TIMEOUT = 120
BRIEF_SCHEMA = "kabo.remake-brief.v1"
# 简报包里必须有的四份文件（与网页端 status.brief 相同）。
BRIEF_FILES = ("work/brief_input.json", "work/script.draft.json", "work/decisions.draft.json",
               "work/route_b/jobs.template.json")
# 旁白没有口播首帧任务，简报里可以没有 jobs.template.json
NARRATION_OPTIONAL = ("work/route_b/jobs.template.json",)
# 旁白出片前看一次图时，简报换成当前稿件与逐句审片模板
REVIEW_FILES = ("work/decisions.json", "work/script.json",
                "production/review/beats/beat_review.template.json")
RESIGN_HINT = ("Call get_remake_video again with this job_id (reading re-signs the link; nothing is re-run), save "
               "its complete result to a new host-results file and run fetch on it right away")
# 签名失败时服务端给 {status: "unavailable", reason}；复刻没有内联回退，只能稍后再读一次重签，不要连着狂调。
UNAVAILABLE_HINTS = {
    "service_unavailable": ("Signing the download link failed for now. Wait about a minute, call get_remake_video "
                            "again with this job_id, save that result and run fetch on it. After three such tries, "
                            "say the file cannot be downloaded right now and that the job is preserved"),
    "not_found": ("The file this result points to was not found. Call get_remake_video once more; if it is still "
                  "not_found, report that the file is missing and that the job is preserved"),
    "too_large": "The file is too large to be signed for download; report this, the job is preserved",
}
RUN_DIRS = ("host-results", "brief", "script", "render", "output")

# 与 start_remake_video 的入参约束一致；服务端仍会再校验一次。
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}
INSTAGRAM_HOSTS = {"instagram.com", "www.instagram.com"}
TIKTOK_HOSTS = {"tiktok.com", "www.tiktok.com", "m.tiktok.com", "vm.tiktok.com", "vt.tiktok.com"}
CREATOR_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
LANGUAGE = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$")
HANDLE = re.compile(r"^@?[A-Za-z0-9._]{1,30}$")
KEY_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789:._-")
KEY_LIMIT = 128


def run_dir():
    raw = os.environ.get("KABO_STATIC_RUN_DIR")
    if raw:
        run = Path(raw).resolve(strict=True)
    else:
        # 宿主不一定在命令之间保留环境变量；从当前目录向上找本次运行的标记。
        here = Path.cwd().resolve()
        run = next((path for path in (here, *here.parents) if (path / ".static-run.json").is_file()), None)
        if run is None:
            raise ValueError("Set KABO_STATIC_RUN_DIR to this request's reserved directory, or run the command from inside it")
    if not (run / ".static-run.json").is_file():
        raise ValueError("Not a reserved static Skill run")
    return run


def init(workspace):
    base = Path(workspace).resolve(strict=True) / ".kabo-runs"
    if SKILL == base or SKILL in base.parents:
        raise ValueError("Runs must be outside the installed Skill")
    base.mkdir(mode=0o700, exist_ok=True)
    name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + secrets.token_hex(6)
    run = base / name
    run.mkdir(mode=0o700)
    # host-results/ 放宿主保存的完整工具结果（fetch 的输入）；brief/ 放下载的简报，script/ 放宿主写的四份文件，
    # render/ 放提交参数，output/ 放成片。
    for name in RUN_DIRS:
        (run / name).mkdir(mode=0o700)
    write_new(run / ".static-run.json", json.dumps({"format": "kabo.static-run.v1", "skill": SKILL.name}).encode())
    return run


def write_new(path, body):
    # 独占写入，重试和并发不能覆盖已保存的文件。
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(body)


# ---------------------------------------------------------------- start-call

def _url(raw, role):
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{role} link is empty")
    url = raw.strip()
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host in TIKTOK_HOSTS:
        raise ValueError(f"{role}: TikTok links are not supported by this version; use a YouTube or Instagram link")
    if parts.scheme != "https" or host not in YOUTUBE_HOSTS | INSTAGRAM_HOSTS or parts.username or parts.port:
        raise ValueError(f"{role} must be an https YouTube or Instagram video link")
    if not parts.path.strip("/"):
        raise ValueError(f"{role} must link to one video, not a home page")
    if any(ord(char) <= 32 or ord(char) == 127 for char in url):
        raise ValueError(f"{role} link contains spaces or control characters")
    return url


def _same_video(a, b):
    # 同一条视频不能同时当参考和创作者素材；按主机族、路径与 YouTube 的 v 参数比较。
    def key(url):
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        family = "youtube" if host in YOUTUBE_HOSTS else "instagram"
        path = parts.path.rstrip("/").lower()
        video = parse_qs(parts.query).get("v", [""])[0]
        if host == "youtu.be":
            video, path = path.strip("/"), ""
        elif path.startswith("/shorts/"):
            video, path = path.split("/")[2], ""
        return family, path, video
    return key(a) == key(b)


def start_call(reference, handle, video_urls, creator_name, language, attempt):
    """The arguments of start_remake_video, with an idempotency_key that is stable for the same inputs."""
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
        raise ValueError("--attempt must be a positive integer")
    reference = _url(reference, "Reference")
    if bool(handle) == bool(video_urls):
        raise ValueError("Give the creator as exactly one of --instagram-handle or --creator-video (1 to 4 links)")
    if handle:
        handle = handle.strip()
        if handle.lower().startswith(("http://", "https://")):
            parts = urlsplit(handle)
            segments = [s for s in parts.path.split("/") if s]
            if parts.scheme != "https" or (parts.hostname or "").lower() not in INSTAGRAM_HOSTS or len(segments) != 1:
                raise ValueError("--instagram-handle must be a handle or an https Instagram profile link")
        elif not HANDLE.match(handle):
            raise ValueError("--instagram-handle must be an Instagram handle (letters, digits, '.', '_')")
        creator = {"instagram_handle": handle}
    else:
        if not 1 <= len(video_urls) <= 4:
            raise ValueError("Give 1 to 4 creator video links")
        urls = [_url(url, "Creator video") for url in video_urls]
        if any(_same_video(a, b) for i, a in enumerate(urls) for b in urls[i + 1:]):
            raise ValueError("The same creator video is listed twice")
        if any(_same_video(reference, url) for url in urls):
            raise ValueError("The reference and a creator video are the same video; each role needs its own video")
        if not creator_name:
            raise ValueError("--creator-name is required with --creator-video (a short ASCII name for the creator)")
        creator = {"video_urls": urls}
    arguments = {"reference_url": reference, "creator": creator}
    if creator_name:
        if not CREATOR_NAME.match(creator_name):
            raise ValueError("--creator-name must be 1-64 ASCII letters, digits, '.', '_' or '-', starting with a letter or digit")
        arguments["creator_name"] = creator_name
    if language:
        if not LANGUAGE.match(language):
            raise ValueError("--language must look like 'en' or 'pt-BR'")
        arguments["language"] = language
    digest = hashlib.sha256(json.dumps(arguments, sort_keys=True, ensure_ascii=False,
                                       separators=(",", ":")).encode()).hexdigest()[:16]
    key = "".join(char if char in KEY_CHARS else "-" for char in f"{SKILL.name}:{run_dir().name}:{digest}:a{attempt}")
    if len(key) > KEY_LIMIT:
        key = "kabo:" + hashlib.sha256(key.encode()).hexdigest()[:48] + f":a{attempt}"
    arguments["idempotency_key"] = key
    return {"tool": "start_remake_video", "arguments": arguments}


# ---------------------------------------------------------------- fetch

def tool_result(raw):
    """The structured object of a complete get_remake_video / render_remake_video result."""
    if len(raw) > MAX_RESULT_BYTES:
        raise ValueError("Tool result is larger than expected; save the complete get_remake_video result, not the brief")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Complete tool result must be a JSON object")
    if value.get("isError") and "structuredContent" not in value:
        blocks = value.get("content") if isinstance(value.get("content"), list) else []
        text = " ".join(block["text"] for block in blocks if isinstance(block, dict) and isinstance(block.get("text"), str))
        raise ValueError("The tool returned an error, not a job state: " + (text.strip()[:300] or "no message"))
    structured = value.get("structuredContent", value)
    if "content" in value and "structuredContent" not in value:
        blocks = value["content"]
        if (not isinstance(blocks, list) or not blocks or not isinstance(blocks[0], dict) or
                not isinstance(blocks[0].get("text"), str)):
            raise ValueError("Complete structured result is required")
        structured = json.loads(blocks[0]["text"])
    if not isinstance(structured, dict) or not isinstance(structured.get("status"), str):
        raise ValueError("This is not a get_remake_video or render_remake_video result")
    return structured


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
    # 测试可在进程内替换这个函数注入假的 HTTPS 处理器；命令行没有入口能改它。
    context = tls_context()
    handlers = [NoRedirect()] + ([urllib.request.HTTPSHandler(context=context)] if context else [])
    return urllib.request.build_opener(*handlers)


def expired(descriptor):
    try:
        stamp = datetime.fromisoformat(str(descriptor.get("expires_at")).replace("Z", "+00:00"))
    except ValueError:
        return False
    return stamp.tzinfo is not None and stamp <= datetime.now(timezone.utc)


def download(descriptor, kind, content_type, limit, sink):
    """GET one Kabo-issued signed link into sink; return (sha256, bytes) only when everything matches."""
    url, digest, size = descriptor.get("url"), descriptor.get("sha256"), descriptor.get("bytes")
    if descriptor.get("status") == "unavailable":
        reason = str(descriptor.get("reason"))
        raise ValueError(f"Download unavailable ({reason}). " + UNAVAILABLE_HINTS.get(reason, RESIGN_HINT))
    if descriptor.get("status") != "ready":
        raise ValueError("This result has no ready download. " + RESIGN_HINT)
    # 简报是 envelope，成片是 artifact（ADR-0084 researchDownloadSchema 的 kind）。
    if descriptor.get("kind") != kind:
        raise ValueError(f"Download kind is {descriptor.get('kind')!r}, expected {kind!r}")
    if (not isinstance(url, str) or not isinstance(digest, str) or len(digest) != 64 or
            any(char not in "0123456789abcdef" for char in digest) or
            isinstance(size, bool) or not isinstance(size, int) or not 0 < size <= limit):
        raise ValueError("Download descriptor is incomplete or over this Skill's size limit")
    if str(descriptor.get("content_type", "")).lower() != content_type:
        raise ValueError(f"Download is declared as {descriptor.get('content_type')!r}, expected {content_type}")
    if expired(descriptor):
        raise ValueError("Download link expired (expires_at has passed). " + RESIGN_HINT)
    # 协议与主机逐字匹配（不带端口、用户名），不含空白或控制字符。
    if (not url.startswith(DOWNLOAD_PREFIX) or urlsplit(url).netloc != DOWNLOAD_HOST or
            any(ord(char) <= 32 or ord(char) == 127 for char in url)):
        raise ValueError("Download link is not a Kabo signed storage link; refusing to fetch it")
    request = urllib.request.Request(url, method="GET", headers={"Accept-Encoding": "identity"})
    # 报错只说类别与状态码：异常文本可能带链接、签名或对象名，一律不转述。
    hasher, total = hashlib.sha256(), 0
    try:
        with build_opener().open(request, timeout=DOWNLOAD_TIMEOUT) as response:
            status = response.status
            served = response.headers.get("Content-Type") if status == 200 else None
            if served and served.split(";")[0].strip().lower() != content_type:
                status = "type"
            while status == 200 and total <= size:
                chunk = response.read(min(1024 * 1024, size + 1 - total))
                if not chunk:
                    break
                hasher.update(chunk)
                sink.write(chunk)
                total += len(chunk)
    except urllib.error.HTTPError as error:
        status = error.code
        error.close()
    except Exception as error:
        raise ValueError(f"Download failed ({type(error).__name__}). If this host blocks network access for "
                         "scripts, say that the file cannot be saved here; do not rebuild it another way") from None
    if status == "type":
        raise ValueError("Download content type does not match the declared content_type; nothing was kept")
    if 300 <= status < 400:
        raise ValueError(f"Download was redirected (HTTP {status}); redirects are not followed")
    if status in (400, 401, 403):
        raise ValueError(f"Download link expired or was refused (HTTP {status}). " + RESIGN_HINT)
    if status != 200:
        raise ValueError(f"Download failed (HTTP {status}). " + RESIGN_HINT)
    if total != size:
        raise ValueError("Download size does not match the declared bytes; nothing was kept")
    if hasher.hexdigest() != digest:
        raise ValueError("Download sha256 does not match the declared value; nothing was kept")
    return digest, size


def safe_relative(name):
    # 简报包里的路径只能是相对路径，不能越出 brief/。
    if (not isinstance(name, str) or not name or name.startswith("/") or "\\" in name or
            not re.fullmatch(r"[A-Za-z0-9._/-]+", name) or any(part in ("", ".", "..") for part in name.split("/"))):
        raise ValueError("Brief bundle contains an unsafe file path")
    return name


def load_bundle(structured):
    """Download and check the brief envelope this result links to; return (body, bundle, digest, size)."""
    brief = structured.get("brief")
    if not isinstance(brief, dict) or not isinstance(brief.get("download"), dict):
        raise ValueError(f"This result (status {structured.get('status')!r}) carries no brief; the brief is given only "
                         "at waiting_script")
    buffer = io.BytesIO()
    digest, size = download(brief["download"], "envelope", "application/json", MAX_BRIEF_BYTES, buffer)
    body = buffer.getvalue()
    bundle = json.loads(body.decode("utf-8"))
    if not isinstance(bundle, dict) or bundle.get("schema") != BRIEF_SCHEMA:
        raise ValueError("Downloaded file is not a remake brief bundle")
    job_id = structured.get("job_id")
    if job_id and bundle.get("job_id") != job_id:
        raise ValueError("Downloaded brief belongs to a different job")
    files = bundle.get("files")
    if not isinstance(files, dict) or not all(isinstance(text, str) for text in files.values()):
        raise ValueError("Brief bundle files must map paths to text")
    return body, bundle, digest, size


def fetch_brief(run, structured, loaded=None):
    if structured.get("failure") == "unsupported_type":
        raise ValueError("This job is not a talking-head or voice-over reference (failure unsupported_type): "
                         + str(structured.get("failure_reason") or "no reason given")
                         + ". Stop: do not call render_remake_video")
    body, bundle, digest, size = loaded or load_bundle(structured)
    files = bundle["files"]
    target = run / "brief"
    marker = target / "bundle.json"
    previous = None
    if marker.exists():
        # 信封里可能带着每次重签的预览链接：只按稿件文件判断简报是否换了
        if (json.loads(marker.read_bytes().decode("utf-8")) or {}).get("files") == files:
            return {"saved": "brief", "job_id": bundle.get("job_id"), "status": "already_saved", "directory": str(target),
                    "files": {name: str(target / name) for name in BRIEF_FILES if (target / name).exists()}}
    narration = False
    try:
        narration = (json.loads(files.get("work/brief_input.json") or "{}").get("reference") or {}).get("type") == "narration"
    except (ValueError, AttributeError):
        pass
    missing = [name for name in BRIEF_FILES if name not in files
               and not (narration and name in NARRATION_OPTIONAL)]
    if missing:
        raise ValueError("Brief bundle is missing " + ", ".join(missing))
    for name, text in files.items():
        safe_relative(name)
        if name.endswith(".json"):
            json.loads(text)
    if marker.exists():
        # 稿件预检没过时服务端回到 waiting_script 并给一份新简报：旧简报整体留作 brief.previous-N，
        # 新简报写回 brief/，script/ 里已写的文档不动。
        n = 1
        while (run / f"brief.previous-{n}").exists():
            n += 1
        previous = run / f"brief.previous-{n}"
        target.rename(previous)
        target.mkdir(mode=0o700)
    written = {}
    for name, text in sorted(files.items()):
        path = target / name
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # bundle.json 还不在说明上次没写完；这几份文件按同一份已校验的字节重写。
        path.write_bytes(text.encode("utf-8"))
        written[name] = str(path)
    # bundle.json 最后写：它在就表示整份简报都已落盘。
    write_new(marker, body)
    result = {"saved": "brief", "job_id": bundle.get("job_id"), "sha256": digest, "bytes": size,
              "directory": str(target), "files": written}
    if previous is not None:
        result["previous_brief"] = str(previous)
    return result


def fetch_video(run, structured):
    video = structured.get("video")
    if structured.get("status") != "delivered" or not isinstance(video, dict) or not isinstance(video.get("download"), dict):
        raise ValueError(f"This result (status {structured.get('status')!r}) carries no finished video")
    descriptor = video["download"]
    job_id = str(structured.get("job_id") or "job")
    stem = re.sub(r"[^A-Za-z0-9-]", "", job_id)[:8] or "job"
    final = run / "output" / f"remake-{stem}.mp4"
    if final.exists():
        existing = hashlib.sha256()
        with open(final, "rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                existing.update(chunk)
        if existing.hexdigest() == descriptor.get("sha256"):
            return {"saved": "video", "job_id": job_id, "status": "already_saved", "path": str(final)}
        raise ValueError("A different video is already saved at " + str(final))
    partial = run / "output" / f".partial-{secrets.token_hex(6)}"
    try:
        fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            digest, size = download(descriptor, "artifact", "video/mp4", MAX_VIDEO_BYTES, stream)
        with open(partial, "rb") as stream:
            head = stream.read(12)
        if head[4:8] != b"ftyp":
            raise ValueError("Downloaded file is not an MP4 video; nothing was kept")
        os.link(partial, final)
    finally:
        if partial.exists():
            partial.unlink()
    return {"saved": "video", "job_id": job_id, "sha256": digest, "bytes": size, "path": str(final)}


def fetch_pages(descriptors, target):
    """Download page images (preview pages or the per-line review sheet) into a fresh target."""
    if not isinstance(descriptors, list) or not descriptors:
        raise ValueError("This result carries no pages to fetch")
    target.mkdir(mode=0o700, parents=True)
    paths = []
    for n, item in enumerate(descriptors, start=1):
        if not isinstance(item, dict) or not isinstance(item.get("download"), dict):
            raise ValueError("A page in this result has no download")
        path = target / f"page_{n:02d}.jpg"
        buffer = io.BytesIO()
        download(item["download"], "artifact", "image/jpeg", MAX_PAGE_BYTES, buffer)
        body = buffer.getvalue()
        if body[:3] != b"\xff\xd8\xff":
            raise ValueError("A downloaded page is not a JPEG image; nothing was kept")
        write_new(path, body)
        paths.append(str(path))
    return paths


def next_round(base):
    n = 1
    while (base / str(n)).exists():
        n += 1
    return base / str(n)


def fetch_preview(run, structured):
    """Save the pages of a voice-over preview (they ride in the brief envelope) as preview/<n>/."""
    _, bundle, _, _ = load_bundle(structured)
    preview = bundle.get("preview")
    if not isinstance(preview, dict):
        raise ValueError("This result carries no preview; call render_remake_video with preview first")
    if preview.get("status") == "running":
        raise ValueError("The preview is still rendering: call get_remake_video with this job_id in about 30 seconds, "
                         "save the result and run fetch-preview on it")
    rows = [row for row in preview.get("rows") or [] if isinstance(row, dict)]
    errors = [{"row": row.get("row"), "lines": row.get("lines"), "error": str(row["error"])[:400]}
              for row in rows if row.get("error")]
    if preview.get("status") != "done":
        return {"saved": "preview", "status": "failed", "errors": errors}
    target = next_round(run / "preview")
    pages = fetch_pages(preview.get("pages"), target)
    return {"saved": "preview", "status": "done", "directory": str(target), "pages": pages,
            "rows": len(rows), "errors": errors, "spend_usd": preview.get("spend_usd")}


def fetch_review(run, bundle):
    """Save the per-line sheet pages and the review brief (current decisions, script, template) as
    review/. Everything is written to a staging directory first and moved into place only once
    every page and file has passed, so a failed fetch can simply be run again."""
    target = run / "review"
    if target.exists():
        raise ValueError("This run already saved its picture review in review/; there is only one round")
    files = bundle["files"]
    if any(name not in files for name in REVIEW_FILES):
        raise ValueError("The review brief is incomplete")
    staging = run / f".review-staging-{secrets.token_hex(6)}"
    try:
        fetch_pages((bundle.get("review") or {}).get("sheets"), staging / "pages")
        for name in REVIEW_FILES:
            safe_relative(name)
            json.loads(files[name])
            path = staging / "brief" / name
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            path.write_text(files[name], encoding="utf-8")
        staging.rename(target)
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
    pages = sorted(str(p) for p in (target / "pages").glob("page_*.jpg"))
    return {"saved": "review", "pages": pages,
            "files": {name: str(target / "brief" / name) for name in REVIEW_FILES}}


def fetch(raw):
    run = run_dir()
    structured = tool_result(raw)
    if isinstance(structured.get("video"), dict):
        return fetch_video(run, structured)
    loaded = load_bundle(structured) if structured.get("failure") != "unsupported_type" else None
    if loaded and isinstance(loaded[1].get("review"), dict):
        return fetch_review(run, loaded[1])
    return fetch_brief(run, structured, loaded)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init").add_argument("--workspace", required=True)
    command = commands.add_parser("start-call")
    command.add_argument("--reference-url", required=True)
    command.add_argument("--instagram-handle")
    command.add_argument("--creator-video", action="append", default=[])
    command.add_argument("--creator-name")
    command.add_argument("--language")
    command.add_argument("--attempt", type=int, default=1)
    # 完整结果只从文件或 stdin 读；下载链接不进 argv，不出现在进程列表里。
    command = commands.add_parser("fetch")
    command.add_argument("--input", default="-")
    command = commands.add_parser("fetch-preview")
    command.add_argument("--input", default="-")
    args = parser.parse_args()
    if args.command == "init":
        print(init(args.workspace))
    elif args.command == "start-call":
        print(json.dumps(start_call(args.reference_url, args.instagram_handle, args.creator_video,
                                    args.creator_name, args.language, args.attempt), ensure_ascii=False))
    else:
        if args.input == "-":
            raw = sys.stdin.buffer.read(MAX_RESULT_BYTES + 1)
        else:
            with open(args.input, "rb") as stream:
                raw = stream.read(MAX_RESULT_BYTES + 1)
        if args.command == "fetch-preview":
            print(json.dumps(fetch_preview(run_dir(), tool_result(raw)), ensure_ascii=False))
        else:
            print(json.dumps(fetch(raw), ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError, TypeError) as error:
        print("Remake run operation failed: " + str(error), file=sys.stderr)
        sys.exit(1)
