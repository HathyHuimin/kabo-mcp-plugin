#!/usr/bin/env python3
"""Run everything between two judgments of a TikTok trend scan, so the model only judges.

A TikTok video-trend scan needs the model for three decisions: the frozen Request with its query
plan (and the search region), the relevance Gate scores, and the synthesis. Everything between them
is mechanical. Measured 2026-09-18 on two runs of this skill, that plumbing took 25 and 30 model
requests (307 s and 340 s): a catalog read, one request per search, date arithmetic for the ranked
board, a hand-copied `--params` for every placement, one request per history batch.

Each stage below starts right after one judgment and stops right before the next one: it makes the
connector calls the judgment implies, places each response at the same position the manual
workflow placed it, writes the same request provenance the manual `--record-request --params`
wrote, runs the same deterministic scripts with the same arguments, and prints exactly what the
next judgment reads.

    start    [--video URL | --profile F] run directory; Request + plan schemas; source video
                                         (get_video_by_share_url) -> its caption and author; or the
                                         Creator's saved account profile -> its signature content
    discover [--policy P [--since --until]] [--region R] [--without-include] [--question-file -]
                                         attach the profile -> freeze + validate -> round 1: the
                                         relevance searches and the D-1 board -> round 2: the
                                         most-liked searches, and the D-2 board when D-1 came back
                                         empty -> provenance -> candidate set (-> eligible candidates,
                                         with a profile) -> Gate table
    rank                                 Gate -> ranking input -> account histories -> enrichment ->
                                         Snapshot -> Analyze -> Analysis + synthesis schema
    report                               Compose -> Validate -> Render -> creator-report.md

Why two search rounds: the two sorts of one intent send the same keyword, and a search response
names its query but not its sort. Responses the model makes together can be recorded in any order,
so a relevance and a most-liked answer to the same keyword made in one response could not be told
apart. Each round carries each keyword once; the ranked board needs its second round anyway, since
D-2 is asked only after D-1 came back empty.

The stages never make connector calls themselves (connector_driver.py): a stage prints the calls
for the model to make as named public MCP tools and stops, and the same stage run again with
`--placed` places those responses from this run's staged copies. With a profile attached the
searches carry `include`; when Kabo refuses a call only for it, the same stage run with
`--without-include` hands those calls over once more without it (attach_creator_profile.py).
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import connector_driver as cd  # noqa: E402
import build_relevance_gate as gate_builder  # authored score validation, never semantic scoring
from persist_envelope import (  # noqa: E402
    account_of,
    in_flight,
    matches,
    place,
    read_json_or_empty,
    run_dir_of,
    run_lock,
    unwrap_envelope,
    usable,
    write_json_atomic,
)


def _own(name: str):
    # The profile intake is the same bytes in every trend package; still load this package's own copy
    # by path, never another package's sys.modules entry.
    path = Path(__file__).with_name(name + ".py")
    spec = importlib.util.spec_from_file_location(f"{path.parent.parent.name}:{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


intake = _own("attach_creator_profile")

PLATFORM = "tiktok"
DISCOVERY = "tiktok-discovery"
PUBLIC = "tiktok-public"
REQUEST = "trend-research-request.json"
PLAN = "tiktok-trend-query-plan.json"
SOURCE_VIDEO = "source-video.json"
# lookup_tiktok_video 给源视频 data 的固定投影，与作答路线无关。
VIDEO_PROJECTION = "kabo.tiktok_video.v1"
ENVELOPES = "discovery-envelopes.json"
PROVENANCE = "discovery-requests.json"
CANDIDATES = "tiktok-trend-candidate-set.json"
# With the Creator's account profile attached, every step after discovery reads this subset instead.
ELIGIBLE = "tiktok-trend-candidate-set-eligible.json"
PROFILE_SOURCE = intake.PROFILE_SOURCE
# What a search asks for on top of its rows when a profile is attached: language, hashtags,
# duration, ad flag, sound and the platform's own description, for eligibility and the Gate.
SEARCH_INCLUDE = ["content_signals"]
GATE_INPUT = "trend-relevance-gate-input.json"
GATE_SCORES = "trend-relevance-gate.tsv"
GATE = "trend-relevance-gate.json"
RANKING = "tiktok-trend-ranking-input.json"
HISTORIES = "account-history-envelopes.json"
ENRICHMENT_RESPONSE = "tiktok-trend-enrichment-01.json"
ENRICHMENT = "tiktok-trend-enrichment.json"
# With the Creator's account profile attached: the seats each signature content item holds.
ALLOCATION = "signature-content-allocation.json"
SNAPSHOT = "public-content-snapshot.json"
ANALYSIS = "public-content-analysis.json"
SYNTHESIS = "trend-research-synthesis.json"
REPORT = "trend-research-report.json"
CREATOR_REPORT = "creator-report.md"
# 用户原话逐字存在 run 里，冻结 Request 时据此核对 locale.named_in_ask。
QUESTION = Path("analysis") / "original-question.txt"

POLICIES = ("default_1y", "recent_90d", "user_named_span")
# search_videos: one call per intent and sort, 30 results each. sort_type 0 is relevance, 1 most liked.
SORTS = ((0, "relevance"), (1, "most-liked"))
SEARCH_COUNT = 30
# list_top_contents: the daily regional board (period_dimension 3), its full 100 rows, dated by the
# start (00:00 UTC) of the day it covers — the form most runs used (11 of 13 real runs, 2026-09-15..17).
BOARD_DIMENSION = 3
BOARD_LIMIT = 100
DAY = 86400
BOARD_KEYS = ("ranked-board-d1", "ranked-board-d2")
# Fields of the source video that the Request is not written from.
SOURCE_SKIP = {"thumbnail_url"}
# The two shapes of a TikTok video address sent to lookup_tiktok_video: the canonical page, or a
# vm./vt. short link — with no query string, fragment or other path (the server drops those too).
TIKTOK_VIDEO = re.compile(r"^(?:https?://)?(?:www\.|m\.)?tiktok\.com/(@[A-Za-z0-9._]{1,64})/video/([0-9]{8,25})(?:[/?#].*)?$",
                          re.IGNORECASE)
TIKTOK_SHORT = re.compile(r"^(?:https?://)?(v[mt])\.tiktok\.com/([A-Za-z0-9]{5,16})(?:[/?#].*)?$", re.IGNORECASE)


def _validator(name: str):
    # 同名模块在各趋势包里字节可能不同，不复用别的包留在 sys.modules 里的那份。
    path = Path(__file__).with_name(name + ".py")
    spec = importlib.util.spec_from_file_location(f"{path.parent.parent.name}:{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


window_rules = _validator("freeze_request_window")


def say(text: str = "") -> None:
    cd.say(text)


def round_key(calls: list[dict[str, Any]]) -> str:
    """The key connector_driver.fetch_round records a round under."""
    return hashlib.sha256(json.dumps(calls, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def file_sha(path: str | Path) -> str | None:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def need(*names: str) -> None:
    missing = [name for name in names if not Path(name).is_file()]
    if missing:
        raise cd.StageError(f"{', '.join(missing)} not found in {Path.cwd()}; run drive.py from the run directory "
                            "after writing it")


def need_authored(*names: str) -> bool:
    """Model-authored discovery inputs not written yet: one correction inside the open reserved run.

    Discovery freezes its window from these files before its first call, so with no frozen window it
    has fetched nothing; outside a reserved run, after a freeze, or a second time, the
    original stop stands.
    """
    missing = [name for name in names if not Path(name).is_file()]
    if not missing:
        return False
    try:
        receipt = json.loads(Path("run-manifest.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        receipt = {}
    state = cd.state()
    # 静态运行没有 run-manifest.json，以 static_host.py init 写下的 .static-run.json 作为预留凭据。
    reserved = receipt.get("status") == "running" or Path(".static-run.json").is_file()
    if (not reserved or cd.in_skill_directory(Path.cwd())
            or state.get("missing_authored_inputs") or (state.get("frozen") or {}).get("sha")):
        need(*names)
    cd.save_state(missing_authored_inputs=missing)
    cd.say(json.dumps({"status": "needs_correction", "reason": "missing_authored_input", "missing": missing,
                       "run_id": cd.run_id(), "corrections_remaining": 0,
                       "next_stage": {"script": "scripts/drive.py", "arguments": sys.argv[1:]},
                       "instruction": "Write the named files in this run directory with the host's normal file-writing "
                                      "tool, then repeat this same non-final stage in this same "
                                      "open run. Discovery has fetched nothing yet. Do not replace the run."}, ensure_ascii=False))
    return True


def _locale_correction(args: argparse.Namespace, originals: dict[str, bytes], error: str) -> bool:
    """作者输入的一次改正（与 YouTube / Instagram 的 discovery 输入改正同一约定），只在任何取数之前。

    TikTok 原先没有这条改正通道；这里只服务 locale 核对：原始字节留档，第二次仍不对就终止。
    """
    state = cd.state()
    try:
        receipt = json.loads(Path("run-manifest.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        receipt = {}  # 旧宿主可能没有 wrapper manifest；其余 I/O 仍是致命错误
    if receipt and receipt.get("status") != "running":
        raise cd.StageError("discovery input correction requires this same open run")
    if state.get("discovery") or Path(ENVELOPES).exists():
        raise cd.StageError(f"discovery inputs are frozen after data work began: {error}")
    previous = list(state.get("discovery_input_errors") or [])
    directory = Path("analysis") / "discovery-input-errors" / f"attempt-{len(previous) + 1:02d}"
    directory.mkdir(parents=True, exist_ok=False)
    hashes = {}
    for name, raw in originals.items():
        with (directory / name).open("xb") as stream:
            stream.write(raw)
        hashes[name] = hashlib.sha256(raw).hexdigest()
    with (directory / "error.json").open("x", encoding="utf-8") as stream:
        stream.write(json.dumps({"error": error, "input_sha256": hashes}, ensure_ascii=False, indent=2) + "\n")
    cd.save_state(discovery_input_errors=[*previous, str(directory)])
    if previous:
        raise cd.StageError(f"discovery input remains invalid after its one allowed correction: {error}")
    next_arguments = ["discover"]
    for key in ("policy", "since", "until", "region"):
        value = getattr(args, key, None)
        if value is not None:
            next_arguments.extend(["--" + key, str(value)])
    say(json.dumps({"status": "needs_correction", "reason": "invalid_discovery_input",
                    "errors": [error], "original_input_record": str(directory),
                    "corrections_remaining": 1, "run_id": cd.run_id(),
                    "next_stage": {"script": "scripts/drive.py", "arguments": next_arguments},
                    "instruction": "Correct only these authored inputs and repeat the documented non-final discover stage in this same open run with its required window arguments. No data was fetched. Do not replace the run, broaden its window or repeat completed calls. A second invalid input is fatal."}, ensure_ascii=False))
    return False


def standard_input(wait: bool) -> str:
    """What standard input holds: all of it when `wait`, else only what is already there."""
    stream = sys.stdin
    if stream is None or stream.isatty():
        return ""  # 漏了 heredoc 的终端，绝不等待
    if not wait:
        try:
            import select

            ready, _, _ = select.select([stream], [], [], 0)
        except (OSError, ValueError, TypeError, AttributeError):
            ready = [stream]  # 内存流（测试）读取不会阻塞
        if not ready:
            return ""
    return stream.read().rstrip("\r\n")


def original_question(args: argparse.Namespace) -> str | None:
    """用户原话：本次 `--question-file` 给的优先，否则沿用本 run 已存的；都没有时返回 None（旧调用方）。

    与 review-creator-account `fetch --question-file` 同一约定：`-` 从标准输入读（同一命令的 heredoc），
    否则读该文件。heredoc 末尾的换行不是用户打的字，去掉。
    """
    source = getattr(args, "question_file", None)
    source = source if isinstance(source, str) else None  # 直接调用阶段函数的旧调用方没有这个参数
    given = ""
    if source == "-":
        given = standard_input(wait=not getattr(args, "placed", False))
    elif source:
        given = Path(source).read_text(encoding="utf-8").rstrip("\r\n")
    stored = QUESTION.read_text(encoding="utf-8").rstrip("\r\n") if QUESTION.is_file() else None
    if not given.strip():
        return stored
    if stored is not None and stored != given:
        raise cd.StageError(f"this run already holds a different original ask ({QUESTION}); a different request "
                            "starts in a new run")
    if stored is None:
        QUESTION.parent.mkdir(parents=True, exist_ok=True)
        QUESTION.write_text(given + "\n", encoding="utf-8")
    return given


def _locale_preflight(args: argparse.Namespace, originals: dict[str, bytes]) -> bool:
    """region / language 非空时，`locale.named_in_ask` 必须逐字出自用户原话；否则在任何取数前要求改一次。

    10-29 plugins K2：中文问句、没点名市场，模型仍写 language=zh-CN，搜索被偏到中文。核对通过后把引语
    从 Request 移到 state：下游 Report 的 scope.locale 只有 region/language 两项，Request 其余字节不变。
    宿主没给原话（旧调用方）时保持原行为，只打印一行警告。
    """
    question = original_question(args)
    request = cd.load(REQUEST)
    locale = request.get("locale") if isinstance(request, dict) else None
    if not isinstance(locale, dict):
        return True  # Request 自身的形状校验会指出
    if question is None:
        say("[drive] warning: no --question-file was given, so locale.region / locale.language were not "
            "checked against the user's ask")
    key = [locale.get("region"), locale.get("language")]
    error = window_rules.locale_named_error(request, question)
    checked = cd.state().get("locale_named_in_ask") or {}
    if error and "named_in_ask" not in locale and checked.get("locale") == key:
        error = None  # 同一 locale 本 run 已核对过，引语已移进 state（重跑 / --placed）
    if error:
        return _locale_correction(args, originals, error)
    if question is not None and any(key):
        cd.save_state(locale_named_in_ask={"locale": key, "quote": locale.get("named_in_ask", checked.get("quote"))})
    if "named_in_ask" in locale:
        locale.pop("named_in_ask")
        Path(REQUEST).write_text(json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True


def fresh_bundle(bundles: tuple[str, ...], calls: list[dict[str, Any]]) -> None:
    """Start this collection's bundles, keeping an earlier collection's as audit copies.

    A stage run again for different calls (a rewritten plan or Gate) must not mix the earlier
    collection's responses into this one. The earlier bundle stays in the run directory under another
    name, so the placement scripts still count its responses as taken and never place them again.
    A placement retried for the same collection keeps its bundle: its filled slots are rewritten in place.
    """
    key = round_key(calls)
    owners = dict(cd.state().get("bundles") or {})
    for bundle in bundles:
        path = Path(bundle)
        if owners.get(bundle) != key and path.exists():
            stem = path.name[: -len(".json")]
            number = 1
            while (path.parent / f"{stem}.previous-{number}.json").exists():
                number += 1
            path.rename(path.parent / f"{stem}.previous-{number}.json")
        owners[bundle] = key
    cd.save_state(bundles=owners)


def record_envelope(path: Path | None, connector: str, operation: str, handle: str | None) -> dict[str, Any] | None:
    """The host's own record of one request's response, when it is that request's and usable here."""
    if path is None or not path.is_file():
        return None
    try:
        envelope = unwrap_envelope(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return None
    if (envelope is None or not matches(envelope, connector, operation) or in_flight(envelope)
            or usable(envelope, True) is not None):
        return None
    if handle:
        account = account_of(envelope)
        if account and account != handle.strip().lstrip("@").lower():
            return None  # its data names another account: let the content decide
    return envelope


def place_by_record(bundle: str, keys: list[str], handles: list[str | None]) -> list[int]:
    """Place each response under `runs/<key>` of its own request; returns the indices still unplaced.

    `keys[i]` belongs to request i in call order (a batch counts each of its requests). The write is
    persist_envelope.py's own (same bundle shape, same serialization), under the same run lock.
    """
    records = cd.LAST_RECORDS
    if records is None or len(records) != len(keys):
        return list(range(len(keys)))
    base = run_dir_of(Path(bundle).resolve()) or Path(bundle).resolve().parent
    left: list[int] = []
    with run_lock(base):
        for index, (key, (request, path)) in enumerate(zip(keys, records)):
            envelope = record_envelope(path, str(request.get("connector_id")), str(request.get("operation")),
                                       handles[index])
            if envelope is None:
                left.append(index)
                continue
            write_json_atomic(Path(bundle), place(read_json_or_empty(Path(bundle)), ["runs", key], envelope))
    return left


def reorder_runs(bundle: str, keys: list[str]) -> None:
    """Keep `runs` in the order the manual workflow placed them (call order), whatever order they landed in."""
    document = read_json_or_empty(Path(bundle))
    runs = document.get("runs") if isinstance(document, dict) else None
    if not isinstance(runs, dict):
        return
    order = [key for key in keys if key in runs] + [key for key in runs if key not in keys]
    if order != list(runs):
        document["runs"] = {key: runs[key] for key in order}
        write_json_atomic(Path(bundle), document)


# --------------------------------------------------------------------------- start


def projected_source_video(data: dict[str, Any]) -> dict[str, Any] | None:
    """What the Request is written from, read from lookup_tiktok_video's fixed projection."""
    # null 表示拿不到：省略并在 unavailable 里列出，不当 0。thumbnail_url 是会过期的 CDN 链接，与旧路径
    # 一样不打印；投影本来就没有播放地址。回退路线作答时注明路线，计数可能来自另一家数据源。
    video = data.get("video")
    if not isinstance(video, dict):
        return None
    author = video.get("author") if isinstance(video.get("author"), dict) else {}
    metrics = video.get("metrics") if isinstance(video.get("metrics"), dict) else {}
    sound = video.get("sound") if isinstance(video.get("sound"), dict) else {}
    source = data.get("source") if isinstance(data.get("source"), dict) else {}
    fields: dict[str, Any] = {
        "id": video.get("id"),
        "url": video.get("url"),
        "caption": video.get("caption"),
        "published_at": video.get("published_at"),
        "author": {key: author.get(key) for key in ("handle", "nickname", "followers") if author.get(key) is not None},
        "metrics": {key: metrics.get(key) for key in ("views", "likes", "comments", "shares") if metrics.get(key) is not None},
        "duration_s": video.get("duration_s"),
        "sound": {key: sound.get(key) for key in ("title", "author", "is_original") if sound.get(key) is not None},
        "route": source.get("route") if source.get("route") == "fallback" else None,
        "unavailable": [name for name in data.get("unavailable_fields") or [] if isinstance(name, str)],
    }
    return {key: value for key, value in fields.items() if value not in (None, "", [], {})}


def print_source_video() -> None:
    envelope = cd.load(SOURCE_VIDEO)
    data = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
    # 投影与旧 get_video_by_share_url 原样（旧运行的快照）并存：按 data.projection 分支读。
    projected = data.get("projection") == VIDEO_PROJECTION
    item = data.get("video" if projected else "item")
    item = item if isinstance(item, dict) else None
    if envelope.get("status") not in cd.USABLE_STATUSES or not item:
        # 投影的 video 为 null：私密、已删除或图文帖，服务端在 limitations 里说明。
        why = (envelope.get("limitations") or [None])[0] if projected and envelope.get("status") in cd.USABLE_STATUSES else None
        say(f"source video: not resolved (status={envelope.get('status')}; "
            f"{str(why or envelope.get('error') or envelope.get('summary') or '')[:200]})")
        return
    if projected:
        say("source video (the Creator is its author; creator_profile.handle comes from author.handle here): "
            + json.dumps(projected_source_video(data), ensure_ascii=False))
        return
    shown = {key: value for key, value in item.items() if key not in SOURCE_SKIP}
    say("source video (the Creator is its author; creator_profile.handle comes from creator_handle here): "
        + json.dumps(shown, ensure_ascii=False))


def share_url_of(value: str) -> str:
    """The user's TikTok link in the form the host accepts, or as given (less its query) when it is neither.

    Shared links carry tracking queries (`?is_from_webapp=1&sender_device=pc`, `?_r=1&_t=…`) that the
    host's allowlist refuses, which turned every source-video read into a hand-over.
    """
    text = value.strip()
    match = TIKTOK_VIDEO.match(text)
    if match:
        return f"https://www.tiktok.com/{match.group(1)}/video/{match.group(2)}"
    match = TIKTOK_SHORT.match(text)
    if match:
        return f"https://{match.group(1).lower()}.tiktok.com/{match.group(2)}/"
    return re.split(r"[?#]", text, 1)[0]


def source_call(video: str) -> dict[str, Any]:
    return cd.run_call(DISCOVERY, "get_video_by_share_url", {"share_url": video})


def place_source() -> None:
    # lookup_tiktok_video 的信封记实际作答的路线：短链是 get_video_by_share_url，链接带 id 时是
    # tiktok-discovery/get_video，回退时是 tiktok-search/get_video。本 Skill 没有别的取视频操作，
    # 所以先按短链那条的操作名、再按 get_video（不限 connector）放置。
    def by_content() -> None:
        try:
            cd.script("persist_envelope.py", "--operation", "get_video_by_share_url", "--any-status", "--dest", SOURCE_VIDEO)
        except cd.StageError:
            cd.script("persist_envelope.py", "--operation", "get_video", "--any-status", "--dest", SOURCE_VIDEO)

    cd.place_record("get_video_by_share_url", SOURCE_VIDEO, by_content)


def read_profile_argument(args: argparse.Namespace) -> bytes | None:
    """The profile file's bytes, checked before a run directory is reserved for it."""
    given = getattr(args, "profile", None)
    if not isinstance(given, str) or not given.strip():
        return None
    if isinstance(getattr(args, "video", None), str) and args.video.strip():
        raise cd.StageError("--profile and --video are two different starts: a supplied video names its own author as "
                            "the Creator, the profile names the account it was read for. Pass one of them")
    path = Path(given).expanduser().resolve()
    try:
        raw = path.read_bytes()
        intake.read_profile(json.loads(raw.decode("utf-8")))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, intake.ProfileError) as error:
        raise cd.StageError(f"the profile file {given} cannot be used: {error}") from error
    return raw


