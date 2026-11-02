#!/usr/bin/env python3
"""Run everything between two judgments of a competitor run, so the model only judges.

A competitor run needs the model for five decisions: which public identity is the subject, how the
Request and discovery intents are frozen, the discovery Gate scores, the eligibility relationships,
and the synthesis. Everything between them is mechanical — which connector call follows, with which
parameters, where each response is placed, which projection is printed next. Measured 2026-09-18 on
an Instagram run: 22 model requests, of which 15 did nothing but that plumbing, at 6 s of fixed cost
each plus whatever the model typed; and every mistake in that plumbing (a positional placement one
slot off, a wrong `--at` form, a repeated `--operation`) cost a further round trip to repair.

Each stage below starts right after one judgment and stops right before the next one: it makes the
connector calls the judgment implies, places every response with the same placement scripts the
manual workflow uses — so the files on disk are the ones those scripts always wrote — runs the
deterministic steps, and prints exactly what the next judgment reads.

    start     --platform P [--query N]   identity search            -> subject candidates
    start     --platform instagram --post URL   seed post + its author's subject stage
    subject   --handle H | --channel-id | --platform youtube --handle @H   subject profile + history  -> caption phrases, schemas
              (an Instagram handle that does not resolve comes back with account-search candidates: a stop to ask the user)
    discover                             collect + profile search   -> Gate author table
    select                               Gate -> roster -> profiles -> eligibility projection
    content [--comments]                 finalize -> pages -> Assemble/Analyze -> synthesis input
                                         (--comments: comment targets -> list_comments -> audience comments)
    comments                             the same comment block alone, after content (kept as an alias)
    report                               Compose -> Validate -> Render

A stage makes no data calls itself: it prints the calls as public MCP tool names (`call:` lines)
for the model to make and stops; the model stages each complete result with `static_host.py
stage`, and the same stage run again with `--placed` picks those responses up.

The hand-over, the retry of a briefly failed round, the run directory, the stage lock and the state
file are `connector_driver.py`, the same file every other driver package ships.

Two options save host round trips without changing what is fetched or judged. `--workspace W` on a
first stage (`start`, `subject`) reserves the run directory itself when none is reserved yet, so the
run needs no separate `static_host.py init` command. `--result <tool>=<file>` on any stage fetches
(or, with no ready download, stages) each saved result of the last hand-over before the stage
resumes as with `--placed`, so a hand-over costs the calls and one command.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import connector_driver as cd  # noqa: E402
from connector_driver import (  # noqa: E402
    StageError,
    batch_calls,
    drained,
    epoch,
    fetch_round,
    load,
    run_call,
    save_state,
    say,
    script as run_script,
    show_schema,
    state,
    usable,
)
from provided_accounts import discovery_mode, requested_accounts, resolved_channel, pin_requested_accounts
from extract_account_caption_phrases import content_texts  # noqa: E402
from validate_discovery_intents import CATEGORY_LANES, COLLECTOR_LIMITS, _term_count_ok as term_count_ok  # noqa: E402
from persist_envelope import (  # noqa: E402
    USABLE_STATUSES,
    place as place_at,
    read_json_or_empty,
    row_count,
    run_dir_of,
    run_lock,
    unwrap_envelope,
    unwrap_typed,
    write_json_atomic,
)
from persist_round import PREFIX, PROFILE_OPERATIONS, owner_of, report as round_report, target_for  # noqa: E402

SCRIPTS = cd.SCRIPTS
POPULAR_TOPIC_MAX_CHARS = COLLECTOR_LIMITS["popular_topic"][1]
REFERENCES = cd.REFERENCES
# Kept under this module's name for callers that catch a hand-over or read its prefix from here.
HANDOVER_PREFIX = cd.HANDOVER_PREFIX
NoOutbox = cd.NoOutbox

IG = "instagram-discovery"
YT = "youtube-public"
REQUEST = "competitor-research-request.json"
INTENTS = "competitor-discovery-intents.json"
PLAN = "competitor-profile-discovery-plan.json"
CANDIDATES = "trend-candidate-set.json"
GATE_INPUT = "competitor-discovery-gate-input.json"
GATE_SCORES = "competitor-discovery-gate.tsv"
GATE = "competitor-discovery-gate.json"
DISCOVERY = "competitor-discovery-selection.json"
ELIGIBILITY_INPUT = "competitor-eligibility-input.json"
ELIGIBILITY = "competitor-eligibility.json"
SELECTION = "competitor-account-selection.json"
# The subject's own recent captions printed before the Request and intents are written.
SUBJECT_CAPTIONS = 12
SUBJECT_CAPTION_CHARS = 200
COMMENT_TARGETS = "comment-targets.json"
SEED = "seed-envelope.json"
# lookup_instagram_post 给种子帖 data 的固定投影，与作答路线无关。
POST_PROJECTION = "kabo.instagram_post.v1"
COMMENT_SOURCE = "comment-source.json"
AUDIENCE = "audience-comments.json"

# Instagram Reels budget per included account: retrieve up to 24 Reels per included account (two
# provider pages of twelve), and a third page only while the window is still uncovered with fewer than
# six in-window Reels — an absolute maximum of 36. Requests on Instagram are Reels (`formats: ["reel"]`
# on every Request recorded through 2026-09), so this workflow does not call `list_posts`.
REEL_PAGES = 2
REEL_PAGES_MAX = 3
MIN_WINDOW_REELS = 6


def response_record(value: Any, source: Path, schema: str | None = None) -> dict[str, Any]:
    """Decode a host transport wrapper without changing the response it carries."""
    if not isinstance(value, dict):
        raise StageError(f"{source}: expected a JSON response object")
    record = unwrap_typed(value, schema) if schema else unwrap_envelope(value)
    if record is not None:
        return record
    if any(key in value for key in ("structuredContent", "content", "envelope")):
        expected = schema or "connector Envelope"
        raise StageError(f"{source}: host wrapper contains no complete {expected}; do not treat it as empty data")
    return value


def normalize_response_file(path: str | Path, schema: str | None = None) -> None:
    """Only the consumer copy is normalized; the original host record stays untouched."""
    target = Path(path)
    value = load(str(target))
    record = response_record(value, target, schema)
    if record != value:
        write_json_atomic(target, record)


def script(name: str, *args: str, show: bool = False) -> str:
    output = run_script(name, *args, show=show)
    if name in ("persist_envelope.py", "persist_many.py"):
        schema = args[args.index("--schema") + 1] if "--schema" in args else None
        for index, arg in enumerate(args):
            if arg == "--dest":
                normalize_response_file(args[index + 1], schema)
    return output


def place_record(operation: str, dest: str, *args: Any, **kwargs: Any) -> None:
    cd.place_record(operation, dest, *args, **kwargs)
    # By-content placement is normalized by script(); record-based placement copies
    # its own host record directly and needs the same consumer representation.
    if any(request.get("operation") == operation and path is not None
           for request, path in (cd.LAST_RECORDS or [])):
        normalize_response_file(dest)


def platform_of(request: dict[str, Any]) -> str:
    platforms = request.get("platforms") or [request.get("platform")]
    platform = str(platforms[0] if platforms else "").lower()
    if platform not in ("instagram", "youtube"):
        raise StageError(f"{REQUEST} names no Instagram or YouTube platform")
    return platform


def window_of(request: dict[str, Any]) -> tuple[str, str]:
    window = request.get("window") or {}
    since, until = window.get("since"), window.get("until")
    if not since or not until:
        raise StageError(f"{REQUEST} has no window.since / window.until")
    return str(since), str(until)


def collect_window(since: str, until: str) -> str:
    days = (epoch(until) - epoch(since)) / 86400
    return "last-week" if days <= 8 else "last-month" if days <= 31.5 else "last-year"


def subject_handles(request: dict[str, Any]) -> list[str]:
    subject = request.get("subject") or {}
    handles = [subject.get("account_ref"), *(state().get("subject_handles") or [])]
    return list(dict.fromkeys(str(h) for h in handles if h))


# --------------------------------------------------------------------------- stages


def seed_author(envelope: dict[str, Any]) -> tuple[str | None, str | None]:
    """The seed post's author handle and shortcode, as `get_post` returns them."""
    post = (envelope.get("data") or {}).get("post") or {}
    owner = post.get("owner") or {}
    handle = owner.get("username") if isinstance(owner, dict) else None
    return (str(handle).lstrip("@") if handle else None), (str(post["shortcode"]) if post.get("shortcode") else None)


def seed_args() -> list[str]:
    """`--seed-envelope` for the Gate projection, when the run started from a post."""
    return ["--seed-envelope", SEED] if Path(SEED).is_file() else []


