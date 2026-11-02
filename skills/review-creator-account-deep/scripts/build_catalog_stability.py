#!/usr/bin/env python3
"""Catalog-only stability and format-share facts from a finished AccountAnalysis.

Does not watch videos and does not change review-creator-account. Numbers here
are the only catalog figures Deep may quote for follower-driven vs recommendation
volatility, hidden likes, and format rise/fade.
"""

from __future__ import annotations

import argparse
import math
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.dont_write_bytecode = True
from run_paths import missing_input, resolve_input  # noqa: E402
from media_payload import load_json, write_json

SCHEMA_VERSION = "catalog-stability.v1"
FOLLOWER_DRIVEN = 0.7
RECOMMENDATION_DRIVEN = 0.15
SHARE_DELTA = 0.05
IG_RESERVED = frozenset(
    {"p", "reel", "reels", "stories", "explore", "tv", "accounts", "direct"}
)


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    return None


def _parse_time(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _percentile(sorted_values: list[float], pct: float) -> float | None:
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    index = (len(sorted_values) - 1) * pct
    low = math.floor(index)
    high = math.ceil(index)
    if low == high:
        return sorted_values[low]
    weight = index - low
    return sorted_values[low] * (1.0 - weight) + sorted_values[high] * weight


def _round(value: float | None, digits: int = 6) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


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


def _metrics(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("metrics")
    return raw if isinstance(raw, dict) else {}


def _view_rows(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in analysis.get("rankings") or []:
        if not isinstance(raw, dict):
            continue
        views = _as_float((_metrics(raw)).get("views"))
        if views is None:
            continue
        rows.append(
            {
                "content_id": str(raw.get("content_id") or "").strip(),
                "format": str(raw.get("format") or "").strip() or "unknown",
                "url": str(raw.get("url") or "").strip(),
                "views": views,
                "likes": _as_float((_metrics(raw)).get("likes")),
                "comments": _as_float((_metrics(raw)).get("comments")),
                "published_at": _parse_time(raw.get("published_at")),
            }
        )
    return rows


def _format_share(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    totals: dict[str, dict[str, float]] = {}
    for row in rows:
        bucket = totals.setdefault(row["format"], {"n": 0.0, "views": 0.0})
        bucket["n"] += 1
        bucket["views"] += row["views"]
    all_views = sum(item["views"] for item in totals.values())
    out: list[dict[str, Any]] = []
    for name, bucket in sorted(totals.items()):
        format_rows = [row["views"] for row in rows if row["format"] == name]
        out.append(
            {
                "format": name,
                "n": int(bucket["n"]),
                "view_share": _round(bucket["views"] / all_views if all_views else None),
                "median_views": _round(statistics.median(format_rows) if format_rows else None),
            }
        )
    return out


def _period_status(earlier: float | None, later: float | None) -> str:
    if earlier is None or later is None:
        return "unknown"
    delta = later - earlier
    if delta >= SHARE_DELTA:
        return "rise"
    if delta <= -SHARE_DELTA:
        return "fade"
    return "stable"


def build_catalog_stability(analysis: dict[str, Any]) -> dict[str, Any]:
    scope = analysis.get("scope") if isinstance(analysis.get("scope"), dict) else {}
    account = str(scope.get("account") or analysis.get("account") or "").strip().lstrip("@").lower()
    followers = _as_float(scope.get("followers"))
    rows = _view_rows(analysis)
    views = sorted(row["views"] for row in rows)
    median_views = statistics.median(views) if views else None
    p10 = _percentile(views, 0.10)
    p90 = _percentile(views, 0.90)
    top_n = max(1, math.ceil(len(views) * 0.10)) if views else 0
    top_share = (
        sum(sorted(views, reverse=True)[:top_n]) / sum(views) if views and sum(views) else None
    )
    hidden_likes_n = sum(1 for row in rows if row["likes"] == -1.0)
    dated = [row for row in rows if row["published_at"] is not None]
    dated.sort(key=lambda row: row["published_at"] or datetime.min.replace(tzinfo=timezone.utc))
    span_days = None
    monthly_count = None
    if len(dated) >= 2 and dated[0]["published_at"] and dated[-1]["published_at"]:
        span_days = max(
            1.0, (dated[-1]["published_at"] - dated[0]["published_at"]).total_seconds() / 86400.0
        )
        monthly_count = _round(len(dated) / (span_days / 30.4375))
    midpoint = len(dated) // 2
    earlier = dated[:midpoint]
    later = dated[midpoint:]
    earlier_share = {row["format"]: row["view_share"] for row in _format_share(earlier)}
    later_share = {row["format"]: row["view_share"] for row in _format_share(later)}
    formats = sorted({row["format"] for row in dated})
    rise_fade = []
    for name in formats:
        earlier_n = sum(1 for row in earlier if row["format"] == name)
        later_n = sum(1 for row in later if row["format"] == name)
        status = (
            _period_status(earlier_share.get(name), later_share.get(name))
            if earlier_n >= 3 and later_n >= 3
            else "thin"
        )
        rise_fade.append(
            {
                "format": name,
                "earlier_n": earlier_n,
                "later_n": later_n,
                "earlier_view_share": earlier_share.get(name),
                "later_view_share": later_share.get(name),
                "view_share_delta": _round(
                    (later_share[name] - earlier_share[name])
                    if name in earlier_share and name in later_share
                    else None
                ),
                "status": status,
            }
        )
    other_handle_n = 0
    if account:
        for row in rows:
            handle = handle_from_url(row["url"])
            if handle and handle != account:
                other_handle_n += 1
    ratio = (
        _round(median_views / followers)
        if median_views is not None and followers and followers > 0
        else None
    )
    readings: list[str] = []
    if ratio is None:
        readings.append("Median views and follower count are not both available, so the views-per-follower ratio cannot be used to assess stability.")
    elif ratio >= FOLLOWER_DRIVEN:
        readings.append(
            f"Median views / followers = {ratio}, near or above 0.7. The heuristic suggests follower-driven viewing and more consistent performance across posts; it does not identify actual viewers or traffic sources."
        )
    elif ratio <= RECOMMENDATION_DRIVEN:
        readings.append(
            f"Median views / followers = {ratio}, near or below 0.15. The heuristic suggests occasional recommendation-driven hits and more variable performance; it does not identify actual viewers or traffic sources."
        )
    else:
        readings.append(
            f"Median views / followers = {ratio}, between the heuristic's follower-driven and recommendation-driven ranges. Follower count alone does not explain the variation."
        )
    if top_share is not None and top_share >= 0.5:
        readings.append(
            f"The top 10% of posts account for a { _round(top_share) } share of all views. A few posts dominate the total, so the views-per-follower ratio alone does not establish consistent performance."
        )
    if hidden_likes_n:
        readings.append(
            f"Likes are hidden (-1) on {hidden_likes_n} {'post' if hidden_likes_n == 1 else 'posts'}. Compare comments / views only; do not use engagement rates that include likes."
        )
    for row in rise_fade:
        if row["status"] in {"rise", "fade"}:
            readings.append(
                f"The view share for {row['format']} changed from {row['earlier_view_share']} to {row['later_view_share']}. It is classified as {'rising' if row['status'] == 'rise' else 'falling'} by view share, not by median views per post."
            )
    if other_handle_n:
        readings.append(
            f"{other_handle_n} {'post links' if other_handle_n == 1 else 'posts link'} to another account's profile (a collaboration or interview). High performance multiples may not come from this account's own profile."
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "analysis_id": str(analysis.get("analysis_id") or "").strip(),
        "account": account or None,
        "followers": _round(followers, 3),
        "view_n": len(views),
        "median_views": _round(median_views),
        "median_views_per_follower": ratio,
        "p10_views": _round(p10),
        "p90_views": _round(p90),
        "p90_over_p10": _round((p90 / p10) if p10 and p90 is not None and p10 > 0 else None),
        "top10_share": _round(top_share),
        "hidden_likes_n": hidden_likes_n,
        "other_handle_n": other_handle_n,
        "span_days": _round(span_days, 3),
        "monthly_count": monthly_count,
        "formats": _format_share(rows),
        "periods": {
            "earlier_n": len(earlier),
            "later_n": len(later),
        },
        "rise_fade": rise_fade,
        "readings": readings,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("account_analysis", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        source = resolve_input(args.account_analysis)
    except FileNotFoundError as missing:
        return missing_input(missing)
    payload = build_catalog_stability(load_json(source))
    if not payload["analysis_id"]:
        raise ValueError("account_analysis.json is missing analysis_id")
    write_json(args.output, payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
