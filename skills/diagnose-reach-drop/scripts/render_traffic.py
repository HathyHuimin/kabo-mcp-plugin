#!/usr/bin/env python3
"""Compact calculated evidence for final delivery, not a complete account report."""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True
from analyze_traffic import require, window


LIMITS = {
    "unknown_audience_window": "The audience measurement window is unknown; the supplied distribution cannot be assigned to the requested period.",
    "excluded_by_explicit_age_policy": "Newer posts were excluded by this analysis's explicit age policy.",
    "missing_requested_metric": "Some requested measurements are missing, not zero or another metric.",
    "missing_same_format_comparator": "Some formats lack a usable comparator.",
    "single_observation_not_repeatability_evidence": "A single observation does not establish repeatability.",
    "different_observation_ages": "The known observation ages differ; those records have age information, but the cumulative-count difference is not an age-matched performance change.",
    "unknown_observation_ages": "Some observation ages are unknown; age matching is unverified.",
    "excluded_unknown_age_under_explicit_policy": "Posts with unknown ages were excluded under the explicit age policy.",
    "chronology_uses_declared_groups": "Chronology follows the supplied groups; complete publication dates were unavailable for verification.",
    "format_unverified": "Content format is unverified.",
    "observed_periods_only_not_long_term_proof": "Findings cover the selected periods, not proof of long-term stagnation.",
    "sample_sum_is_not_calendar_traffic": "Summed post counts are not account traffic earned during a calendar period.",
    "period_total_not_per_post_improvement": "Period-total growth is not per-post improvement; posting volume is a concurrent factor, not an established cause.",
    "incompatible_metric_population_source_or_window": "Metric, population, source or window differs; no cross-period change was calculated.",
    "nonadditive_metric_cannot_sum_across_content": "Deduplicated reach cannot be summed across posts.",
    "different_calendar_windows_within_group": "Calendar windows differ within a group.",
    "absolute_counts_estimated_from_reported_shares": "Source counts are estimated from reported shares and inherit their rounding.",
    "unequal_post_counts_sum_not_per_post_change": "Post counts differ; use per-post means/medians for per-post change, not the change in sample sums.",
    "share_undefined_when_total_missing_or_zero": "Weighted share is undefined when totals are missing or zero.",
    "overlapping_or_reversed_calendar_windows": "Calendar windows overlap or are reversed.",
    "audience_dimension_or_population_mismatch": "Actual and target audience dimensions or populations differ; fit cannot be assessed directly.",
    "target_categories_missing_in_partial_distribution": "The audience breakdown is partial; target share is bounded, not exact.",
    "demographics_do_not_establish_interests_or_algorithm_category": "Demographics do not establish interests or an internal platform category.",
}

MISSING = {
    "source_observations_not_selected": "This comparison has no complete earlier/recent source records selected. Reuse available totals and windows; obtain source counts or shares and confirm they match the same observations.",
    "audience_measurement_window": "the actual measurement window of this audience breakdown",
    "one_actual_calendar_total_per_comparison_period": "one actual calendar total per period, not summed cumulative post counts",
    "earlier_and_recent_performance_records": "earlier and recent performance records",
    "same_post_age_measurements_for_stronger_comparison": "measurements captured at matching post ages",
    "earlier_and_recent_source_totals_and_shares": "earlier and recent source counts/shares matched to the contents, population and windows of available totals; supply matching totals only where missing",
    "matching_measurement_basis": "source records with the same measurement basis",
    "matching_source_totals": "missing totals on the same measurement basis",
    "matching_source_counts_or_shares": "missing source counts, or shares paired with matching totals",
    "deduplicated_period_totals": "deduplicated totals for each period",
    "aligned_calendar_window": "aligned calendar-window data",
    "disjoint_ordered_windows": "ordered, disjoint earlier and recent windows",
    "target_audience_dimension_population_and_categories": "target audience dimension, population and categories",
    "actual_audience_distribution_for_requested_window": "the actual audience breakdown for the requested window",
    "actual_audience_on_target_dimension_and_population": "actual audience data matching the target dimension and population",
}


def fmt(value):
    return "—" if value is None else f"{value:,.2f}".rstrip("0").rstrip(".")


def percent(value):
    return "—" if value is None else f"{value * 100:.2f}%"


