#!/usr/bin/env python3
"""Dependency-free validator for the brand-deal pricing framework.

The framework is the one part of this Skill that cannot be rebuilt from anything else in the
package, so it gets its own gate instead of being checked only where it is consumed.

Four of the checks here are not shape checks and are worth naming, because each one blocks a
specific way a pricing library goes wrong:

  * An internal framework may not back a market band. Trade conventions can come from an internal
    document - somebody has to write down that usage rights are charged as a percentage - but a
    claim about what the market pays has to come from something that measured the market.
  * A lever with no source carries no numbers. The trade agrees revision rounds matter and nobody
    publishes a price for one; that lever exists to raise the term, not to multiply a fee.
  * Platform is not a lever. Per-platform base bands already contain the platform difference, and a
    platform lever on top of them would charge for the same difference twice.
  * At least one source must tile the whole follower range for each platform it covers. Otherwise
    an account lands in a gap and the run silently returns no band at all.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import date
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent.parent
DEFAULT_FRAMEWORK = SKILL_DIR / "references" / "frameworks" / "creator-brand-deal-pricing.v1.json"

TOP_LEVEL_REQUIRED = {
    "schema_version", "framework_id", "version", "compiled_at", "currency",
    "staleness_budget_days", "cpm_divergence_threshold", "sources", "posted_rate_bands",
    "levers", "negotiation_rules",
}
TOP_LEVEL_OPTIONAL = {"transacted_reference", "cpm_model", "divergences", "corroborations"}

SOURCE_CLASSES = {
    "industry_benchmark", "marketplace_transaction", "marketplace_listing",
    "convention", "reach_model", "internal_framework",
}
VERIFICATIONS = {"page_fetch", "search_snippet", "repository_read"}
PLATFORMS = {"instagram", "tiktok", "youtube"}
TIERS = {"nano", "micro", "mid", "macro", "mega"}
LEVER_KINDS = {
    "usage_rights", "exclusivity", "rush", "format", "engagement", "niche", "geography", "revisions",
}
# Classes allowed to back a claim about what the market pays.
MARKET_CLASSES = {"industry_benchmark", "marketplace_listing"}

SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def fail(problems: list[str], message: str) -> None:
    problems.append(message)


def check_dates(value: Any, label: str, problems: list[str]) -> None:
    if not isinstance(value, str) or not ISO_DATE.match(value):
        fail(problems, f"{label} must be an ISO date, got {value!r}")
        return
    try:
        date.fromisoformat(value)
    except ValueError:
        fail(problems, f"{label} is not a real date: {value!r}")


def validate_sources(framework: dict, problems: list[str]) -> dict[str, dict]:
    sources: dict[str, dict] = {}
    for entry in framework.get("sources", []):
        sid = entry.get("id")
        if not isinstance(sid, str) or not sid:
            fail(problems, "a source is missing its id")
            continue
        if sid in sources:
            fail(problems, f"duplicate source id {sid!r}")
        cls = entry.get("class")
        if cls not in SOURCE_CLASSES:
            fail(problems, f"source {sid!r} has unknown class {cls!r}")
        if entry.get("verification") not in VERIFICATIONS:
            fail(problems, f"source {sid!r} has unknown verification {entry.get('verification')!r}")
        check_dates(entry.get("retrieved_at"), f"source {sid!r} retrieved_at", problems)
        # A public figure a reader cannot trace back to its page is an assertion, not a source.
        if cls != "internal_framework":
            if not isinstance(entry.get("publisher"), str) or not entry["publisher"].strip():
                fail(problems, f"source {sid!r} is public but names no publisher")
            if not isinstance(entry.get("url"), str) or not entry["url"].startswith("https://"):
                fail(problems, f"source {sid!r} is public but has no https url a reader can check")
        if cls == "marketplace_transaction" and not entry.get("population_bias"):
            fail(
                problems,
                f"source {sid!r} reports transacted amounts but declares no population_bias; the "
                "bias has to travel with the number or the comparison misleads",
            )
        sources[sid] = entry
    return sources


def validate_bands(framework: dict, sources: dict[str, dict], problems: list[str]) -> None:
    seen: set[tuple] = set()
    by_platform_source: dict[tuple[str, str], list[dict]] = {}

    for band in framework.get("posted_rate_bands", []):
        src = band.get("source")
        platform = band.get("platform")
        label = f"band {src!r}/{platform!r}/{band.get('tier')!r}"
        if src not in sources:
            fail(problems, f"{label} references unknown source")
            continue
        cls = sources[src].get("class")
        if cls not in MARKET_CLASSES:
            fail(
                problems,
                f"{label} is backed by a {cls!r} source; a claim about what the market pays needs a "
                "source that measured the market",
            )
        if platform not in PLATFORMS:
            fail(problems, f"{label} has unknown platform")
            continue
        if band.get("tier") not in TIERS:
            fail(problems, f"{label} has unknown tier")

        key = (src, platform, band.get("followers_min"))
        if key in seen:
            fail(problems, f"{label} duplicates an existing band for the same follower floor")
        seen.add(key)

        fmin, fmax = band.get("followers_min"), band.get("followers_max")
        if not isinstance(fmin, int) or fmin < 0:
            fail(problems, f"{label} has a bad followers_min")
        if fmax is not None and (not isinstance(fmax, int) or fmax <= (fmin or 0)):
            fail(problems, f"{label} has followers_max at or below followers_min")

        low, high, open_ended = band.get("low"), band.get("high"), band.get("open_ended")
        if not isinstance(low, (int, float)) or low < 0:
            fail(problems, f"{label} has a bad low")
        if high is None:
            if open_ended is not True:
                fail(problems, f"{label} has no upper bound but is not marked open_ended")
        elif not isinstance(high, (int, float)) or high < (low or 0):
            fail(problems, f"{label} has high below low")
        if not isinstance(open_ended, bool):
            fail(problems, f"{label} is missing open_ended")

        by_platform_source.setdefault((platform, src), []).append(band)

    # Every platform that appears at all needs one source tiling it end to end.
    platforms_present = {p for (p, _s) in by_platform_source}
    for platform in sorted(platforms_present):
        tiled = False
        for (p, src), bands in by_platform_source.items():
            if p != platform:
                continue
            ordered = sorted(bands, key=lambda b: b["followers_min"])
            cursor = ordered[0]["followers_min"]
            contiguous = True
            for band in ordered:
                if band["followers_min"] != cursor:
                    contiguous = False
                    break
                if band["followers_max"] is None:
                    cursor = None
                    break
                cursor = band["followers_max"]
            if contiguous and cursor is None:
                tiled = True
                break
        if not tiled:
            fail(
                problems,
                f"no single source tiles {platform} from its floor to open-ended; some follower "
                "counts would return no band at all",
            )


def validate_levers(framework: dict, sources: dict[str, dict], problems: list[str]) -> None:
    seen_ids: set[str] = set()
    kind_by_group: dict[str, str] = {}
    for lever in framework.get("levers", []):
        lid = lever.get("id")
        if not isinstance(lid, str) or not lid:
            fail(problems, "a lever is missing its id")
            continue
        if lid in seen_ids:
            fail(problems, f"duplicate lever id {lid!r}")
        seen_ids.add(lid)

        kind = lever.get("kind")
        if kind not in LEVER_KINDS:
            fail(problems, f"lever {lid!r} has unknown kind {kind!r}")
        if kind == "platform":  # unreachable via the enum, kept as a named guard
            fail(problems, f"lever {lid!r} prices the platform, which the base bands already carry")

        group = lever.get("group")
        if not isinstance(group, str) or not group:
            fail(problems, f"lever {lid!r} declares no group, so nothing decides whether it widens "
                           "an existing adjustment or adds a new one")
        elif kind_by_group.setdefault(group, kind) != kind:
            fail(
                problems,
                f"lever {lid!r} joins group {group!r}, which prices {kind_by_group[group]!r}; a group "
                "has to be one term, or widening it silently mixes two different charges",
            )

        if not lever.get("condition"):
            fail(problems, f"lever {lid!r} has no condition, so nothing can decide when it applies")

        unsourced = bool(lever.get("unsourced"))
        low, high = lever.get("low_pct"), lever.get("high_pct")
        if unsourced:
            if lever.get("source") is not None or low is not None or high is not None:
                fail(
                    problems,
                    f"lever {lid!r} is marked unsourced but still carries a source or a number; an "
                    "unsourced lever raises the term and prices nothing",
                )
            continue

        src = lever.get("source")
        if src not in sources:
            fail(problems, f"lever {lid!r} references unknown source {src!r}")
        if low is None:
            fail(problems, f"lever {lid!r} has no low_pct and is not marked unsourced")
        if high is None and not lever.get("open_ended"):
            fail(problems, f"lever {lid!r} has no high_pct but is not marked open_ended")
        if isinstance(low, (int, float)) and isinstance(high, (int, float)) and high < low:
            fail(problems, f"lever {lid!r} has high_pct below low_pct")


def validate_cpm(framework: dict, sources: dict[str, dict], problems: list[str]) -> None:
    model = framework.get("cpm_model")
    if model is None:
        return
    if model.get("source") not in sources:
        fail(problems, "cpm_model references an unknown source")
    if model.get("basis") != "average_views":
        fail(problems, "cpm_model must price off average views; follower count is the other route")
    niches = model.get("niches") or []
    if not niches:
        fail(problems, "cpm_model carries no niches, so it can never produce a figure")
    seen = set()
    for niche in niches:
        name = niche.get("niche")
        if name in seen:
            fail(problems, f"cpm_model repeats niche {name!r}")
        seen.add(name)
        if niche.get("cpm_high", 0) < niche.get("cpm_low", 0):
            fail(problems, f"cpm_model niche {name!r} has high below low")
    for mult in model.get("format_multipliers") or []:
        if mult.get("high", 0) < mult.get("low", 0):
            fail(problems, f"cpm_model format {mult.get('format')!r} has high below low")


def validate_rules_and_divergences(framework: dict, sources: dict[str, dict], problems: list[str]) -> None:
    for rule in framework.get("negotiation_rules", []):
        rid = rule.get("id")
        cited = rule.get("sources") or []
        if not cited:
            fail(problems, f"negotiation rule {rid!r} cites no source")
        for src in cited:
            if src not in sources:
                fail(problems, f"negotiation rule {rid!r} cites unknown source {src!r}")

    for divergence in framework.get("divergences", []):
        topic = divergence.get("topic")
        if divergence.get("decision") not in {"external", "internal", "unresolved"}:
            fail(problems, f"divergence {topic!r} has an unknown decision")
        check_dates(divergence.get("decided_on"), f"divergence {topic!r} decided_on", problems)
        if not divergence.get("rationale"):
            fail(problems, f"divergence {topic!r} records no rationale")
        for src in divergence.get("adopted_sources") or []:
            if src not in sources:
                fail(problems, f"divergence {topic!r} cites unknown source {src!r}")

    for item in framework.get("corroborations", []):
        for src in item.get("evidence_sources") or []:
            if src not in sources:
                fail(problems, f"corroboration {item.get('topic')!r} cites unknown source {src!r}")


def validate(framework: dict) -> list[str]:
    problems: list[str] = []

    if framework.get("schema_version") != "pricing-framework.v1":
        fail(problems, f"unexpected schema_version {framework.get('schema_version')!r}")
    keys = set(framework)
    missing = TOP_LEVEL_REQUIRED - keys
    if missing:
        fail(problems, f"missing top-level fields: {sorted(missing)}")
    extra = keys - TOP_LEVEL_REQUIRED - TOP_LEVEL_OPTIONAL
    if extra:
        fail(problems, f"unexpected top-level fields: {sorted(extra)}")

    if not SEMVER.match(str(framework.get("version", ""))):
        fail(problems, f"version must be x.y.z, got {framework.get('version')!r}")
    check_dates(framework.get("compiled_at"), "compiled_at", problems)
    if framework.get("currency") != "USD":
        fail(problems, "this framework version prices in USD only")
    if not isinstance(framework.get("staleness_budget_days"), int) or framework.get("staleness_budget_days", 0) < 1:
        fail(problems, "staleness_budget_days must be a positive integer")
    threshold = framework.get("cpm_divergence_threshold")
    if not isinstance(threshold, (int, float)) or threshold < 1:
        fail(problems, "cpm_divergence_threshold must be at least 1")

    sources = validate_sources(framework, problems)
    validate_bands(framework, sources, problems)
    validate_levers(framework, sources, problems)
    validate_cpm(framework, sources, problems)
    validate_rules_and_divergences(framework, sources, problems)

    for entry in framework.get("transacted_reference", []):
        src = entry.get("source")
        if src not in sources:
            fail(problems, f"transacted_reference cites unknown source {src!r}")
        elif sources[src].get("class") != "marketplace_transaction":
            fail(
                problems,
                f"transacted_reference cites {src!r}, which is not a transaction source; an asking "
                "price presented as a settled amount is the confusion this section exists to avoid",
            )

    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("framework", type=Path, nargs="?", default=DEFAULT_FRAMEWORK,
                        help="a relative path is also looked up in the Skill directory")
    args = parser.parse_args()

    path = args.framework
    # Same rule as price_brand_deal.skill_path: a packaged path is read from the Skill directory.
    if not path.is_absolute() and (SKILL_DIR / path).exists():
        path = SKILL_DIR / path
    framework = json.loads(path.read_text(encoding="utf-8"))
    problems = validate(framework)
    if problems:
        print(f"pricing framework FAILED ({len(problems)} problem(s)):")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    bands = len(framework.get("posted_rate_bands", []))
    levers = len(framework.get("levers", []))
    print(
        f"pricing framework OK: {framework['framework_id']}@{framework['version']} "
        f"({len(framework['sources'])} sources, {bands} bands, {levers} levers)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
