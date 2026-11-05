#!/usr/bin/env python3
"""Render a BrandDealQuote as the Creator-facing report.

Deterministic: the same quote renders the same report every time, and nothing is added here that
the quote does not already assert.

Every published figure links to the page it came from, and a Sources section says who published
each one, when, and whether it was read from the full page or a search summary - a creator has to be
able to check a number before quoting it. Figures from the internal framework say so instead of
borrowing a link. Run mechanics never appear: no file names, no framework internals, no validation
status.
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

# Fixed labels are English for every delivery language. Free text the quote already carries (open
# terms, reply draft, caveats, boundaries) is printed as the quote states it.
T = {
    "title": "What this deal is worth",
    "intro_incomplete": "The brief leaves the terms that decide most of the price unsettled, so the ranges below price the content only. Settle those terms first, then quote.",
    "intro_settled": "The terms are settled, so these ranges cover the whole scope rather than the content alone.",
    "budget_line": "The brand mentioned {amount} for {basis}.",
    "basis_total": "the whole package",
    "basis_each": "each deliverable",
    "bands_heading": "Published asking ranges",
    "bands_note": "Two independent compilations of published creator rates. They disagree, sometimes by several times over, because they draw the follower tiers in different places and survey different populations. Both are shown; averaging them would invent a precision neither has.",
    "bands_note_single": "Only one of the compilations publishes a band for an account this size on this platform, so there is one reading below rather than two. A single reading is not a firmer number - it is a thinner one.",
    "line_label": "{quantity} x {format} on {platform}",
    "reading_label": "Reading {index}",
    "reading_meta": "{cls}, tier {tier}, {followers} followers",
    "observed_meta": "{cls}, {n} listings seen on {date}, {followers} followers",
    "per_item": "per item",
    "line_total": "for {quantity}",
    "package_heading": "Whole package",
    "package_note": "Each reading adds up the lines above: every line's per-item range times its quantity, summed before rounding to whole dollars.",
    "levers_heading": "What moves it",
    "lever_row": "{condition}: {pct}",
    "transacted_heading": "What brands actually paid",
    "transacted_note": "Asking ranges and settled amounts are different populations. These are averages of money that changed hands on one marketplace:",
    "transacted_row": "{platform}: {value} average",
    "reach_heading": "A second reading, from your views",
    "reach_below": "Priced on delivered views rather than follower count, this scope comes out around {low}-{high} - about {ratio}x below the follower-tier reading. Follower count is flattering your reach here, and a brand that checks views will notice.",
    "reach_above": "Priced on delivered views rather than follower count, this scope comes out around {low}-{high} - about {ratio}x above the follower-tier reading. Your reach outruns your follower count, and quoting off followers alone leaves money behind.",
    "brand_ads_heading": "What this brand is advertising",
    "brand_ads_facts": "{ads} active ad(s) across {countries} market(s){months}.",
    "brand_ads_facts_no_markets": "{ads} active ad(s){months}. How many markets they run in was not reported.",
    "brand_ads_months": ", running for about {n} month(s)",
    "brand_ads_none": "No advertising was visible for this brand.",
    "brand_ads_unmatched": "Nothing matched that brand name, which can be a spelling difference rather than a quiet advertiser.",
    "brand_ads_price_note": "This changes how you talk to them, not what the work is worth.",
    "caveat_single_ecosystem_only": "Only one advertising ecosystem is visible here. A brand running its campaigns elsewhere looks exactly like a brand running none.",
    "caveat_not_a_creator_budget": "Advertising spend and creator budget are different lines. A brand that advertises heavily can still hold a small creator budget.",
    "caveat_count_is_a_floor": "The ad list did not finish loading, so the count is a floor rather than a total.",
    "open_heading": "What the brief has not settled",
    "open_row": "**{term}** - {why}",
    "ask_label": "Ask:",
    "reply_heading": "A reply you can send",
    "no_number_note": "Notice there is no figure in it. That is the point: whoever names one first sets the anchor, and the terms above are most of the price.",
    "opening_ask_line": "When those are answered, open at {band} rather than at your floor, so there is somewhere to move.",
    "gaps_heading": "What could not be checked",
    "boundaries_heading": "Limits",
    "impact_large": "large effect on price",
    "impact_moderate": "moderate effect on price",
    "impact_unpriced": "no published price, settle it in writing",
    "cls_industry_benchmark": "compiled industry rates",
    "cls_marketplace_listing": "live marketplace listings",
    "stale_note": "These rate compilations are past their refresh window, so treat the levers as current and the figures as dated.",
    "plus": " and up",
    "rights_none": "The brief grants the brand no reuse, no paid use and no buyout, so nothing below is priced for a licence. If the brand later wants to use the content, that licence is a separate quote.",
    "internal_rule": "internal rule of thumb, not published data",
    "sources_heading": "Sources",
    "sources_note": "Each published figure above links to the page it came from, and the reply draft follows the negotiation guides listed here. Figures marked as an internal rule of thumb are not published data.",
    "source_row": "[{publisher}]({url}) · published {published} · retrieved {retrieved} · {how}",
    "verif_page_fetch": "read from the full page",
    "verif_search_snippet": "taken from a search summary, not yet checked line by line against the page",
}

TERM_NAMES = {
    "usage_rights": "Usage rights",
    "exclusivity": "Exclusivity",
    "campaign_term": "Campaign dates",
    "geography": "Markets",
    "revisions": "Revision rounds",
    "rush": "Deadline",
}

FORMAT_NAMES = {
    "short_video": "short video", "long_video": "long video", "dedicated_video": "dedicated video",
    "integration": "integration", "static_post": "feed post", "story_frame": "story frame",
    "carousel": "carousel",
}

PLATFORM_NAMES = {"instagram": "Instagram", "tiktok": "TikTok", "youtube": "YouTube"}


def money(value: float | None, plus: str, open_ended: bool = False) -> str:
    if value is None:
        return "—"
    # Always whole dollars. These are negotiating ranges, not invoices; cents in $156,928.98 are false precision.
    return f"${round(value):,}" + (plus if open_ended else "")


def band_text(band: dict, plus: str) -> str:
    low = money(band["low"], plus)
    if band["high"] is None:
        return f"{low}{plus}"
    return f"{low}–{money(band['high'], plus, band.get('open_ended', False))}"


def pct_text(low: float | None, high: float | None) -> str:
    if low is None:
        return "—"
    if high is None or high == low:
        return f"+{low:g}%"
    return f"+{low:g}% – +{high:g}%"


INCOME_HEADING = "The brand-deal part of your income breakdown"


def source_link(quote: dict, source: str | None) -> str:
    """Link a figure to its page, say it is an internal rule, or say nothing for observed listings."""
    for entry in quote.get("sources") or []:
        if entry["id"] == source:
            return f" · [{entry['publisher']}]({entry['url']})"
    if source and source.startswith("internal."):
        return f" · _{T['internal_rule']}_"
    return ""


def render(quote: dict) -> str:
    t = T
    terms = TERM_NAMES
    formats = FORMAT_NAMES
    plus = t["plus"]
    out: list[str] = [f"# {t['title']}", ""]

    incomplete = quote["determinacy"] == "terms_incomplete"
    out.append(t["intro_incomplete"] if incomplete else t["intro_settled"])
    out.append("")
    if quote.get("usage_rights_grant") == "none":
        out.append(t["rights_none"])
        out.append("")

    if quote["framework"].get("stale"):
        out.append(f"> {t['stale_note']}")
        out.append("")

    # Published asking ranges, one reading per source, never merged.
    out.append(f"## {t['bands_heading']}")
    out.append("")
    readings = {band["source"] for item in quote["line_items"] for band in item["base_bands"]}
    # Saying "both are shown" above a single reading reads as a missing row; say what is true instead.
    out.append(t["bands_note"] if len(readings) > 1 else t["bands_note_single"])
    out.append("")

    reading_index: dict[str, int] = {}
    for item in quote["line_items"]:
        label = t["line_label"].format(
            quantity=item["quantity"],
            format=formats.get(item["format"], item["format"]),
            platform=PLATFORM_NAMES.get(item["platform"], item["platform"]),
        )
        out.append(f"**{label}**")
        out.append("")
        base_by_source = {band["source"]: band for band in item["base_bands"]}
        totals_by_source = {total["source"]: total for total in item["line_totals"]}
        for band in item["adjusted_bands"]:
            base = base_by_source[band["source"]]
            idx = reading_index.setdefault(band["source"], len(reading_index) + 1)
            cls = t.get(f"cls_{base['source_class']}", base["source_class"])
            # A null upper bound means the tier has no ceiling, not a number still to fill in. Rendering it
            # as a range gives "1,000,000–1M+", which reads as both a range and an open interval.
            followers = (
                f"{base['followers_min']:,}+"
                if base["followers_max"] is None
                else f"{base['followers_min']:,}–{base['followers_max']:,}"
            )
            if base.get("sample_size"):
                meta = t["observed_meta"].format(
                    cls=cls, n=base["sample_size"], date=base.get("observed_at"),
                    followers=followers,
                )
            else:
                meta = t["reading_meta"].format(
                    cls=cls, tier=base["tier"], followers=followers
                )
            row = (
                f"- {t['reading_label'].format(index=idx)} · {meta}: "
                f"**{band_text(band, plus)}** {t['per_item']}"
            )
            # Several of one deliverable: print the line total, or the package reads as unexplained.
            if item["quantity"] > 1:
                row += (
                    f" · **{band_text(totals_by_source[band['source']], plus)}** "
                    f"{t['line_total'].format(quantity=item['quantity'])}"
                )
            out.append(row + source_link(quote, band["source"]))
        out.append("")

        if item["applied_levers"]:
            out.append(f"_{t['levers_heading']}_")
            for applied in item["applied_levers"]:
                condition = applied["condition"]
                out.append(
                    "  - "
                    + t["lever_row"].format(
                        condition=condition,
                        pct=pct_text(applied.get("low_pct"), applied.get("high_pct")),
                    )
                    + source_link(quote, applied.get("source"))
                )
            out.append("")

    if quote["package_totals"]:
        out.append(f"### {t['package_heading']}")
        out.append("")
        out.append(t["package_note"])
        out.append("")
        for total in quote["package_totals"]:
            idx = reading_index.get(total["source"], 0)
            out.append(f"- {t['reading_label'].format(index=idx)}: **{band_text(total, plus)}**")
        out.append("")

    if quote.get("transacted_reference"):
        out.append(f"## {t['transacted_heading']}")
        out.append("")
        out.append(t["transacted_note"])
        out.append("")
        seen = set()
        for entry in quote["transacted_reference"]:
            if entry["metric"] != "average_amount_paid_per_collaboration":
                continue
            if entry["platform"] in seen:
                continue
            seen.add(entry["platform"])
            out.append(
                "- "
                + t["transacted_row"].format(
                    platform=PLATFORM_NAMES.get(entry["platform"], entry["platform"]),
                    value=money(entry["value"], plus),
                )
                + source_link(quote, entry["source"])
            )
        out.append("")
        out.append(f"> {quote['transacted_reference'][0]['caveat']}")
        out.append("")

    reach = quote.get("reach_estimate")
    if reach:
        out.append(f"## {t['reach_heading']}")
        out.append("")
        key = "reach_below" if reach["interpretation"] == "reach_below_tier" else "reach_above"
        out.append(
            t[key].format(
                low=money(reach["low"], plus),
                high=money(reach["high"], plus),
                ratio=f"{reach['divergence_ratio']:g}",
            )
            + source_link(quote, reach.get("source"))
        )
        out.append("")

    brand_ads = quote.get("brand_ad_context")
    if brand_ads:
        out.append(f"## {t['brand_ads_heading']}")
        out.append("")
        if brand_ads["posture"] == "name_not_matched":
            out.append(t["brand_ads_unmatched"])
        elif brand_ads["active_ads"] == 0:
            out.append(t["brand_ads_none"])
        else:
            months = brand_ads.get("months_running")
            months_text = "" if not months else t["brand_ads_months"].format(n=f"{months:g}")
            countries = brand_ads.get("country_count", 0)
            # Active ads in zero markets can only mean the markets were not reported; ads run somewhere.
            key = "brand_ads_facts" if countries else "brand_ads_facts_no_markets"
            out.append(
                t[key].format(
                    ads=brand_ads["active_ads"], countries=countries, months=months_text
                )
            )
        out.append("")
        out.append(brand_ads["posture_note"])
        out.append("")
        out.append(f"_{t['brand_ads_price_note']}_")
        out.append("")
        for caveat in brand_ads["caveats"]:
            text = t.get(f"caveat_{caveat}")
            if text:
                out.append(f"> {text}")
        out.append("")

    if quote["open_terms"]:
        out.append(f"## {t['open_heading']}")
        out.append("")
        for term in quote["open_terms"]:
            out.append(t["open_row"].format(term=terms.get(term["term"], term["term"]), why=term["why_it_matters"]))
            out.append(f"  - {t['ask_label']} {term['question_to_ask']}")
            out.append(f"  - _{t['impact_' + term['price_impact']]}_")
        out.append("")

    reply = quote["negotiation_reply"]
    opening = reply["draft"]["opening"]
    closing = reply["draft"]["closing"]
    questions = reply["draft"]["questions"]

    out.append(f"## {t['reply_heading']}")
    out.append("")
    out.append("> " + opening)
    out.append(">")
    for question in questions:
        out.append("> - " + question)
    out.append(">")
    out.append("> " + closing)
    out.append("")
    if not reply["should_state_a_number"]:
        out.append(t["no_number_note"])
        out.append("")
    elif reply.get("opening_ask"):
        ask = reply["opening_ask"]
        out.append(
            t["opening_ask_line"].format(band=band_text(ask, plus))
        )
        out.append("")

    rows = quote.get("income_rows") or []
    if quote.get("income_breakdown_requested") and rows:
        lines_by_id = {item["deliverable_id"]: item for item in quote["line_items"]}
        names = {}
        for row in rows:
            item = lines_by_id.get(row["source_ref"])
            if item:
                names[row["row_id"]] = t["line_label"].format(
                    quantity=item["quantity"], format=formats.get(item["format"], item["format"]),
                    platform=PLATFORM_NAMES.get(item["platform"], item["platform"]))
        out.append(f"## {INCOME_HEADING}")
        out.append("")
        out.extend(income_rows.render_rows(rows, quote.get("income_totals") or [], names))
        out.append("")

    if quote["data_gaps"]:
        out.append(f"## {t['gaps_heading']}")
        out.append("")
        for gap in quote["data_gaps"]:
            out.append("- " + gap["effect"])
        out.append("")

    if quote.get("sources"):
        out.append(f"## {t['sources_heading']}")
        out.append("")
        out.append(t["sources_note"])
        out.append("")
        for entry in quote["sources"]:
            out.append("- " + t["source_row"].format(
                publisher=entry["publisher"], url=entry["url"],
                published=entry.get("published") or "—", retrieved=entry["retrieved_at"],
                how=t.get(f"verif_{entry['verification']}", entry["verification"]),
            ))
        out.append("")

    out.append(f"## {t['boundaries_heading']}")
    out.append("")
    for key in ("legal_review", "not_a_platform_payout", "brand_budget_not_inferred"):
        out.append("- " + quote["boundaries"][key])
    out.append("")

    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("quote", type=Path)
    # Optional and informational: any BCP-47 tag is accepted and the report labels stay English.
    parser.add_argument("--language")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    quote = json.loads(args.quote.read_text(encoding="utf-8"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(quote) + "\n", encoding="utf-8")
    print(f"report written: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
