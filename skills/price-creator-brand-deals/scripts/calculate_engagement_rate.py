#!/usr/bin/env python3
"""Calculate deterministic engagement rates for Account Review narrow tasks."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Sibling imports must not leave a __pycache__ in the signed, checksummed skill directory.
sys.dont_write_bytecode = True
from json_io import load_complete_envelope, load_complete_json


SCHEMA_VERSION = "engagement-rate.v1"
RULE_VERSION = "engagement-rate-rules.v1"
MAX_ANALYZED_POSTS = 50

MANUAL_BANDS = {
    "instagram": [
        (6.0, "exceptional", "Exceptional (6%+)"),
        (3.0, "strong", "Strong (3-6%)"),
        (1.0, "solid", "Solid (1-3%)"),
        (0.0, "below_typical", "Below typical (under 1%)"),
    ],
    "tiktok": [
        (10.0, "exceptional", "Exceptional (10%+)"),
        (5.0, "strong", "Strong (5-10%)"),
        (2.0, "solid", "Solid (2-5%)"),
        (0.0, "below_typical", "Below typical (under 2%)"),
    ],
}

MANUAL_PLATFORMS = frozenset((*MANUAL_BANDS, "youtube"))

YOUTUBE_VIEW_BANDS = [
    (5.0, "excellent", "Excellent (above 5%)"),
    (2.0, "good", "Good (2-5%)"),
    (0.0, "needs_work", "Needs work (under 2%)"),
]
YOUTUBE_VIEW_BAND_LIMITATION = (
    "YouTube letter grades use a published True Engagement Rate band on the view rate only. "
    "Do not apply those bands to the subscriber rate. "
    "Our formula is likes plus comments; that published band also counts shares."
)

INSTAGRAM_PROFILE_BENCHMARKS = [
    (5_000, "Under 5K followers", {"high": 7.97, "above_average": 4.42, "average": 3.38, "below_average": 1.72}),
    (10_000, "5K–10K followers", {"high": 3.54, "above_average": 1.62, "average": 1.25, "below_average": 0.62}),
    (50_000, "10K–50K followers", {"high": 2.61, "above_average": 1.11, "average": 0.84, "below_average": 0.39}),
    (100_000, "50K–100K followers", {"high": 2.47, "above_average": 1.01, "average": 0.75, "below_average": 0.32}),
    (500_000, "100K–500K followers", {"high": 2.63, "above_average": 1.14, "average": 0.87, "below_average": 0.41}),
    (math.inf, "500K+ followers", {"high": 2.46, "above_average": 1.13, "average": 0.88, "below_average": 0.46}),
]

FOLLOWER_BAND_FLOOR = 1_000
SMALL_FOLLOWER_LIMITATION = (
    "Follower-rate bands are not comparable below 1,000 followers: "
    "the small denominator inflates the percentage. "
    "Do not use the follower band as a cross-account verdict."
)
SENSITIVITY_LIMITATION = (
    "The default view rate uses the full sample. "
    "A rate after dropping the highest-play post is sensitivity only."
)


def load(path: Path) -> Any:
    return load_complete_json(path)


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def array(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def value_at(source: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = source
    for segment in path:
        current = record(current).get(segment)
    return current


def public_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) and value >= 0 else None
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip().upper().replace(",", "")
    factor = 1.0
    if raw[-1:] in {"K", "M", "B"}:
        factor = {"K": 1_000.0, "M": 1_000_000.0, "B": 1_000_000_000.0}[raw[-1]]
        raw = raw[:-1]
    try:
        parsed = float(raw) * factor
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) and parsed >= 0 else None


def first_number(source: dict[str, Any], paths: list[tuple[str, ...]]) -> float | None:
    for path in paths:
        parsed = public_number(value_at(source, path))
        if parsed is not None:
            return parsed
    return None


def first_text(source: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def round_percent(value: float) -> float:
    return round(value + 1e-12, 4)


def rate(interactions: float, denominator: float | None) -> float | None:
    return round_percent(interactions / denominator * 100) if denominator and denominator > 0 else None


def band_payload(basis: str, bands: list[tuple[float, str, str]], score: float) -> dict[str, Any]:
    minimum, rating, label = next(row for row in bands if score >= row[0])
    return {
        "basis": basis,
        "tier": None,
        "rating": rating,
        "label": label,
        "thresholds_percent": [
            {"minimum": threshold, "rating": band_rating, "label": band_label}
            for threshold, band_rating, band_label in bands
        ],
        "matched_minimum_percent": minimum,
    }


def manual_benchmark(platform: str, follower_rate: float) -> dict[str, Any]:
    return band_payload("platform_wide_follower_bands", MANUAL_BANDS[platform], follower_rate)


def youtube_view_benchmark(view_rate: float | None) -> dict[str, Any]:
    if view_rate is None:
        raise ValueError("YouTube view ER band requires average views")
    return band_payload("youtube_view_engagement_rate", YOUTUBE_VIEW_BANDS, view_rate)


def instagram_profile_benchmark(followers: float, follower_rate: float) -> dict[str, Any]:
    _, tier, thresholds = next(row for row in INSTAGRAM_PROFILE_BENCHMARKS if followers < row[0])
    if follower_rate >= thresholds["high"]:
        rating = "high"
    elif follower_rate >= thresholds["above_average"]:
        rating = "above_average"
    elif follower_rate >= thresholds["average"]:
        rating = "average"
    elif follower_rate >= thresholds["below_average"]:
        rating = "below_average"
    else:
        rating = "low"
    labels = {
        "high": "High",
        "above_average": "Above average",
        "average": "Average",
        "below_average": "Below average",
        "low": "Low",
    }
    return {
        "basis": "instagram_follower_tier",
        "tier": tier,
        "rating": rating,
        "label": labels[rating],
        "thresholds_percent": [
            {"minimum": value, "rating": name, "label": labels[name]}
            for name, value in thresholds.items()
        ],
        "matched_minimum_percent": thresholds.get(rating),
    }


def follower_limitations(followers: float) -> list[str]:
    return [SMALL_FOLLOWER_LIMITATION] if followers < FOLLOWER_BAND_FLOOR else []


def view_rate_excluding_highest_play(calculable: list[dict[str, Any]]) -> tuple[float | None, int | None]:
    play_rows = [row for row in calculable if row.get("plays") is not None]
    if len(play_rows) < 2:
        return None, None
    highest = max(row["plays"] for row in play_rows)
    dropped = False
    remaining: list[dict[str, Any]] = []
    for row in calculable:
        if not dropped and row.get("plays") == highest:
            dropped = True
            continue
        remaining.append(row)
    remaining_calculable = [
        row for row in remaining
        if row["likes"] is not None and row["comments"] is not None
    ]
    play_values = [row["plays"] for row in remaining_calculable if row.get("plays") is not None]
    if len(remaining_calculable) < 2 or not play_values:
        return None, None
    likes = float(statistics.median(row["likes"] for row in remaining_calculable))
    comments = float(statistics.median(row["comments"] for row in remaining_calculable))
    return rate(likes + comments, round(statistics.mean(play_values))), len(remaining_calculable)


def result(
    *,
    platform: str,
    request_surface: str,
    input_mode: str,
    aggregation_method: str,
    sample_size: int | None,
    handle: str | None,
    followers: float,
    likes: float,
    comments: float,
    views: float | None,
    benchmark: dict[str, Any],
    limitations: list[str] | None = None,
    sensitivity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    interactions = likes + comments
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "rule_version": RULE_VERSION,
        "platform": platform,
        "request_surface": request_surface,
        "input_mode": input_mode,
        "aggregation_method": aggregation_method,
        "interaction_scope": ["likes", "comments"],
        "sample_size": sample_size,
        "account": {"handle": handle, "followers": followers},
        "metrics": {
            "likes": likes,
            "comments": comments,
            "views": views,
            "follower_engagement_rate_percent": rate(interactions, followers),
            "view_engagement_rate_percent": rate(interactions, views),
        },
        "benchmark": benchmark,
    }
    if limitations:
        payload["limitations"] = limitations
    if sensitivity:
        payload["sensitivity"] = sensitivity
    return payload


def calculate_manual(source: dict[str, Any]) -> dict[str, Any]:
    platform = str(source.get("platform", "")).strip().lower()
    if platform not in MANUAL_PLATFORMS:
        raise ValueError("manual platform must be instagram, tiktok, or youtube")
    followers = public_number(source.get("followers"))
    likes = public_number(source.get("average_likes"))
    comments = public_number(source.get("average_comments"))
    views = public_number(source.get("average_views"))
    if followers is None or followers <= 0:
        raise ValueError("followers must be greater than zero")
    if likes is None or comments is None:
        raise ValueError("average likes and average comments are required")
    follower_rate = rate(likes + comments, followers)
    assert follower_rate is not None
    surface = source.get("request_surface", "platform_calculator")
    if surface not in {"platform_calculator", "cross_platform_calculator"}:
        raise ValueError("request_surface must be platform_calculator or cross_platform_calculator")
    if platform == "youtube":
        limitations = [YOUTUBE_VIEW_BAND_LIMITATION, *follower_limitations(followers)]
        return result(
            platform=platform,
            request_surface=surface,
            input_mode="manual_averages",
            aggregation_method="average",
            sample_size=None,
            handle=None,
            followers=followers,
            likes=likes,
            comments=comments,
            views=views,
            benchmark=youtube_view_benchmark(rate(likes + comments, views)),
            limitations=limitations,
        )
    return result(
        platform=platform,
        request_surface=surface,
        input_mode="manual_averages",
        aggregation_method="average",
        sample_size=None,
        handle=None,
        followers=followers,
        likes=likes,
        comments=comments,
        views=views,
        benchmark=manual_benchmark(platform, follower_rate),
        limitations=follower_limitations(followers),
    )


def unwrap_data(value: Any) -> dict[str, Any]:
    source = record(value)
    data = record(source.get("data"))
    return data if data else source


def instagram_media(value: Any) -> dict[str, Any]:
    source = record(value)
    return record(source.get("media")) or record(source.get("node")) or source


def embedded_posts(profile: dict[str, Any]) -> list[Any]:
    for path in (
        ("edge_owner_to_timeline_media", "edges"),
        ("edge_felix_video_timeline", "edges"),
    ):
        rows = array(value_at(profile, path))
        if rows:
            return rows
    return []


def post_rows(value: Any) -> list[Any]:
    data = unwrap_data(value)
    for key in ("rows", "items", "posts"):
        rows = data.get(key)
        if isinstance(rows, list):
            return rows
    return []


def parse_post(value: Any) -> dict[str, Any] | None:
    item = instagram_media(value)
    if not item:
        return None
    post_id = first_text(item, ("code", "shortcode", "id", "pk"))
    likes = first_number(item, [
        ("like_count",), ("likes",), ("edge_liked_by", "count"), ("edge_media_preview_like", "count"),
    ])
    comments = first_number(item, [
        ("comment_count",), ("comments",), ("edge_media_to_comment", "count"),
    ])
    plays = first_number(item, [
        ("play_count",), ("view_count",), ("ig_play_count",), ("video_view_count",),
    ])
    return {"id": post_id, "likes": likes, "comments": comments, "plays": plays}


def calculate_instagram_public(profile_value: Any, post_values: list[Any]) -> dict[str, Any]:
    profile_data = unwrap_data(profile_value)
    profile = record(profile_data.get("profile")) or profile_data
    if profile.get("is_private") is True or profile.get("isPrivate") is True:
        raise ValueError("Instagram profile is private")
    followers = first_number(profile, [
        ("follower_count",), ("followers_count",), ("edge_followed_by", "count"), ("followers",), ("followerCount",),
    ])
    if followers is None or followers <= 0:
        raise ValueError("public Instagram profile has no usable follower count")
    handle = first_text(profile, ("username", "handle"))

    embedded = embedded_posts(profile)
    embedded_calculable = [
        row for row in (parse_post(value) for value in embedded)
        if row is not None and row["likes"] is not None and row["comments"] is not None
    ]
    embedded_has_plays = any(row["plays"] is not None for row in embedded_calculable)
    if post_values:
        raw_rows = []
        for value in post_values:
            raw_rows.extend(post_rows(value))
        raw_rows.extend(embedded)
    elif len(embedded_calculable) >= 6 and embedded_has_plays:
        raw_rows = embedded
    elif len(embedded_calculable) >= 6:
        raise ValueError(
            "public Instagram profile requires list_reels because embedded posts have no play counts"
        )
    else:
        raise ValueError(
            "public Instagram profile requires list_posts because it has fewer than 6 embedded calculable posts"
        )
    unique: dict[str, dict[str, Any]] = {}
    for raw in raw_rows:
        parsed = parse_post(raw)
        if parsed is None:
            continue
        key = parsed["id"] or f"row:{len(unique)}"
        if key not in unique:
            unique[key] = parsed
            continue
        existing = unique[key]
        for metric in ("likes", "comments", "plays"):
            if existing.get(metric) is None and parsed.get(metric) is not None:
                existing[metric] = parsed[metric]
    calculable = [
        row for row in list(unique.values())[:MAX_ANALYZED_POSTS]
        if row["likes"] is not None and row["comments"] is not None
    ]
    if not calculable:
        raise ValueError("public Instagram profile has no calculable posts")
    likes = float(statistics.median(row["likes"] for row in calculable))
    comments = float(statistics.median(row["comments"] for row in calculable))
    play_values = [row["plays"] for row in calculable if row["plays"] is not None]
    views = round(statistics.mean(play_values)) if play_values else None
    if views is None and not post_values:
        raise ValueError(
            "public Instagram profile requires list_reels because embedded posts have no play counts"
        )
    follower_rate = rate(likes + comments, followers)
    assert follower_rate is not None
    limitations = follower_limitations(followers)
    sensitivity = None
    excluded_rate, excluded_n = view_rate_excluding_highest_play(calculable)
    if excluded_rate is not None:
        limitations.append(SENSITIVITY_LIMITATION)
        sensitivity = {
            "view_engagement_rate_excluding_highest_play_percent": excluded_rate,
            "sample_size_excluding_highest_play": excluded_n,
        }
    return result(
        platform="instagram",
        request_surface="instagram_profile",
        input_mode="public_profile",
        aggregation_method="median",
        sample_size=len(calculable),
        handle=handle,
        followers=followers,
        likes=likes,
        comments=comments,
        views=views,
        benchmark=instagram_profile_benchmark(followers, follower_rate),
        limitations=limitations,
        sensitivity=sensitivity,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="?", type=Path, help="Manual average input JSON.")
    parser.add_argument("--profile-envelope", type=Path, help="Persisted Instagram resolve_profile Envelope.")
    parser.add_argument("--content-envelope", action="append", default=[], type=Path, help="Persisted Instagram list_posts Envelope; repeat for each page.")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.input and args.profile_envelope:
        parser.error("choose manual input or --profile-envelope, not both")
    if args.profile_envelope:
        value = calculate_instagram_public(
            load_complete_envelope(args.profile_envelope),
            [load_complete_envelope(path) for path in args.content_envelope],
        )
    elif args.input:
        value = calculate_manual(record(load(args.input)))
    else:
        parser.error("manual input or --profile-envelope is required")
    dump(args.output, value)


if __name__ == "__main__":
    main()
