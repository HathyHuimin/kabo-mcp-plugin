#!/usr/bin/env python3
"""Run everything between two judgments of a TikTok competitor run, so the model only judges.

A TikTok competitor run needs the model for three decisions: the frozen Request and query plan
(with the search region), the discovery Gate, and the synthesis. Everything between them is
mechanical — which connector call follows, with which parameters, where each response is placed,
which script runs next. Measured 2026-09-18 on two runs of this skill: 26 and 29 model requests,
most of which did nothing but that plumbing (a catalog read, one request per search, a separate
placement command, a separate comment-target command).

Each stage below starts right after one judgment and stops right before the next one: it makes the
connector calls the judgment implies, places every response into the same bundle files, at the
same positions, as the manual workflow did — so every later script reads exactly what it always
read — runs the deterministic steps, and prints exactly what the next judgment reads.

    start                  reserve the run directory     -> Request and query-plan schemas
    discover --region R    4 searches -> candidate set   -> discovery projection + Gate schema
    content [--comments]   selection -> 5 histories -> Snapshot -> analysis -> synthesis evidence
    comments               comment targets -> list_comments -> audience comments
    report                 Compose -> Validate -> Render -> the Creator report

A stage makes no data calls itself (connector_driver.py): it prints the calls as public MCP tool
names for the model to make in one response; the same stage run again with `--placed` picks the
staged responses up.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import connector_driver as cd  # noqa: E402
from sampling_policy import frozen_request  # noqa: E402
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

DISCOVERY = "tiktok-discovery"
PUBLIC = "tiktok-public"
REQUEST = "competitor-research-request.json"
PLAN = "tiktok-query-plan.json"
DISCOVERY_ENVELOPES = "discovery-envelopes.json"
CANDIDATES = "tiktok-candidate-set.json"
GATE_INPUT = "tiktok-competitor-discovery-input.json"
GATE = "tiktok-competitor-discovery-gate.json"
GATE_SCORES = "tiktok-competitor-discovery-gate.tsv"
SELECTION = "tiktok-competitor-account-selection.json"
HISTORIES = "account-history-envelopes.json"
SNAPSHOT = "public-content-snapshot.json"
ANALYSIS = "public-content-analysis.json"
COVERAGE = "topic-coverage.json"
EVIDENCE = "synthesis-evidence.json"
COMMENT_TARGETS = "comment-targets.json"
COMMENT_SOURCE = "comment-source.json"
AUDIENCE = "audience-comments.json"
SYNTHESIS = "competitor-research-synthesis.json"
REPORT = "competitor-research-report.json"
CREATOR_REPORT = "creator-report.md"

# search_videos: one call per intent and sort, 30 results each. sort_type 0 is relevance, 1 most
# liked; the run key carries the route name build_tiktok_candidate_set.py reads it by.
SORTS = ((0, "relevance"), (1, "most-liked"))
SEARCH_COUNT = 30
HISTORY_LIMIT = 100
ACCOUNT_LIMIT = 5
COMMENT_LIMIT = 3
REGION = re.compile(r"^[A-Z]{2}$")


def say(text: str = "") -> None:
    cd.say(text)


def round_key(calls: list[dict[str, Any]]) -> str:
    """The key connector_driver.fetch_round records a round under."""
    return hashlib.sha256(json.dumps(calls, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def fresh_bundle(bundle: str, calls: list[dict[str, Any]]) -> None:
    """Start this round's bundle, keeping an earlier round's as an audit copy.

    A stage run again for different calls (a rewritten plan or Gate) must not mix the earlier
    round's responses into this one. The earlier bundle stays in the run directory under another
    name, so the placement scripts still count its responses as taken and never place them again.
    A placement retried for the same round keeps its bundle: its filled slots are rewritten in place.
    """
    key = round_key(calls)
    bundles = dict(cd.state().get("bundles") or {})
    path = Path(bundle)
    if bundles.get(bundle) != key and path.exists():
        stem = path.name[: -len(".json")]
        number = 1
        while (path.parent / f"{stem}.previous-{number}.json").exists():
            number += 1
        path.rename(path.parent / f"{stem}.previous-{number}.json")
    bundles[bundle] = key
    cd.save_state(bundles=bundles)


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


def place_by_record(bundle: str, at: list[list[str]], connector: str, operation: str,
                    handles: list[str | None]) -> list[int]:
    """Place each response at its own request's position; returns the positions still unplaced.

    `at[i]` is the position of request i in call order. The write is persist_envelope.py's own
    (same bundle shape, same serialization), under the same run lock.
    """
    records = cd.LAST_RECORDS
    if records is None or len(records) != len(at):
        return list(range(len(at)))
    base = run_dir_of(Path(bundle).resolve()) or Path(bundle).resolve().parent
    left: list[int] = []
    with run_lock(base):
        for index, (segments, (request, path)) in enumerate(zip(at, records)):
            if request.get("operation") != operation:
                left.append(index)
                continue
            envelope = record_envelope(path, connector, operation, handles[index])
            if envelope is None:
                left.append(index)
                continue
            write_json_atomic(Path(bundle), place(read_json_or_empty(Path(bundle)), segments, envelope))
    return left


# --------------------------------------------------------------------------- start


def stage_start(args: argparse.Namespace) -> None:
    cd.reserve_run_directory(args.placed, args=args)
    run = Path.cwd().resolve()
    say("The following JSON Schemas are definitions for reference, not data instances to write. "
        "Do not copy $schema, $id, title, type, properties, required or additionalProperties "
        "into an authored Request or query plan.")
    cd.show_schema("competitor-research-request.schema.json", "tiktok-trend-query-plan.schema.json")
    say("Authoring shapes below are templates, not completed evidence or a confirmed user brief. "
        "Replace each bracketed value from the user's actual request. The driver supplies "
        "schema_version, request_id, intent_id and the default last-30-day window; keep the "
        "documented provided-account/latest-N or explicit-window fields when requested.")
    say(f"Actual run directory: {run}")
    say(f"Write {run / REQUEST}:")
    say(json.dumps({"subject": {"name": "<subject name>", "description": "<subject description>"},
                    "platforms": ["tiktok"],
                    "locale": {"region": "<target region>", "language": "<delivery language>"},
                    "formats": ["tiktok_video"], "discovery_mode": "auto", "competitor_refs": []}))
    say(f"Write {run / PLAN}:")
    say(json.dumps({"intents": [{"search_query": "<compact subject category or audience job>"}]}))
    say("Author both files in the actual run directory above, not the host workspace: use those "
        "absolute paths or cd into that directory before writing. Then run discover --region "
        "<two-letter target code> in that same run; do not reserve another run. "
        "Do not write schema-definition metadata to either file.")


# --------------------------------------------------------------------------- discover


def search_calls(plan: dict[str, Any], publish_time: int, region: str) -> list[dict[str, Any]]:
    """Two searches per intent, in plan order: sort_type 0, then 1. Individual runs, never a batch:
    a host-level batch failure would erase each route's own terminal evidence."""
    calls = []
    for intent in plan["intents"]:
        for sort_type, _ in SORTS:
            calls.append(cd.run_call(DISCOVERY, "search_videos", {
                "keyword": intent["search_query"], "sort_type": sort_type, "count": SEARCH_COUNT,
                "publish_time": publish_time, "region": region,
            }))
    return calls


