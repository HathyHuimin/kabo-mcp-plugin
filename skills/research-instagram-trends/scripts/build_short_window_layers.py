#!/usr/bin/env python3
"""Split collected items into heat (any dated) vs exact UTC window. Do not invent dates."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import short_window_topic_gate as topic_gate


PLACEMENT = "python3 <skill directory>/scripts/persist_envelope.py --into short-window-input.json --at runs --at <intent> --connector instagram-baseline-proxy --operation search_reels"
CORRECTION_MARK = ".short-window-placement-correction"


class MissingPlacement(ValueError):
    """Nothing placed yet: an authoring-order slip, not a failed or empty source."""


def parse_time(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        stamp = float(value)
        if stamp > 1e12:
            stamp /= 1000
        return datetime.fromtimestamp(stamp, tz=timezone.utc)
    if isinstance(value, str) and value.isdigit():
        return parse_time(int(value))
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc)
    return None


def iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def as_record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def has_source_collection(payload: Any) -> bool:
    """Require an actual supported collection; failed responses are not empty searches."""
    if not isinstance(payload, dict):
        return False
    statuses = ("completed", "partial") if payload.get("schema_version") == "trend-candidate-set.v1" else ("completed", "completed_partial")
    if "status" in payload and payload["status"] not in statuses:
        raise ValueError("short-window source is failed or unavailable, not a completed empty search")
    children = [has_source_collection(value) for value in as_record(payload.get("runs")).values()]
    if isinstance(payload.get("items"), list):
        return True
    data = as_record(payload.get("data"))
    if any(isinstance(data.get(key), list) for key in ("items", "rows")):
        return True
    return any(children)


def load_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(payload.get("items"), list):
        return [row for row in payload["items"] if isinstance(row, dict)]
    data = as_record(payload.get("data"))
    # A persisted instagram search_reels Envelope keeps its reels in data.rows.
    for key in ("items", "rows"):
        if isinstance(data.get(key), list):
            return [row for row in data[key] if isinstance(row, dict)]
    runs = as_record(payload.get("runs"))
    out: list[dict[str, Any]] = []
    for envelope in runs.values():
        if not isinstance(envelope, dict):
            continue
        out.extend(load_items(envelope))
    return out


def normalize(row: dict[str, Any]) -> dict[str, Any] | None:
    published = parse_time(
        row.get("published_at")
        or row.get("posted_at")
        or row.get("taken_at")
        or row.get("create_time")
    )
    # Raw Instagram reels (search_reels rows) carry a shortcode, not a URL.
    code = str(row.get("code") or "").strip()
    url = str(row.get("url") or row.get("post_link") or row.get("source_url") or "").strip()
    if not url and code:
        url = f"https://www.instagram.com/reel/{code}/"
    content_id = str(row.get("content_id") or code or row.get("id") or row.get("video_id") or "").strip()
    if not url and not content_id:
        return None
    author = as_record(row.get("author"))
    handle = str(
        row.get("handle")
        or row.get("creator_handle")
        or author.get("handle")
        or as_record(row.get("user")).get("username")
        or as_record(row.get("owner")).get("username")
        or ""
    ).lstrip("@")
    metrics = as_record(row.get("metrics"))
    # First field present, not first truthy: an explicit 0 plays is a count, not a gap.
    views = next(
        (
            value
            for value in (row.get("views"), metrics.get("views"), metrics.get("play_count"), row.get("ig_play_count"), row.get("play_count"))
            if value is not None
        ),
        None,
    )
    if type(views) is not int or views < 0:
        views = None
    caption = row.get("caption")
    if isinstance(caption, dict):
        caption = caption.get("text")
    return {
        "content_id": content_id or url,
        "url": url,
        "handle": handle,
        "title": str(row.get("title") or caption or row.get("description") or ""),
        "published_at": iso(published) if published else None,
        "views": views,
    }


def build(payload: dict[str, Any], gate_input: dict[str, Any] | None = None,
          topic_decisions: Any = None) -> dict[str, Any]:
    window = as_record(payload.get("window"))
    since = parse_time(window.get("since"))
    until = parse_time(window.get("until"))
    if since is None or until is None or since > until:
        raise ValueError("window.since and window.until must be timezone-aware and ordered")
    platform = str(payload.get("platform") or "").strip()
    if platform not in ("tiktok", "instagram", "youtube"):
        raise ValueError("platform must be tiktok, instagram or youtube")
    # Missing source evidence is not proof of a completed empty search.
    if not has_source_collection(payload):
        raise MissingPlacement("short-window input has no completed source collection; place the completed responses before building layers")
    weekly = payload.get("source_policy") == "instagram-week-search.v1"
    if payload.get("source_policy") not in (None, "instagram-week-search.v1"):
        raise ValueError("unsupported short-window source_policy")
    if weekly:
        if platform != "instagram" or until - since != timedelta(days=7):
            raise ValueError("instagram-week-search.v1 requires an exact seven-day Instagram window")
        runs = as_record(payload.get("runs"))
        if len(runs) != 1:
            raise ValueError("weekly Search requires one complete collector result")
        source = next(iter(runs.values()))
        if not isinstance(source, dict) or source.get("schema_version") != "trend-candidate-set.v1" or source.get("window") != "last-week":
            raise ValueError("weekly Search requires a last-week trend-candidate-set.v1")
        if any(route.get("route") != "search" for item in source.get("items", []) for route in item.get("sources", [])):
            raise ValueError("weekly Search cannot mix Popular or unbounded Reels search")
        if gate_input is None:
            raise ValueError("weekly Search requires topic input and complete topic decisions")
    if (gate_input is None) != (topic_decisions is None):
        raise ValueError("topic input and decisions must be provided together")
    eligible, audit = topic_gate.apply(payload, gate_input, topic_decisions) if gate_input is not None else (None, None)
    heat: list[dict[str, Any]] = []
    in_window: list[dict[str, Any]] = []
    seen: set[str] = set()
    undated = 0
    unverified = 0
    outside = 0
    for raw in load_items(payload):
        item = normalize(raw)
        if item is None:
            continue
        key = topic_gate.key(item)
        if key in seen:
            continue
        seen.add(key)
        if eligible is not None and key not in eligible:
            continue
        if weekly and (as_record(raw.get("availability")).get("detail_status") != "completed" or item["views"] is None):
            unverified += 1
            continue
        published = parse_time(item["published_at"])
        if published is None:
            undated += 1
            continue
        item["in_window"] = since <= published <= until
        if weekly and not item["in_window"]:
            outside += 1
            continue
        if item["in_window"]:
            in_window.append(item)
        if platform == "instagram" or until - timedelta(days=30) <= published <= until:
            heat.append(item)
    heat.sort(key=lambda row: (-(row["views"] or 0), row["published_at"] or ""))
    in_window.sort(key=lambda row: (-(row["views"] or 0), row["published_at"] or ""))
    return {
        "schema_version": "short-window-layers.v1",
        "platform": platform,
        "window": {"since": iso(since), "until": iso(until)},
        "heat": heat,
        "in_window": in_window,
        **({"topic_gate": audit} if audit is not None else {}),
        **({"source_policy": payload["source_policy"], "limitations": [
            "Search uses an indexing window; only candidates with completed post details and known plays are ranked.",
            *([f"{unverified} on-topic candidates lack completed post details or plays and were not ranked."] if unverified else []),
            *([f"{outside} on-topic candidates fall outside the exact requested window and were not ranked."] if outside else []),
            *source.get("limitations", []),
        ]} if weekly else {}),
        "coverage": {
            "heat_count": len(heat),
            "in_window_count": len(in_window),
            "undated_count": undated,
            **({"unverified_count": unverified} if weekly else {}),
            **({"outside_window_count": outside} if weekly else {}),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--topic-gate-input", type=Path)
    parser.add_argument("--topic-decisions", type=Path)
    args = parser.parse_args()
    try:
        if bool(args.topic_gate_input) != bool(args.topic_decisions):
            raise ValueError("provide both --topic-gate-input and --topic-decisions")
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        result = build(payload,
                       json.loads(args.topic_gate_input.read_text(encoding="utf-8")) if args.topic_gate_input else None,
                       json.loads(args.topic_decisions.read_text(encoding="utf-8")) if args.topic_decisions else None)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"status": "ok", "heat": result["coverage"]["heat_count"], "in_window": result["coverage"]["in_window_count"]}))
        return 0
    except MissingPlacement as error:
        mark = args.input.with_name(CORRECTION_MARK)
        if mark.exists():
            print(json.dumps({"status": "failed", "error": f"{type(error).__name__}: {error} (after its one allowed correction)"}))
            return 2
        mark.write_text(str(error) + "\n", encoding="utf-8")
        print(json.dumps({
            "status": "needs_correction", "reason": "no_placed_collection", "errors": [str(error)],
            "placement_command": ("python3 <skill directory>/scripts/persist_envelope.py --into short-window-input.json --at runs --at week --schema trend-candidate-set.v1"
                                  if payload.get("source_policy") == "instagram-week-search.v1" else PLACEMENT), "corrections_remaining": 0,
            "next_stage": {"script": "scripts/build_short_window_layers.py", "arguments": sys.argv[1:]},
            "instruction": "Place every completed response this run already fetched with placement_command, one per "
                           "response at its own slot, as its own non-final stage in this same open run. "
                           "Do not fetch again, edit short-window-input.json by hand or replace the run; then repeat this "
                           "build stage once.",
        }, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "failed", "error": f"{type(error).__name__}: {error}"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
