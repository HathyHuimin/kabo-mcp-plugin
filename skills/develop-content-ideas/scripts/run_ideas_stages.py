#!/usr/bin/env python3
"""Deterministic prep/core stage runner for develop-content-ideas.

Merges build → request validation → decision (prep) and idea-set validation →
optional one-shot revision brief → render (core) so the host model does not
re-decide each fixed step. Core generation itself remains model-authored; this
script records generation attempts and refuses a third creative pass.

Exit codes for `core`:
  0  status=ok reports written, or status=needs_revision with one revision open
  3  generation budget exhausted or already delivered; not_met recorded
  1  hard failure (missing files, I/O, unexpected state)

`prep` permits one correction of read authored inputs before generation; another
invalid input exits 3. An unavailable --intent path is an invocation failure
(exit 1) and does not consume or reset that input-validation budget. A successful prep or a started core cannot be reset.

Standard library only.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_idea_request import RequestInputError, build, load as load_json  # noqa: E402
from render_idea_report import concise, full, validate_concise  # noqa: E402
from validate_ideas import bound_conversation_context, generation_decision, validate_authored_intent, validate_idea_set, validate_request  # noqa: E402

STAGE_SCHEMA = "ideas-stage-timing.v1"
MAX_GENERATION_ATTEMPTS = 2
ARTIFACT_FLAGS = (
    "creator-profile", "account-review", "account-context", "trend-report", "trend-context",
    "competitor-report", "competitor-context", "video-analysis", "comment-context", "previous-ideas", "conversation-context",
)


def validation_issue(message):
    """Turn validator errors into bounded corrections while preserving source requirements."""
    if "direct_topic" in message:
        return RequestInputError(message, code="invalid_direct_topic", field="generation_basis.direct_topic",
            next_action="Copy a literal substantive topic from user_brief.text. For a follow-up, keep direct_topic null and bind the previous IdeaSet with --previous-ideas or exact earlier visible messages with --conversation-context, plus a verbatim continuation_quote. Never rewrite the user's brief to make a topic pass.").issue
    if "report_language" in message or "language variant" in message or "language extension" in message:
        return RequestInputError(message,
            code="invalid_report_language",
            field="report_language", next_action="Correct the malformed tag while preserving the user's requested language; never silently substitute another language.").issue
    return RequestInputError(message, code="invalid_request", field=message.split(":", 1)[0] if message.startswith("request.") else "request").issue


def utc_now() -> str:
    """Second-resolution UTC timestamp for audit fields."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def utc_now_precise() -> str:
    """UTC timestamp retaining microseconds for stage duration_ms."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_utc(value: str) -> datetime:
    """Parse an ISO-8601 UTC timestamp written by this runner."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def duration_ms_between(started_at: str | None, ended_at: str | None) -> int | None:
    """Return wall duration in milliseconds between two ISO timestamps."""
    if not started_at or not ended_at:
        return None
    return int((parse_utc(ended_at) - parse_utc(started_at)).total_seconds() * 1000)


