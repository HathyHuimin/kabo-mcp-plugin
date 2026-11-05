#!/usr/bin/env python3
"""Validate and render one brand request's atomic tasks using the existing business rules.

It never reserves a run, fetches evidence, changes permissions, or writes a run manifest: the run
comes from `static_host.py init`, and every page a visual spec quotes is already placed in that
task's `snapshot/` by `place_composite.py pages`.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

sys.dont_write_bytecode = True
from validate_brand_options import (
    check_spec_pages, load, validate_brief, validate_kit, validate_request, validate_result, validate_spec,
)
from render_brand_options import render, render_brief, render_kit, render_spec


TASK_FILES = {
    "youtube_channel_name": "brand_options.json",
    "instagram_bio": "brand_options.json",
    "instagram_username": "brand_options.json",
    "tiktok_bio": "brand_options.json",
    "tiktok_username": "brand_options.json",
    "brand_strategy_brief": "strategy_brief.json",
    "brand_kit": "brand_kit.json",
    "visual_identity_spec": "visual_spec.json",
}


def run_path(root: Path, relative: str) -> Path:
    path = root / relative
    resolved = path.resolve()
    if resolved != root.resolve() and root.resolve() not in resolved.parents:
        raise ValueError(f"path leaves this run: {relative}")
    return path


def requests(root: Path, tasks: list[str]) -> list[tuple[str, dict]]:
    if len(set(tasks)) != len(tasks):
        raise ValueError("each requested atomic task must occur exactly once")
    result = []
    for task in tasks:
        if task not in TASK_FILES:
            raise ValueError(f"unsupported task: {task}")
        payload = load(str(run_path(root, f"{task}/request.json")))
        validate_request(payload)
        if payload["task"] != task:
            raise ValueError(f"{task}/request.json belongs to task {payload['task']}")
        result.append((task, payload))
    return result


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".brand-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(text)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def delivery_language_key(value: str) -> str:
    # Preserve the v1 spellings alongside their existing language-tag equivalents. Locale and
    # script subtags otherwise stay significant: pt-BR must not match pt-PT, nor zh-CN zh-TW.
    aliases = {"English": "en", "中文": "zh-cn"}
    return aliases.get(value, "zh-cn" if value.lower() == "zh" else value.lower())


def finish(root: Path, tasks: list[str]) -> dict:
    rendered = []
    # Check every task before publishing any report. The renderer's original validation is
    # retained too; a failed atomic task must never yield a combined partial report.
    for task, request in requests(root, tasks):
        payload = load(str(run_path(root, f"{task}/{TASK_FILES[task]}")))
        if task == "brand_strategy_brief":
            validate_brief(payload)
            text = render_brief(payload)
        elif task == "brand_kit":
            validate_kit(payload)
            text = render_kit(payload)
        elif task == "visual_identity_spec":
            validate_spec(payload)
            errors = check_spec_pages(payload, str(run_path(root, f"{task}/snapshot")))
            if errors:
                raise ValueError("; ".join(errors))
            text = render_spec(payload)
        else:
            validate_result(payload, request)
            text = render(payload, request)
        if delivery_language_key(payload["delivery_language"]) != delivery_language_key(request["delivery_language"]):
            raise ValueError(f"{task} result delivery_language {payload['delivery_language']!r} does not "
                             f"match request delivery_language {request['delivery_language']!r}")
        rendered.append((run_path(root, f"{task}/RESULT.md"), text))
    report = run_path(root, "report/RESULT.md")
    for path, text in rendered:
        atomic_text(path, text)
    atomic_text(report, "".join(text for _, text in rendered))
    return {"status": "ok", "tasks": tasks, "report": "report/RESULT.md",
            "atomic_reports": [f"{task}/RESULT.md" for task in tasks]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("validate-requests", "finish"):
        child = commands.add_parser(command)
        child.add_argument("--task", action="append", choices=sorted(TASK_FILES), required=True,
                           help="one requested task; repeat in the creator's request order")
    args = parser.parse_args()
    try:
        if args.command == "validate-requests":
            requests(Path.cwd(), args.task)
            result = {"status": "ok", "tasks": args.task, "requests_valid": True}
        else:
            result = finish(Path.cwd(), args.task)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "failed", "error": f"{type(error).__name__}: {error}"}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
