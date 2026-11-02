#!/usr/bin/env python3
"""Run everything between two judgments of an Instagram Reel trend scan, so the model only judges.

A Reel trend scan needs the model for three decisions — the frozen Request with its query plan,
the relevance Gate scores (one table per discovery lane), and the synthesis. Everything between
them is mechanical. Measured 2026-09-18 on two desktop runs (one lane and two lanes), that plumbing
was 21 and 24 model requests: reading schemas, freezing the window, validating the plan once per
lane, persisting and binding the two lane results, projecting each Gate table, building the Gate,
the ranking input, the Snapshot and the Analysis — each one a model turn paying the model's fixed
cost to type a command the workflow had already fixed.

Each stage below starts right after one judgment and stops right before the next one: it makes the
connector calls the judgment implies, places each response with the same persist command the manual
workflow ran, runs the same deterministic scripts with the same arguments, and prints exactly what
the next judgment reads.

    start    [--post URL | --profile F]  run directory; source Reel (get_post + get_transcript),
                                         printed -> Request + plan schemas; or the Creator's saved
                                         account profile -> its signature content
    discover --policy P [--since --until] [--without-include] [--question-file -]
                                         attach the profile -> freeze -> validate the plan per lane
                                         -> collect every lane -> persist (+ bind the two lanes)
                                         (-> eligible candidates, with a profile) -> relevance Gate table
    gate                                 two lanes only: relevance Gate -> reach Gate table
    rank                                 Gate(s) -> ranking input -> enrich -> persist -> Snapshot
                                         -> Analyze -> Analysis + synthesis schema
    report                               Compose -> Validate -> Render -> creator-report.md

The manual commands these stages replace, unchanged in what they run and write:

    freeze_request_window.py --request trend-research-request.json --policy P [--since --until]
    validate_query_plan.py trend-query-plan.json --request … --discovery-window W --collector-output …
    persist_envelope.py --schema trend-candidate-set.v1 --dest trend-candidate-set[-lane-N].json
    bind_lane_candidate_sets.py --relevance-input … --reach-input … --set … --set … (two lanes)
    project_relevance_gate_input.py … --output trend-relevance-gate-input[-reach].json --table
    build_relevance_gate.py --input … --scores trend-relevance-gate[-reach].tsv --output …
    build_instagram_ranking_input.py … [--reach-candidates … --reach-gate …] --output trend-ranking-input.json
    persist_envelope.py --schema trend-enrichment.v1 --dest trend-enrichment-01.json
    build_trend_workflow_snapshot.py … ; analyze_public_snapshot.py … --mode trend …
        (two lanes: over trend-candidate-set-union.json and trend-relevance-gate-union.json)
    compose_report.py … ; validate_output.py … ; render_creator_report.py …

Connector calls are handed to the model (connector_driver.py): the stage prints each call as a
named /mcp-pub tool call and stops, the model makes it and stages the complete result with
`static_host.py stage`, and the same stage run again with `--placed` places that response. With a
profile attached the collect calls carry `include`; when Kabo refuses one only for it, the same
stage run with `--without-include` hands it over once more without it (attach_creator_profile.py).
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import re
import sys
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import connector_driver as cd  # noqa: E402
import build_relevance_gate as gate_builder  # authored score validation, never semantic scoring

IG = "instagram-discovery"
REQUEST = "trend-research-request.json"
# 用户原话逐字存在 run 里，冻结 Request 时据此核对 locale.named_in_ask。
QUESTION = Path("analysis") / "original-question.txt"
PLAN = "trend-query-plan.json"
CANDIDATE_SCHEMA = "trend-candidate-set.v1"
ENRICHMENT_SCHEMA = "trend-enrichment.v1"
SOURCE_POST = "source-post.json"
# lookup_instagram_post 给源帖 data 的固定投影，与作答路线无关。
POST_PROJECTION = "kabo.instagram_post.v1"
# 源 Reel 要读的四组补充字段：评论预览、标记用户、地点，
# 以及合作者与付费合作标记。只有请求了的组才出现在投影里，不额外计费。
SOURCE_INCLUDE = ["comments_preview", "tagged_users", "location", "collaboration"]
SOURCE_TRANSCRIPT = "source-transcript.json"
GATE_SCORES = "trend-relevance-gate.tsv"
RANKING_INPUT = "trend-ranking-input.json"
ENRICH_CALL = "trend-enrichment-call.json"
ENRICHMENT_RESPONSE = "trend-enrichment-01.json"
ENRICHMENT = "trend-enrichment.json"
# With the Creator's account profile attached: the seats each signature content item holds.
ALLOCATION = "signature-content-allocation.json"
SNAPSHOT = "public-content-snapshot.json"
ANALYSIS = "public-content-analysis.json"
SYNTHESIS = "trend-research-synthesis.json"
REPORT = "trend-research-report.json"
CREATOR_REPORT = "creator-report.md"
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
PLATFORM = "instagram"
PROFILE_SOURCE = intake.PROFILE_SOURCE


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
    for key in ('policy', 'since', 'until'):
        value = getattr(args, key, None)
        if value is not None:
            next_arguments.extend(["--" + key.replace("_", "-"), str(value)])
    cd.say(json.dumps({"status": "needs_correction", "reason": "invalid_discovery_input",
                       "errors": [str(error)], "original_input_record": str(directory),
                       "corrections_remaining": 1, "run_id": cd.run_id(),
                       "query_plan_contract": {"maximum_search_intents": 6,
                           "maximum_popular_intents": query_input_validator.maximum_popular_intents({"window": (state.get("frozen") or {}).get("window")}, 6)},
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


def _window_preflight(args: argparse.Namespace, originals: dict[str, bytes]) -> bool:
    state = cd.state()
    frozen = state.get("frozen") or {}
    if frozen.get("sha") and args.policy is not None:
        key = [args.policy, args.since or "", args.until or ""]
        if key != frozen.get("args"):
            if state.get("collected"):
                raise cd.StageError("the Request is immutable once collection starts; the window is already frozen")
            raise cd.StageError("the window is already frozen; discovery arguments cannot replace it")
    if frozen.get("sha"):
        # Existing freeze() owns restoration of the original window on a repeated heredoc.
        return True
    if args.policy is None:
        return True  # preserve the existing missing-policy fatal contract
    try:
        window_input_validator.freeze(dict(cd.load(REQUEST)), args.policy, _utc_now(),
                                      args.since, args.until)
    except ValueError as error:
        return _input_error(args, originals, error)
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
        try:
            # 同一次改正要看到全部可独立发现的 plan 错误
            query_input_validator.validate(cd.load(PLAN), request)
            query_input_validator.validate_against_request(cd.load(PLAN), request)
        except ValueError as plan_error:
            error = f"{error}; query plan: {plan_error}"
        return _input_error(args, originals, ValueError(error))
    if question is not None and any(key):
        cd.save_state(locale_named_in_ask={"locale": key, "quote": locale.get("named_in_ask", checked.get("quote"))})
    if "named_in_ask" in locale:
        locale.pop("named_in_ask")
        Path(REQUEST).write_text(json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True


# Each discovery lane's files. The relevance lane keeps the names the one-lane workflow always used;
# the reach lane's carry `-reach`, and the two collector results are persisted under neutral names
# first, because both return the same schema and only their echoed window says which lane is which.
# `eligible` is the subset every step after collection reads when the Creator's account profile is
# attached (scripts/filter_candidate_eligibility.py); without a profile it is never written.
LANES = {
    "relevance": {"input": "trend-collection-input.json", "candidates": "trend-candidate-set.json",
                  "eligible": "trend-candidate-set-eligible.json",
                  "gate_input": "trend-relevance-gate-input.json", "scores": "trend-relevance-gate.tsv",
                  "gate": "trend-relevance-gate.json"},
    "reach": {"input": "trend-collection-input-reach.json", "candidates": "trend-candidate-set-reach.json",
              "eligible": "trend-candidate-set-reach-eligible.json",
              "gate_input": "trend-relevance-gate-input-reach.json", "scores": "trend-relevance-gate-reach.tsv",
              "gate": "trend-relevance-gate-reach.json"},
}


def lane_candidates(name: str) -> str:
    """The candidate set of a lane that the Gate, ranking and Snapshot read."""
    eligible = LANES[name]["eligible"]
    return eligible if Path(eligible).is_file() else LANES[name]["candidates"]


NEUTRAL_SETS = ("trend-candidate-set-lane-1.json", "trend-candidate-set-lane-2.json")

POST_URL = re.compile(r"instagram\.com/(?:[A-Za-z0-9._]+/)?(reels?|p|tv)/([A-Za-z0-9_-]{5,64})", re.IGNORECASE)
TOP_COMMENTS = 3


def stop(message: str) -> None:
    """A stop the workflow defines as final: say what to tell the user and end without a retry hint."""
    cd.say(message)
    raise SystemExit(2)


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


def file_sha(path: str | Path) -> str | None:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def persist(*args: str) -> str:
    return cd.script("persist_envelope.py", *args)


# --------------------------------------------------------------------------- start


def post_url_of(value: str) -> str:
    """The canonical post address `get_post` and `get_transcript` take: no query, no profile prefix."""
    match = POST_URL.search(value.strip())
    if not match:
        raise cd.StageError(f"cannot read an Instagram post or Reel address from {value!r}; pass the Reel URL "
                            "(https://www.instagram.com/reel/<shortcode>/)")
    kind = {"reels": "reel", "tv": "p"}.get(match.group(1).lower(), match.group(1).lower())
    return f"https://www.instagram.com/{kind}/{match.group(2)}/"


def caption_of(post: dict[str, Any]) -> str | None:
    edges = ((post.get("edge_media_to_caption") or {}).get("edges")) or []
    texts = [((edge or {}).get("node") or {}).get("text") for edge in edges]
    texts = [text for text in texts if isinstance(text, str) and text.strip()]
    if texts:
        return "\n".join(texts)
    caption = post.get("caption")
    if isinstance(caption, dict):
        caption = caption.get("text")
    return caption if isinstance(caption, str) else None


def values_at(edges: Any, path: tuple[str, ...]) -> list[str]:
    """The string at `path` inside each edge, skipping edges without one."""
    names = []
    for edge in edges or []:
        value: Any = edge
        for key in path:
            value = (value or {}).get(key) if isinstance(value, dict) else None
        if isinstance(value, str) and value:
            names.append(value)
    return names


def projected_source_fields(data: dict[str, Any]) -> dict[str, Any]:
    """`source_fields` for lookup_instagram_post's fixed projection, under the same printed names."""
    # null 表示拿不到：省略并在 unavailable 里列出，不当 0。plays_instagram 只是 IG 自身播放数，
    # IG+FB 合并值单列，不顶替 play_count。点赞被隐藏时没有 like_count，只标 likes_hidden。
    # thumbnail_url 是会过期的 CDN 链接，与旧路径一样不打印媒体地址。评论预览只有正文，不带评论者身份。
    post = data.get("post") or {}
    owner = post.get("owner") or {}
    metrics = post.get("metrics") or {}
    audio = post.get("audio") or {}
    location = post.get("location") or {}
    hidden = metrics.get("likes_hidden") is True
    fields: dict[str, Any] = {
        "shortcode": post.get("shortcode"),
        "author": {key: owner.get(key) for key in ("username", "full_name", "is_verified") if owner.get(key) is not None},
        "caption": post.get("caption") if isinstance(post.get("caption"), str) else None,
        "published_at": post.get("published_at"),
        "product_type": post.get("product_type"),
        "video_duration": post.get("duration_s"),
        "play_count": metrics.get("plays_instagram"),
        "play_count_instagram_and_facebook": metrics.get("plays_combined"),
        "like_count": None if hidden else metrics.get("likes"),
        "likes_hidden": True if hidden else None,
        "comment_count": metrics.get("comments"),
        "is_paid_partnership": post.get("is_paid_partnership"),
        "audio": {key: audio.get(key) for key in ("artist_name", "song_name", "uses_original_audio")
                  if audio.get(key) is not None},
        "tagged_users": values_at(post.get("tagged_users"), ("username",)),
        "coauthors": values_at(post.get("coauthors"), ("username",)),
        "location": location.get("name") if isinstance(location, dict) else None,
        "top_comments": values_at(post.get("comments_preview"), ("text",))[:TOP_COMMENTS],
        "unavailable": [name for name in data.get("unavailable_fields") or [] if isinstance(name, str)],
    }
    return {key: value for key, value in fields.items() if value not in (None, "", [], {})}


