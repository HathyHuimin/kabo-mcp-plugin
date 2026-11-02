#!/usr/bin/env python3
"""Read shared short-video methods for a traffic experiment, without new evidence."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
from hook_core.context import prepare_context
from hook_core.common import read_json, write_json


def build(platform: str, pattern_ids: list[str] | None = None) -> dict:
    if platform not in ("instagram", "youtube", "tiktok"):
        raise ValueError("unsupported traffic platform")
    context = prepare_context({})
    modes = [p for p in context["pattern_candidates"] if platform in p["applicability"]["platforms"]]
    known = sorted(p["id"] for p in modes)
    if pattern_ids is not None:
        pattern_ids = [value.strip().upper() for value in pattern_ids]
    if pattern_ids is not None and (not 1 <= len(pattern_ids) <= 3 or len(set(pattern_ids)) != len(pattern_ids)
                                  or set(pattern_ids) - set(known)):
        raise ValueError(f"select one to three unique known pattern IDs for {platform}: {known}")
    keys = (("id", "name", "visual", "sound", "joint_expression", "material_requirements", "applicability", "exceptions")
            if pattern_ids else ("id", "name", "material_requirements"))
    return {"kind": "HookResearchContext", "purpose": "traffic_experiment", "platform": platform,
            "basis": context["basis"], "material_basis": context["material_basis"],
            "pattern_candidates": [{k: p[k] for k in keys} for p in modes if not pattern_ids or p["id"] in pattern_ids],
            "provenance": context["provenance"], "media_execution": "not_requested",
            "scope": "Short-video methods for a proposed experiment, not observed content, retention, platform policy or a cause of traffic change."}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", required=True, type=str.lower, choices=("instagram", "youtube", "tiktok"))
    parser.add_argument("--pattern-id", action="append")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = build(args.platform, args.pattern_id)
        if args.output.exists():
            if read_json(args.output) != result:
                raise ValueError("existing context differs; preserve it and choose a fresh output")
        else:
            write_json(args.output, result)
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(json.dumps({"status": "failed", "error": str(error)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