# The seed post's caption as printed before the Request is written.
SEED_CAPTION_CHARS = 300


def seed_facts(envelope: dict[str, Any]) -> dict[str, Any]:
    """What the seed post shows about its content form: format, length, reach and its own words."""
    data = envelope.get("data") or {}
    post = data.get("post") or {}
    kind = str(post.get("product_type") or "")
    video = post.get("is_video")
    form = ("reel" if kind == "clips" else "carousel" if kind.startswith("carousel") else
            "video post" if video else "image post" if video is False else (kind or None))
    if data.get("projection") == POST_PROJECTION:
        # lookup_instagram_post 的固定投影：null 表示拿不到，直接省略，不当 0；IG+FB 合并播放数
        # 不是 Instagram 播放数，单独列名，不顶替 plays。
        metrics = post.get("metrics") or {}
        caption = post.get("caption") if isinstance(post.get("caption"), str) else ""
        duration = post.get("duration_s")
        plays, combined, comments = (metrics.get("plays_instagram"), metrics.get("plays_combined"),
                                     metrics.get("comments"))
    else:
        edges = ((post.get("edge_media_to_caption") or {}).get("edges")) or []
        caption = "\n".join(str(((e or {}).get("node") or {}).get("text") or "") for e in edges if isinstance(e, dict))
        duration = post.get("video_duration")
        plays = post.get("video_play_count") if post.get("video_play_count") is not None else post.get("play_count")
        combined, comments = None, post.get("comment_count")
    facts = {
        "format": form,
        "duration_seconds": round(float(duration), 1) if duration else None,
        "plays": plays,
        "plays_instagram_and_facebook": combined,
        "comments": comments,
        "caption": " ".join(caption.split())[:SEED_CAPTION_CHARS] or None,
    }
    return {key: value for key, value in facts.items() if value not in (None, "")}


def start_from_post(args: argparse.Namespace) -> None:
    """A run that starts from one public post: fetch it, then run `subject` for its author.

    Done by hand this cost a catalog read, a placement tried before the call went out, and a second
    `get_post` — about 60 s on one case (2026-09-18) before the subject stage even started.
    """
    save_state(platform="instagram")
    # 交接成 lookup_instagram_post；它的信封记实际作答的路线（主源拒绝时是 instagram-detail-proxy），
    # 所以只按 operation 放置，两条路线的回答都能落位。本 Skill 没有别的 get_post 操作。
    fetch_round([run_call(IG, "get_post", {"url": args.post})], args, lambda: place_record(
        "get_post", SEED, lambda: script(
            "persist_envelope.py", "--operation", "get_post", "--any-status", "--dest", SEED, *drained())))
    envelope = load(SEED)
    handle, shortcode = seed_author(envelope)
    if envelope.get("status") not in USABLE_STATUSES or not handle:
        raise StageError(f"the seed post did not resolve (status={envelope.get('status')}); tell the user the post "
                         "could not be read and ask for the account instead")
    say(f"seed post: {shortcode} by @{handle} — the subject is its author; set subject.seed_content_ref from "
        "this post's content_id")
    # The video itself, so the Request can describe its form (one video-started case, 2026-09-19: with only the
    # author in view, subject.description became the author's topics and the Gate compared topics).
    say("seed post (the video the user gave): " + json.dumps(seed_facts(envelope), ensure_ascii=False))
    say("The user asks for accounts that make this kind of video. Write subject.description about this video's "
        "content form — what the video does and how it is built — not only the author's general topics.")
    # `fetch_round` clears `placed` once it places the get_post round, so this is True only on the run
    # that answers the subject's own hand-over. Hard-coding False left that run looking in staging,
    # which a runner had already drained into snapshot/, and the subject was handed over again
    # (plugins, 2026-10-26: the run closed with no discovery).
    stage_subject(argparse.Namespace(handle=handle, channel_id=None, seed=SEED, placed=args.placed))


def stage_start(args: argparse.Namespace) -> None:
    cd.reserve_run_directory(args.placed, args=args)
    if getattr(args, "post", None):
        start_from_post(args)
        return
    previous = state()
    if "subject_query" in previous and previous["subject_query"] != args.query:
        raise StageError("the original subject query cannot change within this run, including --placed")
    save_state(subject_query=args.query)
    if not args.query:
        # No named subject, only a niche: there is no identity to search. Searching the niche itself
        # made one channel of it «the user» — one creator case, 2026-09-18: a niche-only description in the
        # chat language («I make <niche> videos on YouTube») resolved to one same-niche channel in that
        # language, whose market turned discovery to that language and off every labelled account.
        save_state(platform=args.platform)
        say("no subject name: skip the identity search and the subject stage; freeze the Request from the "
            "user's own description, with no account_ref.")
        show_schema("competitor-research-request.schema.json", "competitor-discovery-intents.schema.json")
        return
    # Identity search is named, not chosen: `instagram-discovery/search_accounts` returned the same
    # exact-name match as `search_profiles` on 8 of 8 product names in 1.9 s instead of 12.8 s p50.
    if args.platform == "instagram":
        # The account search rejects a leading "@" as invalid_format; the user's own spelling stays in
        # subject_query, only the searched name drops it.
        connector, operation, params = IG, "search_accounts", {"query": args.query.strip().lstrip("@").strip() or args.query}
    else:
        connector, operation, params = YT, "search_channels", {"query": args.query, "limit": 10, "order": "relevance"}
    save_state(platform=args.platform)
    fetch_round([run_call(connector, operation, params)], args, lambda: place_record(
        operation, "subject-search.json", any_status=False, by_content=lambda: script(
            "persist_many.py", "--connector", connector, "--operation", operation, "--dest", "subject-search.json",
            *drained())))
    script("project_subject_candidates.py", "subject-search.json", show=True)


# --------------------------------------------------------------------------- account histories
#
# Profiles and history pages come from one backend call, `collect_account_history` (KS9): the
# subject's profile and first page, the selected accounts' profiles and first pages with the frozen
# reserves backfilled, and the included accounts' Reels pages under the window rule. The backend
# makes the provider calls, retries an empty first history page once, and returns every call's
# envelope (`calls[]`); `static_host.py fetch` stores each as its own evidence and writes a record
# that names, per call, the account group (`acct-<i>`, `reserve-<i>`) and the evidence file.

HISTORY_TOOL = "collect_account_history"
TOOL_OPERATIONS = {
    "instagram_resolve_profile": "resolve_profile",
    "instagram_list_reels": "list_reels",
    "youtube_get_channel": "get_channel",
    "youtube_list_channel_uploads": "list_channel_uploads",
}
# A YouTube history page: the most one call returns (as for the subject). With 20, an active
# channel's own history ended inside the window and its cadence was only a lower bound (plugins,
# 2026-10-27). Still one page; a longer history stays a lower bound.
YOUTUBE_PAGE = 50


def account_ref(platform: str, account: str) -> dict[str, str]:
    """One account as the composite takes it: an Instagram handle, a YouTube channel id or @handle."""
    if platform == "instagram":
        return {"handle": account.strip().lstrip("@")}
    if re.fullmatch(r"UC[\w-]{22}", account):
        return {"channel_id": account}
    return {"handle": account.strip().lstrip("@")}


def history_call(platform: str, accounts: list[str], *, profile: bool = True, max_pages: int = 1,
                 window: tuple[str, str] | None = None, reserves: list[str] | None = None,
                 identity_candidates: bool = False) -> dict[str, Any]:
    """The `collect_account_history` call for these accounts.

    Instagram reads Reels only (`list_posts` is never called: Requests on Instagram are Reels).
    `max_pages` counts every page of one account. With a window, paging follows step 4's Reels
    budget exactly — the second page while the window is not yet covered or fewer than
    MIN_WINDOW_REELS in-window Reels came back, a third only while both still hold
    (`next_reel_page`). With reserves, each account whose profile came back unavailable is
    replaced once, in order, up to as many usable accounts as were asked for. With
    `identity_candidates`, an account that does not resolve comes back as `needs_identity` with the
    account search's candidates for that name, in the same call.
    """
    history: dict[str, Any] = {"max_pages": max_pages}
    if platform == "instagram":
        history["instagram_formats"] = {"posts": False, "reels": True}
    else:
        history["page_size"] = YOUTUBE_PAGE
    if window:
        history.update({"since": window[0], "until": window[1], "min_items_since": MIN_WINDOW_REELS})
    arguments: dict[str, Any] = {"platform": platform, "accounts": [account_ref(platform, a) for a in accounts],
                                 "profile": profile, "history": history}
    if reserves:
        arguments["reserves"] = [account_ref(platform, r) for r in reserves]
        arguments["target_count"] = len(accounts)
    if identity_candidates:
        arguments["on_identity_miss"] = "candidates"
    return {"tool": HISTORY_TOOL, "arguments": arguments}


