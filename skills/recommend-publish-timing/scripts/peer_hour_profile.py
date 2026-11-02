#!/usr/bin/env python3
"""Read, from a peer snapshot, which hour the comparable accounts publish their best content in.

This is the one signal in this Skill that is not the creator's own history, so it is also the one
that has to justify itself hardest. The line it must not cross is not "someone else's data" — it is
**data a creator cannot check**. A generic best-time chart is refused here and always has been,
because there is no way to ask it who it counted. A named account with a URL is a different object:
the creator can open it, disagree with it, and see the posts it is built from.

So every hour this module reports carries the handles and URLs it was built from, and the layer
above is required to show them. Strip the attribution and what is left is the generic chart.

Three things this module will not do:

  * decide who counts as a peer — that judgement belongs to whoever produced the snapshot, and
    re-deriving it here would be half a competitor Skill living in the wrong place;
  * merge peer numbers into the creator's own medians — they answer a different question about a
    different account and pooling them would make both unreadable;
  * report anything at all from a snapshot that was collected by picking winners. A per-account
    recent-N collection has a denominator: the hours that did badly are in it too. An outlier-only
    collection does not, and "when did the hits go out" is not the same claim as "when should you
    post" — it cannot see the hours where everything flopped.

    python3 peer_hour_profile.py <snapshot.json> --focal <account_ref> --metric views
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


# Same kernel as the creator's own history. Two signals answering the same question must be read
# the same way, or "they agree" means nothing.
KERNEL_HALF_WIDTH_HOURS = 1.5

# Peers are many accounts, so an hour that cannot reach this is genuinely empty rather than merely
# this creator's quiet hour.
MIN_POSTS_IN_HOUR_WINDOW = 8

# Metric names differ between our own panel and a public snapshot. Owner panels say `view_count`,
# public snapshots say `views`; the concept is the same and the aliasing is not a judgement call.
ALIASES = {
    "view_count": ("views", "view_count", "viewCount", "play_count", "plays"),
    "views": ("views", "view_count", "viewCount", "play_count", "plays"),
    "like_count": ("likes", "like_count", "likeCount"),
    "likes": ("likes", "like_count", "likeCount"),
    "comment_count": ("comments", "comment_count", "commentCount"),
    "comments": ("comments", "comment_count", "commentCount"),
}


def circular_distance(a: int, b: int) -> int:
    gap = abs(a - b) % 24
    return min(gap, 24 - gap)


# How an upstream states who is comparable. A canonical PublicContentSnapshot puts it on each
# creator as `relationship`; a competitor report puts it in `cohort_classification` with a written
# reason per account. Both are the same judgement and both are honoured as given.
USABLE_RELATIONSHIPS = ("direct", "adjacent")
CLASSIFICATION_BUCKETS = {
    "focus_creator": "focus",
    "direct_competitors": "direct",
    "adjacent_references": "adjacent",
    "media_accounts": "reference",
    "excluded_candidates": "excluded",
}


def find_first(document, predicate):
    """First nested value satisfying `predicate`, searched depth-first."""
    stack = [document]
    while stack:
        node = stack.pop(0)
        if isinstance(node, dict):
            if predicate(node):
                return node
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return None


_YOUTUBE_CHANNEL_ID = re.compile(r"^UC[A-Za-z0-9_-]{22}$")


def channel_url_from_id(creator_id) -> str | None:
    """The canonical channel page for a YouTube channel id, when the upstream gave no `source_url`.

    Only for an id whose shape leaves no doubt: `UC` plus 22 id characters is a YouTube channel and
    nothing else, and `/channel/<id>` is the address that always resolves for one. Anything else
    returns nothing, because a guessed profile URL that opens the wrong account is worse than a
    handle the creator has to search for -- the point of naming these accounts is that the
    comparison can be checked, and a link to the wrong channel silently defeats that.

    This does not make `channel_url` and `sample_url` interchangeable; it fills the field that was
    already meant to hold the account's own page.
    """
    text = str(creator_id or "")
    return f"https://www.youtube.com/channel/{text}" if _YOUTUBE_CHANNEL_ID.match(text) else None


def peer_relationships(document: dict) -> dict:
    """`creator_id` to the relationship its upstream assigned it, plus the handle it was given.

    Whoever produced the snapshot decided who counts as comparable, and that decision is taken as
    given here. It matters more than it looks: on the one real cohort available, the account with
    by far the highest views is in `excluded_candidates` — its best content is general AI comedy,
    unrelated to the creator's category — and letting it vote would move the medians on its own.
    """
    table: dict = {}

    snapshot = find_first(document, lambda n: isinstance(n.get("creators"), list) and n.get("items"))
    if snapshot:
        for entry in snapshot["creators"]:
            if entry.get("creator_id"):
                table[entry["creator_id"]] = {
                    "relationship": entry.get("relationship") or "unknown",
                    "handle": entry.get("handle"),
                    "url": entry.get("source_url"),
                }

    classification = find_first(
        document, lambda n: any(bucket in n for bucket in CLASSIFICATION_BUCKETS))
    if classification:
        for bucket, relationship in CLASSIFICATION_BUCKETS.items():
            for entry in classification.get(bucket) or []:
                if isinstance(entry, str):
                    table.setdefault(entry, {"relationship": relationship,
                                             "handle": None, "url": None})
                    table[entry]["relationship"] = relationship
                elif isinstance(entry, dict) and entry.get("creator_id"):
                    row = table.setdefault(entry["creator_id"], {})
                    row["relationship"] = relationship
                    row.setdefault("handle", entry.get("handle"))
                    row.setdefault("url", entry.get("source_url"))
                    if entry.get("reason_zh"):
                        row["reason"] = entry["reason_zh"]
    return table


def collect_items(document: dict) -> list:
    """Every content row carrying a publish instant, deduplicated by content id.

    Competitor results repeat the same row under several rankings; counting it once per appearance
    would weight whichever hour happened to rank in more places.
    """
    seen: dict = {}

    def walk(node):
        if isinstance(node, dict):
            if "published_at" in node and "content_id" in node:
                seen.setdefault(node["content_id"], node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(document)
    return list(seen.values())


def metric_of(item: dict, metric: str) -> float | None:
    """One metric off a row, in either the schema's shape or the flattened one."""
    for alias in ALIASES.get(metric, (metric,)):
        raw = (item.get("metrics") or {}).get(alias)
        if isinstance(raw, dict):
            if raw.get("status") in ("available", "zero") and raw.get("value") is not None:
                return float(raw["value"])
        elif isinstance(raw, (int, float)):
            return float(raw)
        direct = item.get(alias)
        if isinstance(direct, (int, float)):
            return float(direct)
    return None


