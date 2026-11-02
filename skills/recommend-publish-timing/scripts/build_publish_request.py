#!/usr/bin/env python3
"""Build the publish request's `history` block from the PublishHistory it describes.

The request carries a summary of the history - its snapshot hash, window, metric, maturity and two
counts - plus a reference to the PublishHistory holding the per-post rows. The model used to type
that block: a 64-character hash, dates and counts copied off a file it had read. A real fixture
shows what that produces: 74 usable posts in a request whose own history counts 72, date-only
windows where the history carries instants, and a metric name the history does not use. Nothing
in this Skill checked it; the calendar Skill rejected the mismatch one Skill later.

Here the model writes only the request fields it authors (request id, content, creator, candidate
windows, peer snapshot ref) and this script adds `history` from the PublishHistory:

  python3 scripts/build_publish_request.py --intent intent.json \\
    --history publish_history.json --output publish_request.json

Standard library only.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Any


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must hold a JSON object")
    return value


def history_block(history: dict[str, Any], history_ref: str) -> dict[str, Any]:
    if not str(history.get("schema_version", "")).startswith("publish-history."):
        raise ValueError("--history must be the PublishHistory written by project_publish_history.py")
    source = history.get("source") or {}
    coverage = history.get("coverage") or {}
    panel = history.get("metric_panel") or []
    total = coverage.get("catalog_posts")
    usable = coverage.get("cohort_mature_with_metric")
    if not total:
        status = "unavailable"
    elif usable:
        status = "available"
    else:
        status = "partial"
    return {
        "source_mode": source.get("mode") or "unavailable",
        "status": status,
        "snapshot_sha256": source.get("snapshot_sha256"),
        "window": source.get("window"),
        "metric": panel[0].get("name") if panel and isinstance(panel[0], dict) else None,
        "maturity_days": (history.get("maturity") or {}).get("days"),
        "post_count_total": total,
        "post_count_usable": usable,
        "history_ref": history_ref,
    }


def bind_selected_idea(intent: dict[str, Any], history: dict[str, Any], selected_path: Path,
                       output: Path) -> dict[str, Any]:
    """Bind immutable selected content; never replace an explicit conflicting intent."""
    selected_bytes = selected_path.read_bytes()
    selected = json.loads(selected_bytes)
    if not isinstance(selected, dict):
        raise ValueError("--selected-idea must hold a JSON object")
    if selected.get("schema_version") != "selected-idea.v2" or selected.get("selection_status") != "selected":
        raise ValueError("--selected-idea requires a selected selected-idea.v2 artifact")
    selected_id = selected.get("selected_idea_id")
    if not isinstance(selected_id, str) or not selected_id.strip():
        raise ValueError("SelectedIdea has no selected_idea_id")
    request = copy.deepcopy(intent)
    if request.get("schema_version") != "publish-request.v1":
        raise ValueError("a selected-content intent must use publish-request.v1")
    if not isinstance(request.get("request_id"), str) or not request["request_id"].strip():
        raise ValueError("a selected-content intent requires a new request_id")
    content = request.setdefault("content", {})
    creator = request.setdefault("creator", {})
    if not isinstance(content, dict) or not isinstance(creator, dict):
        raise ValueError("intent content and creator must be objects")
    idea = selected.get("idea") or {}
    if not isinstance(idea, dict):
        raise ValueError("SelectedIdea.idea must be an object")
    for field, allowed in (("platform", idea.get("platforms")), ("format", idea.get("formats"))):
        if not isinstance(allowed, list) or not allowed or any(not isinstance(v, str) or not v for v in allowed):
            raise ValueError(f"SelectedIdea has no usable {field} choices")
        supplied = content.get(field)
        if supplied is None:
            if len(allowed) != 1:
                raise ValueError(f"select one explicit content.{field} from SelectedIdea before building the intent")
            content[field] = allowed[0]
        elif supplied not in allowed:
            raise ValueError(f"intent content.{field} conflicts with SelectedIdea; author a new intent")
    constraints = selected.get("confirmed_constraints")
    if not isinstance(constraints, dict):
        raise ValueError("SelectedIdea.confirmed_constraints must be an object")
    language = constraints.get("language")
    if not isinstance(language, str) or not language.strip():
        raise ValueError("SelectedIdea has no confirmed content language")
    if content.get("language") is not None and content["language"] != language:
        raise ValueError("intent content.language conflicts with SelectedIdea; author a new intent")
    content["language"] = language
    account = history.get("account_ref")
    if not isinstance(account, str) or not account.strip():
        raise ValueError("PublishHistory has no account_ref")
    if creator.get("account_ref") is not None and creator["account_ref"] != account:
        raise ValueError("intent creator.account_ref conflicts with PublishHistory; author a new intent")
    creator["account_ref"] = account
    if history.get("platform") != content["platform"]:
        raise ValueError("SelectedIdea platform conflicts with PublishHistory")
    formats = {p.get("product_type") for p in history.get("posts", []) if isinstance(p, dict)}
    if formats and content["format"] not in formats:
        raise ValueError("selected content.format has no matching PublishHistory rows; do not rename history formats")
    ref = {"path": os.path.relpath(selected_path.resolve(), output.parent.resolve()),
           "artifact_id": selected_id, "schema_version": "selected-idea.v2",
           "sha256": hashlib.sha256(selected_bytes).hexdigest()}
    existing = content.get("source_idea_ref")
    if existing is not None and existing != ref:
        raise ValueError("intent content.source_idea_ref conflicts with SelectedIdea; author a new intent")
    content["source_idea_ref"] = ref
    return request


SCHEMA = Path(__file__).resolve().parent.parent / "references" / "publish-request.schema.json"


def check_shape(intent: dict[str, Any]) -> None:
    """按 publish-request 的必填结构先查一遍，缺块时指明缺什么，而不是让 compute_timing.py 读到 KeyError。

    插件实测（T-20261029-12 R2）：模型把 platform / format / timezone 平铺写在顶层、没有 content 块，
    这里照常通过，到 compute_timing.py 才以 KeyError: 'content' 失败，整轮没有交付。
    """
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    missing = [key for key in schema["required"] if key != "history" and key not in intent]
    for block in ("content", "creator"):
        value = intent.get(block)
        if block in intent and not isinstance(value, dict):
            missing.append(f"{block} (must be an object)")
        elif isinstance(value, dict):
            missing += [f"{block}.{key}" for key in schema["properties"][block].get("required", []) if key not in value]
    if missing:
        raise ValueError(f"the intent is missing {', '.join(missing)}; write it in the shape of "
                         "references/publish-request.schema.json (platform and format go under content, "
                         "account_ref and timezone under creator), then run this command again")


def build(intent: dict[str, Any], history_path: Path, output: Path,
          *, selected_idea_path: Path | None = None) -> dict[str, Any]:
    if "history" in intent:
        raise ValueError("the intent carries history: it is built from the PublishHistory, leave it out")
    if selected_idea_path is None:
        check_shape(intent)
    history = load(history_path)
    if selected_idea_path is not None:
        intent = bind_selected_idea(intent, history, selected_idea_path, output)
    ref = os.path.relpath(history_path.resolve(), output.parent.resolve())
    request = {**intent, "history": history_block(history, ref)}
    platform = (request.get("content") or {}).get("platform")
    if platform and history.get("platform") and platform != history["platform"]:
        raise ValueError(f"the request is for {platform!r} but the history is {history['platform']!r}")
    return request


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--intent", type=Path, required=True, help="the request fields you author, no history")
    parser.add_argument("--history", type=Path, required=True, help="project_publish_history.py output")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--selected-idea", type=Path, help="optional immutable selected-idea.v2; conflicting intent fields fail")
    args = parser.parse_args()
    try:
        request = build(load(args.intent), args.history, args.output, selected_idea_path=args.selected_idea)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x" if args.selected_idea else "w", encoding="utf-8") as handle:
            handle.write(json.dumps(request, ensure_ascii=False, indent=2) + "\n")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "failed", "error": f"{type(error).__name__}: {error}"}, ensure_ascii=False))
        return 1
    print(json.dumps({"status": "ok", "output": str(args.output), "history": request["history"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
