#!/usr/bin/env python3
"""Compare account facts against the versioned rule library and emit a MonetizationPlan.

Three properties this module exists to hold, none of which survive being left to a model:

1. Observed facts and assumed numbers never merge. A gate decided `met` or `not_met` was decided
   against something retrieved; anything resting on an assumption comes back `estimated_only`.
2. A reading that can only bound the rule's true metric is used in the one direction it is valid.
   Public Shorts view counts overstate qualified Shorts views, so they can show a channel is short
   of the bar and can never show it has cleared it.
3. A rule that is unverified, expired, not yet in force, or out of region does not decide anything.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
# Sibling imports must not leave a __pycache__ in the skill directory.
sys.dont_write_bytecode = True
from rule_library import DEFAULT_LIBRARY, load_rule_library  # noqa: E402

DECIDING_OUTCOMES = {"met", "not_met"}


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def parse_date(raw: Any) -> date | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def compare(value: float, comparator: str, threshold: float) -> bool:
    if comparator == "gte":
        return value >= threshold
    if comparator == "lte":
        return value <= threshold
    return value == threshold


def decide_with_bound(value: float, comparator: str, threshold: float, bound: str) -> str:
    """Resolve a comparison given what direction the reading can be trusted in.

    An upper-bound reading (V >= true) settles `gte` only when it falls short: if even the
    overstatement is below the bar, the true figure is too. A lower-bound reading settles `gte`
    only when it clears. The unsettled half is `estimated_only`, never a guess in either direction.
    """
    meets = compare(value, comparator, threshold)
    if bound == "exact":
        return "met" if meets else "not_met"
    if comparator == "eq":
        return "estimated_only"
    if comparator == "gte":
        if bound == "upper":
            return "estimated_only" if meets else "not_met"
        return "met" if meets else "estimated_only"
    # comparator == "lte"
    if bound == "upper":
        return "met" if meets else "estimated_only"
    return "estimated_only" if meets else "not_met"


def region_applies(regions: list[str], creator_region: str) -> tuple[bool, str | None]:
    """Returns (may_decide, unassessed_reason)."""
    if "GLOBAL" in regions:
        return True, None
    if "SOURCE_LISTS_COUNTRIES" in regions:
        return True, "region_unchecked"
    if creator_region == "UNKNOWN":
        return False, None
    return creator_region in regions, None


def rule_status(provenance: dict[str, Any], as_of: date, creator_region: str, budget_days: int) -> tuple[str, str | None]:
    if provenance.get("verification_status") != "verified":
        return "unverified", None
    start = parse_date(provenance.get("effective_from"))
    end = parse_date(provenance.get("effective_until"))
    if start and as_of < start:
        return "not_yet_effective", None
    if end and as_of >= end:
        return "expired", None
    applies, unassessed = region_applies(list(provenance.get("regions") or []), creator_region)
    if not applies:
        return "region_not_applicable", None
    verified_at = parse_date(provenance.get("verified_at"))
    # An official page has been observed still stating a superseded threshold, so age of the check
    # is tracked separately from the rule's own effective date.
    if verified_at is None or as_of - verified_at > timedelta(days=budget_days):
        return "stale", unassessed
    return "usable", unassessed


def evaluate_requirement(req: dict[str, Any], metric: dict[str, Any] | None,
                         observed: dict[str, Any] | None, rate: dict[str, Any] | None,
                         as_of: date) -> dict[str, Any]:
    result: dict[str, Any] = {
        "requirement_id": req["requirement_id"],
        "metric_id": req["metric_id"],
        "comparator": req["comparator"],
        "threshold": req["threshold"],
        "unit": req["unit"],
        "window_days": req.get("window_days"),
        "any_of_group": req.get("any_of_group"),
        "outcome": "unavailable_metric",
        "bound": req.get("bound_override") or (metric or {}).get("bound", "exact"),
        "basis": (observed or {}).get("basis"),
        "observed_value": None,
        "gap": None,
        "projection": None,
        "note": req.get("note"),
    }
    if metric is None:
        result["note"] = "This requirement names a metric the rule library does not define."
        return result

    availability = metric["availability"]
    if availability == "not_published":
        result["outcome"] = "not_assessable"
        result["note"] = metric.get("caveat") or "The platform does not publish this bar."
        return result

    if observed is None:
        if availability == "needs_authorization":
            result["outcome"] = "needs_authorization"
            result["note"] = metric.get("caveat") or metric.get("what_would_unblock")
        elif availability == "unavailable":
            result["outcome"] = "unavailable_metric"
            result["note"] = metric.get("caveat")
        else:
            result["outcome"] = "unavailable_metric"
            result["note"] = "Obtainable, but no reading for it was supplied to this run."
        return result

    value = float(observed["value"])
    result["observed_value"] = observed["value"]
    bound = req.get("bound_override") or metric.get("bound", "exact")
    threshold = float(req["threshold"])

    # A rounded published count sitting on its threshold cannot resolve the gate either way.
    if metric.get("rounded") and threshold > 0 and abs(value - threshold) / threshold <= 0.01:
        result["outcome"] = "indeterminate_rounding"
        result["note"] = metric.get("caveat")
        return result

    outcome = decide_with_bound(value, req["comparator"], threshold, bound)
    # A windowed metric whose retrieval could not cover the window has an unknown bias in the other
    # direction, so neither half of the bound argument holds and nothing is settled.
    if outcome in DECIDING_OUTCOMES and observed.get("coverage_complete") is False:
        outcome = "estimated_only"
        result["note"] = (
            "Counted over a retrieval window that did not cover the full period this bar is measured over."
        )
    result["outcome"] = outcome
    if outcome == "estimated_only":
        result["note"] = metric.get("caveat") or result["note"]
    if outcome == "not_met" and req["comparator"] == "gte":
        result["gap"] = round(threshold - value, 6)
        if rate and rate.get("value", 0) > 0:
            days = (threshold - value) / float(rate["value"])
            result["projection"] = {
                "days_to_threshold": round(days, 1),
                "rate_value": rate["value"],
                "rate_unit": rate["unit"],
                "assumption_ref": rate["assumption_id"],
                "projected_date": (as_of + timedelta(days=int(days))).isoformat() if days < 36500 else None,
            }
    return result


def evaluate_path(path: dict[str, Any], metrics: dict[str, Any], observed: dict[str, Any],
                  rates: dict[str, Any], as_of: date, creator_region: str, budget: int) -> dict[str, Any]:
    # A path carries its own provenance because one programme can hold a current bar and a future
    # bar at the same time. Deciding a creator's case against a bar that is not in force yet - or
    # against a draft nobody has confirmed - is the failure this check exists to stop.
    status, _ = rule_status(path["provenance"], as_of, creator_region, budget)
    outcomes = [
        evaluate_requirement(req, metrics.get(req["metric_id"]), observed.get(req["metric_id"]),
                             rates.get(req["metric_id"]), as_of)
        for req in path["requirements"]
    ]

    mandatory = [item for item in outcomes if not item["any_of_group"]]
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in outcomes:
        if item["any_of_group"]:
            groups.setdefault(item["any_of_group"], []).append(item)

    verdict = "met"
    if any(item["outcome"] == "not_met" for item in mandatory):
        verdict = "not_met"
    elif any(item["outcome"] not in DECIDING_OUTCOMES for item in mandatory):
        verdict = "undetermined"

    for members in groups.values():
        if any(item["outcome"] == "met" for item in members):
            continue
        if all(item["outcome"] == "not_met" for item in members):
            verdict = "not_met"
        elif verdict == "met":
            verdict = "undetermined"

    if status != "usable":
        verdict = "undetermined"
    return {
        "path_id": path["path_id"],
        "applies_to": path["applies_to"],
        "rule_status": status,
        "effective_from": path["provenance"].get("effective_from"),
        "outcome": verdict,
        "requirements": outcomes,
    }


def build(rules: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    as_of = parse_date(request.get("as_of")) or date.today()
    platform = request["platform"]
    creator_region = (request.get("region") or "UNKNOWN").upper()
    region_source = request.get("region_source") or "unknown"
    # A country with no stated provenance cannot be trusted to exclude anyone, so it is not used.
    if creator_region != "UNKNOWN" and region_source == "unknown":
        creator_region = "UNKNOWN"
    participation = request.get("participation") or "unknown"
    budget = int(rules["staleness_budget_days"])
    metrics = {metric["metric_id"]: metric for metric in rules["metrics"]}

    observed: dict[str, Any] = {}
    for row in request.get("observed_metrics") or []:
        observed[row["metric_id"]] = row

    assumptions = list(request.get("assumptions") or [])
    ledger: list[dict[str, Any]] = []
    rates: dict[str, Any] = {}
    for item in assumptions:
        ledger.append({
            "assumption_id": item["assumption_id"],
            "statement": item["statement"],
            "value": item.get("value"),
            "unit": item.get("unit"),
            # Carried from the request, never assumed: a rate the caller worked out to model a
            # scenario is not a rate the creator gave, and recording it as one is how an
            # estimate ends up quoted back as the channel's own figure.
            "origin": item["origin"],
            "source_url": item.get("source_url"),
        })
        if item.get("role") == "growth_per_day" and item.get("applies_to_metric"):
            rates[item["applies_to_metric"]] = item

    wanted = request.get("programs")
    selected = [p for p in rules["programs"] if p["platform"] == platform and (not wanted or p["program_id"] in wanted)]

    assessments: list[dict[str, Any]] = []
    gaps: dict[str, dict[str, Any]] = {}
    owner_metrics_used = any(
        row.get("basis") == "owner_authorized_analytics" for row in (request.get("observed_metrics") or [])
    )

    for program in selected:
        status, unassessed_region = rule_status(program["provenance"], as_of, creator_region, budget)
        not_assessed = list(program.get("additional_requirements_not_assessed") or [])
        region_checked = unassessed_region is None

        paths = [
            evaluate_path(path, metrics, observed, rates, as_of, creator_region, budget)
            for path in program["eligibility_paths"]
        ]
        # A path written for someone already in the programme cannot settle a new applicant's case,
        # and a path whose own rule is not in force cannot settle anybody's.
        relevant = [
            p for p in paths
            if p["rule_status"] == "usable"
            and (participation == "unknown" or p["applies_to"] == "any" or p["applies_to"] == participation)
        ]

        decided_by = None
        if status != "usable" or not relevant:
            eligibility = "undetermined"
        elif any(p["outcome"] == "met" for p in relevant):
            eligibility = "eligible"
            decided_by = next(p["path_id"] for p in relevant if p["outcome"] == "met")
        elif all(p["outcome"] == "not_met" for p in relevant):
            eligibility = "not_eligible"
        else:
            eligibility = "partially_determined"

        # Reporting eligible while a known gate went unchecked would be a false positive.
        if eligibility == "eligible" and (not_assessed or not region_checked):
            eligibility = "partially_determined"

        assessments.append({
            "program_id": program["program_id"],
            "platform": program["platform"],
            "family": program["family"],
            "display_name": program["display_name"],
            "what_it_pays_for": program["what_it_pays_for"],
            "rule_status": status,
            "eligibility": eligibility,
            "decided_by_path": decided_by,
            "paths": paths,
            "additional_requirements_not_assessed": not_assessed,
            "region_checked": region_checked,
            "i18n": program.get("i18n"),
            "source": {
                "source_url": program["provenance"]["source_url"],
                "regions": program["provenance"]["regions"],
                "effective_from": program["provenance"]["effective_from"],
                "effective_until": program["provenance"]["effective_until"],
                "verified_at": program["provenance"]["verified_at"],
                "verification_status": program["provenance"]["verification_status"],
            },
        })

        # The gap list is an output, not an error log: it is the evidence for what to build next.
        for path in paths:
            if path["rule_status"] != "usable":
                continue
            for req in path["requirements"]:
                if req["outcome"] not in {"needs_authorization", "unavailable_metric", "estimated_only"}:
                    continue
                metric = metrics.get(req["metric_id"])
                if metric is None or metric["availability"] not in {
                    "available_with_caveat", "partial", "needs_authorization", "unavailable"
                }:
                    continue
                entry = gaps.setdefault(req["metric_id"], {
                    "metric_id": req["metric_id"],
                    "availability": metric["availability"],
                    "blocks": [],
                    "what_would_unblock": metric.get("what_would_unblock") or metric.get("caveat")
                                          or "No path to this metric has been identified.",
                })
                ref = f"{program['program_id']}/{path['path_id']}/{req['requirement_id']}"
                if ref not in entry["blocks"]:
                    entry["blocks"].append(ref)

    facts = []
    for metric_id, row in observed.items():
        facts.append({
            "metric_id": metric_id,
            "observed": {
                "value": row["value"],
                "unit": row["unit"],
                "basis": row["basis"],
                "retrieved_at": row["retrieved_at"],
                "coverage_complete": row.get("coverage_complete"),
                "sample_size": row.get("sample_size"),
            },
            "estimated": None,
            "caveat": row.get("caveat") or (metrics.get(metric_id) or {}).get("caveat"),
        })

    scenarios, ledger, inputs_needed, no_figure = build_scenarios(
        rules, request, assessments, observed, metrics, ledger)

    seed = json.dumps([request.get("account"), platform, as_of.isoformat(), rules["library_version"]], sort_keys=True)
    return {
        "schema_version": "monetization-plan.v1",
        "plan_id": f"mp-{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:16]}",
        "generated_at": as_of.isoformat(),
        "as_of": as_of.isoformat(),
        "rule_library": {
            "library_version": rules["library_version"],
            "compiled_at": rules["compiled_at"],
            "staleness_budget_days": budget,
        },
        "creator": {
            "platform": platform,
            "account": request.get("account"),
            "region": creator_region,
            "region_source": region_source if creator_region != "UNKNOWN" else "unknown",
            "participation": participation,
        },
        "account_facts": facts,
        "programs": assessments,
        "revenue_scenarios": scenarios,
        "revenue_inputs_needed": inputs_needed,
        "revenue_not_available": no_figure,
        "assumption_ledger": ledger,
        "data_gaps": list(gaps.values()),
        "niche_comparison": niche_comparison(rules, request, platform),
        "income_rows": income_rows_for(rules, request, assessments, scenarios, ledger, inputs_needed, no_figure),
        "income_totals": [],
        "income_breakdown_requested": "income_breakdown" in request,
        "claim_boundary": {
            "conclusion_kind": "rule_comparison",
            "guarantees_eligibility": False,
            "guarantees_earnings": False,
            "owner_verified_metrics_used": owner_metrics_used,
        },
    }


def income_rows_for(rules: dict[str, Any], request: dict[str, Any], assessments: list[dict[str, Any]],
                    scenarios: list[dict[str, Any]], ledger: list[dict[str, Any]],
                    inputs_needed: list[dict[str, Any]], no_figure: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Project the plan onto income rows: the platform-payout part of an income breakdown.

    Every priced row is one of the scenarios above, at the same range - a threshold never becomes
    an amount. A programme that cannot be priced still gets a row, carrying the path to a figure
    and what it lacks, so a combined answer cannot drop it.
    """
    programs = {p["program_id"]: p for p in rules["programs"]}
    origin_of = {e["assumption_id"]: e["origin"] for e in ledger}

    def evidence(item: dict[str, Any]) -> str:
        if item["source"] == "observed":
            return "retrieved"  # read from the creator's own account or public profile
        if item["source"] == "benchmark":
            # A rate other creators report is not a published one, and is never shown as one.
            return "creator_reported" if item.get("reported") else "rule_library"
        origin = origin_of.get(item["ref"])
        if origin not in {"user_supplied", "model_estimate"}:
            raise ValueError(f"input {item['name']} came from the creator but its ledger origin is {origin!r}")
        return origin

    def number(value: float) -> str:
        return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}".rstrip("0").rstrip(".")

    def span(item: dict[str, Any]) -> str:
        low, high = item["low"], item["high"]
        figure = number(low) if low == high else f"{number(low)}-{number(high)}"
        return f"{item['name']} {figure} {item['unit']}"

    def name(program_id: str) -> str:
        return (programs.get(program_id) or {}).get("display_name") or program_id

    def rate_unit(program_id: str) -> str:
        model = (programs.get(program_id) or {}).get("revenue_model") or {}
        return (model.get("inputs") or [{}])[-1].get("unit") or "no published rate"

    def period(program_id: str) -> str:
        model = (programs.get(program_id) or {}).get("revenue_model") or {}
        return model.get("period") or "monthly"

    rows: list[dict[str, Any]] = []
    for scenario in scenarios:
        # Every revenue model here is volume x rate, with the rate last.
        rate, volumes = scenario["inputs"][-1], scenario["inputs"][:-1]
        missing = []
        if scenario["conditional_on_eligibility"]:
            missing.append("Not eligible yet: this applies only once the programme's bar is met.")
        if rate.get("reported"):
            missing.append("No rate published by the platform; this uses the rate other creators report.")
        rows.append({
            "row_id": f"R-{scenario['program_id']}",
            "stream_kind": "platform_payout",
            "stream": name(scenario["program_id"]),
            "unit": rate["unit"],
            "volume_basis": {"description": "; ".join(span(v) for v in volumes) or "none",
                             "source": evidence(volumes[0]) if volumes else "unknown"},
            "rate_basis": {"origin": evidence(rate), "description": span(rate)},
            "period": scenario["period"],
            # A channel RPM is already after the platform's share (see the rule's note); the other
            # payout models do not say, so they are left unmodelled rather than guessed.
            "cost_basis": {"platform_cut": "net_of_platform_share" if scenario["model"] == "revenue_share"
                           else "not_modelled", "tax": "pre_tax"},
            "already_counted_in": [],
            "missing": missing,
            "amount": {"low": scenario["low"], "high": scenario["high"], "currency": scenario["currency"]},
            "after_tax": None if not scenario["after_tax"] else {
                "low": scenario["after_tax"]["low"], "high": scenario["after_tax"]["high"],
                "rate_percent": scenario["after_tax"]["rate_percent"]},
            "source_ref": scenario["scenario_id"],
        })

    needed: dict[str, list[str]] = {}
    for item in inputs_needed:
        label = item["label"] + (f" ({item['where_to_find']})" if item.get("where_to_find") else "")
        bucket = needed.setdefault(item["program_id"], [])
        if label not in bucket:
            bucket.append(label)
    unpriced = [(pid, labels) for pid, labels in needed.items()]
    unpriced += [(item["program_id"], [item["reason"]]) for item in no_figure]
    # An unpriced programme still states where the creator stands against its bar, first, so a combined
    # answer cannot read a missing rate as the only thing between the creator and the money.
    standing = {a["program_id"]: a["eligibility"] for a in assessments}
    status = {
        "not_eligible": "Not eligible yet: this applies only once the programme's bar is met.",
        "partially_determined": "Eligibility only partly confirmed: part of the programme's bar could not be checked.",
    }
    for program_id, labels in unpriced:
        first = status.get(standing.get(program_id))
        labels = ([first] if first else []) + [label for label in labels if label != first]
        rows.append({
            "row_id": f"R-{program_id}", "stream_kind": "platform_payout", "stream": name(program_id),
            "unit": rate_unit(program_id),
            "volume_basis": {"description": "not available yet", "source": "unknown"},
            "rate_basis": {"origin": None, "description": "not priced until what is missing is supplied"},
            "period": period(program_id),
            "cost_basis": {"platform_cut": "not_modelled", "tax": "not_modelled"},
            "already_counted_in": [], "missing": labels, "amount": None, "after_tax": None, "source_ref": None,
        })

    # A total that already contains another programme's money: that programme's row sits inside it.
    present = {row["row_id"] for row in rows}
    for scenario in scenarios:
        for inner in scenario["includes_programs"]:
            row = next((r for r in rows if r["row_id"] == f"R-{inner}"), None)
            outer = f"R-{scenario['program_id']}"
            if row is not None and outer in present and outer not in row["already_counted_in"]:
                row["already_counted_in"].append(outer)

    asked = (request.get("income_breakdown") or {}).get("streams_without_owner") or []
    if "digital_products" in asked:
        rows.append({
            "row_id": "R-digital_products", "stream_kind": "digital_product", "stream": "Digital products",
            "unit": "per sale", "volume_basis": {"description": "not known", "source": "unknown"},
            "rate_basis": {"origin": None, "description": "no Skill prices this income yet"},
            "period": "monthly", "cost_basis": {"platform_cut": "not_modelled", "tax": "not_modelled"},
            "already_counted_in": [],
            "missing": ["No Skill prices digital-product income yet; it is listed so the answer does not drop it."],
            "amount": None, "after_tax": None, "source_ref": None,
        })
    return rows


