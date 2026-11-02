#!/usr/bin/env python3
"""Fetch bounded evidence, calculate selected modules, then check and report the written analysis."""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import connector_driver as cd  # noqa: E402
from correction import CONTENT_KIND, PLATFORM, Correction, Parser, language, read_file  # noqa: E402
from persist_envelope import USABLE_STATUSES, account_of, row_count, unwrap_envelope  # noqa: E402

IG = "instagram-discovery"
YT = "youtube-public"
TT = "tiktok-public"
PUBLIC_SAMPLE_CAP = 30
INSTAGRAM_FORMAT_CAP = 15
SNAPSHOT_DIR = Path("snapshot")
RAW_SNAPSHOT = Path("snapshot-full.json")
SNAPSHOT = Path("snapshot.json")
ANALYSIS = Path("account_analysis.json")
BRIEF_INPUT = Path("traffic-brief-input.json")
SYNTHESIS_INPUT = Path("traffic-synthesis-input.json")
NORMALIZED_BRIEF = Path("traffic_brief.json")
CASE = Path("traffic_case.json")
ASSESSMENT = Path("reach_drop_assessment.json")
QUESTION = Path("user-request.txt")


def bare(value: str | None) -> str:
    return str(value or "").strip().lstrip("@").strip()


PROFILE_HOSTS = {"instagram": "instagram.com", "tiktok": "tiktok.com", "youtube": "youtube.com"}
CHANNEL_ID = re.compile(r"UC[\w-]{22}")


def account_identity(platform: str, handle: str, channel_id: str | None) -> tuple[str, str | None]:
    """Read a handle (or YouTube channel id) from a profile URL or a channel id passed as --handle."""
    value = handle.strip()
    if platform == "youtube" and not channel_id and CHANNEL_ID.fullmatch(value):
        cd.say(f"[drive] {value} is a YouTube channel id; using it as --channel-id")
        return "", value
    if "/" not in value and not any(domain in value.casefold() for domain in PROFILE_HOSTS.values()):
        return bare(value), channel_id
    from urllib.parse import urlparse
    parsed = urlparse(value if "://" in value else "https://" + value)
    host = (parsed.hostname or "").removeprefix("www.").removeprefix("m.")
    parts = [part for part in parsed.path.split("/") if part]
    owner = next((name for name, domain in PROFILE_HOSTS.items() if host == domain or host.endswith("." + domain)), None)
    if owner and owner != platform:
        raise Correction(f"--handle is a {owner} profile link but --platform is {platform}; "
                         "confirm the platform with the user")
    found = None
    if owner == "youtube" and parts:
        if CHANNEL_ID.fullmatch(parts[-1]) and parts[0] == "channel":
            cd.say(f"[drive] read channel id {parts[-1]} from the profile link")
            return "", parts[-1]
        found = parts[0] if parts[0].startswith("@") else (parts[1] if parts[0] in {"c", "user"} and len(parts) > 1 else None)
    elif owner == "tiktok":
        found = next((part for part in parts if part.startswith("@")), None)
    elif owner == "instagram" and parts and parts[0] not in {"p", "reel", "reels", "stories", "explore"}:
        found = parts[0]
    if not found:
        raise Correction(f"--handle takes the account handle, not {value!r}; "
                         "pass the @handle from the profile (YouTube also accepts --channel-id)")
    cd.say(f"[drive] read handle @{bare(found)} from the profile link")
    return bare(found), channel_id


def safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value) or "account"


def standard_input(wait: bool) -> str:
    stream = sys.stdin
    if stream is None or stream.isatty():
        return ""
    if not wait:
        try:
            import select
            ready, _, _ = select.select([stream], [], [], 0)
        except (OSError, ValueError, TypeError, io.UnsupportedOperation):
            ready = [stream]
        if not ready:
            return ""
    return stream.read().rstrip("\r\n")


