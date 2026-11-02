#!/usr/bin/env python3
"""Question-scoped traffic calculations. No platform-enforcement classifier."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import statistics
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

KINDS = {"recent_drop", "growth", "traffic_sources", "audience"}
MODULES = {"recent_drop": "performance", "growth": "performance",
           "traffic_sources": "traffic_sources", "audience": "audience"}
ACTION_NUMBER_BASIS = frozenset({
    "derived_from_record", "user_stated", "platform_rule_cited", "provisional_default",
})


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def fields(value: Any, required: set, optional: set = frozenset()) -> None:
    require(isinstance(value, dict), "expected an object")
    require(required <= value.keys(), f"missing fields: {sorted(required - value.keys())}")
    require(value.keys() <= required | optional,
            f"unknown fields: {sorted(value.keys() - required - optional)}")


def text(value: Any) -> str:
    require(isinstance(value, str) and bool(value.strip()), "expected non-empty text")
    return value


def number(value: Any) -> float:
    require(not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value) and value >= 0, "expected a finite non-negative number")
    return float(value)


def action_number_basis(value: Any) -> str:
    require(value in ACTION_NUMBER_BASIS, "unknown action number basis")
    return value


def time(value: str) -> datetime:
    parsed = datetime.fromisoformat(text(value).replace("Z", "+00:00"))
    require(parsed.tzinfo is not None,
            f"timestamp {value!r} needs a timezone: use a bare date (YYYY-MM-DD) or add Z")
    return parsed.astimezone(timezone.utc)


def calendar_time(value: str) -> datetime:
    # A calendar date is not an invented timestamp/timezone.
    if len(text(value)) == 10:
        return datetime.combine(date.fromisoformat(value), datetime.min.time())
    return time(value)


def post_age(row: dict) -> float | None:
    measured = None
    if row.get("published_at") is not None and row.get("observed_at") is not None:
        measured = (time(row["observed_at"]) - time(row["published_at"])).total_seconds() / 3600
        require(measured >= 0, "observation precedes publication")
    supplied = row.get("post_age_hours")
    if supplied is not None:
        number(supplied)
        require(measured is None or abs(supplied - measured) < 1e-9,
                "supplied post age conflicts with timestamps")
    return measured if supplied is None else supplied


def publication_day(row: dict) -> date | None:
    if row.get("published_on") is not None:
        return date.fromisoformat(row["published_on"])
    return time(row["published_at"]).date() if row.get("published_at") is not None else None


def digest(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def delta(before: float, after: float) -> dict:
    return {"before": before, "after": after, "difference": after - before,
            "ratio": after / before if before else None}


def window(value: dict) -> tuple:
    require(isinstance(value, dict), "window must be an object")
    if value.get("kind") == "post_age":
        fields(value, {"kind", "hours"})
        return ("post_age", number(value["hours"]))
    fields(value, {"kind", "start", "end"})
    require(value["kind"] == "calendar", "unknown window kind")
    start, end = calendar_time(value["start"]), calendar_time(value["end"])
    require((start.tzinfo is None) == (end.tzinfo is None), "calendar date precision must match")
    require(start < end, "calendar window must have positive duration")
    return ("calendar", (end - start).total_seconds() / 3600,
            "date" if start.tzinfo is None else "timestamp")


def validate_case(case: dict) -> None:
    fields(case, {"schema_version", "platform", "account", "questions", "sources"},
           {"performance", "period_totals", "traffic_sources", "audience", "coverage"})
    require(case["schema_version"] == "traffic-case.v1", "expected traffic-case.v1")
    require(case["platform"] in {"youtube", "tiktok", "instagram"}, "unsupported platform")
    text(case["account"])
    require(isinstance(case["sources"], list), "sources must be an array")
    source_ids = set()
    for source in case["sources"]:
        fields(source, {"id", "kind", "locator"})
        require(source["kind"] in {"public", "owner_export", "user_supplied", "synthetic"},
                "invalid source kind")
        text(source["locator"])
        require(text(source["id"]) not in source_ids, "duplicate source id")
        source_ids.add(source["id"])
    if "coverage" in case:
        require(isinstance(case["coverage"], list)
                and all(isinstance(x, str) for x in case["coverage"]), "coverage must be text array")
    for module in {"performance", "period_totals", "traffic_sources", "audience"}:
        records = case.get(module, [])
        require(isinstance(records, list), f"{module} must be an array")
        ids = set()
        for row in records:
            common = {"id", "source_ref"}
            if module == "performance":
                fields(row, common | {"content_id", "format", "metrics"},
                       {"url", "date_label", "published_at", "observed_at", "published_on", "observed_on", "post_age_hours"})
                if "date_label" in row:
                    text(row["date_label"])
                for stem in ("published", "observed"):
                    require(not (stem + "_at" in row and stem + "_on" in row),
                            "use a timestamp or date, not both")
                    if row.get(stem + "_at") is not None:
                        time(row[stem + "_at"])
                    if row.get(stem + "_on") is not None:
                        date.fromisoformat(text(row[stem + "_on"]))
                if row.get("published_on") and row.get("observed_on"):
                    require(row["observed_on"] >= row["published_on"], "observation precedes publication")
                post_age(row)
                text(row["content_id"])
                text(row["format"])
                require(isinstance(row["metrics"], dict), "metrics must be an object")
                for key, value in row["metrics"].items():
                    text(key)
                    if value is not None:
                        number(value)
            elif module == "period_totals":
                fields(row, common | {"metric", "population", "window", "value", "posts_published"})
                require(window(row["window"])[0] == "calendar", "period totals require calendar windows")
                text(row["metric"])
                text(row["population"])
                number(row["value"])
                if row["posts_published"] is not None:
                    count = number(row["posts_published"])
                    require(count.is_integer(), "posts_published must be an integer")
            elif module == "traffic_sources":
                fields(row, common | {"metric", "population", "source", "window"},
                       {"content_id", "total", "count", "share", "share_tolerance"})
                window(row["window"])
                if row["window"]["kind"] == "post_age":
                    text(row.get("content_id"))
                for key in ("total", "count", "share", "share_tolerance"):
                    if row.get(key) is not None:
                        number(row[key])
                require(row.get("share") is None or row["share"] <= 1, "share must be a fraction")
                tolerance = row.get("share_tolerance", 0)
                require(tolerance is not None and tolerance <= 1, "share_tolerance must be a fraction")
                if row.get("count") is not None and row.get("total") is not None:
                    require(row["count"] <= row["total"], "source count exceeds total")
                    if row.get("share") is not None:
                        implied = row["count"] / row["total"] if row["total"] else 0
                        require(abs(implied - row["share"]) <= tolerance + 1e-12,
                                "source count conflicts with reported share")
                for key in ("metric", "population", "source"):
                    text(row[key])
            else:
                fields(row, common | {"dimension", "population", "window", "shares"})
                if row["window"] is not None:
                    window(row["window"])
                text(row["dimension"])
                text(row["population"])
                require(isinstance(row["shares"], dict) and bool(row["shares"]), "shares must be non-empty")
                for category, value in row["shares"].items():
                    text(category)
                    require(number(value) <= 1, "audience share must be a fraction")
                require(sum(row["shares"].values()) <= 1 + 1e-12, "audience shares exceed one")
            require(row["source_ref"] in source_ids, "unknown source reference")
            require(text(row["id"]) not in ids, f"duplicate {module} record id")
            ids.add(row["id"])
    # Reused evidence must not acquire a different value in another module.
    for source in case.get("traffic_sources", []):
        if source["window"]["kind"] != "post_age" or source.get("total") is None:
            continue
        for row in case.get("performance", []):
            measured = row["metrics"].get(source["metric"])
            if (row["content_id"] == source["content_id"] and
                    post_age(row) == source["window"]["hours"] and measured is not None):
                require(measured == source["total"], "source total conflicts with the same performance observation")
    questions = case["questions"]
    require(isinstance(questions, list) and bool(questions), "questions must be non-empty")
    ids = set()
    for q in questions:
        require(isinstance(q, dict), "question must be an object")
        optional = {"intent"}
        if q.get("kind") in {"recent_drop", "growth"}:
            optional.add("metric")
            if q.get("growth_basis") != "period_total":
                optional.update({"minimum_age_hours", "age_policy_reason", "target", "target_basis"})
        if q.get("kind") == "growth":
            optional.add("growth_basis")
        if q.get("kind") == "audience":
            optional.add("target")
        fields(q, {"id", "kind", "ask", "groups", "selection_reason"},
               optional)
        require(text(q["id"]) not in ids, "duplicate question id")
        ids.add(q["id"])
        require(q["kind"] in KINDS, "unknown question kind; clarify instead of forcing a branch")
        text(q["ask"])
        text(q["selection_reason"])
        require(q.get("intent", "diagnose") in {"explain", "diagnose", "act"}, "invalid delivery intent")
        require(isinstance(q["groups"], list), "groups must be an array")
        used = set()
        labels = set()
        for group in q["groups"]:
            fields(group, {"label", "record_ids"})
            require(text(group["label"]) not in labels, "duplicate group label")
            labels.add(group["label"])
            refs = group["record_ids"]
            require(isinstance(refs, list) and all(isinstance(r, str) for r in refs), "invalid record_ids")
            require(len(set(refs)) == len(refs) and not used.intersection(refs), "overlapping groups")
            used.update(refs)
        if "minimum_age_hours" in q:
            number(q["minimum_age_hours"])
            text(q.get("age_policy_reason"))
        require("age_policy_reason" not in q or "minimum_age_hours" in q, "age policy requires hours")
        if q["kind"] in {"recent_drop", "growth"}:
            text(q.get("metric"))
        if "growth_basis" in q:
            require(q["kind"] == "growth" and q["growth_basis"] in {"per_post", "period_total"}, "invalid growth basis")
        if "target" in q:
            if q["kind"] == "audience":
                fields(q["target"], {"dimension", "population", "categories", "criterion", "definition"})
                text(q["target"]["dimension"])
                text(q["target"]["population"])
                cats = q["target"]["categories"]
                require(isinstance(cats, list) and bool(cats)
                        and all(isinstance(c, str) and c for c in cats)
                        and len(cats) == len(set(cats)), "invalid target categories")
                criterion = q["target"]["criterion"]
                require(isinstance(criterion, dict), "audience target criterion must be an object")
                kind = criterion.get("kind")
                if kind == "minimum_share":
                    fields(criterion, {"kind", "value"})
                    require(0 <= number(criterion["value"]) <= 1, "minimum target share must be a fraction")
                else:
                    fields(criterion, {"kind"})
                    require(kind in {"largest_share", "increase"}, "unknown audience target criterion")
                definition = q["target"]["definition"]
                require(isinstance(definition, dict), "audience target definition must be an object")
                status = definition.get("status")
                if status == "user_explicit":
                    fields(definition, {"status", "source_text"})
                    text(definition["source_text"])
                else:
                    fields(definition, {"status", "confirmation"})
                    require(status == "provisional", "unknown audience target definition status")
                    text(definition["confirmation"])
            else:
                number(q["target"])
                require(q.get("target_basis") in {"each_record", "group_median"},
                        "performance target requires each_record or group_median target_basis")
        else:
            require("target_basis" not in q, "target_basis requires a performance target")


def from_analysis(case: dict, analysis: dict) -> dict:
    """Reuse normalized public rows, not the shared Analyzer's mixed-age baselines."""
    require(analysis.get("schema_version") == "account-analysis.v3",
            "the --analysis file is not an AccountAnalysis (expected account-analysis.v3); "
            "pass the account_analysis.json file")
    actual = analysis.get("scope", {}).get("account")
    require(analysis.get("platform") == case["platform"] and
            account_identity(actual) == account_identity(case["account"]),
            f"plan account {account_identity(case['account'])!r} on {case['platform']} does not match the "
            f"AccountAnalysis account {actual!r} on {analysis.get('platform')}; set the plan's account and "
            "platform to the analysis's")
    require(not case.get("performance"), "do not merge supplied and imported performance silently")
    value = json.loads(json.dumps(case, allow_nan=False))
    source_id = "account-analysis:" + text(analysis.get("analysis_id"))
    value["sources"].append({"id": source_id, "kind": "public", "locator": source_id})
    observed = analysis["scope"]["observed_at"]
    value["performance"] = [
        {"id": str(row["content_id"]), "content_id": str(row["content_id"]),
         "format": row["format"], "published_at": row["published_at"], "observed_at": observed,
         "metrics": row["metrics"], "source_ref": source_id}
        for row in analysis["rankings"]
    ]
    # Keep full upstream coverage in the input binding; do not silently claim completeness.
    value["coverage"] = [*value.get("coverage", []), "imported_public_snapshot_not_fixed_age_history"]
    return value


