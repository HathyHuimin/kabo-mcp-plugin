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
"""

from __future__ import annotations

import argparse
import json
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
from persist_round import PROFILE_OPERATIONS, owner_of, place_round, report as round_report, target_for  # noqa: E402

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


def history_rows(path: str) -> tuple[str | None, int]:
    """The status of a placed history page and how many rows it holds."""
    try:
        envelope = load(path)
    except (OSError, json.JSONDecodeError):
        return None, 0
    rows = (envelope.get("data") or {}).get("rows") if isinstance(envelope, dict) else None
    return (envelope.get("status") if isinstance(envelope, dict) else None), len(rows) if isinstance(rows, list) else 0


def profile_post_count(path: str) -> int:
    """How many posts the placed profile says the account has; 0 when it does not say."""
    try:
        envelope = load(path)
    except (OSError, json.JSONDecodeError):
        return 0
    data = (envelope.get("data") or {}) if isinstance(envelope, dict) else {}
    rows = data.get("rows") if isinstance(data.get("rows"), list) else []
    profile = data.get("profile") if isinstance(data.get("profile"), dict) else (
        rows[0] if rows and isinstance(rows[0], dict) else {})
    for key in ("post_count", "media_count", "posts_count"):
        value = profile.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return int(value)
    return 0


def retry_empty_history(handle: str, args: argparse.Namespace) -> None:
    """Ask for the subject's Reels once more when the page came back empty from an account with posts.

    One video-started case (2026-09-19): `list_reels` answered `completed` with no rows for an account whose
    profile listed 234 posts, while the same call returned twelve rows 14 minutes before and 11 after.
    Without that history the caption phrases came from the seed post alone and discovery drifted off
    the subject's own wording. A failed page (quota, not found) is not retried here: asking again does
    not change it, and a rate limit is already retried inside the fetch.
    """
    status, rows = history_rows("subject-history.json")
    if status not in USABLE_STATUSES or rows:
        return
    posts = profile_post_count("subject-profile.json")
    if posts <= 0:
        return
    calls = [run_call(IG, "list_reels", {"handle": handle})]
    key = cd.round_key(calls)
    current = state()
    if key not in (current.get("rounds") or []):
        if key not in (current.get("handed") or {}):
            say(f"subject history: no Reels although the profile lists {posts} posts; asking once more")
            # The empty page stays counted as placed, so placing by content takes the new response.
            cd.supersede(Path("subject-history.json"))
        fetch_round(calls, args, lambda: place_record(
            "list_reels", "subject-history.json", lambda: script(
                "persist_many.py", "--connector", IG, "--operation", "list_reels", "--any-status",
                "--dest", "subject-history.json", "--handle", handle, *drained())))
    if not history_rows("subject-history.json")[1]:
        say("note: the subject's Reels came back empty twice; the seed post and profile are the only "
            "wording from the subject in this run")


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


def resolve_youtube_handle(handle: str, args: argparse.Namespace) -> str:
    """The channel id behind a YouTube @handle, asked with `get_channel` alone.

    `youtube_get_channel` takes a handle on every contract version; the subject round itself then
    stays on the channel id, as every other YouTube call does.
    """
    fetch_round([run_call(YT, "get_channel", {"handle": handle})], args, lambda: place_record(
        "get_channel", "subject-handle.json", any_status=False, by_content=lambda: script(
            "persist_many.py", "--connector", YT, "--operation", "get_channel", "--dest", "subject-handle.json",
            *drained())))
    profile = load("subject-handle.json")
    rows = (profile.get("data") or {}).get("rows") or [] if isinstance(profile, dict) else []
    ids = [row.get("id") for row in rows if isinstance(row, dict)]
    if profile.get("status") not in USABLE_STATUSES or len(ids) != 1 or not isinstance(ids[0], str) or not ids[0].startswith("UC"):
        raise StageError(f"YouTube handle {handle} resolved to no single channel; ask the user for the channel link")
    # The handle is the identity asked for, whatever `start` searched: the profile must prove it.
    save_state(subject_handle=handle)
    return ids[0]


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
        handle = args.handle.lstrip("@")
        # Instagram subject history is `instagram-discovery/list_reels` only — never
        # `instagram-baseline-proxy`: on 2026-09-14 the proxy timed out and the caption extractor saw
        # zero captions.
        requests = [
            {"connector_id": IG, "operation": "resolve_profile", "params": {"handle": handle, "include_post_count": True}},
            {"connector_id": IG, "operation": "list_reels", "params": {"handle": handle}},
        ]
        profile_op, history_op, connector = "resolve_profile", "list_reels", IG
        save_state(subject_handles=[handle])
    else:
        if not args.channel_id and not (args.handle or "").strip():
            raise StageError("YouTube needs --channel-id (the UC… id the candidates printed) or --handle <@handle>")
        channel_id = args.channel_id or resolve_youtube_handle("@" + args.handle.strip().lstrip("@"), args)
        # `get_channel` accepts exactly one identity parameter — here the canonical channel id; never
        # send both in one request. The same one-identity rule holds for `list_channel_uploads`.
        requests = [
            {"connector_id": YT, "operation": "get_channel", "params": {"channel_id": channel_id}},
            # 50, the most one call returns (as for competitors): with 20, an active channel's own
            # history ended inside the window and its cadence was only a lower bound (plugins,
            # 2026-10-27). Still one call; a longer history stays a lower bound.
            {"connector_id": YT, "operation": "list_channel_uploads",
             "params": {"channel_id": channel_id, "max_results": 50}},
        ]
        profile_op, history_op, connector = "get_channel", "list_channel_uploads", YT
        save_state(platform="youtube", subject_handles=[channel_id, *([args.handle] if args.handle else [])])
    dests = {profile_op: "subject-profile.json", history_op: "subject-history.json"}

    def place_subject() -> None:
        # By its own request first. Placing by staging order re-placed the first subject's response
        # when the stage ran again for a second handle, because the file being rewritten does not
        # count as taken: on one product case, 2026-09-18, three `subject` runs all printed the same handle and the
        # model had to start a new run directory (about 65 s).
        records = cd.LAST_RECORDS or []
        if len(records) == len(dests) and all(path is not None and request.get("operation") in dests
                                              for request, path in records):
            for request, path in records:
                target = Path(dests[str(request["operation"])])
                target.write_bytes(path.read_bytes())
                target.chmod(0o600)
                normalize_response_file(target)
            return
        identity = ["--handle", handle] if platform == "instagram" else []
        for operation, dest in dests.items():
            script("persist_many.py", "--connector", connector, "--operation", operation, "--any-status",
                   "--dest", dest, *identity, *drained())

    fetch_round(batch_calls(requests), args, place_subject)
    if platform == "instagram":
        retry_empty_history(handle, args)
    else:
        verify_explicit_youtube_handle()
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
    later_searches: list[dict[str, Any]] = []
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
        searches = batch_calls([{"connector_id": IG, "operation": "search_profiles", "params": {"query": c["query"]}}
                                for c in plan_calls])
        # Profile-search responses are placed into plan slots in the order they were staged, and the
        # host runs the calls of one round concurrently: only one batch per round keeps that order.
        # The planner emits at most four queries, so this is one round today.
        calls += searches[:1]
        later_searches = searches[1:]
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

    if platform == "instagram" and later_searches:
        def place_first() -> None:
            script("persist_envelope.py", "--schema", schema, "--dest", CANDIDATES, *drained())
            capture(first_searches)

        if not fetch_round(calls, args, place_first):
            search_records.extend([None] * first_searches)
        for search in later_searches:
            count = len(cd.requests_of(search))
            if not fetch_round([search], args, lambda count=count: capture(count)):
                search_records.extend([None] * count)
        place_searches()  # every slot at once: by record where one was named, else in staging order
    else:
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


def profile_requests(platform: str, handles: list[str]) -> list[dict[str, Any]]:
    requests = []
    for handle in handles:
        if platform == "instagram":
            # Instagram profile identity is `instagram-discovery/resolve_profile` only.
            requests.append({"connector_id": IG, "operation": "resolve_profile",
                             "params": {"handle": handle, "include_post_count": True}})
            requests.append({"connector_id": IG, "operation": "list_reels", "params": {"handle": handle}})
        else:
            # One identity parameter per call: the canonical channel id. persist_round.py files each
            # response under the id the response names, so a returned canonical channel id must equal
            # the requested `channel.id` exactly to fill that account; a mismatch leaves it unfilled
            # and the reserve replacement below takes over. `list_channel_uploads` once per included
            # channel, `max_results: 50`.
            requests.append({"connector_id": YT, "operation": "get_channel", "params": {"channel_id": handle}})
            requests.append({"connector_id": YT, "operation": "list_channel_uploads",
                             "params": {"channel_id": handle, "max_results": 50}})
    return requests


def operations_of(platform: str) -> list[str]:
    return ["resolve_profile", "list_reels"] if platform == "instagram" else ["get_channel", "list_channel_uploads"]


def place(operations: list[str]) -> dict[str, Any]:
    run_dir = run_dir_of(Path.cwd() / ".persist-round.json")
    if run_dir is None:
        raise StageError("run drive.py from inside the run directory")
    moved = drained()
    result = place_round(run_dir, operations, Path(moved[1]).resolve() if moved else None)
    say(round_report(result))
    return result


def place_records(operations: list[str]) -> None:
    """Place each response under the account its own request named; the rest by content (`place`).

    An empty or failed history page names no account, and matching those by fetch order put one
    account's rate-limited page on another and three copies of one empty page on a third (one product case,
    2026-09-18). The host's record of which call staged which file makes that guess unnecessary.
    """
    records = cd.LAST_RECORDS
    if records is None:  # the model made these calls itself (--placed): only their content says whose
        place(operations)
        return
    run_dir = run_dir_of(Path.cwd() / ".persist-round.json")
    if run_dir is None:
        raise StageError("run drive.py from inside the run directory")
    placed: list[dict[str, Any]] = []
    leftover = False
    with run_lock(run_dir):
        for request, path in records:
            operation = str(request.get("operation"))
            if operation not in operations:
                continue
            params = request.get("params") or {}
            account = str(params.get("handle") or params.get("channel_id") or "").lstrip("@")
            if path is None or not account:
                leftover = True
                continue
            body = path.read_bytes()
            raw_record = json.loads(body)
            envelope = response_record(raw_record, path)
            owner = owner_of(envelope)
            if owner is not None and owner[0].lower() != account.lower():
                leftover = True  # its rows name someone else: let the content decide
                continue
            target = target_for(run_dir, operation, account)
            if operation in PROFILE_OPERATIONS and target.exists():
                continue
            target.write_bytes(body)
            target.chmod(0o600)
            if envelope != raw_record:
                write_json_atomic(target, envelope)
            placed.append({"path": target.name, "operation": operation, "account": account,
                           "status": envelope.get("status"), "rows": row_count(envelope),
                           "matched_by": "content" if owner is not None else "its request"})
    say(round_report({"placed": placed, "skipped": []}))
    if leftover:
        try:
            place(operations)
        except ValueError as error:
            say(f"[drive] nothing further to place by content: {str(error)[:200]}")


def profile_round(platform: str, handles: list[str], args: argparse.Namespace) -> None:
    """Fetch and place profile + history for these accounts."""
    fetch_round(batch_calls(profile_requests(platform, handles)), args, lambda: place_records(operations_of(platform)))


def project_eligibility(platform: str, envelope_args: list[str]) -> None:
    """Project the eligibility input; a profile shortfall caused by lookups lost on the way says so."""
    try:
        script("project_gate_inputs.py", "eligibility", "--request", REQUEST, "--discovery", DISCOVERY,
               *envelope_args, "--output", ELIGIBILITY_INPUT, "--print", show=True)
    except StageError as error:
        raise lost_profiles(platform, str(error)) from None


def lost_profiles(platform: str, problem: str) -> StageError:
    """This stop, explained as a network or data-service interruption when profile lookups were lost on the way."""
    if not cd.lost_in_transit(operations_of(platform)):
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
    if selected:
        profile_round(platform, selected, args)
    profiles = usable_profiles()
    # One frozen reserve for every selected account whose profile came back unusable, up to the
    # reserves discovery froze — the replacement rule of step 3.
    # Decided once: a stage run again (after a hand-over, or after a fix) keeps the replacements it
    # chose, even though they now have usable profiles of their own.
    decided = state().get("replacements") or {}
    if decided.get("selected") == selected:
        replacements = list(decided.get("handles") or [])
    else:
        pinned = set(summary.get("provided_handles") or [])
        missing = [h for h in selected if h.lower() not in profiles and h not in pinned]
        replacements = reserves[:len(missing)]
        save_state(replacements={"selected": selected, "handles": replacements})
    missing = [h for h in selected if h.lower() not in profiles]
    if replacements:
        say(f"unusable profiles: {', '.join(missing)}; resolving reserves {', '.join(replacements)}")
        profile_round(platform, replacements, args)
        profiles = usable_profiles()
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
        profile_round(platform, extra, args)
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
        # Retrieval rounds: every account that still owes a page gets it in the same round.
        for _ in range(REEL_PAGES_MAX):
            owed = []
            for handle in included:
                cursor = next_reel_page(pages_of("reels", handle), since, until)
                if cursor is not None:
                    owed.append({"connector_id": IG, "operation": "list_reels",
                                 "params": {"handle": handle, "max_id": cursor}})
                elif not pages_of("reels", handle):
                    owed.append({"connector_id": IG, "operation": "list_reels", "params": {"handle": handle}})
            if not owed:
                break
            if not fetch_round(batch_calls(owed), args, lambda: place_records(["list_reels"])):
                break
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


def stage_report(args: argparse.Namespace) -> None:
    if Path(REQUEST).is_file():
        # The Request's own id and the synthesis schema are not the model's to retype (audit item 7):
        # a Request written without request_id got the run's, which the synthesis must repeat.
        cd.complete_artifact("competitor-research-synthesis.json",
                             {"schema_version": "competitor-research-synthesis.v3.1",
                              "request_id": cd.load(REQUEST).get("request_id")})
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
    say(f"creator reply: {reply}")
    say(reply.read_text(encoding="utf-8").rstrip())


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
    for command in sub.choices.values():
        command.add_argument("--placed", action="store_true",
                             help="the connector calls were already made by hand; place their responses")
    return cd.run_stage(STAGES, parser.parse_args(), first=("start", "subject"),
                        model_files=(REQUEST, INTENTS, GATE_SCORES, GATE, ELIGIBILITY,
                                     "competitor-research-synthesis.json"))

if __name__ == "__main__":
    raise SystemExit(main())