def search_keys(plan: dict[str, Any]) -> list[str]:
    """One run key per search, in call order. Numbered rather than named after the intent: the
    candidate builder reads the route from the key (`liked` -> most_liked, `top…` -> ranked board),
    and an intent id may contain either."""
    return [f"intent-{number}-{route}" for number, _ in enumerate(plan["intents"], start=1) for _, route in SORTS]


def rekey_by_query(keys: list[str], keywords: list[str]) -> None:
    """After placement by fetch order, move any response onto the run of the keyword it answered.

    Responses the model made itself are placed in the order the host recorded them, and calls made
    together in one response may finish in any order. A search response names its own query
    (`data.query`), so a response placed under another intent's run is moved back; the two sorts of
    one intent answer the same query and keep their recorded order.
    """
    bundle = read_json_or_empty(Path(DISCOVERY_ENVELOPES))
    runs = bundle.get("runs") if isinstance(bundle, dict) else None
    if not isinstance(runs, dict) or not all(isinstance(runs.get(key), dict) for key in keys):
        return
    queries = [(runs[key].get("data") or {}).get("query") if isinstance(runs[key].get("data"), dict) else None
               for key in keys]
    if any(not isinstance(q, str) for q in queries) or queries == keywords or sorted(queries) != sorted(keywords):
        return
    pool: dict[str, list[dict[str, Any]]] = {}
    for key, query in zip(keys, queries):
        pool.setdefault(str(query), []).append(runs[key])
    for key, keyword in zip(keys, keywords):
        runs[key] = pool[keyword].pop(0)
    write_json_atomic(Path(DISCOVERY_ENVELOPES), bundle)
    say("[drive] search responses finished out of order; each was placed under the query it answered")