def history_record(call: dict[str, Any]) -> dict[str, Any]:
    """The staged `collect_account_history` record answering this call, newest first, never one used before."""
    from static_host import composite_records

    wanted = call["arguments"]["accounts"]
    used = set(state().get("history_records") or [])
    for record in reversed(composite_records(HISTORY_TOOL)):
        if record["id"] in used:
            continue
        accounts = ((record.get("summary") or {}).get("accounts")) or []
        asked = [a.get("input") for a in accounts if isinstance(a, dict) and str(a.get("group", "")).startswith("acct-")]
        if asked and asked != wanted:
            continue
        save_state(history_records=[*used, record["id"]])
        return record
    raise StageError(f"no staged {HISTORY_TOOL} result for these accounts in this run. Make the handed-over call, "
                     f"save its result and run the printed fetch command, then run this stage again with --placed.")


def copy_evidence(source: str | Path, target: Path) -> None:
    target.write_bytes(Path(source).read_bytes())
    target.chmod(0o600)
    normalize_response_file(target)


def place_history(call: dict[str, Any], targets, record: dict[str, Any] | None = None) -> dict[str, list[Path]]:
    """Copy each call of the composite to the file `targets(group, operation, page)` names (None: not kept).

    Returns the files written per group. Page numbers count each account's history pages from 1.
    """
    record = record or history_record(call)
    labels = group_accounts(call)
    written: dict[str, list[Path]] = {}
    pages: dict[str, int] = {}
    report: list[dict[str, Any]] = []
    for item in record.get("calls") or []:
        operation = TOOL_OPERATIONS.get(str(item.get("tool")))
        group = str(item.get("group") or "")
        if operation is None:
            continue  # an identity search after a miss: the summary carries its candidates
        page = 0
        if operation not in PROFILE_OPERATIONS:
            page = pages[group] = pages.get(group, 0) + 1
        target = targets(group, operation, page)
        if target is None:
            continue
        if target.is_file() and usable(target) and item.get("status") not in USABLE_STATUSES:
            # A page read again that failed never replaces the usable page already placed.
            say(f"[drive] kept {target.name}: the new response failed ({item.get('status')})")
            continue
        copy_evidence(item["path"], target)
        written.setdefault(group, []).append(target)
        envelope = load(target)
        report.append({"path": target.name, "operation": operation, "account": labels.get(group, group),
                       "status": envelope.get("status"), "rows": row_count(envelope), "matched_by": "its request"})
    say(round_report({"placed": report, "skipped": []}))
    for account in ((record.get("summary") or {}).get("accounts")) or []:
        if isinstance(account, dict) and account.get("status") not in ("completed", None):
            note = f" (replaced by {account['replaced_by']})" if account.get("replaced_by") else ""
            say(f"[drive] {account.get('group')}: {account.get('status')}{note}")
    for limitation in record.get("limitations") or []:
        say(f"[drive] limitation: {limitation}")
    return written


def group_accounts(call: dict[str, Any]) -> dict[str, str]:
    """group -> the account label (handle or channel id) this call asked for."""
    arguments = call["arguments"]
    label = lambda ref: str(ref.get("channel_id") or ref.get("handle") or "")  # noqa: E731
    found = {f"acct-{i}": label(ref) for i, ref in enumerate(arguments["accounts"])}
    found.update({f"reserve-{i}": label(ref) for i, ref in enumerate(arguments.get("reserves") or [])})
    return found


def fetch_histories(call: dict[str, Any], args: argparse.Namespace, place) -> bool:
    """One `collect_account_history` hand-over, placed by `place(call)`; False when it had been done."""
    return fetch_round([call], args, lambda: place(call))


def account_files(call: dict[str, Any]):
    """Targets for the accounts of a discovery roster: profile-<account>.json, <prefix>-<account>-p<n>.json."""
    run_dir = run_dir_of(Path.cwd() / ".persist-round.json")
    if run_dir is None:
        raise StageError("run drive.py from inside the run directory")
    labels = group_accounts(call)

    def targets(group: str, operation: str, page: int) -> Path | None:
        label = labels.get(group)
        if not label:
            return None
        if operation in PROFILE_OPERATIONS:
            return target_for(run_dir, operation, label)
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", label)
        return run_dir / f"{PREFIX[operation]}-{safe}-p{page}.json"
    return targets


def verify_explicit_youtube_handle() -> None:
    """A bio mention is not proof that a selected channel owns the requested public handle."""
    query = state().get("subject_handle") or state().get("subject_query")
    if not isinstance(query, str) or not query.strip().startswith("@"):
        return
    requested = query.strip().casefold()
    profile = load("subject-profile.json")
    rows = (profile.get("data") or {}).get("rows") or []
    # get_channel returns the canonical public handle as custom_url. A title, description,
    # outbound link, or channel-ID URL cannot establish ownership of an @handle.
    handles = [row.get("custom_url") for row in rows if isinstance(row, dict)]
    if profile.get("status") not in USABLE_STATUSES or len(handles) != 1 or not isinstance(handles[0], str) or not handles[0].strip().startswith("@"):
        raise StageError("YouTube subject identity unresolved: the profile does not prove the requested public handle")
    if handles[0].strip().casefold() != requested:
        raise StageError("YouTube subject identity mismatch: the profile's public handle differs from the original requested handle")