def source_fields(post: dict[str, Any]) -> dict[str, Any]:
    """What the Request is written from: the author, the Reel's own words, its public numbers.

    Media and profile-picture addresses are left out; they say nothing about the format.
    """
    owner = post.get("owner") or post.get("user") or {}
    comments = ((post.get("edge_media_to_parent_comment") or post.get("edge_media_preview_comment") or {})
                .get("edges")) or []
    music = post.get("clips_music_attribution_info") or {}
    location = post.get("location") or {}
    fields: dict[str, Any] = {
        "shortcode": post.get("shortcode") or post.get("code"),
        "author": {key: owner.get(key) for key in ("username", "full_name", "is_verified") if key in owner},
        "caption": caption_of(post),
        "published_at": post.get("created_at") or post.get("taken_at_timestamp") or post.get("taken_at"),
        "product_type": post.get("product_type"),
        "video_duration": post.get("video_duration"),
        "play_count": post.get("video_play_count", post.get("play_count")),
        "like_count": (post.get("edge_media_preview_like") or {}).get("count", post.get("like_count")),
        "comment_count": post.get("comment_count", (post.get("edge_media_to_comment") or {}).get("count")),
        "is_paid_partnership": post.get("is_paid_partnership"),
        "audio": {key: music.get(key) for key in ("artist_name", "song_name", "uses_original_audio") if key in music},
        "tagged_users": values_at((post.get("edge_media_to_tagged_user") or {}).get("edges"), ("node", "user", "username")),
        "coauthors": values_at(post.get("coauthor_producers"), ("username",)),
        "location": location.get("name") if isinstance(location, dict) else None,
        "top_comments": values_at(comments, ("node", "text"))[:TOP_COMMENTS],
    }
    return {key: value for key, value in fields.items() if value not in (None, "", [], {})}


