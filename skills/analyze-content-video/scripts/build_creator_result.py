#!/usr/bin/env python3
"""Wrap the creator-facing view in the delivery envelope this run already knows how to fill.

The model used to write the envelope too: the version string, the result id, the task name, the
status, the identity block, the timestamp index and the follow-up list. Measured 2026-09-23, that
was about a quarter of a 5.9 KB object it spent 57 seconds typing, and none of it was a judgement
-- every value is either a constant, a copy of the request, or the timestamp of an observation the
analysis already carries. Three real runs wrote three different shapes of it, and two spellings of
`status` are on disk right now, because nothing checked a part nobody was reading.

What stays the model's is the reading -- the headline, summary, cards and next actions, in the
creator's language -- plus the decision and follow-up options. The source note is derived only from recorded
sampling and citation facts; an authored note never overrides those facts.
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
from creator_measurement_basis import canonical_note, note_language, note_matches

SCHEMA_VERSION = "creator-task-result.v2-final"
TASK = "analyze_content_video"
# One spelling. `partial` and `completed_partial` are both on disk from runs that each invented it.
COMPLETE, PARTIAL = "complete", "partial"
# What the delivery is made of. The shape gate checks the same list; naming it here moves the
# failure to the step that can still be fixed cheaply.
VIEW_REQUIRED = ("language", "headline", "summary", "data_note", "cards", "next_actions")
NEXT_ACTIONS_MIN = 1
NEXT_ACTIONS_MAX = 3
FRAME_PREFIX = "frame-"


def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"{path} does not exist; write it before building the result")
    except json.JSONDecodeError as error:
        raise SystemExit(f"{path} is not valid JSON: {error}")
    if not isinstance(value, dict):
        raise SystemExit(f"{path} must hold a JSON object")
    return value


def record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def view_contract(analysis: dict[str, Any]) -> dict[str, Any]:
    """Describe the author input using the same constraints as the delivery gate."""
    from validate_creator_shape import (
        CARD_REQUIRED, CARD_COUNT, CARD_ROLES, LINES_PER_CARD, FIELD_LIMITS,
        CHANGE_CARD, CHANGE_HOOK_MIN, CHANGE_HOOK_MAX, OBSERVATION_REF,
    )
    observations = analysis.get("observations")
    if not isinstance(observations, list) or not observations:
        raise SystemExit("view author contract requires analysis.observations to be a non-empty array")
    identifiers = []
    for index, observation in enumerate(observations):
        identifier = record(observation).get("observation_id")
        if not isinstance(identifier, str) or not identifier.strip():
            raise SystemExit(f"analysis.observations[{index}].observation_id must be a non-empty string")
        matched = OBSERVATION_REF.fullmatch(f"observations.{identifier}")
        if matched is None or matched.group(1) != identifier:
            raise SystemExit(f"analysis.observations[{index}].observation_id cannot be referenced exactly")
        if identifier in identifiers:
            raise SystemExit(f"analysis has duplicate observation_id {identifier!r}; references are ambiguous")
        identifiers.append(identifier)
    return {
        "title": "Creator view author contract (not a complete JSON Schema)",
        "document": {"decision": "one-word string", "creator_view": "object described below"},
        "creator_view": {
            "required": [key for key in VIEW_REQUIRED if key != "data_note"],
            "derived_read_only": {"data_note": "The builder derives one sampling line from evidence and card citations, in Chinese when language starts with zh and in English otherwise. Omit this field; it never records how many pixels were viewed."},
            "strings": [key for key in VIEW_REQUIRED if key not in ("cards", "next_actions", "data_note")],
            "language": "The creator's language as a BCP 47 tag such as zh-CN or en. A zh tag renders the report's own labels and data_note in Chinese; any other tag renders them in English.",
            "cards": {
                "count": CARD_COUNT, "roles_in_order": list(CARD_ROLES),
                "required": list(CARD_REQUIRED),
                "strings": [key for key in CARD_REQUIRED if key != "evidence_refs"],
                "why_for_you": {"type": "string", "nonempty_lines": LINES_PER_CARD,
                                "pairing": "The same line index pairs strength, supported weakness, and next change."},
                "evidence_refs": {"type": "array of strings", "at_least_one_from":
                                  [f"observations.{identifier}" for identifier in identifiers],
                                  "format": "Use these canonical references verbatim, not bare observation IDs."},
                "hooks": {"type": "array of objects", "required_on_card_index": CHANGE_CARD,
                          "min_items_on_change_card": CHANGE_HOOK_MIN,
                          "max_items_on_change_card": CHANGE_HOOK_MAX,
                          "fields": {"purpose": "string", "visual_direction": "string",
                                     "spoken_line": "optional string", "on_screen_text": "optional string",
                                     "time_range": "optional source-video moment; never fabricate a still-image timestamp"}},
            },
            "next_actions": {"type": "array of non-empty strings", "min_items": NEXT_ACTIONS_MIN,
                             "max_items": NEXT_ACTIONS_MAX},
        },
        "display_column_limits": dict(FIELD_LIMITS),
        "width_rule": "Count each full-width or wide Unicode character as two display columns, all others as one; count the complete field including line breaks.",
        "scope": "Judgment or completed analysis supplies observation IDs only. Read references/diagnosis-report.md whole for evidence-grounded content, localization and hooks.",
    }


def prose_of(view: dict[str, Any]) -> str:
    """Every sentence the creator reads, so a figure written in one can be recognised as a moment."""
    parts = [str(view.get(key) or "") for key in ("headline", "summary", "data_note")]
    parts += [str(line) for line in view.get("next_actions") or []]
    for card in view.get("cards") or []:
        entry = record(card)
        parts += [str(entry.get(key) or "") for key in ("title", "why_for_you", "action")]
        for hook in entry.get("hooks") or []:
            parts += [str(value) for value in record(hook).values() if isinstance(value, str)]
    return " ".join(parts)


def evidence_index(view: dict[str, Any], analysis: dict[str, Any],
                   evidence: dict[str, Any]) -> list[dict[str, Any]]:
    """Every reference the cards cite, with the moment it points at.

    The times are not new information: an observation already carries `t_start_s` and `t_end_s`,
    and a frame already carries `t_seconds`. Copying them by hand is how a report ends up with a
    timestamp that is not the one the observation recorded.
    """
    moments: dict[str, tuple[Any, Any]] = {}
    for observation in analysis.get("observations") or []:
        entry = record(observation)
        identifier = entry.get("observation_id")
        if identifier:
            moments[f"observations.{identifier}"] = (entry.get("t_start_s"), entry.get("t_end_s"))
    for frame in record(evidence.get("frames")).get("items") or []:
        entry = record(frame)
        identifier = entry.get("frame_id")
        if identifier:
            at = entry.get("t_seconds")
            moments[identifier] = (at, at)
    cited: list[str] = []
    for card in view.get("cards") or []:
        for ref in record(card).get("evidence_refs") or []:
            if isinstance(ref, str) and ref not in cited:
                cited.append(ref)
    # The index is also what the renderer is allowed to read as a position: a figure in the prose
    # becomes a timecode only if this result says that instant exists. A card that names 12.0s in a
    # sentence while citing only the observations it drew the point from would leave that figure in
    # the document as raw seconds, which is what the report gate exists to catch. So every moment
    # the prose actually names earns a row too, whether or not a card listed its reference.
    spoken = set()
    for figure in re.findall(r"(\d+(?:\.\d+)?)s", prose_of(view)):
        try:
            spoken.add(round(float(figure), 3))
        except ValueError:
            continue
    index = []
    for ref, (start, end) in moments.items():
        names_it = ref in cited
        if not names_it:
            names_it = any(isinstance(at, (int, float)) and round(float(at), 3) in spoken
                           for at in (start, end))
        if names_it:
            index.append({"ref": ref, "t_start_s": start, "t_end_s": end})
    index.sort(key=lambda row: (row["t_start_s"] if isinstance(row["t_start_s"], (int, float)) else 0,
                                row["ref"]))
    return index


def follow_up_options(view: dict[str, Any], supplied: Any, analysis: dict[str, Any]) -> list[str]:
    """What the creator could supply next. Theirs when written, because these are read by a person.

    Generating them from `unavailable_dimensions[].would_require` is a fallback and nothing more:
    those sentences are English audit records, and a creator who asked in another language should
    not be handed two lines of it at the end of a localized report.
    """
    if isinstance(supplied, list) and supplied:
        return [line for line in supplied if isinstance(line, str) and line.strip()]
    if isinstance(record(view.get("interaction")).get("follow_up_options"), list):
        return [line for line in view["interaction"]["follow_up_options"] if isinstance(line, str)]
    options = []
    for entry in analysis.get("unavailable_dimensions") or []:
        needed = record(entry).get("would_require")
        if isinstance(needed, str) and needed.strip() and needed not in options:
            options.append(needed)
    return options


def validate_next_actions(view: dict[str, Any]) -> None:
    actions = record(view).get('next_actions')
    if not isinstance(actions, list):
        raise SystemExit('creator_view.next_actions must be an array of non-empty strings')
    if not NEXT_ACTIONS_MIN <= len(actions) <= NEXT_ACTIONS_MAX:
        raise SystemExit('creator_view.next_actions must contain one to three non-empty strings')
    for index, action in enumerate(actions):
        if not isinstance(action, str) or not action.strip():
            raise SystemExit(f'creator_view.next_actions[{index}] must be a non-empty string')


def build(view_document: dict[str, Any], analysis: dict[str, Any], evidence: dict[str, Any],
          request: dict[str, Any]) -> dict[str, Any]:
    view = record(view_document.get("creator_view")) or view_document
    if record(request.get("source")).get("acquisition") not in ("local_file", "public_url", "owner_connector"):
        raise SystemExit("cannot derive data_note: the video source is not known")
    images = record(evidence.get("images"))
    if images.get("media_type") in ("image", "image_carousel"):
        # Returned positions alone do not establish the full count. Preserve a separately
        # recorded total without letting model-authored prose change the measurement basis.
        available = sum(item.get("status") == "available" for item in images["items"])
        total = "unknown" if images["expected_count"] is None else images["expected_count"]
        view = dict(view)
        owner = evidence['owner_metrics']['status']
        if note_language(view) == "zh":
            # 和英文行同一组事实、同一组数字；报告原样转述，所以中文报告里这一行也是中文。
            completeness = {"unknown": "未知"}.get(images['completeness'], images['completeness'])
            owner_zh = {"available": "可用", "partial": "部分可用", "unavailable": "不可用"}.get(owner, owner)
            view["data_note"] = (
                f"图文帖：返回 {images['returned_count']} 个位置，{available} 个可用；"
                f"总数{'未知' if total == 'unknown' else f' {total}'}；完整度{completeness}；"
                f"后台数据{owner_zh}。"
            )
        else:
            view["data_note"] = (
                f"Image post: {available}/{images['returned_count']} returned positions available; "
                f"total count {total}; completeness {images['completeness']}; "
                f"owner metrics {owner}."
            )
    else:
        view = dict(view)
        try:
            view["data_note"] = canonical_note(evidence, analysis, view)
        except ValueError as error:
            raise SystemExit(str(error)) from error
    # Named here rather than left to the shape gate three steps later: the gate is correct but it
    # runs after the document has been rendered, so a missing key costs a render and a round trip.
    missing = [key for key in VIEW_REQUIRED if not view.get(key)]
    if missing:
        raise SystemExit(
            f"the creator view is missing {', '.join(missing)}. It carries the whole delivery: "
            f"{', '.join(VIEW_REQUIRED)}. This script fills the envelope around it, never it")
    validate_next_actions(view)
    ref = record(analysis.get("content_ref")) or record(evidence.get("content_ref"))
    run_id = record(evidence.get("reproducibility")).get("run_id") or "unknown"
    completeness = record(analysis.get("evidence_completeness"))
    # Anything the analysis could not see makes the delivery partial. A run that says otherwise
    # presents a reading of public counters alone as a complete one.
    whole = all(completeness.get(key) == "available" for key in ("media", "transcript", "frames", "owner_metrics"))
    decision = view_document.get("decision") or record(view_document.get("details")).get("decision")
    return {
        "schema_version": SCHEMA_VERSION,
        "result_id": f"ctr-{run_id}",
        "task": TASK,
        "status": COMPLETE if whole else PARTIAL,
        "details": {
            "mode": record(view_document.get("details")).get("mode") or "diagnosis",
            "platform": ref.get("platform"),
            "content_id": ref.get("content_id"),
            "decision": decision,
        },
        "evidence_index": evidence_index(view, analysis, evidence),
        "interaction": {
            "follow_up_options": follow_up_options(
                view_document, record(view_document.get("interaction")).get("follow_up_options"), analysis),
        },
        "creator_view": view,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build CreatorTaskResult from the creator view and this run's analysis")
    parser.add_argument("--print-view-contract", action="store_true")
    parser.add_argument("--observations-from", type=Path,
                        help="Judgment or completed analysis used only for observation IDs")
    for name in ("creator-view", "analysis", "evidence", "request", "output"):
        parser.add_argument(f"--{name}", type=Path)
    args = parser.parse_args()
    if args.print_view_contract:
        if args.observations_from is None:
            parser.error("--observations-from is required for --print-view-contract")
        if any(getattr(args, name) is not None for name in ("creator_view", "analysis", "evidence", "request", "output")):
            parser.error("--print-view-contract accepts only --observations-from")
        print(json.dumps(view_contract(load(args.observations_from)), indent=2, ensure_ascii=False))
        return 0
    if args.observations_from is not None:
        parser.error("--observations-from requires --print-view-contract")
    for name in ("creator_view", "analysis", "evidence", "request", "output"):
        if getattr(args, name) is None:
            parser.error(f"--{name.replace('_', '-')} is required when building a result")

    document = load(args.creator_view)
    # A finished result handed here is passed through: a replay, or a view written before this step
    # existed, must not have its envelope rebuilt underneath it.
    if document.get("schema_version") == SCHEMA_VERSION and document.get("creator_view"):
        validate_next_actions(document['creator_view'])
        evidence, analysis = load(args.evidence), load(args.analysis)
        if not record(evidence.get("images")).get("media_type") in ("image", "image_carousel"):
            try:
                matches = note_matches(document['creator_view'].get('data_note'), evidence, analysis, document['creator_view'])
            except ValueError as error:
                raise SystemExit(str(error)) from error
            if not matches:
                raise SystemExit("creator_view.data_note disagrees with the recorded measurement basis")
        args.output.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(json.dumps({"status": "ok", "output": str(args.output), "built": False,
                          "reason": "the creator view is already a complete result"}))
        return 0

    result = build(document, load(args.analysis), load(args.evidence), load(args.request))
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "ok", "output": str(args.output), "built": True,
        "result_id": result["result_id"], "result_status": result["status"],
        "cards": len(result["creator_view"].get("cards") or []),
        "evidence_index": len(result["evidence_index"]),
        "follow_up_options": len(result["interaction"]["follow_up_options"]),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