def place_profile(raw: bytes | None) -> None:
    if raw is None:
        return
    placed = Path(PROFILE_SOURCE)
    # 用户可能在本轮中途改了画像。冻结后的 Request 已经带着旧画像的字段，换文件会让同一轮前后两半口径
    # 不一致，所以字节不同就停下，让模型开一个新 run；同一份文件重跑 start 照常。
    if placed.is_file() and placed.read_bytes() != raw:
        raise cd.StageError("the profile file differs from the one this run started with: the profile changed during "
                            "the run. Start a new run with the new file")
    placed.write_bytes(raw)
    for line in intake.describe(json.loads(raw.decode("utf-8")), PLATFORM):
        say(line)


def stage_start(args: argparse.Namespace) -> None:
    profile = read_profile_argument(args)
    cd.reserve_run_directory(args.placed, args=args)
    cd.show_schema("trend-research-request.schema.json", "tiktok-trend-query-plan.schema.json")
    place_profile(profile)
    video = share_url_of(str(args.video or ""))
    if not video:
        return
    calls = [source_call(video)]
    try:
        cd.fetch_round(calls, args, place_source)
    except SystemExit as handover:
        if handover.code == 0:
            # 下载交付的结果不带 data，模型从调用结果里读不到标题与作者：fetch 之后再跑一次
            # start --placed 由脚本打印。没再跑 start 时 `discover` 照旧放置这份回答。
            cd.save_state(source_pending=calls)
            say("That call's result is the source video. Fetch (or stage) it, then run this same start command again "
                "with --placed added: it places the response and prints the caption, author.handle and public "
                "numbers to write the Request from.")
        raise
    cd.save_state(source_pending=None)
    if Path(SOURCE_VIDEO).exists():
        print_source_video()