def window_label(value, _language=None):
    if value is None:
        return "Measurement window unknown"
    if value["kind"] == "calendar":
        _, hours, precision = window(value)
        duration = f"{fmt(hours / 24)} days" if precision == "date" else f"{fmt(hours)} hours"
        return f"Calendar window: {value['start']} → {value['end']} (end exclusive, {duration})"
    return f"Post-publication observation age: {fmt(value['hours'])} hours"


def measurement_window(value, _language=None):
    label = window_label(value)
    if value is None:
        return label
    if value["kind"] == "calendar":
        return label + "; retest using equal-duration calendar windows on the same measurement basis, not individual post-age measurements."
    return label + "; retest at the same post age."


def snapshot_context(value, _language=None):
    snapshot = value.get("imported_snapshot")
    if snapshot is None:
        return ""
    prefix = f"{snapshot['record_count']} · {snapshot['observed_at']}"
    return ("Imported account snapshot (records · observed at): " + prefix +
            ". Post ages are observation time minus publication time. Upstream maturity parameters filter samples, not define a common observation age. Group statistics below are recalculated from the selected records.")


def group_label(group):
    dates = group["publication_range"]
    return f"{dates[0][:10]} → {dates[1][:10]}" if dates else group["label"]


def coverage_gaps(value: dict) -> list[str]:
    """Keep authored and imported source gaps, in order, without changing native evidence."""
    imported = value.get("imported_snapshot") or {}
    source = ((imported.get("coverage") or {}).get("source_coverage") or {}).get("limitations") or []
    authored = value.get("coverage") or []
    require(isinstance(authored, list) and isinstance(source, list)
            and all(isinstance(gap, str) for gap in [*authored, *source]), "coverage gaps must be text arrays")
    return list(dict.fromkeys([*authored, *source]))


