#!/usr/bin/env python3
"""Persist one connector envelope from this run's staged copy of that response.

The tool result the model sees is a rendering: hosts fence it and abridge large results.
Retyping it once lost every row (100 TikTok posts persisted with empty ``rows``), so
the bytes must come from a complete copy. The source is this run's own staging directory,
``<run>/snapshot/.host-evidence``, which ``static_host.py stage`` fills from the host's exported
tool result after checking it. This script never reads a host's conversation or session records:
when nothing complete was staged it fails closed, and the Skill reports that gap.

``--schema <schema_version>`` copies a typed tool result instead of an envelope: a collector's
candidate set or an enrichment (``trend-candidate-set.v1``, ``youtube-trend-enrichment.v1``, ...);
every rule below applies to them the same way.

The envelope lands either in its own file (``--dest``) or at a position inside a bundle file the
next script reads (``--into FILE --at SEGMENT...``, e.g. ``runs`` / ``<run key>`` or ``scenes`` /
``[0]`` / ``video``). ``--record-request FILE`` writes the same position in a provenance file
with the envelope's own ``connector_id``, ``operation`` and ``request_id`` plus the ``--params``
the model sent, so no identifier is ever copied by hand.

- Only responses staged after this run was reserved qualify, earliest first (fetch order). A
  sidecar without ``staged_at`` is dated by its own mtime. Staged bodies are sha256-checked
  against their sidecar and are not second-guessed.
- ``--drained <dir>`` names a directory of this run that already holds response files
  (``envelope-NN.json``); they are read when staging has nothing left to place, and files under
  that directory then count as the source rather than as placements.
- A response this run already persisted (any JSON file in the run directory, bundles included)
  is skipped, counted per copy, so one call per envelope walks through repeated pages of one
  operation instead of copying the first twice, and two identical responses (two timeouts) still
  fill two slots. The host's raw tool results that ``static_host.py fetch`` / ``stage`` read (kept
  in ``<run>/host-results/``, or saved anywhere else in the run) are inputs, not placements, and
  never count.
- Responses are taken in that order whatever their status, so a failed call cannot slide the next
  call's response into its slot. A response that is not completed (without ``--any-status``) is
  refused, nothing is written, and it is recorded in ``.persist-rejected.jsonl`` beside the run
  so the next call moves past it: to the retry, or to the next call's response. ``--any-status``
  takes failed responses as the evidence they are and ignores that ledger. Responses still in
  flight (``queued``, ``running``) are never taken.
- One persist at a time per run: the choice and the write happen under ``.persist.lock``. A lock
  left by a killed process is broken once that process is gone or the lock is two minutes old.
- With ``--handle``, a response whose account (``data.handle`` / ``data.username``) differs is
  refused, and a staged page that carries no account field is only taken when a response carrying
  that account was also staged in this run. Without ``--handle`` there is no account check:
  competitor and search runs persist many accounts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
import time
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

# 有的宿主导出工具结果时把正文裹在这个标签里,于是它不以 "{" 开头。
# 只认这一个标签名,标签内仍必须是合法 JSON——放宽成「尖括号开头就剥」会让读取器
# 开始接受任意标记文本。
UNTRUSTED_DATA = re.compile(r"<untrusted_data\b[^>]*>\s*(.*?)\s*</untrusted_data>", re.DOTALL)
RUN_DIR = re.compile(r"^(\d{8}T\d{6})Z-[0-9A-Za-z]+$")
LIST_INDEX = re.compile(r"^\[(\d+)\]$")
# completed_partial is a real answer, not a failure: Instagram resolve_profile routinely returns
# it when some public fields are absent, and the analyzers read its limitations.
USABLE_STATUSES = {"completed", "completed_partial"}
ACCOUNT_KEYS = ("handle", "username")
EPOCH = datetime.min.replace(tzinfo=timezone.utc)
# An async job reports these before its terminal envelope; they answer no call yet.
IN_FLIGHT_STATUSES = {"queued", "pending", "running", "in_progress", "accepted"}
REJECTED_LEDGER = ".persist-rejected.jsonl"
LOCK_NAME = ".persist.lock"
STALE_LOCK_SECONDS = 120


@dataclass
class Candidate:
    recorded_at: datetime | None
    envelope: dict[str, Any]
    body: bytes | None  # the staged bytes, written verbatim
    source: str
    # The staging directory a response came from; account anchoring is scoped to it.
    conversation: str | None = None


def default_staging_roots() -> list[Path]:
    from static_host import run_dir
    return [run_dir() / "snapshot/.host-evidence"]


def meta_conflicts(meta: dict[str, Any], connector_id: str | None, operation: str | None) -> bool:
    # A sidecar field that is absent is unknown, not a mismatch; the body is matched again below.
    if connector_id and meta.get("connector_id") not in (None, connector_id):
        return True
    if operation and meta.get("operation") not in (None, operation):
        return True
    return False


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def run_dir_of(target: Path) -> Path | None:
    for part in (target.resolve().parent, *target.resolve().parents):
        if RUN_DIR.match(part.name):
            return part
    return None


def run_started(target: Path) -> datetime | None:
    run_dir = run_dir_of(target)
    if run_dir is None:
        return None
    stamp = RUN_DIR.match(run_dir.name).group(1)  # type: ignore[union-attr]
    return datetime.strptime(stamp, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)


def unwrap_untrusted_data(value: str) -> str | None:
    """The JSON inside a host's ``<untrusted_data source="…">…</untrusted_data>`` wrapper.

    A host may export a tool result as ``content[].text`` with this wrapper around it, so the
    body never starts with ``{`` and every reader below it saw nothing. Observed 2026-09-16: the
    typed results of ``collect_*`` were unreadable for that reason alone, and the run failed with
    "no complete host record" while the problem list stayed empty — no candidate had reached the
    filters at all.

    Only this exact tag is unwrapped, and the body still has to parse as JSON: a reader that
    accepted anything angle-bracketed would start admitting arbitrary markup.
    """
    match = UNTRUSTED_DATA.search(value)
    return match.group(1) if match else None


def as_object(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        text = value.lstrip()
        if not text.startswith("{"):
            inner = unwrap_untrusted_data(value)
            if inner is None:
                return None
            text = inner.lstrip()
            if not text.startswith("{"):
                return None
        try:
            loaded = json.loads(text)
        except json.JSONDecodeError:
            return None
        return loaded if isinstance(loaded, dict) else None
    return None


def is_envelope(obj: Any) -> bool:
    return isinstance(obj, dict) and {"connector_id", "operation", "status"} <= set(obj)


def unwrap_envelope(value: Any) -> dict[str, Any] | None:
    obj = as_object(value)
    if obj is None:
        return None
    structured = obj.get("structuredContent")
    if isinstance(structured, dict):
        inner = unwrap_envelope(structured)
        if inner is not None:
            return inner
    contents = obj.get("content")
    if isinstance(contents, list):
        for item in contents:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            # 不在这里预判前缀：正文可能裹在 <untrusted_data> 里，不以 "{" 开头。
            # 由 as_object 统一裁决（它认这两种形态，解析不出仍返回 None）。
            if isinstance(text, str):
                inner = unwrap_envelope(text)
                if inner is not None:
                    return inner
    if is_envelope(obj):
        return obj
    envelope = obj.get("envelope")
    if isinstance(envelope, dict):
        return unwrap_envelope(envelope)
    return None


def nested_envelopes(obj: Any) -> Iterator[dict[str, Any]]:
    """Every envelope inside a persisted file, including those placed in bundles."""
    if is_envelope(obj):
        yield obj
        return
    if isinstance(obj, dict):
        for value in obj.values():
            yield from nested_envelopes(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from nested_envelopes(value)


def unwrap_typed(value: Any, schema: str) -> dict[str, Any] | None:
    """A typed tool result carrying this ``schema_version``, through any MCP wrapper."""
    obj = as_object(value)
    if obj is None:
        return None
    if obj.get("schema_version") == schema:
        return obj
    structured = obj.get("structuredContent")
    if isinstance(structured, dict):
        inner = unwrap_typed(structured, schema)
        if inner is not None:
            return inner
    contents = obj.get("content")
    if isinstance(contents, list):
        for item in contents:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                inner = unwrap_typed(item["text"], schema)
                if inner is not None:
                    return inner
    return None


def nested_typed(obj: Any, schema: str) -> Iterator[dict[str, Any]]:
    """Every typed result of this schema inside a persisted file."""
    if isinstance(obj, dict):
        if obj.get("schema_version") == schema:
            yield obj
            return
        for value in obj.values():
            yield from nested_typed(value, schema)
    elif isinstance(obj, list):
        for value in obj:
            yield from nested_typed(value, schema)


def row_count(envelope: dict[str, Any]) -> int | None:
    data = envelope.get("data")
    if not isinstance(data, dict):
        return None
    rows = data.get("rows")
    if isinstance(rows, list):
        return len(rows)
    items = data.get("items")
    if isinstance(items, list):
        return len(items)
    return None


def matches(envelope: dict[str, Any], connector_id: str | None, operation: str | None) -> bool:
    if connector_id and envelope.get("connector_id") != connector_id:
        return False
    if operation and envelope.get("operation") != operation:
        return False
    return True


def in_flight(envelope: dict[str, Any]) -> bool:
    return str(envelope.get("status") or "").lower() in IN_FLIGHT_STATUSES


def usable(envelope: dict[str, Any], any_status: bool = False) -> str | None:
    # --any-status is for consumers that record failed routes themselves (discovery runs, comment
    # targets): a failed response is still that route's evidence.
    if envelope.get("status") not in USABLE_STATUSES:
        if any_status and isinstance(envelope.get("status"), str):
            return None
        return f"status={envelope.get('status')!r} is not a completed response"
    return None


def identity(envelope: dict[str, Any]) -> str:
    """What counts as "the same response" when tallying what this run already placed.

    ``raw_sha256`` first, deliberately. A host may answer every sub-request of one
    ``data_connector_batch_run`` under a single ``request_id``, so a request id does not identify a
    response: 2026-09-21 two ``search_videos`` calls of one batch, on different keywords, came back
    sharing one. Counting by it folded them into a single identity, placement filled both slots from
    the same response, the other was dropped, and the run failed its own
    ``intent-1-relevance request/response query mismatch`` check. The same fold is why a paginating
    fetch could re-place the page it had already placed.

    ``raw_sha256`` is the host's hash of the response bytes: two different responses never collide,
    and two copies of one always match — which is exactly the question being asked here. The request
    id stays as the fallback for a host that does not send the hash, and the canonical hash below
    for one that sends neither.
    """
    for key in ("raw_sha256", "request_id"):
        value = envelope.get(key)
        if isinstance(value, str) and value:
            return f"{key}:{value}"
    canonical = json.dumps(envelope, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def account_of(envelope: dict[str, Any]) -> str | None:
    data = envelope.get("data")
    if not isinstance(data, dict):
        return None
    for key in ACCOUNT_KEYS:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lstrip("@").lower()
    return None


DRAINED_RECORD = re.compile(r"envelope-\d+\.json")


# SKILL.md 给宿主导出的原始工具结果定的存放目录（运行目录下）。
HOST_RESULTS_DIR = "host-results"
# MCP CallToolResult 的顶层字段。
MCP_RESULT_KEYS = {"content", "structuredContent", "isError", "_meta"}


def host_tool_result(document: Any) -> bool:
    # 宿主导出的原始工具结果是 static_host.py fetch / stage 的输入，不是放置写出的槽位。
    # 2026-10-22 Codex 0.163 实测：模型把 youtube_get_video 的完整结果存成
    # snapshot/tool-metadata-initial.json，fetch 正常暂存；放置前统计「已放置」时扫了整个运行目录，
    # 这份原始结果 structuredContent.result 里的 envelope 与暂存副本是同一个 raw_sha256，唯一的
    # 暂存候选被当成已放置扣掉，persist 报 "no complete host record"。
    # 认这几种形状：MCP CallToolResult（顶层有 structuredContent，或只有 content 块数组 / isError /
    # _meta），以及去掉 MCP 外壳的 structuredContent 本体（带 download 描述，或只有 result）。
    # 放置写出的东西都不是这些形状：暂存正文是 static_host 去壳后的裸 envelope 或类型化结果（类型化
    # 结果的 download 已去掉），bundle 只在指定位置放裸 envelope。envelope 本身一律不算：没有就绪
    # 下载时暂存的 envelope 会带着 status=unavailable 的 download。
    if not isinstance(document, dict) or is_envelope(document):
        return False
    if "structuredContent" in document:
        return True
    if ("content" in document or "isError" in document) and set(document) <= MCP_RESULT_KEYS:
        return isinstance(document.get("content", []), list)
    download = document.get("download")
    if isinstance(download, dict) and download.get("status") in ("ready", "unavailable"):
        return True
    return set(document) == {"result"} and isinstance(document["result"], dict)


def source_copies(staging_roots: list[Path], records: list[Path]) -> dict[str, set[Path]]:
    # 每份来源正文的 sha256 → 路径。放置按字节原样写出来源正文，带 MCP 外壳的放置结果只可能是
    # 某份来源（--drained 的宿主记录）的逐字节副本。暂存区用旁注里记下的摘要，不重读正文。
    copies: dict[str, set[Path]] = {}
    for root in staging_roots:
        try:
            metas = list(root.glob("*.meta")) if root.is_dir() else []
        except OSError:
            continue
        for meta_path in metas:
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(meta, dict) and isinstance(meta.get("sha256"), str):
                copies.setdefault(meta["sha256"], set()).add(meta_path.with_suffix(".json").resolve())
    for path in records:
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            continue
        copies.setdefault(digest, set()).add(path.resolve())
    return copies


def already_persisted(
    target: Path, slot: tuple[Path, list[str]] | None, schema: str | None = None,
    source_dir: Path | None = None, staging_roots: list[Path] | None = None,
) -> Counter[str]:
    """Identities of the envelopes this run already stored, one count per stored copy.

    Under a Kabo run directory that is the whole run tree: a Skill may keep single envelopes in
    ``snapshot/`` and bundles at the run root, and a response placed in one must not be placed
    again in the other. Without a run directory, only files beside ``target`` count. The slot
    being (re)written is excluded, so re-running the same persist call is idempotent instead of
    walking on to the next response.

    The host's raw tool results are not placements (see ``host_tool_result``): they are what
    ``static_host.py fetch`` / ``stage`` read, and counting the envelope inside one took the very
    response it had just staged.
    """
    taken: Counter[str] = Counter()
    run_dir = run_dir_of(target)
    if run_dir is not None:
        files = sorted(run_dir.rglob("*.json"))
    elif target.parent.is_dir():
        files = sorted(target.parent.glob("*.json"))
    else:
        return taken
    source_root = source_dir.resolve() if source_dir is not None else None
    from static_host import run_dir as static_run_dir
    run = static_run_dir()
    # 暂存区是来源；host-results/ 是 SKILL.md 给原始结果定的位置，同样只是 fetch / stage 的输入。
    inputs = (run / "snapshot/.host-evidence", run / HOST_RESULTS_DIR)
    sources: dict[str, set[Path]] | None = None
    for path in files:
        if any(directory in path.resolve().parents for directory in inputs):
            continue
        if slot is None and path.resolve() == target.resolve():
            continue
        # With --drained, the response files already in that directory (envelope-NN.json) are the
        # source, not placements. Anything else written there, such as a bundle this run is filling, still
        # counts, or every slot of that bundle would take the same first record.
        if (
            source_root is not None
            and source_root in path.resolve().parents
            and DRAINED_RECORD.fullmatch(path.name)
        ):
            continue
        try:
            body = path.read_bytes()
            document = json.loads(body.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if host_tool_result(document):
            # 原始结果存在 host-results/ 之外（snapshot/、运行目录根……）时按形状认出来。只有一种放置
            # 结果长这样：某份来源正文的逐字节副本，它照旧计入；与自身之外任何来源都不相同的，是模型
            # 存下的输入，不计。
            if sources is None:
                # --drained 目录里能当来源读的文件，含归档的等价副本。
                records = sorted(source_root.rglob("*.json")) if source_root is not None else []
                roots = default_staging_roots() if staging_roots is None else staging_roots
                sources = source_copies(roots, records)
            if not sources.get(hashlib.sha256(body).hexdigest(), set()) - {path.resolve()}:
                continue
        if slot is not None and path.resolve() == slot[0].resolve():
            document = without_slot(document, slot[1])
        for envelope in nested_typed(document, schema) if schema else nested_envelopes(document):
            taken[identity(envelope)] += 1
    return taken


def rejected_sources(ledger: Path) -> set[str]:
    sources: set[str] = set()
    try:
        # JSONL is delimited by \n alone. `splitlines()` also breaks on U+2028, U+2029, \x0b,
        # \x0c and \x85, all of which a JSON string may carry unescaped, so a record holding one
        # came back as fragments that no longer parse — and this ledger drops the rejection it was
        # recording, which lets the same refused response be chosen again.
        lines = ledger.read_text(encoding="utf-8").split("\n")
    except (OSError, UnicodeDecodeError):
        return sources
    for line in lines:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict) and isinstance(entry.get("source"), str):
            sources.add(entry["source"])
    return sources


def record_rejected(ledger: Path, candidate: Candidate, reason: str) -> None:
    ledger.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "source": candidate.source,
        "identity": identity(candidate.envelope),
        "status": candidate.envelope.get("status"),
        "reason": reason,
        "rejected_at": datetime.now(timezone.utc).isoformat(),
    }
    fd = os.open(ledger, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as stream:
        stream.write(json.dumps(entry, ensure_ascii=False) + "\n")


def file_time(path: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return None


def staged_candidates(
    roots: list[Path],
    connector_id: str | None,
    operation: str | None,
    since: datetime | None,
    problems: list[str],
    schema: str | None = None,
) -> list[Candidate]:
    """Every staged response of this operation (or typed result of this schema), whatever its status."""
    found: list[Candidate] = []
    seen: set[Path] = set()
    for root in roots:
        # static_host.py stage writes every response flat into this one directory.
        for base, recursive in [(root, False)]:
            try:
                metas = sorted(base.rglob("*.meta") if recursive else base.glob("*.meta")) if base.is_dir() else []
            except OSError as error:
                problems.append(f"{base}: {error.strerror or error}")
                continue
            for meta_path in metas:
                try:
                    already = meta_path.resolve() in seen
                    seen.add(meta_path.resolve())
                except OSError:
                    already = False
                if already:
                    continue
                # `.art` bodies are binary artifacts and a lone sidecar is a half-written pair:
                # neither is an envelope.
                body_path = meta_path.with_suffix(".json")
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    if not isinstance(meta, dict) or not body_path.is_file():
                        continue
                    if schema:
                        # A typed result's sidecar names its schema and no connector.
                        if meta.get("schema_version") not in (None, schema) or "connector_id" in meta:
                            continue
                    elif meta_conflicts(meta, connector_id, operation):
                        continue
                    # The contract does not require staged_at; the sidecar's own mtime is the
                    # moment the host wrote it, which is the same fact.
                    recorded = parse_time(meta.get("staged_at")) or file_time(meta_path)
                    if since is not None and (recorded is None or recorded < since):
                        continue
                    body = body_path.read_bytes()
                except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if hashlib.sha256(body).hexdigest() != meta.get("sha256"):
                    problems.append(f"{body_path}: sha256 does not match its sidecar")
                    continue
                text = body.decode("utf-8", errors="replace")
                if schema:
                    envelope = unwrap_typed(text, schema)
                    if envelope is None:
                        continue
                else:
                    envelope = unwrap_envelope(text)
                    if envelope is None or not matches(envelope, connector_id, operation) or in_flight(envelope):
                        continue
                # The first directory under the root (the root itself for flat staging), so
                # account anchoring spans everything staged there.
                relative = body_path.relative_to(root)
                conversation = str(root / relative.parts[0]) if len(relative.parts) > 1 else str(root)
                found.append(Candidate(recorded, envelope, body, f"host-staging:{body_path}", conversation))
    return found


def drained_candidates(
    directory: Path,
    connector_id: str | None,
    operation: str | None,
    problems: list[str],
    schema: str | None = None,
) -> list[Candidate]:
    """Response files already held in a directory of this run, newest numbering last.

    ``{run}/snapshot/envelope-NN.json`` files have no sidecars, so staging cannot offer them and
    every one of them already counts as stored. They may fill a bundle slot once, and the
    envelope body still carries its ``request_id``, so per-route provenance survives.
    """
    found: list[Candidate] = []
    try:
        paths = sorted(directory.glob("*.json")) if directory.is_dir() else []
    except OSError as error:
        problems.append(f"{directory}: {error.strerror or error}")
        return found
    for path in paths:
        try:
            body = path.read_bytes()
            text = body.decode("utf-8", errors="replace")
        except OSError:
            continue
        recorded = file_time(path)
        if schema:
            envelope = unwrap_typed(text, schema)
            if envelope is None:
                continue
        else:
            envelope = unwrap_envelope(text)
            if envelope is None or not matches(envelope, connector_id, operation) or in_flight(envelope):
                continue
        found.append(Candidate(recorded, envelope, body, f"drained:{path}", None))
    return found


def verified_conversations(
    roots: list[Path], since: datetime | None, expected: set[str]
) -> set[str]:
    """Conversations in which this account's own responses (ones carrying its handle) were staged."""
    return {
        candidate.conversation
        for candidate in staged_candidates(roots, None, None, since, [])
        if candidate.conversation
        and candidate.envelope.get("status") in USABLE_STATUSES
        and account_of(candidate.envelope) in expected
    }


def in_order(candidates: list[Candidate]) -> list[Candidate]:
    return [pair[1] for pair in sorted(enumerate(candidates), key=lambda pair: (pair[1].recorded_at or EPOCH, pair[0]))]


def group_of(candidate: Candidate) -> str:
    return candidate.source.rsplit("/", 1)[0]


def newest_group(candidates: list[Candidate]) -> list[Candidate]:
    """Unbound staging: keep only the directory of the latest response.

    Chosen before already-persisted responses are dropped, so an extra call after the last page
    fails closed instead of reaching back into an older run's directory.
    """
    if not candidates:
        return []
    group = group_of(in_order(candidates)[-1])
    return [candidate for candidate in candidates if group_of(candidate) == group]


def parse_segment(segment: str) -> int | str:
    match = LIST_INDEX.match(segment)
    return int(match.group(1)) if match else segment


def without_slot(document: Any, segments: list[str]) -> Any:
    """A copy of ``document`` with the value at ``segments`` removed (if present)."""
    if not segments:
        return None
    copy = json.loads(json.dumps(document))
    node = copy
    for segment in segments[:-1]:
        key = parse_segment(segment)
        if isinstance(key, int) and isinstance(node, list) and key < len(node):
            node = node[key]
        elif isinstance(key, str) and isinstance(node, dict) and key in node:
            node = node[key]
        else:
            return copy
    last = parse_segment(segments[-1])
    if isinstance(last, int) and isinstance(node, list) and last < len(node):
        node[last] = None
    elif isinstance(last, str) and isinstance(node, dict):
        node.pop(last, None)
    return copy


def place(document: Any, segments: list[str], value: Any) -> Any:
    """Set ``value`` at ``segments``; ``[n]`` indexes a list, anything else is an object key."""
    root = document if isinstance(document, (dict, list)) else {}
    node: Any = root
    for position, segment in enumerate(segments):
        key = parse_segment(segment)
        last = position == len(segments) - 1
        if isinstance(key, int):
            if not isinstance(node, list):
                raise ValueError(f"--at {segment} indexes a list, but that position holds an object")
            while len(node) <= key:
                # A slot not yet filled stays null: a consumer then reports it missing instead
                # of failing on an empty object.
                node.append(None)
        elif not isinstance(node, dict):
            raise ValueError(f"--at {segment} names an object key, but that position holds a list")
        if last:
            node[key] = value
            break
        child = node[key] if isinstance(key, int) else node.get(key)
        if not isinstance(child, (dict, list)):
            child = [] if LIST_INDEX.match(segments[position + 1]) else {}
            node[key] = child
        node = child
    return root


def read_lock(lock: Path) -> str | None:
    try:
        return lock.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def lock_is_stale(lock: Path, owner_text: str) -> bool:
    try:
        age = time.time() - lock.stat().st_mtime
    except OSError:
        return False  # gone this instant: wait and look again
    if age > STALE_LOCK_SECONDS:
        return True
    owner = owner_text.split()
    if owner and owner[0].isdigit():
        try:
            os.kill(int(owner[0]), 0)
        except ProcessLookupError:
            return True
        except OSError:
            return False  # alive, or a sandbox that will not say: the age rule decides
    return False


def break_stale_lock(lock: Path) -> None:
    """Remove a lock whose owner is gone, unless another waiter already replaced it."""
    owner_text = read_lock(lock)
    if owner_text is None or not lock_is_stale(lock, owner_text):
        return
    # Two waiters can judge the same lock stale; the one that loses must not delete the fresh
    # lock the winner just took. Re-read right before unlinking and leave a changed lock alone.
    if read_lock(lock) != owner_text:
        return
    try:
        lock.unlink()
    except OSError:
        pass


@contextmanager
def run_lock(base: Path) -> Iterator[None]:
    """One persist at a time per run: choosing a response and writing it are one step."""
    base.mkdir(parents=True, exist_ok=True)
    lock = base / LOCK_NAME
    deadline = time.monotonic() + 60
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.write(fd, f"{os.getpid()} {int(time.time())}\n".encode("ascii"))
            break
        except FileExistsError:
            break_stale_lock(lock)
            if not lock.exists():
                continue
            if time.monotonic() > deadline:
                raise OSError(
                    f"another persist_envelope.py call has held {lock} for a minute; "
                    "if none is running, delete that file and run this command again"
                )
            time.sleep(0.05)
    try:
        yield
    finally:
        os.close(fd)
        try:
            lock.unlink()
        except OSError:
            pass


def write_json_atomic(path: Path, document: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(document, ensure_ascii=False, indent=2) + "\n")
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise


def read_json_or_empty(path: Path) -> Any:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8").strip()
    return json.loads(text) if text else {}


def persist(
    dest: Path | None = None,
    *,
    connector_id: str | None = None,
    operation: str | None = None,
    schema: str | None = None,
    handle: str | None = None,
    into: Path | None = None,
    at: list[str] | None = None,
    record_request: Path | None = None,
    params: dict[str, Any] | None = None,
    any_status: bool = False,
    drained: Path | None = None,
    staging_roots: list[Path] | None = None,
) -> dict[str, Any]:
    if (dest is None) == (into is None):
        raise ValueError("give exactly one of --dest or --into")
    if into is not None and not at:
        raise ValueError("--into needs at least one --at segment")
    if record_request is not None and into is None:
        raise ValueError("--record-request only applies with --into/--at")
    if schema and (connector_id or operation or handle or record_request is not None):
        raise ValueError("--schema copies a typed tool result; it takes no --connector, --operation, --handle or --record-request")
    target = dest if dest is not None else into
    assert target is not None
    from static_host import require_target
    require_target(target)
    for source in [*(staging_roots or []), *([drained] if drained else [])]:
        require_target(Path(source) / "evidence.json")
    since = run_started(target)
    run_dir = run_dir_of(target)
    base = run_dir if run_dir is not None else target.parent
    ledger = base / REJECTED_LEDGER
    requested = handle.strip().lstrip("@").lower() if handle and handle.strip() else None
    expected = {requested} if requested else set()
    problems: list[str] = []
    roots = default_staging_roots() if staging_roots is None else list(staging_roots)

    def account_ok(candidate: Candidate) -> bool:
        account = account_of(candidate.envelope)
        return not (account and expected and account not in expected)

    with run_lock(base):
        taken = already_persisted(
            target, (into, list(at or [])) if into is not None else None, schema, drained,
            staging_roots=roots,
        )
        # The ledger only holds responses refused for their status, which --any-status accepts.
        rejected = set() if any_status else rejected_sources(ledger)

        def admissible(candidates: list[Candidate]) -> list[Candidate]:
            # In fetch order, so each stored copy accounts for the earliest response it matches.
            remaining = Counter(taken)
            kept: list[Candidate] = []
            for candidate in in_order(candidates):
                if candidate.source in rejected:
                    continue
                key = identity(candidate.envelope)
                if remaining[key] > 0:
                    remaining[key] -= 1
                    continue
                if not account_ok(candidate):
                    problems.append(
                        f"{candidate.source}: belongs to @{account_of(candidate.envelope)}, not @{', @'.join(sorted(expected))}"
                    )
                    continue
                kept.append(candidate)
            return kept

        staged = staged_candidates(roots, connector_id, operation, since, problems, schema)
        anchors = verified_conversations(roots, since, expected) if expected else set()
        if anchors:
            # A page without an account field (list_posts, list_reels) proves nothing about whose
            # it is; it must come from a conversation in which this account's own responses were
            # staged. With no such anchor (e.g. envelopes that never carry a handle) there is
            # nothing to bind to.
            kept: list[Candidate] = []
            for candidate in staged:
                if account_of(candidate.envelope) is None and candidate.conversation not in anchors:
                    problems.append(
                        f"{candidate.source}: carries no account and was not staged beside @{', @'.join(sorted(expected))}'s responses"
                    )
                    continue
                kept.append(candidate)
            staged = kept
        if since is None:
            staged = newest_group([candidate for candidate in staged if account_ok(candidate)])
        candidates = admissible(staged)
        if not candidates and drained is not None:
            candidates = admissible(drained_candidates(drained, connector_id, operation, problems, schema))
        if not candidates:
            detail = f" ({'; '.join(problems[:5])})" if problems else ""
            wanted = f"schema_version={schema!r}" if schema else f"connector_id={connector_id!r} operation={operation!r}"
            # A response this conversation fetched before the run directory was reserved is no
            # run's evidence and stays refused; say so, so the next step is a fresh call, not a
            # dead end. Only this conversation's own records are counted.
            early = 0
            if since is not None:
                early = sum(
                    1 for candidate in staged_candidates(roots, connector_id, operation, None, [], schema)
                    if candidate.recorded_at is not None and candidate.recorded_at < since
                )
            hint = (
                f"; {early} matching response(s) of this conversation were fetched before this run "
                f"directory was reserved ({run_dir.name if run_dir else target.parent}) and cannot be "
                "this run's evidence: make the same connector call again now, then run this same "
                "command again"
            ) if early else ""
            raise ValueError(
                f"no complete host record of {wanted} for this run{detail}{hint}; "
                "do not hand-write a truncated tool result"
            )
        # Fetch order.
        chosen = candidates[0]
        envelope = chosen.envelope
        # A typed result has no connector status; it is the tool's whole answer.
        reason = None if schema else usable(envelope, any_status)
        if reason is not None:
            record_rejected(ledger, chosen, reason)
            raise ValueError(
                f"{chosen.source}: {reason}. This response cannot fill the slot, so nothing was written. "
                "If you retried the call, run this same command again to take the retry; "
                "otherwise go on without this response."
            )

        result: dict[str, Any] = {"status": "ok"}
        if dest is not None:
            dest.parent.mkdir(parents=True, exist_ok=True)
            if chosen.body is not None:
                dest.write_bytes(chosen.body)
            else:
                dest.write_text(json.dumps(envelope, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.chmod(dest, 0o600)
            result.update({"path": str(dest), "bytes": dest.stat().st_size})
        else:
            assert into is not None and at
            write_json_atomic(into, place(read_json_or_empty(into), at, envelope))
            result.update({"into": str(into), "at": at})
            if record_request is not None:
                provenance = {
                    "connector_id": envelope.get("connector_id"),
                    "operation": envelope.get("operation"),
                    "request_id": envelope.get("request_id"),
                    "params": params if params is not None else {},
                }
                write_json_atomic(record_request, place(read_json_or_empty(record_request), at, provenance))
                result["record_request"] = str(record_request)
    result.update({
        "rows": (len(envelope["items"]) if isinstance(envelope.get("items"), list) else None) if schema else row_count(envelope),
        "schema_version": schema,
        "connector_id": envelope.get("connector_id"),
        "operation": envelope.get("operation"),
        "envelope_status": envelope.get("status"),
        "request_id": envelope.get("request_id"),
        "source": chosen.source,
    })
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dest", type=Path, help="write the envelope as this file")
    parser.add_argument("--into", type=Path, help="place the envelope inside this bundle file")
    parser.add_argument("--at", action="append", help="one position segment inside --into; [n] is a list index")
    parser.add_argument("--record-request", type=Path, help="provenance bundle written at the same --at position")
    parser.add_argument("--params", help="JSON object: the params this call was sent with (for --record-request)")
    parser.add_argument("--connector", dest="connector_id")
    parser.add_argument("--operation")
    parser.add_argument("--schema", help="schema_version of a typed tool result (a collector's candidate set, "
                        "an enrichment) to copy instead of an envelope")
    parser.add_argument("--handle", help="the verified handle of the account this response must belong to")
    parser.add_argument("--any-status", action="store_true",
                        help="also accept failed/unsupported responses (for consumers that record failed routes)")
    parser.add_argument("--drained", type=Path,
                        help="a directory of this run that already holds response files (envelope-NN.json); "
                             "read when staging holds nothing left to place")
    parser.add_argument("--staging", type=Path, action="append", help="staging root to search instead of the defaults")
    args = parser.parse_args()
    try:
        params = json.loads(args.params) if args.params else None
        if params is not None and not isinstance(params, dict):
            raise ValueError("--params must be a JSON object")
        result = persist(
            args.dest,
            connector_id=args.connector_id,
            operation=args.operation,
            schema=args.schema,
            handle=args.handle,
            into=args.into,
            at=args.at,
            record_request=args.record_request,
            params=params,
            any_status=args.any_status,
            drained=args.drained,
            staging_roots=args.staging,
        )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "failed", "error": f"{type(error).__name__}: {error}"}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
