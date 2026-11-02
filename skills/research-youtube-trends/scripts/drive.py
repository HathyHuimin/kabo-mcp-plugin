#!/usr/bin/env python3
"""Run everything between two judgments of a YouTube trend scan, so the model only judges.

A longer-window YouTube trend scan needs the model for three decisions: the frozen Request with its
query plan, the relevance Gate scores, and the synthesis (a fourth, occasionally: a replacement plan
when the first collection loses a required route). Everything between them is mechanical. Measured
2026-09-18 on two runs, that plumbing was 16 and 21 model requests (278 s and 206 s), each paying the
model's fixed cost to type a command the workflow had already fixed.

Each stage below starts right after one judgment and stops right before the next one: it makes the
connector calls the judgment implies, places each response with the same persist command the manual
workflow ran, runs the same deterministic scripts with the same arguments, and prints exactly what
the next judgment reads.

    start    [--video URL|ID | --profile F]
                                         run directory; source video (get_video), or the Creator's
                                         saved account profile -> Request + plan schemas
    discover [--policy P [--since --until]] [--question-file -]
                                         attach the profile -> freeze + validate -> collect -> persist
                                         -> coverage (-> eligible candidates, with a profile) -> Gate table
    rank                                 Gate -> ranking input -> enrich -> persist -> Snapshot -> Analyze
                                         -> Analysis + synthesis schema
    report                               Compose -> Validate -> Render -> creator-report.md

The manual commands these stages replace, unchanged in what they run and write:

    persist_envelope.py --schema youtube-trend-candidate-set.v1 --dest youtube-trend-candidate-set.json
    validate_youtube_candidate_coverage.py --plan trend-query-plan.json youtube-trend-candidate-set.json
    project_relevance_gate_input.py ... --output trend-relevance-gate-input.json --table
    build_relevance_gate.py --input trend-relevance-gate-input.json --scores trend-relevance-gate.tsv ...
    build_youtube_ranking_input.py ... --output trend-ranking-input.json
    persist_envelope.py --schema youtube-trend-enrichment.v1 --dest trend-enrichment-01.json
    build_trend_workflow_snapshot.py ... ; analyze_public_snapshot.py ... --mode trend ...
    compose_report.py ... ; validate_output.py ... ; render_creator_report.py ...

The stages never make connector calls themselves (connector_driver.py): a stage prints the call for
the model to make as a named public MCP tool and stops, and the same stage run again with
`--placed` places that response from this run's staged copy.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Any

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import connector_driver as cd  # noqa: E402
import build_relevance_gate as gate_builder  # authored score validation, never semantic scoring

YT = "youtube-public"
REQUEST = "trend-research-request.json"
PLAN = "trend-query-plan.json"
CANDIDATES = "youtube-trend-candidate-set.json"
FIRST_CANDIDATES = "youtube-trend-candidate-set-first.json"
CANDIDATE_SCHEMA = "youtube-trend-candidate-set.v1"
GATE_INPUT = "trend-relevance-gate-input.json"
GATE_SCORES = "trend-relevance-gate.tsv"
GATE = "trend-relevance-gate.json"
RANKING_INPUT = "trend-ranking-input.json"
ENRICHMENT_SCHEMA = "youtube-trend-enrichment.v1"
ENRICHMENT_RESPONSE = "trend-enrichment-01.json"
ENRICHMENT = "trend-enrichment.json"
# With the Creator's account profile attached: the seats each signature content item holds.
ALLOCATION = "signature-content-allocation.json"
SNAPSHOT = "public-content-snapshot.json"
ANALYSIS = "public-content-analysis.json"
SYNTHESIS = "trend-research-synthesis.json"
REPORT = "trend-research-report.json"
CREATOR_REPORT = "creator-report.md"
SOURCE_VIDEO = "source-video.json"
# 用户原话逐字存在 run 里，冻结 Request 时据此核对 locale.named_in_ask。
QUESTION = Path("analysis") / "original-question.txt"

# The collection budget of the longer-window scan. `results_per_route` is never omitted: the
# backend default is 25, and that first-page prefix is not this skill's measured discovery ceiling.
COLLECT_BUDGET = {"results_per_route": 50, "max_provider_calls": 4, "max_candidates": 160}
POLICIES = ("default_1y", "recent_90d", "user_named_span")


def _validator(name: str):
    # These validator modules differ by platform; never reuse another package's sys.modules entry.
    path = Path(__file__).with_name(name + ".py")
    spec = importlib.util.spec_from_file_location(f"{path.parent.parent.name}:{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


query_input_validator = _validator("validate_query_plan")
window_input_validator = _validator("freeze_request_window")
intake = _validator("attach_creator_profile")
PLATFORM = "youtube"
PROFILE_SOURCE = intake.PROFILE_SOURCE
# With the Creator's account profile attached, every step after coverage reads this subset instead.
ELIGIBLE = "youtube-trend-candidate-set-eligible.json"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _input_error(args: argparse.Namespace, originals: dict[str, bytes], error: ValueError) -> bool:
    """One correction of known authored inputs, never a data/host/internal error handler."""
    state = cd.state()
    manifest = Path("run-manifest.json")
    try:
        receipt = json.loads(manifest.read_text(encoding="utf-8"))
    except FileNotFoundError:
        receipt = {}  # legacy source hosts may have no wrapper manifest; other I/O stays fatal
    if receipt and receipt.get("status") != "running":
        raise cd.StageError("discovery input correction requires this same open run")
    if (state.get("collected") or state.get("collections") or state.get("rounds")
            or state.get("handed") or receipt.get("provider_requests")):
        raise cd.StageError(f"discovery inputs are frozen after data work began: {error}")
    previous = list(state.get("discovery_input_errors") or [])
    directory = Path("analysis") / "discovery-input-errors" / f"attempt-{len(previous) + 1:02d}"
    directory.mkdir(parents=True, exist_ok=False)
    hashes = {}
    for name, raw in originals.items():
        with (directory / name).open("xb") as stream:
            stream.write(raw)
        hashes[name] = hashlib.sha256(raw).hexdigest()
    diagnostic = {"error": str(error), "input_sha256": hashes,
                  "window": (state.get("frozen") or {}).get("window"),
                  "window_arguments": {key: getattr(args, key, None) for key in ("policy", "since", "until", "last_days")}}
    with (directory / "error.json").open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(diagnostic, ensure_ascii=False, indent=2) + "\n")
    cd.save_state(discovery_input_errors=[*previous, str(directory)])
    if previous:
        raise cd.StageError(f"discovery input remains invalid after its one allowed correction: {error}")
    next_arguments = ["discover"]
    for key in ('policy', 'since', 'until', 'last_days'):
        value = getattr(args, key, None)
        if value is not None:
            next_arguments.extend(["--" + key.replace("_", "-"), str(value)])
    cd.say(json.dumps({"status": "needs_correction", "reason": "invalid_discovery_input",
                       "errors": [str(error)], "original_input_record": str(directory),
                       "corrections_remaining": 1, "run_id": cd.run_id(),
                       "next_stage": {"script": "scripts/drive.py", "arguments": next_arguments},
                       "instruction": "Correct only these authored inputs and repeat the documented non-final discover stage in this same open run with its required window arguments. No data was fetched. Do not replace the run, broaden its window, prune search intents or repeat completed calls. A second invalid input is fatal."}, ensure_ascii=False))
    return False


def _authored_inputs(args: argparse.Namespace) -> dict[str, bytes] | None:
    # File access is outside the narrow JSON-input exception handler: I/O remains fatal.
    originals = {name: Path(name).read_bytes() for name in (REQUEST, PLAN)}
    try:
        for name, raw in originals.items():
            if not isinstance(json.loads(raw.decode("utf-8")), dict):
                raise ValueError(f"{name} must hold an object")
    except (json.JSONDecodeError, ValueError) as error:
        _input_error(args, originals, error)
        return None
    return originals


def _window_error(args: argparse.Namespace, originals: dict[str, bytes], error: ValueError) -> bool:
    # Report independent local plan errors together, so the single correction is informed.
    plan = cd.load(PLAN)  # I/O/decoding faults outside the pure validator exception handler
    try:
        query_input_validator.validate(plan)
    except ValueError as plan_error:
        error = ValueError(f"{error}; query plan: {plan_error}")
    return _input_error(args, originals, error)


def _window_preflight(args: argparse.Namespace, originals: dict[str, bytes]) -> bool:
    state = cd.state()
    frozen = state.get("frozen") or {}
    relative = getattr(args, "last_days", None)
    key = ([args.policy, "last_days", str(relative)] if relative is not None
           else [args.policy, args.since or "", args.until or ""] if args.policy else None)
    if frozen.get("sha") and relative is not None and (args.since or args.until):
        raise cd.StageError("the window is already frozen; conflicting span arguments cannot replace it")
    if frozen.get("sha") and key is not None and key != frozen.get("args"):
        raise cd.StageError("the window is already frozen; discovery arguments cannot replace it")
    relative = getattr(args, "last_days", None)
    if relative is not None:
        if args.policy != "user_named_span" or args.since or args.until or relative <= 0:
            return _window_error(args, originals, ValueError("--last-days needs user_named_span and a positive day count, without --since/--until"))
        key = [args.policy, "last_days", str(relative)]
        if frozen.get("sha"):
            if frozen.get("args") != key:
                raise cd.StageError("the window is already frozen; --last-days cannot replace it")
            return True
        try:
            now = _utc_now().replace(microsecond=0)
            args._resolved_bounds = (window_input_validator.stamp(now - timedelta(days=relative)),
                                     window_input_validator.stamp(now))
        except OverflowError as error:
            return _window_error(args, originals, ValueError(f"--last-days is outside the representable UTC calendar: {error}"))
    elif frozen.get("sha"):
        # A repeated request still uses the exact previously frozen window.
        return True
    if args.policy is None:
        return True  # preserve the existing missing-policy fatal contract
    try:
        window_input_validator.freeze(dict(cd.load(REQUEST)), args.policy, _utc_now(),
                                      *getattr(args, "_resolved_bounds", (args.since, args.until)))
    except ValueError as error:
        return _window_error(args, originals, error)
    return True


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
        cd.say("[drive] warning: no --question-file was given, so locale.region / locale.language were not "
               "checked against the user's ask")
    key = [locale.get("region"), locale.get("language")]
    error = window_input_validator.locale_named_error(request, question)
    checked = cd.state().get("locale_named_in_ask") or {}
    if error and "named_in_ask" not in locale and checked.get("locale") == key:
        error = None  # 同一 locale 本 run 已核对过，引语已移进 state（重跑 / --placed）
    if error:
        return _window_error(args, originals, ValueError(error))
    if question is not None and any(key):
        cd.save_state(locale_named_in_ask={"locale": key, "quote": locale.get("named_in_ask", checked.get("quote"))})
    if "named_in_ask" in locale:
        locale.pop("named_in_ask")
        Path(REQUEST).write_text(json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True


VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
VIDEO_URL = re.compile(r"(?:[?&]v=|youtu\.be/|/shorts/|/embed/|/live/|/v/)([A-Za-z0-9_-]{11})(?![A-Za-z0-9_-])")
# Fields of the source video the Request is written from; thumbnails and the API kind are not read.
SOURCE_SKIP = {"thumbnails", "kind"}


def stop(message: str) -> None:
    """A stop the workflow defines as final: say what to tell the user and end without a retry hint."""
    cd.say(message)
    raise SystemExit(2)


def round_key(calls: list[dict[str, Any]]) -> str:
    """The key connector_driver.fetch_round records a round under."""
    return hashlib.sha256(json.dumps(calls, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def file_sha(path: str | Path) -> str | None:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def persist(*args: str) -> str:
    """Every persist_envelope.py call of the stages goes through here, from this run's staged copies."""
    return cd.script("persist_envelope.py", *args)


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


