#!/usr/bin/env python3
"""Deterministic pricing engine: a brand-deal request plus the framework, out comes a BrandDealQuote.

No model judgement runs in here. Everything the quote asserts is either read from the framework or
computed from the request, which is what makes the result reproducible and reviewable.

Three decisions in this file carry most of the weight:

  * Bands are matched by follower count, not by tier name. Sources put their tier boundaries in
    different places, so one account can be mid-tier to one source and micro to another. Both bands
    come back, and nothing reconciles them.
  * Adjustments widen inside a group and add across groups. Two sources pricing the same six-month
    licence differently produce one wider range, not two stacked charges.
  * The format premium only applies where the base band is not already a video. A TikTok band is
    quoted per video; charging a video premium on top of it would bill the same thing twice.
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent.parent
DEFAULT_FRAMEWORK = SKILL_DIR / "references" / "frameworks" / "creator-brand-deal-pricing.v1.json"

# Below this, the spread between listings is noise rather than a distribution, and a band built on
# it would claim a precision the observations do not carry.
MIN_COMPARABLES = 5

VIDEO_FORMATS = {"short_video", "long_video", "dedicated_video", "integration"}
# Platforms whose published bands are already quoted per video. Instagram's are quoted per feed
# post, so a video deliverable there is genuinely a step up in production cost.
VIDEO_NATIVE_PLATFORMS = {"tiktok", "youtube"}

NICHE_PREMIUM_KEYWORDS = {"finance", "fintech", "investing", "software", "saas", "b2b", "tech", "technology"}

# Published category lock-out windows, shortest first.
CATEGORY_EXCLUSIVITY = (
    (30, "exclusivity.category.30d"),
    (60, "exclusivity.category.60d"),
    (90, "exclusivity.category.90d"),
    (180, "exclusivity.category.180d"),
    (365, "exclusivity.category.365d"),
)
UNPRICED_LICENCE = (
    "a licence longer than six months, perpetual, or a full buyout - no published source prices "
    "one, so it is not inside these ranges and has to be priced separately"
)
UNPRICED_EXCLUSIVITY = (
    "exclusivity longer than any published window - it is not inside these ranges and has to be "
    "priced separately"
)

CPM_FORMAT_MAP = {
    "dedicated_video": "dedicated_video",
    "long_video": "dedicated_video",
    "integration": "integration",
    "short_video": "short_video",
}

# What each advertising posture changes about the conversation. None of these say anything about
# a number, because the evidence behind them cannot support one.
BRAND_AD_POSTURE = {
    "name_not_matched": (
        "Nothing matched that brand name. That can be a spelling difference or a company trading "
        "under another name, so it says nothing about whether they advertise. Treat this as no "
        "reading rather than as a quiet advertiser."
    ),
    "none": (
        "No advertising was visible for this brand. Price the work on what it is worth to you, and "
        "expect little appetite for a separate usage-rights fee - if they are not running ads, they "
        "are not buying media."
    ),
    "testing": (
        "This brand is advertising lightly. Early campaigns are often where a brand is most willing "
        "to pay for creative that works, and least likely to have a fixed rate card to point at."
    ),
    "steady": (
        "This brand advertises consistently. That makes usage rights the item worth pricing "
        "separately and explicitly: they have somewhere to run your content, so the licence is worth "
        "something concrete to them."
    ),
    "large": (
        "This brand advertises at scale across markets. Ask what the content will be used for before "
        "quoting, and treat the usage-rights licence as a separate line rather than a courtesy."
    ),
}

TERM_GUIDANCE = {
    "usage_rights": (
        "Whether the brand may run this content as its own advertising, and for how long. It is "
        "commonly the largest single item after the content fee, and briefs routinely omit it.",
        "Will you be running this as a paid ad, and for how long do you need the licence?",
        "large",
    ),
    "exclusivity": (
        "Whether competing brands are off limits afterwards, and for how long. It removes income "
        "that has not been offered yet, so it is priced separately from the work itself.",
        "Are you asking for category exclusivity, and over what period?",
        "large",
    ),
    "campaign_term": (
        "How long the campaign runs. It sets when the content is live and when any licence starts.",
        "What are the campaign start and end dates?",
        "moderate",
    ),
    "geography": (
        "Which markets the campaign targets. Audience location moves published rates.",
        "Which markets is this campaign running in?",
        "moderate",
    ),
    "revisions": (
        "How many revision rounds are included. No published source prices an extra round, which is "
        "exactly why the cap belongs in writing rather than in a fee.",
        "How many rounds of revisions are included, and what happens beyond that?",
        "unpriced",
    ),
    "rush": (
        "How fast delivery has to be. Speed has a published premium once it goes inside 72 hours.",
        "What is the delivery deadline?",
        "moderate",
    ),
}


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def skill_path(path: Path) -> Path:
    """A relative path that names a file in the Skill directory is read from there.

    Hosts run the pipeline from the run directory, so `references/...` only exists relative to
    the Skill directory there; a same-named file in the run directory never overrides the
    packaged one. Any other relative path keeps its working-directory meaning.
    """
    if path.is_absolute():
        return path
    candidate = SKILL_DIR / path
    return candidate if candidate.exists() else path


def bands_for(framework: dict, platform: str, followers: float) -> list[dict]:
    """Every source's band whose follower range contains this account."""
    found = []
    for band in framework["posted_rate_bands"]:
        if band["platform"] != platform:
            continue
        if followers < band["followers_min"]:
            continue
        if band["followers_max"] is not None and followers >= band["followers_max"]:
            continue
        found.append(band)
    return found