def print_source() -> None:
    envelope = cd.load(SOURCE_POST)
    data = envelope.get("data") or {}
    post = data.get("post")
    if envelope.get("status") not in cd.USABLE_STATUSES or not isinstance(post, dict):
        cd.say(f"source Reel: not resolved (status={envelope.get('status')}; "
               f"{str(envelope.get('error') or envelope.get('summary') or '')[:200]})")
    else:
        handle = ((post.get("owner") or {}).get("username")) or "?"
        # 投影与旧 GraphQL 形状并存（旧运行的快照、重签读回的原始信封）：按 data.projection 分支读。
        fields = projected_source_fields(data) if data.get("projection") == POST_PROJECTION else source_fields(post)
        cd.say(f"source Reel (the Creator is its author: creator_profile.handle is @{handle}, its owner.username): "
               + json.dumps(fields, ensure_ascii=False))
    transcript = cd.load(SOURCE_TRANSCRIPT)
    data = transcript.get("data") or {}
    text = data.get("text") if isinstance(data.get("text"), str) else " ".join(
        str(row.get("text")) for row in data.get("rows") or [] if isinstance(row, dict) and row.get("text"))
    if transcript.get("status") in cd.USABLE_STATUSES and text.strip():
        language = data.get("language")
        cd.say(f"source Reel transcript{f' ({language})' if language else ''}: {text.strip()}")
    else:
        cd.say(f"source Reel transcript: none (status={transcript.get('status')}; "
               f"{str(data.get('transcript_evidence') or transcript.get('summary') or '')[:200]})")


