#!/usr/bin/env python3
"""Income rows: one Skill's own part of a "what does my income add up to" answer.

The rows share one shape with Kabo's brand-deal pricing, which is not bundled in this plugin, so
the main agent can put platform payouts and brand deals side by side in one reply. The shape and
the three rules below hold for every row:

1. An eligibility bar is not income. A row only carries an amount its own Skill computed.
2. Rows whose rates rest on different kinds of evidence are never added into one total.
3. What a row still lacks travels with it: missing, assumed and unconfirmed never drop out.

Standard library only; it is imported by each Skill's validator and renderer.
"""
from __future__ import annotations

from typing import Any

ROW_FIELDS = {
    "row_id", "stream_kind", "stream", "unit", "volume_basis", "rate_basis", "period", "cost_basis",
    "already_counted_in", "missing", "amount", "after_tax", "source_ref",
}
TOTAL_FIELDS = {"origin", "period", "currency", "row_ids", "low", "high"}
STREAM_KINDS = {"platform_payout", "brand_deal", "ugc_production", "digital_product"}
VOLUME_SOURCES = {"retrieved", "user_supplied", "model_estimate", "rule_library", "unknown"}
# A rate read from the creator's own account (a subscription price, say) is none of the other
# three, and stronger than any of them, so it keeps its own name. So does a rate other creators
# report (not published by anyone who pays it) and a range read off observed marketplace listings
# (what sellers ask, on a date, not a published rate card).
ORIGINS = {"retrieved", "user_supplied", "model_estimate", "rule_library", "creator_reported",
           "marketplace_observed"}
PERIODS = {"monthly", "annual", "one_off"}
PLATFORM_CUTS = {"net_of_platform_share", "before_platform_share", "not_modelled"}
TAX_BASES = {"pre_tax", "after_tax", "not_modelled"}