def write_json(path: Path, value: dict[str, Any]) -> None:
    """Write a JSON object with trailing newline."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_timing(path: Path) -> dict[str, Any]:
    """Load stage_timing.json or return an empty timing envelope."""
    if not path.exists():
        return {
            "schema_version": STAGE_SCHEMA,
            "prep": None,
            "core": None,
            "network_retries": [],
        }
    value = load_json(path)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must hold a JSON object")
    return value


def empty_core() -> dict[str, Any]:
    """Fresh core timing block for a new prep run."""
    return {
        "started_at": None,
        "ended_at": None,
        "duration_ms": None,
        "status": "pending",
        "generation_attempts": 0,
        "within_two_calls": None,
        "not_met_reason": None,
        "validation_errors": [],
        "rendered": False,
    }


def unlink_if_exists(path: Path) -> None:
    """Remove a path if present; ignore missing files."""
    try:
        path.unlink()
    except FileNotFoundError:
        return


def non_json_artifacts(args: argparse.Namespace) -> list[dict[str, Any]]:
    """List artifact flags whose existing file is not a JSON document (e.g. a Markdown report)."""
    rows = []
    for name in ARTIFACT_FLAGS:
        path = getattr(args, name.replace("-", "_"))
        if path is None or not path.is_file():
            continue
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            row: dict[str, Any] = {"flag": f"--{name}", "path": str(path)}
            siblings = sorted(p.name for p in path.parent.glob("*.json"))
            if siblings:
                row["json_files_in_same_directory"] = siblings[:10]
            rows.append(row)
    return rows


def missing_artifacts(args: argparse.Namespace) -> list[dict[str, Any]]:
    """Identify unavailable authored/upstream files before build performs any writes."""
    rows = []
    for name in ("intent", *ARTIFACT_FLAGS):
        path = getattr(args, name.replace("-", "_"))
        if path is not None and not path.is_file():
            if name == "conversation-context":
                rows.append(RequestInputError(
                    f"{path} is not an available file", code="missing_conversation_context", field="conversation_context",
                    next_action="Restore the exact earlier visible messages and referenced text from this conversation. Do not fabricate history or ask the user to repeat text that is already available.").issue)
                continue
            if name == "previous-ideas":
                # Keep the builder's specific error, including when another
                # independently invalid field would otherwise prevent build.
                rows.append(RequestInputError(
                    f"{path} is not an available file", code="missing_previous_ideas", field="continuation_context",
                    next_action="Restore the original previous idea_set.json from this conversation's retained file or saved result, then rerun prep. Do not recreate it from memory.").issue)
                continue
            rows.append(RequestInputError(
                f"{path} is not an available file", code="missing_artifact", field=f"--{name}",
                next_action="Restore the original input JSON from this conversation's saved result or file, then rerun prep in this run. Do not fabricate evidence or recreate an upstream artifact from memory.").issue)
    return rows


def unavailable_intent(intent_path: Path, attempts: int, detail: str) -> int:
    """报告尚未读取的调用输入，不消耗或重置业务校验预算。"""
    issue = RequestInputError(
        f"{intent_path} cannot be read: {detail}", code="unavailable_intent_path", field="--intent",
        next_action="Correct the --intent path or file permissions and rerun prep in the same run directory. Do not change the user request, replace evidence or reset this run.").issue
    print(json.dumps({"status": "invocation_error", "stage": "prep", "issues": [issue],
                      "input_attempts": attempts, "correction_remaining": 2 - attempts,
                      "next_action": issue["next_action"]}, ensure_ascii=False))
    return 1


def run_prep(args: argparse.Namespace) -> int:
    """Build request, validate, decide, and open core timing when ready."""
    run_dir = args.run_dir.resolve()
    timing_path = run_dir / "stage_timing.json"
    started = time.monotonic()
    started_at = utc_now_precise()
    intent_path = args.intent.resolve()
    request_path = (run_dir / "request.json").resolve()
    decision_path = (run_dir / "decision.json").resolve()
    state_checked = False
    publishing_request = False
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
        timing = read_timing(timing_path)
        previous = timing.get("prep") or {}
        if not isinstance(previous, dict):
            raise ValueError("invalid prep state; refusing to reset the correction budget")
        # Correction is legal only before a successful prep. Never erase an
        # existing decision, draft, delivery or core generation counter.
        if timing.get("core") is not None or previous.get("status") not in {None, "needs_correction"}:
            print(json.dumps({"status": "not_met", "stage": "prep", "reason": "prep_restart_forbidden",
                              "next_action": "Continue from the existing decision/core state; do not reset this run."}))
            return 3
        attempts = previous.get("input_attempts", 0)
        if type(attempts) is not int or attempts not in {0, 1}:
            raise ValueError("invalid prep input_attempts; refusing to reset the correction budget")
        if any((run_dir / name).exists() for name in ("request.json", "decision.json", "idea_set.json", "RESULT.md", "IDEA_REPORT_FULL.md")):
            raise ValueError("existing generation artifacts without an open prep; refusing to overwrite them")
        state_checked = True
        # 尚未读取业务输入：调用路径错误不算业务校验失败。保留计数和终态，直接返回。
        if not intent_path.is_file():
            return unavailable_intent(intent_path, attempts, "not an available file")
        attempts += 1
        issues = missing_artifacts(args)
        not_json = non_json_artifacts(args)
        if not_json:
            issues.extend(RequestInputError(f"{row['path']} is not JSON", code="not_json", field=row["flag"],
                next_action=("Wrap the exact earlier visible messages in the conversation-source JSON contract; do not invent a native IdeaSet."
                             if row["flag"] == "--conversation-context" else
                             "Use the original upstream JSON artifact, not its Markdown report. Do not hand-write a replacement.")).issue
                for row in not_json)
        files_available = not issues
        # 即使独立上游文件缺失，也要合报可读聊天的引用错误，避免恢复文件后才耗尽修订预算。
        if (args.conversation_context is not None and args.conversation_context.is_file()
                and not any(row["flag"] == "--conversation-context" for row in not_json)):
            try:
                bound_conversation_context(args.conversation_context, run_dir)
            except (OSError, ValueError) as error:
                issues.append(RequestInputError(str(error), field="conversation_context").issue)
        intent = None
        try:
            intent = load_json(intent_path)
        except OSError as error:
            return unavailable_intent(intent_path, attempts - 1, str(error))
        except RequestInputError as error:
            issues.append(error.issue)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            issues.append(RequestInputError(str(error), code="invalid_json").issue)
        else:
            # schema 或后台读取错误不归入调用方输入文件的异常处理。
            try:
                issues.extend(validation_issue(error) for error in dict.fromkeys(
                    validate_authored_intent(intent, has_previous_ideas=args.previous_ideas is not None,
                                             has_conversation_context=args.conversation_context is not None)))
            except RequestInputError as error:
                issues.append(error.issue)
        if files_available and intent is not None:
            try:
                request = build(
                    intent, request_path,
                    creator_profile=args.creator_profile, account_review=args.account_review,
                    account_context=args.account_context, trend_report=args.trend_report,
                    trend_context=args.trend_context, competitor_report=args.competitor_report,
                    competitor_context=args.competitor_context, video_analysis=args.video_analysis,
                    comment_context=args.comment_context, previous_ideas=args.previous_ideas,
                    conversation_context=args.conversation_context,
                )
                issues.extend(validation_issue(error) for error in dict.fromkeys(validate_request(request, request_path)))
            except RequestInputError as error:
                issues.append(error.issue)
            except (json.JSONDecodeError, UnicodeDecodeError) as error:
                issues.append(RequestInputError(str(error), code="invalid_json").issue)
        issues = list({(issue["code"], issue["field"], issue["message"]): issue for issue in issues}.values())
        if issues:
            status = "needs_correction" if attempts == 1 else "not_met"
            ended_at = utc_now_precise()
            timing["prep"] = {"started_at": started_at, "ended_at": ended_at,
                              "duration_ms": duration_ms_between(started_at, ended_at),
                              "status": status, "input_attempts": attempts, "issues": issues}
            timing["schema_version"] = STAGE_SCHEMA
            timing["core"] = None
            write_json(timing_path, timing)
            print(json.dumps({"status": status, "stage": "prep", "issues": issues,
                **({"issue": {"not_json": not_json}} if not_json else {}),
                "correction_remaining": 2 - attempts,
                "next_action": "Correct the listed inputs once, then rerun prep in the same run directory. Do not generate or deliver an unvalidated substitute."
                               if attempts == 1 else "Input correction was not completed. Report the unresolved limitation; do not reset prep or deliver an unvalidated substitute."}, ensure_ascii=False))
            return 0 if attempts == 1 else 3
        publishing_request = True
        write_json(request_path, request)
        decision = generation_decision(request)
        write_json(decision_path, decision)
    except (OSError, ValueError, json.JSONDecodeError, KeyError, TypeError) as error:
        if publishing_request:
            unlink_if_exists(request_path)
            unlink_if_exists(decision_path)
        ended_at = utc_now_precise()
        try:
            if state_checked:
                write_json(timing_path, {
                    "schema_version": STAGE_SCHEMA,
                    "prep": {"started_at": started_at, "ended_at": ended_at,
                              "duration_ms": duration_ms_between(started_at, ended_at),
                              "status": "failed", "error": f"{type(error).__name__}: {error}"},
                    "core": None, "network_retries": timing.get("network_retries", []),
                })
        except OSError:
            pass
        payload = {
            "status": "failed",
            "stage": "prep",
            "error": f"{type(error).__name__}: {error}",
        }
        print(json.dumps(payload, ensure_ascii=False))
        return 1

    ended_at = utc_now_precise()
    duration_ms = int((time.monotonic() - started) * 1000)
    timing["schema_version"] = STAGE_SCHEMA
    timing["prep"] = {
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_ms": duration_ms,
        "status": decision.get("status"),
        "input_attempts": attempts,
        "request_path": "request.json",
        "decision_path": "decision.json",
    }
    # A new core is opened only once; all prep restarts were rejected above.
    core = empty_core()
    if decision.get("status") in {"ready_for_generation", "blocked_missing_required_source", "needs_selection_context"}:
        # Core wall clock starts when generation is authorized, before the host writes idea_set.json.
        core["started_at"] = utc_now_precise()
        core["status"] = "awaiting_generation" if decision.get("status") == "ready_for_generation" else "awaiting_terminal"
    timing["core"] = core
    write_json(timing_path, timing)

    print(json.dumps({
        "status": "ok",
        "stage": "prep",
        "decision": decision,
        "request": "request.json",
        "decision_file": "decision.json",
        "stage_timing": "stage_timing.json",
        "duration_ms": duration_ms,
    }, ensure_ascii=False, sort_keys=True))
    return 0


def collect_validation_errors(idea_set: dict[str, Any], request: dict[str, Any], request_path: Path) -> list[str]:
    """Deduplicate idea-set validation errors for revision briefs."""
    return list(dict.fromkeys(validate_idea_set(idea_set, request, request_path)))


def finish_core(core: dict[str, Any], *, status: str, not_met_reason: str | None = None,
                within_two_calls: bool | None = None, rendered: bool = False,
                validation_errors: list[str] | None = None) -> dict[str, Any]:
    """Close the core timing block with a terminal status."""
    core["status"] = status
    core["ended_at"] = utc_now_precise()
    core["duration_ms"] = duration_ms_between(core.get("started_at"), core["ended_at"])
    if within_two_calls is not None:
        core["within_two_calls"] = within_two_calls
    core["not_met_reason"] = not_met_reason
    core["rendered"] = rendered
    if validation_errors is not None:
        core["validation_errors"] = validation_errors
    return core


def run_core(args: argparse.Namespace) -> int:
    """Validate idea_set, allow one revision, then render or mark not_met."""
    run_dir = args.run_dir.resolve()
    timing_path = run_dir / "stage_timing.json"
    request_path = (run_dir / "request.json").resolve()
    idea_set_path = (run_dir / (args.idea_set or "idea_set.json")).resolve()
    decision_path = run_dir / "decision.json"
    revision_path = run_dir / "revision_brief.json"
    concise_path = run_dir / "RESULT.md"
    full_path = run_dir / "IDEA_REPORT_FULL.md"

    try:
        timing = read_timing(timing_path)
        core = timing.get("core") or empty_core()

        if args.record_network_retry:
            retries = list(timing.get("network_retries") or [])
            retries.append({"recorded_at": utc_now_precise(), "note": args.record_network_retry})
            timing["network_retries"] = retries
            write_json(timing_path, timing)
            print(json.dumps({
                "status": "ok",
                "stage": "core",
                "recorded": "network_retry",
                "network_retries": len(retries),
            }, ensure_ascii=False, sort_keys=True))
            return 0

        if not request_path.exists():
            raise ValueError("request.json missing; run prep first")
        prep = timing.get("prep") or {}
        decision = load_json(decision_path) if decision_path.exists() else {}
        allowed_statuses = {"ready_for_generation", "blocked_missing_required_source", "needs_selection_context"}
        if prep.get("status") not in allowed_statuses or decision.get("status") not in allowed_statuses:
            raise ValueError("core requires prep and decision status ready_for_generation or a terminal selection gate")
        if not idea_set_path.exists():
            raise ValueError(f"{idea_set_path.name} missing; write the IdeaSet before core")
        request = load_json(request_path)
        idea_set = load_json(idea_set_path)
        request_errors = validate_request(request, request_path)
        if request_errors:
            raise ValueError("core request failed validation: " + "; ".join(dict.fromkeys(request_errors)))
        if core.get("rendered"):
            print(json.dumps({
                "status": "not_met",
                "stage": "core",
                "reason": "already_rendered",
                "generation_attempts": core.get("generation_attempts", 0),
                "within_two_calls": core.get("within_two_calls"),
            }, ensure_ascii=False, sort_keys=True))
            return 3

        terminal_gate = decision.get("status") in {"blocked_missing_required_source", "needs_selection_context"}
        if terminal_gate:
            errors = collect_validation_errors(idea_set, request, request_path)
            if errors:
                raise ValueError("terminal IdeaSet failed validation: " + "; ".join(errors))
            concise_text = concise(idea_set, request)
            validate_concise(concise_text, idea_set, request)
            full_text = full(idea_set, request)
            concise_path.write_text(concise_text, encoding="utf-8")
            full_path.write_text(full_text, encoding="utf-8")
            finish_core(core, status="delivered_terminal", within_two_calls=True, rendered=True, validation_errors=[])
            timing["core"] = core
            write_json(timing_path, timing)
            print(json.dumps({"status": "ok", "stage": "core", "generation_attempts": 0,
                              "within_two_calls": True, "concise": "RESULT.md", "full": "IDEA_REPORT_FULL.md",
                              "duration_ms": core["duration_ms"]}, ensure_ascii=False, sort_keys=True))
            return 0

        if core.get("started_at") is None:
            # Fallback for hosts that skip a ready_for_generation prep open.
            core["started_at"] = utc_now_precise()
        attempts = int(core.get("generation_attempts") or 0) + 1
        core["generation_attempts"] = attempts
        timing["core"] = core
        write_json(timing_path, timing)

        if attempts > MAX_GENERATION_ATTEMPTS:
            finish_core(
                core,
                status="not_met",
                not_met_reason="exceeded_two_generation_attempts",
                within_two_calls=False,
            )
            timing["core"] = core
            write_json(timing_path, timing)
            print(json.dumps({
                "status": "not_met",
                "stage": "core",
                "reason": "exceeded_two_generation_attempts",
                "generation_attempts": attempts,
                "within_two_calls": False,
            }, ensure_ascii=False, sort_keys=True))
            return 3

        errors = collect_validation_errors(idea_set, request, request_path)
        if errors:
            brief = {
                "schema_version": "ideas-revision-brief.v1",
                "attempt": attempts,
                "max_attempts": MAX_GENERATION_ATTEMPTS,
                "revision_allowed": attempts < MAX_GENERATION_ATTEMPTS,
                "errors": errors,
                "instruction": (
                    "Revise idea_set.json in place to clear every listed error. "
                    "Preserve user language, requested counts, evidence conditions, "
                    "and data_gaps. Do not invent evidence. After revising, run core again."
                ),
            }
            if decision_path.exists():
                brief["decision"] = load_json(decision_path)
            write_json(revision_path, brief)
            if attempts >= MAX_GENERATION_ATTEMPTS:
                finish_core(
                    core,
                    status="not_met",
                    not_met_reason="validation_failed_after_two_attempts",
                    within_two_calls=True,
                    validation_errors=errors,
                )
                timing["core"] = core
                write_json(timing_path, timing)
                print(json.dumps({
                    "status": "not_met",
                    "stage": "core",
                    "reason": "validation_failed_after_two_attempts",
                    "generation_attempts": attempts,
                    "within_two_calls": True,
                    "errors": errors,
                    "revision_brief": "revision_brief.json",
                }, ensure_ascii=False, sort_keys=True))
                return 3
            core["status"] = "needs_revision"
            core["validation_errors"] = errors
            core["within_two_calls"] = True
            timing["core"] = core
            write_json(timing_path, timing)
            print(json.dumps({
                "status": "needs_revision",
                "stage": "core",
                "generation_attempts": attempts,
                "revision_remaining": MAX_GENERATION_ATTEMPTS - attempts,
                # A non-final wrapper must stay open for the one permitted
                # authored revision. Exit 0 is not delivery: status remains
                # needs_revision and neither report is rendered.
                "next_action": (
                    "Revise idea_set.json once using the listed errors, then run core "
                    "again in the same run directory. Do not restart prep or reset "
                    "the attempt counter. Deliver RESULT.md only after core succeeds; "
                    "if execution cannot continue, report the draft as unvalidated."
                ),
                "errors": errors,
                "revision_brief": "revision_brief.json",
            }, ensure_ascii=False, sort_keys=True))
            return 0

        concise_text = concise(idea_set, request)
        validate_concise(concise_text, idea_set, request)
        full_text = full(idea_set, request)
        concise_path.write_text(concise_text, encoding="utf-8")
        full_path.write_text(full_text, encoding="utf-8")
        if revision_path.exists():
            revision_path.unlink()

        finish_core(
            core,
            status="delivered",
            within_two_calls=True,
            rendered=True,
            validation_errors=[],
        )
        timing["core"] = core
        write_json(timing_path, timing)

        print(json.dumps({
            "status": "ok",
            "stage": "core",
            "generation_attempts": attempts,
            "within_two_calls": True,
            "concise": "RESULT.md",
            "full": "IDEA_REPORT_FULL.md",
            "duration_ms": core["duration_ms"],
        }, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError, json.JSONDecodeError, KeyError, TypeError) as error:
        try:
            if "core" not in locals() or not isinstance(core, dict):
                core = empty_core()
            if not core.get("started_at"):
                core["started_at"] = utc_now_precise()
            finish_core(core, status="failed", not_met_reason=f"{type(error).__name__}: {error}")
            timing = locals().get("timing") or read_timing(timing_path)
            timing["core"] = core
            write_json(timing_path, timing)
        except OSError:
            pass
        print(json.dumps({
            "status": "failed",
            "stage": "core",
            "error": f"{type(error).__name__}: {error}",
        }, ensure_ascii=False))
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    prep = commands.add_parser("prep", help="build request, validate, write decision and prep timing")
    prep.add_argument("--intent", type=Path, required=True)
    prep.add_argument("--run-dir", type=Path, required=True)
    for name in ARTIFACT_FLAGS:
        prep.add_argument(f"--{name}", type=Path)

    core = commands.add_parser("core", help="validate idea-set, revise once if needed, then render")
    core.add_argument("--run-dir", type=Path, required=True)
    core.add_argument("--idea-set", default="idea_set.json",
                      help="idea-set filename inside run-dir (default idea_set.json)")
    core.add_argument(
        "--record-network-retry",
        metavar="NOTE",
        help="append a network/host retry note without consuming a generation attempt",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "prep":
        return run_prep(args)
    return run_core(args)


if __name__ == "__main__":
    raise SystemExit(main())
