#!/usr/bin/env python3
"""Derive a brand-deal request's account_facts from the Envelopes this run persisted.

The follower count places the tier, the engagement rate decides a premium and average views feed
the reach estimate. The model used to read them off a fenced rendering and do the arithmetic itself
- an average over whichever posts it happened to see, an engagement rate worked out by hand. Here
each is derived by one rule:

- followers: the profile's follower count (Instagram resolve_profile), the channel's subscriber
  count (YouTube get_channel, or the uploads response's channel block), follower_count on the
  account-post response (TikTok). A missing count is left out, never zero.
- average_views: the mean public view count of the most recent 12 posts that are at least two days
  old (YouTube uploads, TikTok posts). Younger posts are still collecting views; with fewer than
  three mature posts the figure is left out rather than drawn from one or two.
- engagement_rate_pct: the follower engagement rate calculate_engagement_rate.py produced for this
  account (the Account Review calculation), passed with --engagement-rate.

Everything returned is basis "retrieved". Standard library only.
"""

from __future__ import annotations

import argparse
import json
import statistics
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

USABLE = {"completed", "completed_partial"}
RECENT_POSTS = 12
MATURE_DAYS = 2
MIN_POSTS = 3


def record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value.strip().replace(",", "").isdigit():
        return float(value.strip().replace(",", ""))
    return None