# The two ways an upstream states its collection method. `pagination.bounded_collection: true` is
# the older boolean; `collection_mode: "bounded_collection"` is what a snapshot built straight off
# the connector says. They are the same declaration and both are honoured — but only ever by exact
# match, never by looking for the substring. Real snapshots in this repo carry
# `"incomplete_bounded_collection"`, which is the opposite claim and contains the word.
BOUNDED_MODE = "bounded_collection"


def collection_is_bounded(document: dict) -> tuple[bool, str | None, str]:
    """Was this collected with a denominator, or by picking winners?

    `bounded_collection` means the upstream took the most recent N per account: the hours that did
    badly are in the sample. Without it the sample may be outliers only, and an outlier-only sample
    answers "when did the hits go out", which is a different question wearing the same words.
    """
    flags = []

    def walk(node):
        if isinstance(node, dict):
            if "bounded_collection" in node:
                flags.append(bool(node["bounded_collection"]))
            if "collection_mode" in node:
                flags.append(node["collection_mode"] == BOUNDED_MODE)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(document)
    if not flags:
        return False, "peer_sample_declares_no_collection", (
                      "This comparable-account sample does not declare how it was collected. "
                      "Whether it is the last N posts per account or only the ones that did "
                      "well cannot be confirmed, so it does not enter the comparison.")
    if not any(flags):
        return False, "peer_sample_selected_on_performance", (
                      "This comparable-account sample was selected on performance and has no "
                      "denominator: the hours that did badly are not in it, so it cannot say "
                      "when posting does better.")
    return True, None, ""


