#!/usr/bin/env python3
"""Dependency-free semantic validator for MonetizationPlan.

Shape is the easy half. The checks that matter here are the ones that would let a plausible-looking
plan overstate what it knows: a gate reported decided without a retrieved value behind it, an
eligible verdict on a rule that was never allowed to decide, a money range with no assumption
under it, or a verdict declared while a known requirement went unchecked.
"""

from __future__ import annotations

import argparse
import json
from datetime import date
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
# Sibling imports must not leave a __pycache__ in the skill directory.
sys.dont_write_bytecode = True
import income_rows  # noqa: E402

REQUIRED = {
    "schema_version", "plan_id", "generated_at", "as_of", "rule_library", "creator",
    "account_facts", "programs", "revenue_scenarios", "assumption_ledger", "data_gaps", "claim_boundary", "revenue_inputs_needed",
    "revenue_not_available", "niche_comparison", "income_rows", "income_totals", "income_breakdown_requested",
}
LIBRARY_FIELDS = {"library_version", "compiled_at", "staleness_budget_days"}
CREATOR_FIELDS = {"platform", "account", "region", "region_source", "participation"}
BOUNDARY_FIELDS = {"conclusion_kind", "guarantees_eligibility", "guarantees_earnings", "owner_verified_metrics_used"}
PROGRAM_FIELDS = {"program_id", "platform", "family", "display_name", "what_it_pays_for", "rule_status",
                  "eligibility", "decided_by_path", "paths", "source", "additional_requirements_not_assessed", "i18n", "region_checked"}
PATH_FIELDS = {"path_id", "applies_to", "rule_status", "effective_from", "outcome", "requirements"}
REQ_FIELDS = {"requirement_id", "metric_id", "comparator", "threshold", "unit", "window_days",
              "any_of_group", "outcome", "bound", "basis", "observed_value", "gap", "projection", "note"}
SOURCE_FIELDS = {"source_url", "regions", "effective_from", "effective_until", "verified_at", "verification_status"}
GAP_FIELDS = {"metric_id", "availability", "blocks", "what_would_unblock"}
SCENARIO_FIELDS = {"scenario_id", "program_id", "model", "period", "currency", "low", "high",
                   "formula", "inputs", "assumption_refs", "conditional_on_eligibility", "after_tax", "note",
                   "includes_programs"}
LEDGER_FIELDS = {"assumption_id", "statement", "value", "unit", "origin", "source_url"}
FACT_FIELDS = {"metric_id", "observed", "estimated", "caveat"}

RULE_STATUSES = {"usable", "unverified", "stale", "expired", "not_yet_effective", "region_not_applicable"}
ELIGIBILITY = {"eligible", "not_eligible", "partially_determined", "undetermined"}
OUTCOMES = {"met", "not_met", "indeterminate_rounding", "needs_authorization", "unavailable_metric",
            "estimated_only", "not_assessable"}
DECIDING = {"met", "not_met"}



def calendar_date(value: object) -> bool:
    """A check date has to be a real day: the pattern alone lets 2026-19-40 through."""
    if not isinstance(value, str) or not re.match(r"^\d{4}-\d{2}-\d{2}$", value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True

def text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float))