def supplied_text(path: str | None, placed: bool, stored: str | None = None) -> str:
    if path == "-":
        value = standard_input(wait=not placed)
        if value.strip():
            return value
    elif path:
        return read_file("--question-file", path).rstrip("\r\n")
    if stored:
        return stored
    raise Correction("pass the user's original request with --question-file - and a heredoc on the same command")


def envelopes() -> list[dict[str, Any]]:
    return list(cd.state().get("envelopes") or [])


def take(requests: list[dict[str, Any]], handle: str | None) -> list[tuple[dict[str, Any], Path, dict[str, Any]]]:
    # persist_many.py uses the shared persist_envelope.py contract for every host-staged response.
    records = cd.LAST_RECORDS
    paired = records is not None and len(records) == len(requests)
    result = []
    for index, request in enumerate(requests):
        operation = str(request["operation"])
        holding = (Path.cwd() / f".incoming-{index}-{operation}.json").resolve()
        record = records[index][1] if paired else None
        if record is not None:
            holding.write_bytes(record.read_bytes())
            holding.chmod(0o600)
        else:
            identity = ["--handle", handle] if handle else []
            cd.script(
                "persist_many.py", "--connector", str(request["connector_id"]),
                "--operation", operation, "--any-status", "--dest", str(holding),
                *identity,
            )
        envelope = unwrap_envelope(holding.read_text(encoding="utf-8"))
        if envelope is None:
            raise cd.StageError(f"the {operation} response is not a connector Envelope")
        owner = account_of(envelope)
        if owner and handle and owner != bare(handle).lower():
            holding.unlink(missing_ok=True)
            raise cd.StageError(f"the {operation} response belongs to @{owner}, not @{bare(handle)}")
        result.append((request, holding, envelope))
    return result


def keep(holding: Path, operation: str) -> str:
    placed = envelopes()
    SNAPSHOT_DIR.mkdir(exist_ok=True)
    target = SNAPSHOT_DIR / f"envelope-{len(placed) + 1:02d}.json"
    holding.replace(target)
    cd.save_state(envelopes=[*placed, {"file": target.as_posix(), "operation": operation}])
    return target.as_posix()


def reject(holding: Path, operation: str, envelope: dict[str, Any]) -> None:
    directory = Path("rejected")
    directory.mkdir(exist_ok=True)
    failed = list(cd.state().get("failed") or [])
    holding.replace(directory / f"{operation}-{len(failed) + 1:02d}.json")
    cd.save_state(failed=[*failed, {"operation": operation, "status": envelope.get("status"),
                                    "error_code": envelope.get("error_code")}])


def require_usable(holding: Path, operation: str, envelope: dict[str, Any], label: str) -> None:
    if envelope.get("status") in USABLE_STATUSES:
        return
    reject(holding, operation, envelope)
    error_code = str(envelope.get("error_code") or "")
    code = f", error_code={error_code}" if error_code else ""
    raise cd.StageError(
        f"the public-data call {operation} failed for {label} (status={envelope.get('status')}{code}); "
        "no account conclusion was produced. " + failure_guidance(error_code)
    )


def failure_guidance(code: str) -> str:
    """What the failure means for the creator, so the reply names the right next step."""
    key = code.casefold()
    if any(word in key for word in ("rate", "limit", "quota", "unavailable", "timeout", "upstream", "server", "5xx")):
        return "This is a temporary provider problem, not a finding about the account; tell the user to try later."
    if any(word in key for word in ("not_found", "notfound", "private", "invalid", "validation", "identity")):
        return ("The account could not be read: confirm the exact handle with the user (not a display name); "
                "the account may be private or renamed.")
    return "Tell the user this public-data source failed; do not substitute another source."


def place_pages(requests: list[dict[str, Any]], handle: str | None) -> None:
    for request, holding, envelope in take(requests, handle):
        operation = str(request["operation"])
        require_usable(holding, operation, envelope, "@" + handle if handle else "the channel")
        keep(holding, operation)


def remember_identity(handle: str, account_id: str | None = None) -> None:
    cd.save_state(identity={"handle": handle, "account_id": account_id})


