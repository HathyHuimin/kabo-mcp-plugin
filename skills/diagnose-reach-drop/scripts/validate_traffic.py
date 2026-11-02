#!/usr/bin/env python3
"""Bind calculated facts (and optional rendered evidence) to their actual inputs."""
import argparse
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
from analyze_traffic import build, load
from render_traffic import render
from compose_traffic import compose


def validate(value, case, analysis=None):
    expected = build(case, analysis)
    return [] if value == expected else ["assessment differs from input-bound deterministic result"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("assessment", type=Path)
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--analysis", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--delivery", type=Path)
    parser.add_argument("--language", choices=["en", "zh-CN"], default="zh-CN")
    args = parser.parse_args()
    try:
        value = load(args.assessment)
        errors = validate(value, load(args.case), load(args.analysis) if args.analysis else None)
        if args.report:
            expected = compose(value, load(args.delivery), args.language) if args.delivery else render(value, args.language)
            if args.report.read_text(encoding="utf-8") != expected:
                errors.append("report differs from input-bound rendering")
    except (OSError, ValueError, KeyError, TypeError) as error:
        errors = [str(error)]
    print(json.dumps({"valid": not errors, "errors": errors}, ensure_ascii=False))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
