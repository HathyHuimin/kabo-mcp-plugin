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
    discover --region R    one collect_tiktok_video_candidates call (every intent x both sorts)
                           -> candidate set -> discovery projection + Gate schema
    content [--comments]   selection -> one collect_account_history call (up to 5 accounts) ->
                           Snapshot -> analysis -> synthesis evidence
    comments               comment targets -> list_comments -> audience comments
    report                 Compose -> Validate -> Render -> the Creator report

A stage makes no data calls itself (connector_driver.py): it prints the calls as public MCP tool
names for the model to make in one response; the same stage run again with `--placed` picks the
staged responses up. The searches and the account histories each run as one backend call
(kabo-composite-fetch.v1); `static_host.py fetch` stores every single call it made as its own
evidence, tagged with its group (`intent-<i>-sort-<s>`, `acct-<i>`), and the stage places each
response under the run key of the intent, sort or account its group names, so every script after
it reads exactly what it read when the model made each call itself.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import connector_driver as cd  # noqa: E402
from sampling_policy import frozen_request  # noqa: E402
from persist_envelope import unwrap_envelope, write_json_atomic  # noqa: E402

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

# The searches and the account histories each run as one backend call (kabo-composite-fetch.v1).
COLLECT_CANDIDATES = "collect_tiktok_video_candidates"
COLLECT_HISTORY = "collect_account_history"
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


def composite_record(tool: str, wanted: Callable[[dict[str, Any]], bool]) -> dict[str, Any] | None:
    """The newest staged record of this composite tool that answers the call this stage made.

    `static_host.py fetch` / `stage` split a composite result into one evidence file per backend call
    plus this record (summary, coverage, limitations, calls with their groups and evidence paths).
    `wanted` checks the record's summary against what was asked, so an earlier collection of the same
    tool in this run (a rewritten plan) is never taken for this one.
    """
    import static_host
    try:
        records = static_host.composite_records(tool)
    except ValueError as error:
        raise cd.StageError(str(error)) from error
    for record in reversed(records):
        if wanted(record):
            return record
    return None