def resolve_instagram(handle: str, args: argparse.Namespace) -> None:
    params = {"handle": handle, "include_post_count": True}
    request = {"connector_id": IG, "operation": "resolve_profile", "params": params}

    def place() -> None:
        ((_, holding, envelope),) = take([request], handle)
        require_usable(holding, "resolve_profile", envelope, "@" + handle)
        profile = (envelope.get("data") or {}).get("profile") if isinstance(envelope.get("data"), dict) else None
        if not isinstance(profile, dict) or not (profile.get("username") or profile.get("id")):
            reject(holding, "resolve_profile", envelope)
            raise cd.StageError(f"@{handle} did not resolve to a public Instagram profile; confirm the exact "
                                "handle with the user (not a display name); the account may be private or renamed")
        keep(holding, "resolve_profile")
        account_id = profile.get("id") or profile.get("pk") or profile.get("user_id")
        remember_identity(str(profile.get("username") or handle), str(account_id) if account_id else None)

    cd.fetch_round([cd.run_call(IG, "resolve_profile", params)], args, place)


def resolve_youtube(handle: str | None, channel_id: str | None, args: argparse.Namespace) -> None:
    params = {"channel_id": channel_id} if channel_id else {"handle": "@" + bare(handle)}
    request = {"connector_id": YT, "operation": "get_channel", "params": params}

    def place() -> None:
        ((_, holding, envelope),) = take([request], None)
        require_usable(holding, "get_channel", envelope, channel_id or "@" + bare(handle))
        rows = (envelope.get("data") or {}).get("rows") if isinstance(envelope.get("data"), dict) else None
        channel = rows[0] if isinstance(rows, list) and rows and isinstance(rows[0], dict) else None
        if not isinstance(channel, dict) or not channel.get("id"):
            reject(holding, "get_channel", envelope)
            raise cd.StageError("the supplied YouTube identity did not resolve to a public channel; confirm the "
                                "exact @handle or channel id (UC…) with the user")
        keep(holding, "get_channel")
        remember_identity(str(channel.get("custom_url") or bare(handle) or channel["id"]), str(channel["id"]))

    cd.fetch_round([cd.run_call(YT, "get_channel", params)], args, place)


def fetch_history(platform: str, handle: str, content_kind: str, args: argparse.Namespace) -> None:
    if platform == "tiktok":
        params = {"handle": "@" + handle, "limit": PUBLIC_SAMPLE_CAP}
        request = {"connector_id": TT, "operation": "list_account_posts", "params": params}
        cd.fetch_round([cd.run_call(TT, "list_account_posts", params)], args,
                       lambda: place_pages([request], handle))
        remember_identity(handle)
        return
    if platform == "youtube":
        channel_id = str((cd.state().get("identity") or {}).get("account_id"))
        params = {"channel_id": channel_id, "max_results": PUBLIC_SAMPLE_CAP}
        request = {"connector_id": YT, "operation": "list_channel_uploads", "params": params}
        cd.fetch_round([cd.run_call(YT, "list_channel_uploads", params)], args,
                       lambda: place_pages([request], None))
        return
    user_id = (cd.state().get("identity") or {}).get("account_id")
    requests = []
    if content_kind in {"auto", "feed"}:
        requests.append({"connector_id": IG, "operation": "list_posts", "params": {"handle": handle}})
    if content_kind in {"auto", "reels"}:
        params = {"handle": handle}
        if user_id:
            params["user_id"] = user_id
        requests.append({"connector_id": IG, "operation": "list_reels", "params": params})
    cd.fetch_round(cd.batch_calls(requests), args, lambda: place_pages(requests, handle))


def focus_handle(requested: str) -> str:
    identity = cd.state().get("identity") or {}
    creators = [row for row in cd.load(SNAPSHOT).get("creators") or []
                if isinstance(row, dict) and row.get("handle")]
    account_id = identity.get("account_id")
    for creator in creators:
        if account_id and str(creator.get("creator_id")) == str(account_id):
            return str(creator["handle"])
    names = {bare(identity.get("handle")).lower(), bare(requested).lower()} - {""}
    for creator in creators:
        if bare(str(creator["handle"])).lower() in names:
            return str(creator["handle"])
    if len(creators) == 1:
        return str(creators[0]["handle"])
    return bare(identity.get("handle") or requested)


