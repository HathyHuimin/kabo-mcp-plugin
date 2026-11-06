#!/usr/bin/env python3
"""Place one fetched composite result's envelopes into this run, in call order.

`static_host.py fetch --tool <composite> --id=<id>` splits a composite result (one backend call that
ran several data calls, `kabo-composite-fetch.v1`) into one staged envelope per call. This script
copies those staged envelopes, byte for byte, to the files the next steps read, so nobody counts
pages or types a destination per call:

    place_composite.py accounts [--dir snapshot]
        collect_account_history -> <dir>/envelope-NN.json, numbering after the files already there
    place_composite.py pages --dir <task>/snapshot
        render_official_pages   -> <dir>/<source_id>.envelope.json

It takes the newest record of that tool this run has not placed yet, places each call whose status
is completed or completed_partial, and prints what it placed, what it skipped (with the status) and
the result's own summary (per account or page: resolved, needs_identity with candidates, ...), as one
JSON object. Nothing is fetched here and no envelope is edited. Standard library only.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

from static_host import composite_records, run_dir  # noqa: E402

USABLE = {"completed", "completed_partial"}
TOOLS = {"accounts": "collect_account_history", "pages": "render_official_pages"}
LEDGER = "analysis/.placed-composites.json"
NUMBERED = re.compile(r"^envelope-(\d+)\.json$")
SOURCE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def inside(run: Path, path: Path) -> Path:
    resolved = path.resolve()
    if run != resolved and run not in resolved.parents:
        raise ValueError(f"{path} is outside this run")
    return resolved


def next_number(directory: Path) -> int:
    numbers = [int(match.group(1)) for path in directory.glob("envelope-*.json")
               if (match := NUMBERED.match(path.name))]
    return max(numbers, default=0) + 1


def page_sources(record: dict) -> dict[str, str]:
    pages = (record.get("summary") or {}).get("pages") or []
    sources = {}
    for page in pages:
        if isinstance(page, dict) and isinstance(page.get("group"), str) and isinstance(page.get("source_id"), str):
            sources[page["group"]] = page["source_id"]
    return sources


def place(kind: str, directory: Path) -> dict:
    run = run_dir().resolve()
    tool = TOOLS[kind]
    ledger_path = run / LEDGER
    placed_ids = set(json.loads(ledger_path.read_text())) if ledger_path.is_file() else set()
    records = [record for record in composite_records(tool) if record["id"] not in placed_ids]
    if not records:
        raise ValueError(f"no fetched {tool} result is waiting in this run: make the {tool} call, then run "
                         f"static_host.py fetch --tool {tool} --id=<result id> first")
    record = records[-1]
    target = inside(run, directory)
    target.mkdir(parents=True, exist_ok=True)
    sources = page_sources(record) if kind == "pages" else {}
    number = next_number(target)
    placed, skipped = [], []
    for call in record["calls"]:
        entry = {"tool": call["tool"], "group": call.get("group"), "status": call.get("status")}
        if call.get("status") not in USABLE:
            skipped.append(entry)
            continue
        if kind == "pages":
            source_id = sources.get(str(call.get("group")))
            if not source_id or not SOURCE_ID.match(source_id):
                skipped.append({**entry, "reason": "page has no source_id in the call"})
                continue
            dest = target / f"{source_id}.envelope.json"
        else:
            dest = target / f"envelope-{number:02d}.json"
            number += 1
        if dest.exists():
            raise ValueError(f"{dest.relative_to(run)} already exists; nothing after it was placed")
        dest.write_bytes(Path(call["path"]).read_bytes())
        placed.append({**entry, "file": str(dest.relative_to(run))})
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(json.dumps(sorted(placed_ids | {record["id"]})))
    return {"status": "ok", "tool": tool, "result_status": record.get("status"), "placed": placed,
            "skipped": skipped, "summary": record.get("summary"), "limitations": record.get("limitations") or []}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("kind", choices=sorted(TOOLS))
    parser.add_argument("--dir", type=Path, default=Path("snapshot"),
                        help="destination directory inside the run (default: snapshot)")
    args = parser.parse_args()
    try:
        result = place(args.kind, args.dir)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