def _text(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate_rows(rows: Any, totals: Any, allowed_kinds: set[str]) -> list[str]:
    errors: list[str] = []
    if not isinstance(rows, list):
        return ["income_rows must be an array"]
    if not isinstance(totals, list):
        return ["income_totals must be an array"]
    ids = [row.get("row_id") for row in rows if isinstance(row, dict)]
    if len(ids) != len(set(ids)):
        errors.append("income_rows has a repeated row_id")
    known = {row_id for row_id in ids if _text(row_id)}
    by_id = {row["row_id"]: row for row in rows if isinstance(row, dict) and _text(row.get("row_id"))}

    for index, row in enumerate(rows):
        where = f"income_rows[{index}]"
        if not isinstance(row, dict) or set(row) != ROW_FIELDS:
            errors.append(f"{where} must carry exactly {sorted(ROW_FIELDS)}")
            continue
        if not _text(row["row_id"]):
            errors.append(f"{where}.row_id must be a non-empty string")
        if row["stream_kind"] not in STREAM_KINDS or row["stream_kind"] not in allowed_kinds:
            errors.append(f"{where}.stream_kind {row['stream_kind']!r} is not one this Skill prices")
        for field in ("stream", "unit"):
            if not _text(row[field]):
                errors.append(f"{where}.{field} must be a non-empty string")
        if row["period"] not in PERIODS:
            errors.append(f"{where}.period must be one of {sorted(PERIODS)}")

        volume = row["volume_basis"]
        if not isinstance(volume, dict) or set(volume) != {"description", "source"} \
                or not _text(volume.get("description")) or volume.get("source") not in VOLUME_SOURCES:
            errors.append(f"{where}.volume_basis needs a description and a source from {sorted(VOLUME_SOURCES)}")
            volume = {"source": "unknown"}
        rate = row["rate_basis"]
        if not isinstance(rate, dict) or set(rate) != {"origin", "description"} \
                or not _text(rate.get("description")) or rate.get("origin") not in ORIGINS | {None}:
            errors.append(f"{where}.rate_basis needs a description and an origin from {sorted(ORIGINS)} or null")
            rate = {"origin": None}
        cost = row["cost_basis"]
        if not isinstance(cost, dict) or set(cost) != {"platform_cut", "tax"} \
                or cost.get("platform_cut") not in PLATFORM_CUTS or cost.get("tax") not in TAX_BASES:
            errors.append(f"{where}.cost_basis must state platform_cut and tax, even if only as not_modelled")

        missing = row["missing"]
        if not isinstance(missing, list) or not all(_text(item) for item in missing) \
                or len(missing) != len(set(missing)):
            errors.append(f"{where}.missing must be an array of distinct non-empty strings")
            missing = ["?"]
        counted = row["already_counted_in"]
        if not isinstance(counted, list) or any(ref not in known or ref == row["row_id"] for ref in counted):
            errors.append(f"{where}.already_counted_in must name other rows of this output")
            row = {**row, "already_counted_in": []}
            by_id[row["row_id"]] = row

        amount = row["amount"]
        if amount is None:
            # No figure is a legitimate row, but only if it says why.
            if not missing:
                errors.append(f"{where} has no amount, so missing must say what would produce one")
        else:
            if not isinstance(amount, dict) or set(amount) != {"low", "high", "currency"} \
                    or not _number(amount.get("low")) or amount["low"] < 0 or not _text(amount.get("currency")) \
                    or not (amount.get("high") is None or (_number(amount["high"]) and amount["high"] >= amount["low"])):
                errors.append(f"{where}.amount must be a range with 0 <= low <= high (high may be null) and a currency")
            if rate.get("origin") is None:
                errors.append(f"{where} carries an amount with no rate origin; without a rate it can only give the path")
            if volume.get("source") == "unknown":
                errors.append(f"{where} carries an amount although its volume is unknown")
        if volume.get("source") == "unknown" and not missing:
            errors.append(f"{where} has an unknown volume, so missing must say so")
        if row["stream_kind"] == "digital_product" and amount is not None:
            errors.append(f"{where}: no Skill prices digital-product income, so this row carries no amount")

        after = row["after_tax"]
        if after is not None:
            if amount is None or not isinstance(amount, dict) \
                    or set(amount) != {"low", "high", "currency"} \
                    or not _number(amount.get("low")) or not _number(amount.get("high")):
                errors.append(f"{where}.after_tax needs a closed pre-tax amount beside it")
            elif not isinstance(after, dict) or set(after) != {"low", "high", "rate_percent"} \
                    or not all(_number(after.get(k)) for k in ("low", "high", "rate_percent")) \
                    or not 0 <= after["rate_percent"] <= 100:
                errors.append(f"{where}.after_tax must carry low, high and a rate between 0 and 100")
            else:
                keep = 1 - after["rate_percent"] / 100
                # Both ends were rounded to cents independently, so allow one cent of drift.
                for end in ("low", "high"):
                    if abs(after[end] - round(amount[end] * keep, 2)) > 0.01 + 1e-9:
                        errors.append(f"{where}.after_tax.{end} is not the pre-tax {end} at the stated rate")
        if row["source_ref"] is not None and not _text(row["source_ref"]):
            errors.append(f"{where}.source_ref must be null or a non-empty string")

    # A row inside a row that is inside it is a loop: no reading of it says which one to count.
    inside = {row_id: list(row["already_counted_in"]) for row_id, row in by_id.items()
              if isinstance(row.get("already_counted_in"), list)}
    for start in sorted(inside):
        seen: set[str] = set()
        stack = list(inside[start])
        while stack:
            node = stack.pop()
            if node == start:
                errors.append(f"income_rows: {start} is inside a row that is inside it (already_counted_in loops)")
                break
            if node not in seen:
                seen.add(node)
                stack.extend(inside.get(node, []))

    for index, total in enumerate(totals):
        where = f"income_totals[{index}]"
        if not isinstance(total, dict) or set(total) != TOTAL_FIELDS:
            errors.append(f"{where} must carry exactly {sorted(TOTAL_FIELDS)}")
            continue
        members = total["row_ids"]
        if not isinstance(members, list) or not members or any(ref not in by_id for ref in members):
            errors.append(f"{where}.row_ids must name rows of this output")
            continue
        if len(members) != len(set(members)):
            errors.append(f"{where}.row_ids repeats a row, which would count it twice")
            continue
        picked = [by_id[ref] for ref in members]
        if any((row.get("rate_basis") or {}).get("origin") != total["origin"] for row in picked) \
                or total["origin"] not in ORIGINS:
            errors.append(f"{where} adds rows whose rates rest on different kinds of evidence")
        if any(row.get("amount") is None for row in picked):
            errors.append(f"{where} adds a row that has no amount")
            continue
        if any(row["period"] != total["period"] or row["amount"].get("currency") != total["currency"] for row in picked):
            errors.append(f"{where} adds rows with a different period or currency")
        if any(ref in members for row in picked for ref in row.get("already_counted_in") or []):
            errors.append(f"{where} adds a row to the row that already contains it")
        low = round(sum(row["amount"]["low"] for row in picked), 2)
        highs = [row["amount"]["high"] for row in picked]
        high = None if any(h is None for h in highs) else round(sum(highs), 2)
        if not _number(total["low"]) or abs(total["low"] - low) > 0.01 \
                or (high is None) != (total["high"] is None) \
                or (high is not None and (not _number(total["high"]) or abs(total["high"] - high) > 0.01)):
            errors.append(f"{where} is not the sum of the rows it names")
    return errors


# The labels this table adds around the row's own values. One set, in English, because a table
# that ships byte-identical in two Skills cannot carry one Skill's idea of which languages exist.
# The main agent restates them when it presents the breakdown.
LABELS = {
    "head": ["Income", "Unit", "Volume", "Rate (evidence)", "Period", "What it is net of",
             "Already inside", "Still missing", "Amount"],
    "origin": {"retrieved": "from your account", "user_supplied": "you gave it",
               "model_estimate": "modelled estimate", "rule_library": "published source",
               "creator_reported": "what other creators report",
               "marketplace_observed": "observed marketplace listings",
               None: "no rate - path only"},
    "period": {"monthly": "per month", "annual": "per year", "one_off": "one-off"},
    "cut": {"net_of_platform_share": "after platform share",
            "before_platform_share": "before platform share",
            "not_modelled": "platform cut not modelled"},
    "tax": {"pre_tax": "pre-tax", "after_tax": "after tax", "not_modelled": "tax not modelled"},
    "after": "after tax {span} at {rate}%",
    "inside": "{names} - do not add",
    "none": "-",
    "total": "Total of {names} ({origin}, {period}): {span}",
    "rules": ("Rows whose rates rest on different kinds of evidence are not added together; a row "
              "marked as already inside another is not counted twice; a platform's eligibility "
              "bar is not income."),
}


def _cell(value: str) -> str:
    return " ".join(str(value).replace("|", "/").split())


def _money(value: float) -> str:
    # Small or fractional amounts keep their cents: 0.4 rounded to "0" or 7.5 to "8" misstates them.
    return f"{value:,.2f}" if value < 100 or not float(value).is_integer() else f"{value:,.0f}"


def _span(low: float, high: float | None, currency: str) -> str:
    if high is None:
        return f"{currency} {_money(low)}+"
    if low == high:
        return f"{currency} {_money(low)}"
    return f"{currency} {_money(low)}-{_money(high)}"


def render_rows(rows: list[dict[str, Any]], totals: list[dict[str, Any]],
                names: dict[str, str] | None = None) -> list[str]:
    """The rows as a Creator-facing table.

    The JSON is English and so is the table. There used to be a `localize` hook, and the one
    Skill that passed it translated by looking the plan's own English sentences up in a
    dictionary — so changing a word in the producer silently dropped the whole table back to
    English with no test failing. Restating a finished table is the main agent's job, and it does
    not need a lookup to do it.
    """
    words = LABELS
    names = names or {}
    title = {row["row_id"]: names.get(row["row_id"], row["stream"]) for row in rows}
    sep = ", "
    lines = ["| " + " | ".join(words["head"]) + " |", "|" + "---|" * len(words["head"])]
    for row in rows:
        amount = row["amount"]
        figure = words["none"] if amount is None else _span(amount["low"], amount["high"], amount["currency"])
        if amount is not None and row["after_tax"]:
            after = row["after_tax"]
            figure += "; " + words["after"].format(span=_span(after["low"], after["high"], amount["currency"]),
                                                   rate=f"{after['rate_percent']:g}")
        inside = words["none"] if not row["already_counted_in"] else words["inside"].format(
            names=sep.join(title.get(ref, ref) for ref in row["already_counted_in"]))
        cells = [
            title[row["row_id"]], row["unit"], row["volume_basis"]["description"],
            f"{row['rate_basis']['description']} ({words['origin'][row['rate_basis']['origin']]})",
            words["period"][row["period"]],
            f"{words['cut'][row['cost_basis']['platform_cut']]}{sep}{words['tax'][row['cost_basis']['tax']]}",
            inside, sep.join(row["missing"]) or words["none"], figure,
        ]
        lines.append("| " + " | ".join(_cell(cell) for cell in cells) + " |")
    lines.append("")
    for total in totals:
        lines.append(words["total"].format(
            names=sep.join(title.get(ref, ref) for ref in total["row_ids"]),
            origin=words["origin"][total["origin"]], period=words["period"][total["period"]],
            span=_span(total["low"], total["high"], total["currency"])))
    if totals:
        lines.append("")
    lines.append(words["rules"])
    return lines