# --------------------------------------------------------------------------- start


def video_id_of(value: str) -> str:
    text = value.strip()
    if VIDEO_ID.match(text):
        return text
    match = VIDEO_URL.search(text)
    if not match:
        raise cd.StageError(f"cannot read a YouTube video id from {value!r}; pass the watch URL or the 11-character id")
    return match.group(1)


def print_source_video() -> None:
    envelope = cd.load(SOURCE_VIDEO)
    rows = (envelope.get("data") or {}).get("rows") or []
    if envelope.get("status") not in cd.USABLE_STATUSES or not rows:
        cd.say(f"source video: not resolved (status={envelope.get('status')}; "
               f"{str(envelope.get('error') or envelope.get('summary') or '')[:200]})")
        return
    for row in rows:
        shown = {key: value for key, value in row.items() if key not in SOURCE_SKIP}
        cd.say("source video (the Creator is its author; creator_profile.handle comes from channel_title / "
               "channel_id here): " + json.dumps(shown, ensure_ascii=False))


def read_profile_argument(args: argparse.Namespace) -> bytes | None:
    """The profile file's bytes, checked before a run directory is reserved for it."""
    given = getattr(args, "profile", None)
    if not isinstance(given, str) or not given.strip():
        return None
    if isinstance(getattr(args, "video", None), str) and args.video.strip():
        raise cd.StageError("--profile and --video are two different starts: a supplied video names its own channel as "
                            "the Creator, the profile names the account it was read for. Pass one of them")
    path = Path(given).expanduser().resolve()
    try:
        raw = path.read_bytes()
        intake.read_profile(json.loads(raw.decode("utf-8")))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, intake.ProfileError) as error:
        raise cd.StageError(f"the profile file {given} cannot be used: {error}") from error
    return raw


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
        cd.say(f"[drive] removed {', '.join(report['dropped'])} from creator_profile: those fields come only from a "
               "supplied profile file")
    if source.is_file():
        cd.say(f"[drive] creator profile: {json.dumps(report, ensure_ascii=False)}")
    return intake.profile_fields_attached(cd.load(REQUEST))


