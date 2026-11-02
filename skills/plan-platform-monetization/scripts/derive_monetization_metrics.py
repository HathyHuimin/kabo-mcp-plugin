#!/usr/bin/env python3
"""Derive a monetization request's observed_metrics from the account Envelopes this run persisted.

Every gate here turns on a count: subscribers against 1,000, uploads in 90 days, views in 30. The
model used to read those off a fenced, possibly truncated rendering and add them up itself, which
is how a string subscriber count, a post one day outside the window or a page nobody fetched ends
up deciding eligibility. This script reads the persisted Envelopes and applies SKILL.md's
derivation rules exactly:

- youtube.subscribers: the channel's published subscriber count.
- youtube.public_uploads_90d: uploads published within 90 days of the assessment date.
- youtube.shorts_views_90d: public views of Shorts published within 90 days, only when the rows
  mark which uploads are Shorts. Omitted otherwise, never approximated from duration.
- instagram.followers: the profile's follower count.
- tiktok.followers: follower_count on the account-post response; a null is left out, never zero.
- tiktok.public_posts_30d / tiktok.views_30d: posts published within 30 days and their views.

A windowed count carries coverage_complete: true only when the retrieved rows reach back past the
window start or the chain ended. Otherwise it is a lower bound, and the assessor reads it as one.
The channel's declared country, when the response carries it, comes back as ``region``.

Standard library only.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
# Sibling imports must not leave a __pycache__ in the skill directory.
sys.dont_write_bytecode = True
from rule_library import DEFAULT_LIBRARY as DEFAULT_RULES, load_rule_library  # noqa: E402

USABLE = {"completed", "completed_partial"}


def record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def number(value: Any) -> float | None:
    """A public count, which some providers return as a string ("218")."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value.strip().replace(",", "").isdigit():
        return float(value.strip().replace(",", ""))
    return None


def moment(value: Any) -> datetime | None:
    if isinstance(value, (int, float)):
        stamp = float(value) / (1000.0 if value > 1e12 else 1.0)
        return datetime.fromtimestamp(stamp, tz=timezone.utc)
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
        document = json.loads(path.read_text(encoding="utf-8"))
        envelope = document if "connector_id" in record(document) else record(record(document).get("envelope"))
        if not envelope.get("connector_id"):
            raise ValueError(f"{path} is not a connector envelope")
        envelopes.append(envelope)
    return envelopes


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


def of(envelopes: list[dict[str, Any]], connector: str, operation: str) -> list[dict[str, Any]]:
    """Usable Envelopes of one operation, latest retrieval first: a re-fetch supersedes an older copy,
    so a post both carry keeps its newer counts and a figure is dated by the newest read."""
    matched = [e for e in envelopes if e.get("connector_id") == connector and e.get("operation") == operation
               and e.get("status") in USABLE]
    oldest = datetime.min.replace(tzinfo=timezone.utc)
    return sorted(matched, key=lambda e: moment(retrieved_at(e)) or oldest, reverse=True)


def retrieved_at(envelope: dict[str, Any]) -> str:
    return str(envelope.get("retrieved_at") or record(envelope.get("data")).get("retrieved_at") or "")


class Window:
    """Rows inside [as_of - days, as_of], and whether the retrieval reached the window's start."""

    def __init__(self, as_of: date, days: int) -> None:
        self.end = datetime(as_of.year, as_of.month, as_of.day, 23, 59, 59, tzinfo=timezone.utc)
        self.start = self.end - timedelta(days=days)

    def split(self, rows: list[dict[str, Any]], chain_ended: bool) -> tuple[list[dict[str, Any]], bool, int]:
        dated = [(moment(row.get("published_at")), row) for row in rows]
        inside = [row for when, row in dated if when is not None and self.start <= when <= self.end]
        oldest = min((when for when, _ in dated if when is not None), default=None)
        complete = chain_ended or (oldest is not None and oldest < self.start)
        return inside, complete, len(rows)


