"""Project existing baseline age rules without changing metrics or rankings."""
from __future__ import annotations

from datetime import timedelta
from typing import Any
from account_analyzers import parse_time


def baseline_maturity_context(analysis: dict[str, Any]) -> dict[str, Any] | None:
    # 与 Analyzer 同口径：发布当天算第 1 天，不能解释成已满 N 个 24 小时。
    if not analysis["baselines"]:
        return None
    scope = analysis["scope"]
    end = scope.get("window", {}).get("end")
    anchor = parse_time(f"{end}T23:59:59+00:00") if end and "T" not in end else parse_time(end or scope["observed_at"])
    by_format = []
    for baseline in analysis["baselines"]:
        days = baseline["maturity_days"]
        if not isinstance(days, int) or isinstance(days, bool) or days < 1:
            raise ValueError("baseline maturity_days must be a positive integer")
        by_format.append({
            "format": baseline["format"], "maturity_days": days,
            "cutoff_at": (anchor - timedelta(days=days - 1)).isoformat(),
            "minimum_elapsed_days_at_anchor": days - 1,
        })
    return {
        "convention": "publication_day_counts_as_day_one",
        "anchor_source": "scope.window.end" if end else "scope.observed_at",
        "anchor_at": anchor.isoformat(), "comparison": "published_at <= cutoff_at",
        "by_format": by_format,
    }


def maturity_by_format(analysis: dict[str, Any]) -> dict[str, dict[str, Any]]:
    context = baseline_maturity_context(analysis)
    return {row["format"]: row for row in context["by_format"]} if context else {}


def content_maturity(published_at: str, policy: dict[str, Any] | None) -> str:
    if policy is None:
        return "unavailable"
    return "mature" if parse_time(published_at) <= parse_time(policy["cutoff_at"]) else "too_recent"


def evidence_age(row: dict[str, Any], policies: dict[str, dict[str, Any]]) -> dict[str, Any]:
    policy = policies.get(row["format"])
    return {
        "published_at": row["published_at"],
        "baseline_maturity": content_maturity(row["published_at"], policy),
        **({"baseline_cutoff_at": policy["cutoff_at"]} if policy else {}),
    }