def account_identity(value: object) -> str:
    """Compare equivalent public account labels without weakening source binding."""
    label = text(value).strip().lower().rstrip("/")
    match = re.search(r"(?:^|/)@([^/?#]+)", label)
    if match:
        return match.group(1)
    return label.lstrip("@")


def selected(q: dict, records: list[dict]) -> list[tuple[str, list[dict]]]:
    indexed = {r["id"]: r for r in records}
    result = []
    for group in q["groups"]:
        unknown = set(group["record_ids"]) - indexed.keys()
        require(not unknown, f"unknown selected records: {sorted(unknown)}")
        result.append((group["label"], [indexed[r] for r in group["record_ids"]]))
    return result


def performance(q: dict, groups: list) -> tuple[list, list, list]:
    facts, limits, missing = [], [], []
    metric = q["metric"]
    if len(groups) < 2 or any(not rows for _, rows in groups):
        return facts, limits, ["earlier_and_recent_performance_records"]
    if q["kind"] == "recent_drop":
        require(len(groups) == 2, "recent_drop requires earlier and recent groups")
    all_ids = [r["content_id"] for _, rows in groups for r in rows]
    require(len(all_ids) == len(set(all_ids)), "select one observation per content; cohorts must be disjoint")
    order_verified = True
    for (_, before), (_, after) in zip(groups, groups[1:]):
        if all(r.get("published_at") for r in before + after):
            require(max(time(r["published_at"]) for r in before) <
                    min(time(r["published_at"]) for r in after), "performance groups must be chronologically disjoint")
        else:
            days_before, days_after = [publication_day(r) for r in before], [publication_day(r) for r in after]
            if None in days_before + days_after:
                order_verified = False
            else:
                require(max(days_before) <= min(days_after), "performance groups must be chronologically disjoint")
                order_verified = order_verified and max(days_before) < min(days_after)
    formats = sorted({r["format"] for _, rows in groups for r in rows})
    for fmt in formats:
        summaries, ages, refs, target_groups = [], [], [], []
        local_limits = [] if order_verified else ["chronology_uses_declared_groups"]
        for label, rows in groups:
            cohort = [r for r in rows if r["format"] == fmt]
            values, cohort_ages, accepted = [], [], []
            for row in cohort:
                age = post_age(row)
                if age is None:
                    local_limits.append("unknown_observation_ages")
                    if "minimum_age_hours" in q:
                        local_limits.append("excluded_unknown_age_under_explicit_policy")
                        continue
                if age is not None and age < q.get("minimum_age_hours", 0):
                    local_limits.append("excluded_by_explicit_age_policy")
                    continue
                value = row["metrics"].get(metric)
                if value is None:
                    local_limits.append("missing_requested_metric")
                    continue
                values.append(number(value))
                if age is not None:
                    cohort_ages.append(age)
                accepted.append(row["id"])
            ages.extend(cohort_ages)
            refs.extend(accepted)
            dated = [time(r["published_at"]).isoformat() for r in cohort if r["id"] in accepted and r.get("published_at")]
            publication_range = [min(dated), max(dated)] if dated and len(dated) == len(accepted) else None
            summaries.append({"label": label, "n": len(values), "selected_n": len(cohort),
                              "median": statistics.median(values) if values else None,
                              "max": max(values) if values else None,
                              "sample_sum": sum(values) if values else None,
                              "age_hours": {"min": min(cohort_ages), "max": max(cohort_ages)} if cohort_ages else None,
                              "publication_range": publication_range,
                              "record_ids": accepted})
            if "target" in q:
                target = q["target"]
                met_n = sum(value >= target for value in values)
                decision_value = ((min(values) if values else None) if q["target_basis"] == "each_record"
                                  else (statistics.median(values) if values else None))
                target_groups.append({"label": label, "n": len(values), "met_n": met_n,
                                      "met_share": met_n / len(values) if values else None,
                                      "decision_value": decision_value,
                                      "gap": None if decision_value is None else decision_value - target,
                                      "met": bool(values) and (met_n == len(values) if q["target_basis"] == "each_record"
                                                               else decision_value >= target)})
        if any(s["n"] == 0 for s in summaries):
            local_limits.append("missing_same_format_comparator")
        if any(s["n"] == 1 for s in summaries):
            local_limits.append("single_observation_not_repeatability_evidence")
        # Equality is a measurement condition, not a claim that every post matures at this age.
        same_age = bool(ages) and max(ages) - min(ages) < 1e-9
        if ages and not same_age:
            local_limits.append("different_observation_ages")
        if fmt in {"unknown", "unknown_video"}:
            local_limits.append("format_unverified")
        change = None
        if all(s["median"] is not None for s in summaries):
            change = delta(summaries[0]["median"], summaries[-1]["median"])
        target_evaluation = ({"value": q["target"], "basis": q["target_basis"],
                              "groups": target_groups} if "target" in q else None)
        facts.append({"type": "performance", "format": fmt, "metric": metric,
                      "groups": summaries, "change": change,
                      "comparison": "limited" if local_limits else "same_age_descriptive",
                      "limits": sorted(set(local_limits)), "record_ids": refs,
                      "target_gap": None if target_evaluation is None else target_groups[-1]["gap"]})
        if "target" in q:
            facts[-1]["target"] = q["target"]
            facts[-1]["target_basis"] = q["target_basis"]
            facts[-1]["target_evaluation"] = target_evaluation
        limits.extend(local_limits)
    if q["kind"] == "growth":
        limits.extend(["observed_periods_only_not_long_term_proof", "sample_sum_is_not_calendar_traffic"])
    if "missing_requested_metric" in limits:
        missing.append(f"{metric}_for_selected_missing_records")
    if "missing_same_format_comparator" in limits:
        missing.append("earlier_and_recent_performance_records")
    if "different_observation_ages" in limits or "unknown_observation_ages" in limits:
        missing.append("same_post_age_measurements_for_stronger_comparison")
    return facts, limits, missing