PERIOD_DAYS = {"monthly": 30.0, "annual": 365.0}
PLATFORM_NAMES = {"youtube": "YouTube", "instagram": "Instagram", "tiktok": "TikTok"}


def niche_comparison(rules: dict[str, Any], request: dict[str, Any], platform: str) -> dict[str, Any] | None:
    """Answer the two halves of a which-niche-pays-more question differently.

    The rate half is read from the library, which records that no platform publishes a per-niche
    rate. The competition half is about what is being made and how it performs - research, not a
    payout rule - so it is handed back with the question to ask, never silently dropped.
    """
    question = request.get("niche_question")
    if not question:
        return None
    niches = list(question["niches"])
    library = rules["niche_rates"]
    if question.get("asks_competition"):
        competition = {
            "status": "handed_back",
            "research_skill": f"research-{platform}-trends",
            "question": (f"How crowded is each of these niches on {PLATFORM_NAMES[platform]} right now, and how are "
                         f"recent public posts in them performing: {', '.join(niches)}?"),
        }
    else:
        competition = {"status": "not_asked", "research_skill": None, "question": None}
    # The conclusion only reaches as far as the pages behind it. A platform nobody checked is
    # reported as unchecked - "not published" would be a claim with no page to point at.
    pages = [page for page in library["checked"] if page.get("platform") == platform]
    if pages:
        rate = {"published": library["published"], "note": library["note"],
                "i18n": library.get("i18n"), "checked": pages}
    else:
        rate = {"published": None, "note": library["unchecked_note"],
                "i18n": library.get("unchecked_i18n"), "checked": []}
    return {"niches": niches, "rate": rate, "competition": competition}


