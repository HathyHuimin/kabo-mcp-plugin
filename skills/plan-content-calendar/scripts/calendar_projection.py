#!/usr/bin/env python3
"""Pure deterministic CSV/ICS projections for ContentCalendar."""

from __future__ import annotations

import csv
import io
from datetime import timezone
from typing import Any

import sys
# Sibling imports must not leave a __pycache__ in the signed, checksummed skill directory.
sys.dont_write_bytecode = True
from calendar_common import parse_offset_datetime

CSV_FIELDS = [
    "slot_id",
    "week_index",
    "planned_date",
    "publish_at",
    "timezone",
    "account_ref",
    "platform",
    "idea_format",
    "production_format",
    "publish_format",
    "title",
    "content_promise",
    "hook_opening_line",
    "planning_state",
    "timing_source",
    "recommendation_outcome",
    "recommendation_confidence",
    "schedule_state",
    "production_state",
    "production_source",
    "production_remaining_minutes",
    "production_as_of",
    "capacity_status",
    "publish_prep_status",
    "required_actions",
]


def _csv_cell(value: Any) -> Any:
    """Prevent spreadsheet formula execution while preserving visible source text."""

    if not isinstance(value, str):
        return value
    probe = value.lstrip(" \t\r\n")
    if probe.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def csv_bytes(calendar: dict[str, Any]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS, lineterminator="\n")
    writer.writeheader()
    for slot in calendar.get("slots") or []:
        target = slot["target"]
        production = slot["production"]
        timing = slot["timing"]
        row = {
            "slot_id": slot["slot_id"],
            "week_index": slot["week_index"],
            "planned_date": slot["planned_date"],
            "publish_at": slot["publish_at"] or "",
            "timezone": calendar["planning_window"]["timezone"],
            "account_ref": target["account_ref"],
            "platform": target["platform"],
            "idea_format": target["idea_format"],
            "production_format": target["production_format"],
            "publish_format": target["publish_format"],
            "title": slot["title"],
            "content_promise": slot["content_preview"]["content_promise"],
            "hook_opening_line": slot["content_preview"]["hook"]["opening_line"],
            "planning_state": slot["planning_state"],
            "timing_source": timing["source"],
            "recommendation_outcome": timing["recommendation_outcome"] or "",
            "recommendation_confidence": timing["recommendation_confidence"] or "",
            "schedule_state": timing["schedule_state"] or "",
            "production_state": production["state"],
            "production_source": production["source"],
            "production_remaining_minutes": "" if production["remaining_minutes"] is None else production["remaining_minutes"],
            "production_as_of": production["as_of"] or "",
            "capacity_status": production["capacity_status"],
            "publish_prep_status": slot["publish_prep"]["status"],
            "required_actions": " | ".join(slot["required_actions"]),
        }
        writer.writerow({key: _csv_cell(value) for key, value in row.items()})
    return stream.getvalue().encode("utf-8")


def _ics_escape(value: str) -> str:
    normalized = str(value).replace("\r\n", "\n").replace("\r", "\n")
    normalized = "".join(" " if ord(char) < 32 and char != "\n" else char for char in normalized)
    return normalized.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold_line(line: str) -> list[str]:
    """RFC 5545 content-line folding: at most 75 UTF-8 octets per physical line."""

    chunks: list[str] = []
    current = ""
    limit = 75
    for char in line:
        candidate = current + char
        if len(candidate.encode("utf-8")) > limit and current:
            chunks.append(current)
            current = char
            limit = 74  # continuation space consumes one octet
        else:
            current = candidate
    chunks.append(current)
    return [chunks[0], *(" " + chunk for chunk in chunks[1:])]


def ics_bytes(calendar: dict[str, Any]) -> bytes:
    zone_name = calendar["planning_window"]["timezone"]
    created = parse_offset_datetime(calendar["created_at"]).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    logical = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Kabo//Content Calendar V1//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_ics_escape('Kabo Content Calendar')}",
        f"X-WR-TIMEZONE:{_ics_escape(zone_name)}",
    ]
    scopes = {
        scope.get("action"): set(scope.get("slot_ids") or [])
        for scope in ((calendar.get("interaction") or {}).get("runtime_handoff") or {}).get("action_scopes") or []
    }
    pre_scope = scopes.get("create_pre_publish_reminders", set())
    post_scope = scopes.get("activate_post_publish_reminders_after_receipt", set())
    for slot in calendar.get("slots") or []:
        timing = slot["timing"]
        logical.extend([
            "BEGIN:VEVENT",
            f"UID:{_ics_escape(calendar['calendar_id'] + '-' + slot['slot_id'])}@kabo.local",
            f"DTSTAMP:{created}",
        ])
        if slot["publish_at"]:
            instant = parse_offset_datetime(slot["publish_at"]).astimezone(timezone.utc)
            logical.append(f"DTSTART:{instant.strftime('%Y%m%dT%H%M%SZ')}")
        else:
            logical.append(f"DTSTART;VALUE=DATE:{slot['planned_date'].replace('-', '')}")
        description = (
            f"Promise: {slot['content_preview']['content_promise']} | "
            f"Opening hook: {slot['content_preview']['hook']['opening_line']} | "
            f"Platform: {slot['target']['platform']} | Publish format: {slot['target']['publish_format']} | "
            f"Timing: {timing['source']} | Recommendation: {timing['recommendation_outcome'] or 'none'}"
            f"/{timing['recommendation_confidence'] or 'none'} | Schedule: {timing['schedule_state'] or 'none'} | "
            f"Prep: {slot['publish_prep']['status']} | State: {slot['planning_state']}"
        )
        logical.extend([
            f"SUMMARY:{_ics_escape(slot['title'])}",
            f"DESCRIPTION:{_ics_escape(description)}",
            f"X-KABO-SLOT-ID:{_ics_escape(slot['slot_id'])}",
            f"X-KABO-TIMING-SOURCE:{_ics_escape(timing['source'])}",
        ])
        post_offsets = [
            str(reminder["offset_minutes"])
            for reminder in slot.get("reminders") or []
            if slot["slot_id"] in post_scope and reminder["type"] == "post_publish_attention"
        ]
        if post_offsets:
            logical.append(f"X-KABO-POST-PUBLISH-REMINDER-TEMPLATE:{_ics_escape('connector_published_at+' + ','.join(post_offsets) + 'm')}")
        if slot["publish_at"] and slot["slot_id"] in pre_scope:
            for reminder in slot.get("reminders") or []:
                if reminder["type"] != "pre_publish":
                    continue
                minutes = abs(int(reminder["offset_minutes"]))
                logical.extend([
                    "BEGIN:VALARM",
                    "ACTION:DISPLAY",
                    f"TRIGGER:-PT{minutes}M",
                    f"DESCRIPTION:{_ics_escape('Pre-publish check: ' + slot['title'])}",
                    "END:VALARM",
                ])
        logical.append("END:VEVENT")
    logical.append("END:VCALENDAR")
    physical: list[str] = []
    for line in logical:
        physical.extend(_fold_line(line))
    return ("\r\n".join(physical) + "\r\n").encode("utf-8")