def sources(q: dict, groups: list) -> tuple[list, list, list]:
    facts, limits, missing = [], [], []
    if len(groups) != 2 or any(not rows for _, rows in groups):
        return facts, limits, ["source_observations_not_selected"]
    flat = [r for _, rows in groups for r in rows]
    keys = {(r["metric"], r["population"], r["source"], window(r["window"])) for r in flat}
    if len(keys) != 1:
        return facts, ["incompatible_metric_population_source_or_window"], ["matching_measurement_basis"]
    key = next(iter(keys))
    if key[3][0] == "post_age":
        content_ids = [r["content_id"] for r in flat]
        require(len(content_ids) == len(set(content_ids)), "same post-age observation reused across groups")
    summaries = []
    for label, rows in groups:
        if len(rows) > 1:
            require(key[0] in {"views", "plays", "impressions", "reach", "unique_viewers"},
                    "source metric aggregation is undefined; use the supplied count metric "
                    "(views, plays, impressions) or deduplicated audience metric (reach, unique_viewers)")
        if key[0] in {"reach", "unique_viewers"} and len(rows) > 1:
            return facts, ["nonadditive_metric_cannot_sum_across_content"], ["deduplicated_period_totals"]
        seen = set()
        for row in rows:
            identity = row.get("content_id", "account-total")
            require(identity not in seen, "duplicate source measurements within group")
            seen.add(identity)
        if len(rows) > 1:
            require(all(r.get("content_id") for r in rows), "cannot combine account totals with content rows")
        if key[3][0] == "calendar":
            intervals = {(r["window"]["start"], r["window"]["end"]) for r in rows}
            if len(intervals) != 1:
                return facts, ["different_calendar_windows_within_group"], ["aligned_calendar_window"]
        counts, totals, estimated = [], [], False
        for row in rows:
            total, count, share = row.get("total"), row.get("count"), row.get("share")
            if count is None and total is not None and share is not None:
                count = total * share
                estimated = True
            counts.append(count)
            totals.append(total)
        total = sum(totals) if all(x is not None for x in totals) else None
        count = sum(counts) if all(x is not None for x in counts) else None
        per_post = key[3][0] == "post_age"
        summaries.append({"label": label, "n": len(rows), "total": total, "count": count,
                          "per_post_total_mean": total / len(rows) if per_post and total is not None else None,
                          "per_post_count_mean": count / len(rows) if per_post and count is not None else None,
                          "per_post_count_median": statistics.median(counts) if per_post and count is not None else None,
                          "weighted_share": count / total if count is not None and total else None,
                          "reported_shares": [r.get("share") for r in rows],
                          "estimated_from_share": estimated, "window": dict(rows[0]["window"]),
                          "record_ids": [r["id"] for r in rows]})
        if total is None:
            missing.append("matching_source_totals")
        if count is None:
            missing.append("matching_source_counts_or_shares")
        if estimated:
            limits.append("absolute_counts_estimated_from_reported_shares")
        if not total:
            limits.append("share_undefined_when_total_missing_or_zero")
    if key[3][0] == "calendar":
        before, after = groups[0][1][0]["window"], groups[1][1][0]["window"]
        if calendar_time(before["end"]) > calendar_time(after["start"]):
            return facts, ["overlapping_or_reversed_calendar_windows"], ["disjoint_ordered_windows"]
    if key[3][0] == "post_age" and summaries[0]["n"] != summaries[1]["n"]:
        limits.append("unequal_post_counts_sum_not_per_post_change")
    def change(field: str) -> dict | None:
        a, b = [s[field] for s in summaries]
        return delta(a, b) if a is not None and b is not None else None
    facts.append({"type": "traffic_sources", "metric": key[0], "population": key[1],
                  "source": key[2], "window_basis": list(key[3]), "groups": summaries,
                  "share_change": change("weighted_share"), "count_change": change("count"),
                  "total_change": change("total"),
                  "per_post_count_change": change("per_post_count_mean"),
                  "per_post_total_change": change("per_post_total_mean"),
                  "record_ids": [r["id"] for r in flat]})
    return facts, limits, missing