def assemble(platform: str, requested: str, content_kind: str) -> None:
    content_operations = {
        "tiktok": {"list_account_posts"},
        "youtube": {"list_channel_uploads"},
        "instagram": {"list_posts", "list_reels"},
    }[platform]
    content = [entry for entry in envelopes() if entry["operation"] in content_operations]
    rows = sum(row_count(cd.load(entry["file"])) or 0 for entry in content)
    if not rows:
        raise cd.StageError("the account returned no usable public content rows")
    envelope_args = [arg for entry in envelopes() for arg in ("--envelope", entry["file"])]
    cd.script(
        "build_public_snapshot.py", "--platform", platform, "--window", "auto",
        *envelope_args, "--coverage-incomplete", "--output", str(RAW_SNAPSHOT),
    )
    cap_args = [str(RAW_SNAPSHOT), str(SNAPSHOT), "--limit", str(PUBLIC_SAMPLE_CAP)]
    if platform == "instagram" and content_kind == "auto":
        cap_args += ["--per-format", str(INSTAGRAM_FORMAT_CAP)]
    cd.script("cap_public_snapshot.py", *cap_args)
    focus = focus_handle(requested)
    cd.script(
        "account_analyzers.py", str(SNAPSHOT), "--operation", "all",
        "--focus-handle", focus, "--output", str(ANALYSIS),
    )
    if cd.state().get("analysis_plan"):
        return
    cd.script("summarize_account_analysis.py", str(ANALYSIS), "--metric", "views", show=True)
    cd.say(
        f"[drive] public evidence bounded to {PUBLIC_SAMPLE_CAP} items"
        + (f" ({INSTAGRAM_FORMAT_CAP} per format in auto mode)" if platform == "instagram" and content_kind == "auto" else "")
        + "; select only relevant analysis modules in one traffic-workflow-brief.v1 object, then run: "
        + analyze_command()
    )
    cd.say("Submit questions with module kind, intent, metric and selected groups. "
           "Platform, account, source and original request are inherited; omit user_request. "
           "Group select accepts format, start/stop and published_from/published_before; "
           "bounds apply oldest first after filters. Read traffic-brief.md for other measurement shapes.")


def analyze_command() -> str:
    """The next stage for fetched public data, runnable from any directory."""
    return cd.driver_command(Path.cwd().resolve(), "analyze") + " (plan JSON on stdin)"


def stage_fetch(args: argparse.Namespace) -> None:
    for name in ("build_public_snapshot.py", "account_analyzers.py"):
        if not (cd.SCRIPTS / name).is_file():
            raise cd.StageError(f"This skill package is incomplete: scripts/{name} is missing. "
                                "Reload a complete verified package before fetching account content.")
    platform = args.platform
    channel_id = str(args.channel_id or "").strip() or None
    handle, channel_id = account_identity(platform, str(args.handle or ""), channel_id)
    if not handle and not (platform == "youtube" and channel_id):
        raise Correction("--handle is required (YouTube also accepts --channel-id)")
    if channel_id and platform != "youtube":
        raise Correction("--channel-id is YouTube only; pass --handle")
    if platform != "instagram" and args.content_kind != "auto":
        raise Correction("--content-kind feed/reels is Instagram only; omit it")
    previous = cd.state() if args.placed else {}
    plan = previous.get("analysis_plan")
    language = previous.get("analysis_language", getattr(args, "language", "zh-CN"))
    if getattr(args, "brief_file", None) and not args.placed:
        try:
            plan = read_brief(args.brief_file)
        except ValueError as error:
            raise Correction(f"--brief-file: {error}",
                             next_step="Nothing was fetched or written. Correct the plan and run the same fetch "
                                       "again in this run.") from None
        if not (isinstance(plan.get("user_request"), str) and plan["user_request"].strip()):
            raise Correction("the plan given with --brief-file has no user_request; add the creator's original "
                             "request verbatim as \"user_request\" in the plan")
    stored = previous.get("question")
    question = plan.get("user_request") if plan and not args.placed else None
    if question is None:
        question = supplied_text(args.question_file, args.placed, stored)
    if not isinstance(question, str) or not question.strip():
        raise Correction("fetch requires the original user_request")
    cd.reserve_run_directory(args.placed, stage="fetch", args=args, extra=question)
    requested = (channel_id or handle).lower()
    current = cd.state()
    if current.get("requested") and current.get("requested") != requested:
        raise Correction(f"this run belongs to {current.get('requested')}, not {requested}; rerun with the same "
                         "--handle/--channel-id as the first fetch, plus --placed")
    cd.save_state(platform=platform, requested=requested, question=question,
                  content_kind=args.content_kind, analysis_plan=plan, analysis_language=language)
    if platform == "instagram":
        resolve_instagram(handle, args)
    elif platform == "youtube":
        resolve_youtube(handle or None, channel_id, args)
    else:
        remember_identity(handle)
    fetch_history(platform, handle, args.content_kind, args)
    assemble(platform, channel_id or handle, args.content_kind)
    if plan:
        execute_analysis(plan, language)


