#!/usr/bin/env python3
"""Pick a bounded watch set from a finished AccountAnalysis. Does not fetch media."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.dont_write_bytecode = True
from run_paths import missing_input, resolve_input  # noqa: E402

SCHEMA_VERSION = "watch-set.v1"
WATCHABLE_FORMATS = frozenset(
    {"reel", "tiktok_video", "youtube_short", "youtube_long"}
)
# Working default 10; product ceiling 50. Catalog Review already stops at 100.
DEFAULT_CAP = 10
MAX_CAP = 50
# First pass per watchable format, then fill remaining extremes up to the ceiling.
DEFAULT_HIGH_SLOTS = 3
DEFAULT_LOW_SLOTS = 2
IG_RESERVED = frozenset(
    {"p", "reel", "reels", "stories", "explore", "tv", "accounts", "direct"}
)


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must be a JSON object")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _row_from_ranking(row: dict[str, Any]) -> dict[str, Any]:
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    return {
        "content_id": str(row.get("content_id") or "").strip(),
        "url": str(row.get("url") or "").strip(),
        "format": str(row.get("format") or "").strip(),
        "views": _as_float(metrics.get("views")),
        "view_multiple": _as_float(row.get("view_multiple_vs_format_median")),
        "rank_by_views": row.get("rank_by_views")
        if isinstance(row.get("rank_by_views"), int)
        else None,
        "title": row.get("title") if isinstance(row.get("title"), str) else None,
        "published_at": row.get("published_at")
        if isinstance(row.get("published_at"), str)
        else None,
    }


def index_rankings(analysis: dict[str, Any]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for raw in analysis.get("rankings") or []:
        if not isinstance(raw, dict):
            continue
        row = _row_from_ranking(raw)
        if row["content_id"]:
            indexed[row["content_id"]] = row
    return indexed


def handle_from_url(url: str) -> str | None:
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    parts = [part for part in parsed.path.split("/") if part]
    if not parts:
        return None
    if "instagram.com" in host:
        if parts[0].lower() in IG_RESERVED:
            return None
        if len(parts) >= 2 and parts[1].lower() in {"p", "reel", "reels"}:
            return parts[0].lstrip("@").lower()
        return None
    if "tiktok.com" in host:
        first = parts[0]
        if first.startswith("@"):
            return first[1:].lower()
        return None
    return None


def account_handle(analysis: dict[str, Any]) -> str:
    scope = analysis.get("scope") if isinstance(analysis.get("scope"), dict) else {}
    return str(scope.get("account") or analysis.get("account") or "").strip().lstrip("@").lower()


def is_other_handle(row: dict[str, Any], account: str) -> bool:
    if not account:
        return False
    handle = handle_from_url(row["url"])
    return bool(handle and handle != account)


def watchable(row: dict[str, Any]) -> str | None:
    if row["format"] not in WATCHABLE_FORMATS:
        return "not_watchable_format"
    if not row["url"]:
        return "missing_url"
    return None


def _high_key(row: dict[str, Any]) -> tuple:
    multiple = row["view_multiple"]
    views = row["views"]
    return (
        multiple is None,
        -(multiple if multiple is not None else -1.0),
        views is None,
        -(views if views is not None else -1.0),
        row["content_id"],
    )


def _low_key(row: dict[str, Any]) -> tuple:
    multiple = row["view_multiple"]
    views = row["views"]
    return (
        multiple is None,
        multiple if multiple is not None else float("inf"),
        views is None,
        views if views is not None else float("inf"),
        row["content_id"],
    )


def _item(row: dict[str, Any], role: str) -> dict[str, Any]:
    return {
        "content_id": row["content_id"],
        "url": row["url"],
        "format": row["format"],
        "role": role,
        "views": row["views"],
        "view_multiple": row["view_multiple"],
        "rank_by_views": row["rank_by_views"],
        "title": row["title"],
        "published_at": row["published_at"],
    }


def _take(
    selected: list[dict[str, Any]],
    chosen: set[str],
    row: dict[str, Any],
    role: str,
    cap: int,
) -> bool:
    if len(selected) >= cap or row["content_id"] in chosen:
        return False
    selected.append(_item(row, role))
    chosen.add(row["content_id"])
    return True


def select_watch_set(
    analysis: dict[str, Any],
    *,
    named_ids: list[str] | None = None,
    cap: int = DEFAULT_CAP,
    high_slots: int = DEFAULT_HIGH_SLOTS,
    low_slots: int = DEFAULT_LOW_SLOTS,
) -> dict[str, Any]:
    if cap < 1:
        raise ValueError("cap must be >= 1")
    if high_slots < 0 or low_slots < 0:
        raise ValueError("high_slots and low_slots must be >= 0")
    cap = min(cap, MAX_CAP)

    indexed = index_rankings(analysis)
    selected: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    chosen: set[str] = set()
    seen_named: set[str] = set()

    for raw_id in named_ids or []:
        content_id = str(raw_id or "").strip()
        if not content_id:
            continue
        if content_id in seen_named:
            skipped.append({"content_id": content_id, "reason": "duplicate_named"})
            continue
        seen_named.add(content_id)
        row = indexed.get(content_id)
        if row is None:
            skipped.append({"content_id": content_id, "reason": "not_in_analysis"})
            continue
        reason = watchable(row)
        if reason:
            skipped.append({"content_id": content_id, "reason": reason})
            continue
        _take(selected, chosen, row, "named", cap)

    remaining = [row for row in indexed.values() if row["content_id"] not in chosen]
    watchable_rows = [row for row in remaining if watchable(row) is None]
    account = account_handle(analysis)

    other_pool = [row for row in watchable_rows if is_other_handle(row, account)]
    if other_pool:
        _take(selected, chosen, sorted(other_pool, key=_high_key)[0], "other_handle", cap)

    def unused() -> list[dict[str, Any]]:
        return [row for row in watchable_rows if row["content_id"] not in chosen]

    for fmt in sorted({row["format"] for row in unused()}):
        pool = [row for row in unused() if row["format"] == fmt]
        if pool:
            _take(selected, chosen, sorted(pool, key=_high_key)[0], "high", cap)
        low_pool = [
            row
            for row in unused()
            if row["format"] == fmt and row["views"] is not None
        ]
        if low_pool:
            _take(selected, chosen, sorted(low_pool, key=_low_key)[0], "low", cap)

    while len(selected) < cap:
        pool = unused()
        if not pool:
            break
        _take(selected, chosen, sorted(pool, key=_high_key)[0], "high", cap)
        if len(selected) >= cap:
            break
        low_pool = [row for row in unused() if row["views"] is not None]
        if low_pool:
            _take(selected, chosen, sorted(low_pool, key=_low_key)[0], "low", cap)
        elif unused():
            _take(selected, chosen, sorted(unused(), key=_high_key)[0], "high", cap)
        else:
            break

    empty_reason = "no_watchable_videos" if not selected else None
    return {
        "schema_version": SCHEMA_VERSION,
        "analysis_id": str(analysis.get("analysis_id") or "").strip(),
        "platform": str(analysis.get("platform") or "").strip(),
        "cap": cap,
        "high_slots": high_slots,
        "low_slots": low_slots,
        "selected": selected,
        "skipped": skipped,
        "empty_reason": empty_reason,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("analysis", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--named-content-id", action="append", default=[])
    parser.add_argument(
        "--cap",
        type=int,
        default=DEFAULT_CAP,
        help="watch size (default 10, max 50)",
    )
    parser.add_argument("--high-slots", type=int, default=DEFAULT_HIGH_SLOTS)
    parser.add_argument("--low-slots", type=int, default=DEFAULT_LOW_SLOTS)
    args = parser.parse_args(argv)

    try:
        analysis = load_json(resolve_input(args.analysis))
    except FileNotFoundError as missing:
        return missing_input(missing)
    payload = select_watch_set(
        analysis,
        named_ids=args.named_content_id,
        cap=args.cap,
        high_slots=args.high_slots,
        low_slots=args.low_slots,
    )
    if not payload["analysis_id"]:
        raise ValueError("account_analysis.json is missing analysis_id")
    write_json(args.output, payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