def period_totals(q: dict, groups: list) -> tuple[list, list, list]:
    if len(groups) < 2 or any(len(rows) != 1 for _, rows in groups):
        return [], [], ["one_actual_calendar_total_per_comparison_period"]
    rows = [r[0] for _, r in groups]
    bases = {(r["metric"], r["population"], window(r["window"])) for r in rows}
    if len(bases) != 1 or rows[0]["metric"] != q["metric"]:
        return [], ["incompatible_metric_population_source_or_window"], ["matching_measurement_basis"]
    for a, b in zip(rows, rows[1:]):
        if calendar_time(a["window"]["end"]) > calendar_time(b["window"]["start"]):
            return [], ["overlapping_or_reversed_calendar_windows"], ["disjoint_ordered_windows"]
    return [{"type": "period_totals", "metric": q["metric"], "population": rows[0]["population"],
             "groups": [{"label": label, "value": r["value"], "posts_published": r["posts_published"],
                         "window": r["window"]} for (label, _), r in zip(groups, rows)],
             "change": delta(rows[0]["value"], rows[-1]["value"]),
             "publishing_change": (delta(rows[0]["posts_published"], rows[-1]["posts_published"])
                                   if all(r["posts_published"] is not None for r in (rows[0], rows[-1])) else None),
             "record_ids": [r["id"] for r in rows]}], ["period_total_not_per_post_improvement"], []


