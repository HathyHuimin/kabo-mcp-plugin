#!/usr/bin/env python3
"""Semantic gate for a BrandDealQuote.

Shape is the easy half and the schema covers it. What this file enforces is the set of promises
that make the quote worth trusting, each of which corresponds to a way pricing advice goes wrong:

  1. No figure while the terms that decide it are open. Usage rights and exclusivity are most of
     the price; a number quoted before they are settled is a number for a scope nobody agreed.
  2. Sources are never merged. One band per source, all the way through to the package total. Two
     sources that disagree by 10x have no meaningful midpoint, and printing one invents precision.
  3. Nothing unsourced ever multiplies a fee. A term the trade agrees matters but nobody prices is
     raised as a question, never converted into a percentage.
  4. Platform is never charged twice. The bands are per platform already.
  5. A total only exists for a source that priced every line, or it silently values a line at zero,
     and it is exactly the sum of the line totals the report prints, so the lines add up to it.
  6. The second pricing route only appears when it actually disagrees, by at least the threshold.
  7. Transacted averages always carry their caveat, because an all-tier average compared against
     one tier's band is a false comparison.
  8. Brand advertising activity never moves a price. It is observable context about what a brand
     buys media for; what they will pay a creator is a different line in their plan, and inferring
     one from the other is the failure this whole structure is built to prevent.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
# Sibling imports must not leave a __pycache__ in the signed, checksummed skill directory.
sys.dont_write_bytecode = True
import income_rows  # noqa: E402  (ships byte-identical in the platform-monetization Skill)

MARKET_CLASSES = {"industry_benchmark", "marketplace_listing"}
MIN_COMPARABLES = 5
LEVER_KINDS = {"usage_rights", "exclusivity", "rush", "format", "engagement", "niche", "geography", "revisions"}
PRICE_DECIDING_TERMS = {"usage_rights", "exclusivity"}

REQUIRED_BOUNDARIES = {
    "legal_review": "This prices a scope. It is not a legal review of the contract, and it does not approve any term.",
    "not_a_platform_payout": "Sponsorship fees and platform payouts have different units and different evidence. Nothing here estimates platform ad revenue.",
    "brand_budget_not_inferred": "Nothing here estimates what the brand can afford. Observable advertising activity is not a budget.",
}


def close(actual: object, expected: float | None) -> bool:
    """Equal to the cent; a missing ceiling only matches a missing ceiling."""
    if expected is None or actual is None:
        return actual is None and expected is None
    return isinstance(actual, (int, float)) and abs(actual - expected) < 0.005


def validate(quote: dict) -> list[str]:
    problems: list[str] = []

    def fail(message: str) -> None:
        problems.append(message)

    if quote.get("quote_version") != "brand-deal-quote.v1":
        fail(f"unexpected quote_version {quote.get('quote_version')!r}")
    if quote.get("currency") != "USD":
        fail("this version prices in USD only")

    determinacy = quote.get("determinacy")
    if determinacy not in {"terms_settled", "terms_incomplete"}:
        fail(f"unknown determinacy {determinacy!r}")

    reply = quote.get("negotiation_reply") or {}
    open_terms = quote.get("open_terms") or []
    open_names = {term.get("term") for term in open_terms}

    # 1 - no number before the terms that decide it are settled
    if determinacy == "terms_incomplete":
        if reply.get("should_state_a_number"):
            fail("terms are incomplete but the reply is set to state a number; the terms still open "
                 "are most of the price")
        if reply.get("opening_ask") is not None:
            fail("terms are incomplete but an opening ask was produced")
        if not open_terms:
            fail("terms are incomplete yet nothing is listed as open, so the creator is told a "
                 "price is provisional without being told what would settle it")
    else:
        settled_but_open = PRICE_DECIDING_TERMS & open_names
        if settled_but_open:
            fail(f"determinacy says terms are settled while {sorted(settled_but_open)} are still listed as open")

    for term in open_terms:
        if not term.get("question_to_ask"):
            fail(f"open term {term.get('term')!r} names no question that would settle it")

    unpriced_terms = {term["term"] for term in open_terms if term.get("price_impact") == "unpriced"}

    for item in quote.get("line_items", []):
        label = item.get("deliverable_id")
        base_sources = [band.get("source") for band in item.get("base_bands", [])]
        adjusted_sources = [band.get("source") for band in item.get("adjusted_bands", [])]

        # 2 - one adjusted band per base band, nothing merged and nothing invented
        if sorted(base_sources) != sorted(adjusted_sources):
            fail(f"line {label!r} has base bands from {sorted(set(base_sources))} but adjusted bands "
                 f"from {sorted(set(adjusted_sources))}; sources must survive the adjustment one to one")
        if len(set(adjusted_sources)) != len(adjusted_sources):
            fail(f"line {label!r} carries two adjusted bands for one source")

        for band in item.get("base_bands", []):
            if band.get("source_class") not in MARKET_CLASSES:
                fail(f"line {label!r} prices against a {band.get('source_class')!r} band; a claim "
                     "about what the market pays needs a source that measured the market")
            if band.get("source_class") == "marketplace_listing":
                sample = band.get("sample_size")
                if not isinstance(sample, int) or sample < MIN_COMPARABLES:
                    fail(f"line {label!r} carries an observed band built on {sample} listing(s); below "
                         f"{MIN_COMPARABLES} the spread is noise, not a distribution")
                if not band.get("observed_at"):
                    fail(f"line {label!r} carries an observed band with no observation date, so nobody "
                         "can tell how old the listings are")
            elif band.get("sample_size") is not None:
                fail(f"line {label!r} attaches a sample size to a compiled band; compilations do not "
                     "publish their sample, and borrowing one overstates what is known")

            low, high = band.get("low"), band.get("high")
            if high is None and not band.get("open_ended"):
                fail(f"line {label!r} has a band with no ceiling that is not marked open_ended")
            if high is not None and high < low:
                fail(f"line {label!r} has a band whose high is below its low")

        base_by_source = {band["source"]: band for band in item.get("base_bands", [])}
        for band in item.get("adjusted_bands", []):
            base = base_by_source.get(band.get("source"))
            if base is None:
                continue
            if band.get("low", 0) < base.get("low", 0) - 1e-9:
                fail(f"line {label!r} adjusted below its base band for {band['source']!r}; every "
                     "lever in this framework adds, none discounts")

        # 5 - a line total is its per-item band times the quantity, nothing added on top
        line_totals = item.get("line_totals") or []
        totals_by_source = {total.get("source"): total for total in line_totals}
        if len(totals_by_source) != len(line_totals):
            fail(f"line {label!r} repeats a source in line_totals; every reading needs exactly one "
                 "line total")
        if sorted(totals_by_source) != sorted(adjusted_sources):
            fail(f"line {label!r} has line totals from {sorted(totals_by_source)} but adjusted bands "
                 f"from {sorted(set(adjusted_sources))}; every reading needs exactly one line total")
        quantity = item.get("quantity") or 0
        for band in item.get("adjusted_bands", []):
            total = totals_by_source.get(band.get("source"))
            if total is None:
                continue
            expected_high = None if band.get("high") is None else round(band["high"] * quantity, 2)
            if (not close(total.get("low"), round(band.get("low", 0) * quantity, 2))
                    or not close(total.get("high"), expected_high)
                    or bool(total.get("open_ended")) != bool(band.get("open_ended"))):
                fail(f"line {label!r} carries a line total for {band.get('source')!r} that is not its "
                     f"per-item band times {quantity}")

        seen_levers = set()
        for applied in item.get("applied_levers", []):
            lid = applied.get("id")
            if lid in seen_levers:
                fail(f"line {label!r} applies lever {lid!r} twice")
            seen_levers.add(lid)
            # 4 - platform is already inside the bands
            if applied.get("kind") not in LEVER_KINDS:
                fail(f"line {label!r} applies lever {lid!r} of unknown kind {applied.get('kind')!r}")
            if applied.get("kind") == "platform":
                fail(f"line {label!r} charges a platform premium on top of a per-platform band")
            # 3 - nothing unsourced multiplies a fee
            if not applied.get("source"):
                fail(f"line {label!r} applies lever {lid!r} with no source behind it")
            if applied.get("low_pct") is None:
                fail(f"line {label!r} applies lever {lid!r} with no percentage, so it cannot have "
                     "moved the price in a reviewable way")

        for term in unpriced_terms:
            if any(applied.get("kind") == term for applied in item.get("applied_levers", [])):
                fail(f"line {label!r} prices {term!r}, which is listed as having no published price")

    # 5 - a package total only for a source that priced every line
    if quote.get("line_items"):
        per_line = [{band["source"] for band in item.get("adjusted_bands", [])} for item in quote["line_items"]]
        complete = set.intersection(*per_line) if all(per_line) else set()
        totalled = {total.get("source") for total in quote.get("package_totals", [])}
        if not totalled <= complete:
            fail(f"package totals exist for {sorted(totalled - complete)}, which did not price every "
                 "line; the missing line would be counted as free")
        for package in quote.get("package_totals", []):
            source = package.get("source")
            if source not in complete:
                continue
            lines = [next((t for t in item.get("line_totals") or [] if t.get("source") == source), None)
                     for item in quote["line_items"]]
            if any(line is None for line in lines):
                continue
            highs = [line.get("high") for line in lines]
            expected_high = None if any(h is None for h in highs) else round(sum(highs), 2)
            if (not close(package.get("low"), round(sum(line.get("low", 0) for line in lines), 2))
                    or not close(package.get("high"), expected_high)
                    or bool(package.get("open_ended")) != any(bool(line.get("open_ended")) for line in lines)):
                fail(f"package total for {source!r} is not the sum of its line totals; the report "
                     "would state a total its own lines do not add up to")

    # 6 - the second route only when it disagrees
    reach = quote.get("reach_estimate")
    if reach is not None:
        ratio, threshold = reach.get("divergence_ratio"), reach.get("threshold")
        if ratio is None or threshold is None or ratio < threshold:
            fail("the reach-based route is shown while agreeing with the follower-tier route; that "
                 "adds a second number without adding information")
        if reach.get("interpretation") not in {"reach_below_tier", "reach_above_tier"}:
            fail("the reach-based route does not say which way it disagrees, which is the part that "
                 "changes what the creator should do")

    # 7 - transacted averages keep their caveat
    for entry in quote.get("transacted_reference", []):
        if entry.get("source") == "marketplace.observed":
            fail("an observed asking price is listed among transacted amounts; a profile shows what "
                 "a creator wants, never what a brand paid")
        if entry.get("source_class") != "marketplace_transaction":
            fail(f"transacted reference from {entry.get('source')!r} is not a transaction source")
        if not entry.get("caveat"):
            fail(f"transacted reference from {entry.get('source')!r} travels without its caveat")

    # 8 - advertising activity is posture, never price
    brand_ads = quote.get("brand_ad_context")
    if brand_ads is not None:
        if brand_ads.get("price_impact") != "none":
            fail("brand advertising activity is recorded as affecting the price; what a brand spends "
                 "on media does not establish what it will pay a creator")
        caveats = set(brand_ads.get("caveats") or [])
        for required in ("single_ecosystem_only", "not_a_creator_budget"):
            if required not in caveats:
                fail(f"brand advertising activity travels without its {required!r} caveat")
        if brand_ads.get("active_ads") == 0 and brand_ads.get("scale") != "none":
            fail("brand advertising activity reports no active ads but a non-zero scale")
        if not brand_ads.get("coverage_complete", True) and "count_is_a_floor" not in caveats:
            fail("the ad count is incomplete but is not marked as a floor")
        for item in quote.get("line_items", []):
            for applied in item.get("applied_levers", []):
                if "brand_ad" in str(applied.get("id", "")) or applied.get("source") == "brand-ad-library":
                    fail(f"line {item.get('deliverable_id')!r} prices a lever drawn from brand "
                         "advertising activity")

    boundaries = quote.get("boundaries") or {}
    for key, text in REQUIRED_BOUNDARIES.items():
        if boundaries.get(key) != text:
            fail(f"boundary {key!r} is missing or altered")

    rows, totals = quote.get("income_rows"), quote.get("income_totals")
    if rows is None or totals is None or not isinstance(quote.get("income_breakdown_requested"), bool):
        fail("income_rows, income_totals and income_breakdown_requested are required")
    else:
        for message in income_rows.validate_rows(rows, totals, {"brand_deal", "ugc_production"}):
            fail(message)
        lines = {item.get("deliverable_id"): item for item in quote.get("line_items", [])}
        for index, row in enumerate(rows if isinstance(rows, list) else []):
            if not isinstance(row, dict) or not isinstance(row.get("amount"), dict):
                continue
            # 1 again, per row: no amount while the terms that decide it are open.
            if quote.get("determinacy") != "terms_settled":
                fail(f"income_rows[{index}] carries an amount while the terms that decide the price are open")
            item = lines.get(row.get("source_ref"))
            ranges = [] if item is None else [
                (t.get("low"), t.get("high")) for t in item.get("line_totals") or []]
            if (row["amount"].get("low"), row["amount"].get("high")) not in ranges:
                fail(f"income_rows[{index}] carries an amount that is not one of its line's published ranges")

    # 9 - a brief that grants no rights is not priced as a short licence
    grant = quote.get("usage_rights_grant")
    if grant not in {"unspecified", "none", "licensed"}:
        fail(f"unknown usage_rights_grant {grant!r}")
    if grant == "none":
        for item in quote.get("line_items", []):
            for applied in item.get("applied_levers", []):
                if applied.get("kind") == "usage_rights":
                    fail(f"line {item.get('deliverable_id')!r} applies {applied.get('id')!r} although the "
                         "brief grants no usage rights")
    if (grant == "unspecified") != ("usage_rights" in open_names):
        fail("usage_rights_grant and open_terms disagree about whether usage rights are settled")

    # 10 - every public figure can be traced to the page it came from
    listed = {entry.get("id"): entry for entry in quote.get("sources") or []}
    for sid, entry in listed.items():
        if not entry.get("publisher") or not str(entry.get("url", "")).startswith("https://"):
            fail(f"source {sid!r} is listed without a publisher and an https url")
    cited = (
        [band.get("source") for item in quote.get("line_items", []) for band in item.get("base_bands", [])]
        + [a.get("source") for item in quote.get("line_items", []) for a in item.get("applied_levers", [])]
        + [entry.get("source") for entry in quote.get("transacted_reference", [])]
        + ([reach.get("source")] if reach else [])
        + [sid for rule in quote.get("negotiation_rules_applied") or [] for sid in rule.get("sources") or []]
    )
    for sid in dict.fromkeys(cited):
        # Observed listings and the internal framework have no public page to point at.
        if sid and sid != "marketplace.observed" and not sid.startswith("internal.") and sid not in listed:
            fail(f"the quote cites {sid!r} but does not list where a reader can check it")

    if quote.get("framework", {}).get("stale") and not quote.get("data_gaps"):
        fail("the framework is past its staleness budget but nothing records that as a limitation")

    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("quote", type=Path)
    args = parser.parse_args()

    quote = json.loads(args.quote.read_text(encoding="utf-8"))
    problems = validate(quote)
    if problems:
        print(f"BrandDealQuote FAILED ({len(problems)} problem(s)):")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print(
        f"BrandDealQuote OK: {len(quote['line_items'])} line item(s), "
        f"{len(quote['package_totals'])} sourced total(s), determinacy={quote['determinacy']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