def stage_subject(args: argparse.Namespace) -> None:
    # Continues the run `start` began when run from it; reserves one when the identity was known.
    cd.reserve_run_directory(args.placed, stage="subject", reuse=True, args=args)
    named = getattr(args, "platform", None)
    platform = state().get("platform") or named or ("youtube" if args.channel_id else "instagram")
    if named and named != platform:
        raise StageError(f"this run is {platform}; --platform {named} needs its own run (`start --platform {named}`)")
    if platform == "instagram" and not state().get("platform") and not named and (args.handle or "").strip().startswith("@"):
        # A bare --handle used to mean Instagram silently: on one product case, 2026-10-23, a YouTube
        # channel's @handle went to instagram-discovery and came back as a 0-follower account.
        say("note: treating --handle as Instagram; for a YouTube @handle run `subject --platform youtube --handle <@handle>`")
    if platform == "instagram":
        if not args.handle:
            raise StageError("Instagram needs --handle")
        # Instagram subject history is the Reels feed only — never `instagram-baseline-proxy`: on
        # 2026-09-14 the proxy timed out and the caption extractor saw zero captions. An empty first
        # page from an account whose profile lists posts is asked once more inside the call (one
        # video-started case, 2026-09-19: an empty page, while the same call returned twelve rows 14
        # minutes before and 11 after).
        account = args.handle.lstrip("@")
        save_state(subject_handles=[account])
    else:
        if not args.channel_id and not (args.handle or "").strip():
            raise StageError("YouTube needs --channel-id (the UC… id the candidates printed) or --handle <@handle>")
        # One identity per call: the canonical channel id when known, else the @handle, which the
        # backend resolves to its channel before reading the uploads by channel id.
        account = args.channel_id or args.handle.strip().lstrip("@")
        if not args.channel_id:
            # The handle is the identity asked for, whatever `start` searched: the profile must prove it.
            save_state(subject_handle="@" + account)
        save_state(platform="youtube")
    # On Instagram the same call answers a handle that does not resolve with the account search's
    # candidates for it, so a run that starts from a named handle needs no separate identity-search
    # round first (plugins and Web, 2026-11-02: that round cost three model steps on every run).
    call = history_call(platform, [account], identity_candidates=platform == "instagram")

    def place_subject(call: dict[str, Any]) -> None:
        def targets(group: str, operation: str, page: int) -> Path | None:
            if group != "acct-0" or page > 1:
                return None
            return Path("subject-profile.json" if operation in PROFILE_OPERATIONS else "subject-history.json")
        record = history_record(call)
        place_history(call, targets, record)
        accounts = ((record.get("summary") or {}).get("accounts")) or []
        subject = next((a for a in accounts if isinstance(a, dict) and a.get("group") == "acct-0"), {})
        search = next((item for item in record.get("calls") or []
                       if item.get("tool") == "instagram_search_accounts" and item.get("group") == "acct-0"), None)
        if search is not None:
            copy_evidence(search["path"], Path("subject-search.json"))
        save_state(subject_identity={"status": subject.get("status"), "searched": search is not None,
                                     "candidates": subject.get("candidates") or []})

    fetch_histories(call, args, place_subject)
    if (state().get("subject_identity") or {}).get("status") == "needs_identity":
        ask_for_identity(account)
        return
    if not Path("subject-profile.json").is_file() or not usable(Path("subject-profile.json")):
        status = load("subject-profile.json").get("status") if Path("subject-profile.json").is_file() else "not fetched"
        if platform == "youtube" and not args.channel_id:
            raise StageError(f"YouTube handle @{account} resolved to no single channel; ask the user for the channel link")
        raise StageError(f"the subject profile did not resolve ({status}); ask the user for the exact public handle")
    if platform == "youtube":
        rows = (load("subject-profile.json").get("data") or {}).get("rows") or []
        channel = str((rows[0] or {}).get("id") or account) if rows and isinstance(rows[0], dict) else account
        save_state(subject_handles=[channel, *([args.handle] if args.handle else [])])
        verify_explicit_youtube_handle()
    if not Path("subject-history.json").is_file():
        raise StageError("the subject's history page did not come back in the account-history result; "
                         "no complete host record of it")
    history = load("subject-history.json")
    if history.get("status") in USABLE_STATUSES and not ((history.get("data") or {}).get("rows")):
        say("note: the subject's first history page came back empty; the seed post and profile are "
            "the only wording from the subject in this run")
    sources = [*([args.seed] if args.seed else []), "subject-history.json"]
    save_state(subject_history=sources)
    script("project_subject_candidates.py", "subject-profile.json", show=True)
    phrases = script("extract_account_caption_phrases.py", *sources, "--output", "competitor-caption-phrases.json")
    say(f"caption phrases: {phrases.strip()}")
    history = load("subject-history.json")
    rows = (history.get("data") or {}).get("rows") or []
    say(f"subject history: {history.get('status')}, {len(rows)} rows")
    # The subject's own recent wording. The discovery intents are grounded in how the subject talks
    # about itself, and before this driver the model read that from the raw history it placed. With
    # only the one-line bio left in view, the workflow lane drifted off the subject's own vocabulary:
    # measured 2026-09-18 on one product case, twelve captions named the product's function noun twelve
    # times and a broad role word once, yet from the bio alone half the runs led with that role word, whose
    # profile-search variant retrieved AI-agent accounts with no Reels in the window and an empty Report.
    data = history.get("data") or {}
    texts = content_texts(rows) or [str(item.get("title")) for item in [*rows, *(data.get("items") or [])]
                                    if isinstance(item, dict) and item.get("title")]
    if texts:
        # Scoped to that one use. Read as general context, a creator's lifestyle words became a lane:
        # On one creator case (2026-09-18/19), twelve captions tagged #minimalism turned `professional organizer`
        # into `minimalist organizing`, whose profile search picked six minimalist bloggers with almost
        # no Reels in the window — two Reports with zero patterns, where the four runs without the
        # captions had delivered three each.
        if args.seed:
            # A run from one video: that video's form is what is compared; these say who made it.
            say("subject's recent captions (who the author is; the seed video above is the content form to "
                "compare, so describe that form in subject.description; never copy style or lifestyle words, such "
                "as an aesthetic or a philosophy, into a search_query):")
        else:
            say("subject's recent captions (for a product or service subject, only to find the noun it uses for "
                "what it sells; for a creator subject, to see the content form it keeps making; never copy style "
                "or lifestyle words, such as an aesthetic or a philosophy, into a search_query):")
        for text in texts[:SUBJECT_CAPTIONS]:
            say("- " + " ".join(str(text).split())[:SUBJECT_CAPTION_CHARS])
    show_schema("competitor-research-request.schema.json", "competitor-discovery-intents.schema.json")


def ask_for_identity(handle: str) -> None:
    """The named handle did not resolve: print the accounts its search returned and stop for the user.

    The subject is the user's to confirm here, not the model's to pick: the handle the user gave
    names no public account, so any candidate is a guess at what they meant.
    """
    identity = state().get("subject_identity") or {}
    say(f"@{handle} did not resolve to a public Instagram profile.")
    if Path("subject-search.json").is_file():
        say("An account search for that name returned:")
        script("project_subject_candidates.py", "subject-search.json", show=True)
    elif identity.get("candidates"):
        say("An account search for that name returned:")
        for number, candidate in enumerate(identity["candidates"], start=1):
            name = f" | {candidate['full_name']}" if candidate.get("full_name") else ""
            verified = " verified" if candidate.get("is_verified") else ""
            say(f"{number}. [@{candidate.get('handle')}{name}]{verified}")
    else:
        say("No candidate account came back for that name.")
    say("Stop here and ask the user which public account they mean — one of these, or the exact handle — "
        "without choosing one yourself and without starting discovery. Their answer is a new request: "
        "start a new run for it.")


def complete_request() -> dict[str, Any]:
    """Fill the Request's mechanical fields the model left out (never one it wrote): schema_version,
    request_id, and the window when it is the default last 30 days (a span the user named is written)."""
    fields: dict[str, Any] = {"schema_version": "competitor-research-request.v3", "request_id": cd.run_id()}
    written = load(REQUEST) if Path(REQUEST).is_file() else {}
    if isinstance(written, dict) and "window" not in written and written.get("window_policy") in (None, "recent_30d"):
        fields["window"] = cd.last_days(30)
    return cd.complete_artifact(REQUEST, fields)


def complete_popular_topics() -> None:
    """Reuse a category lane's search_query as its popular_topic when the model left it out.

    Only category lanes: a workflow phrase is not an Instagram topic, so it gets none. Only a
    search_query of up to four terms and 80 characters: a longer copy fails the validator's topic
    limits and costs a needs_correction round for a field the model never wrote.
    """
    if not Path(INTENTS).is_file():
        return
    plan = load(INTENTS)
    if not isinstance(plan, dict) or plan.get("platform") != "instagram":
        return
    intents = plan.get("intents")
    if not isinstance(intents, list):
        return
    changed = False
    for intent in intents:
        if not isinstance(intent, dict) or intent.get("intent_id") not in CATEGORY_LANES:
            continue
        query = intent.get("search_query")
        if ("popular_topic" not in intent and isinstance(query, str) and term_count_ok(query, 2, 4)
                and len(query.strip()) <= POPULAR_TOPIC_MAX_CHARS):
            intent["popular_topic"] = query
            changed = True
    if changed:
        write_json_atomic(Path(INTENTS), plan)