def resolve_input(spec: dict[str, Any], observed: dict[str, Any], assumptions: list[dict[str, Any]],
                  benchmarks: dict[str, Any], program_id: str, metrics: dict[str, Any],
                  period: str) -> dict[str, Any]:
    """Find one revenue input, or report precisely what is missing.

    The three sources are not interchangeable and the result says which one answered, because a
    figure built half from retrieved views and half from a number the creator typed is not the same
    claim as one built entirely from either.
    """
    name, unit = spec["name"], spec["unit"]
    supplied = next(
        (a for a in assumptions
         if a.get("applies_to_program") in (None, program_id) and a.get("input_name") == name),
        None,
    )
    if supplied is not None:
        low = float(supplied["value"])
        high = float(supplied["value_high"]) if supplied.get("value_high") is not None else low
        if high < low:
            raise ValueError(f"assumption {supplied['assumption_id']} has value_high below value")
        return {"name": name, "unit": unit, "low": low, "high": high,
                "source": "creator", "ref": supplied["assumption_id"]}

    if spec["from"] == "observed_metric":
        row = observed.get(spec.get("metric_id"))
        if row is not None:
            value = float(row["value"])
            # A 90-day total priced as a monthly one overstates the figure threefold. Scale the
            # metric's own window onto the model's period, and say so in the output.
            window = (metrics.get(spec.get("metric_id")) or {}).get("window_days")
            scaled_from = None
            if window and period in PERIOD_DAYS and window != PERIOD_DAYS[period]:
                factor = PERIOD_DAYS[period] / float(window)
                scaled_from = {"window_days": window, "factor": round(factor, 6)}
                value *= factor
            return {"name": name, "unit": unit, "low": value, "high": value,
                    "source": "observed", "ref": spec.get("metric_id"),
                    "scaled_from": scaled_from,
                    "coverage_complete": row.get("coverage_complete")}

    if spec["from"] == "benchmark":
        bench = benchmarks.get(spec.get("benchmark_id"))
        if bench is not None:
            # Whether the platform published this rate or creators reported it changes how the
            # figure may be worded, so the distinction travels with the input rather than being
            # re-derived from the benchmark id downstream.
            reported = bench["provenance"].get("source_authority") == "creator_reported"
            return {"name": name, "unit": unit, "low": float(bench["low"]), "high": float(bench["high"]),
                    "source": "benchmark", "ref": bench["benchmark_id"], "reported": reported}

    return {"name": name, "unit": unit, "missing": True,
            "label": spec["label"], "where_to_find": spec.get("where_to_find"), "i18n": spec.get("i18n")}


