#!/usr/bin/env python3
"""Project a NormalizedSocialSnapshot into the PublishHistory this Skill consumes.

The timing engine needs one table: when each post went out, how it did on every metric the
platform reports, and whether it is mature enough to count. That is a small, de-identified slice
of the M1 snapshot — captions and post content stay out.

Several metrics rather than one, because agreement between them is what the engine ranks on. An
earlier version kept a single figure and dropped the rest here, which made it impossible to ask
whether views, reach and skip rate point at the same window — and that question is the one a
creator can actually judge.

Each metric's scope and direction travel with the table. An owner `views` figure and a public
`play_count` are different populations even when they happen to agree; skip rate is the one where
a higher number is a worse post.

    python3 project_publish_history.py <normalized_snapshot.json> --output <publish_history.json>

The snapshot arrives already normalized. Reading an owner export archive in place was a local
ingest path; owner-authorized data now reaches this Skill through a platform connector, so the
archive reader does not ship with the package.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA_VERSION = "publish-history.v2"

# A collector may report a metric as measured-and-zero rather than available. Both are data.
PRESENT = {"available", "zero"}

# Each platform gets its own panel because the platforms do not know the same things. An Instagram
# owner export knows who was reached and who skipped; a public YouTube read knows only what the
# open API returns and nothing about the audience that left. Ranking one window against another
# needs several metrics precisely because no single one is trustworthy on its own — a viral post
# moves views a long way and moves like rate barely at all.
#
# The Instagram panel is the metric set M1 already uses for its baseline cohorts, so a window
# ranked here is ranked on the same figures the account review compares against.
INSTAGRAM_PANEL = [
    {"name": "views", "native_name": "views", "direction": "higher_is_better",
     "period": "post_lifetime_at_export",
     "denominator": "all plays counted by the platform at export time",
     "denominator_known": True, "accumulates_with_age": True, "scope": "owner"},
    {"name": "reach", "native_name": "reach", "direction": "higher_is_better",
     "period": "post_lifetime_at_export", "denominator": "unique accounts that saw the post",
     "denominator_known": True, "accumulates_with_age": True, "scope": "owner"},
    {"name": "total_interactions", "native_name": "total_interactions",
     "direction": "higher_is_better", "period": "post_lifetime_at_export",
     "denominator": "likes, comments, saves and shares combined",
     "denominator_known": True, "accumulates_with_age": True, "scope": "owner"},
    # The one metric where a higher number is a worse post. Ranking it like the others would put
    # the worst window on top, which is why direction travels with every metric.
    {"name": "skip_rate_percent", "native_name": "skip_rate_percent",
     "direction": "lower_is_better", "period": "first_three_seconds",
     "denominator": "plays that reached the post at all",
     "denominator_known": True, "accumulates_with_age": False, "scope": "owner"},
    {"name": "average_watch_time_seconds", "native_name": "ig_reels_avg_watch_time",
     "direction": "higher_is_better", "period": "post_lifetime_at_export",
     "denominator": "mean seconds watched per play", "denominator_known": True,
     "accumulates_with_age": False, "scope": "owner"},
]

# Normalized public Instagram, YouTube and TikTok snapshots share three counters. The two derived
# rates divide out how far the platform distributed the content, so they track how the audience
# that did see it responded — the part a publish hour could plausibly move, where raw views mostly
# track distribution.
PUBLIC_ENGAGEMENT_PANEL = [
    {"name": "view_count", "native_name": "viewCount", "direction": "higher_is_better",
     "period": "video_lifetime_at_collection", "denominator": "plays counted by the platform",
     "denominator_known": True, "accumulates_with_age": True, "scope": "public"},
    {"name": "like_count", "native_name": "likeCount", "direction": "higher_is_better",
     "period": "video_lifetime_at_collection", "denominator": "likes counted by the platform",
     "denominator_known": True, "accumulates_with_age": True, "scope": "public"},
    {"name": "comment_count", "native_name": "commentCount", "direction": "higher_is_better",
     "period": "video_lifetime_at_collection", "denominator": "comments counted by the platform",
     "denominator_known": True, "accumulates_with_age": True, "scope": "public"},
    {"name": "like_rate", "derived_from": ["like_count", "view_count"],
     "direction": "higher_is_better", "period": "video_lifetime_at_collection",
     "denominator": "likes per play", "denominator_known": True,
     "accumulates_with_age": False, "scope": "public"},
    {"name": "comment_rate", "derived_from": ["comment_count", "view_count"],
     "direction": "higher_is_better", "period": "video_lifetime_at_collection",
     "denominator": "comments per play", "denominator_known": True,
     "accumulates_with_age": False, "scope": "public"},
]


def derived_rates(counts: dict) -> dict:
    """likes and comments per play. Left null rather than zero when views are absent or zero: a
    post nobody saw has no response rate, and calling that 0.0 would rank it as the worst window."""
    views = counts.get("view_count")
    if not views:
        return {"like_rate": None, "comment_rate": None}
    rates = {}
    for source, name in (("like_count", "like_rate"), ("comment_count", "comment_rate")):
        value = counts.get(source)
        rates[name] = round(value / views, 6) if value is not None else None
    return rates


def panel_with_coverage(panel: list, posts: list) -> list:
    """Attach how many rows actually carry each metric. A metric present on three posts must not
    be given the same weight as one present on all of them."""
    out = []
    for entry in panel:
        carrying = sum(1 for p in posts if p["metrics"].get(entry["name"]) is not None)
        if carrying:
            out.append({**entry, "posts_carrying": carrying})
    return out

SHORTCODE = re.compile(r"/(?:reel|p|tv)/([A-Za-z0-9_-]+)")


def post_ref(permalink: str, post_id: str) -> str:
    """Prefer the public shortcode; it is what a human can open and what fixtures already cite."""
    found = SHORTCODE.search(permalink or "")
    return found.group(1) if found else post_id


def project(snapshot: dict, product_type: str, maturity_days: int) -> dict:
    generated_at = snapshot["source"]["generated_at"]
    cutoff = datetime.fromisoformat(generated_at) - timedelta(days=maturity_days)

    posts = []
    dropped = {"wrong_product_type": 0, "no_panel_metric": 0}
    cohort_total = 0
    # Checked across the whole catalogue, not just the rows that survive filtering: the claim the
    # recommendation makes is about the export, so it must be measured on the export.
    offsets = {datetime.fromisoformat(row["timestamp"]).utcoffset() for row in snapshot["posts"]}
    for row in snapshot["posts"]:
        if row["media_product_type"] != product_type:
            dropped["wrong_product_type"] += 1
            continue
        cohort_total += 1
        owner = row.get("owner_metrics") or {}
        # A partial export is normal. Each metric is taken where present and left null where not,
        # rather than dropping the whole row: a post missing watch time can still rank on reach.
        metrics = {}
        for entry in INSTAGRAM_PANEL:
            item = owner.get(entry["native_name"]) or owner.get(entry["name"]) or {}
            value = item.get("value") if item.get("status") in PRESENT else None
            metrics[entry["name"]] = value
        if all(v is None for v in metrics.values()):
            dropped["no_panel_metric"] += 1
            continue
        published = datetime.fromisoformat(row["timestamp"])
        posts.append({
            "post_ref": post_ref(row.get("permalink"), row["post_id"]),
            "published_at": published.astimezone(timezone.utc).isoformat(),
            "product_type": row["media_product_type"],
            "metrics": metrics,
            "is_mature": published < cutoff,
        })

    posts.sort(key=lambda p: p["published_at"])
    mature = [p for p in posts if p["is_mature"]]

    return {
        "schema_version": SCHEMA_VERSION,
        "history_id": f"ph-{snapshot['scope']['account_ref']}-{snapshot['source']['archive_sha256'][:12]}",
        "platform": snapshot["platform"],
        "account_ref": snapshot["scope"]["account_ref"],
        "source": {
            "mode": snapshot["source_mode"],
            "snapshot_id": snapshot["snapshot_id"],
            "snapshot_sha256": snapshot["source"]["archive_sha256"],
            "generated_at": generated_at,
            "provider_api_version": snapshot["source"].get("graph_version"),
            "window": {"since": snapshot["scope"]["since"], "until": snapshot["scope"]["until"]},
        },
        "timestamps": {
            "timezone": "UTC" if offsets == {timedelta(0)} else "mixed",
            "rows_checked": len(snapshot["posts"]),
            # Every row carries an offset, so the instant is absolute and the creator's local
            # hour is a conversion needing only their zone — which the host supplies. This used to
            # be hard-coded false, which made the engine's local branch unreachable and put UTC in
            # fields named `_local` on every run ever published.
            "local_time_recoverable": offsets != set() and None not in offsets,
        },
        "metric_panel": panel_with_coverage(INSTAGRAM_PANEL, posts),
        "maturity": {
            "days": maturity_days,
            "cutoff": cutoff.astimezone(timezone.utc).isoformat(),
            "basis": "snapshot generated_at minus maturity_days",
        },
        "coverage": {
            "catalog_posts": snapshot["coverage"]["catalog_posts"],
            "posts_with_insights": snapshot["coverage"]["posts_with_insights"],
            "cohort_total": cohort_total,
            "cohort_with_metric": len(posts),
            "cohort_mature_with_metric": len(mature),
            "dropped": dropped,
            "coverage_note": (
                f"{snapshot['coverage']['posts_without_insights']} catalogue posts carry no per-post "
                f"insights at all; the export recorded "
                f"{snapshot['coverage']['partial_failure_count']} partial failures and "
                f"{'exhausted' if snapshot['coverage']['time_budget_exhausted'] else 'did not exhaust'}"
                " its time budget, so the usable history is both smaller than the catalogue and "
                "not a random sample of it."
            ),
        },
        "posts": posts,
    }


def project_channel_snapshot(snapshot: dict, account_ref: str, content_format: str,
                             maturity_days: int, owner_id: str) -> dict:
    """Project a normalized public-content snapshot into the same contract.

    The YouTube, Instagram and TikTok public connectors all normalize into this shape. Every row
    carries the platform's publish instant, so the platform must travel from the snapshot rather
    than being inferred from the CLI path. Cohorts stay split by format: a Short, a long video, a
    Reel and a TikTok video are not the same product and pooling them would compare different
    audiences at once.

    `owner_id` is required, and rows published by anyone else are dropped before anything is
    counted. A profile listing carries collab posts: they appear on the account and they are not
    its publishing decisions, so their hour says nothing about when this creator should post. Two
    such reels sat in a real 26-row Instagram run; one run excluded them by hand and two later runs
    did not, and the three produced three different recommended windows. The projection is
    de-identified by design, so `creator_id` is unrecoverable downstream — if the split does not
    happen here it cannot happen at all.
    """
    platform = snapshot.get("platform")
    if platform not in {"instagram", "youtube", "tiktok"}:
        raise ValueError(f"unsupported or missing public snapshot platform: {platform!r}")
    observed_at = snapshot["observed_at"]
    collected = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    cutoff = collected - timedelta(days=maturity_days)
    collected_window = (snapshot.get("scope") or {}).get("window") or {}

    owned, not_owner, unattributed = [], [], []
    for row in snapshot["items"]:
        raw = row.get("creator_id")
        if raw is None or str(raw).strip() == "":
            # The provider returned no author for this row. Keeping it is the lesser error - a
            # dropped row is invisible, an unverifiable one can at least be counted and said out
            # loud - but it never passes silently.
            unattributed.append(row)
        elif str(raw) == str(owner_id):
            owned.append(row)
        else:
            not_owner.append(row)

    scoped_rows = owned + unattributed
    in_format = [row for row in scoped_rows if row.get("format") == content_format]
    if scoped_rows and not in_format:
        available_formats = sorted({str(row.get("format")) for row in scoped_rows if row.get("format")})
        raise ValueError(
            f"requested content format {content_format!r} matched 0 account rows; "
            f"available formats: {available_formats or ['unknown']}"
        )
    posts, coarse = [], 0
    for row in in_format:
        available = row.get("metrics") or {}
        stamp = row.get("published_at")
        if not stamp:
            continue
        counts = {}
        for native, name in (("views", "view_count"), ("likes", "like_count"),
                             ("comments", "comment_count")):
            item = available.get(native) or {}
            # `zero` is a measurement, not a gap: a video really did get no comments. Reading it as
            # missing would drop 78 of 92 rows out of the comment ranking and leave only the posts
            # that happened to get talked about.
            counts[name] = item.get("value") if item.get("status") in PRESENT else None
        if counts["view_count"] is None:
            continue
        metrics = dict(counts)
        metrics.update(derived_rates(counts))
        published = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        # A row that lost its time of day would silently land in whatever bucket midnight falls in.
        if published.hour == 0 and published.minute == 0 and published.second == 0:
            coarse += 1
        posts.append({
            "post_ref": row["content_id"],
            "published_at": published.astimezone(timezone.utc).isoformat(),
            "product_type": content_format,
            "metrics": metrics,
            "is_mature": published < cutoff,
        })
    posts.sort(key=lambda p: p["published_at"])

    return {
        "schema_version": SCHEMA_VERSION,
        "history_id": f"ph-{account_ref}-{content_format}",
        "platform": platform,
        "account_ref": account_ref,
        "source": {
            "mode": "connector",
            "snapshot_id": snapshot["snapshot_id"],
            "snapshot_sha256": (snapshot.get("source") or {}).get("raw_sha256"),
            "generated_at": observed_at,
            "provider_api_version": (snapshot.get("source") or {}).get("connector_id"),
            # The collector names the window start/end; the contract names it since/until.
            "window": {"since": collected_window.get("start"), "until": collected_window.get("end")}
            if collected_window else None,
        },
        "timestamps": {
            "timezone": "UTC",
            "rows_checked": len(in_format),
            # The platform reports an absolute instant, so the local hour is a conversion the
            # engine performs from the zone on the request. Same correction as the Instagram path.
            "local_time_recoverable": True,
            "time_of_day_recoverable": coarse != len(posts),
            "date_precision": "second",
            "precision_note": (
                f"{len(posts) - coarse} of {len(posts)} rows carry a non-midnight publish second "
                f"from the platform's own publishedAt field. Hours are observed, not inferred, "
                f"and each one is an absolute instant, so they convert into the creator's zone "
                f"exactly once that zone is supplied."),
        },
        "metric_panel": panel_with_coverage(PUBLIC_ENGAGEMENT_PANEL, posts),
        "maturity": {"days": maturity_days, "cutoff": cutoff.isoformat(),
                     "basis": "collection time minus maturity_days"},
        "owner_scope": {
            "owner_id": str(owner_id),
            "rule": ("rows are restricted to posts published by this account; a collab post carries "
                     "another account's publishing decision, so its hour is not evidence about "
                     "this creator"),
            "kept": len(owned),
            "excluded_not_owner": [
                {"content_id": row.get("content_id"), "creator_id": str(row.get("creator_id")),
                 "url": row.get("url"), "published_at": row.get("published_at")}
                for row in not_owner
            ],
            "unattributed": len(unattributed),
            "unattributed_note": (
                "rows the provider returned with no creator_id; they are kept and counted here "
                "because dropping them would be invisible, but the scope claim does not cover them"
                if unattributed else None),
        },
        "coverage": {
            "catalog_posts": len(snapshot["items"]),
            "posts_with_insights": None,
            "cohort_total": len(in_format),
            "cohort_with_metric": len(posts),
            "cohort_mature_with_metric": sum(1 for p in posts if p["is_mature"]),
            "dropped": {"no_timestamp_or_metric": len(in_format) - len(posts),
                        "not_owner_published": len(not_owner)},
            "coverage_note": (
                f"{len(snapshot['items'])} uploads collected with coverage_complete="
                f"{(snapshot.get('source') or {}).get('coverage_complete')}; "
                f"{len(not_owner)} published by another account were excluded; {len(in_format)} are "
                f"{content_format}. Metrics are {platform} public counters; owner-only figures "
                f"such as retention, reach or audience breakdowns are absent."),
        },
        "posts": posts,
    }


def owner_id_from_envelope(path: str) -> str:
    """The id of the account a persisted Envelope names, so nobody types it.

    Instagram: resolve_profile's profile id. YouTube: the channel id on get_channel or on the
    uploads response's channel block. TikTok: list_account_posts lists one account's own posts, so
    the creator_id every row shares is the owner's; rows naming two ids are refused.
    """
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    envelope = document if isinstance(document, dict) and "connector_id" in document else (document or {}).get("envelope") or {}
    data = envelope.get("data") or {}
    operation = (envelope.get("connector_id"), envelope.get("operation"))
    if operation == ("instagram-discovery", "resolve_profile"):
        profile = data.get("profile") or {}
        value = profile.get("pk") or profile.get("id")
    elif operation == ("youtube-public", "get_channel"):
        row = (data.get("rows") or [{}])[0] or {}
        value = row.get("channel_id") or row.get("id")
    elif operation == ("youtube-public", "list_channel_uploads"):
        value = (data.get("channel") or {}).get("id")
    elif operation == ("tiktok-public", "list_account_posts"):
        ids = sorted({str(row.get("creator_id")) for row in data.get("rows") or []
                      if isinstance(row, dict) and row.get("creator_id")})
        if len(ids) > 1:
            raise SystemExit(f"{path}: the rows name more than one creator_id {ids}; pass --owner-id")
        value = ids[0] if ids else None
    else:
        raise SystemExit(f"{path} is not a resolve_profile, get_channel, list_channel_uploads or "
                         "list_account_posts Envelope")
    if not value:
        raise SystemExit(f"{path} names no account id")
    return str(value)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("snapshot", nargs="?", help="normalized_snapshot.json")
    ap.add_argument("--channel-snapshot", help="a normalized public-content snapshot.json")
    ap.add_argument("--content-format", default="short", help="with --channel-snapshot")
    ap.add_argument("--account-ref")
    ap.add_argument("--owner-id", help="numeric id of the account whose publishing decisions this "
                                       "is about; required with --channel-snapshot")
    ap.add_argument("--profile-envelope",
                    help="the persisted Envelope that names the account; the owner id is read from it")
    ap.add_argument("--collected-at", help="collection instant, passed in so reruns stay identical")
    ap.add_argument("--product-type", default="REELS")
    ap.add_argument("--maturity-days", type=int, default=14)
    ap.add_argument("--output")
    args = ap.parse_args()

    if args.channel_snapshot:
        snapshot = json.loads(Path(args.channel_snapshot).read_text(encoding="utf-8"))
        if args.profile_envelope:
            derived = owner_id_from_envelope(args.profile_envelope)
            if args.owner_id and str(args.owner_id) != derived:
                raise SystemExit(f"--owner-id {args.owner_id} disagrees with {args.profile_envelope} ({derived})")
            args.owner_id = derived
        if not args.owner_id:
            raise SystemExit(
                "--owner-id is required: a profile listing carries collab posts published by other "
                "accounts, and their hour is not this creator's decision. On Instagram the id is what "
                "resolve_profile returns; on YouTube and TikTok it is the creator_id already carried "
                "on the snapshot rows. Without it the scope cannot be stated, and it cannot be "
                "recovered later because the projection is de-identified.")
        history = project_channel_snapshot(snapshot, args.account_ref or "unknown",
                                           args.content_format, args.maturity_days, args.owner_id)
        text = json.dumps(history, ensure_ascii=False, indent=1) + "\n"
        if args.output:
            Path(args.output).write_text(text, encoding="utf-8")
            print(json.dumps({"ok": True, "history_id": history["history_id"],
                              "mature": history["coverage"]["cohort_mature_with_metric"],
                              "time_of_day_recoverable":
                                  history["timestamps"]["time_of_day_recoverable"],
                              "output": args.output}, ensure_ascii=False))
        else:
            print(text)
        return 0

    if args.snapshot:
        snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
    else:
        raise SystemExit("provide a normalized snapshot path")

    history = project(snapshot, args.product_type, args.maturity_days)
    text = json.dumps(history, ensure_ascii=False, indent=1) + "\n"
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(json.dumps({"ok": True, "history_id": history["history_id"],
                          "mature": history["coverage"]["cohort_mature_with_metric"],
                          "output": args.output}, ensure_ascii=False))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