def stage_discover(args: argparse.Namespace) -> None:
    request = complete_request()
    if discovery_mode(request) != (request.get("discovery_mode") or "auto"):
        # Accounts the user named are never dropped: auto with references runs as hybrid.
        request["discovery_mode"] = discovery_mode(request)
        write_json_atomic(Path(REQUEST), request)
        say("competitor_refs were given under auto; running as hybrid so they are kept")
    requested_accounts(request)
    if request.get("discovery_mode") == "provided":
        script("validate_request_window.py", REQUEST, show=True)
        stage_select(args)
        return
    platforms = request.get("platforms") or [request.get("platform")]
    if not Path(INTENTS).is_file():
        # The plan was never written (the Request alone was): nothing was retrieved yet, so the
        # run stays open for it instead of closing on a missing file (plugins, 2026-10-28).
        say(json.dumps({"status": "needs_correction", "artifact": INTENTS,
                        "issues": [f"{INTENTS} is missing; write it next to {REQUEST}"]}, ensure_ascii=False))
        say("Discovery retrieval did not start. Write the discovery intents in this same run directory, "
            "then rerun drive.py discover there; this is not a completed discovery stage.")
        return
    # The four intent kinds are the model's judgment; the file's own schema, platform and a
    # popular_topic that only repeats the search_query are not.
    cd.complete_artifact(INTENTS, {"schema_version": "competitor-discovery-intents.v1",
                                   "platform": str(platforms[0] if platforms else "").lower() or None})
    complete_popular_topics()
    script("validate_request_window.py", REQUEST, show=True)
    history = state().get("subject_history") or []
    if history:
        script("extract_account_caption_phrases.py", *history, "--output", "competitor-caption-phrases.json",
               "--into", REQUEST)
    try:
        script("validate_discovery_intents.py", REQUEST, INTENTS, show=True)
    except StageError as error:
        # A rejected authored plan has not reached discovery retrieval. Keep a non-final wrapper
        # open so the model can correct the validator's named field in this same run. Script or
        # infrastructure failures still propagate; no constraint or query is changed here.
        prefix = "validate_discovery_intents.py failed:\ninvalid competitor discovery intents: "
        if not str(error).startswith(prefix):
            raise
        say(json.dumps({"status": "needs_correction", "artifact": INTENTS,
                        "issues": [str(error)[len(prefix):]]}, ensure_ascii=False))
        say("Discovery retrieval did not start. Correct the named input field without changing the "
            "subject, market or intended meaning; do not truncate the query automatically. Then "
            "rerun drive.py discover in this same run directory; this is not a completed discovery stage.")
        return
    request = load(REQUEST)
    platform = platform_of(request)
    save_state(platform=platform)
    plan = load(INTENTS)
    intents = plan["intents"]
    # The collector searches in the language the sought accounts publish in. That is a judgment the
    # plan records (`content_language`, the «local» rule); the Request's locale.language is the report
    # language and is usually not it — measured on the 2026-09 runs, a zh-CN report collected in the
    # local-market language for a non-English market and in `en` otherwise. Without it the collector's own default applies.
    content_language = str(plan.get("content_language") or "").strip()
    if not content_language:
        say(f"note: {INTENTS} has no content_language, so the collector searches in its default language")
    since, until = window_of(request)
    locale = request.get("locale") or {}
    plan_calls: list[dict[str, Any]] = []
    # The collector is called exactly once per run, and on Instagram together with the planned profile
    # searches: both read only the frozen intents, so neither waits for the other.
    if platform == "instagram":
        script("plan_profile_discovery.py", INTENTS, PLAN)
        plan_calls = load(PLAN).get("calls") or []
        collect = {"platform": "instagram", "intents": intents, "window": collect_window(since, until),
                   # One Search page per intent: across 32 Instagram runs of 2026-09-18/19 no selected
                   # account came only from a second Search page, and each page is one more
                   # sequential provider call inside the collector.
                   "budget": {"search_pages_per_intent": 1, "popular_pages_per_intent": 1,
                              "challenger_pages_per_intent": 0, "max_provider_calls": 20,
                              "max_candidates": 60, "max_detail_calls": 0}}  # max_candidates=60, max_detail_calls=0
        if content_language:
            collect["language"] = content_language
        if locale.get("region"):
            collect["region"] = locale["region"]
        calls = [{"tool": "collect_instagram_trend_candidates", "arguments": collect}]
        # The planner emits at most four profile searches; they go out in the collector's round, each
        # as its own call, and each plan slot takes its own search's response.
        if len(plan_calls) > cd.BATCH_LIMIT:
            raise StageError(f"{PLAN} names {len(plan_calls)} profile searches; at most {cd.BATCH_LIMIT} are planned")
        calls += batch_calls([{"connector_id": IG, "operation": "search_profiles", "params": {"query": c["query"]}}
                              for c in plan_calls])
    else:
        # The YouTube collector names each intent's phrase `query`; the plan names it `search_query`.
        youtube_intents = [{"intent_id": i["intent_id"], "query": i.get("query") or i["search_query"]}
                           for i in intents]
        collect = {"platform": "youtube", "intents": youtube_intents, "published_after": since,
                   "published_before": until, "budget": {"max_candidates": 60}}
        if content_language:
            collect["language"] = content_language
        calls = [{"tool": "collect_youtube_trend_candidates", "arguments": collect}]
    # The Candidate Set is the host's own record of the collector result, copied by persist_envelope.py;
    # never retype, reconstruct, trim or relabel the Candidate Set, and never change its status.
    schema = "trend-candidate-set.v1" if platform == "instagram" else "youtube-trend-candidate-set.v1"

    # The host's record of each profile search, in plan order, as each round's fetch named it (None
    # where it did not: a hand-over, or a round already placed by an earlier run of this stage).
    search_records: list[Path | None] = []

    def capture(count: int) -> None:
        records = [path for request, path in (cd.LAST_RECORDS or []) if request.get("operation") == "search_profiles"]
        search_records.extend((records + [None] * count)[:count])

    def place_searches() -> None:
        """Each plan slot takes its own search's response: by the host's record of that request first
        (after a rate-limit retry, the retry), by staging order only where no record says which. Placing
        every slot by staging order put a superseded response's neighbour one slot early."""
        source = Path("profile-discovery-source.json")
        left = []
        base = run_dir_of(source.resolve()) or source.resolve().parent
        with run_lock(base):
            for index, _ in enumerate(plan_calls):
                record = search_records[index] if index < len(search_records) else None
                envelope = unwrap_envelope(record.read_text(encoding="utf-8")) if record and record.is_file() else None
                if envelope is None:
                    left.append(index)
                    continue
                write_json_atomic(source, place_at(read_json_or_empty(source), ["results", f"[{index}]"], envelope))
        for index in left:
            script("persist_envelope.py", "--into", str(source), "--at", "results",
                   "--at", f"[{index}]", "--connector", IG, "--operation", "search_profiles", "--any-status",
                   *drained())

    first_searches = sum(len(cd.requests_of(call)) for call in calls if call.get("tool") != "collect_instagram_trend_candidates")

    def place_discovery() -> None:
        script("persist_envelope.py", "--schema", schema, "--dest", CANDIDATES, *drained())
        capture(first_searches)
        place_searches()

    fetch_round(calls, args, place_discovery)
    if platform == "instagram":
        if plan_calls:
            script("persist_profile_envelopes.py", PLAN, "profile-discovery-source.json")
            script("merge_profile_discovery.py", "--candidates", CANDIDATES, "--plan", PLAN, "--output", CANDIDATES)
        script("project_gate_inputs.py", "discovery", "--request", REQUEST, "--candidates", CANDIDATES,
               "--output", GATE_INPUT, *seed_args(), "--table", show=True)
    else:
        # Numbered channels, labelled by number: retyping 24-character channel ids cost ~1.4k tokens a run
        # and twice wrote one id twice (one creator case, 2026-09-18/19).
        script("project_gate_inputs.py", "discovery", "--request", REQUEST, "--candidates", CANDIDATES,
               "--output", GATE_INPUT, *seed_args(), "--table", show=True)
        say(f"Gate: one line per numbered channel above into {GATE_SCORES} — `<n> d` (direct), `<n> a` (adjacent) "
            "or `<n> r` (reject), every number exactly once.")


def project_eligibility(platform: str, envelope_args: list[str]) -> None:
    """Project the eligibility input; a profile shortfall caused by lookups lost on the way says so."""
    try:
        script("project_gate_inputs.py", "eligibility", "--request", REQUEST, "--discovery", DISCOVERY,
               *envelope_args, "--output", ELIGIBILITY_INPUT, "--print", show=True)
    except StageError as error:
        raise lost_profiles(platform, str(error)) from None


def lost_profiles(platform: str, problem: str) -> StageError:
    """This stop, explained as a network or data-service interruption when profile lookups were lost on the way."""
    if not cd.lost_in_transit([HISTORY_TOOL]):
        return StageError(problem)
    return StageError(f"{problem}\nSome profile lookups of this run were lost on the way (the network or the data "
                      f"service failed) and stayed lost after one retry. {cd.LOST_NOTE}")


def usable_profiles() -> dict[str, str]:
    """Account key (lower-case handle or channel id) -> its placed profile file, usable ones only.

    Read from the run directory rather than from one round's placement, so a stage run again —
    after a manual fetch, say — sees every profile placed so far.
    """
    found: dict[str, str] = {}
    for path in sorted(Path.cwd().glob("profile-*.json")):
        if path.name.startswith("profile-discovery-"):
            continue
        try:
            envelope = load(path)
        except (OSError, json.JSONDecodeError):
            continue
        owner = owner_of(envelope) if isinstance(envelope, dict) else None
        if owner is not None and envelope.get("status") in USABLE_STATUSES:
            found[owner[0].lower()] = path.name
    return found


def resolve_requested_accounts(request: dict[str, Any], args: argparse.Namespace) -> list[dict[str, Any]]:
    identities = []
    resolved = {}
    for index, (ref, params) in enumerate(requested_accounts(request)):
        if platform_of(request) == "instagram":
            handle = params["handle"]
        else:
            key = tuple(sorted(params.items()))
            if key not in resolved:
                dest = f"provided-identity-{index + 1}.json"
                fetch_round([run_call(YT, "get_channel", params)], args, lambda dest=dest: place_record(
                    "get_channel", dest, lambda: script("persist_envelope.py", "--dest", dest,
                        "--connector", YT, "--operation", "get_channel", "--any-status", *drained())))
                resolved[key] = resolved_channel(params, load(dest))
            handle = resolved[key]
        identities.append({"ref": ref, "handle": handle})
    return identities