def build_scenarios(rules: dict[str, Any], request: dict[str, Any], assessments: list[dict[str, Any]],
                    observed: dict[str, Any], metrics: dict[str, Any], ledger: list[dict[str, Any]]
                    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]],
                               list[dict[str, Any]]]:
    """Compute a figure per programme where every input resolved; otherwise name what is missing.

    There is still no invented rate anywhere in here. What changed is the failure mode: a programme
    whose rate nobody publishes now returns the question to the creator with the place to look it
    up, instead of returning nothing at all.
    """
    assumptions = list(request.get("assumptions") or [])
    currency = request.get("currency") or "USD"
    benchmarks = {b["benchmark_id"]: b for b in (rules.get("benchmarks") or [])}
    programs = {p["program_id"]: p for p in rules["programs"]}
    payout_models = {
        "ad_share": "revenue_share", "memberships": "fan_payment", "fan_funding": "fan_payment",
        "subscriptions": "fan_payment", "gifts": "fan_payment", "bonuses": "platform_fund",
        "creator_rewards": "platform_fund", "paid_content": "fan_payment", "commerce": "commission",
    }
    scenarios: list[dict[str, Any]] = []
    needed: list[dict[str, Any]] = []
    unavailable: list[dict[str, Any]] = []

    for assessment in assessments:
        program = programs.get(assessment["program_id"]) or {}
        model = program.get("revenue_model")
        if not model:
            if program.get("no_figure_reason"):
                unavailable.append({
                    "program_id": assessment["program_id"],
                    "reason": program["no_figure_reason"],
                    "i18n": program.get("i18n"),
                })
            continue
        resolved = [
            resolve_input(spec, observed, assumptions, benchmarks, assessment["program_id"],
                          metrics, model["period"])
            for spec in model["inputs"]
        ]
        missing = [r for r in resolved if r.get("missing")]
        if missing:
            for item in missing:
                needed.append({
                    "program_id": assessment["program_id"], "input_name": item["name"],
                    "unit": item["unit"], "label": item["label"],
                    "where_to_find": item.get("where_to_find"), "i18n": item.get("i18n"),
                })
            continue

        low = high = 1.0
        divisor = 1000.0 if "1000" in model["formula"] else 1.0
        for item in resolved:
            low *= item["low"]
            high *= item["high"]
        low, high = low / divisor, high / divisor

        # Tax is not a revenue factor, so it never enters the product above. Without a rate the
        # figure stays pre-tax and the report says so, rather than reading as take-home.
        tax = next((a for a in assumptions
                    if a.get("input_name") == "tax_rate"
                    and a.get("applies_to_program") in (None, assessment["program_id"])), None)
        after_tax = None
        if tax is not None:
            rate = float(tax["value"])
            if (tax.get("unit") or "").strip().lower() != "percent":
                raise ValueError(f"assumption {tax['assumption_id']} is a tax rate whose unit is not percent")
            if not 0 <= rate <= 100:
                raise ValueError(f"assumption {tax['assumption_id']} is a tax rate outside 0-100 percent")
            after_tax = {"low": round(low * (1 - rate / 100), 2), "high": round(high * (1 - rate / 100), 2),
                         "rate_percent": rate, "assumption_ref": tax["assumption_id"]}
        note = None
        if model.get("note"):
            note = {"en": model["note"]}
            for tag, block in (model.get("i18n") or {}).items():
                if block.get("note"):
                    note[tag] = block["note"]

        refs = sorted({r["ref"] for r in resolved if r["source"] == "creator"})
        for item in resolved:
            if item["source"] == "benchmark":
                bench = benchmarks[item["ref"]]
                entry_id = f"B-{item['ref']}"
                if not any(e["assumption_id"] == entry_id for e in ledger):
                    ledger.append({
                        "assumption_id": entry_id,
                        "statement": (
                            f"Rate creators report for this programme: {bench['low']}-{bench['high']} {bench['unit']}."
                            if bench["provenance"].get("source_authority") == "creator_reported"
                            else f"Rate published by the platform: {bench['low']} {bench['unit']}."
                        ),
                        "value": bench["low"], "unit": bench["unit"], "origin": "rule_library",
                        "source_url": bench["provenance"]["source_url"],
                    })
                refs.append(entry_id)
        scenarios.append({
            "scenario_id": f"S-{assessment['program_id']}",
            "program_id": assessment["program_id"],
            "model": payout_models.get(assessment["family"], "revenue_share"),
            "period": model["period"], "currency": currency,
            "low": round(low, 2), "high": round(high, 2),
            "formula": model["formula"],
            "inputs": resolved,
            "assumption_refs": sorted(set(refs)),
            "conditional_on_eligibility": assessment["eligibility"] != "eligible",
            "after_tax": after_tax,
            "note": note,
            # A total that already contains another programme's income is not a component to be
            # summed with it: the membership money inside a channel RPM would be counted twice.
            "includes_programs": list(model.get("includes_programs") or []),
        })

    return scenarios, ledger, needed, unavailable


