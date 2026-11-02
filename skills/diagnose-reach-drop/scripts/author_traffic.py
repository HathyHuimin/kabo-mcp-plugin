"""Resolve declarative cohorts and bind semantic inputs to the original request."""
from __future__ import annotations

import copy
import re
import sys

sys.dont_write_bytecode = True
from analyze_traffic import fields, require, text, time

INTENTS = {"explain", "diagnose", "act"}
INTENT_ALIASES = {"action": "act", "actions": "act", "next_steps": "act", "next_step": "act",
                  "recommend": "act", "advice": "act", "explanation": "explain"}
FORMAT_ALIASES = {"reels": "reel", "shorts": "short", "videos": "video", "long": "video", "posts": "feed",
                  "post": "feed", "carousel": "feed", "image": "feed"}


def selector_format(value: str, formats: set[str]) -> str:
    """Match a written format to this analysis's stored formats, case- and plural-insensitively."""
    key = value.strip().casefold().replace("-", "_").replace(" ", "_")
    candidates = [key, FORMAT_ALIASES.get(key, key)]
    for platform in ("youtube", "instagram", "tiktok"):
        candidates += [f"{platform}_{candidate}" for candidate in list(candidates)]
    lowered = {stored.casefold(): stored for stored in formats}
    return next((lowered[candidate] for candidate in candidates if candidate in lowered), value)


def resolve_plan(payload: dict, analysis: dict | None) -> dict:
    result = copy.deepcopy(payload)
    request = result.get("user_request")
    if request is not None:
        text(request)
    for index, question in enumerate(result.get("questions", [])):
        path = f"questions[{index}]"
        require(isinstance(question, dict), f"{path}: expected an object")
        if isinstance(question.get("kind"), str):
            question["kind"] = question["kind"].strip().casefold().replace("-", "_").replace(" ", "_")
        intent = question.get("intent")
        if isinstance(intent, str):
            intent = intent.strip().casefold().replace(" ", "_")
            intent = INTENT_ALIASES.get(intent, intent)
        question["intent"] = intent if intent in INTENTS else "diagnose"
        if question.get("kind") in {"recent_drop", "growth"} and "target" in question:
            target = question["target"]
            if isinstance(target, dict) and "target_source_text" in target:
                question.setdefault("target_source_text", target.pop("target_source_text"))
            if request is not None:
                quote = question.get("target_source_text")
                require(isinstance(quote, str) and bool(quote.strip()) and quote in request,
                        f"{path}.target_source_text: quote the user's explicit goal from user_request; "
                        "omit target when the user gave no numeric goal")
                value = target.get("value") if isinstance(target, dict) else target
                numbers = re.findall(r"(?<![\d.])(\d+(?:,\d{3})*(?:\.\d+)?)(?![\d.])\s*(万|亿|[kKmM](?![a-zA-Z]))?", quote)
                scales = {"": 1, "k": 1000, "m": 1000000, "万": 10000, "亿": 100000000}
                require(value in [float(n.replace(',', '')) * scales[unit.lower()] for n, unit in numbers],
                        f"{path}.target: use the numeric value stated in target_source_text; "
                        "otherwise omit target and retain the user's goal in ask")
        for group_index, group in enumerate(question.get("groups", [])):
            if not isinstance(group, dict) or "select" not in group:
                continue
            group_path = f"{path}.groups[{group_index}].select"
            require(analysis is not None, f"{group_path}: requires AccountAnalysis")
            fields(group, {"label", "select"})
            selector = group["select"]
            fields(selector, set(), {"format", "start", "stop", "published_from", "published_before"})
            require(bool(selector), f"{group_path}: specify a range or format")
            rows = analysis.get("rankings", [])
            require(isinstance(rows, list) and all(isinstance(row, dict) for row in rows),
                    f"{group_path}: invalid AccountAnalysis rankings")
            rows = sorted(rows, key=lambda row: (time(row["published_at"]), str(row["content_id"])))
            if "format" in selector:
                formats = {str(row.get("format")) for row in rows if row.get("format")}
                format_ = selector_format(text(selector["format"]), formats)
                require(format_ in formats, f"{group_path}.format {selector['format']!r} matches no records; "
                        f"formats in this analysis: {sorted(formats)}")
                rows = [row for row in rows if row.get("format") == format_]
            for key in ("published_from", "published_before"):
                # A bare calendar date bounds a whole UTC day; the plan need not invent a timezone for it.
                if isinstance(selector.get(key), str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", selector[key]):
                    selector[key] += "T00:00:00Z"
            if "published_from" in selector:
                start_time = time(selector["published_from"])
                rows = [row for row in rows if time(row["published_at"]) >= start_time]
            if "published_before" in selector:
                end_time = time(selector["published_before"])
                if "published_from" in selector:
                    require(start_time < end_time, f"{group_path}: date range must be ordered")
                rows = [row for row in rows if time(row["published_at"]) < end_time]
            start, stop = selector.get("start", 0), selector.get("stop", len(rows))
            require(type(start) is int and type(stop) is int and 0 <= start <= stop <= len(rows),
                    f"{group_path}: require 0 <= start <= stop <= {len(rows)} after filters")
            group.pop("select")
            group["record_ids"] = [str(row["content_id"]) for row in rows[start:stop]]
    return result


def synthesis_hint(questions: list[dict]) -> str:
    """The one shape the model writes after reading the evidence."""
    ids = ", ".join(question["id"] for question in questions)
    return "\n".join([
        "Next: write your analysis as one JSON object, keyed by question id (" + ids + "):",
        '{"questions": {"<id>": {"interpretation": text, "hypotheses": [text], '
        '"need": {"items": [text], "why": text}, '
        '"next_steps": [{"do": text, "check": text, "then": text, "opening": text}]}}}',
        "Every field is optional. Code already writes the conclusion, evidence and data limits; add judgment, "
        "unconfirmed explanations, the data still needed, and next steps (what to do, what to check and when, "
        "what to do with each result). Cite account numbers only as the {fact:…} placeholders listed below; "
        "interpretation types only numbers this run's evidence states (a printed fact, date or record metric, the "
        "user's request, or a retrieved title quoted verbatim). Next-step durations and counts are proposals and may be typed.",
    ])