def stage_select(args: argparse.Namespace) -> None:
    request = load(REQUEST)
    requested_accounts(request)
    platform = platform_of(request)
    mode = discovery_mode(request)
    if mode != "provided":
        if platform == "instagram":
            script("build_discovery_gate.py", "--input", GATE_INPUT, "--scores", GATE_SCORES, "--output", GATE, show=True)
            strategy = "evidence-score-v3"
        else:
            if Path(GATE_SCORES).is_file():
                script("build_discovery_gate.py", "--labels", "--input", GATE_INPUT, "--scores", GATE_SCORES,
                       "--output", GATE, show=True)
            elif not Path(GATE).is_file():  # a Gate the model wrote as v2 JSON itself is read as it is
                raise StageError(f"{GATE_SCORES} not found in {Path.cwd()}: write one line per numbered channel, then run "
                                 "select again")
            strategy = "comparable-v2"
        subjects = [arg for handle in subject_handles(request) for arg in ("--subject-handle", handle)]
        summary = json.loads(script("select_accounts.py", "discover", "--candidates", CANDIDATES, "--gate", GATE,
                                    "--request", REQUEST, *subjects, "--strategy", strategy, "--output", DISCOVERY))
    if mode in {"provided", "hybrid"}:
        identities = resolve_requested_accounts(request, args)
        discovery = pin_requested_accounts(request, identities, load(DISCOVERY) if mode == "hybrid" else None)
        write_json_atomic(Path(DISCOVERY), discovery)
        summary = discovery
    selected = [str(h) for h in summary.get("selected_handles") or []]
    reserves = [str(h) for h in summary.get("reserve_handles") or []]
    say(f"selected: {', '.join(selected) or '(none)'}; reserves: {', '.join(reserves) or '(none)'}")
    # Profiles and first history pages of every selected account in one call, with step 3's
    # replacement rule inside it: one frozen reserve for every selected account whose profile came
    # back unavailable, in order, up to the reserves discovery froze.
    fetched: list[str] = []
    if selected:
        call = history_call(platform, selected, reserves=reserves)

        def place_selected(call: dict[str, Any]) -> None:
            groups = place_history(call, account_files(call))
            labels = group_accounts(call)
            fetched.extend(labels[g] for g in sorted(groups) if g.startswith("reserve-"))
            save_state(fetched_reserves=fetched)

        if not fetch_histories(call, args, place_selected):
            fetched = list(state().get("fetched_reserves") or [])
    profiles = usable_profiles()
    # Decided once: a stage run again (after a hand-over, or after a fix) keeps the replacements it
    # chose. A requested account is never replaced: a reserve the call fetched in its place stays
    # out of the eligibility projection.
    decided = state().get("replacements") or {}
    if decided.get("selected") == selected:
        replacements = list(decided.get("handles") or [])
    else:
        pinned = set(summary.get("provided_handles") or [])
        missing = [h for h in selected if h.lower() not in profiles and h not in pinned]
        replacements = [h for h in reserves if h in fetched][:len(missing)]
        save_state(replacements={"selected": selected, "handles": replacements})
    missing = [h for h in selected if h.lower() not in profiles]
    if replacements:
        say(f"unusable profiles: {', '.join(missing)}; replaced by reserves {', '.join(replacements)}")
    save_state(used_reserves=replacements)
    order = [h for h in [*selected, *replacements] if h.lower() in profiles]
    envelope_args = [arg for h in order for arg in ("--profile-envelope", profiles[h.lower()])]
    if not envelope_args:
        refs = ", ".join(request.get("competitor_refs") or selected)
        raise lost_profiles(platform, f"No requested or selected account returned a verified usable profile: {refs}. "
                                      "No substitute accounts were used.")
    project_eligibility(platform, envelope_args)
    show_schema("competitor-eligibility.schema.json")


