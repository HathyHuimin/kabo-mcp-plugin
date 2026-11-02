#!/usr/bin/env python3
"""Validate and normalize the model's small semantic analysis plan."""
from __future__ import annotations

import json
import sys
from typing import Any

sys.dont_write_bytecode = True
from analyze_traffic import fields, require, text


KINDS = {"recent_drop", "growth", "traffic_sources", "audience"}


def copied(value: Any) -> Any:
    """Return a JSON-safe deep copy."""
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def localized(language: str, en: str, zh: str) -> str:
    """Select one of the two delivery languages supported by the runner."""
    return zh if language == "zh-CN" else en


def question_defaults(question: dict, index: int, language: str) -> dict:
    """Validate one semantic question and add compiler-owned fields."""
    require(isinstance(question, dict), "traffic intent question must be an object")
    allowed = {
        "id", "kind", "intent", "platform_status_check", "metric", "format",
        "post_age_hours", "minimum_age_hours", "age_policy_reason", "target",
        "target_basis", "growth_basis", "groups", "population", "total_metric",
        "source_name", "window", "dimension", "calendar", "ask", "selection_reason", "target_source_text",
    }
    fields(question, {"kind", "groups"}, allowed - {"kind", "groups"})
    kind = question["kind"]
    require(kind in KINDS, f"unknown question kind {kind!r}; use one of {sorted(KINDS)}")
    result = copied(question)
    if kind == "growth":
        groups = result["groups"]
        period_shape = bool(groups) and all(
            isinstance(group, dict) and "value" in group and "window" in group
            and "values" not in group for group in groups
        )
        if period_shape:
            require(result.get("growth_basis", "period_total") == "period_total",
                    "period-total groups require growth_basis=period_total")
            result["growth_basis"] = "period_total"
        else:
            # Measurement shape determines the branch. Do not let a stale field
            # generate period-total actions for per-record measurements.
            result.pop("growth_basis", None)
    result["id"] = text(question.get("id") or f"q{index}")
    result["intent"] = question.get("intent", "diagnose")
    require(result["intent"] in {"explain", "diagnose", "act", "decide"},
            "invalid traffic intent")
    result["ask"] = text(question.get("ask") or localized(
        language,
        {
            "recent_drop": "Assess whether the supplied performance shows a recent drop.",
            "growth": "Assess the supplied performance level, trajectory and stated target.",
            "traffic_sources": "Explain the supplied traffic-source change on matched totals and windows.",
            "audience": "Assess the supplied audience distribution against the stated target.",
        }[kind],
        {
            "recent_drop": "判断所给表现是否存在近期下降。",
            "growth": "判断所给表现水平、阶段变化与用户目标。",
            "traffic_sources": "按匹配的总量和窗口解释所给流量来源变化。",
            "audience": "将所给受众分布与用户定义的目标比较。",
        }[kind],
    ))
    result["selection_reason"] = text(question.get("selection_reason") or localized(
        language,
        "The supplied groups match this requested comparison.",
        "用户提供的分组与该问题所需比较一致。",
    ))
    return result


def complete_brief(payload: dict, language: str = "zh-CN") -> dict:
    """Compile a model-authored brief into the expander's internal shape."""
    fields(payload, {"schema_version", "platform", "account", "questions"},
           {"source", "coverage", "user_request"})
    require(payload["schema_version"] == "traffic-workflow-brief.v1",
            "unknown traffic brief schema")
    require(language in {"en", "zh-CN"}, "unsupported delivery language")
    platform = text(payload["platform"]).lower()
    require(platform in {"youtube", "tiktok", "instagram"},
            "traffic intent platform must be youtube, tiktok or instagram")
    questions = payload["questions"]
    require(isinstance(questions, list) and bool(questions),
            "traffic intent questions must be a non-empty array")
    source = payload.get("source", {"kind": "user_supplied", "locator": "user message"})
    result = {
        "schema_version": payload["schema_version"],
        "platform": platform,
        "account": copied(payload["account"]),
        "source": copied(source),
        "questions": [
            question_defaults(question, index, language)
            for index, question in enumerate(questions, 1)
        ],
    }
    if "coverage" in payload:
        result["coverage"] = copied(payload["coverage"])
    if "user_request" in payload:
        result["user_request"] = text(payload["user_request"])
    return result


if __name__ == "__main__":
    raise SystemExit("Use run_traffic_workflow.py; the brief compiler is internal.")