def profile(document: dict, focal: str | None, metric: str, zone: str | None = None) -> dict:
    """The peer hour signal, or a stated reason there is not one."""
    signal = {
        "signal": "peer_accounts_hour",
        "status": "unavailable",
        "hour": None,
        "metric": metric,
        "accounts": [],
        "posts_used": 0,
        "hours_read": 0,
        "code": None,
        "params": {},
        "detail": None,
    }

    items = collect_items(document)
    relationships = peer_relationships(document)
    if not items:
        signal["code"] = "peer_posts_carry_no_publish_time"
        signal["params"] = {}
        signal["detail"] = "The input holds no posts with a publish time."
        return signal
    if not relationships:
        signal["status"] = "no_classification"
        signal["code"] = "peer_sample_declares_no_cohort"
        signal["params"] = {}
        signal["detail"] = ("This sample does not declare which accounts count as comparable. "
                            "That classification belongs to whatever produced the sample; this "
                            "module does not make it, so the sample does not enter the "
                            "comparison.")
        return signal

    bounded, why_code, why = collection_is_bounded(document)
    if not bounded:
        signal["status"] = "collection_not_comparable"
        signal["code"] = why_code
        signal["params"] = {}
        signal["detail"] = why
        return signal

    creators = relationships
    tz = None
    if zone:
        try:
            tz = ZoneInfo(zone)
        except (ZoneInfoNotFoundError, ValueError, TypeError):
            tz = None

    by_hour: dict[int, list] = {h: [] for h in range(24)}
    used: dict = {}
    for item in items:
        creator_id = item.get("creator_id")
        meta = creators.get(creator_id, {})
        handle = item.get("handle") or meta.get("handle")
        # The focal account is the creator's own; it is already signal 1 and must not vote twice.
        if focal and creator_id and focal in str(creator_id):
            continue
        if focal and handle and str(handle).lstrip("@").lower() == str(focal).lstrip("@").lower():
            continue
        # Only the accounts the upstream placed as comparable. Everything else — the focus
        # account, media accounts, the ones it looked at and rejected, and any it could not place —
        # stays out. This module consumes that judgement; it does not second-guess or replace it.
        if meta.get("relationship") not in USABLE_RELATIONSHIPS:
            continue
        value = metric_of(item, metric)
        if value is None:
            continue
        try:
            moment = datetime.fromisoformat(str(item["published_at"]).replace("Z", "+00:00"))
        except ValueError:
            continue
        if tz:
            moment = moment.astimezone(tz)
        by_hour[moment.hour].append(value)
        # Keyed on the account, not on whether it has a display name. A classified account with
        # no handle still put posts into `by_hour` above, and keying on the handle dropped it
        # from the contributor list — the signal could then resolve an hour while reporting zero
        # comparable accounts and naming none, which is exactly the shape the naming rule exists
        # to prevent.
        if creator_id or handle:
            # A channel URL when the upstream gave one, otherwise one of this account's own posts.
            # The two are not interchangeable and the field names say which is which, because the
            # whole justification for using peer data is that the creator can go and look.
            entry = used.setdefault(creator_id or handle, {
                "handle": handle,
                "relationship": meta.get("relationship"),
                "n": 0,
                "channel_url": meta.get("url") or channel_url_from_id(creator_id),
                "sample_url": item.get("url"),
                "evidence_id": item.get("evidence_id"),
                "comparable_because": meta.get("reason"),
            })
            entry["n"] += 1

    span = int(KERNEL_HALF_WIDTH_HOURS)
    curve = {}
    for hour in range(24):
        window = [v for offset in range(-span, span + 1) for v in by_hour[(hour + offset) % 24]]
        if len(window) >= MIN_POSTS_IN_HOUR_WINDOW:
            curve[hour] = {"median": round(statistics.median(window), 4), "n": len(window)}

    signal["accounts"] = sorted(used.values(), key=lambda a: -a["n"])
    signal["posts_used"] = sum(len(v) for v in by_hour.values())
    signal["hours_read"] = len(curve)

    if not curve:
        signal["status"] = "insufficient_sample"
        signal["code"] = "peer_hours_too_thin"
        signal["params"] = {"minimum_posts_in_window": MIN_POSTS_IN_HOUR_WINDOW}
        signal["detail"] = (f"No hour across the comparable accounts holds "
                            f"{MIN_POSTS_IN_HOUR_WINDOW} posts.")
        return signal

    best = max(c["median"] for c in curve.values())
    leaders = [h for h, c in curve.items() if c["median"] == best]
    if len(leaders) != 1:
        signal["status"] = "no_single_hour"
        signal["code"] = "peer_hours_tied"
        signal["params"] = {}
        signal["detail"] = ("Several hours tie at the top across the comparable accounts and "
                            "cannot be separated.")
        return signal

    hour = leaders[0]
    # Every contributing account is counted; only the ones with a name can be named. Three is
    # enough to show these are real accounts a creator can open and check, which is the whole
    # reason this signal is admissible; the full list travels in `accounts`.
    named = [a["handle"] for a in signal["accounts"] if a.get("handle")][:3]
    signal["status"] = "available"
    signal["hour"] = hour
    signal["code"] = "peer_hour_resolved"
    signal["params"] = {"hour": hour, "accounts_n": len(signal["accounts"]),
                        "posts_used": signal["posts_used"], "named": named}
    signal["detail"] = (f"Across {signal['posts_used']} posts from "
                        f"{len(signal['accounts'])} comparable accounts, the hour around "
                        f"{hour:02d}:00 does best"
                        + (f" ({', '.join(named)} and others)." if named else "."))
    return signal


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("snapshot", type=Path)
    ap.add_argument("--focal", default=None)
    ap.add_argument("--metric", default="views")
    ap.add_argument("--zone", default=None)
    args = ap.parse_args()
    document = json.loads(args.snapshot.read_text(encoding="utf-8"))
    print(json.dumps(profile(document, args.focal, args.metric, args.zone),
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