def audience(q: dict, groups: list) -> tuple[list, list, list]:
    facts, limits, missing = [], [], []
    target = q.get("target")
    if not target:
        return facts, limits, ["target_audience_dimension_population_and_categories"]
    rows = [r for _, cohort in groups for r in cohort]
    if not rows:
        return facts, limits, ["actual_audience_distribution_for_requested_window"]
    for row in rows:
        if row["window"] is None:
            limits.append("unknown_audience_window")
            missing.append("audience_measurement_window")
        if row["dimension"] != target["dimension"] or row["population"] != target["population"]:
            limits.append("audience_dimension_or_population_mismatch")
            continue
        known = sum(row["shares"].values())
        lower = sum(row["shares"].get(c, 0) for c in target["categories"])
        all_target_known = all(c in row["shares"] for c in target["categories"])
        upper = lower if all_target_known else min(1, lower + max(0, 1 - known))
        if upper != lower:
            limits.append("target_categories_missing_in_partial_distribution")
        criterion = target["criterion"]
        status = "baseline_only"
        if criterion["kind"] == "minimum_share":
            status = ("met" if lower >= criterion["value"] else
                      "not_met" if upper < criterion["value"] else "unknown")
        elif criterion["kind"] == "largest_share":
            other = [value for category, value in row["shares"].items() if category not in target["categories"]]
            known_other = max(other, default=0)
            possible_other = max([known_other, max(0, 1 - known)])
            status = "met" if lower > possible_other else "not_met" if upper <= known_other else "unknown"
        facts.append({"type": "audience", "dimension": row["dimension"], "population": row["population"],
                      "window": row["window"], "target_categories": target["categories"],
                      "target_share_range": [lower, upper], "reported_distribution_share": known,
                      "target_criterion": criterion, "target_status": status,
                      "record_ids": [row["id"]]})
    if not facts:
        missing.append("actual_audience_on_target_dimension_and_population")
    limits.append("demographics_do_not_establish_interests_or_algorithm_category")
    return facts, limits, missing


