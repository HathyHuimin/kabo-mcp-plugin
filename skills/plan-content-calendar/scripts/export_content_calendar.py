#!/usr/bin/env python3
"""Project one validated ContentCalendar JSON truth source to CSV and ICS."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Sibling imports must not leave a __pycache__ in the signed, checksummed skill directory.
sys.dont_write_bytecode = True
from calendar_common import canonical_json, file_sha256, load_json, request_input_paths, safe_output_path, write_new_bytes
from calendar_projection import csv_bytes, ics_bytes
from validate_content_calendar import validate_calendar


def _relative_output(value: str, run_root: Path) -> Path:
    relative = Path(value)
    if relative.is_absolute():
        raise ValueError(f"output paths must be run-relative: {value}")
    path = (run_root / relative).resolve()
    if path != run_root and run_root not in path.parents:
        raise ValueError(f"output path escapes run root: {value}")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("calendar", type=Path)
    parser.add_argument("request", type=Path)
    parser.add_argument("--run-root", type=Path, default=None)
    parser.add_argument("--csv", required=True, dest="csv_path")
    parser.add_argument("--ics", required=True, dest="ics_path")
    parser.add_argument("--output", type=Path, required=True, help="calendar JSON with export hashes")
    args = parser.parse_args()
    request_path = args.request.resolve()
    run_root = (args.run_root or request_path.parent).resolve()
    try:
        calendar_path = args.calendar.resolve()
        calendar = load_json(calendar_path)
        request = load_json(request_path)
        errors = validate_calendar(calendar, request, request_path, run_root)
        if errors:
            raise ValueError("input calendar is invalid:\n- " + "\n- ".join(errors))
        if calendar.get("exports"):
            raise ValueError("input calendar already contains exports; project from the original JSON truth source")
        protected = request_input_paths(request, request_path, run_root) | {calendar_path}
        csv_path = safe_output_path(_relative_output(args.csv_path, run_root), run_root, protected, "--csv")
        ics_path = safe_output_path(_relative_output(args.ics_path, run_root), run_root, protected, "--ics")
        output_path = safe_output_path(args.output, run_root, protected, "--output")
        if len({csv_path, ics_path, output_path}) != 3:
            raise ValueError("--csv, --ics and --output must identify three different files")
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        ics_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        write_new_bytes(csv_path, csv_bytes(calendar))
        write_new_bytes(ics_path, ics_bytes(calendar))
        calendar["exports"] = [
            {"format": "csv", "path": args.csv_path, "sha256": file_sha256(csv_path), "record_count": len(calendar.get("slots") or [])},
            {"format": "ics", "path": args.ics_path, "sha256": file_sha256(ics_path), "record_count": len(calendar.get("slots") or [])},
        ]
        errors = validate_calendar(calendar, request, request_path, run_root, output_path)
        if errors:
            raise ValueError("projected calendar is invalid:\n- " + "\n- ".join(errors))
    except (OSError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    write_new_bytes(output_path, canonical_json(calendar).encode("utf-8"))
    print(f"WROTE {output_path}")
    print(f"WROTE {csv_path}")
    print(f"WROTE {ics_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