def read_profile_argument(args: argparse.Namespace) -> bytes | None:
    """The profile file's bytes, checked before a run directory is reserved for it."""
    given = getattr(args, "profile", None)
    if not isinstance(given, str) or not given.strip():
        return None
    if isinstance(getattr(args, "post", None), str) and args.post.strip():
        raise cd.StageError("--profile and --post are two different starts: a supplied Reel names its own author as "
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


def stage_start(args: argparse.Namespace) -> None:
    profile = read_profile_argument(args)
    cd.reserve_run_directory(args.placed, args=args)
    if args.post:
        url = post_url_of(args.post)
        # One batch: both reads go out together, and a host that takes only one of them hands the
        # pair to the model in one call — the same call the manual workflow made.
        # get_post 交接成 lookup_instagram_post（include 由 static_host.public_calls 原样带上）。
        requests = [{"connector_id": IG, "operation": "get_post", "params": {"url": url, "include": SOURCE_INCLUDE}},
                    {"connector_id": IG, "operation": "get_transcript", "params": {"url": url}}]

        def place() -> None:
            # lookup 的信封记实际作答的路线（主源拒绝时是 instagram-detail-proxy），所以只按 operation
            # 放置；本 Skill 没有别的 get_post 操作。
            cd.place_record("get_post", SOURCE_POST, lambda: persist(
                "--operation", "get_post", "--any-status", "--dest", SOURCE_POST))
            cd.place_record("get_transcript", SOURCE_TRANSCRIPT, lambda: persist(
                "--connector", IG, "--operation", "get_transcript", "--any-status", "--dest", SOURCE_TRANSCRIPT))

        cd.fetch_round(cd.batch_calls(requests), args, place)
        print_source()
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


def freeze(args: argparse.Namespace) -> list[dict[str, str]]:
    """Freeze the Request window once and return its discovery lanes; a run again keeps both.

    The window is derived from the time of freezing, so freezing again on every run would move it
    and make the same plan a different collection.
    """
    policy_args = [args.policy, args.since or "", args.until or ""] if args.policy else None
    frozen = cd.state().get("frozen") or {}
    same_policy = policy_args is None or policy_args == frozen.get("args")
    if frozen.get("sha") and same_policy:
        if frozen["sha"] == file_sha(REQUEST):
            return frozen["lanes"]
        # The Request written out again (a heredoc chained before a rerun, or a corrected field) under
        # the same policy: keep the window frozen the first time rather than deriving a later one.
        request = cd.load(REQUEST)
        if not isinstance(request, dict):
            raise cd.StageError(f"{REQUEST} must hold the Request object")
        request["window"] = frozen["window"]
        Path(REQUEST).write_text(json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        cd.save_state(frozen={**frozen, "sha": file_sha(REQUEST)})
        cd.say(f"[drive] kept the window frozen earlier: {json.dumps(frozen['window'])}")
        return frozen["lanes"]
    if policy_args is None:
        raise cd.StageError(f"{REQUEST} is not frozen yet: run discover with --policy <{'|'.join(POLICIES)}> "
                            "(and --since/--until for user_named_span)")
    if cd.state().get("collected"):
        raise cd.StageError(f"this run already collected with the window frozen under {frozen.get('args')}; the "
                            "Request is immutable once collection starts. Run discover with that same policy.")
    extra = [*(["--since", args.since] if args.since else []), *(["--until", args.until] if args.until else [])]
    printed = cd.script("freeze_request_window.py", "--request", REQUEST, "--policy", args.policy, *extra)
    cd.say(printed.rstrip())
    lanes = json.loads(printed.strip().split("\n")[-1]).get("instagram_lanes")
    if not lanes:
        raise cd.StageError(f"{REQUEST} did not freeze to an Instagram window: set platforms to [\"instagram\"]")
    request = cd.load(REQUEST)
    cd.save_state(frozen={"sha": file_sha(REQUEST), "args": policy_args, "window": request["window"], "lanes": lanes})
    return lanes



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


def collection_inputs(lanes: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Validate the plan once per lane and write each lane's collector input; returns the collect calls.

    Until the collection has run, an input written for an earlier plan is replaced; afterwards the
    validator refuses any other arguments at the same path, as it always did.
    """
    calls = []
    collected = cd.state().get("collected")
    for lane in lanes:
        path = LANES[lane["lane"]]["input"]
        if not collected:
            Path(path).unlink(missing_ok=True)
        try:
            cd.script("validate_query_plan.py", PLAN, "--request", REQUEST, "--discovery-window",
                      lane["discovery_window"], "--collector-output", path, show=True)
        except cd.StageError as error:
            if collected and "already contains different arguments" in str(error):
                raise cd.StageError(f"this run already collected with an earlier {PLAN}; collection runs once per "
                                    f"run, so restore {PLAN} to the plan it collected with") from error
            raise
        calls.append({"tool": "collect_instagram_trend_candidates", "arguments": cd.load(path)})
    return calls


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
    lanes = freeze(args)
    try:
        query_input_validator.validate(cd.load(PLAN), cd.load(REQUEST))
        query_input_validator.validate_against_request(cd.load(PLAN), cd.load(REQUEST))
    except ValueError as error:
        if not _input_error(args, originals, error):
            return
    names = [lane["lane"] for lane in lanes]
    if names not in (["relevance"], ["relevance", "reach"]):
        raise cd.StageError(f"unexpected discovery lanes {names}")
    calls = collection_inputs(lanes)

    def place() -> None:
        try:
            if len(calls) == 1:
                persist("--schema", CANDIDATE_SCHEMA, "--dest", LANES["relevance"]["candidates"])
                return
            for name in NEUTRAL_SETS:
                persist("--schema", CANDIDATE_SCHEMA, "--dest", name)
        except cd.StageError as error:
            stop(f"{error}\nThe collection result was not recorded by the host. Stop and report that the collection "
                 "result was not recorded by the host: never write or reconstruct items yourself, and never call "
                 "collection again to repair a local file, even if the response might be cached.")
        cd.say(cd.script("bind_lane_candidate_sets.py",
                         "--relevance-input", LANES["relevance"]["input"], "--reach-input", LANES["reach"]["input"],
                         "--set", NEUTRAL_SETS[0], "--set", NEUTRAL_SETS[1],
                         "--relevance-output", LANES["relevance"]["candidates"],
                         "--reach-output", LANES["reach"]["candidates"]).rstrip())

    # Both lanes in one round: one hand-over names both collector calls for one response.
    with intake.include_fallback(cd, args):
        cd.fetch_round(calls, args, place)
    cd.save_state(collected=True, lanes=names)
    lane = LANES["relevance"]
    need(lane["candidates"])
    for name in names:
        if profiled:
            cd.script("filter_candidate_eligibility.py", "--request", REQUEST, "--candidates", LANES[name]["candidates"],
                      "--output", LANES[name]["eligible"], "--platform", PLATFORM, show=True)
        else:
            Path(LANES[name]["eligible"]).unlink(missing_ok=True)
    cd.script("project_relevance_gate_input.py", "--request", REQUEST, "--candidates", lane_candidates("relevance"),
              "--output", lane["gate_input"], "--table", show=True)
    cd.say(json.dumps(gate_builder.stage_contract(cd.load(lane["gate_input"]), lane["scores"],
                                                 "gate" if "reach" in names else "rank", __file__), ensure_ascii=False))


# --------------------------------------------------------------------------- gate (two lanes)


def lanes_run() -> list[str]:
    lanes = cd.state().get("lanes")
    if not lanes:
        raise cd.StageError("no collection is recorded for this run yet; run discover first")
    return lanes


def build_gate(name: str) -> None:
    lane = LANES[name]
    need(lane["gate_input"], lane["scores"])
    cd.script("build_relevance_gate.py", "--input", lane["gate_input"], "--scores", lane["scores"],
              "--output", lane["gate"], show=True)


def gate_frozen() -> bool:
    return (Path(ENRICHMENT_RESPONSE).exists() or Path(ENRICH_CALL).exists()
            or (Path(RANKING_INPUT).exists() and bool(cd.state().get("handed"))))


def stage_gate(args: argparse.Namespace) -> None:
    names = lanes_run()
    if names == ["relevance"]:
        if gate_frozen():
            raise cd.StageError("the Gate is frozen after data execution began; gate is not a stage of this single-lane run")
        lane = LANES["relevance"]
        contract = gate_builder.stage_contract(cd.load(lane["gate_input"]), lane["scores"], "rank", __file__)
        cd.say(json.dumps({"status": "needs_correction", "reason": "single_lane_requires_rank",
                           "message": "this run has one discovery lane, so there is no reach table",
                           **contract}, ensure_ascii=False))
        return
    if names != ["relevance", "reach"]:
        raise cd.StageError(f"unexpected discovery lanes {names}")
    lane = LANES["relevance"]
    if not gate_preflight([(lane["gate_input"], lane["scores"], lane["gate"])], "gate", gate_frozen()):
        return
    build_gate("relevance")
    # One table per command: two full tables in one output overflow the tool output limit.
    reach = LANES["reach"]
    need(REQUEST, lane_candidates("reach"))
    cd.script("project_relevance_gate_input.py", "--request", REQUEST, "--candidates", lane_candidates("reach"),
              "--output", reach["gate_input"], "--table", show=True)
    cd.say(json.dumps(gate_builder.stage_contract(cd.load(reach["gate_input"]), reach["scores"],
                                                 "rank", __file__), ensure_ascii=False))


# --------------------------------------------------------------------------- rank


def build_ranking_input(lanes: list[str]) -> tuple[dict[str, Any], str]:
    """The ranking input for these Gates, built once, and its `--print` line.

    The builder refuses to overwrite its output, so a later run builds a fresh copy beside it and
    keeps the first unless the Gate changed. The `--print` line is the compact ranking input the
    manual workflow printed for the enrichment call; it is shown only when the model makes that call.
    """
    args = ["--request", REQUEST, "--candidates", lane_candidates("relevance"), "--gate", LANES["relevance"]["gate"]]
    if "reach" in lanes:
        args += ["--reach-candidates", lane_candidates("reach"), "--reach-gate", LANES["reach"]["gate"]]
    if not Path(RANKING_INPUT).exists():
        status, printed = cd.script("build_instagram_ranking_input.py", *args, "--output", RANKING_INPUT,
                                    "--print").rstrip("\n").split("\n", 1)
        cd.say(status)
        return cd.load(RANKING_INPUT), printed
    fresh = Path(".trend-ranking-input.next.json")
    fresh.unlink(missing_ok=True)
    try:
        printed = cd.script("build_instagram_ranking_input.py", *args, "--output", str(fresh),
                            "--print").rstrip("\n").split("\n", 1)[1]
        if fresh.read_bytes() != Path(RANKING_INPUT).read_bytes():
            if Path(ENRICHMENT_RESPONSE).exists():
                raise cd.StageError(f"the Gate changed after the enrichment already ran on {RANKING_INPUT}; the Gate is "
                                    "frozen before detail: restore the score lines it was built from")
            fresh.replace(RANKING_INPUT)
            cd.say(f"[drive] {RANKING_INPUT} rebuilt from the changed Gate")
    finally:
        fresh.unlink(missing_ok=True)
    return cd.load(RANKING_INPUT), printed


def fetch_round_compact(calls: list[dict[str, Any]], args: argparse.Namespace, place) -> bool:
    """`connector_driver.fetch_round`, with a handed-over call printed as compact JSON.

    The call is the same object either way; only the whitespace between its tokens goes, as in the
    compact line the manual workflow printed for the enrichment call.
    """
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            return cd.fetch_round(calls, args, place)
    finally:
        # Split on newlines only: a caption can hold U+2028 and the like, which splitlines() also breaks on.
        for line in buffer.getvalue().rstrip("\n").split("\n"):
            if line.startswith(cd.HANDOVER_PREFIX):
                call = json.loads(line[len(cd.HANDOVER_PREFIX):])
                line = cd.HANDOVER_PREFIX + json.dumps(call, ensure_ascii=False, separators=(",", ":"))
            cd.say(line)


# The two lanes' enrichment by handle — both candidate sets named, the same six-and-six selected —
# instead of the lean inline items. Off until the backend takes several candidate sets;
# with it on, a run whose lanes lack either candidate_set_id still sends the lean inline call.
USE_MULTISET_HANDLE = False


def multiset_arguments(lean: dict[str, Any]) -> dict[str, Any] | None:
    from build_instagram_ranking_input import multiset_handle  # noqa: E402 (this package's own)

    return multiset_handle(lean, cd.load(lane_candidates("relevance")), cd.load(lane_candidates("reach")))


def hand_over_lean(call: dict[str, Any]) -> None:
    """Print the lean inline enrichment arguments (six of each lane) for the model, and stop."""
    # 与 public_calls 交出的调用一致：带 delivery:"download"，脚本凭下载副本拿到完整补全结果。
    call = {**call, "delivery": "download"}
    Path(ENRICH_CALL).write_text(json.dumps(call, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    cd.say(json.dumps(call, ensure_ascii=False, separators=(",", ":")))
    cd.say(f"This host does not fetch the enrichment for scripts. Call `enrich_instagram_shortlist` exactly once "
           f"with the JSON printed above (also in {ENRICH_CALL}) verbatim as the tool arguments — every item and "
           "every top-level field, `window` included; do not add, drop or reorder items — then run this same "
           "command again with --placed added.")
    raise SystemExit(0)


def hand_over_inline(calls: list[dict[str, Any]], args: argparse.Namespace, place, lanes: list[str]) -> None:
    """The inline enrichment, handed to the model the way the manual workflow (0.3.44) did.

    The inline form cannot go through the outbox (about 113 KB, over the host's request limits).
    Printing it as a `call:` line to copy verbatim would change what the model does at this step,
    so on a hand-over this stage prints the lean call — six items of each lane (`lane_pick`), trimmed
    to the fields the backend needs — with the manual workflow's own enrichment instruction, and
    stops; `rank --placed` then persists the result and goes on.

    Two lanes send one enrichment request, whichever way it goes: the lean six-and-six arguments,
    through the outbox and in the hand-over alike. Offering the whole inline ranking to the outbox
    first made the result depend on the host's request limits — a host that took it enriched every
    item, one that answered it `error` ended the run as «data service unreachable» — where the
    tested behaviour is the six-and-six call.
    """
    if "reach" in lanes:
        lean = handed_enrichment(calls[0]["arguments"])
        handle = multiset_arguments(lean) if USE_MULTISET_HANDLE else None
        sent = handle or lean
        cd.fetch_round([{"tool": "enrich_instagram_shortlist", "arguments": sent}], args, place,
                       hand_over_calls=lambda: hand_over_lean(sent))
        return
    buffer = io.StringIO()
    handed = False
    try:
        with contextlib.redirect_stdout(buffer):
            cd.fetch_round(calls, args, place)
    except SystemExit as exit_:
        handed = exit_.code == 0 and cd.HANDOVER_PREFIX in buffer.getvalue()
        if not handed:
            raise
    finally:
        # Split on newlines only: a caption can hold U+2028 and the like, which splitlines() also breaks on.
        for line in buffer.getvalue().rstrip("\n").split("\n"):
            if line and not (handed and (line.startswith(cd.HANDOVER_PREFIX)
                                         or line.startswith("This host does not fetch for scripts"))):
                cd.say(line)
    if handed:
        hand_over_lean(handed_enrichment(calls[0]["arguments"]))


# Items handed to the model per lane for the inline enrichment. The whole inline ranking input is about
# 113 KB — more than a model reply can carry — so the model trimmed it itself, and how much it kept
# decided the report: 12 items (six of each lane) on 2026-09-19 02:33, one item at 05:00, which left
# a one-item report. Six of each lane is what the model kept when the run went well; now it is
# always six, chosen by `lane_pick`.
HANDED_PER_LANE = 6


# What the model has to type is the cost of this step: twelve full items (about 24 KB, cover
# addresses included) took 120 s to write on 2026-09-19, where the call the model trimmed itself in
# 0.3.44 (about 8 KB) took 16 s. The handed items keep that trimmed shape — the backend fills
# covers, dates and plays from its own detail requests — so the model writes what it wrote then.
HANDED_CAPTION_CHARS = 80


def lean_item(item: dict[str, Any]) -> dict[str, Any]:
    kept: dict[str, Any] = {}
    for key in ("content_key", "platform", "content_id", "url", "author", "format", "caption", "published_at",
                "metrics", "sources", "semantic_scores", "lane"):
        if key not in item:
            continue
        value = item[key]
        if key == "author" and isinstance(value, dict):
            value = {k: value[k] for k in ("handle",) if k in value}
        elif key == "caption" and isinstance(value, str):
            value = value[:HANDED_CAPTION_CHARS]
        elif key == "sources" and isinstance(value, list):
            value = value[:1]  # one complete source entry: route, intent, query, rank, limitations
        kept[key] = value
    return kept


# Which six of a lane go to enrichment: two by meaning, four by heat ("三分准、七分热"). The Gate scores
# in steps of five and ties widely — on 2026-09-19 (one benchmark niche, 0.3.49) eleven relevance items tied at
# 95, and the builder broke the tie by search rank, so a 1.04M-play and an 8.1M-play Reel of the
# format sat 11th and 12th while four items under 6K plays went. Within the lane's semantic head
# (every item within HANDED_HEAD_BAND points of its top score) the first HANDED_BY_MEANING stay in
# the builder's order, and the rest of the lane's six go to the head's hottest items by discovery
# heat: public plays, or likes × HANDED_PLAYS_PER_LIKE where discovery returned no plays (the median
# plays/likes of 208 discovery rows that had both, three runs of 2026-09-19). A head shorter than
# six is topped up in the builder's order.
HANDED_BY_MEANING = 2
HANDED_HEAD_BAND = 7.5
HANDED_PLAYS_PER_LIKE = 24


def discovery_heat(item: dict[str, Any]) -> float:
    """Plays as discovery saw them: `play_count`, else likes scaled to plays, else 0."""
    metrics = item.get("metrics") or {}
    for key in ("play_count", "views"):
        value = metrics.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            return float(value)
    likes = metrics.get("like_count")
    if isinstance(likes, (int, float)) and not isinstance(likes, bool) and likes > 0:
        return float(likes) * HANDED_PLAYS_PER_LIKE
    return 0.0


def lane_pick(items: list[dict[str, Any]]) -> set[int]:
    """Positions (in the builder's order) of the lane's items handed to enrichment."""
    from build_instagram_ranking_input import semantic_total  # noqa: E402 (this package's own)

    if not items:
        return set()
    totals = [semantic_total(item.get("semantic_scores") or {}) for item in items]
    head = [n for n, total in enumerate(totals) if total >= max(totals) - HANDED_HEAD_BAND]
    picked = head[:HANDED_BY_MEANING]
    hottest = sorted(head[HANDED_BY_MEANING:], key=lambda n: (-discovery_heat(items[n]), n))
    picked += hottest[: HANDED_PER_LANE - len(picked)]
    picked += [n for n in range(len(items)) if n not in picked][: HANDED_PER_LANE - len(picked)]
    return set(picked)


def handed_enrichment(ranking: dict[str, Any]) -> dict[str, Any]:
    """The inline enrichment arguments: HANDED_PER_LANE items of each lane (`lane_pick`), in the builder's order, lean."""
    by_lane: dict[str, list[int]] = {}
    items = ranking.get("items") or []
    for n, item in enumerate(items):
        by_lane.setdefault(str(item.get("lane") or ""), []).append(n)
    kept_at: set[int] = set()
    for positions in by_lane.values():
        kept_at |= {positions[n] for n in lane_pick([items[p] for p in positions])}
    return {**ranking, "items": [lean_item(item) for n, item in enumerate(items) if n in kept_at]}


# Two lanes are one pool from the Snapshot on. The Snapshot keeps an enriched item only when the Gate
# it is given classifies it, and the Analysis reads its allowlist from that Gate too; given the
# relevance lane's alone, every reach-lane item the backend enriched was dropped there without a
# trace. Measured over four two-lane runs of 2026-09-19 (0.3.44–0.3.49): twelve items enriched, five
# or six in the Snapshot, all from the relevance lane — that niche lost a 15.5× outlier account and
# ohneis652 (6×) that way, and delivered four items, three of them under 1.5×.
UNION_CANDIDATES = "trend-candidate-set-union.json"
UNION_GATE = "trend-relevance-gate-union.json"


def lane_union() -> tuple[str, str]:
    """Write both lanes' candidates and Gates as one of each; the relevance lane wins a shared key.

    The same rule the ranking builder applies: an item both lanes found counts as relevance.
    """
    candidates, reach_candidates = cd.load(lane_candidates("relevance")), cd.load(lane_candidates("reach"))
    gate, reach_gate = cd.load(LANES["relevance"]["gate"]), cd.load(LANES["reach"]["gate"])
    keys = {str(item["content_key"]) for item in candidates.get("items") or []}
    union = {**candidates, "items": [*(candidates.get("items") or []),
                                     *(item for item in reach_candidates.get("items") or []
                                       if str(item["content_key"]) not in keys)]}
    union_gate = {**gate, "items": [*(gate.get("items") or []),
                                    *(row for row in reach_gate.get("items") or []
                                      if str(row["content_key"]) not in keys)]}
    for name, value in ((UNION_CANDIDATES, union), (UNION_GATE, union_gate)):
        Path(name).write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return UNION_CANDIDATES, UNION_GATE


def stage_rank(args: argparse.Namespace) -> None:
    lanes = lanes_run()
    if "reach" in lanes and not Path(LANES["reach"]["scores"]).is_file():
        raise cd.StageError(f"this run has two discovery lanes: score the reach table into {LANES['reach']['scores']} "
                            f"first (`drive.py gate` prints it after {GATE_SCORES})")
    need(REQUEST, lane_candidates("relevance"))
    if not gate_preflight([(LANES[name]["gate_input"], LANES[name]["scores"], LANES[name]["gate"])
                           for name in lanes], "rank", gate_frozen()):
        return
    for name in lanes:
        build_gate(name)
    ranking, printed = build_ranking_input(lanes)
    selected = ranking.get("selected", ranking.get("items")) or []
    pool, pool_gate = lane_union() if "reach" in lanes else (lane_candidates("relevance"), LANES["relevance"]["gate"])
    base = ["--request", REQUEST, "--candidates", pool, "--gate", pool_gate]
    if not selected:
        # No item reached the floor: never call enrichment with an empty array.
        cd.say("[drive] no candidate reached the Gate floor; building the empty Snapshot without enrichment")
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
        calls = [{"tool": "enrich_instagram_shortlist", "arguments": ranking}]
        if "items" in ranking:
            hand_over_inline(calls, args, place, lanes)
        else:
            fetch_round_compact(calls, args, place)
        need(ENRICHMENT_RESPONSE)
        cd.script("build_trend_workflow_snapshot.py", *base, "--enrichment", ENRICHMENT_RESPONSE,
                  "--merged-enrichment-output", ENRICHMENT, "--output", SNAPSHOT)
    # 带画像时在服务端 enrich 定下的条目上按主打内容分名额；双车道时分配的是两条车道的并集。
    # 不带画像时命令与参数和原来一样。
    profiled = intake.attached(cd.load(REQUEST))
    if profiled:
        cd.say(cd.script("allocate_signature_content.py", "--platform", PLATFORM, "--request", REQUEST,
                         "--gate", pool_gate, "--candidates", pool, "--enrichment", ENRICHMENT,
                         "--output", ALLOCATION).rstrip())
    cd.script("analyze_public_snapshot.py", SNAPSHOT, "--mode", "trend", "--request", REQUEST,
              "--relevance-gate", pool_gate, "--trend-enrichment", ENRICHMENT,
              *(("--allocation", ALLOCATION) if profiled else ()), "--output", ANALYSIS)
    cd.say(Path(ANALYSIS).read_text(encoding="utf-8").rstrip())
    cd.script("prepare_hook_context.py", "--platform", "instagram", "--purpose", "trends",
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
    "gate": stage_gate,
    "rank": stage_rank,
    "report": stage_report,
}


def gate_preflight(specs: list[tuple[str, str, str]], stage: str, frozen: bool) -> bool:
    try:
        errors = gate_builder.preflight_scores(
            specs, frozen=frozen,
            frozen_message="the Gate changed after its data work was bound; the Gate is frozen before detail: restore its original score lines")
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
    start.add_argument("--post", help="the source Reel's URL, when the ask supplies one")
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
    for name in ("gate", "rank", "report"):
        sub.add_parser(name)
    sub.choices["report"].add_argument("--language", help="the Creator's chat language (BCP 47, such as zh-CN)")
    for command in sub.choices.values():
        command.add_argument("--placed", action="store_true",
                             help="the connector calls were already made by hand; place their responses")
    return cd.run_stage(STAGES, parser.parse_args(), first=("start",),
                        model_files=(REQUEST, PLAN, LANES["relevance"]["scores"], LANES["reach"]["scores"], SYNTHESIS))


if __name__ == "__main__":
    raise SystemExit(main())