def complete_request() -> dict[str, Any]:
    """Fill the Request's mechanical fields the model left out (never one it wrote): schema_version,
    request_id, and the window when it is the default last 30 days (a span the user named is written)."""
    fields: dict[str, Any] = {"schema_version": "competitor-research-request.v3", "request_id": cd.run_id()}
    written = cd.load(REQUEST) if Path(REQUEST).is_file() else {}
    if isinstance(written, dict) and "sampling" not in written and "window" not in written and written.get("window_policy") in (None, "recent_30d"):
        fields["window"] = cd.last_days(30)
    completed = cd.complete_artifact(REQUEST, fields)
    if "sampling" in completed or Path(".sampling-request.sha256").exists():
        return frozen_request(Path(REQUEST), initialize=True)
    return completed


def stage_discover(args: argparse.Namespace) -> None:
    region = str(args.region or "").strip().upper()
    if not REGION.match(region):
        raise cd.StageError("discover needs --region: the two-letter ISO 3166-1 market code every search is sent "
                            "with, chosen by the region rule in SKILL.md")
    if region == "CN":
        raise cd.StageError("--region CN is never sent to search_videos (the provider rejects it or returns empty "
                            "pages for Chinese keywords); choose TW for Chinese keywords, or SG / HK")
    request = complete_request()
    cd.complete_artifact(PLAN, {"schema_version": "tiktok-trend-query-plan.v1",
                                "request_id": request.get("request_id")}, intent_ids=True)
    cd.script("validate_tiktok_query_plan.py", PLAN, show=True)
    resolved = json.loads(cd.script("resolve_tiktok_publish_time.py", REQUEST))
    publish_time = resolved["publish_time"]
    plan = cd.load(PLAN)
    calls = search_calls(plan, publish_time, region)
    keys = search_keys(plan)
    keywords = [call["arguments"]["params"]["keyword"] for call in calls]
    say(f"[drive] search_videos x{len(calls)}: publish_time={publish_time}, region={region}")

    def place_searches() -> None:
        fresh_bundle(DISCOVERY_ENVELOPES, calls)
        left = place_by_record(DISCOVERY_ENVELOPES, [["runs", key] for key in keys], DISCOVERY, "search_videos",
                               [None] * len(keys))
        if not left:
            return
        # Without the host's record of which call staged which response (the model made the calls,
        # or a record could not be paired): persist_many.py in call order, as the manual step did.
        cd.script("persist_many.py", "--connector", DISCOVERY, "--operation", "search_videos", "--any-status",
                  "--into", DISCOVERY_ENVELOPES, "--at", "runs", *[arg for i in left for arg in ("--slot", keys[i])],
                  *cd.drained())
        rekey_by_query(keys, keywords)

    cd.fetch_round(calls, args, place_searches)
    cd.script("build_tiktok_candidate_set.py", "--request", REQUEST, "--envelopes", DISCOVERY_ENVELOPES,
              "--output", CANDIDATES)
    cd.script("project_tiktok_accounts.py", "--request", REQUEST, "--candidates", CANDIDATES,
              "--output", GATE_INPUT, "--table", show=True)
    say(f"Gate: one line per numbered account above into {GATE_SCORES} — `<n> <relationship> <category_fit> "
        "<audience_job_fit> <sustained_focus> <locale_fit>`, relationship direct|adjacent|media|excluded, "
        "scores integers 0–100, every number exactly once.")


# --------------------------------------------------------------------------- content


def history_key(handle: str) -> str:
    return handle.strip().lstrip("@").lower()


def build_gate() -> None:
    """The Gate JSON from the numbered lines; a Gate the model wrote as JSON itself is read as it is."""
    if Path(GATE_SCORES).is_file():
        cd.script("build_tiktok_discovery_gate.py", "--input", GATE_INPUT, "--scores", GATE_SCORES, "--output", GATE)
    elif not Path(GATE).is_file():
        raise cd.StageError(f"{GATE_SCORES} not found in {Path.cwd()}: write one line per numbered account, then run "
                            "content again")