def read_brief(path: str) -> dict:
    raw = standard_input(True) if path == "-" else read_file("--brief-file", path)
    if not raw.strip():
        raise ValueError("received no plan; send the plan JSON as a heredoc on this command")
    value = json.loads(strip_fence(raw))
    if not isinstance(value, dict) or not isinstance(value.get("questions"), list) or not value["questions"]:
        raise ValueError("send one JSON object with a non-empty questions array")
    value["schema_version"] = "traffic-workflow-brief.v1"
    return value


def strip_fence(raw: str) -> str:
    """A Markdown code fence around the JSON is formatting, not content."""
    match = re.fullmatch(r"\s*```[A-Za-z]*\s*\n(.*?)\n\s*```\s*", raw, flags=re.S)
    return match.group(1) if match else raw


def plan_correction(issues: list[str]) -> None:
    cd.say(json.dumps({"status": "needs_correction", "issues": issues}, ensure_ascii=False))
    cd.say("Nothing was calculated. Submit the corrected plan with: " + analyze_command())


def stage_analyze(args: argparse.Namespace) -> None:
    if not ANALYSIS.is_file():
        raise Correction("fetch has not finished in this run (no account_analysis.json). If fetch handed connector "
                         "calls over, make them and rerun that same fetch with --placed; then submit the plan to "
                         "analyze.")
    try:
        value = read_brief(args.brief_file)
    except ValueError as error:
        plan_correction([str(error)])
        return
    execute_analysis(value, args.language or cd.state().get("analysis_language", "zh-CN"))


def execute_analysis(value: dict, language: str) -> None:
    value = dict(value)
    state = cd.state()
    # Platform and account come from this run's fetch; a restated copy in the plan is replaced.
    value["platform"] = state["platform"]
    value["account"] = cd.load(ANALYSIS)["scope"]["account"]
    value.setdefault("source", {"kind": "account_analysis", "locator": str(ANALYSIS)})
    question = state.get("question")
    if not isinstance(question, str) or not question.strip():
        raise cd.StageError("the run has no saved original request; run fetch with --question-file")
    QUESTION.write_text(question, encoding="utf-8")
    BRIEF_INPUT.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    output = cd.script(
        "run_traffic_workflow.py", "prepare", str(BRIEF_INPUT), "--analysis", str(ANALYSIS),
        "--output-dir", ".", "--language", language,
        "--question-file", str(QUESTION),
    )
    if '"needs_correction"' in output:
        # Rerunning fetch would reserve a new run; the corrected plan goes to analyze in this one.
        plan_correction(json.loads(output.splitlines()[0])["issues"])
        return
    cd.say(output.rstrip())