def candidates_file() -> str:
    """The candidate set every step after coverage reads: the eligible subset when a profile made one."""
    return ELIGIBLE if Path(ELIGIBLE).is_file() else CANDIDATES


def stage_start(args: argparse.Namespace) -> None:
    profile = read_profile_argument(args)
    cd.reserve_run_directory(args.placed, args=args)
    if args.video:
        video = video_id_of(args.video)
        cd.fetch_round([cd.run_call(YT, "get_video", {"video_id": video})], args, lambda: cd.place_record(
            "get_video", SOURCE_VIDEO, lambda: persist(
                "--connector", YT, "--operation", "get_video", "--any-status",
                "--dest", SOURCE_VIDEO)))
        print_source_video()
    cd.show_schema("trend-research-request.schema.json", "trend-query-plan.schema.json")
    if profile is not None:
        placed = Path(PROFILE_SOURCE)
        # 用户可能在本轮中途改了画像。冻结后的 Request 已经带着旧画像的字段，换文件会让同一轮前后两半口径
        # 不一致，所以字节不同就停下，让模型开一个新 run；同一份文件重跑 start 照常。
        if placed.is_file() and placed.read_bytes() != profile:
            raise cd.StageError("the profile file differs from the one this run started with: the profile changed "
                                "during the run. Start a new run with the new file")
        placed.write_bytes(profile)
        for line in intake.describe(json.loads(profile.decode("utf-8")), PLATFORM):
            cd.say(line)