def percentile(sorted_values: list[float], fraction: float) -> float:
    """Nearest-rank percentile. No interpolation, so the same listings always give the same band."""
    index = max(1, min(len(sorted_values), -(-len(sorted_values) * fraction // 1)))
    return sorted_values[int(index) - 1]


def comparables_band(request: dict, deliverable: dict) -> dict | None:
    """Turn observed marketplace listings into one more band, or nothing at all."""
    observed = request.get("marketplace_comparables")
    if not observed:
        return None
    if observed["platform"] != deliverable["platform"]:
        return None
    if observed["deliverable_format"] != deliverable["format"]:
        return None
    observations = observed.get("observations") or []
    if len(observations) < MIN_COMPARABLES:
        return None

    prices = sorted(float(entry["price"]) for entry in observations)
    followers = [float(entry["followers"]) for entry in observations]
    return {
        "source": "marketplace.observed",
        "source_class": "marketplace_listing",
        "tier": "observed",
        "followers_min": int(min(followers)),
        "followers_max": int(max(followers)),
        "low": round(percentile(prices, 0.25), 2),
        "high": round(percentile(prices, 0.75), 2),
        "open_ended": False,
        "sample_size": len(observations),
        "observed_at": observed["observed_at"],
    }


def lever(framework: dict, lever_id: str) -> dict:
    for entry in framework["levers"]:
        if entry["id"] == lever_id:
            return entry
    raise KeyError(f"framework has no lever {lever_id!r}")


def usage_rights_grant(rights: dict) -> str:
    """Say whether the brief licenses the content at all: unspecified, none or licensed.

    "No reuse, no paid use, no buyout" is a settled term, not a short licence. Read as zero days
    it used to fall into the 30-day lever and print "reused for up to 30 days" over a brief that
    granted nothing.
    """
    status = rights["status"]
    if status == "unspecified":
        return "unspecified"
    granted = (
        (rights.get("licence_days") or 0) > 0
        or rights.get("perpetual")
        or rights.get("paid_amplification")
        or rights.get("full_buyout")
    )
    if status == "none_granted":
        if granted:
            raise ValueError("usage_rights is none_granted but also grants a licence, paid use or a buyout")
        return "none"
    if not granted and rights.get("licence_days") == 0:
        return "none"
    return "licensed"


def select_levers(framework: dict, request: dict, deliverable: dict) -> tuple[list[dict], list[str]]:
    """Pick the levers this line actually earns, plus terms that apply but nobody prices."""
    terms = request["brief"]["terms"]
    account = request["account_facts"]
    chosen: list[dict] = []
    unpriced: list[str] = []

    rights = terms["usage_rights"]
    if usage_rights_grant(rights) == "licensed":
        days = rights.get("licence_days")
        if rights.get("full_buyout") or rights.get("perpetual") or days is None or days > 180:
            # No source read for the framework publishes a figure past six months; say so instead
            # of stretching the six-month number over a licence it never priced.
            unpriced.append(UNPRICED_LICENCE)
        else:
            # Paid use sits inside the same published licence fee, so these share one group and
            # widen rather than stack.
            if 0 < days <= 90:
                chosen.append(lever(framework, "usage_rights.licence.up_to_90d"))
            elif days > 90:
                chosen.append(lever(framework, "usage_rights.licence.180d"))
            if rights.get("paid_amplification"):
                chosen.append(lever(framework, "usage_rights.paid_amplification"))

    exclusivity = terms["exclusivity"]
    if exclusivity["status"] == "specified" and (exclusivity.get("days") or 0) > 0:
        days = exclusivity["days"]
        if exclusivity.get("scope") == "category":
            # The source publishes 30, 60, 90 days, six months and twelve months; take the
            # shortest published window that covers the requested one.
            for limit, lever_id in CATEGORY_EXCLUSIVITY:
                if days <= limit:
                    chosen.append(lever(framework, lever_id))
                    if lever_id == "exclusivity.category.30d":
                        chosen.append(lever(framework, "exclusivity.category.30d.alt"))
                    break
            else:
                unpriced.append(UNPRICED_EXCLUSIVITY)
        elif days <= 30:
            chosen.append(lever(framework, "exclusivity.30d"))
        elif days <= 90:
            chosen.append(lever(framework, "exclusivity.90d"))
        else:
            unpriced.append(UNPRICED_EXCLUSIVITY)

    rush = terms["rush"]
    if rush["status"] == "specified" and rush.get("delivery_within_hours") is not None:
        if rush["delivery_within_hours"] <= 72:
            chosen.append(lever(framework, "rush.under_72h"))

    if deliverable["format"] in VIDEO_FORMATS and deliverable["platform"] not in VIDEO_NATIVE_PLATFORMS:
        chosen.append(lever(framework, "format.video_over_static"))

    engagement = account.get("engagement_rate_pct")
    if engagement and engagement["value"] > 5:
        chosen.append(lever(framework, "engagement.above_5pct"))

    niche = (account.get("niche") or "").lower()
    if niche and any(word in niche for word in NICHE_PREMIUM_KEYWORDS):
        chosen.append(lever(framework, "niche.premium"))

    if account.get("audience_geography") == "mostly_us_western_europe":
        chosen.append(lever(framework, "geography.western_audience"))

    revisions = terms["revisions"]
    if revisions["status"] == "specified":
        unpriced.append(
            "revision rounds beyond the agreed cap - every guide says to cap them in writing, none "
            "of them prices one"
        )

    return chosen, unpriced


def aggregate_levers(levers: list[dict]) -> tuple[float, float, bool]:
    """Widen within a group, add across groups. Returns (low_pct, high_pct, open_ended)."""
    groups: dict[str, list[dict]] = {}
    for entry in levers:
        groups.setdefault(entry["group"], []).append(entry)

    total_low = 0.0
    total_high = 0.0
    open_ended = False
    for entries in groups.values():
        lows = [e["low_pct"] for e in entries if e.get("low_pct") is not None]
        highs = [e["high_pct"] for e in entries if e.get("high_pct") is not None]
        if not lows:
            continue
        group_low = min(lows)
        if any(e.get("open_ended") for e in entries):
            open_ended = True
        # An open-ended lever publishes a floor and no ceiling. Its floor is the honest upper
        # figure to carry; the open_ended flag is what says the real number can exceed it.
        group_high = max(highs) if highs else group_low
        total_low += group_low
        total_high += max(group_high, group_low)
    return total_low, total_high, open_ended


def apply_adjustment(band: dict, low_pct: float, high_pct: float, open_ended: bool) -> dict:
    low = round(band["low"] * (1 + low_pct / 100), 2)
    if band["high"] is None:
        high = None
        result_open = True
    else:
        high = round(band["high"] * (1 + high_pct / 100), 2)
        result_open = bool(band.get("open_ended")) or open_ended
    return {"source": band["source"], "low": low, "high": high, "open_ended": result_open}


def line_total(band: dict, quantity: int) -> dict:
    """One adjusted per-item band times the line's quantity: what the line costs, per source."""
    return {
        "source": band["source"],
        "low": round(band["low"] * quantity, 2),
        "high": None if band["high"] is None else round(band["high"] * quantity, 2),
        "open_ended": band["open_ended"],
    }


def midpoint(low: float, high: float | None) -> float:
    return low if high is None else (low + high) / 2


def build_reach_estimate(framework: dict, request: dict, follower_span: tuple[float, float | None] | None) -> dict | None:
    model = framework.get("cpm_model")
    account = request["account_facts"]
    views = account.get("average_views")
    if model is None or not views or follower_span is None:
        return None

    niche_name = (account.get("niche") or "").lower()
    match = None
    for entry in model["niches"]:
        if entry["niche"].lower() in niche_name or niche_name in entry["niche"].lower():
            match = entry
            break
    if match is None:
        return None

    deliverable = request["brief"]["deliverables"][0]
    cpm_format = CPM_FORMAT_MAP.get(deliverable["format"])
    multiplier = next((m for m in model["format_multipliers"] if m["format"] == cpm_format), None)
    if multiplier is None:
        return None

    low = round(views["value"] / 1000 * match["cpm_low"] * multiplier["low"], 2)
    high = round(views["value"] / 1000 * match["cpm_high"] * multiplier["high"], 2)

    tier_mid = midpoint(follower_span[0], follower_span[1])
    reach_mid = midpoint(low, high)
    if tier_mid <= 0 or reach_mid <= 0:
        return None
    ratio = max(reach_mid / tier_mid, tier_mid / reach_mid)
    if ratio < framework["cpm_divergence_threshold"]:
        # The two routes agree. Showing a second range that says the same thing adds noise, not
        # information, so it is left out.
        return None

    return {
        "source": model["source"],
        "low": low,
        "high": high,
        "inputs": {
            "average_views": views["value"],
            "average_views_basis": views["basis"],
            "niche": match["niche"],
            "cpm_low": match["cpm_low"],
            "cpm_high": match["cpm_high"],
            "format_multiplier_low": multiplier["low"],
            "format_multiplier_high": multiplier["high"],
        },
        "divergence_ratio": round(ratio, 2),
        "threshold": framework["cpm_divergence_threshold"],
        "interpretation": "reach_below_tier" if reach_mid < tier_mid else "reach_above_tier",
    }


def brand_ad_context(request: dict) -> dict | None:
    """Project an advertising-activity lookup into negotiation posture, and nothing else.

    Every priced structure in this module is computed before this runs and none of them read it.
    That is the whole design: the evidence cannot move a price because it never reaches the code
    that produces one.
    """
    evidence = request.get("brand_ad_evidence")
    if not evidence:
        return None

    caveats = ["single_ecosystem_only", "not_a_creator_budget"]
    if not evidence.get("coverage_complete", True):
        caveats.append("count_is_a_floor")
    if evidence.get("matched_name") is None:
        caveats.append("name_not_matched")

    # A name that did not match is its own outcome. Folding it into "advertises nothing" would
    # turn a spelling difference into a claim about the brand.
    posture = "name_not_matched" if evidence.get("matched_name") is None else evidence["scale"]

    return {
        "observed_at": evidence["observed_at"],
        "brand_query": evidence.get("brand_query"),
        "matched_name": evidence.get("matched_name"),
        "active_ads": evidence["active_ads"],
        "country_count": evidence.get("country_count", 0),
        "months_running": evidence.get("months_running"),
        "scale": evidence["scale"],
        "coverage_complete": evidence.get("coverage_complete", True),
        "price_impact": "none",
        "posture": posture,
        "posture_note": BRAND_AD_POSTURE[posture],
        "caveats": caveats,
    }


def open_terms_for(request: dict) -> list[dict]:
    terms = request["brief"]["terms"]
    out = []
    for name, (why, question, impact) in TERM_GUIDANCE.items():
        if terms[name]["status"] == "unspecified":
            out.append(
                {"term": name, "why_it_matters": why, "question_to_ask": question, "price_impact": impact}
            )
    return out


def build_negotiation_reply(framework: dict, request: dict, determinacy: str,
                            totals: list[dict], open_terms: list[dict]) -> tuple[dict, list[dict]]:
    rules = {r["id"]: r for r in framework["negotiation_rules"]}
    applied = [
        {
            "id": r["id"],
            "rule": r["rule"],
            "sources": r["sources"],
            "low_pct": r.get("low_pct"),
            "high_pct": r.get("high_pct"),
        }
        for r in framework["negotiation_rules"]
    ]

    questions = [term["question_to_ask"] for term in open_terms]
    mode = request["mode"]

    if determinacy == "terms_incomplete":
        opening_ask = None
        should = False
        reason = rules["no_number_before_terms"]["why"]
        opening = (
            "Thanks for thinking of me - this looks like a good fit. Before I put a number on it, "
            "a few things about the scope are still open."
        )
        closing = (
            "Once those are settled I can send a rate that covers the whole scope rather than a "
            "figure that only covers the content."
        )
    else:
        rule = rules["open_above_target"]
        opening_ask = None
        if totals:
            anchor = totals[0]
            opening_ask = {
                "source": anchor["source"],
                "low": round(anchor["low"] * (1 + (rule.get("low_pct") or 0) / 100), 2),
                "high": None if anchor["high"] is None
                else round(anchor["high"] * (1 + (rule.get("high_pct") or 0) / 100), 2),
                "open_ended": anchor["open_ended"],
            }
        should = True
        reason = "Usage rights and exclusivity are both settled, so a figure now covers the real scope."
        opening = "Thanks for the detail - that is enough for me to price it properly."
        closing = (
            "Happy to talk through the breakdown. If the budget needs to come down, I would rather "
            "reduce the scope than the rate for the same scope."
        )
        if not questions:
            questions = ["Anything else about the campaign I should factor in before we confirm?"]

    if mode == "counter":
        closing = (
            "That is below where this scope prices for me. " + closing
        )

    return (
        {
            "should_state_a_number": should,
            "reason": reason,
            "opening_ask": opening_ask,
            "draft": {"opening": opening, "questions": questions, "closing": closing},
        },
        applied,
    )


def income_rows_for(line_items: list[dict], determinacy: str, open_terms: list[dict],
                    data_gaps: list[dict]) -> list[dict]:
    """Project the quote onto income rows: the brand-deal part of an income breakdown.

    One row per deliverable per published reading, because readings are never merged: the second
    reading of a line is the same money read another way, so it is marked as already inside the
    first rather than added to it. While the terms that decide the price are open, a row carries
    no amount - it names the terms instead, exactly as the quote itself states no figure.
    """
    deciding = [t for t in open_terms if t["term"] in ("usage_rights", "exclusivity")]
    rows: list[dict] = []
    for item in line_items:
        first = f"D-{item['deliverable_id']}-1"
        common = {
            "stream_kind": "brand_deal",
            "stream": f"{item['quantity']} x {item['format']} on {item['platform']}",
            "unit": "USD per deliverable",
            "volume_basis": {"description": f"{item['quantity']} deliverable(s), from the brief",
                             "source": "user_supplied"},
            "period": "one_off",
            "cost_basis": {"platform_cut": "not_modelled", "tax": "pre_tax"},
            "after_tax": None,
            "source_ref": item["deliverable_id"],
        }
        unpriced = [f"unpriced term: {term}" for term in item.get("unpriced_terms") or []]
        bands = item["adjusted_bands"]
        if not bands:
            gap = next((g["effect"] for g in data_gaps if item["deliverable_id"] in g["effect"]),
                       "no published range covers this line")
            rows.append({**common, "row_id": first,
                         "rate_basis": {"origin": None, "description": "no published range covers this line"},
                         "already_counted_in": [], "missing": [gap] + unpriced, "amount": None})
            continue
        settled = determinacy == "terms_settled"
        waiting = [f"{t['term']}: {t['question_to_ask']}" for t in deciding] or [
            "the terms that decide the price are not settled"]
        observed_on = {band["source"]: band.get("observed_at") for band in item["base_bands"]}
        totals_by_source = {total["source"]: total for total in item["line_totals"]}
        for index, band in enumerate(bands, start=1):
            # A band built from listings seen on a marketplace is what sellers asked on a date, not a
            # published rate card, and it is labelled - and kept apart - as that.
            if band["source"] == "marketplace.observed":
                rate = {"origin": "marketplace_observed",
                        "description": f"observed marketplace listings ({observed_on.get(band['source'])}), "
                                       f"reading {index} of {len(bands)}; readings are never merged"}
            else:
                rate = {"origin": "rule_library",
                        "description": f"published asking range, reading {index} of {len(bands)}; "
                                       "readings are never merged"}
            rows.append({
                **common,
                "row_id": f"D-{item['deliverable_id']}-{index}",
                "rate_basis": rate,
                "already_counted_in": [] if index == 1 else [first],
                "missing": ([] if settled else waiting) + unpriced,
                "amount": None if not settled else {
                    "low": totals_by_source[band["source"]]["low"],
                    "high": totals_by_source[band["source"]]["high"],
                    "currency": "USD"},
            })
    return rows


def price(request: dict, framework: dict) -> dict:
    account = request["account_facts"]
    assessed_at = date.fromisoformat(request["assessed_at"])
    compiled_at = date.fromisoformat(framework["compiled_at"])
    age_days = (assessed_at - compiled_at).days

    data_gaps: list[dict] = []
    line_items: list[dict] = []
    # Span of the first line before any contract term is applied, carrying only the format
    # adjustment. That is the like-for-like counterpart of what the reach route computes.
    comparable_span: tuple[float, float | None] | None = None

    for deliverable in request["brief"]["deliverables"]:
        levers, unpriced = select_levers(framework, request, deliverable)
        low_pct, high_pct, open_ended = aggregate_levers(levers)

        if deliverable["platform"] != account["platform"]:
            base_bands: list[dict] = []
            data_gaps.append(
                {
                    "code": "platform_mismatch",
                    "field": f"followers on {deliverable['platform']}",
                    "effect": (
                        f"deliverable {deliverable['id']} is on {deliverable['platform']} but the "
                        f"account evidence covers {account['platform']}, so no band could be placed "
                        "for that line"
                    ),
                }
            )
        else:
            base_bands = bands_for(framework, deliverable["platform"], account["followers"]["value"])

        observed_band = comparables_band(request, deliverable)

        priced_bands = base_bands + ([observed_band] if observed_band else [])
        adjusted = [apply_adjustment(band, low_pct, high_pct, open_ended) for band in priced_bands]

        if comparable_span is None and base_bands:
            format_low, format_high, format_open = aggregate_levers(
                [entry for entry in levers if entry["group"] == "format"]
            )
            comparable = [apply_adjustment(band, format_low, format_high, format_open)
                          for band in base_bands]
            low = min(band["low"] for band in comparable)
            highs = [band["high"] for band in comparable if band["high"] is not None]
            comparable_span = (low, max(highs) if highs else None)

        line_items.append(
            {
                "deliverable_id": deliverable["id"],
                "platform": deliverable["platform"],
                "format": deliverable["format"],
                "quantity": deliverable["quantity"],
                "base_bands": [
                    {
                        "source": band["source"],
                        "source_class": next(s["class"] for s in framework["sources"] if s["id"] == band["source"]),
                        "tier": band["tier"],
                        "followers_min": band["followers_min"],
                        "followers_max": band["followers_max"],
                        "low": band["low"],
                        "high": band["high"],
                        "open_ended": band["open_ended"],
                        "sample_size": None,
                        "observed_at": None,
                    }
                    for band in base_bands
                ] + ([observed_band] if observed_band else []),
                "applied_levers": [
                    {
                        "id": entry["id"],
                        "kind": entry["kind"],
                        "condition": entry["condition"],
                        "low_pct": entry.get("low_pct"),
                        "high_pct": entry.get("high_pct"),
                        "source": entry["source"],
                        "variant": entry.get("variant"),
                        "note": entry.get("note"),
                        "i18n": entry.get("i18n"),
                    }
                    for entry in levers
                ],
                "adjusted_bands": adjusted,
                "line_totals": [line_total(band, deliverable["quantity"]) for band in adjusted],
                "unpriced_terms": unpriced,
            }
        )

    # A package total is only honest for a source that priced every line. A source missing one
    # line would otherwise produce a total that silently values that line at zero.
    sources_per_line = [{band["source"] for band in item["adjusted_bands"]} for item in line_items]
    complete_sources = set.intersection(*sources_per_line) if sources_per_line and all(sources_per_line) else set()
    package_totals = []
    for source in sorted(complete_sources):
        low = 0.0
        high: float | None = 0.0
        open_ended = False
        # The sum of the line totals the report prints, so the lines always add up to the package.
        for item in line_items:
            total = next(t for t in item["line_totals"] if t["source"] == source)
            low += total["low"]
            if total["high"] is None or high is None:
                high = None
            else:
                high += total["high"]
            open_ended = open_ended or total["open_ended"]
        package_totals.append(
            {"source": source, "low": round(low, 2), "high": None if high is None else round(high, 2),
             "open_ended": open_ended}
        )

    partial = {s for line in sources_per_line for s in line} - complete_sources
    for source in sorted(partial):
        data_gaps.append(
            {
                "code": "partial_benchmark_coverage",
                "field": f"benchmark coverage for {source}",
                "effect": "that source does not price every deliverable in this package, so it "
                          "contributes line ranges but no package total",
            }
        )

    observed = request.get("marketplace_comparables")
    if observed and len(observed.get("observations") or []) < MIN_COMPARABLES:
        data_gaps.append({
            "code": "insufficient_comparables",
            "field": "marketplace_comparables",
            "effect": (
                f"only {len(observed.get('observations') or [])} comparable listings were observed, "
                f"below the {MIN_COMPARABLES} needed for a distribution, so no observed band was built"
            ),
        })

    # A term the brief settles but no source prices would otherwise vanish from the report: the
    # ranges simply leave it out. Say so where the creator reads what could not be checked.
    for code, text in (("unpriced_licence", UNPRICED_LICENCE), ("unpriced_exclusivity", UNPRICED_EXCLUSIVITY)):
        if any(text in item["unpriced_terms"] for item in line_items):
            data_gaps.append({"code": code, "field": "brief.terms", "effect": text})

    if not account.get("engagement_rate_pct"):
        data_gaps.append({"code": "missing_engagement_rate", "field": "engagement_rate_pct",
                          "effect": "the high-engagement premium could not be decided either way"})
    if not account.get("average_views"):
        data_gaps.append({"code": "missing_average_views", "field": "average_views",
                          "effect": "the reach-based route could not be computed, so only the "
                                    "follower-tier route is available"})
    if account.get("audience_geography") in (None, "unknown"):
        data_gaps.append({"code": "unknown_audience_geography", "field": "audience_geography",
                          "effect": "the audience-location premium was left out rather than guessed"})

    terms = request["brief"]["terms"]
    rights_grant = usage_rights_grant(terms["usage_rights"])
    determinacy = (
        "terms_settled"
        if rights_grant != "unspecified" and terms["exclusivity"]["status"] == "specified"
        else "terms_incomplete"
    )

    open_terms = open_terms_for(request)
    reply, rules_applied = build_negotiation_reply(framework, request, determinacy, package_totals, open_terms)

    reach = build_reach_estimate(framework, request, comparable_span)

    platforms = {item["platform"] for item in line_items}
    transacted = [
        {
            "source": entry["source"],
            "source_class": "marketplace_transaction",
            "platform": entry["platform"],
            "metric": entry["metric"],
            "value": entry["value"],
            "tier_scope": entry["tier_scope"],
            "caveat": (
                "An average across every tier on that marketplace, whose population skews towards "
                "smaller creators and UGC work. It shows how far asking ranges sit from settled "
                "amounts; it is not the band for this account."
            ),
        }
        for entry in framework.get("transacted_reference", [])
        if entry["platform"] in platforms
    ]

    rows = income_rows_for(line_items, determinacy, open_terms, data_gaps)

    # Every public source the quote leans on, so a reader can open the page a figure came from.
    cited = (
        [band["source"] for item in line_items for band in item["base_bands"]]
        + [entry["source"] for item in line_items for entry in item["applied_levers"]]
        + [entry["source"] for entry in transacted]
        + ([reach["source"]] if reach else [])
        + [sid for rule in rules_applied for sid in rule["sources"]]
    )
    by_id = {entry["id"]: entry for entry in framework["sources"]}
    sources = []
    for sid in dict.fromkeys(cited):
        entry = by_id.get(sid)
        if entry is None or not entry.get("url"):
            continue  # observed listings and the internal framework have no public page
        sources.append({key: entry.get(key) for key in
                        ("id", "publisher", "url", "class", "verification", "published", "retrieved_at")})

    return {
        "quote_version": "brand-deal-quote.v1",
        "assessed_at": request["assessed_at"],
        "currency": "USD",
        "mode": request["mode"],
        "framework": {
            "id": framework["framework_id"],
            "version": framework["version"],
            "compiled_at": framework["compiled_at"],
            "age_days": age_days,
            "stale": age_days > framework["staleness_budget_days"],
        },
        "determinacy": determinacy,
        "usage_rights_grant": rights_grant,
        "line_items": line_items,
        "package_totals": package_totals,
        "transacted_reference": transacted,
        "reach_estimate": reach,
        "brand_ad_context": brand_ad_context(request),
        "open_terms": open_terms,
        "negotiation_reply": reply,
        "negotiation_rules_applied": rules_applied,
        "data_gaps": data_gaps,
        "income_rows": rows,
        "income_totals": [],
        "income_breakdown_requested": bool(request.get("income_breakdown")),
        "sources": sources,
        "boundaries": {
            "legal_review": "This prices a scope. It is not a legal review of the contract, and it does not approve any term.",
            "not_a_platform_payout": "Sponsorship fees and platform payouts have different units and different evidence. Nothing here estimates platform ad revenue.",
            "brand_budget_not_inferred": "Nothing here estimates what the brand can afford. Observable advertising activity is not a budget.",
        },
    }


def merge_account_facts(request: dict, derived: dict) -> dict:
    """Put the retrieved account facts into the request; a figure entered by hand for the same field goes.

    A count retyped from a rendering is what derive_account_facts.py replaces, so a derived field
    wins over the request's value for that field. Fields nothing derived (a figure the creator
    recalled, niche, audience geography) stay as the request has them.
    """
    if derived.get("schema_version") != "account-facts.v1":
        raise ValueError("--account-facts must be the output of derive_account_facts.py")
    facts = derived.get("account_facts") or {}
    account = dict(request.get("account_facts") or {})
    if account.get("platform") and facts.get("platform") and account["platform"] != facts["platform"]:
        raise ValueError(f"--account-facts were derived for {facts['platform']!r}, "
                         f"but the request is for {account['platform']!r}")
    def bare(handle: object) -> str:
        return str(handle or "").strip().lstrip("@").casefold()

    if account.get("handle") and facts.get("handle") and bare(account["handle"]) != bare(facts["handle"]):
        raise ValueError(f"--account-facts were derived for @{bare(facts['handle'])}, "
                         f"but the request is for @{bare(account['handle'])}")
    for key in ("platform", "handle", "followers", "engagement_rate_pct", "average_views"):
        if facts.get(key) is not None:
            account[key] = facts[key]
    return {**request, "account_facts": account}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", type=Path)
    parser.add_argument("--framework", type=Path, default=DEFAULT_FRAMEWORK,
                        help="pricing framework; a relative path is also looked up in the Skill directory")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--account-facts", type=Path,
                        help="derive_account_facts.py output; its fields replace the request's for the same field")
    args = parser.parse_args()

    request = load(args.request)
    if args.account_facts:
        request = merge_account_facts(request, load(args.account_facts))
    framework = load(skill_path(args.framework))
    quote = price(request, framework)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(quote, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(
        f"quote written: {args.output} "
        f"({len(quote['line_items'])} line item(s), determinacy={quote['determinacy']}, "
        f"{len(quote['open_terms'])} open term(s))"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
