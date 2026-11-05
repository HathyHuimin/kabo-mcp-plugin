#!/usr/bin/env python3
"""Build the idea request's upstream_sources and idea_context from the upstream artifacts.

`upstream_sources` is lineage - each artifact's path, id, schema version and sha256 - and
`idea_context` is the deterministic projection every upstream Skill already writes
(project_idea_context.py). The model used to copy both into request.json by hand: 64-character
hashes, report ids and whole pattern lists retyped from files it had read. The validator checks the
lineage, but nothing compared the context to the projection it claims to be.

Here the model writes only the intent - task, counts, goal, audience, brief, generation_basis,
constraints, exclusions - and this script adds everything that comes from a file:

  python3 scripts/build_idea_request.py --intent intent.json --output request.json \\
    [--creator-profile creator_profile.json] \\
    [--account-review account_review.json [--account-context account_idea_context.json]] \\
    [--trend-report trend-report.json --trend-context trend_idea_context.json] \\
    [--competitor-report competitor-report.json --competitor-context competitor_idea_context.json] \\
    [--video-analysis video_analysis.json] [--comment-context comments.json]

Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
# Sibling imports must not leave a __pycache__ in the signed, checksummed skill directory.
sys.dont_write_bytecode = True
from project_account_context import project as project_account_review
from project_comments import validate_projection
from validate_ideas import DERIVED_FIELDS, REQUEST_SCHEMA, V3_SOURCE_VERSIONS, bound_conversation_context, derive_source_mode, file_sha256, previous_ideas_context  # noqa: E402

CREATOR_CONTEXT_FIELDS = ("schema_version", "profile_id", "accounts", "markets", "audience", "positioning",
                          "content_profile", "key_missing_info", "extensions")
ID_FIELDS = {"creator_profile": "profile_id", "account_review_report": "report_id", "trend_report": "report_id",
             "competitor_report": "report_id", "video_analysis": "analysis_id", "comment_context": "report_id"}


class RequestInputError(ValueError):
    def __init__(self, message, *, code="invalid_input", field="intent", next_action=None):
        """Carry an actionable input issue without consuming a generation attempt."""
        super().__init__(message)
        self.issue = {
            "code": code, "field": field, "message": message,
            "next_action": next_action or "Correct the named input from the original request or source artifact; do not fabricate evidence.",
        }


def load(path: Path) -> dict[str, Any]:
    """Read an original JSON object, rejecting scalar or array substitutes."""
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RequestInputError(f"{path} must hold a JSON object")
    return value


def source_ref(source_type: str, path: Path, artifact: dict[str, Any], base: Path, artifact_id: str | None = None) -> dict[str, Any]:
    """Bind source identity and checksum to the provided artifact's actual bytes."""
    version = V3_SOURCE_VERSIONS.get(source_type) or artifact.get("schema_version")
    identifier = artifact_id or artifact.get(ID_FIELDS.get(source_type, ""))
    if not isinstance(identifier, str) or not identifier:
        raise RequestInputError(f"{path} carries no {ID_FIELDS.get(source_type, 'id')} for its {source_type} lineage",
            code="missing_artifact_metadata", field=f"{source_type}.{ID_FIELDS.get(source_type, 'id')}",
            next_action="Restore the original upstream JSON, including its original identifier, from this conversation's saved result or file. A summary is not the artifact. Do not invent an identifier; if restoration is unavailable, report the missing evidence.")
    if not version:
        raise RequestInputError(f"{path} carries no schema_version", code="missing_artifact_metadata", field=f"{source_type}.schema_version")
    return {"source_type": source_type, "path": os.path.relpath(path.resolve(), base.resolve()),
            "artifact_id": identifier, "schema_version": version, "sha256": file_sha256(path)}


def projection(path: Path, key: str) -> Any:
    """Read the named upstream projection without reconstructing missing fields."""
    document = load(path)
    if key not in document:
        raise RequestInputError(f"{path} is not a project_idea_context.py output: it has no {key!r}")
    return document[key]