def render(value: dict, language: str = "en") -> str:
    """Render fixed machine evidence in English; final delivery owns localization."""
    lines = ["# Traffic analysis", "",
             "These measurements describe performance, not platform enforcement or its causes."]
    context = snapshot_context(value)
    if context:
        lines += ["", context]
    coverage = coverage_gaps(value)
    if coverage:
        lines += ["", "## 样本与证据限制" if language == "zh-CN" else "## Coverage gaps"]
        lines += ["- " + item for item in coverage]
    for question in value["questions"]:
        lines += ["", "## " + question["ask"], ""]
        target = question.get("audience_target")
        if target:
            basis = " · ".join([target["population"], target["dimension"],
                                ", ".join(target["categories"])])
            criterion = target["criterion"]
            criterion_text = {
                "largest_share": "share greater than every other reported category",
                "increase": "increase over a same-basis baseline",
            }.get(criterion["kind"]) or "share of at least " + percent(criterion["value"])
            definition = target["definition"]
            provisional = definition["status"] == "provisional"
            heading = ("Provisional operating target (confirm): " if provisional else
                       "User-stated target basis (not an observed result): ")
            lines.append(heading + basis + " · " + criterion_text)
            if provisional:
                lines.append("Please confirm: " + definition["confirmation"])
            lines += ["Evaluate this target on its own basis, not a proxy or required growth in unrelated metrics. State operational guardrails separately.", ""]
        if not question["facts"]:
            lines.append("The available evidence cannot resolve this question.")
        if question.get("available_measurements"):
            lines.append("Measurements already available elsewhere in this case (source-data matching not established):")
            for available in question["available_measurements"]:
                basis = "post records" if available["basis"] == "performance" else "calendar-period total records"
                lines.append(f"- {available['metric']} · {len(available['record_ids'])} {basis}")
            lines.append("These measurements are not missing. Confirm matching contents, population and window, then obtain the missing source counts or shares; these values alone are not source traffic.")
        for fact in question["facts"]:
            if fact["type"] == "performance":
                lines += [f"{fact['format']} · {fact['metric']}", "",
                          "| Group | Measured/selected | Median | Peak | Observation age (hours) |",
                          "|---|---:|---:|---:|---|"]
                for group in fact["groups"]:
                    ages = group["age_hours"]
                    span = "—" if ages is None else f"{fmt(ages['min'])}–{fmt(ages['max'])}"
                    lines.append(f"| {group_label(group)} | {group['n']}/{group['selected_n']} | {fmt(group['median'])} | {fmt(group['max'])} | {span} |")
                if fact["change"]:
                    change = fact["change"]
                    last, first = fact["groups"][-1], fact["groups"][0]
                    basis = f"{group_label(last)} ({fmt(last['median'])}) ÷ {group_label(first)} ({fmt(first['median'])})"
                    result = "undefined with zero baseline" if change["ratio"] is None else fmt(change["ratio"]) + "×"
                    lines += ["", "Median comparison (last ÷ first): " + basis + " = " + result]
                if "target_evaluation" in fact:
                    target_evaluation = fact["target_evaluation"]
                    current = target_evaluation["groups"][-1]
                    lines.append(f"User target ({fact['metric']}): {fmt(target_evaluation['value'])}")
                    if target_evaluation["basis"] == "each_record":
                        item = f"Target basis: each record; last group meeting target: {current['met_n']}/{current['n']}"
                        if current["decision_value"] is not None:
                            item += "; minimum " + fmt(current["decision_value"])
                        lines.append(item)
                    else:
                        lines.append("Target basis: group median; last median " + fmt(current["decision_value"]))
                    lines.append("Evaluate the goal on the user's stated basis. Other statistics may explain results, but cannot replace the goal or become unrelated co-gates.")
                if fact["target_gap"] is not None:
                    basis = "Last minimum" if fact.get("target_basis") == "each_record" else "Last median"
                    lines.append(basis + " minus user target: " + fmt(fact["target_gap"]))
            elif fact["type"] == "traffic_sources":
                per_post = fact["window_basis"][0] == "post_age"
                if per_post:
                    lines.append(window_label(fact["groups"][0]["window"]))
                lines += [f"{fact['source']} · {fact['metric']} · {fact['population']}", "",
                          ("| Group | Posts | Mean total per post | Mean source per post | Mean remaining sources per post | Weighted share |" if per_post else
                           "| Group | n | Total | Source count | Remaining sources | Weighted share |"),
                          "|---|---:|---:|---:|---:|---:|"]
                for group in fact["groups"]:
                    total = group["per_post_total_mean"] if per_post else group["total"]
                    count = group["per_post_count_mean"] if per_post else group["count"]
                    remainder = total - count if total is not None and count is not None else None
                    label = group["label"] if per_post else group["label"] + " · " + window_label(group["window"])
                    lines.append(f"| {label} | {group['n']} | {fmt(total)} | {fmt(count)} | {fmt(remainder)} | {percent(group['weighted_share'])} |")
                    if group["weighted_share"] is None and any(item is not None for item in group["reported_shares"]):
                        lines.append("Reported shares (not averaged): " +
                                     ", ".join(percent(item) for item in group["reported_shares"]))
            elif fact["type"] == "period_totals":
                lines += [f"{fact['metric']} · {fact['population']}", "",
                          "| Period | Period total | Posts published |", "|---|---:|---:|"]
                for group in fact["groups"]:
                    label = group["label"] + " · " + window_label(group["window"])
                    lines.append(f"| {label} | {fmt(group['value'])} | {fmt(group['posts_published'])} |")
                lines += ["", "Last period versus first:",
                          "| Measure | Difference (last−first) | Ratio (last÷first) | Change (difference÷first) |",
                          "|---|---:|---:|---:|"]
                for label, change in [("Period total", fact["change"]),
                                      ("Posts published", fact["publishing_change"])]:
                    ratio = change["ratio"] if change else None
                    difference = change["difference"] if change else None
                    lines.append(f"| {label} | {fmt(difference)} | {fmt(ratio)} | {percent(ratio - 1 if ratio is not None else None)} |")
                if any(change is None or change["ratio"] is None
                       for change in (fact["change"], fact["publishing_change"])):
                    lines.append("— means missing data or a zero first-period denominator; it does not mean zero change.")
            else:
                lower, upper = fact["target_share_range"]
                lines.append(f"{fact['dimension']} · {fact['population']} · {', '.join(fact['target_categories'])}: " +
                             (percent(lower) if lower == upper else f"{percent(lower)}–{percent(upper)}"))
                lines.append(measurement_window(fact["window"]))
                status = fact.get("target_status")
                if status:
                    labels = {"met": "meets target", "not_met": "does not meet target",
                              "baseline_only": "baseline only", "unknown": "undetermined"}
                    lines.append("Target status: " + labels[status])
        for code in question["limits"]:
            lines.append("- " + LIMITS[code])
        for code in question["missing"]:
            detail = MISSING.get(code, "the requested metric for missing records")
            lines.append("- Needed: " + detail)
    return "\n".join(lines).rstrip() + "\n"