def metric(metric_id: str, value: float, units: dict[str, str], when: str, *, complete: bool | None = None,
           sample: int | None = None, caveat: str | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {
        "metric_id": metric_id,
        "value": int(value) if float(value).is_integer() else value,
        "unit": units.get(metric_id, "count"),
        "basis": "public_account_facts",
        "retrieved_at": when,
    }
    if complete is not None:
        row["coverage_complete"] = complete
    if sample is not None:
        row["sample_size"] = sample
    if caveat:
        row["caveat"] = caveat
    return row


def summed_views(rows: list[dict[str, Any]]) -> tuple[float, int]:
    """Total public views, and how many rows had none. A missing count is not zero."""
    counts = [number(record(row.get("metrics")).get("views")) for row in rows]
    return sum(c for c in counts if c is not None), sum(1 for c in counts if c is None)


def is_short(row: dict[str, Any]) -> bool | None:
    """Whether a row is marked a Short. None when nothing on the row says either way."""
    marker = str(row.get("format") or row.get("content_type") or "").lower()
    if marker:
        if "short" in marker:
            return True
        if marker in {"youtube_video", "long_form", "video"}:
            return False
    url = str(row.get("source_url") or row.get("url") or "")
    if "/shorts/" in url:
        return True
    return None


def derive(platform: str, envelopes: list[dict[str, Any]], as_of: date, units: dict[str, str]) -> dict[str, Any]:
    metrics: list[dict[str, Any]] = []
    omitted: list[dict[str, str]] = []
    region = None
    if platform == "youtube":
        channels = of(envelopes, "youtube-public", "get_channel")
        uploads = of(envelopes, "youtube-public", "list_channel_uploads")
        subscribers = None
        for envelope in channels:
            row = record((record(envelope.get("data")).get("rows") or [{}])[0])
            subscribers = number(record(row.get("metrics")).get("subscribers"))
            if subscribers is not None:
                metrics.append(metric("youtube.subscribers", subscribers, units, retrieved_at(envelope)))
                break
        rows: list[dict[str, Any]] = []
        chain_ended = False
        for envelope in uploads:
            data = record(envelope.get("data"))
            rows.extend(r for r in data.get("rows") or [] if isinstance(r, dict))
            # Only an explicit empty next_page_token ends the chain; a response without a coverage
            # block says nothing about whether more uploads exist.
            coverage = record(data.get("coverage"))
            chain_ended = chain_ended or ("next_page_token" in coverage and not coverage.get("next_page_token"))
            channel = record(data.get("channel"))
            if subscribers is None and number(record(channel.get("metrics")).get("subscribers")) is not None:
                subscribers = number(record(channel.get("metrics")).get("subscribers"))
                metrics.append(metric("youtube.subscribers", subscribers, units, retrieved_at(envelope)))
            if isinstance(channel.get("country"), str) and len(channel["country"]) == 2:
                region = {"region": channel["country"].upper(), "region_source": "account_declared"}
        if uploads:
            when = retrieved_at(uploads[0])
            rows = unique_rows(rows)
            inside, complete, sample = Window(as_of, 90).split(rows, chain_ended)
            metrics.append(metric("youtube.public_uploads_90d", len(inside), units, when, complete=complete, sample=sample))
            marks = [is_short(row) for row in inside]
            if inside and all(mark is not None for mark in marks):
                shorts = [row for row, mark in zip(inside, marks) if mark]
                views, missing = summed_views(shorts)
                notes = ["public view counts, not the qualified views the programme counts"]
                if missing:
                    notes.append(f"{missing} Short(s) in the window carry no public view count; a lower bound")
                metrics.append(metric("youtube.shorts_views_90d", views, units, when, complete=complete and not missing,
                                      sample=sample, caveat="; ".join(notes)))
            else:
                omitted.append({"metric_id": "youtube.shorts_views_90d",
                                "reason": "the retrieved uploads do not mark which are Shorts"})
    elif platform == "instagram":
        for envelope in of(envelopes, "instagram-discovery", "resolve_profile"):
            profile = record(record(envelope.get("data")).get("profile"))
            followers = next((number(profile.get(key)) for key in ("follower_count", "followers_count")
                              if number(profile.get(key)) is not None), None)
            if followers is None:
                followers = number(record(profile.get("edge_followed_by")).get("count"))
            if followers is not None:
                metrics.append(metric("instagram.followers", followers, units, retrieved_at(envelope)))
                break
    elif platform == "tiktok":
        posts = of(envelopes, "tiktok-public", "list_account_posts")
        rows = []
        chain_ended = False
        followers = None
        for envelope in posts:
            data = record(envelope.get("data"))
            rows.extend(r for r in data.get("rows") or [] if isinstance(r, dict))
            chain_ended = chain_ended or data.get("more_available") is False
            if followers is None:
                followers = number(data.get("follower_count"))
        if posts:
            when = retrieved_at(posts[0])
            if followers is not None:
                metrics.append(metric("tiktok.followers", followers, units, when))
            else:
                omitted.append({"metric_id": "tiktok.followers", "reason": "the response carried no follower_count"})
            inside, complete, sample = Window(as_of, 30).split(unique_rows(rows), chain_ended)
            metrics.append(metric("tiktok.public_posts_30d", len(inside), units, when, complete=complete, sample=sample))
            views, missing = summed_views(inside)
            notes = []
            if not complete:
                notes.append("the retrieved posts do not reach back 30 days; a lower bound")
            if missing:
                notes.append(f"{missing} post(s) in the window carry no public view count; a lower bound")
            metrics.append(metric("tiktok.views_30d", views, units, when, complete=complete and not missing,
                                  sample=sample, caveat="; ".join(notes) or None))
    else:
        raise ValueError(f"platform must be youtube, instagram or tiktok, got {platform!r}")
    return {"schema_version": "observed-metrics.v1", "platform": platform, "as_of": as_of.isoformat(),
            "observed_metrics": metrics, "omitted": omitted, "region": region}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--platform", required=True, choices=["youtube", "instagram", "tiktok"])
    parser.add_argument("--envelope", type=Path, action="append", default=[], help="one persisted Envelope (repeat)")
    parser.add_argument("--as-of", help="assessment date, YYYY-MM-DD (default: today, UTC)")
    parser.add_argument("--rules", type=Path, default=DEFAULT_RULES)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        as_of = date.fromisoformat(args.as_of) if args.as_of else datetime.now(timezone.utc).date()
        rules = load_rule_library(args.rules)
        units = {m["metric_id"]: m.get("unit", "count") for m in rules.get("metrics", []) if isinstance(m, dict)}
        if not args.envelope:
            raise ValueError("pass every account Envelope this run persisted with --envelope")
        derived = derive(args.platform, load_envelopes(args.envelope), as_of, units)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(derived, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "failed", "error": f"{type(error).__name__}: {error}"}, ensure_ascii=False))
        return 1
    print(json.dumps({"status": "ok", "output": str(args.output),
                      "metrics": [m["metric_id"] for m in derived["observed_metrics"]],
                      "omitted": [m["metric_id"] for m in derived["omitted"]], "region": derived["region"]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