def merge_observed(request: dict[str, Any], derived: dict[str, Any]) -> dict[str, Any]:
    """Put the retrieved account facts into the request in place of any retyped public reading.

    A public count read off a rendering and retyped is the transcription derive_monetization_metrics.py
    replaces, so a derived row replaces a request row for the same metric only when that row is a
    public reading too. A figure the creator gave (user_supplied) or owner analytics is a better
    source than the public count - an exact 1,004 beats a rounded public "1K", qualified Shorts views
    beat public plays - so for those metrics the request's row stays and the derived one is dropped.
    """
    if derived.get("schema_version") != "observed-metrics.v1":
        raise ValueError("--observed-metrics must be the output of derive_monetization_metrics.py")
    if derived.get("platform") != request.get("platform"):
        raise ValueError(f"--observed-metrics was derived for {derived.get('platform')!r}, "
                         f"but the request is for {request.get('platform')!r}")
    given = [row for row in request.get("observed_metrics") or [] if isinstance(row, dict)]
    better = {row.get("metric_id") for row in given if row.get("basis") not in (None, "public_account_facts")}
    rows = [row for row in derived.get("observed_metrics") or []
            if isinstance(row, dict) and row.get("metric_id") not in better]
    derived_ids = {row.get("metric_id") for row in rows}
    kept = [row for row in given if row.get("metric_id") not in derived_ids]
    merged = {**request, "observed_metrics": kept + rows}
    # The channel's declared country fills a region nobody gave, and replaces one copied from the same
    # declaration; a region the creator stated stays. The evaluation date is the request's when it has one.
    region = derived.get("region") or {}
    stated = str(request.get("region") or "").upper()
    if region.get("region") and (stated in ("", "UNKNOWN") or request.get("region_source") == "account_declared"):
        merged["region"], merged["region_source"] = region["region"], region.get("region_source") or "account_declared"
    if not request.get("as_of") and derived.get("as_of"):
        merged["as_of"] = derived["as_of"]
    return merged


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("request", type=Path)
    parser.add_argument("--rules", type=Path, default=DEFAULT_LIBRARY,
                        help="rule library directory or file (default: the bundled library)")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--observed-metrics", type=Path,
                        help="derive_monetization_metrics.py output; its rows replace any row the request "
                             "carries for the same metric")
    args = parser.parse_args()
    try:
        request = load(args.request)
        if args.observed_metrics:
            request = merge_observed(request, load(args.observed_metrics))
        plan = build(load_rule_library(args.rules), request)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"status": "ok", "path": str(args.output), "programs": len(plan["programs"]),
                          "data_gaps": len(plan["data_gaps"])}, ensure_ascii=False))
        return 0
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "failed", "error": f"{type(error).__name__}: {error}"}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