def finalize(args: argparse.Namespace, *mode: str) -> None:
    """Hand the written analysis to the workflow: `check` lists issues, `report` writes the report."""
    required = (NORMALIZED_BRIEF, CASE, ASSESSMENT, Path("traffic_run.json"))
    missing = [path.name for path in required if not path.is_file()]
    if missing:
        where = ("analyze has not produced evidence in this run yet: if it returned needs_correction, submit the "
                 "corrected plan to analyze first" if ANALYSIS.is_file() else
                 "this is not a run directory with prepared evidence: run this stage from the run directory where "
                 "analyze (or prepare) printed the evidence; do not fetch again")
        raise Correction(f"no prepared evidence in {Path.cwd()} ({', '.join(missing)} missing); {where}")
    state = cd.load("traffic_run.json")
    source = []
    raw = None
    if args.synthesis_file is not None:
        raw = standard_input(True) if args.synthesis_file == "-" else read_file("--synthesis-file", args.synthesis_file)
    elif "--check" not in mode:
        # An analysis sent to report without the flag is still the one the model means to deliver.
        raw = standard_input(False) or None
        if raw is not None:
            cd.say("[drive] report uses the analysis sent on stdin")
    if raw is not None:
        if not raw.strip():
            raise Correction("received no analysis on stdin; send the analysis JSON as a heredoc on this command")
        # Passed through unparsed: the workflow reads it leniently and names any issue.
        SYNTHESIS_INPUT.write_text(strip_fence(raw), encoding="utf-8")
        source = [str(SYNTHESIS_INPUT)]
    output = cd.script(
        "run_traffic_workflow.py", "finalize", *source,
        "--brief", str(NORMALIZED_BRIEF), "--case", str(CASE),
        "--assessment", str(ASSESSMENT),
        *(["--analysis", "traffic_source_analysis.json"] if state["has_analysis"] else []),
        "--output-dir", ".", "--language", args.language or state["language"],
        *mode,
    )
    cd.say(output.rstrip())


def stage_check(args: argparse.Namespace) -> None:
    finalize(args, "--check")


def stage_report(args: argparse.Namespace) -> None:
    finalize(args)


STAGES = {"fetch": stage_fetch, "analyze": stage_analyze, "check": stage_check, "report": stage_report}


def main() -> int:
    parser = Parser(description=__doc__)
    sub = parser.add_subparsers(dest="stage", required=True)
    fetch = sub.add_parser("fetch")
    fetch.add_argument("--platform", type=PLATFORM, metavar="{instagram,youtube,tiktok}", required=True)
    fetch.add_argument("--handle")
    fetch.add_argument("--channel-id")
    fetch.add_argument("--content-kind", type=CONTENT_KIND, metavar="{auto,feed,reels}", default="auto")
    fetch.add_argument("--question-file", default="-")
    fetch.add_argument("--brief-file", help="Optional preselected analysis plan; includes user_request")
    fetch.add_argument("--language", type=language, metavar="{en,zh-CN}", default="zh-CN")
    analyze = sub.add_parser("analyze")
    analyze.add_argument("--brief-file", default="-")
    analyze.add_argument("--language", type=language, metavar="{en,zh-CN}", help="Defaults to the fetch language")
    check = sub.add_parser("check", help="List issues in the written analysis without writing the report")
    check.add_argument("--synthesis-file", default="-")
    report = sub.add_parser("report", help="Write the report from the last checked analysis, or a new one")
    report.add_argument("--synthesis-file")
    for command in (check, report):
        command.add_argument("--language", type=language, metavar="{en,zh-CN}",
                             help="Defaults to the prepared run language")
    for command in (fetch, analyze, check, report):
        command.add_argument("--placed", action="store_true")
    args = parser.parse_args()
    try:
        return cd.run_stage(
            STAGES, args, first=("fetch", "check", "report"),
            model_files=(str(BRIEF_INPUT), str(SYNTHESIS_INPUT)),
        )
    except Correction as correction:
        # Raised before the stage fetched or wrote anything, so the same step can run again in this run.
        return correction.emit()


if __name__ == "__main__":
    raise SystemExit(main())