def moment(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def load_envelopes(paths: list[Path]) -> list[dict[str, Any]]:
    envelopes = []
    for path in paths:
        document = record(json.loads(path.read_text(encoding="utf-8")))
        envelope = document if "connector_id" in document else record(document.get("envelope"))
        if not envelope.get("connector_id"):
            raise ValueError(f"{path} is not a connector envelope")
        envelopes.append(envelope)
    return envelopes


def of(envelopes: list[dict[str, Any]], connector: str, operation: str) -> list[dict[str, Any]]:
    """Usable Envelopes of one operation, latest retrieval first: a re-fetch supersedes an older copy,
    so a post both carry keeps its newer counts and a figure is dated by the newest read."""
    matched = [e for e in envelopes if e.get("connector_id") == connector and e.get("operation") == operation
               and e.get("status") in USABLE]
    oldest = datetime.min.replace(tzinfo=timezone.utc)
    return sorted(matched, key=lambda e: moment(when(e)) or oldest, reverse=True)


def when(envelope: dict[str, Any]) -> str | None:
    value = envelope.get("retrieved_at") or record(envelope.get("data")).get("retrieved_at")
    return str(value) if value else None


def bare(handle: Any) -> str:
    return str(handle or "").strip().lstrip("@").casefold()


def measured(value: float, retrieved_at: str | None) -> dict[str, Any]:
    return {"value": int(value) if float(value).is_integer() else round(value, 2), "basis": "retrieved",
            "retrieved_at": retrieved_at}


def unique_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per post. A second call with a higher limit returns the first call's posts again."""
    seen: set[str] = set()
    kept = []
    for row in rows:
        key = row.get("content_id") or row.get("id") or row.get("url") or row.get("source_url")
        key = str(key) if key else json.dumps(row, sort_keys=True)
        if key not in seen:
            seen.add(key)
            kept.append(row)
    return kept


def average_views(rows: list[dict[str, Any]], as_of: date) -> tuple[float | None, int]:
    cutoff = datetime(as_of.year, as_of.month, as_of.day, tzinfo=timezone.utc) - timedelta(days=MATURE_DAYS)
    mature = []
    for row in unique_rows(rows):
        published = moment(row.get("published_at"))
        views = number(record(row.get("metrics")).get("views"))
        if published is not None and published <= cutoff and views is not None:
            mature.append((published, views))
    mature.sort(key=lambda pair: pair[0], reverse=True)
    recent = [views for _, views in mature[:RECENT_POSTS]]
    if len(recent) < MIN_POSTS:
        return None, len(recent)
    return round(statistics.fmean(recent)), len(recent)


def derive(platform: str, envelopes: list[dict[str, Any]], as_of: date,
           engagement: dict[str, Any] | None = None) -> dict[str, Any]:
    facts: dict[str, Any] = {"platform": platform}
    method: dict[str, Any] = {}
    omitted: list[dict[str, str]] = []
    followers = None
    followers_at = None
    rows: list[dict[str, Any]] = []
    rows_at = None
    if platform == "instagram":
        for envelope in of(envelopes, "instagram-discovery", "resolve_profile"):
            profile = record(record(envelope.get("data")).get("profile"))
            followers = next((number(profile.get(k)) for k in ("follower_count", "followers_count")
                              if number(profile.get(k)) is not None), None)
            if followers is None:
                followers = number(record(profile.get("edge_followed_by")).get("count"))
            handle = profile.get("handle") or profile.get("username")
            if isinstance(handle, str) and handle:
                facts["handle"] = handle
            followers_at = when(envelope)
            if followers is not None:
                break
    elif platform == "youtube":
        for envelope in of(envelopes, "youtube-public", "get_channel"):
            row = record((record(envelope.get("data")).get("rows") or [{}])[0])
            followers = number(record(row.get("metrics")).get("subscribers"))
            followers_at = when(envelope)
            if followers is not None:
                break
        for envelope in of(envelopes, "youtube-public", "list_channel_uploads"):
            data = record(envelope.get("data"))
            rows.extend(r for r in data.get("rows") or [] if isinstance(r, dict))
            rows_at = rows_at or when(envelope)
            channel = record(data.get("channel"))
            if followers is None and number(record(channel.get("metrics")).get("subscribers")) is not None:
                followers, followers_at = number(record(channel.get("metrics")).get("subscribers")), when(envelope)
            if isinstance(channel.get("custom_url"), str):
                facts.setdefault("handle", channel["custom_url"].lstrip("@"))
    elif platform == "tiktok":
        for envelope in of(envelopes, "tiktok-public", "list_account_posts"):
            data = record(envelope.get("data"))
            rows.extend(r for r in data.get("rows") or [] if isinstance(r, dict))
            rows_at = rows_at or when(envelope)
            if followers is None:
                followers, followers_at = number(data.get("follower_count")), when(envelope)
            if isinstance(data.get("handle"), str):
                facts.setdefault("handle", data["handle"])
    else:
        raise ValueError(f"platform must be instagram, youtube or tiktok, got {platform!r}")

    if followers is not None:
        facts["followers"] = measured(followers, followers_at)
    else:
        omitted.append({"field": "followers", "reason": "no retrieved response carried a follower count"})
    if platform in ("youtube", "tiktok"):
        value, sample = average_views(rows, as_of)
        method["average_views"] = {"rule": f"mean of the {RECENT_POSTS} most recent posts at least "
                                           f"{MATURE_DAYS} days old, every public format together",
                                   "sample_size": sample}
        if value is not None:
            facts["average_views"] = measured(value, rows_at)
        else:
            omitted.append({"field": "average_views",
                            "reason": f"fewer than {MIN_POSTS} posts at least {MATURE_DAYS} days old"})
    if engagement is not None:
        if engagement.get("platform") != platform:
            raise ValueError(f"--engagement-rate was calculated for {engagement.get('platform')!r}, not {platform!r}")
        rated = record(engagement.get("account")).get("handle")
        if rated and facts.get("handle") and bare(rated) != bare(facts["handle"]):
            raise ValueError(f"--engagement-rate was calculated for @{bare(rated)}, not @{bare(facts['handle'])}")
        rate = number(record(engagement.get("metrics")).get("follower_engagement_rate_percent"))
        if rate is not None:
            facts["engagement_rate_pct"] = measured(rate, followers_at)
            method["engagement_rate_pct"] = {"rule": "calculate_engagement_rate.py follower rate",
                                             "sample_size": engagement.get("sample_size")}
        else:
            omitted.append({"field": "engagement_rate_pct", "reason": "the calculation returned no follower rate"})
    return {"schema_version": "account-facts.v1", "as_of": as_of.isoformat(), "account_facts": facts,
            "method": method, "omitted": omitted}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--platform", required=True, choices=["instagram", "youtube", "tiktok"])
    parser.add_argument("--envelope", type=Path, action="append", default=[], help="one persisted Envelope (repeat)")
    parser.add_argument("--engagement-rate", type=Path, help="calculate_engagement_rate.py output for this account")
    parser.add_argument("--as-of", help="YYYY-MM-DD (default: today, UTC)")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if not args.envelope:
            raise ValueError("pass every account Envelope this run persisted with --envelope")
        as_of = date.fromisoformat(args.as_of) if args.as_of else datetime.now(timezone.utc).date()
        engagement = json.loads(args.engagement_rate.read_text(encoding="utf-8")) if args.engagement_rate else None
        derived = derive(args.platform, load_envelopes(args.envelope), as_of, engagement)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(derived, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "failed", "error": f"{type(error).__name__}: {error}"}, ensure_ascii=False))
        return 1
    # The values, not just their names: the next step is a request the model writes, and it needs
    # these numbers. Printing only which fields exist left every run opening the file it had just
    # been told about, which is a round trip spent reading back what this command already knew.
    print(json.dumps({"status": "ok", "output": str(args.output),
                      "account_facts": derived["account_facts"],
                      "fields": sorted(k for k in derived["account_facts"] if k != "platform"),
                      "omitted": [o["field"] for o in derived["omitted"]]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