def place_pending_source() -> None:
    """Place a source-video response the model made after `start` handed the call over."""
    pending = cd.state().get("source_pending")
    if not pending or Path(SOURCE_VIDEO).exists():
        return
    try:
        place_source()
        say(f"[drive] placed the source-video response as {SOURCE_VIDEO}")
        done = list(cd.state().get("rounds") or [])
        cd.save_state(rounds=[*done, round_key(pending)])
    except cd.StageError as error:
        say(f"[drive] no source-video response to place ({str(error).splitlines()[-1][:200]}); going on without "
            f"{SOURCE_VIDEO}")
    cd.save_state(source_pending=None)


# --------------------------------------------------------------------------- discover


def freeze(args: argparse.Namespace) -> None:
    """Freeze the Request window once; a run again (a rewritten plan, --placed) keeps it.

    The window is derived from the time of freezing, so freezing again on every run would move it and
    make the same plan a different collection.
    """
    policy_args = [args.policy, args.since or "", args.until or ""] if args.policy else None
    frozen = cd.state().get("frozen") or {}
    same_policy = policy_args is None or policy_args == frozen.get("args")
    if frozen.get("sha") and same_policy:
        if frozen["sha"] == file_sha(REQUEST):
            return
        # The Request written out again (a heredoc chained before a rerun, or a corrected field) under
        # the same policy: keep the window frozen the first time rather than deriving a later one.
        request = cd.load(REQUEST)
        if not isinstance(request, dict):
            raise cd.StageError(f"{REQUEST} must hold the Request object")
        for field in ("window", "window_policy"):
            if field in frozen.get("window", {}):
                request[field] = frozen["window"][field]
            else:
                request.pop(field, None)
        Path(REQUEST).write_text(json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        cd.save_state(frozen={**frozen, "sha": file_sha(REQUEST)})
        say(f"[drive] kept the window frozen earlier: {json.dumps(frozen['window'].get('window'))}")
        return
    if policy_args is None:
        raise cd.StageError(f"{REQUEST} is not frozen yet: run discover with --policy <{'|'.join(POLICIES)}> "
                            "(and --since/--until for user_named_span)")
    extra = [*(["--since", args.since] if args.since else []), *(["--until", args.until] if args.until else [])]
    cd.script("freeze_request_window.py", "--request", REQUEST, "--policy", args.policy, *extra, show=True)
    request = cd.load(REQUEST)
    cd.save_state(frozen={"sha": file_sha(REQUEST), "args": policy_args,
                          "window": {f: request[f] for f in ("window", "window_policy") if f in request}})



def drop_blank(path: str, keys: tuple[str, ...]) -> None:
    """An empty mechanical field carries nothing the model decided; treat it as left out."""
    target = Path(path)
    if not target.is_file():
        return
    document = cd.load(target)
    if not isinstance(document, dict):
        return  # the shape check that reads it says so
    blank = [key for key in keys if key in document and document[key] in ("", None)]
    if blank:
        for key in blank:
            del document[key]
        target.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def complete_request_and_plan(plan_schema: str) -> None:
    """Fill the Request's and plan's mechanical fields the model left out (never one it wrote)."""
    for name in (REQUEST, PLAN):
        drop_blank(name, ("schema_version", "request_id"))
    request = cd.complete_artifact(REQUEST, {"schema_version": "trend-research-request.v3", "request_id": cd.run_id()})
    cd.complete_artifact(PLAN, {"schema_version": plan_schema, "request_id": request.get("request_id")}, intent_ids=True)


def attach_profile() -> bool:
    """Copy the saved account profile's fields into the Request; whether any profile field is attached.

    Signature content may be missing while the topics the Creator excluded are not; the plan check,
    the `include` fields and the eligibility filter follow any field, the seats only signature content.

    Runs before the window is frozen, so the frozen Request already carries them and a rerun finds
    nothing to change. Without a profile file it only removes profile fields written by hand.
    """
    source = Path(PROFILE_SOURCE)
    try:
        report = intake.attach_file(Path(REQUEST), source if source.is_file() else None, PLATFORM)
    except intake.ProfileError as error:
        raise cd.StageError(str(error)) from error
    if report.get("dropped"):
        say(f"[drive] removed {', '.join(report['dropped'])} from creator_profile: those fields come only from a "
            "supplied profile file")
    if source.is_file():
        say(f"[drive] creator profile: {json.dumps(report, ensure_ascii=False)}")
    return intake.profile_fields_attached(cd.load(REQUEST))


def candidates_file() -> str:
    """The candidate set every step after discovery reads: the eligible subset when a profile made one."""
    return ELIGIBLE if Path(ELIGIBLE).is_file() else CANDIDATES


def search_region(request: dict[str, Any], given: str | None) -> str | None:
    """The one region every call carries: the Request's, else the one the model named, else the
    market the Creator set in the saved profile, else none.

    Copied as written, never inferred: the region decides what the searches return. The market is
    the last default only: the ask's own region and `--region` come first, and it never enters
    `locale.region`, which the Gate scores against.
    """
    locale = request.get("locale") if isinstance(request.get("locale"), dict) else {}
    frozen = locale.get("region")
    frozen = frozen.strip() if isinstance(frozen, str) and frozen.strip() else None
    named = str(given).strip() if given and str(given).strip() else None
    if frozen and named and named != frozen:
        raise cd.StageError(f"--region {named} conflicts with the Request's locale.region {frozen}; every call uses "
                            "the Request region, so drop --region or correct the Request")
    creator = request.get("creator_profile") if isinstance(request.get("creator_profile"), dict) else {}
    market = creator.get("market_region")
    market = market if isinstance(market, str) and intake.REGION.match(market) else None
    return frozen or named or market


def discovery_settings(request: dict[str, Any], plan: dict[str, Any], region: str | None) -> dict[str, Any]:
    """publish_time and the board day, fixed the first time this collection runs.

    Both depend on the clock. A stage run again later (after a hand-over, past midnight UTC) must send
    the same calls, or it would be a new collection instead of the one already made.
    """
    inputs = json.dumps([[i.get("search_query") for i in plan.get("intents") or []], region,
                         request.get("window")], sort_keys=True, ensure_ascii=False)
    saved = cd.state().get("discovery") or {}
    if saved.get("inputs") == inputs:
        return saved
    resolved = json.loads(cd.script("resolve_search_publish_time.py", "--request", REQUEST))
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    settings = {"inputs": inputs, "publish_time": resolved["publish_time"],
                "reaches_since": resolved.get("reaches_since"), "days_back": resolved.get("days_back"),
                "board_d1": int((today - timedelta(days=1)).timestamp())}
    cd.save_state(discovery=settings)
    return settings


def search_params(keyword: str, sort_type: int, region: str | None, publish_time: int,
                  profiled: bool = False) -> dict[str, Any]:
    params: dict[str, Any] = {"keyword": keyword, "sort_type": sort_type, "count": SEARCH_COUNT}
    if region is not None:
        params["region"] = region
    params["publish_time"] = publish_time
    if profiled:
        params["include"] = list(SEARCH_INCLUDE)
    return params


def board_params(stamp: int, region: str | None) -> dict[str, Any]:
    params: dict[str, Any] = {"period_end_timestamp": stamp, "period_dimension": BOARD_DIMENSION}
    if region is not None:
        params["country_code"] = region
    params["limit"] = BOARD_LIMIT
    return params


def search_key(number: int, route: str) -> str:
    """Numbered rather than named after the intent; the candidate builder reads the route from the
    request provenance, never from the key."""
    return f"intent-{number}-{route}"


def rekey_by_query(keys: list[str], keywords: list[str]) -> None:
    """After placement by landing order, move each search response onto the run of the keyword it answered.

    Calls the model makes together finish in any order, and the placement fallback takes them in the
    order the host recorded them. A search response names its own query (`data.query`); a failed one
    names none and takes the run no answered query claims. Within one round each keyword is sent once.
    """
    bundle = read_json_or_empty(Path(ENVELOPES))
    runs = bundle.get("runs") if isinstance(bundle, dict) else None
    if not isinstance(runs, dict) or not all(isinstance(runs.get(key), dict) for key in keys):
        return
    queries = [(runs[key].get("data") or {}).get("query") if isinstance(runs[key].get("data"), dict) else None
               for key in keys]
    queries = [query if isinstance(query, str) else None for query in queries]
    if all(query in (None, keyword) for query, keyword in zip(queries, keywords)):
        return
    wanted = list(keywords)
    for query in queries:
        if query is None:
            continue
        if query not in wanted:
            return  # an answer to no query of this round: leave it for the candidate builder to reject
        wanted.remove(query)
    known: dict[str, list[dict[str, Any]]] = {}
    unknown: list[dict[str, Any]] = []
    for key, query in zip(keys, queries):
        (known.setdefault(query, []) if query is not None else unknown).append(runs[key])
    for key, keyword in zip(keys, keywords):
        runs[key] = known[keyword].pop(0) if known.get(keyword) else unknown.pop(0)
    write_json_atomic(Path(ENVELOPES), bundle)
    say("[drive] search responses landed out of order; each was placed under the query it answered")


def trace_placement(slot: str, output: str) -> None:
    """Name the host record this placement actually consumed.

    Which staged response a slot takes is the one fact that decides whether each query keeps its own
    answer, and nothing used to record it: `cd.script` captures a placement's JSON and returns it
    without printing, so a round that silently re-took an earlier response looked exactly like one
    that advanced — until the candidate builder rejected the bundle with "request/response query
    mismatch", which points at the data rather than the placement. 2026-09-21 that cost four fix
    attempts across the connector, the staging area and the envelope identity before the placement
    itself came into view. One line per placement makes the next one a glance.
    """
    try:
        placed = json.loads(output)
    except (json.JSONDecodeError, TypeError):
        return
    if not isinstance(placed, dict):
        return
    source = str(placed.get("source") or "")
    say(f"[drive] {slot} placed from {source.rsplit('/', 1)[-1] or '(unnamed)'}"
        f" request_id={placed.get('request_id') or '(none)'} rows={placed.get('rows')}")


def place_round(calls: list[dict[str, Any]], keys: list[str]) -> None:
    """Place one discovery round: by the host's record of each request, else in call order by content."""
    left = place_by_record(ENVELOPES, keys, [None] * len(keys))
    for index in left:
        arguments = calls[index]["arguments"]
        # Without the host's record of which call staged which response (the model made the calls):
        # persist_envelope.py in call order, as the manual chain did; it takes the earliest response of
        # that operation not yet placed.
        trace_placement(keys[index], cd.script(
            "persist_envelope.py", "--into", ENVELOPES, "--at", "runs", "--at", keys[index],
            "--connector", DISCOVERY, "--operation", str(arguments["operation"]), "--any-status"))
    searches = [i for i, call in enumerate(calls) if call["arguments"]["operation"] == "search_videos"]
    if left:
        rekey_by_query([keys[i] for i in searches], [calls[i]["arguments"]["params"]["keyword"] for i in searches])


def write_provenance(order: list[str], params: dict[str, dict[str, Any]]) -> None:
    """discovery-requests.json: what `persist_envelope.py --record-request --params` wrote for each run.

    `connector_id`, `operation` and `request_id` come from the placed response, `params` from the
    request that was sent — at the same run key, in the same order.
    """
    runs = (read_json_or_empty(Path(ENVELOPES)) or {}).get("runs") or {}
    # A search the host refused for `include` went out again without it; record what was sent.
    refused = set(cd.state().get("include_refused") or [])
    provenance: dict[str, Any] = {}
    for key in order:
        envelope = runs.get(key)
        if not isinstance(envelope, dict):
            raise cd.StageError(f"{ENVELOPES} has no response at runs/{key}; run discover again with --placed once "
                                "every call it printed has been made")
        provenance = place(provenance, ["runs", key], {
            "connector_id": envelope.get("connector_id"),
            "operation": envelope.get("operation"),
            "request_id": envelope.get("request_id"),
            "params": ({k: v for k, v in params[key].items() if k != "include"}
                       if envelope.get("operation") in refused else params[key]),
        })
    if read_json_or_empty(Path(PROVENANCE)) != provenance:
        write_json_atomic(Path(PROVENANCE), provenance)


def board_needs_fallback() -> bool:
    """D-2 is asked once, only after a completed D-1 board came back empty."""
    runs = (read_json_or_empty(Path(ENVELOPES)) or {}).get("runs") or {}
    first = runs.get(BOARD_KEYS[0])
    return (isinstance(first, dict) and first.get("status") == "completed"
            and isinstance(first.get("data"), dict) and first["data"].get("items") == [])


def stage_discover(args: argparse.Namespace) -> None:
    if need_authored(REQUEST, PLAN):
        return
    originals = {name: Path(name).read_bytes() for name in (REQUEST, PLAN)}
    complete_request_and_plan("tiktok-trend-query-plan.v1")
    if not _locale_preflight(args, originals):
        return
    place_pending_source()
    profiled = attach_profile()
    freeze(args)
    cd.script("validate_tiktok_query_plan.py", PLAN, *(("--request", REQUEST) if profiled else ()), show=True)
    request, plan = cd.load(REQUEST), cd.load(PLAN)
    region = search_region(request, args.region)
    settings = discovery_settings(request, plan, region)
    publish_time = settings["publish_time"]
    say(f"[drive] resolve_search_publish_time: publish_time={publish_time}, reaches_since="
        f"{json.dumps(settings.get('reaches_since'))}; region={region or '(none)'}")
    if settings.get("reaches_since") is False:
        say("[drive] publish_time does not reach back to the window's start: the collection's coverage is "
            "incomplete; keep that in data_gaps")
    keywords = [str(intent["search_query"]) for intent in plan["intents"]]
    d1 = int(settings["board_d1"])
    say(f"[drive] ranked board D-1: {datetime.fromtimestamp(d1, timezone.utc).date().isoformat()} "
        f"(period_end_timestamp {d1})")

    order: list[str] = []
    params: dict[str, dict[str, Any]] = {}
    rounds: list[tuple[list[dict[str, Any]], list[str]]] = [([], []), ([], [])]
    for number, keyword in enumerate(keywords, start=1):
        for index, (sort_type, route) in enumerate(SORTS):
            key = search_key(number, route)
            order.append(key)
            params[key] = search_params(keyword, sort_type, region, publish_time, profiled)
            rounds[index][0].append(cd.run_call(DISCOVERY, "search_videos", params[key]))
            rounds[index][1].append(key)
    order.append(BOARD_KEYS[0])
    params[BOARD_KEYS[0]] = board_params(d1, region)
    rounds[0][0].append(cd.run_call(DISCOVERY, "list_top_contents", params[BOARD_KEYS[0]]))
    rounds[0][1].append(BOARD_KEYS[0])

    first_calls, first_keys = rounds[0]

    def place_first() -> None:
        fresh_bundle((ENVELOPES, PROVENANCE), first_calls)
        place_round(first_calls, first_keys)

    # Every route is its own data_connector_run, never a batch: a host-level batch failure would erase
    # each route's own terminal evidence.
    with intake.include_fallback(cd, args):
        cd.fetch_round(first_calls, args, place_first)

    second_calls, second_keys = rounds[1]
    if board_needs_fallback():
        order.append(BOARD_KEYS[1])
        params[BOARD_KEYS[1]] = board_params(d1 - DAY, region)
        second_calls = [*second_calls, cd.run_call(DISCOVERY, "list_top_contents", params[BOARD_KEYS[1]])]
        second_keys = [*second_keys, BOARD_KEYS[1]]
        say("[drive] the D-1 board came back empty; asking D-2 once")
    with intake.include_fallback(cd, args):
        cd.fetch_round(second_calls, args, lambda: place_round(second_calls, second_keys))

    reorder_runs(ENVELOPES, order)
    write_provenance(order, params)
    cd.script("build_tiktok_candidate_set.py", "--request", REQUEST, "--envelopes", ENVELOPES,
              "--requests", PROVENANCE, "--output", CANDIDATES, show=True)
    if profiled:
        cd.script("filter_candidate_eligibility.py", "--request", REQUEST, "--candidates", CANDIDATES,
                  "--output", ELIGIBLE, "--platform", PLATFORM, show=True)
    else:
        Path(ELIGIBLE).unlink(missing_ok=True)
    cd.script("project_relevance_gate_input.py", "--request", REQUEST, "--candidates", candidates_file(),
              "--output", GATE_INPUT, "--table", show=True)
    cd.say(json.dumps(gate_builder.stage_contract(cd.load(GATE_INPUT), GATE_SCORES, "rank", __file__), ensure_ascii=False))


# --------------------------------------------------------------------------- rank


def history_key(handle: str) -> str:
    return handle.strip().lstrip("@").lower()


def build_ranking_input(profiled: bool = False) -> dict[str, Any]:
    """The shortlist for this Gate. Once account histories were fetched for it, the Gate is frozen."""
    fresh = Path(".tiktok-trend-ranking-input.next.json")
    fresh.unlink(missing_ok=True)
    try:
        say(cd.script("build_tiktok_ranking_input.py", "--request", REQUEST, "--candidates", candidates_file(),
                      "--gate", GATE, *(("--signature-content",) if profiled else ()), "--output", str(fresh)).rstrip())
        if Path(RANKING).exists() and fresh.read_bytes() == Path(RANKING).read_bytes():
            return cd.load(RANKING)
        # fresh_bundle records the history bundle once a history round has been placed.
        if Path(RANKING).exists() and (cd.state().get("bundles") or {}).get(HISTORIES):
            raise cd.StageError(f"the Gate changed after the account histories were already fetched for {RANKING}; "
                                f"the Gate is frozen before account-history retrieval: restore {GATE_SCORES} to the "
                                "scores it was built from")
        fresh.replace(RANKING)
    finally:
        fresh.unlink(missing_ok=True)
    return cd.load(RANKING)


def stage_rank(args: argparse.Namespace) -> None:
    need(REQUEST, candidates_file(), GATE_INPUT, GATE_SCORES)
    current = cd.state()
    history_round = current.get("history_round")
    history_handed = bool(history_round) and history_round in (current.get("handed") or {})
    if not gate_preflight([(GATE_INPUT, GATE_SCORES, GATE)], "rank",
                          Path(ENRICHMENT_RESPONSE).exists() or bool((current.get("bundles") or {}).get(HISTORIES)) or history_handed):
        return
    cd.script("build_relevance_gate.py", "--input", GATE_INPUT, "--scores", GATE_SCORES, "--output", GATE, show=True)
    # 带画像时 shortlist 按主打内容预分配名额，基线加收缩倍数，最后按同一个名额函数定稿；
    # 不带画像时每条命令与参数都和原来一样。
    profiled = intake.attached(cd.load(REQUEST))
    ranking = build_ranking_input(profiled)
    rows = [row for row in ranking.get("history_requests") or [] if isinstance(row, dict) and row.get("handle")]
    handles = [str(row["handle"]) for row in rows]
    keys = [history_key(handle) for handle in handles]
    if not handles:
        say("[drive] no candidate reached the shortlist, so there is no account history to retrieve")
        if not Path(HISTORIES).exists():
            write_json_atomic(Path(HISTORIES), {"runs": {}})
    else:
        # In the listed order (highest-ranked accounts first), four to a batch: a host that caps the
        # retrievals of a turn refuses the last ones, which leaves the lowest-ranked without a baseline.
        requests = [{"connector_id": PUBLIC, "operation": "list_account_posts",
                     "params": {"handle": handle, "limit": row.get("limit")}} for handle, row in zip(handles, rows)]
        calls = cd.batch_calls(requests)

        def place_histories() -> None:
            fresh_bundle((HISTORIES,), calls)
            left = place_by_record(HISTORIES, keys, list(handles))
            missing = []
            for index in left:
                # `--handle` binds each response to its account, as the manual chain did.
                try:
                    trace_placement(keys[index], cd.script(
                        "persist_envelope.py", "--into", HISTORIES, "--at", "runs", "--at", keys[index],
                        "--connector", PUBLIC, "--operation", "list_account_posts", "--handle", handles[index],
                        "--any-status"))
                except cd.StageError:
                    missing.append(handles[index])
            if missing:
                # A retrieval the host refused (the turn's budget spent) or never made: that account
                # keeps absolute plays only, and nothing retries it.
                say(f"[drive] no account history came back for: {', '.join(missing)}; those accounts keep absolute "
                    "plays only")
            if not Path(HISTORIES).exists():
                write_json_atomic(Path(HISTORIES), {"runs": {}})
            reorder_runs(HISTORIES, keys)

        # `optional`: the placement above already carries on without the histories the host would
        # not give, which is what this stage promises. Without it a refusal ended the stage, so a
        # turn that spent its retrieval budget before this round delivered nothing at all.
        # Source/discovery handovers do not bind this Gate. Record this exact history round before
        # fetch_round can hand it over, so the next rank cannot change scores while it is pending.
        cd.save_state(history_round=round_key(calls))
        cd.fetch_round(calls, args, place_histories, optional=True)
    cd.script("build_tiktok_enrichment.py", "--ranking", RANKING, "--histories", HISTORIES,
              *(("--shrink",) if profiled else ()), "--output", ENRICHMENT_RESPONSE)
    cd.script("build_trend_workflow_snapshot.py", "--request", REQUEST, "--candidates", candidates_file(), "--gate", GATE,
              "--enrichment", ENRICHMENT_RESPONSE, "--merged-enrichment-output", ENRICHMENT, "--output", SNAPSHOT)
    if profiled:
        say(cd.script("allocate_signature_content.py", "--platform", PLATFORM, "--request", REQUEST,
                      "--gate", GATE, "--candidates", candidates_file(), "--enrichment", ENRICHMENT,
                      "--output", ALLOCATION).rstrip())
    cd.script("analyze_public_snapshot.py", SNAPSHOT, "--mode", "trend", "--request", REQUEST,
              "--relevance-gate", GATE, "--trend-enrichment", ENRICHMENT,
              *(("--allocation", ALLOCATION) if profiled else ()), "--output", ANALYSIS)
    cd.say(Path(ANALYSIS).read_text(encoding="utf-8").rstrip())
    cd.script("prepare_hook_context.py", "--platform", "tiktok", "--purpose", "trends",
              "--output", "hook-context.json", show=True)
    cd.show_schema("trend-research-synthesis.schema.json")


# --------------------------------------------------------------------------- report



def report_id_of(request: dict[str, Any]) -> str | None:
    """This run's own name, reused to name its report."""
    request_id = request.get("request_id")
    return f"{request_id}-report" if isinstance(request_id, str) and request_id else None

def language_option(args: argparse.Namespace) -> tuple[str, ...]:
    """The Creator's chat language for the report's closing basis sentence; none keeps English."""
    language = getattr(args, "language", None)
    return ("--language", language.strip()) if isinstance(language, str) and language.strip() else ()


def restore(files: dict[str, bytes | None]) -> None:
    """Put each file back to its bytes from before this stage, removing one the stage created."""
    for name, raw in files.items():
        path = Path(name)
        if raw is None:
            path.unlink(missing_ok=True)
        elif not path.exists() or path.read_bytes() != raw:
            path.write_bytes(raw)


def report_correction(args: argparse.Namespace, authored: dict[str, bytes | None], error: Exception) -> None:
    """One correction of the authored synthesis after Compose or Validate rejects it; never a fetch."""
    try:
        receipt = json.loads(Path("run-manifest.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        receipt = {}  # legacy source hosts may have no wrapper manifest; other I/O stays fatal
    if receipt and receipt.get("status") != "running":
        raise cd.StageError(f"synthesis correction requires this same open run: {error}")
    state = cd.state()
    previous = list(state.get("report_input_errors") or [])
    if previous:
        raise cd.StageError(f"the synthesis remains invalid after its one allowed correction: {error}")
    directory = Path("analysis") / "report-input-errors" / f"attempt-{len(previous) + 1:02d}"
    directory.mkdir(parents=True, exist_ok=False)
    raw = authored.get(SYNTHESIS)
    if raw is not None:
        with (directory / SYNTHESIS).open("xb") as stream:
            stream.write(raw)
    diagnostic = {"error": str(error), "synthesis_sha256": hashlib.sha256(raw).hexdigest() if raw is not None else None}
    with (directory / "error.json").open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(diagnostic, ensure_ascii=False, indent=2) + "\n")
    cd.save_state(report_input_errors=[*previous, str(directory)])
    cd.say(json.dumps({"status": "needs_correction", "reason": "invalid_synthesis",
                       "errors": [str(error)], "original_input_record": str(directory),
                       "corrections_remaining": 1, "run_id": cd.run_id(),
                       "next_stage": {"script": "scripts/drive.py", "arguments": ["report", *language_option(args)]},
                       "instruction": f"Correct only the fields of {SYNTHESIS} that these errors name, from the evidence already in this run, and repeat the report stage once in this same open run. No data was fetched and the run is unchanged. Do not refetch, replace the run or deliver an older report. A second rejection is fatal."}, ensure_ascii=False))


def stage_report(args: argparse.Namespace) -> None:
    need(REQUEST, SNAPSHOT, ANALYSIS, SYNTHESIS)
    # The Request's own id and the synthesis schema are not the model's to retype (audit item 7):
    # a Request written without request_id got the run's, which the synthesis must repeat.
    # `report_id` joins them: it names this report, and this run already has a name. The model
    # invented a slug for it every time and was sent back to write one whenever it forgot — twice
    # out of two runs on 2026-09-20, each costing a whole round-trip to a field that carries no
    # judgment. A slug the model does write is still kept.
    request = cd.load(REQUEST)
    authored = {name: Path(name).read_bytes() if Path(name).exists() else None for name in (SYNTHESIS, REPORT)}
    cd.complete_artifact(SYNTHESIS, {"schema_version": "trend-research-synthesis.v3.3",
                                     "request_id": request.get("request_id"),
                                     "report_id": report_id_of(request)})
    try:
        cd.script("compose_report.py", "--request", REQUEST, "--snapshot", SNAPSHOT, "--analysis", ANALYSIS,
                  "--synthesis", SYNTHESIS, "--output", REPORT)
        cd.script("validate_output.py", REPORT, "--request", REQUEST, "--analysis", ANALYSIS)
    except cd.StageError as error:
        # Nothing is fetched here, so a synthesis that Compose or Validate rejects (a momentum claim
        # from one snapshot, a ref outside the evidence) is the model's to fix. Putting the run back
        # as it was and answering needs_correction keeps the run open for one corrected repeat
        # instead of closing it with every fetched result (plugins, 2026-10-28).
        restore(authored)
        report_correction(args, authored, error)
        return
    cd.script("render_creator_report.py", REPORT, CREATOR_REPORT, *language_option(args))
    report = Path(CREATOR_REPORT).resolve()
    # Deliver by this path where the host can send a file as one block; its text follows unchanged.
    say(f"creator_report: {report}")
    say(report.read_text(encoding="utf-8").rstrip())


STAGES = {
    "start": stage_start,
    "discover": stage_discover,
    "rank": stage_rank,
    "report": stage_report,
}


def gate_preflight(specs: list[tuple[str, str, str]], stage: str, frozen: bool) -> bool:
    try:
        errors = gate_builder.preflight_scores(
            specs, frozen=frozen,
            frozen_message="the Gate changed after its account-history work was bound; the Gate is frozen before account-history retrieval: restore its original score lines")
    except gate_builder.FrozenGateError as error:
        raise cd.StageError(str(error)) from error
    if not errors:
        return True
    contracts = [gate_builder.stage_contract(cd.load(input_file), scores_file, stage, __file__)
                 for input_file, scores_file, _ in specs]
    cd.say(json.dumps({"status": "needs_correction", "reason": "invalid_score_input", "errors": errors,
                       "score_contracts": [row["gate_scoring"] for row in contracts],
                       "next_stage": contracts[0]["next_stage"],
                       "instruction": "Only correct the authored score lines in this same open run. No fetch or Gate write occurred; do not recollect or replace the run."}, ensure_ascii=False))
    return False



def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="stage", required=True)
    start = sub.add_parser("start")
    start.add_argument("--video", help="the source video's URL as the user gave it, when the ask supplies one")
    start.add_argument("--profile", help="the Creator's saved account profile file, when the host supplies one")
    discover = sub.add_parser("discover")
    discover.add_argument("--policy", choices=POLICIES, help="the window policy the ask was classified as")
    discover.add_argument("--since", help="user_named_span only: the span's start, kept exactly")
    discover.add_argument("--until", help="user_named_span only: the span's end, kept exactly")
    discover.add_argument("--question-file", help="- to read the user's original ask, verbatim, from standard input "
                                                  "(a heredoc on the same command); or a file holding it")
    discover.add_argument("--without-include", action="store_true",
                          help="Kabo refused handed-over calls only for their `include` parameter: hand them over "
                               "once more without it")
    discover.add_argument("--region", help="the one search region every call carries, when the Request region is "
                                           "unknown (null)")
    for name in ("rank", "report"):
        sub.add_parser(name)
    sub.choices["report"].add_argument("--language", help="the Creator's chat language (BCP 47, such as zh-CN)")
    for command in sub.choices.values():
        command.add_argument("--placed", action="store_true",
                             help="the connector calls were already made by hand; place their responses")
    return cd.run_stage(STAGES, parser.parse_args(), first=("start",),
                        model_files=(REQUEST, PLAN, GATE_SCORES, SYNTHESIS))


if __name__ == "__main__":
    raise SystemExit(main())
