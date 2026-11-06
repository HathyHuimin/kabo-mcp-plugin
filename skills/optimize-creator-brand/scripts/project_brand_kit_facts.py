#!/usr/bin/env python3
"""Project a brand kit's account facts from the Envelopes this run persisted.

A kit's numbers, its account provenance and its representative works are facts about a resolved
public profile. The model used to copy them off the rendering it was shown: a follower count, a
profile link it composed, a retrieval date, post links and titles. Here each comes from the host's
own record of the fetch:

- accounts_provenance: one resolved_public row for the account, only when a profile or account
  listing resolved it (Instagram resolve_profile, YouTube get_channel or list_channel_uploads,
  TikTok list_account_posts).
- metrics: followers (subscribers on YouTube), the public post count the profile shows, and on
  YouTube the channel's total views, each resolved_public with the profile link and retrieval date.
  A count the response did not carry is left out and named in missing.
- representative_works: the latest posts as recent_sample; a post passed with --highlight (its link
  or id, found in the Envelopes) as curated_highlight.

  python3 scripts/project_brand_kit_facts.py --platform instagram --handle <verified handle> \\
    --envelope snapshot/envelope-01.json --envelope snapshot/envelope-02.json \\
    [--highlight <post link or id>] --into brand_kit.json

Run it once per account. With --into, it replaces that account's resolved rows in the kit and keeps
everything else, including numbers the creator gave. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
# Sibling imports must not leave a __pycache__ in the signed, checksummed skill directory.
sys.dont_write_bytecode = True
from validate_brand_options import OWN_DOMAINS, carries_price  # noqa: E402  - one definition of each

USABLE = {"completed", "completed_partial"}
RECENT_WORKS = 3
TITLE_CHARS = 120
CONNECTORS = {"instagram": "instagram-discovery", "youtube": "youtube-public", "tiktok": "tiktok-public"}
ACCOUNT_OPERATIONS = {"instagram": {"resolve_profile"}, "youtube": {"get_channel", "list_channel_uploads"},
                      "tiktok": {"list_account_posts"}}
WORK_OPERATIONS = {"instagram": {"list_posts", "list_reels"}, "youtube": {"list_channel_uploads"},
                   "tiktok": {"list_account_posts"}}
# (name, unit) the renderer prints as written. Fixed labels are English for every delivery language.
LABELS = {
    "followers": ("followers", "followers"),
    "subscribers": ("subscribers", "subscribers"),
    "posts": ("public posts", "posts"),
    "videos": ("public videos", "videos"),
    "channel_views": ("channel views", "total views"),
}
MISSING = {
    "followers": "followers (the public profile did not show a count)",
    "subscribers": "subscribers (the channel hides its count)",
}


def record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def number(value: Any) -> int | float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value if value >= 0 else None
    text = str(value).strip().replace(",", "")
    return int(text) if text.isdigit() else None


def bare(handle: Any) -> str:
    return str(handle or "").strip().lstrip("@")


def load_envelopes(paths: list[Path]) -> list[dict[str, Any]]:
    envelopes = []
    for path in paths:
        document = record(json.loads(path.read_text(encoding="utf-8")))
        envelope = document if "connector_id" in document else record(document.get("envelope"))
        if not envelope.get("connector_id"):
            raise ValueError(f"{path} is not a connector Envelope")
        envelopes.append(envelope)
    return envelopes


def retrieved_on(envelope: dict[str, Any]) -> str:
    value = str(envelope.get("retrieved_at") or record(envelope.get("data")).get("retrieved_at") or "")
    if not re.match(r"^\d{4}-\d{2}-\d{2}", value):
        raise ValueError(f"the {envelope.get('operation')} Envelope carries no retrieved_at")
    return value[:10]


def named_handle(platform: str, envelope: dict[str, Any]) -> str | None:
    data = record(envelope.get("data"))
    if platform == "instagram":
        profile = record(data.get("profile"))
        return profile.get("handle") or profile.get("username")
    if platform == "tiktok":
        rows = [r for r in data.get("rows") or [] if isinstance(r, dict)]
        return data.get("handle") or next((r.get("username") for r in rows if r.get("username")), None)
    return record(data.get("channel")).get("custom_url")


def moment(row: dict[str, Any]) -> datetime | None:
    for key in ("published_at", "created_at"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            try:
                parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            except ValueError:
                continue
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    taken = number(row.get("taken_at"))
    return datetime.fromtimestamp(taken, timezone.utc) if taken else None


def work_url(platform: str, row: dict[str, Any]) -> str | None:
    url = row.get("url") or row.get("source_url")
    if not url and platform == "instagram" and row.get("code"):
        url = f"https://www.instagram.com/p/{row['code']}/"
    if not url and platform == "youtube" and row.get("id"):
        url = f"https://www.youtube.com/watch?v={row['id']}"
    return str(url) if url else None


def work_title(platform: str, row: dict[str, Any]) -> str:
    text = row.get("title")
    if platform == "instagram":
        caption = row.get("caption")
        text = caption.get("text") if isinstance(caption, dict) else caption
    line = next((part.strip() for part in str(text or "").splitlines() if part.strip()), "")
    line = re.sub(r"\s+", " ", line)
    if len(line) > TITLE_CHARS:
        line = line[:TITLE_CHARS - 1].rstrip() + "…"
    if line:
        return line
    published = moment(row)
    day = published.date().isoformat() if published else ""
    return f"Untitled post {day}".strip()


def row_ids(platform: str, row: dict[str, Any]) -> set[str]:
    ids = {str(row[k]) for k in ("id", "pk", "code", "content_id") if row.get(k)}
    url = work_url(platform, row)
    if url:
        ids.update({url, url.rstrip("/")})
    return ids


def project(platform: str, handle: str, envelopes: list[dict[str, Any]], *,
            highlights: list[str] | None = None, works: int = RECENT_WORKS) -> dict[str, Any]:
    handle = bare(handle)
    if not handle:
        raise ValueError("--handle is the verified handle of the account")
    connector = CONNECTORS[platform]
    for envelope in envelopes:
        if envelope.get("connector_id") != connector:
            raise ValueError(f"a {envelope.get('connector_id')} Envelope is not about a {platform} account; "
                             "run once per account with that account's Envelopes")
        named = bare(named_handle(platform, envelope)) if envelope.get("status") in USABLE else ""
        if named and named.casefold() != handle.casefold():
            raise ValueError(f"the {envelope.get('operation')} Envelope names @{named}, not @{handle}: "
                             "persist this account's responses")
    usable = [e for e in envelopes if e.get("status") in USABLE]
    account = [e for e in usable if e.get("operation") in ACCOUNT_OPERATIONS[platform]]
    if not account:
        raise ValueError(f"no completed {' / '.join(sorted(ACCOUNT_OPERATIONS[platform]))} Envelope: the account "
                         "is not resolved, so the kit records it as the creator gave it, with no numbers")
    metrics: list[dict[str, Any]] = []
    missing: list[str] = []

    def add(key: str, value: Any, source_url: str, envelope: dict[str, Any]) -> None:
        name, unit = LABELS[key]
        metrics.append({"name": name, "value": value, "unit": unit, "platform": platform, "handle": handle,
                        "source": "resolved_public", "source_url": source_url, "retrieved_at": retrieved_on(envelope)})

    if platform == "instagram":
        envelope = account[0]
        profile = record(record(envelope.get("data")).get("profile"))
        profile_url = f"https://www.instagram.com/{handle}/"
        followers = next((number(profile.get(k)) for k in ("follower_count", "followers_count")
                          if number(profile.get(k)) is not None), None)
        if followers is None:
            followers = number(record(profile.get("edge_followed_by")).get("count"))
        posts = next((number(profile.get(k)) for k in ("media_count", "post_count", "posts_count")
                      if number(profile.get(k)) is not None), None)
        counts = [("followers", followers), ("posts", posts)]
    elif platform == "youtube":
        envelope, channel = account[0], {}
        for candidate in account:
            data = record(candidate.get("data"))
            block = record(data.get("channel")) or record((data.get("rows") or [{}])[0])
            if block:
                envelope, channel = candidate, block
                if candidate.get("operation") == "get_channel":
                    break
        channel_id = channel.get("id") or channel.get("channel_id")
        profile_url = channel.get("source_url") or (f"https://www.youtube.com/channel/{channel_id}" if channel_id else "")
        figures = record(channel.get("metrics"))
        counts = [("subscribers", number(figures.get("subscribers"))), ("videos", number(figures.get("videos"))),
                  ("channel_views", number(figures.get("views")))]
    else:
        envelope = account[0]
        profile_url = f"https://www.tiktok.com/@{handle}"
        counts = [("followers", number(record(envelope.get("data")).get("follower_count")))]
    if not any(profile_url.startswith(("https://", "http://")) and f".{domain}" in f".{profile_url.split('/')[2]}"
               for domain in OWN_DOMAINS[platform]):
        raise ValueError(f"the {platform} Envelopes carry no public profile link")
    for key, value in counts:
        if value is not None:
            add(key, value, profile_url, envelope)
        elif key in MISSING:
            missing.append(MISSING[key])

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for envelope in usable:
        if envelope.get("operation") not in WORK_OPERATIONS[platform]:
            continue
        for row in record(envelope.get("data")).get("rows") or []:
            url = work_url(platform, row) if isinstance(row, dict) else None
            if url and url.rstrip("/") not in seen:
                seen.add(url.rstrip("/"))
                rows.append(row)
    rows.sort(key=lambda row: moment(row) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

    def work(row: dict[str, Any], selection: str) -> dict[str, Any]:
        return {"title": work_title(platform, row), "url": work_url(platform, row), "platform": platform,
                "handle": handle, "selection": selection}

    chosen: list[dict[str, Any]] = []
    picked: set[int] = set()
    for wanted in highlights or []:
        match = next((i for i, row in enumerate(rows) if wanted.strip().rstrip("/") in row_ids(platform, row)), None)
        if match is None:
            raise ValueError(f"--highlight {wanted} is not a post in these Envelopes")
        entry = work(rows[match], "curated_highlight")
        if carries_price(entry["title"]):
            raise ValueError(f"--highlight {wanted}: its title carries an amount, and the kit never carries a "
                             "price; pick another post")
        if match not in picked:
            picked.add(match)
            chosen.append(entry)
    recent = 0
    for index, row in enumerate(rows):
        if recent >= works:
            break
        entry = work(row, "recent_sample")
        # A caption quoting an amount would read as the creator's rate; the next post stands in.
        if index in picked or carries_price(entry["title"]):
            continue
        chosen.append(entry)
        recent += 1
    return {"schema_version": "brand-kit-account-facts.v1",
            "account": {"platform": platform, "handle": handle, "source": "resolved_public"},
            "metrics": metrics, "representative_works": chosen, "missing": missing}


def merge(kit: dict[str, Any], facts: dict[str, Any]) -> dict[str, Any]:
    if kit.get("mode") != "account":
        raise ValueError("--into takes an account kit (mode: account); a checklist kit has no account")
    account = facts["account"]
    key = (account["platform"], account["handle"].casefold())

    def other(row: Any) -> bool:
        return not (isinstance(row, dict) and (row.get("platform"), bare(row.get("handle")).casefold()) == key)

    kit["accounts_provenance"] = [r for r in kit.get("accounts_provenance") or [] if other(r)] + [account]
    # A number the creator gave stays; a resolved number for this account is replaced, never retyped.
    kit["metrics"] = [m for m in kit.get("metrics") or []
                      if other(m) or (isinstance(m, dict) and m.get("source") != "resolved_public")] + facts["metrics"]
    for metric in kit["metrics"]:
        if not other(metric):
            metric["handle"] = account["handle"]
    kit["representative_works"] = [w for w in kit.get("representative_works") or [] if other(w)] \
        + facts["representative_works"]
    missing = list(kit.get("missing") or [])
    kit["missing"] = missing + [item for item in facts["missing"] if item not in missing]
    return kit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--platform", required=True, choices=sorted(CONNECTORS))
    parser.add_argument("--handle", required=True, help="the verified handle of this account")
    parser.add_argument("--envelope", type=Path, action="append", default=[], help="one persisted Envelope (repeat)")
    parser.add_argument("--highlight", action="append", default=[],
                        help="link or id of a post the creator picked because it did well (repeat)")
    parser.add_argument("--works", type=int, default=RECENT_WORKS, help="how many latest posts to list")
    parser.add_argument("--into", type=Path, help="brand_kit.json to write this account's facts into")
    parser.add_argument("--delivery-language", help="accepted for compatibility (any BCP-47 tag); projected labels are English")
    parser.add_argument("--output", type=Path, help="also write the projected facts here")
    args = parser.parse_args()
    try:
        if not args.envelope:
            raise ValueError("pass every Envelope this run persisted for the account with --envelope")
        if args.into is None and args.output is None:
            raise ValueError("pass --into brand_kit.json, --output, or both")
        kit = json.loads(args.into.read_text(encoding="utf-8")) if args.into else None
        if kit is not None and not isinstance(kit, dict):
            raise ValueError(f"{args.into} must hold the kit object")
        facts = project(args.platform, args.handle, load_envelopes(args.envelope),
                        highlights=args.highlight, works=args.works)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(facts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if kit is not None:
            args.into.write_text(json.dumps(merge(kit, facts), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "failed", "error": f"{type(error).__name__}: {error}"}, ensure_ascii=False))
        return 1
    print(json.dumps({"status": "ok", "metrics": [m["name"] for m in facts["metrics"]],
                      "works": len(facts["representative_works"]), "missing": facts["missing"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