def composite_calls(record: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Each backend call of a composite record by its group (the last one of a group), with its
    envelope as staged and the parameters the backend sent."""
    calls: dict[str, dict[str, Any]] = {}
    for call in record.get("calls") or []:
        path = Path(str(call.get("path") or ""))
        try:
            envelope = unwrap_envelope(path.read_text(encoding="utf-8"))
            meta = json.loads(path.with_suffix(".meta").read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            envelope, meta = None, {}
        if envelope is None:
            raise cd.StageError(f"the staged evidence of {record.get('tool')} call {call.get('index')} "
                                f"({call.get('group')}) is missing or unreadable: {path}")
        parameters = meta.get("composite_parameters") if isinstance(meta, dict) else None
        calls[str(call.get("group"))] = {"envelope": envelope,
                                         "parameters": parameters if isinstance(parameters, dict) else {}}
    return calls


def say_limitations(record: dict[str, Any]) -> None:
    for line in record.get("limitations") or []:
        say(f"[drive] {record.get('tool')}: {str(line)[:300]}")


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


def discovery_call(plan: dict[str, Any], publish_time: int, region: str) -> dict[str, Any]:
    """Every intent x both sorts as one backend call: the frozen intent phrase as `keyword`, 30 results
    each, the same `region` and `publish_time` on every search."""
    return {"tool": COLLECT_CANDIDATES, "arguments": {
        "intents": [{"keyword": intent["search_query"], "publish_time": publish_time} for intent in plan["intents"]],
        "sorts": [sort_type for sort_type, _ in SORTS], "count": SEARCH_COUNT, "region": region}}


def search_keys(plan: dict[str, Any]) -> list[tuple[str, str]]:
    """(run key, composite group) per search, in call order. The key is numbered rather than named
    after the intent: the candidate builder reads the route from the key (`liked` -> most_liked,
    `top…` -> ranked board), and an intent id may contain either."""
    return [(f"intent-{number + 1}-{route}", f"intent-{number}-sort-{sort_type}")
            for number, _ in enumerate(plan["intents"]) for sort_type, route in SORTS]


def place_searches(plan: dict[str, Any]) -> None:
    """Place every search response under the intent and sort its group names; a failed one stays as
    that route's evidence. A search the backend returned nothing for stops discovery."""
    expected = [[intent["search_query"], sort_type] for intent in plan["intents"] for sort_type, _ in SORTS]

    def wanted(record: dict[str, Any]) -> bool:
        searches = (record.get("summary") or {}).get("searches") or []
        return [[row.get("keyword"), row.get("sort_type")] for row in searches if isinstance(row, dict)] == expected

    record = composite_record(COLLECT_CANDIDATES, wanted)
    if record is None:
        raise cd.StageError(f"no complete host record of {COLLECT_CANDIDATES} for this run: fetch (or stage) the "
                            "result of the call discover handed over, then run discover again with --placed")
    say(f"[drive] {COLLECT_CANDIDATES}: status={record.get('status')}")
    say_limitations(record)
    calls = composite_calls(record)
    slots = search_keys(plan)
    missing = [group for _, group in slots if group not in calls]
    if missing:
        raise cd.StageError(f"no complete host record of the searches {', '.join(missing)} "
                            f"({'; '.join(map(str, record.get('limitations') or [])) or 'no reason given'})")
    write_json_atomic(Path(DISCOVERY_ENVELOPES), {"runs": {key: calls[group]["envelope"] for key, group in slots}})


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
    calls = [discovery_call(plan, publish_time, region)]
    say(f"[drive] search_videos x{len(plan['intents']) * len(SORTS)} in one backend call: "
        f"publish_time={publish_time}, region={region}")

    def place() -> None:
        fresh_bundle(DISCOVERY_ENVELOPES, calls)
        place_searches(plan)

    cd.fetch_round(calls, args, place)
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


def place_history_record(keys: list[str]) -> None:
    """Place each account's history under its own handle, from the group the backend fetched it in.

    A failed or unresolved account keeps its failed response as that account's evidence; an account
    the backend returned nothing for stops the stage, as a missing response always did.
    """
    def wanted(record: dict[str, Any]) -> bool:
        accounts = [row for row in (record.get("summary") or {}).get("accounts") or []
                    if isinstance(row, dict) and str(row.get("group") or "").startswith("acct-")]
        return [history_key(str((row.get("input") or {}).get("handle") or "")) for row in accounts] == keys

    record = composite_record(COLLECT_HISTORY, wanted)
    if record is None:
        raise cd.StageError(f"no complete host record of {COLLECT_HISTORY} for this run: fetch (or stage) the "
                            "result of the call content handed over, then run content again with --placed")
    say(f"[drive] {COLLECT_HISTORY}: status={record.get('status')}")
    say_limitations(record)
    calls = composite_calls(record)
    missing = [key for index, key in enumerate(keys) if f"acct-{index}" not in calls]
    if missing:
        raise cd.StageError(f"no complete host record of the account histories of {', '.join(missing)} "
                            f"({'; '.join(map(str, record.get('limitations') or [])) or 'no reason given'})")
    write_json_atomic(Path(HISTORIES), {"runs": {key: calls[f"acct-{index}"]["envelope"]
                                                 for index, key in enumerate(keys)}})


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
    keys = [history_key(handle) for handle in handles]
    calls = [{"tool": COLLECT_HISTORY, "arguments": {
        "platform": "tiktok", "accounts": [{"handle": handle} for handle in handles],
        "history": {"max_items": HISTORY_LIMIT}, "profile": False, "on_identity_miss": "gap"}}]

    def place_histories() -> None:
        fresh_bundle(HISTORIES, calls)
        place_history_record(keys)

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
    slots = range(len(targets))

    def place_comments() -> None:
        fresh_bundle(COMMENT_SOURCE, calls)
        for index in slots:
            try:
                cd.script("persist_envelope.py", "--into", COMMENT_SOURCE, "--at", "results", "--at", f"[{index}]",
                          "--connector", connector, "--operation", operation, "--any-status")
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
    # 交付方式紧跟报告打印：实测里短问题的回复常被模型改写成摘要。
    say('Your reply is this report, even when the question was short: at most one short sentence of your own, then the report above complete and unchanged (translated only where the Skill says), then this creator_report line.')


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
