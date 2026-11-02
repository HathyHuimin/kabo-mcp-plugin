#!/usr/bin/env python3
"""Bound the public evidence that enters reach diagnosis without altering raw Envelopes."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != "public-content-snapshot.v1":
        raise ValueError("expected public-content-snapshot.v1")
    return value


def bounded(snapshot: dict, limit: int, per_format: int | None) -> dict:
    rows = snapshot.get("items")
    if not isinstance(rows, list):
        raise ValueError("snapshot items must be an array")
    required = ("content_id", "creator_id", "published_at", "format")
    validated = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"snapshot items[{index}] must be an object")
        for field in required:
            value = row.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"snapshot items[{index}].{field} must be a non-empty string"
                )
        validated.append(row)
    ranked = sorted(
        validated,
        key=lambda row: (row["published_at"], row["content_id"]),
        reverse=True,
    )
    if per_format is not None:
        counts: dict[str, int] = {}
        selected = []
        for row in ranked:
            format_name = str(row.get("format") or "unknown")
            if counts.get(format_name, 0) >= per_format:
                continue
            counts[format_name] = counts.get(format_name, 0) + 1
            selected.append(row)
    else:
        selected = ranked
    selected = selected[:limit]
    kept_ids = {str(row.get("creator_id")) for row in selected}
    snapshot["items"] = sorted(
        selected,
        key=lambda row: (str(row.get("published_at") or ""), str(row.get("content_id") or "")),
    )
    creators = snapshot.get("creators")
    if isinstance(creators, list):
        snapshot["creators"] = [
            row for row in creators
            if isinstance(row, dict) and str(row.get("creator_id")) in kept_ids
        ]
    removed = len(ranked) - len(selected)
    source = snapshot.setdefault("source", {})
    if not isinstance(source, dict):
        raise ValueError("snapshot source must be an object")
    pagination = source.setdefault("pagination", {})
    if not isinstance(pagination, dict):
        raise ValueError("snapshot source.pagination must be an object")
    pagination["pre_cap_normalized_items"] = len(ranked)
    pagination["normalized_items"] = len(selected)
    pagination["analysis_sample_limit"] = limit
    if per_format is not None:
        pagination["analysis_per_format_limit"] = per_format
    if removed:
        source["coverage_complete"] = False
        limitations = source.setdefault("limitations", [])
        if not isinstance(limitations, list):
            raise ValueError("snapshot source.limitations must be an array")
        limitations.append(
            f"Reach diagnosis used the latest {len(selected)} of {len(ranked)} normalized public items"
            + (f", with at most {per_format} per format" if per_format is not None else "")
            + "."
        )
    identity = "\n".join(str(row.get("content_id") or "") for row in snapshot["items"])
    snapshot["snapshot_id"] = "public-content-snapshot-" + hashlib.sha256(
        f"{snapshot.get('snapshot_id')}:{identity}".encode("utf-8")
    ).hexdigest()[:16]
    return snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--per-format", type=int)
    args = parser.parse_args()
    try:
        if args.limit < 1 or args.limit > 100:
            raise ValueError("--limit must be between 1 and 100")
        if args.per_format is not None and not 1 <= args.per_format <= args.limit:
            raise ValueError("--per-format must be between 1 and --limit")
        value = bounded(load(args.source), args.limit, args.per_format)
        args.output.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        return 0
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
