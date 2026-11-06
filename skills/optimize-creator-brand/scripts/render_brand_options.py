#!/usr/bin/env python3
"""Render validated creator brand options without another model call.

Fixed labels are English for every delivery language; the strings the model wrote (options,
rationales, positioning, missing items) are printed as written, in the creator's language.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import sys
# Sibling imports must not leave a __pycache__ in the signed, checksummed skill directory.
sys.dont_write_bytecode = True
from validate_brand_options import validate_result, validate_spec, validate_brief, validate_kit


TASK_LABELS = {
    "youtube_channel_name": "YouTube channel name options",
    "instagram_bio": "Instagram bio options",
    "instagram_username": "Instagram username options",
    "tiktok_bio": "TikTok bio options",
    "tiktok_username": "TikTok username options",
}

STATUS_LABELS = {
    "unverified": "unverified",
    "available": "available",
    "taken": "taken",
    "check_failed": "check failed",
}

AUTHORITY_LABELS = {
    "official_help_page": "platform help page",
    "official_secondary": "platform secondary page",
    "creator_reported": "creator reported",
    "unofficial": "third-party source",
    "derived_working_size": "working size (not stated by the platform)",
}

SELECTION_LABELS = {
    "curated_highlight": "curated highlight - picked for performance, not typical",
    "recent_sample": "recent post, not picked",
}


def number(value: float) -> str:
    return f"{value:,}" if float(value).is_integer() else f"{value:,.2f}"


def render_kit(payload: dict[str, Any]) -> str:
    """Render a brand kit. Each number prints with where it came from and each highlight says it is
    one, because a kit is exactly what a brand will read as the creator's typical performance."""
    validate_kit(payload)
    identity = payload["identity"]
    lines = ["# Brand kit", ""]
    lines += ["## Who I am", identity["positioning"], ""]
    lines.append(f"- Niche: {identity['niche']}")
    lines.append(f"- Audience: {identity['audience']}")
    lines.append("")

    if payload["metrics"]:
        lines.append("## Numbers")
        for metric in payload["metrics"]:
            where = f"{metric['platform']} @{metric['handle']}"
            if metric["source"] == "resolved_public":
                origin = f"public profile, retrieved {metric['retrieved_at']} | {metric['source_url']}"
            else:
                origin = "supplied by the creator"
            lines.append(f"- {metric['name']}: {number(metric['value'])} {metric['unit']} ({where} | {origin})")
        lines.append("")

    if payload["representative_works"]:
        lines.append("## Representative work")
        for work in payload["representative_works"]:
            lines.append(f"- [{work['title']}]({work['url']}) — {SELECTION_LABELS[work['selection']]}")
        lines.append("")

    lines.append("## How we can work together")
    for item in payload["collaboration_formats"]:
        lines.append(f"- {item}")
    lines.append("")

    if payload["contact"]:
        contact = payload["contact"]
        lines += ["## Contact", f"- {contact['channel']}: {contact['value']}", ""]

    official = [item for item in payload["profile_checklist"] if item["basis"] == "official"]
    inferred = [item for item in payload["profile_checklist"] if item["basis"] == "inferred"]
    if official or inferred:
        lines.append("## What brands look at on a profile")
        if official:
            lines.append("### Fields the platform itself lists")
            for item in official:
                lines.append(f"- {item['item']} ({item['source_url']})")
        if inferred:
            lines.append("### Our suggestions (inferred, not a platform requirement)")
            for item in inferred:
                lines.append(f"- {item['item']}")
        lines.append("")

    if payload["missing"]:
        lines.append("## Still missing (left blank, not filled in)")
        for item in payload["missing"]:
            lines.append(f"- {item}")
        lines.append("")

    lines += ["## Rates",
              "Rates are not part of this kit; they are quoted separately from the format, usage rights and term."
              + " " + payload["rate_card"]["note"], ""]
    return "\n".join(lines).rstrip() + "\n"