# --------------------------------------------------------------------------- discover


def freeze(args: argparse.Namespace) -> None:
    """Freeze the Request window once; a run again (a replacement plan, --placed) keeps it.

    The window is derived from the time of freezing, so freezing again on every run would move it and
    make the same plan a different collection.
    """
    relative = getattr(args, "last_days", None)
    policy_args = ([args.policy, "last_days", str(relative)] if relative is not None
                   else [args.policy, args.since or "", args.until or ""] if args.policy else None)
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
        cd.say(f"[drive] kept the window frozen earlier: {json.dumps(frozen['window'].get('window'))}")
        return
    if policy_args is None:
        raise cd.StageError(f"{REQUEST} is not frozen yet: run discover with --policy <{'|'.join(POLICIES)}> "
                            "(and --since/--until for user_named_span)")
    since, until = getattr(args, "_resolved_bounds", (args.since, args.until))
    extra = [*(["--since", since] if since else []), *(["--until", until] if until else [])]
    cd.script("freeze_request_window.py", "--request", REQUEST, "--policy", args.policy, *extra, show=True)
    request = cd.load(REQUEST)
    cd.save_state(frozen={"sha": file_sha(REQUEST), "args": policy_args,
                          "window": {f: request[f] for f in ("window", "window_policy") if f in request}})