def bound(items: Any, report: dict[str, Any], path: Path, kind: str) -> Any:
    """A context is its report's patterns, field for field (project_idea_context.py). One projected
    from another run's report would pair this report's lineage with that report's content."""
    patterns = [pattern for pattern in report.get("patterns") or [] if isinstance(pattern, dict)]
    same = isinstance(items, list) and len(items) == len(patterns) and all(
        isinstance(item, dict) and all(pattern.get(key) == value for key, value in item.items() if key != "gaps")
        for item, pattern in zip(items, patterns))
    if not same:
        raise RequestInputError(f"{path} is not the projection of --{kind}-report: rerun project_idea_context.py on that report")
    return items


def build(intent: dict[str, Any], output: Path, *, creator_profile: Path | None = None,
          account_review: Path | None = None, account_context: Path | None = None,
          trend_report: Path | None = None, trend_context: Path | None = None,
          competitor_report: Path | None = None, competitor_context: Path | None = None,
          video_analysis: Path | None = None, comment_context: Path | None = None,
          previous_ideas: Path | None = None, conversation_context: Path | None = None) -> dict[str, Any]:
    """Construct provenance and context from source files, not model-authored copies."""
    retyped = [field for field in DERIVED_FIELDS if field in intent]
    if retyped:
        raise RequestInputError(f"the intent carries {retyped}: those are built from the artifacts, leave them out and "
                         "pass the artifacts instead (--creator-profile, --account-review with --account-context, "
                         "--trend-report with --trend-context, --competitor-report with --competitor-context, "
                         "--video-analysis, --comment-context)")
    if previous_ideas is not None and conversation_context is not None:
        raise RequestInputError("choose either --previous-ideas or --conversation-context for this continuation",
                                field="conversation_context")
    for report, context, name in ((trend_report, trend_context, "trend"), (competitor_report, competitor_context, "competitor")):
        if (report is None) != (context is None):
            raise RequestInputError(f"--{name}-report and --{name}-context go together: the report is the lineage, "
                             "the context is its projection")
    base = output.parent
    sources: list[dict[str, Any]] = []
    context: dict[str, Any] = {"creator": None, "trend": [], "competitor": [], "account": None}

    if creator_profile is not None:
        profile = load(creator_profile)
        sources.append(source_ref("creator_profile", creator_profile, profile, base))
        context["creator"] = {key: profile[key] for key in CREATOR_CONTEXT_FIELDS if key in profile}
    review_id = None
    if account_review is not None:
        review = load(account_review)
        sources.append(source_ref("account_review_report", account_review, review, base))
        review_id = review.get("report_id")
        # The account projection is a pure function of the review (a byte copy of
        # review-creator-account's project_idea_context.py), so prep derives it.
        projected = project_account_review(review)
        if account_context is None:
            account_context = base / "account_idea_context.json"
            account_context.parent.mkdir(parents=True, exist_ok=True)
            account_context.write_text(json.dumps(projected, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                                       encoding="utf-8")
        elif load(account_context) != projected:
            raise RequestInputError(f"{account_context} is not the projection of --account-review: drop --account-context "
                             "and prep projects the review itself")
    if account_context is not None:
        document = load(account_context)
        # Its own id: upstream artifact ids must be unique, and the review report already carries review_id.
        sources.append(source_ref("account_idea_context", account_context, document, base,
                                  artifact_id=f"account-idea-context:{review_id or account_context.stem}"))
        context["account"] = projection(account_context, "account")
    for kind, report_path, context_path in (("trend", trend_report, trend_context),
                                            ("competitor", competitor_report, competitor_context)):
        if report_path is None or context_path is None:
            continue
        report = load(report_path)
        sources.append(source_ref(f"{kind}_report", report_path, report, base))
        context[kind] = bound(projection(context_path, kind), report, context_path, kind)
    if video_analysis is not None:
        sources.append(source_ref("video_analysis", video_analysis, load(video_analysis), base))
    if comment_context is not None:
        comments = load(comment_context)
        validate_projection(comments, comment_context.parent)
        sources.append(source_ref("comment_context", comment_context, comments, base))
        context["comments"] = comments
    request = {**intent, "upstream_sources": sources, "idea_context": context}
    if previous_ideas is not None:
        # One-call input mounts can disappear before core. Retain the exact bytes
        # in this run so validation and revision bind to the same historical file.
        retained_previous = base / "previous_ideas.json"
        if not previous_ideas.is_file():
            raise RequestInputError(
                f"{previous_ideas} is not an available file", code="missing_previous_ideas", field="continuation_context",
                next_action="Restore the original previous idea_set.json from this conversation's retained file or saved result, then rerun prep. Do not recreate it from memory.")
        if previous_ideas.resolve() != retained_previous.resolve():
            base.mkdir(parents=True, exist_ok=True)
            retained_previous.write_bytes(previous_ideas.read_bytes())
        try:
            request["continuation_context"] = previous_ideas_context(retained_previous, base)
        except ValueError as error:
            raise RequestInputError(str(error), field="continuation_context") from error
    if conversation_context is not None:
        retained_conversation = base / "conversation_context.json"
        if not conversation_context.is_file():
            raise RequestInputError(f"{conversation_context} is not an available file", field="conversation_context",
                next_action="Restore the exact earlier visible messages from this conversation, including the referenced text. Do not invent a previous IdeaSet or rewrite the current brief.")
        if conversation_context.resolve() != retained_conversation.resolve():
            try:
                bound_conversation_context(conversation_context, base)
            except ValueError as error:
                raise RequestInputError(str(error), field="conversation_context") from error
            base.mkdir(parents=True, exist_ok=True)
            retained_conversation.write_bytes(conversation_context.read_bytes())
        try:
            request["conversation_context"] = bound_conversation_context(retained_conversation, base)
        except ValueError as error:
            raise RequestInputError(str(error), field="conversation_context") from error
    # 输入模式是已提供证据的事实，不能要求模型在投影生成前猜；复用 validator 的唯一判据。
    # 兼容旧 intent 中的 source_mode，但绝不让它提升证据完整度或绕过后续生成门。
    request["source_mode"] = derive_source_mode(request)
    return request


def main() -> int:
    """Build an IdeaRequest; callers then run request validation and the generation decision."""
    schema = load(REQUEST_SCHEMA)
    authored = [field for field in schema["required"] if field not in (*DERIVED_FIELDS, "source_mode")]
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0], formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Intent contract (the builder does not fill missing metadata):\n"
            f"  Required authored fields: {', '.join(authored)}\n"
            f"  schema_version: {schema['properties']['schema_version']['const']}\n"
            "  request_id: unique within the run; created_at: ISO-8601 date-time.\n"
            "  source_mode is derived from the supplied evidence, never authored: trend/competitor context alone is context_only; no evidence is brief_only.\n"
            "  generation_basis.direct_topic is an exact user-supplied topic, not a paraphrase.\n"
            "  report_language: required for new non-Chinese requests; use a BCP 47 tag.\n"
            "  open_shortlist/capacity_plan/brief_only: 3..10 ideas and 1 or 2 Hook lines.\n"
            "  One Hook line requires progressive_line=null; two require a distinct second line.\n"
            "  assets_used may contain only declared available_assets.\n"
            "  Undeclared required_assets need requires_user_confirmation, assumptions and production_missing; capacity_plan forbids them.\n\n"
            "Normal execution (including timing and the two-pass limit):\n"
            "  cd <run directory> && python3 <skill directory>/scripts/run_ideas_stages.py prep --intent intent.json --run-dir .\n"
            "  cd <run directory> && python3 <skill directory>/scripts/run_ideas_stages.py core --run-dir .\n"
            "The validate_ideas.py and render_idea_report.py commands remain available for debugging only."
        ),
    )
    parser.add_argument("--intent", type=Path, required=True, help="the request fields you author, nothing derived")
    parser.add_argument("--output", type=Path, required=True)
    for name in ("creator-profile", "account-review", "account-context", "trend-report", "trend-context",
                 "competitor-report", "competitor-context", "video-analysis", "comment-context", "previous-ideas", "conversation-context"):
        parser.add_argument(f"--{name}", type=Path)
    args = parser.parse_args()
    try:
        request = build(load(args.intent), args.output, creator_profile=args.creator_profile,
                        account_review=args.account_review, account_context=args.account_context,
                        trend_report=args.trend_report, trend_context=args.trend_context,
                        competitor_report=args.competitor_report, competitor_context=args.competitor_context,
                        video_analysis=args.video_analysis, comment_context=args.comment_context,
                        previous_ideas=args.previous_ideas, conversation_context=args.conversation_context)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "failed", "error": f"{type(error).__name__}: {error}"}, ensure_ascii=False))
        return 1
    print(json.dumps({"status": "ok", "output": str(args.output),
                      "sources": [s["source_type"] for s in request["upstream_sources"]]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