def pages_of(prefix: str, label: str) -> list[Path]:
    """This account's placed pages in page order. persist_round.py names a file after the account as
    the response spells it, which may differ in case from the roster handle."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", label)
    pattern = re.compile(rf"{re.escape(prefix)}-{re.escape(safe)}-p(\d+)\.json", re.IGNORECASE)
    found = []
    for path in Path.cwd().glob(f"{prefix}-*-p*.json"):
        match = pattern.fullmatch(path.name)
        if match:
            found.append((int(match.group(1)), path))
    return [path for _, path in sorted(found)]


def page_facts(path: Path, since: float, until: float) -> dict[str, Any]:
    envelope = load(path)
    data = envelope.get("data") or {}
    rows = [row for row in data.get("rows") or [] if isinstance(row, dict)]
    taken = [(row.get("media") or row).get("taken_at") for row in rows]
    taken = [float(t) if isinstance(t, (int, float)) else None for t in taken]
    return {
        "usable": envelope.get("status") in USABLE_STATUSES,
        "more": bool(data.get("more_available")),
        "cursor": data.get("cursor"),
        "in_window": sum(1 for t in taken if t is not None and since <= t <= until),
        # Pinned Reels sit at the top of the first page whatever their age; the page's last row is
        # where the feed's chronological order has reached. A row without a date proves nothing.
        "covered": bool(taken) and taken[-1] is not None and taken[-1] < since,
    }


def next_reel_page(pages: list[Path], since: float, until: float) -> str | None:
    """The cursor of the next Reels page this account still owes, or None when it owes none.

    Step 4's budget, made exact: the second page when the feed goes on and either the window is not
    yet covered or fewer than six in-window Reels have come back (those older rows are what the
    account-relative baseline compares against); a third page only while the window is still not
    covered and six in-window Reels have still not come back. P7B (2026-09-18) fetched exactly the
    pages this rule names.
    """
    if not pages:
        return None
    facts = [page_facts(path, since, until) for path in pages]
    last = facts[-1]
    if not last["usable"] or not last["more"] or not last["cursor"]:
        return None
    short = sum(f["in_window"] for f in facts) < MIN_WINDOW_REELS
    if len(pages) < REEL_PAGES and (short or not last["covered"]):
        return str(last["cursor"])
    if len(pages) < REEL_PAGES_MAX and short and not last["covered"]:
        return str(last["cursor"])
    return None


def stage_content(args: argparse.Namespace) -> None:
    # 云端没有桌面 plugin root；这条链的快照构建器必须与 skill 一起校验、分发。
    # 缺件在取数前报错，避免再次拿完账号历史才发现安装不完整。
    if not (SCRIPTS / "build_public_snapshot.py").is_file():
        raise StageError(f"This skill package is incomplete: {SCRIPTS / 'build_public_snapshot.py'} is missing. "
                         "Reload a complete verified package before fetching account content.")
    request = load(REQUEST)
    # The run after a reserve's classification has no calls of its own to place: the reserve's were
    # placed by the run that asked for the classification. Carried over, `--placed` skipped the next
    # page round's hand-over and placement then found nothing (2026-10-27).
    if state().get("reserve_to_classify"):
        save_state(reserve_to_classify=False)
        if args.placed:
            say("[drive] the reserve's calls were already placed; --placed is ignored for this run")
            args.placed = False
    # A missing classification is an authoring slip, not a failed stage: closing the run here
    # throws away the fetched profiles (2026-10-27: a run wrote competitor-eligibility.v2.json).
    if not Path(ELIGIBILITY).is_file():
        misnamed = sorted(p.name for p in Path(".").glob("competitor-eligibility*.json")
                          if p.name not in (ELIGIBILITY, ELIGIBILITY_INPUT))
        say(json.dumps({"status": "needs_correction", "artifact": ELIGIBILITY,
                        "issues": [f"{ELIGIBILITY} is missing" +
                                   (f"; found {', '.join(misnamed)} — the file name is {ELIGIBILITY}, "
                                    "competitor-eligibility.v2 is only its schema_version" if misnamed else "")]},
                       ensure_ascii=False))
        say(f"Account content was not fetched. Write the classification to {ELIGIBILITY} in this run "
            "directory, then rerun drive.py content in this same run directory.")
        return
    # Eligibility's own schema and request id, and an empty relationship list, are not judgments.
    cd.complete_artifact(ELIGIBILITY, {"schema_version": "competitor-eligibility.v2",
                                       "request_id": request.get("request_id"), "direct_refs": [],
                                       "adjacent_refs": [], "media_refs": [], "excluded_refs": []})
    platform = platform_of(request)
    summary = json.loads(script("select_accounts.py", "finalize", "--discovery", DISCOVERY,
                                "--eligibility-input", ELIGIBILITY_INPUT, "--eligibility", ELIGIBILITY,
                                "--output", SELECTION))
    included = [str(h) for h in summary.get("included_handles") or []]
    relationships = summary.get("relationships") or {}
    say(f"included: {', '.join(f'{h} ({relationships.get(h)})' for h in included) or '(none)'}")
    # Step 3's second chance: fewer than three direct/adjacent accounts and a frozen reserve still
    # unused -> resolve that one reserve and let the model classify only it.
    comparable = [h for h in included if relationships.get(h) in ("direct", "adjacent")]
    discovery = load(DISCOVERY)
    used = {h.lower() for h in state().get("used_reserves") or []}
    unused = [str(h) for h in discovery.get("reserve_handles") or [] if str(h).lower() not in used]
    if len(comparable) < 3 and unused and not state().get("second_reserve_round"):
        extra = unused[:1]
        say(f"fewer than three direct/adjacent accounts; resolving reserve {extra[0]}")
        call = history_call(platform, extra)
        fetch_histories(call, args, lambda call: place_history(call, account_files(call)))
        profiles = usable_profiles()
        save_state(used_reserves=[*used, *[h.lower() for h in extra]], second_reserve_round=True)
        if extra[0].lower() not in profiles:
            say(f"reserve {extra[0]} returned no usable profile; continuing with the accounts already classified")
            return stage_content(args)
        roster = [str(h) for h in [*(discovery.get("selected_handles") or []),
                                   *(discovery.get("reserve_handles") or [])]]
        envelope_args = [arg for h in roster if h.lower() in profiles
                         for arg in ("--profile-envelope", profiles[h.lower()])]
        project_eligibility(platform, envelope_args)
        save_state(reserve_to_classify=True)
        say(f"Classify only {extra[0]}: add it to one list in {ELIGIBILITY}, leave every other entry as it is, "
            "then run drive.py content again, without --placed.")
        return
    since_text, until_text = window_of(request)
    since, until = epoch(since_text), epoch(until_text)
    formats = [str(f) for f in request.get("formats") or []]
    if platform == "instagram" and any(f != "reel" for f in formats):
        say(f"note: this workflow retrieves Reels only; formats {formats} other than reel are not fetched")
    envelopes: list[Path] = []
    incomplete = False
    if platform == "instagram":
        # Every included account that still owes a page (or has none yet) gets its Reels history in
        # one call, read again from the first page so its cursors are its own, under step 4's budget:
        # the window rule of `next_reel_page`, at most REEL_PAGES_MAX pages. Its pages replace the
        # first page `select` placed. Decided once: a stage run again keeps what that call returned.
        if not state().get("content_history"):
            owed = [h for h in included if not pages_of("reels", h)
                    or next_reel_page(pages_of("reels", h), since, until) is not None]
            if owed:
                call = history_call(platform, owed, profile=False, max_pages=REEL_PAGES_MAX,
                                    window=(since_text, until_text))
                fetch_histories(call, args, lambda call: place_history(call, account_files(call)))
            save_state(content_history=True)
        by_page: dict[int, list[Path]] = {}
        for handle in included:
            pages = pages_of("reels", handle)
            if not pages:
                incomplete = True
            for number, path in enumerate(pages, start=1):
                facts = page_facts(path, since, until)
                if facts["usable"]:
                    by_page.setdefault(number, []).append(path)
                if path == pages[-1] and (not facts["usable"] or (facts["more"] and not facts["covered"])):
                    # Its history stops at a failed page, or goes on inside the window.
                    incomplete = True
        envelopes = [path for number in sorted(by_page) for path in by_page[number]]
        operation = "list_reels"
    else:
        for handle in included:
            found = [p for p in Path.cwd().glob("uploads-*-p*.json") if usable(p)
                     and str(((load(p).get("data") or {}).get("channel") or {}).get("id")) == handle]
            if not found:
                incomplete = True
            envelopes.extend(sorted(found))
            for path in found:
                # Only an explicit `more_available: false` ends a history; a page that does not say
                # (the field absent) is unknown coverage, not a complete one.
                if (load(path).get("data") or {}).get("more_available") is not False:
                    incomplete = True
        operation = "list_channel_uploads"
    candidates = load(CANDIDATES) if request.get("discovery_mode") != "provided" else None
    discovery_incomplete = candidates is not None and (str(candidates.get("status")) != "completed"
                            or len(candidates.get("items") or []) >= 60)
    flags: list[str] = []
    if incomplete or discovery_incomplete:
        flags.append("--coverage-incomplete")
    # `--complete-operation` only when every Envelope for that operation explicitly reports
    # `more_available=false`: a YouTube channel at the 50-item ceiling with more history means omit
    # `--complete-operation list_channel_uploads` and keep `--coverage-incomplete`.
    if envelopes and not incomplete and all((load(p).get("data") or {}).get("more_available") is False for p in envelopes):
        flags += ["--complete-operation", operation]
    if not envelopes:
        raise StageError("no included account returned usable content")
    # Account-history Envelopes only — discovery Envelopes and audit artifacts never reach the
    # Snapshot, nor does content of an account eligibility excluded — with the frozen window as
    # `--window <since>/<until>` (so `--window auto` is never valid here) and no discovery `--query`.
    envelope_args = [arg for path in envelopes for arg in ("--envelope", path.name)]
    script("build_public_snapshot.py", "--platform", platform, "--window", f"{since_text}/{until_text}",
           *envelope_args, *flags, "--output", "public-content-snapshot.json")
    seed = (request.get("subject") or {}).get("seed_content_ref")
    retain = ["--retain-content-id", str(seed)] if seed else []
    script("analyze_public_snapshot.py", "public-content-snapshot.json", "--mode", "competitor", *retain,
           "--output", "public-content-analysis.json")
    coverage = script("build_topic_coverage.py", "--snapshot", "public-content-snapshot.json", "--selection",
                      SELECTION, "--output", "topic-coverage.json", "--print")
    with_comments = getattr(args, "comments", False) is True
    if with_comments:
        # 评论在打印证据之前取：评论调用被移交时阶段在这里停下，证据只由最后完成的那次运行打印一次，
        # 省掉单独的 comments 轮次。
        fetch_comments(args)
    evidence = script("project_synthesis_evidence.py", "--analysis", "public-content-analysis.json", "--snapshot",
                      "public-content-snapshot.json", "--selection", SELECTION, "--eligibility", ELIGIBILITY,
                      "--request", REQUEST, "--subject-history", "subject-history.json",
                      "--output", "synthesis-evidence.json", "--print")
    # schema 与下一步说明先打印、且用紧凑 JSON，再打印大段 topic coverage / synthesis evidence：
    # 宿主截断的是输出尾部（10-22..29 生产回执 55 轮里 12 轮被截），截掉的只会是已落盘的证据，
    # 不会是 schema。只改本包 drive.py；共享的 connector_driver.show_schema 不动。
    show_compact_schema("competitor-research-synthesis.schema.json")
    say("Next judgment: write competitor-research-synthesis.json in this run directory using "
        "the schema above and the topic coverage and synthesis evidence below; "
        "competitor-research-synthesis.v3.1 is the schema_version, not a file name. "
        "Then run the documented drive.py report stage.")
    say(coverage.rstrip())
    say(evidence.rstrip())
    if with_comments and Path(AUDIENCE).exists():
        say(Path(AUDIENCE).read_text(encoding="utf-8").rstrip())


def show_compact_schema(name: str) -> None:
    """One reference schema as a single compact JSON line (same content as the file, no indentation)."""
    say(json.dumps(json.loads((REFERENCES / name).read_text(encoding="utf-8")),
                   ensure_ascii=False, separators=(",", ":")))


def stage_comments(args: argparse.Namespace) -> None:
    """`comments` alone, after `content` finished: the same block `content --comments` runs.

    旧说明里单独的 `drive.py comments` 仍可运行，作为 `content --comments` 的别名保留。
    """
    if fetch_comments(args):
        say(Path(AUDIENCE).read_text(encoding="utf-8").rstrip())


def fetch_comments(args: argparse.Namespace) -> bool:
    """The comment block, only when the question needs audience comments: targets -> list_comments -> persist.

    Each response is placed at its target's position in fetch order, as the manual block did. A
    response that cannot be placed stops the placement there — the next response would otherwise
    slide into its slot — and the posts left over are recorded as `not_fetched`. True when
    `audience-comments.json` was written.
    """
    script("select_comment_targets.py", "--snapshot", "public-content-snapshot.json", "--selection", SELECTION,
           "--analysis", "public-content-analysis.json", "--output", COMMENT_TARGETS)
    plan = load(COMMENT_TARGETS)
    targets = plan.get("targets") or []
    if not targets:
        say("no comment targets: there is no comment evidence for this run; do not claim audience-comment findings.")
        return False
    requests = [{"connector_id": plan["connector_id"], "operation": plan["operation"], "params": t["params"]}
                for t in targets]

    def place_comments() -> None:
        for index in range(len(targets)):
            try:
                script("persist_envelope.py", "--into", COMMENT_SOURCE, "--at", "results", "--at", f"[{index}]",
                       "--connector", plan["connector_id"], "--operation", plan["operation"], "--any-status",
                       *drained())
            except StageError as error:
                say(f"[drive] comment response {index + 1} could not be placed; the rest stay unfetched: "
                    f"{str(error)[:300]}")
                break

    fetch_round(batch_calls(requests), args, place_comments)
    if not Path(COMMENT_SOURCE).exists():
        say("no comment response was placed: there is no comment evidence for this run; do not claim audience-comment findings.")
        return False
    script("persist_comment_envelopes.py", COMMENT_TARGETS, COMMENT_SOURCE, "--output", AUDIENCE, show=True)
    return True


# Synonyms models write for the synthesis status; any other value gets compose_report's correctable error.
STATUS_SYNONYMS = {"completed": "complete", "done": "complete", "ok": "complete", "success": "complete",
                   "incomplete": "partial", "partially_complete": "partial", "failed": "blocked"}


def normalize_synthesis_status(path: str) -> None:
    target = Path(path)
    if not target.is_file():
        return
    document = cd.load(target)
    status = document.get("status") if isinstance(document, dict) else None
    if isinstance(status, str) and status.strip().lower() in STATUS_SYNONYMS:
        document["status"] = STATUS_SYNONYMS[status.strip().lower()]
        target.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def stage_report(args: argparse.Namespace) -> None:
    if Path(REQUEST).is_file():
        # The Request's own id and the synthesis schema are not the model's to retype (audit item 7):
        # a Request written without request_id got the run's, which the synthesis must repeat.
        cd.complete_artifact("competitor-research-synthesis.json",
                             {"schema_version": "competitor-research-synthesis.v3.1",
                              "request_id": cd.load(REQUEST).get("request_id")})
        normalize_synthesis_status("competitor-research-synthesis.json")
    script("compose_report.py", "--request", REQUEST, "--snapshot", "public-content-snapshot.json",
           "--analysis", "public-content-analysis.json", "--synthesis", "competitor-research-synthesis.json",
           "--discovery", DISCOVERY, "--eligibility-input", ELIGIBILITY_INPUT, "--eligibility", ELIGIBILITY,
           "--selection", SELECTION, "--output", "competitor-research-report.json")
    script("validate_output.py", "competitor-research-report.json", "--request", REQUEST)
    script("render_creator_report.py", "competitor-research-report.json", "creator-report.md")
    # The Creator-facing reply, written deterministically in the Report's language (render_reply.py):
    # delivered by path where the host can show a file, relayed unchanged where it cannot.
    script("render_reply.py", "competitor-research-report.json", "creator-reply.md")
    reply = Path("creator-reply.md").resolve()
    say(f"creator_report: {reply}")
    say(reply.read_text(encoding="utf-8").rstrip())
    # 交付方式紧跟报告打印：实测里短问题的回复常被模型改写成摘要。
    say('Your reply is this report, even when the question was short: at most one short sentence of your own, then the report above complete and unchanged (translated only where the Skill says), then this creator_report line.')


# --------------------------------------------------------------------------- fewer host commands


def reserve_from_workspace(args: argparse.Namespace) -> None:
    """`--workspace` on a first stage: reserve the run directory, as `static_host.py init` would.

    Only when no run is reserved yet — neither KABO_STATIC_RUN_DIR nor a run directory around the
    current one — so the printed resume command, which runs inside the run, never reserves another.
    """
    workspace, args.workspace = args.workspace, None  # not part of what the request asked
    if not workspace:
        return
    import static_host

    try:
        static_host.run_dir()
        return
    except (ValueError, OSError):
        pass
    try:
        run = static_host.init(workspace)
    except (ValueError, OSError) as error:
        raise StageError(f"could not reserve a run directory in {workspace}: {error}") from None
    os.environ["KABO_STATIC_RUN_DIR"] = str(run)
    say(f"run directory: {run}")
    say("Run every later command of this request from that directory, as the printed commands do.")


def take_results(items: list[str]) -> None:
    """`--result <tool>=<file>`: fetch each saved result (stage it when it holds no ready download).

    The same two `static_host.py` commands the hand-over prints under each call, run here in the
    printed order before the stage resumes, so a hand-over costs the calls and one command. Every
    file is checked to exist before any is taken.
    """
    import static_host

    pairs: list[tuple[str, Path]] = []
    for item in items:
        tool, sep, name = item.partition("=")
        if not sep or not tool.strip() or not name.strip():
            raise StageError(f"--result takes <tool>=<saved result file>, not {item!r}")
        pairs.append((tool.strip(), Path(name.strip())))
    missing = [str(path) for _, path in pairs if not path.is_file()]
    if missing:
        raise StageError(f"no saved result at {', '.join(missing)}: save each call's complete result to the file "
                         "named under it (or name the file where the host saved it), then run this command again")
    for tool, path in pairs:
        with path.open("rb") as stream:
            raw = stream.read(static_host.MAX_BYTES + 1)
        try:
            result = static_host.fetch(tool, raw)
            how = "fetched"
            if result.get("fetched") == 0:
                result = static_host.stage(tool, raw)
                how = "staged"
        except (ValueError, OSError, KeyError, TypeError) as error:
            raise StageError(f"{path} ({tool}): {error}") from None
        held = result.get("duplicate")
        say(f"[drive] {path.name}: {how} {tool}" + (" (already held)" if held is True or (held and not result.get("staged"))
                                                   else ""))


def hand_over_in_one(calls: list[dict[str, Any]], why: str = "") -> None:
    """The shared hand-over, plus the same resume as one command that takes every saved result itself."""
    from static_host import public_calls

    public = [mapped for call in calls for mapped in public_calls(call)]
    results = Path("host-results")
    first = len(list(results.glob("*.json"))) + 1 if results.is_dir() else 1
    names = [f"host-results/{number:02d}-{mapped['tool']}.json" for number, mapped in enumerate(public, first)]
    try:
        shared_hand_over(calls, why)
    except SystemExit:
        say("These results are this run's inputs, not the reply: whatever they show, the next step after the "
            "calls is the resume, never an answer from the returned data.")
        say("Or, once every result above is saved, resume in one command instead (it fetches or stages each saved "
            "result itself, in this order): " + cd.resume_command() + "".join(
                f" --result {mapped['tool']}={name}" for mapped, name in zip(public, names)))
        raise


shared_hand_over = cd.hand_over
cd.hand_over = hand_over_in_one


def with_results(stage):
    def run(args: argparse.Namespace) -> None:
        items, args.result = list(getattr(args, "result", None) or []), None
        if items:
            take_results(items)
            args.placed = True
        stage(args)
    return run


STAGES = {
    "start": stage_start,
    "subject": stage_subject,
    "discover": stage_discover,
    "select": stage_select,
    "content": stage_content,
    "comments": stage_comments,
    "report": stage_report,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="stage", required=True)
    start = sub.add_parser("start")
    start.add_argument("--platform", choices=["instagram", "youtube"], required=True)
    start.add_argument("--query", help="the subject name as the user wrote it; omit when the user named none")
    start.add_argument("--post", help="the public Instagram post URL the user started from")
    subject = sub.add_parser("subject")
    subject.add_argument("--handle")
    subject.add_argument("--channel-id")
    subject.add_argument("--platform", choices=["instagram", "youtube"],
                         help="needed only for a YouTube @handle without a channel id; a bare --handle is Instagram")
    subject.add_argument("--seed", help="the placed get_post Envelope when the run started from a public post")
    for name in ("discover", "select", "content", "comments", "report"):
        sub.add_parser(name)
    sub.choices["content"].add_argument(
        "--comments", action="store_true",
        help="also fetch audience comments before the synthesis evidence is printed")
    for name in ("start", "subject"):
        sub.choices[name].add_argument(
            "--workspace", help="reserve the run directory in this workspace when none is reserved yet")
    for command in sub.choices.values():
        command.add_argument("--placed", action="store_true",
                             help="the connector calls were already made by hand; place their responses")
        command.add_argument("--result", action="append", metavar="TOOL=FILE",
                             help="a saved result of the last hand-over to fetch or stage first (implies --placed)")
    args = parser.parse_args()
    # The printed resume command repeats this command's arguments: never the results it already took.
    sys.argv = [arg for index, arg in enumerate(sys.argv)
                if not (arg == "--result" or arg.startswith("--result=")
                        or (index > 0 and sys.argv[index - 1] == "--result"))]
    if args.stage in ("start", "subject"):
        try:
            reserve_from_workspace(args)
        except StageError as error:
            say(f"drive.py {args.stage} stopped: {error}")
            return 2
    else:
        args.workspace = None
    return cd.run_stage({name: with_results(stage) for name, stage in STAGES.items()}, args,
                        first=("start", "subject"),
                        model_files=(REQUEST, INTENTS, GATE_SCORES, GATE, ELIGIBILITY,
                                     "competitor-research-synthesis.json"))

if __name__ == "__main__":
    raise SystemExit(main())