def stage_content(args: argparse.Namespace) -> None:
    frozen_request(Path(REQUEST))
    build_gate()
    summary = json.loads(cd.script("select_tiktok_accounts.py", "--candidates", CANDIDATES, "--gate-input", GATE_INPUT,
                                   "--gate", GATE, "--limit", str(ACCOUNT_LIMIT), "--output", SELECTION))
    included = [row for row in summary.get("handles") or [] if row.get("handle")]
    say("included: " + (", ".join(f"{row['handle']} ({row.get('relationship')})" for row in included) or "(none)"))
    unresolved = summary.get("unresolved") or []
    if unresolved:
        say("supplied but not compared (reported in data_gaps): "
            + ", ".join(f"{row.get('handle')} ({row.get('reason')})" for row in unresolved))
    if not included:
        say("No account passed the Gate selection, so there is no account history to retrieve. Tell the user no "
            "comparable TikTok account was found for this request; do not write a synthesis.")
        return
    handles = [str(row["handle"]) for row in included]
    calls = [cd.run_call(PUBLIC, "list_account_posts", {"handle": handle, "limit": HISTORY_LIMIT}) for handle in handles]
    keys = [history_key(handle) for handle in handles]

    def place_histories() -> None:
        fresh_bundle(HISTORIES, calls)
        left = place_by_record(HISTORIES, [["runs", key] for key in keys], PUBLIC, "list_account_posts", list(handles))
        if not left:
            return
        # `--handle` binds each response to its account, as the manual step did.
        cd.script("persist_many.py", "--connector", PUBLIC, "--operation", "list_account_posts", "--any-status",
                  "--into", HISTORIES, "--at", "runs", *[arg for i in left for arg in ("--slot", keys[i])],
                  *[arg for i in left for arg in ("--handle", handles[i])], *cd.drained())

    cd.fetch_round(calls, args, place_histories)
    cd.script("build_tiktok_competitor_snapshot.py", "--request", REQUEST, "--selection", SELECTION,
              "--histories", HISTORIES, "--output", SNAPSHOT)
    cd.script("analyze_public_snapshot.py", SNAPSHOT, "--mode", "competitor", "--output", ANALYSIS)
    cd.script("build_topic_coverage.py", "--snapshot", SNAPSHOT, "--selection", SELECTION, "--output", COVERAGE)
    if getattr(args, "comments", False):
        # Comments before the evidence is printed: when their calls are handed over, the stage stops
        # here and the evidence is printed once, by the run that finishes.
        stage_comments(args)
    cd.script("project_synthesis_evidence.py", "--analysis", ANALYSIS, "--snapshot", SNAPSHOT, "--selection", SELECTION,
              "--request", REQUEST, "--output", EVIDENCE, "--print", show=True)
    if getattr(args, "comments", False) and Path(AUDIENCE).exists():
        say(Path(AUDIENCE).read_text(encoding="utf-8").rstrip())
    cd.show_schema("competitor-research-synthesis.schema.json")


# --------------------------------------------------------------------------- comments


def stage_comments(args: argparse.Namespace) -> None:
    frozen_request(Path(REQUEST))
    """targets -> list_comments (once per target, together) -> comment-source.json -> audience comments.

    Each response is placed at its target's position (`results[i]`, the order of `targets[]`);
    persist_comment_envelopes.py pairs every envelope with its post by `aweme_id` whatever the order.
    """
    if not Path(SNAPSHOT).exists():
        raise cd.StageError("content has not finished; run `drive.py content --comments` (with --placed if it "
                            "handed its calls to you) instead")
    cd.script("select_comment_targets.py", "--snapshot", SNAPSHOT, "--selection", SELECTION,
              "--analysis", ANALYSIS, "--limit", str(COMMENT_LIMIT), "--output", COMMENT_TARGETS)
    plan = cd.load(COMMENT_TARGETS)
    targets = plan.get("targets") or []
    if not targets:
        say("no comment targets: there is no comment text for this run; record that in data_gaps, do not claim "
            "audience-want, and still deliver what the accounts have not done from titles.")
        return
    connector, operation = str(plan["connector_id"]), str(plan["operation"])
    calls = [cd.run_call(connector, operation, target["params"]) for target in targets]
    slots = [["results", f"[{index}]"] for index in range(len(targets))]

    def place_comments() -> None:
        fresh_bundle(COMMENT_SOURCE, calls)
        left = place_by_record(COMMENT_SOURCE, slots, connector, operation, [None] * len(slots))
        for index in left:
            try:
                cd.script("persist_envelope.py", "--into", COMMENT_SOURCE, "--at", "results", "--at", f"[{index}]",
                          "--connector", connector, "--operation", operation, "--any-status", *cd.drained())
            except cd.StageError as error:
                say(f"[drive] comment response {index + 1} could not be placed; that post and the ones after it "
                    f"stay not_fetched: {str(error)[:300]}")
                break

    cd.fetch_round(calls, args, place_comments)
    if not Path(COMMENT_SOURCE).exists():
        say("no comment response was placed: there is no comment text for this run; record that in data_gaps and "
            "do not claim audience-want.")
        return
    cd.script("persist_comment_envelopes.py", COMMENT_TARGETS, COMMENT_SOURCE, "--output", AUDIENCE, show=True)
    if not getattr(args, "comments", False):
        say(Path(AUDIENCE).read_text(encoding="utf-8").rstrip())