def validate(value: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if set(value) != REQUIRED:
        errors.append(f"top-level fields must be exactly {sorted(REQUIRED)}")
        return errors
    if value["schema_version"] != "monetization-plan.v1":
        errors.append("schema_version must be monetization-plan.v1")
    for field in ("plan_id", "generated_at", "as_of"):
        if not text(value[field]):
            errors.append(f"{field} must be a non-empty string")

    library = value["rule_library"]
    if not isinstance(library, dict) or set(library) != LIBRARY_FIELDS:
        errors.append("rule_library has an invalid shape")

    creator = value["creator"]
    if not isinstance(creator, dict) or set(creator) != CREATOR_FIELDS:
        errors.append("creator has an invalid shape")
    else:
        if creator["platform"] not in {"youtube", "instagram", "tiktok"}:
            errors.append("creator.platform is invalid")
        if creator["participation"] not in {"new_applicant", "existing_participant", "unknown"}:
            errors.append("creator.participation is invalid")
        if creator["region_source"] not in {"account_declared", "user_stated", "unknown"}:
            errors.append("creator.region_source is invalid")
        # A country with no provenance must not be used to include or exclude anyone.
        if creator["region"] != "UNKNOWN" and creator["region_source"] == "unknown":
            errors.append("creator.region names a country with no stated provenance - a region check must know "
                          "whether the platform reported it or the creator did")
        if creator["region"] == "UNKNOWN" and creator["region_source"] != "unknown":
            errors.append("creator.region is UNKNOWN so region_source must be unknown too")

    boundary = value["claim_boundary"]
    if (not isinstance(boundary, dict) or set(boundary) != BOUNDARY_FIELDS
            or boundary.get("conclusion_kind") != "rule_comparison"
            or boundary.get("guarantees_eligibility") is not False
            or boundary.get("guarantees_earnings") is not False
            or not isinstance(boundary.get("owner_verified_metrics_used"), bool)):
        errors.append("claim_boundary must state a rule comparison that guarantees neither eligibility nor earnings")

    ledger_ids: set[str] = set()
    ledger = value["assumption_ledger"]
    if not isinstance(ledger, list):
        errors.append("assumption_ledger must be an array")
    else:
        for index, item in enumerate(ledger):
            if not isinstance(item, dict) or not set(item) <= LEDGER_FIELDS or not {"assumption_id", "statement", "origin"} <= set(item):
                errors.append(f"assumption_ledger[{index}] has an invalid shape")
                continue
            if not text(item["assumption_id"]) or not text(item["statement"]):
                errors.append(f"assumption_ledger[{index}] requires a non-empty id and statement")
            if item["origin"] not in {"user_supplied", "model_estimate", "rule_library", "not_supplied"}:
                errors.append(f"assumption_ledger[{index}].origin is invalid")
            elif item["origin"] == "rule_library" and not text(item.get("source_url")):
                errors.append(f"assumption_ledger[{index}] comes from the rule library so it must carry source_url")
            ledger_ids.add(item.get("assumption_id"))

    facts = value["account_facts"]
    if not isinstance(facts, list):
        errors.append("account_facts must be an array")
    else:
        for index, fact in enumerate(facts):
            if not isinstance(fact, dict) or not set(fact) <= FACT_FIELDS or not {"metric_id", "observed", "estimated"} <= set(fact):
                errors.append(f"account_facts[{index}] has an invalid shape")
                continue
            observed, estimated = fact["observed"], fact["estimated"]
            if observed is None and estimated is None:
                errors.append(f"account_facts[{index}] carries neither an observed nor an estimated reading")
            if isinstance(observed, dict) and observed.get("basis") not in {
                "public_account_facts", "owner_authorized_analytics", "user_supplied"
            }:
                errors.append(f"account_facts[{index}].observed.basis is invalid")
            if isinstance(estimated, dict) and estimated.get("assumption_ref") not in ledger_ids:
                errors.append(f"account_facts[{index}].estimated cites an assumption that is not in the ledger")

    programs = value["programs"]
    if not isinstance(programs, list) or not programs:
        errors.append("programs must be a non-empty array")
        return errors

    program_ids: set[str] = set()
    for index, program in enumerate(programs):
        where = f"programs[{index}]"
        if not isinstance(program, dict) or set(program) != PROGRAM_FIELDS:
            errors.append(f"{where} must carry exactly {sorted(PROGRAM_FIELDS)}")
            continue
        where = f"program {program['program_id']}"
        program_ids.add(program["program_id"])
        if program["rule_status"] not in RULE_STATUSES:
            errors.append(f"{where}.rule_status is invalid")
        if program["eligibility"] not in ELIGIBILITY:
            errors.append(f"{where}.eligibility is invalid")
        if not text(program["what_it_pays_for"]):
            errors.append(f"{where}.what_it_pays_for must be non-empty")

        if not isinstance(program["region_checked"], bool):
            errors.append(f"{where}.region_checked must be boolean")
        caps = program["additional_requirements_not_assessed"]
        if not isinstance(caps, list) or any(not text(item) for item in caps):
            errors.append(f"{where}.additional_requirements_not_assessed must contain only non-empty strings")

        source = program["source"]
        if not isinstance(source, dict) or set(source) != SOURCE_FIELDS:
            errors.append(f"{where}.source has an invalid shape")
        else:
            if not text(source["source_url"]):
                errors.append(f"{where}.source.source_url must be non-empty - a verdict with no citable rule is not traceable")
            if source["verification_status"] not in {"verified", "unverified_draft"}:
                errors.append(f"{where}.source.verification_status is invalid")
            elif source["verification_status"] != "verified" and program["rule_status"] == "usable":
                errors.append(f"{where} is usable although its rule is not verified")

        # Only a rule that was allowed to decide may produce a decided verdict.
        if program["rule_status"] != "usable" and program["eligibility"] != "undetermined":
            errors.append(f"{where} has rule_status {program['rule_status']} so its eligibility must be undetermined")
        if program["eligibility"] == "eligible":
            if not text(program["decided_by_path"]):
                errors.append(f"{where} is eligible but names no deciding path")
            if caps or program["region_checked"] is False:
                errors.append(f"{where} is eligible although a known requirement or its country list went unchecked - "
                              f"that verdict must be capped at partially_determined")
        if text(program["decided_by_path"]):
            # The named route may accompany a capped verdict - it is the route whose numbers were
            # cleared - but it must be a route that was allowed to decide and did.
            if program["eligibility"] not in {"eligible", "partially_determined"}:
                errors.append(f"{where} names a deciding path alongside a {program['eligibility']} verdict")
            decider = next((p for p in program["paths"] if isinstance(p, dict)
                            and p.get("path_id") == program["decided_by_path"]), None)
            if decider is None:
                errors.append(f"{where} names a deciding path that is not in its own path list")
            elif decider.get("rule_status") != "usable" or decider.get("outcome") != "met":
                errors.append(f"{where} was decided by a path that was not usable and met")

        paths = program["paths"]
        if not isinstance(paths, list):
            errors.append(f"{where}.paths must be an array")
            continue
        for path_index, path in enumerate(paths):
            pwhere = f"{where} path[{path_index}]"
            if not isinstance(path, dict) or set(path) != PATH_FIELDS:
                errors.append(f"{pwhere} must carry exactly {sorted(PATH_FIELDS)}")
                continue
            pwhere = f"{where} path {path['path_id']}"
            if path["outcome"] not in {"met", "not_met", "undetermined"}:
                errors.append(f"{pwhere}.outcome is invalid")
            if path["rule_status"] not in RULE_STATUSES:
                errors.append(f"{pwhere}.rule_status is invalid")
            elif path["rule_status"] != "usable" and path["outcome"] != "undetermined":
                errors.append(f"{pwhere} has rule_status {path['rule_status']} so its outcome must be undetermined")
            requirements = path["requirements"]
            if not isinstance(requirements, list) or not requirements:
                errors.append(f"{pwhere}.requirements must be non-empty")
                continue
            for req_index, req in enumerate(requirements):
                rwhere = f"{pwhere} requirement[{req_index}]"
                if not isinstance(req, dict) or set(req) != REQ_FIELDS:
                    errors.append(f"{rwhere} must carry exactly {sorted(REQ_FIELDS)}")
                    continue
                if req["bound"] not in {"exact", "upper", "lower"}:
                    errors.append(f"{rwhere}.bound is invalid")
                if req["basis"] is not None and req["basis"] not in {
                    "public_account_facts", "owner_authorized_analytics", "user_supplied"
                }:
                    errors.append(f"{rwhere}.basis is invalid")
                if req["outcome"] in DECIDING and req["basis"] is None:
                    errors.append(f"{rwhere} was decided but records no basis for the number that decided it")
                outcome = req["outcome"]
                if outcome not in OUTCOMES:
                    errors.append(f"{rwhere}.outcome is invalid")
                    continue
                # The separation that gives the whole contract its meaning: a gate reported as
                # decided must have a retrieved number behind it.
                if outcome in DECIDING and not number(req["observed_value"]):
                    errors.append(f"{rwhere} reports {outcome} without an observed value - a gate may only be decided "
                                  f"against something retrieved, never against an assumption")
                if outcome not in DECIDING and req["gap"] is not None:
                    errors.append(f"{rwhere} carries a gap although it was not decided against an observed value")
                if outcome == "not_met" and req["comparator"] == "gte" and req["gap"] is None:
                    errors.append(f"{rwhere} is not met on a lower bound and must state how much is missing")
                projection = req["projection"]
                if projection is not None:
                    if not isinstance(projection, dict):
                        errors.append(f"{rwhere}.projection has an invalid shape")
                    elif projection.get("assumption_ref") not in ledger_ids:
                        errors.append(f"{rwhere}.projection cites an assumption that is not in the ledger")
                    elif outcome != "not_met":
                        errors.append(f"{rwhere}.projection exists although the gate was not decided as unmet")

            # A path is met only when nothing in it is still open.
            if path["outcome"] == "met":
                unresolved = [r for r in requirements if r["outcome"] not in DECIDING and not r["any_of_group"]]
                if unresolved:
                    errors.append(f"{pwhere} is met although {len(unresolved)} requirement(s) were never decided")

    scenarios = value["revenue_scenarios"]
    if not isinstance(scenarios, list):
        errors.append("revenue_scenarios must be an array")
    else:
        for index, scenario in enumerate(scenarios):
            where = f"revenue_scenarios[{index}]"
            if not isinstance(scenario, dict) or set(scenario) != SCENARIO_FIELDS:
                errors.append(f"{where} must carry exactly {sorted(SCENARIO_FIELDS)}")
                continue
            if scenario["program_id"] not in program_ids:
                errors.append(f"{where} prices a program that is not in this plan")
            if scenario["model"] not in {"revenue_share", "fan_payment", "platform_fund", "commission"}:
                errors.append(f"{where}.model is invalid")
            if not number(scenario["low"]) or not number(scenario["high"]) or scenario["low"] > scenario["high"]:
                errors.append(f"{where} must carry a numeric range with low no greater than high")
            refs = scenario["assumption_refs"]
            if not isinstance(refs, list):
                errors.append(f"{where}.assumption_refs must be an array")
            elif any(ref not in ledger_ids for ref in refs):
                errors.append(f"{where} cites an assumption that is not in the ledger")
            # An empty ref list is fine and is the strongest case - every input retrieved. What is
            # never fine is an input with no source, or a creator-supplied one with no ledger entry.
            scenario_inputs = scenario["inputs"]
            if not isinstance(scenario_inputs, list) or not scenario_inputs:
                errors.append(f"{where} must record the inputs it was built from")
            else:
                for item in scenario_inputs:
                    if not isinstance(item, dict) or item.get("source") not in {
                        "observed", "benchmark", "creator"
                    }:
                        errors.append(f"{where} has an input with no stated source")
                    elif item["source"] == "creator" and item.get("ref") not in ledger_ids:
                        errors.append(f"{where} uses a creator-supplied value with no ledger entry")
            included = scenario["includes_programs"]
            if not isinstance(included, list) or not all(text(pid) for pid in included) \
                    or scenario["program_id"] in included:
                errors.append(f"{where}.includes_programs must list other programmes by id")
            after_tax = scenario["after_tax"]
            if after_tax is not None:
                entry = next((e for e in ledger if isinstance(e, dict)
                              and e.get("assumption_id") == (after_tax or {}).get("assumption_ref")), None) \
                    if isinstance(after_tax, dict) and isinstance(ledger, list) else None
                if not isinstance(after_tax, dict) or set(after_tax) != {"low", "high", "rate_percent", "assumption_ref"}:
                    errors.append(f"{where}.after_tax has an invalid shape")
                elif not number(after_tax["rate_percent"]) or not 0 <= after_tax["rate_percent"] <= 100:
                    errors.append(f"{where}.after_tax.rate_percent must be between 0 and 100")
                elif (not number(after_tax["low"]) or not number(after_tax["high"])
                      or not 0 <= after_tax["low"] <= after_tax["high"]):
                    errors.append(f"{where}.after_tax must be a range with 0 <= low <= high")
                elif after_tax["low"] > scenario["low"] or after_tax["high"] > scenario["high"]:
                    errors.append(f"{where}.after_tax cannot exceed the pre-tax figure")
                elif entry is None:
                    errors.append(f"{where}.after_tax cites a tax rate that is not in the ledger")
                elif (str(entry.get("unit") or "").strip().lower() != "percent"
                      or not number(entry.get("value")) or entry["value"] != after_tax["rate_percent"]):
                    errors.append(f"{where}.after_tax.rate_percent does not match the percent rate it cites")
                else:
                    keep = 1 - after_tax["rate_percent"] / 100
                    # Both ends were rounded to cents independently, so allow one cent of drift.
                    for end in ("low", "high"):
                        if abs(after_tax[end] - round(scenario[end] * keep, 2)) > 0.01 + 1e-9:
                            errors.append(f"{where}.after_tax.{end} is not the pre-tax {end} at the cited rate")

    needed = value["revenue_inputs_needed"]
    if not isinstance(needed, list):
        errors.append("revenue_inputs_needed must be an array")
    else:
        for index, item in enumerate(needed):
            where = f"revenue_inputs_needed[{index}]"
            if not isinstance(item, dict) or not {"program_id", "input_name", "unit", "label"} <= set(item):
                errors.append(f"{where} has an invalid shape")
                continue
            if item["program_id"] not in program_ids:
                errors.append(f"{where} names a programme that is not in this plan")
            if not text(item["label"]):
                errors.append(f"{where}.label must be non-empty")

    unavailable = value["revenue_not_available"]
    if not isinstance(unavailable, list):
        errors.append("revenue_not_available must be an array")
    else:
        for index, item in enumerate(unavailable):
            where = f"revenue_not_available[{index}]"
            if not isinstance(item, dict) or not {"program_id", "reason"} <= set(item):
                errors.append(f"{where} has an invalid shape")
                continue
            if item["program_id"] not in program_ids:
                errors.append(f"{where} names a programme that is not in this plan")
            if not text(item["reason"]):
                errors.append(f"{where} must carry a reason")

    niche = value["niche_comparison"]
    if niche is not None:
        if not isinstance(niche, dict) or set(niche) != {"niches", "rate", "competition"}:
            errors.append("niche_comparison has an invalid shape")
        else:
            if not isinstance(niche["niches"], list) or not niche["niches"] or not all(text(n) for n in niche["niches"]):
                errors.append("niche_comparison.niches must name at least one niche")
            rate = niche["rate"]
            if not isinstance(rate, dict) or rate.get("published") not in (True, False, None) or not text(rate.get("note")):
                errors.append("niche_comparison.rate must say whether a per-niche rate is published, and why")
            elif not isinstance(rate.get("checked"), list):
                errors.append("niche_comparison.rate.checked must be an array")
            elif rate["published"] is None and rate["checked"]:
                errors.append("niche_comparison.rate is unchecked for this platform so it carries no pages")
            elif rate["published"] is not None and not rate["checked"]:
                errors.append("niche_comparison.rate draws a conclusion with no checked page behind it")
            else:
                platform = (value.get("creator") or {}).get("platform")
                for index, page in enumerate(rate["checked"]):
                    if not (isinstance(page, dict) and page.get("platform") == platform
                            and isinstance(page.get("source_url"), str) and page["source_url"].startswith("https://")
                            and all(text(page.get(k)) for k in ("source_title", "quote"))
                            and calendar_date(page.get("verified_at"))):
                        errors.append(f"niche_comparison.rate.checked[{index}] needs this platform, an https URL, "
                                      f"title, verbatim quote and check date")
            competition = niche["competition"]
            if not isinstance(competition, dict) or competition.get("status") not in {"handed_back", "not_asked"}:
                errors.append("niche_comparison.competition.status is invalid")
            elif competition["status"] == "handed_back" and not (
                    text(competition.get("research_skill")) and text(competition.get("question"))):
                # "No rate table exists" must not stand as the whole answer to a two-part question.
                errors.append("niche_comparison.competition is handed back but names no research step or question")

    gaps = value["data_gaps"]
    if not isinstance(gaps, list):
        errors.append("data_gaps must be an array")
    else:
        for index, gap in enumerate(gaps):
            where = f"data_gaps[{index}]"
            if not isinstance(gap, dict) or set(gap) != GAP_FIELDS:
                errors.append(f"{where} must carry exactly {sorted(GAP_FIELDS)}")
                continue
            if gap["availability"] not in {"available_with_caveat", "partial", "needs_authorization", "unavailable"}:
                errors.append(f"{where}.availability is invalid")
            if not isinstance(gap["blocks"], list) or not gap["blocks"]:
                errors.append(f"{where} must name at least one gate it blocks")
            if not text(gap["what_would_unblock"]):
                errors.append(f"{where} must say what would unblock it")
    if not isinstance(value["income_breakdown_requested"], bool):
        errors.append("income_breakdown_requested must be a boolean")
    rows = value["income_rows"]
    errors.extend(income_rows.validate_rows(rows, value["income_totals"], {"platform_payout", "digital_product"}))
    # An eligibility bar is not income: a priced row has to be one of this plan's scenarios, at its range.
    by_id = {s.get("scenario_id"): s for s in scenarios if isinstance(s, dict)} if isinstance(scenarios, list) else {}
    for index, row in enumerate(rows if isinstance(rows, list) else []):
        if isinstance(row, dict) and isinstance(row.get("amount"), dict):
            scenario = by_id.get(row.get("source_ref"))
            if scenario is None or (row["amount"].get("low"), row["amount"].get("high")) != (scenario["low"], scenario["high"]):
                errors.append(f"income_rows[{index}] carries an amount that is not one of this plan's revenue scenarios")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("plan", type=Path)
    args = parser.parse_args()
    try:
        value = json.loads(args.plan.read_text(encoding="utf-8"))
        errors = validate(value) if isinstance(value, dict) else ["plan must be a JSON object"]
    except (OSError, json.JSONDecodeError) as error:
        errors = [str(error)]
    print(json.dumps({"valid": not errors, "errors": errors}, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