def build(case: dict, analysis: dict | None = None) -> dict:
    original = case
    if analysis is not None:
        case = from_analysis(case, analysis)
    validate_case(case)
    findings = []
    for q in case["questions"]:
        is_total = q["kind"] == "growth" and q.get("growth_basis") == "period_total"
        module = "period_totals" if is_total else MODULES[q["kind"]]
        groups = selected(q, case.get(module, []))
        handler = period_totals if is_total else performance if q["kind"] in {"recent_drop", "growth"} else sources if q["kind"] == "traffic_sources" else audience
        facts, limits, missing = handler(q, groups)
        findings.append({"id": q["id"], "kind": q["kind"], "ask": q["ask"],
                         "intent": q.get("intent", "diagnose"),
                         "status": "needs_data" if not facts else "partial" if missing else "computed",
                         "facts": facts, "limits": sorted(set(limits)), "missing": sorted(set(missing))})
        if q["kind"] == "audience" and q.get("target"):
            # Keep the requested measurement even when no actual record matches.
            # Otherwise downstream reasoning must recover it from free-text ask.
            findings[-1]["audience_target"] = {
                "dimension": q["target"]["dimension"], "population": q["target"]["population"],
                "categories": list(q["target"]["categories"]),
                "criterion": json.loads(json.dumps(q["target"]["criterion"], allow_nan=False)),
                "definition": json.loads(json.dumps(q["target"]["definition"], allow_nan=False))}
            statuses = {fact["target_status"] for fact in facts if fact["type"] == "audience"}
            findings[-1]["audience_verdict"] = (next(iter(statuses)) if len(statuses) == 1 else
                                                     "unmeasured" if not statuses else "mixed")
    for finding in findings:
        if finding["kind"] == "traffic_sources" and finding["missing"]:
            available = [{"question_id": other["id"], "metric": fact["metric"],
                          "basis": fact["type"], "record_ids": list(fact["record_ids"])}
                         for other in findings if other["id"] != finding["id"]
                         for fact in other["facts"]
                         if fact["type"] in {"performance", "period_totals"} and fact["record_ids"]]
            if available:
                # Availability is not proof of matching source/population/window.
                # References reuse the computed evidence, without another copy of its values.
                finding["available_measurements"] = available
    result = {"schema_version": "reach-drop-assessment.v3", "platform": case["platform"],
            "account": case["account"], "input_digest": digest({"case": original, "analysis": analysis}),
            "sources": case["sources"], "coverage": case.get("coverage", []), "questions": findings,
            "claim_boundary": {"platform_enforcement_inferred": False, "causes_established": False}}
    if analysis is not None:
        result["imported_snapshot"] = {"observed_at": analysis["scope"]["observed_at"],
                                       "record_count": len(analysis["rankings"])}
        if "coverage" in analysis:
            require(isinstance(analysis["coverage"], dict), "analysis coverage must be an object")
            result["imported_snapshot"]["coverage"] = json.loads(json.dumps(analysis["coverage"], allow_nan=False))
    return result


def load(path: Path) -> dict:
    result = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(result, dict), "input must be a JSON object")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", type=Path)
    parser.add_argument("--analysis", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = build(load(args.case), load(args.analysis) if args.analysis else None)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        print(json.dumps({"status": "ok", "questions": [{"id": q["id"], "status": q["status"]} for q in result["questions"]]}))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({"status": "failed", "error": str(error)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