# --------------------------------------------------------------------------- report



def report_id_of(request: dict[str, Any]) -> str | None:
    """This run's own name, reused to name its report."""
    request_id = request.get("request_id")
    return f"{request_id}-report" if isinstance(request_id, str) and request_id else None

def stage_report(args: argparse.Namespace) -> None:
    frozen_request(Path(REQUEST))
    if Path(SYNTHESIS).is_file() and Path(REQUEST).is_file():
        # The Request's own id and the synthesis schema are not the model's to retype (audit item 7):
        # a Request written without request_id got the run's, which the synthesis must repeat.
        # See the trend channels: `report_id` names this report and the run already has a name,
        # so a forgotten slug no longer costs a round-trip. A slug the model writes is kept.
        request = cd.load(REQUEST)
        cd.complete_artifact(SYNTHESIS, {"schema_version": "competitor-research-synthesis.v3.1",
                                         "request_id": request.get("request_id"),
                                         "report_id": report_id_of(request)})
    cd.script("compose_report.py", "--request", REQUEST, "--snapshot", SNAPSHOT, "--analysis", ANALYSIS,
              "--synthesis", SYNTHESIS, "--output", REPORT)
    cd.script("validate_output.py", REPORT, "--request", REQUEST)
    presentation = getattr(args, "presentation", None)
    prepare = getattr(args, "prepare_presentation", False)
    if prepare and presentation:
        raise ValueError("choose prepare or final presentation, never both")
    language = (cd.load(REQUEST).get("locale") or {}).get("language")
    if prepare:
        cd.script("render_creator_report.py", REPORT, CREATOR_REPORT, "--language", language,
                  "--print-presentation-contract", "--contract-output", "report-presentation.json", show=True)
        say("presentation_contract: " + str(Path("report-presentation.json").resolve()))
        say("Fill only text in that contract; then run the same report stage with --presentation report-presentation.json. "
            "Keep this run open until that final stage; do not fetch again or rewrite authored synthesis prose.")
        return
    render_args = [REPORT, CREATOR_REPORT]
    if presentation:
        candidate = Path(presentation).resolve()
        if candidate.parent != Path.cwd().resolve() or candidate.name != "report-presentation.json":
            raise ValueError("presentation must be this run's report-presentation.json")
        render_args += ["--language", language, "--presentation", str(candidate)]
    rendered = cd.script("render_creator_report.py", *render_args)
    if rendered and rendered.strip():
        # 渲染器只在有译文槽位退回原文时输出 kept_source_text，原样转给 agent。
        say(rendered.rstrip())
    report = Path(CREATOR_REPORT).resolve()
    say(f"creator_report: {report}")
    say(report.read_text(encoding="utf-8").rstrip())


STAGES = {
    "start": stage_start,
    "discover": stage_discover,
    "content": stage_content,
    "comments": stage_comments,
    "report": stage_report,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="stage", required=True)
    sub.add_parser("start")
    discover = sub.add_parser("discover")
    discover.add_argument("--region", help="the two-letter market code every search_videos call is sent with")
    content = sub.add_parser("content")
    content.add_argument("--comments", action="store_true",
                         help="also fetch audience comments (when the question asks what the accounts have not "
                              "done or what the audience wants)")
    sub.add_parser("comments")
    report = sub.add_parser("report")
    mode = report.add_mutually_exclusive_group()
    mode.add_argument("--prepare-presentation", action="store_true")
    mode.add_argument("--presentation")
    for command in sub.choices.values():
        command.add_argument("--placed", action="store_true",
                             help="the connector calls were already made by hand; place their responses")
    return cd.run_stage(STAGES, parser.parse_args(), first=("start",),
                        model_files=(REQUEST, PLAN, GATE, GATE_SCORES, SYNTHESIS, "report-presentation.json"))


if __name__ == "__main__":
    raise SystemExit(main())
