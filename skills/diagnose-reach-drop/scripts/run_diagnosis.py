#!/usr/bin/env python3
"""One local invocation for calculation and compact evidence rendering."""
import argparse
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
from analyze_traffic import build, load
from render_traffic import render


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", type=Path)
    parser.add_argument("--analysis", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--language", choices=["en", "zh-CN"], default="zh-CN")
    parser.add_argument("--print-evidence", action="store_true",
                        help="Print the calculated evidence; send file metadata to stderr.")
    args = parser.parse_args()
    try:
        value = build(load(args.case), load(args.analysis) if args.analysis else None)
        report = render(value, args.language)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        result_path = args.output_dir / "reach_drop_assessment.json"
        report_path = args.output_dir / "TRAFFIC_EVIDENCE.md"
        result_path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        report_path.write_text(report, encoding="utf-8")
        print(json.dumps({"status": "ok", "assessment": str(result_path), "evidence_report": str(report_path)}, ensure_ascii=False),
              file=sys.stderr if args.print_evidence else sys.stdout)
        if args.print_evidence:
            print(report, end="")
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