def collect_call(request: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    """`collect_youtube_trend_candidates` with the frozen intents, the exact window and the fixed budget."""
    window = request.get("window") or {}
    if not window.get("since") or not window.get("until"):
        raise cd.StageError(f"{REQUEST} has no window.since / window.until")
    locale = request.get("locale") or {}
    arguments: dict[str, Any] = {
        "platform": "youtube",
        # The collector names each intent's phrase `query`; the plan names it `search_query`.
        "intents": [{"intent_id": intent["intent_id"], "query": intent["search_query"]}
                    for intent in plan.get("intents") or []],
        "published_after": window["since"],
        "published_before": window["until"],
        "budget": dict(COLLECT_BUDGET),
    }
    # language 只取 Request 的 locale：collect 把它当搜索语言过滤传出去，是硬过滤，画像里的语言只是检索标签，
    # 绝不进这里。region 在 locale 没给时退到创作者自己设的市场，那只是缺省检索地区。
    if locale.get("language"):
        arguments["language"] = locale["language"]
    creator = request.get("creator_profile") if isinstance(request.get("creator_profile"), dict) else {}
    market = creator.get("market_region")
    if locale.get("region"):
        arguments["region"] = locale["region"]
    elif isinstance(market, str) and intake.REGION.match(market):
        arguments["region"] = market
    return {"tool": "collect_youtube_trend_candidates", "arguments": arguments}



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


def failed_intents(output: str, plan: dict[str, Any]) -> list[str]:
    ids = list(dict.fromkeys(re.findall(r"'intent_id': '([^']+)'", output)))
    queries = {str(i.get("intent_id")): str(i.get("search_query")) for i in plan.get("intents") or []}
    return [f"{intent_id} ({queries.get(intent_id, '?')!r})" for intent_id in ids]


def stage_discover(args: argparse.Namespace) -> None:
    if need_authored(REQUEST, PLAN):
        return
    originals = _authored_inputs(args)
    if originals is None:
        return
    complete_request_and_plan("trend-query-plan.v1")
    profiled = attach_profile()
    if not _locale_preflight(args, originals):
        return
    if not _window_preflight(args, originals):
        return
    freeze(args)
    try:
        query_input_validator.validate(cd.load(PLAN))
        if profiled:
            query_input_validator.validate_against_request(cd.load(PLAN), cd.load(REQUEST))
    except ValueError as error:
        if not _input_error(args, originals, error):
            return
    cd.script("validate_request_window.py", REQUEST, show=True)
    cd.script("validate_query_plan.py", PLAN, *(("--request", REQUEST) if profiled else ()), show=True)
    request, plan = cd.load(REQUEST), cd.load(PLAN)
    calls = [collect_call(request, plan)]
    key = round_key(calls)
    collections: list[dict[str, Any]] = list(cd.state().get("collections") or [])
    entry = next((c for c in collections if c.get("key") == key), None)
    if entry is None and collections:
        if any(c.get("covered") for c in collections):
            raise cd.StageError("this run already collected its candidates with a different plan or window, and that "
                                f"collection passed coverage; restore {PLAN} and {REQUEST} to what it collected with. "
                                "Collection runs once per validated plan.")
        if len(collections) >= 2:
            stop("This run already recollected once and that plan failed coverage as well. Stop: never produce a "
                 "Creator report from a second failed plan. Tell the user the YouTube search returned too little for "
                 "these intents right now.")
    if entry is not None and entry.get("covered") is False:
        if len(collections) >= 2:
            stop("The recollection failed coverage as well. Stop: never produce a Creator report from a second failed "
                 "plan. Tell the user the YouTube search returned too little for these intents right now.")
        raise cd.StageError(f"this plan already failed coverage (its result is kept as {FIRST_CANDIDATES}); write the "
                            f"replacement plan to {PLAN} and run discover again without --placed")

    def place() -> None:
        try:
            persist("--schema", CANDIDATE_SCHEMA, "--dest", CANDIDATES)
        except cd.StageError as error:
            stop(f"{error}\nThe collection result was not recorded by the host. Stop and report that the collection "
                 "result was not recorded by the host: never write, hand-truncate or reconstruct `items` or a "
                 "shortlist yourself, and never call collection again to repair a local file.")

    if entry is None:
        collections.append({"key": key, "covered": None})
    handed = list(cd.state().get("handed_collections") or [])
    if args.placed and collections[:-1] and key not in handed:
        # A recollection that was never handed over has no response of the model's to place; with a
        # stray --placed its persist would find nothing new and end the run. Fetch it instead.
        args.placed = False
    try:
        cd.fetch_round(calls, args, place)
    except SystemExit as handover:
        if handover.code == 0:
            cd.save_state(handed_collections=[*handed, key])
        raise
    need(CANDIDATES)
    try:
        cd.say(cd.script("validate_youtube_candidate_coverage.py", "--plan", PLAN, CANDIDATES).rstrip())
    except cd.StageError as error:
        for c in collections:
            if c.get("key") == key:
                c["covered"] = False
        cd.save_state(collections=collections)
        if len(collections) >= 2:
            stop(f"{error}\nThe recollection failed coverage as well. Stop: never produce a Creator report from a "
                 "second failed plan. Tell the user the YouTube search returned too little for these intents right now.")
        # Kept under another name so the recollection's persist takes the new result: it takes the
        # earliest result not yet on disk, so while the first sits at the target it copies it again.
        Path(CANDIDATES).replace(FIRST_CANDIDATES)
        failed = failed_intents(str(error), plan)
        raise cd.StageError(
            f"{error}\nThe first collection failed coverage for: {', '.join(failed) or 'see above'}. It is kept as "
            f"{FIRST_CANDIDATES}. Shorten only the failed query to its 2–5 core topic terms, write the replacement "
            f"plan to {PLAN} (same intent_ids; leave {REQUEST} as it is) and run discover again without --placed: "
            "it validates the plan and recollects once.")
    for c in collections:
        if c.get("key") == key:
            c["covered"] = True
    cd.save_state(collections=collections)
    # Coverage judges the collection as the backend returned it; eligibility then narrows what the
    # Creator is shown, so a thin eligible set never reads as a failed route.
    if profiled:
        cd.script("filter_candidate_eligibility.py", "--request", REQUEST, "--candidates", CANDIDATES,
                  "--output", ELIGIBLE, "--platform", PLATFORM, show=True)
    else:
        Path(ELIGIBLE).unlink(missing_ok=True)
    cd.script("project_relevance_gate_input.py", "--request", REQUEST, "--candidates", candidates_file(),
              "--output", GATE_INPUT, "--table", show=True)
    cd.say(json.dumps(gate_builder.stage_contract(cd.load(GATE_INPUT), GATE_SCORES, "rank", __file__), ensure_ascii=False))


# --------------------------------------------------------------------------- rank


def build_ranking_input() -> dict[str, Any]:
    """The ranking input for this Gate, built once; the builder refuses to overwrite its output."""
    args = ["--request", REQUEST, "--candidates", candidates_file(), "--gate", GATE]
    if not Path(RANKING_INPUT).exists():
        cd.say(cd.script("build_youtube_ranking_input.py", *args, "--output", RANKING_INPUT).rstrip())
        return cd.load(RANKING_INPUT)
    fresh = Path(".trend-ranking-input.next.json")
    fresh.unlink(missing_ok=True)
    try:
        cd.script("build_youtube_ranking_input.py", *args, "--output", str(fresh))
        same = fresh.read_bytes() == Path(RANKING_INPUT).read_bytes()
        if not same:
            if Path(ENRICHMENT_RESPONSE).exists():
                raise cd.StageError(f"the Gate changed after the enrichment already ran on {RANKING_INPUT}; the Gate is "
                                    f"frozen before exact metrics: restore {GATE_SCORES} to the scores it was built from")
            fresh.replace(RANKING_INPUT)
            cd.say(f"[drive] {RANKING_INPUT} rebuilt from the changed Gate")
    finally:
        fresh.unlink(missing_ok=True)
    return cd.load(RANKING_INPUT)


def stage_rank(args: argparse.Namespace) -> None:
    need(REQUEST, candidates_file(), GATE_INPUT, GATE_SCORES)
    if not gate_preflight([(GATE_INPUT, GATE_SCORES, GATE)], "rank",
                          Path(ENRICHMENT_RESPONSE).exists() or (Path(RANKING_INPUT).exists() and bool(cd.state().get("handed")))):
        return
    cd.script("build_relevance_gate.py", "--input", GATE_INPUT, "--scores", GATE_SCORES, "--output", GATE, show=True)
    ranking = build_ranking_input()
    selected = ranking.get("selected", ranking.get("items")) or []
    base = ["--request", REQUEST, "--candidates", candidates_file(), "--gate", GATE]
    if not selected:
        # No item reached both floors: never call enrichment with an empty array.
        cd.say("[drive] no candidate reached both Gate floors; building the empty Snapshot without enrichment")
        cd.script("build_trend_workflow_snapshot.py", *base, "--empty-enrichment-output", ENRICHMENT,
                  "--output", SNAPSHOT)
    else:
        def place() -> None:
            try:
                persist("--schema", ENRICHMENT_SCHEMA, "--dest", ENRICHMENT_RESPONSE)
            except cd.StageError as error:
                stop(f"{error}\nThe enrichment result was not recorded by the host. Stop and report that the "
                     "enrichment result was not recorded by the host: never write or reconstruct it yourself, and "
                     "never call enrichment again to repair a local file.")

        # The ranking input verbatim as the tool arguments: never rebuilt, re-derived, trimmed or reordered.
        cd.fetch_round([{"tool": "enrich_youtube_shortlist", "arguments": ranking}], args, place)
        need(ENRICHMENT_RESPONSE)
        cd.script("build_trend_workflow_snapshot.py", *base, "--enrichment", ENRICHMENT_RESPONSE,
                  "--merged-enrichment-output", ENRICHMENT, "--output", SNAPSHOT)
    # 带画像时在服务端 enrich 定下的条目上按主打内容分名额；不带画像时命令与参数和原来一样。
    profiled = intake.attached(cd.load(REQUEST))
    if profiled:
        cd.say(cd.script("allocate_signature_content.py", "--platform", PLATFORM, "--request", REQUEST,
                         "--gate", GATE, "--candidates", candidates_file(), "--enrichment", ENRICHMENT,
                         "--output", ALLOCATION).rstrip())
    cd.script("analyze_public_snapshot.py", SNAPSHOT, "--mode", "trend", "--request", REQUEST,
              "--relevance-gate", GATE, "--trend-enrichment", ENRICHMENT,
              *(("--allocation", ALLOCATION) if profiled else ()), "--output", ANALYSIS)
    cd.say(Path(ANALYSIS).read_text(encoding="utf-8").rstrip())
    cd.script("prepare_hook_context.py", "--platform", "youtube", "--purpose", "trends",
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
    cd.say(f"creator_report: {report}")
    cd.say(report.read_text(encoding="utf-8").rstrip())


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
            frozen_message="the Gate changed after its data work was bound; the Gate is frozen before exact metrics: restore its original score lines")
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
    start.add_argument("--video", help="the source video's URL or id, when the ask supplies one")
    start.add_argument("--profile", help="the Creator's saved account profile file, when the host supplies one")
    discover = sub.add_parser("discover")
    discover.add_argument("--policy", choices=POLICIES, help="the window policy the ask was classified as")
    discover.add_argument("--since", help="user_named_span only: the span's start, kept exactly")
    discover.add_argument("--until", help="user_named_span only: the span's end, kept exactly")
    discover.add_argument("--question-file", help="- to read the user's original ask, verbatim, from standard input "
                                                  "(a heredoc on the same command); or a file holding it")
    discover.add_argument("--last-days", type=int, help="user_named_span only: explicit positive relative day count; freezes current UTC once, mutually exclusive with --since/--until")
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
