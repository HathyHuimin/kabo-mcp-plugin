#!/usr/bin/env python3
"""Build the only allowed SelectedIdea projection for a literal user direction."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


DIRECT_SOURCE_ID = "direct-user-selection"


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def write_private(path: Path, value: str) -> None:
    """Create/truncate a private artifact without a world-readable permission window."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        handle = os.fdopen(descriptor, "w", encoding="utf-8")
        descriptor = -1
        with handle:
            handle.write(value)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def nonempty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def direct_ids(request_id: str) -> tuple[str, str]:
    """Stable request-scoped IDs that the request can declare before this file exists."""
    return f"direct-selected:{request_id}", f"direct-user:{request_id}"


def resolve(base: Path, value: str) -> Path:
    candidate = Path(value)
    return (candidate if candidate.is_absolute() else base / candidate).resolve()


def build(request: dict[str, Any], request_path: Path, output_path: Path) -> dict[str, Any]:
    if request.get("schema_version") != "script-package-request.v1":
        raise ValueError("request must use script-package-request.v1")
    request_id = nonempty(request.get("request_id"), "request_id")
    created_at = nonempty(request.get("created_at"), "created_at")
    platform = nonempty(request.get("platform"), "platform")
    language = nonempty(request.get("language"), "language")
    content_format = request.get("content_format") or {}
    format_kind = content_format.get("kind")
    if format_kind not in {"short_form", "long_form"}:
        raise ValueError("content_format.kind must be short_form or long_form")

    direct = request.get("direct_user_selection")
    if not isinstance(direct, dict):
        raise ValueError("request.direct_user_selection is required for direct selection")
    literal = nonempty(direct.get("literal_direction"), "direct_user_selection.literal_direction")
    goal = nonempty(direct.get("goal"), "direct_user_selection.goal")
    audience = nonempty(direct.get("audience"), "direct_user_selection.audience")
    desired_action = nonempty(
        direct.get("desired_audience_action"),
        "direct_user_selection.desired_audience_action",
    )

    selected_ref = request.get("selected_idea") or {}
    selected_id, candidate_id = direct_ids(request_id)
    if selected_ref.get("schema_version") != "selected-idea.v2":
        raise ValueError("direct selection must reference selected-idea.v2")
    if selected_ref.get("artifact_id") != selected_id:
        raise ValueError(f"direct selection artifact_id must be {selected_id}")
    selected_path_value = nonempty(selected_ref.get("path"), "selected_idea.path")
    if resolve(request_path.parent, selected_path_value) != output_path.resolve():
        raise ValueError("--output must match request.selected_idea.path")
    if selected_ref.get("sha256") is not None:
        raise ValueError("direct selection sha256 must be null until the artifact has been built")

    outline = [
        {
            "order": order,
            "beat": literal,
            "purpose": purpose,
            "evidence_refs": [],
        }
        for order, purpose in enumerate(
            (
                "introduce the creator-selected direction",
                "develop the creator-selected direction",
                "deliver the creator-selected direction",
            ),
            start=1,
        )
    ]
    return {
        "schema_version": "selected-idea.v2",
        "selected_idea_id": selected_id,
        "source_idea_set_id": DIRECT_SOURCE_ID,
        "source_candidate_id": candidate_id,
        "selection_status": "selected_with_edits",
        "selected_at": created_at,
        "user_edits": [literal],
        "confirmed_constraints": {
            "goal": goal,
            "audience": audience,
            "desired_audience_action": desired_action,
            "language": language,
            "time_per_item": None,
            "budget": None,
            "filming_mode": None,
            "editing_capacity": None,
            "publishing_cadence": None,
        },
        "idea": {
            "working_title": literal,
            "audience_problem": literal,
            "content_promise": literal,
            "angle": literal,
            "platforms": [platform],
            "formats": [format_kind],
            "hook_preview": {
                "opening_line": literal,
                "progressive_line": None,
                "mechanism": "literal_creator_direction",
            },
            "content_outline": outline,
            "source_context_refs": [],
            "evidence_refs": [],
            "primary_evidence_ref": None,
            "differentiation": "Preserve the creator's literal direction without adding unsupported claims.",
            "must_differ": [
                "Do not replace or broaden the creator's literal direction.",
                "Do not invent evidence or factual support.",
            ],
            "required_assets": [],
            "risks": [
                "This direct selection has no validated evidence; factual claims remain hypothetical."
            ],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        request_path = args.request.resolve()
        result = build(load(request_path), request_path, args.output)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        write_private(
            args.output,
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        # 实测（gpt-5.6）：这一步静默成功时，模型常把它当成终点、直接在回复里写脚本，
        # 后面的 package、校验与渲染都没跑。打印一行状态与下一步，让它接着走流程。
        print(json.dumps({
            "status": "ok",
            "selected_idea": str(args.output),
            "next_action": "This only records the selected direction. Continue with Workflow step 2: "
                           "write script_package.json, project its input snapshot, then validate and "
                           "render; the deliverable is the rendered SCRIPT_PACKAGE.md.",
        }, ensure_ascii=False))
        return 0
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        print(
            json.dumps(
                {"status": "failed", "error": f"{type(error).__name__}: {error}"},
                ensure_ascii=False,
            )
        )
        return 2


if __name__ == "__main__":
    sys.exit(main())
