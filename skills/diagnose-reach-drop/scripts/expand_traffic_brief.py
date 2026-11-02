#!/usr/bin/env python3
"""Expand a compact model-authored traffic workflow into the canonical contracts."""
from __future__ import annotations

import json
import math
import sys
from typing import Any

sys.dont_write_bytecode = True
from analyze_traffic import fields, require, text
from compile_traffic_brief import complete_brief


KINDS = {"recent_drop", "growth", "traffic_sources", "audience"}


def finite(value: Any, *, allow_none: bool = False) -> Any:
    if allow_none and value is None:
        return None
    require(
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
        and value >= 0,
        "brief measurements must be finite non-negative numbers or null",
    )
    return value


def copied(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def compact_target(value: Any, basis: Any = None) -> tuple[Any, str]:
    """Normalize the two common ways models express a numeric target."""
    if isinstance(value, dict):
        fields(value, {"value"}, {"metric", "unit", "basis", "population", "at_hours", "at"})
        basis = value.get("basis", basis)
        value = value["value"]
    value = finite(value)
    basis = {"median": "group_median"}.get(basis, basis)
    require(basis in {"each_record", "group_median"}, "unknown performance target basis")
    return value, basis


def source_kind(value: Any) -> str:
    value = text(value).lower().replace("-", "_")
    aliases = {
        "user_provided": "user_supplied",
        "user_supplied_owner_export": "owner_export",
        "user_provided_owner_export": "owner_export",
        "account_analysis": "public",
        "public_account_analysis": "public",
        "public_snapshot": "public",
    }
    return aliases.get(value, value)


def normalized_window(value: Any) -> Any:
    require(isinstance(value, dict), "brief window must be an object")
    result = copied(value)
    if "calendar" in result and isinstance(result["calendar"], dict):
        require(set(result) <= {"kind", "calendar"}, "brief calendar window has unknown fields")
        result = copied(result["calendar"])
        result["kind"] = "calendar"
    if "kind" not in result:
        if {"start", "end"} <= set(result):
            result["kind"] = "calendar"
        elif "hours" in result:
            result["kind"] = "post_age"
    return result


def audience_target(question: dict) -> dict:
    value = question["target"]
    require(isinstance(value, dict), "brief audience target must be an object")
    if {"dimension", "population", "categories", "criterion", "definition"} <= set(value):
        return copied(value)
    fields(value, {"criterion"},
           {"desired_categories", "categories", "category", "definition_status",
            "source_text", "confirmation"})
    category_fields = [key for key in ("desired_categories", "categories", "category")
                       if key in value]
    require(len(category_fields) == 1, "brief audience target needs one desired-category field")
    categories = value[category_fields[0]]
    if isinstance(categories, str):
        categories = [categories]
    require(isinstance(categories, list) and bool(categories), "audience target categories must be non-empty")
    criterion = value["criterion"]
    if isinstance(criterion, str):
        require(criterion in {"largest_share", "increase"}, "unknown audience target criterion")
        criterion = {"kind": criterion}
    else:
        require(isinstance(criterion, dict), "audience target criterion must be text or an object")
    status = value.get("definition_status", "user_explicit")
    require(status in {"user_explicit", "provisional"}, "unknown audience target definition status")
    definition = ({"status": "provisional",
                   "confirmation": text(value.get("confirmation") or value.get("source_text"))}
                  if status == "provisional" else
                  {"status": "user_explicit", "source_text": text(value.get("source_text"))})
    return {
        "dimension": text(question["dimension"]),
        "population": text(question["population"]),
        "categories": copied(categories),
        "criterion": copied(criterion),
        "definition": definition,
    }


def account_label(value: Any) -> str:
    """Accept a human label or the small account object models naturally produce."""
    if isinstance(value, str):
        return text(value)
    require(isinstance(value, dict), "brief account must be text or an object")
    fields(value, set(), {"label", "handle", "url", "kind", "locator"})
    for key in ("label", "handle", "locator", "url"):
        if value.get(key):
            return text(value[key])
    raise ValueError("brief account object needs label, handle, locator or url")


def aligned(group: dict, key: str, size: int, *, optional: bool = True) -> list | None:
    values = group.get(key)
    if values is None:
        require(optional, f"brief group requires {key}")
        return None
    require(isinstance(values, list) and len(values) == size,
            f"brief group {key} must align with its measurements")
    return values


def group_size(group: dict, keys: tuple[str, ...]) -> int:
    sizes = {len(group[key]) for key in keys if key in group and isinstance(group[key], list)}
    require(len(sizes) == 1 and next(iter(sizes)) > 0,
            "brief group measurement arrays must be non-empty and aligned")
    return next(iter(sizes))


def common_question(question: dict) -> dict:
    intent = question.get("intent", "diagnose")
    intent = {"decide": "diagnose"}.get(intent, intent)
    result = {
        "id": text(question["id"]),
        "kind": question["kind"],
        "intent": intent,
        "ask": text(question["ask"]),
        "selection_reason": text(question["selection_reason"]),
        "groups": [],
    }
    require(result["kind"] in KINDS, f"unknown question kind {result['kind']!r}; use one of {sorted(KINDS)}")
    require(result["intent"] in {"explain", "diagnose", "act"}, "invalid brief intent")
    return result


def add_performance(case: dict, question: dict, result: dict, source_id: str) -> None:
    allowed = {
        "id", "kind", "intent", "ask", "selection_reason", "metric", "format",
        "post_age_hours", "minimum_age_hours", "age_policy_reason", "target",
        "target_basis", "growth_basis", "groups", "population", "platform_status_check",
    }
    fields(question, {"id", "kind", "ask", "selection_reason", "metric", "groups"},
           allowed - {"id", "kind", "ask", "selection_reason", "metric", "groups"})
    metric = text(question["metric"])
    result["metric"] = metric
    if "target" in question:
        value, basis = compact_target(question["target"], question.get("target_basis"))
        result["target"], result["target_basis"] = value, basis
    elif "target_basis" in question:
        raise ValueError("performance target_basis requires target")
    for key in ("minimum_age_hours", "age_policy_reason"):
        if key in question:
            result[key] = copied(question[key])

    groups = question["groups"]
    require(isinstance(groups, list) and bool(groups), "brief groups must be non-empty")
    imported = all(isinstance(group, dict) and "record_ids" in group for group in groups)
    require(imported or all(isinstance(group, dict) and "values" in group for group in groups),
            "performance brief groups must all use record_ids or all use values")
    if imported:
        for group in groups:
            fields(group, {"label", "record_ids"})
            ids = group["record_ids"]
            require(isinstance(ids, list) and all(isinstance(item, str) for item in ids),
                    "brief record_ids must be text arrays")
            result["groups"].append({"label": text(group["label"]), "record_ids": ids})
        return

    format_ = text(question.get("format"))
    common_age = question.get("post_age_hours")
    if common_age is not None:
        finite(common_age)
    case.setdefault("performance", [])
    for group_index, group in enumerate(groups, 1):
        allowed_group = {
            "label", "values", "content_ids", "date_labels", "published_at", "observed_at",
            "published_on", "observed_on", "post_age_hours",
        }
        fields(group, {"label", "values"}, allowed_group - {"label", "values"})
        size = group_size(group, ("values",))
        values = aligned(group, "values", size, optional=False)
        content_ids = aligned(group, "content_ids", size)
        age = group.get("post_age_hours")
        if age is not None and not isinstance(age, list):
            group = {**group, "post_age_hours": [finite(age)] * size}
        dates = {key: aligned(group, key, size) for key in (
            "date_labels", "published_at", "observed_at", "published_on", "observed_on", "post_age_hours"
        )}
        refs = []
        for row_index, value in enumerate(values, 1):
            row_id = f"{result['id']}-g{group_index}-r{row_index}"
            content_id = content_ids[row_index - 1] if content_ids else row_id
            row = {
                "id": row_id,
                "source_ref": source_id,
                "content_id": text(content_id),
                "format": format_,
                "metrics": {metric: finite(value, allow_none=True)},
            }
            if common_age is not None:
                row["post_age_hours"] = common_age
            for key, entries in dates.items():
                if entries is not None:
                    target_key = "date_label" if key == "date_labels" else key
                    if key == "post_age_hours" and common_age is not None:
                        require(entries[row_index - 1] == common_age,
                                "group post age conflicts with the common post age")
                    row[target_key] = entries[row_index - 1]
            case["performance"].append(row)
            refs.append(row_id)
        result["groups"].append({"label": text(group["label"]), "record_ids": refs})


def add_period_totals(case: dict, question: dict, result: dict, source_id: str) -> None:
    required = {"id", "kind", "ask", "selection_reason", "metric", "population",
                "growth_basis", "groups"}
    fields(question, required, {"intent", "platform_status_check"})
    require(question.get("growth_basis") == "period_total", "period totals require growth_basis=period_total")
    result["metric"] = text(question["metric"])
    result["growth_basis"] = "period_total"
    groups = question["groups"]
    require(isinstance(groups, list) and len(groups) >= 2, "period-total brief needs at least two groups")
    case.setdefault("period_totals", [])
    result["groups"] = []
    for index, group in enumerate(groups, 1):
        fields(group, {"label", "value", "window", "posts_published"})
        row_id = f"{result['id']}-g{index}"
        case["period_totals"].append({
            "id": row_id,
            "source_ref": source_id,
            "metric": result["metric"],
            "population": text(question["population"]),
            "window": normalized_window(group["window"]),
            "value": finite(group["value"]),
            "posts_published": finite(group["posts_published"], allow_none=True),
        })
        result["groups"].append({"label": text(group["label"]), "record_ids": [row_id]})
def add_sources(case: dict, question: dict, result: dict, source_id: str) -> None:
    required = {"id", "kind", "ask", "selection_reason", "total_metric", "population", "source_name",
                "groups"}
    fields(question, required, {"intent", "window", "post_age_hours", "platform_status_check"})
    require(("window" in question) != ("post_age_hours" in question),
            "source brief needs exactly one window or post_age_hours")
    source_window = (copied(question["window"]) if "window" in question else
                     {"kind": "post_age", "hours": finite(question["post_age_hours"])})
    groups = question["groups"]
    require(isinstance(groups, list) and len(groups) == 2, "source brief requires two groups")
    case.setdefault("traffic_sources", [])
    for group_index, group in enumerate(groups, 1):
        fields(group, {"label"}, {"totals", "counts", "shares", "share_tolerances", "content_ids", "post_age_hours"})
        size = group_size(group, ("totals", "counts", "shares"))
        if "post_age_hours" in group:
            age = group["post_age_hours"]
            ages = age if isinstance(age, list) else [age] * size
            require(source_window.get("kind") == "post_age" and len(ages) == size,
                    "group post ages require the common post-age window and aligned measurements")
            require(all(finite(value) == source_window["hours"] for value in ages),
                    "group post age conflicts with the common source window")
        arrays = {key: aligned(group, key, size) for key in (
            "totals", "counts", "shares", "share_tolerances", "content_ids"
        )}
        refs = []
        for row_index in range(size):
            row_id = f"{result['id']}-g{group_index}-r{row_index + 1}"
            content_id = arrays["content_ids"][row_index] if arrays["content_ids"] else row_id
            row = {
                "id": row_id,
                "source_ref": source_id,
                "content_id": text(content_id),
                "metric": text(question["total_metric"]),
                "population": text(question["population"]),
                "source": text(question["source_name"]),
                "window": source_window,
            }
            for source_key, target_key in (("totals", "total"), ("counts", "count"),
                                           ("shares", "share"), ("share_tolerances", "share_tolerance")):
                if arrays[source_key] is not None:
                    row[target_key] = finite(arrays[source_key][row_index], allow_none=True)
            case["traffic_sources"].append(row)
            refs.append(row_id)
        result["groups"].append({"label": text(group["label"]), "record_ids": refs})


def add_audience(case: dict, question: dict, result: dict, source_id: str) -> None:
    required = {"id", "kind", "ask", "selection_reason", "dimension", "population", "target",
                "groups"}
    fields(question, required, {"intent", "window", "calendar", "platform_status_check"})
    require(not ("window" in question and "calendar" in question),
            "audience brief uses window or calendar, not both")
    common_window = (question["window"] if question.get("window") is not None else
                     {"calendar": question["calendar"]}
                     if question.get("calendar") is not None else None)
    result["target"] = audience_target(question)
    groups = question["groups"]
    require(isinstance(groups, list) and bool(groups), "audience brief groups must be non-empty")
    case.setdefault("audience", [])
    for index, group in enumerate(groups, 1):
        fields(group, {"label", "shares"}, {"window"})
        require(isinstance(group["shares"], dict),
                "audience brief group shares must map categories to values")
        row_id = f"{result['id']}-g{index}"
        case["audience"].append({
            "id": row_id,
            "source_ref": source_id,
            "dimension": text(question["dimension"]),
            "population": text(question["population"]),
            "window": (normalized_window(group.get("window", common_window))
                       if group.get("window", common_window) is not None else None),
            "shares": copied(group["shares"]),
        })
        result["groups"].append({"label": text(group["label"]), "record_ids": [row_id]})
    observed_categories = {
        category for group in groups for category in group["shares"].keys()
    }
    target_categories = set(result["target"]["categories"])
    require(not observed_categories or target_categories != observed_categories,
            "audience target cannot include every observed category")


def expand_case(payload: dict, language: str = "zh-CN") -> tuple[dict, dict]:
    """Compile selected analysis modules without inventing a delivery decision."""
    payload = complete_brief(payload, language)
    fields(payload, {"schema_version", "platform", "account", "source", "questions"}, {"coverage", "user_request"})
    require(payload["schema_version"] == "traffic-workflow-brief.v1", "unknown traffic brief schema")
    source = payload["source"]
    fields(source, {"kind", "locator"})
    source_id = "brief-source"
    case = {
        "schema_version": "traffic-case.v1",
        "platform": text(payload["platform"]).lower(),
        "account": account_label(payload["account"]),
        "sources": [{"id": source_id, "kind": source_kind(source["kind"]),
                     "locator": source["locator"]}],
        "questions": [],
    }
    if "coverage" in payload:
        case["coverage"] = copied(payload["coverage"])
    questions = payload["questions"]
    require(isinstance(questions, list) and bool(questions), "brief questions must be non-empty")
    ids = set()
    for question in questions:
        require(isinstance(question, dict), "brief question must be an object")
        question = copied(question)
        question.pop("target_source_text", None)
        result = common_question(question)
        require(result["id"] not in ids, "duplicate brief question id")
        ids.add(result["id"])
        if result["kind"] in {"recent_drop", "growth"}:
            groups = question.get("groups", [])
            period_shape = bool(groups) and all(
                isinstance(group, dict) and "value" in group and "window" in group
                and "values" not in group for group in groups
            )
            if result["kind"] == "growth" and period_shape:
                add_period_totals(case, question, result, source_id)
            else:
                add_performance(case, question, result, source_id)
        elif result["kind"] == "traffic_sources":
            add_sources(case, question, result, source_id)
        else:
            add_audience(case, question, result, source_id)
        case["questions"].append(result)
    return payload, case


if __name__ == "__main__":
    raise SystemExit("Use run_traffic_workflow.py; the expander is an internal contract adapter.")