def render_brief(payload: dict[str, Any]) -> str:
    """Render a brand strategy brief. Assumptions print as assumptions: the brief is usable without
    an account, and what it had to guess must not read back as something the creator confirmed."""
    validate_brief(payload)
    lines = ["# Brand strategy brief", ""]
    lines += ["## Positioning", payload["positioning"], ""]
    lines += ["## Audience", payload["audience"]["summary"], ""]
    for need in payload["audience"]["needs"]:
        lines.append(f"- {need}")
    lines.append("")
    content = payload["content"]
    lines.append("## Content pillars")
    for pillar in content["pillars"]:
        lines.append(f"- {pillar}")
    lines.append("")
    if content["formats"]:
        lines.append("## Formats")
        for item in content["formats"]:
            lines.append(f"- {item}")
        lines.append("")
    if content["exclusions"]:
        lines.append("## Out of scope")
        for item in content["exclusions"]:
            lines.append(f"- {item}")
        lines.append("")
    lines += ["## Tone", payload["tone"], ""]
    if payload["assumptions"]:
        lines.append("## Assumptions, not confirmed facts")
        for item in payload["assumptions"]:
            lines.append(f"- {item}")
        lines.append("")
    handoff = payload["handoff"]
    lines.append("## Machine handoff")
    if handoff["status"] == "ready":
        handles = ", ".join(f"{a['platform']} {a['handle']}" for a in handoff["accounts_provenance"])
        lines.append("Ready. A creator-profile.v3 artifact is attached for the topic step; accounts: " + handles)
    else:
        lines.append(f"Not yet: {handoff['blocked_reason']} The brief above is usable as it stands; "
                     "what is missing is only the account information the topic step needs.")
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_spec(payload: dict[str, Any]) -> str:
    """Render a visual identity spec. Every measurement prints where it came from, because a
    number whose source is not on the page is the one a creator will repeat as official."""
    validate_spec(payload)
    lines = ["# Profile visual spec", ""]

    lines.append("## Palette")
    for colour in payload["palette"]:
        lines.append(f"- **{colour['name']}** `{colour['hex']}` — {colour['role']}")
    lines.append("")

    lines.append("## Typography")
    for face in payload["typography"]:
        lines.append(f"- **{face['role']}**: {face['family']} ({face['availability']})")
    lines.append("")

    lines.append("## How it lands")
    for surface in payload["surfaces"]:
        lines.append(f"- **{surface['surface']}**: {surface['direction']}")
    lines.append("")

    if payload["measurements"]:
        lines.append("## Measurements")
        for item in payload["measurements"]:
            label = AUTHORITY_LABELS[item["authority"]]
            lines.append(f"- **{item['surface']}**: {item['claim']}")
            if item["authority"] in {"official_help_page", "official_secondary"}:
                lines.append(f"  - {label} | retrieved {item['retrieved_at']} | {item['source_url']}")
                lines.append(f"  - > {item['quote']}")
            else:
                lines.append(f"  - {label} | {item['basis']}")
        lines.append("")

    if payload["not_published"]:
        lines.append("## Sizes the platform does not publish")
        for gap in payload["not_published"]:
            lines.append(f"- **{gap['surface']}**: the platform's help pages do not state a pixel size for "
                         f"this; the figure above is a working size, not a platform statement. ({gap['checked_url']})")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def render(payload: dict[str, Any], request: dict[str, Any] | None = None) -> str:
    validate_result(payload, request)
    lines = [f"# {TASK_LABELS[payload['task']]}", ""]
    username_task = payload["task"].endswith("_username")
    show_live_statuses = (
        payload["task"] == "tiktok_username"
        and any(option.get("availability_status", "unverified") != "unverified" for option in payload["options"])
    )
    shortlist = payload.get("recommended") or []
    if shortlist:
        lines.append("## Start with these")
        lines.append("")
        for rank, entry in enumerate(shortlist, start=1):
            shown = f"@{entry['value']}" if username_task else entry["value"]
            lines.append(f"{rank}. **{shown}** — {entry['reason']}")
        lines.append("")

    for index, option in enumerate(payload["options"], start=1):
        value = option["value"]
        if username_task:
            value = f"@{value}"
        heading = f"## {index}. {value}"
        if option.get("strategy"):
            # The angle belongs next to the option: without it a list of ten reads as ten spellings
            # of one idea, and the creator has nothing to choose between.
            heading += f" - {option['strategy']}"
        lines.extend([heading, "", option["rationale"], ""])
        if show_live_statuses:
            label = STATUS_LABELS[option.get("availability_status", "unverified")]
            lines.extend([f"_Status: {label}_", ""])
    if username_task:
        if payload["task"] == "instagram_username":
            note = "These candidates are unverified; confirm availability in Instagram signup or Edit Profile."
        elif show_live_statuses:
            note = ("Statuses come from a live check after selection. Recheck unverified or failed candidates; "
                    "a check does not reserve a name.")
        else:
            note = "These candidates are unverified. Check a selected name live; a check does not reserve it."
        lines.extend([f"> {note}", ""])
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("--output", required=True)
    parser.add_argument("--request", help="request.json; binds the delivered option count to it")
    args = parser.parse_args()
    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    kind = payload.get("schema_version") if isinstance(payload, dict) else None
    if kind == "creator-brand-kit.v1":
        text = render_kit(payload)
    elif kind == "creator-brand-visual-spec.v1":
        text = render_spec(payload)
    elif kind == "creator-brand-strategy-brief.v1":
        text = render_brief(payload)
    else:
        request = json.loads(Path(args.request).read_text(encoding="utf-8")) if args.request else None
        text = render(payload, request)
    Path(args.output).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
