#!/usr/bin/env python3
"""Render a validated MonetizationPlan as Creator-facing Markdown.

The order answers the three questions a creator actually arrives with, in the order they ask them:
what ways does this platform pay, am I qualified yet, and roughly how much. The first section is
the one that still has value when no account data was available at all - most creators do not know
these programmes exist, and that is the part of this report nothing else replaces.

Nothing about rules, runs, sources of data, validation or file mechanics belongs in this output.
A threshold nobody has confirmed is said to be unconfirmed, in plain words, not omitted.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
# Sibling imports must not leave a __pycache__ in the skill directory.
sys.dont_write_bytecode = True
import income_rows  # noqa: E402

PLATFORM = {"youtube": "YouTube", "instagram": "Instagram", "tiktok": "TikTok"}

# The labels this renderer adds around the plan's own content. One set, in English.
#
# There used to be two, keyed on whether `--language` was exactly "zh-CN". Everything else read as
# English anyway, and the income table went further: it translated by looking the plan's own
# English sentences up in a dictionary, so changing one word in `assess_monetization.py` made the
# Chinese report fall back to English with no test failing. The plan's content is localized where
# the rule library carries a translation (`localized()` below); these labels are the renderer's
# own, and the main agent restates them when it presents the report.
WORDS = {
    "title": "How {platform} can pay you",
    "map": "The ways this platform pays",
    "standing": "Where you stand",
    "unconfirmed": "Still to confirm",
    "scenarios": "Revenue scenarios",
    "assumptions": "What these numbers assume",
    "estimate_mark": "worked out to model this scenario, not a figure you gave",
    "no_scenarios": ("Putting a figure on these needs numbers that live only inside your own dashboard:"),
    "needed": "Supply this and the figure follows",
    "no_figure": "No amount can be put on these:",
    "eligible": "Qualified",
    "not_eligible": "Not there yet",
    "partial": "Partly confirmed",
    "undetermined": "Cannot tell",
    "met": "met",
    "missing": "short by",
    "closing": ("This compares your public numbers against each programme's published bar. It is not the platform's "
                "own decision - your account dashboard is where that is confirmed."),
    "any_of": "Meeting any one of these qualifies you:",
    "route": "Route",
    "region": "This programme is only open in some countries; check that yours is on the list.",
    "unverified": ("This programme's bar has not been checked against the platform's own page yet, so it is not being "
                   "used to judge your position."),
    "not_yet": "This one starts on {date}, so it is not judging you yet - but it is worth knowing it is coming.",
    "coming": "From {date} this bar rises. You have until then, so here is what it becomes:",
    "coming_option": "Path",
    "region_no": "This one is only open in some countries, and your account is not registered in one of them.",
    "region_unknown": ("Your account's registered country is unknown. Confirm it before checking whether this "
                       "programme is available there."),
    "stale": ("It has been too long since this bar was last checked against the platform. Rules change without the "
              "page changing, so it is not judging you until it has been re-read."),
    "note_reported": ("Two things to discount there: the rate is not published by the platform - it is what other "
                      "creators report earning - and it is multiplied by your public view count, while the platform "
                      "pays on qualified views, which are fewer. Read it as an upper order of magnitude, not as a "
                      "price you have been quoted."),
    "expired": "This bar has been superseded by a newer rule from the platform.",
    "self_reported": "on the figure you gave",
    "pre_tax": ("These figures are before tax. What reaches you depends on your own tax, which depends on where you "
                "file - give me your rate and the after-tax figure follows."),
    "after_tax": "after tax about {span} (at a {rate}% rate)",
    "niches": "Comparing niches",
    "income": "The platform-payout part of your income breakdown",
    "includes": "{outer} already contains the income from {inner}; do not add the two figures together.",
    "niche_handback": ("How crowded these niches are is a question about what is being made in them and how it is "
                       "performing, not a payout rule, so it needs its own trend research: {niches}."),
}


def compact(value: float | int | None) -> str:
    if value is None:
        return "-"
    value = float(value)
    for limit, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if abs(value) >= limit:
            trimmed = value / limit
            return f"{trimmed:.1f}".rstrip("0").rstrip(".") + suffix
    return f"{int(value):,}" if value.is_integer() else f"{value:,.2f}"


def in_language(translations: dict[str, Any] | None, language: str) -> dict[str, Any]:
    """The translation block for this tag: exact match first, then the base language.

    One resolver for every localized read in this file. A rule library carrying `zh-CN` should
    still answer `zh-Hans-CN`, and one carrying `pt` should answer `pt-BR`. When only the
    programme lookup did this and the reason, label, note and niche lookups matched the tag
    exactly, a regional tag produced a report with the programme names translated and everything
    around them in the source language — a worse document than either language on its own.
    """
    blocks = translations or {}
    exact = blocks.get(language)
    if exact:
        return exact
    base = str(language or "").split("-")[0].lower()
    if not base:
        return {}
    for tag, candidate in blocks.items():
        if candidate and str(tag).split("-")[0].lower() == base:
            return candidate
    return {}


def localized(program: dict[str, Any], field: str, language: str) -> Any:
    """Creator-facing prose in the requested language, falling back to the source text.

    A missing translation must degrade to English, never to an empty section: a creator reading a
    blank line learns nothing, while a creator reading the English one still learns the programme
    exists.
    """
    value = in_language(program.get("i18n"), language).get(field)
    return value if value else program[field]


def status_word(program: dict[str, Any], words: dict[str, str]) -> str:
    return {
        "eligible": words["eligible"],
        "not_eligible": words["not_eligible"],
        "partially_determined": words["partial"],
        "undetermined": words["undetermined"],
    }[program["eligibility"]]


def usable_paths(program: dict[str, Any]) -> list[dict[str, Any]]:
    return [p for p in program["paths"] if p["rule_status"] == "usable"]


def coming_paths(program: dict[str, Any]) -> list[dict[str, Any]]:
    return [p for p in program["paths"] if p["rule_status"] == "not_yet_effective"]


# Units that are really yes/no flags. "1 professional account - met (you have 1)" is arithmetic
# leaking into a sentence; these read as conditions, not counts.
FLAG_UNITS = {"professional account", "invitation"}


def requirement_line(req: dict[str, Any], words: dict[str, str]) -> str | None:
    label = req["unit"]
    threshold = compact(req["threshold"])
    outcome = req["outcome"]
    if outcome == "met":
        # A number the creator typed settles the arithmetic but was never retrieved. Rendering it
        # exactly like a fetched fact would be the same swap this Skill refuses everywhere else.
        mark = f", {words['self_reported']}" if req.get("basis") == "user_supplied" else ""
        if req["unit"] in FLAG_UNITS:
            return f"- {label} - {words['met']}{mark}"
        return (f"- {threshold} {label} - {words['met']} "
                f"(you have {compact(req['observed_value'])}{mark})")
    if outcome == "not_met":
        if req["unit"] in FLAG_UNITS:
            return f"- {label} - not yet"
        line = (f"- {threshold} {label} - {words['missing']} {compact(req['gap'])} "
                f"(you have {compact(req['observed_value'])})")
        projection = req.get("projection")
        if projection:
            line += (f". At {compact(projection['rate_value'])} a day that is about "
                     f"{int(projection['days_to_threshold'])} days")
        return line
    if outcome == "needs_authorization":
        return (f"- {threshold} {label} - this figure only exists inside your own dashboard, so it "
                f"cannot be checked from outside")
    if outcome == "indeterminate_rounding":
        return (f"- {threshold} {label} - your public figure sits right on this line and the "
                f"platform rounds it, so it cannot be resolved either way")
    if outcome == "estimated_only":
        # Two opposite situations. An overstating reading that clears the bar cannot prove you are
        # there; an understating one that falls short cannot prove you are not. Saying "cannot
        # confirm" for both would send a creator who is probably fine and one who is probably
        # short away with the same sentence.
        if req.get("bound") == "upper":
            return (f"- {threshold} {label} - your public figure is past this line, but it is "
                    f"counted more loosely than the bar itself, so it is not yet confirmation "
                    f"(you have {compact(req['observed_value'])})")
        if req.get("bound") == "lower":
            return (f"- {threshold} {label} - this only counts a bounded recent window and may "
                    f"undercount, so it is not proof you are short (the window shows "
                    f"{compact(req['observed_value'])})")
        return f"- {threshold} {label} - the public figure is counted differently from this bar"
    if outcome == "unavailable_metric":
        return f"- {threshold} {label} - this figure is not obtainable right now"
    if outcome == "not_assessable":
        # Rendering nothing here left a heading with an empty body, which reads as a bug rather
        # than as the real answer: there is no number to chase on this one.
        return ("- There is no public number to reach on this one - access is offered by the "
                "platform, so no follower count predicts it. Watch for an in-app invitation.")
    return None


def future_bar_lines(program: dict[str, Any], words: dict[str, str],
                     announce: bool) -> list[str]:
    """The bars this programme raises to later, with their thresholds and this creator's gap.

    A bar that has not started yet still belongs in front of the creator, and only while it has not
    started: the work it asks for takes months, so the window to act on it closes before it begins
    deciding anything. `announce` is False where the caller has already said when it starts.
    """
    coming = coming_paths(program)
    if not coming:
        return []
    lines: list[str] = []
    if announce:
        starts = sorted(p["effective_from"] for p in coming if p["effective_from"])
        lines += [words["coming"].format(date=starts[0] if starts else "—"), ""]
    if len(coming) > 1:
        lines += [words["any_of"], ""]
    for index, path in enumerate(coming, 1):
        if len(coming) > 1:
            lines.append(f"**{words['coming_option']} {index}**")
        rendered = [requirement_line(req, words) for req in path["requirements"]]
        lines += [line for line in rendered if line]
        lines.append("")
    return lines


def render(value: dict[str, Any], language: str) -> str:
    words = WORDS
    platform = PLATFORM[value["creator"]["platform"]]
    programs = value["programs"]
    lines = [f"# {words['title'].format(platform=platform)}", ""]

    decided = [p for p in programs if p["eligibility"] in {"eligible", "partially_determined"}]
    lines.append(f"{platform} runs {len(programs)} ways of paying creators directly. Below is "
                 f"what each one pays for and how far you are from it.")
    lines += ["", f"## {words['map']}", ""]
    for program in programs:
        lines.append(f"**{localized(program, 'display_name', language)}** - "
                     f"{localized(program, 'what_it_pays_for', language)}")
        lines.append("")

    lines += [f"## {words['standing']}", ""]
    for program in programs:
        name = localized(program, "display_name", language)
        lines.append(f"### {name} - {status_word(program, words)}")
        lines.append("")
        routes = usable_paths(program)
        if not routes:
            # "no live route" has three quite different causes, and telling a creator their rule is
            # unchecked when it simply has not started yet is wrong in a way they would act on.
            statuses = {p["rule_status"] for p in program["paths"]}
            if statuses and statuses <= {"not_yet_effective"}:
                starts = sorted(p["effective_from"] for p in program["paths"] if p["effective_from"])
                lines += [words["not_yet"].format(date=starts[0] if starts else "—"), ""]
                # The notice alone told a creator a bar is coming and not what it asks for. That is
                # the wrong half to withhold from the one programme where every route is still
                # ahead of them.
                lines += future_bar_lines(program, words, announce=False)
            elif statuses and statuses <= {"region_not_applicable"}:
                # 同一状态也用于地区未知；不能把缺少信息写成已确认的地区排除。
                region_message = "region_unknown" if value["creator"]["region"] == "UNKNOWN" else "region_no"
                lines += [words[region_message], ""]
            elif statuses and statuses <= {"stale"}:
                lines += [words["stale"], ""]
            elif statuses and statuses <= {"expired"}:
                lines += [words["expired"], ""]
            elif statuses and statuses <= {"stale", "expired", "not_yet_effective"}:
                lines += [words["stale"], ""]
            else:
                lines += [words["unverified"], ""]
            continue
        # Every live route is shown, not just the nearest one. The alternatives are genuinely
        # different strategies - a watch-time bar and a Shorts-views bar ask for different work -
        # and picking one for the creator hides the choice that is theirs to make.
        if len(routes) > 1:
            lines += [words["any_of"], ""]
        for index, path in enumerate(routes, 1):
            if len(routes) > 1:
                lines.append(f"**{words['route']} {index}**")
            rendered = [requirement_line(req, words) for req in path["requirements"]]
            lines += [line for line in rendered if line]
            lines.append("")

        lines += future_bar_lines(program, words, announce=True)

    # Grouped by programme, not merged: the conditions differ per programme, and a single flat list
    # leaves a creator unable to tell which of them applies to the one they are actually chasing.
    grouped: list[tuple[str, list[str]]] = []
    for program in programs:
        items = list(localized(program, "additional_requirements_not_assessed", language))
        if program["region_checked"] is False:
            items.append(words["region"])
        if items:
            grouped.append((localized(program, "display_name", language), items))
    if grouped:
        lines += [f"## {words['unconfirmed']}", ""]
        lines.append(
            "Even once the numbers are there, these conditions have to hold too. None of them is "
            "visible from outside, so they are yours to check:"
        )
        lines.append("")
        for name, items in grouped:
            lines.append(f"**{name}**")
            for item in items:
                lines.append(f"- {item}")
            lines.append("")

    scenarios = value["revenue_scenarios"]
    reported_note = False
    lines += [f"## {words['scenarios']}", ""]
    if not scenarios:
        # Naming the one missing number, and where it lives, is the difference between a dead end
        # and a next step. Every programme here has one side we already hold.
        # Two different answers that must not share a heading: one says a number exists once you
        # connect an account, the other says no number exists at all. Merging them would tell a
        # creator that connecting would unlock the ad figure, which it would not.
        if value.get("revenue_not_available"):
            lines += [words["no_figure"], ""]
            for item in value["revenue_not_available"]:
                program = next((p for p in programs if p["program_id"] == item["program_id"]), None)
                name = localized(program, "display_name", language) if program else item["program_id"]
                block = in_language(item.get("i18n"), language)
                reason = block.get("no_figure_reason") or item["reason"]
                lines.append(f"- **{name}** — {reason}")
            lines.append("")
        if value.get("revenue_inputs_needed"):
            lines += [words["no_scenarios"], ""]
        for item in value.get("revenue_inputs_needed", []):
            program = next((p for p in programs if p["program_id"] == item["program_id"]), None)
            name = localized(program, "display_name", language) if program else item["program_id"]
            block = in_language(item.get("i18n"), language)
            label = block.get("label") or item["label"]
            where = block.get("where_to_find") or item.get("where_to_find")
            lines.append(f"- **{name}** — {label}" + (f" — {where}" if where else ""))
        lines.append("")
    else:
        for scenario in scenarios:
            program = next((p for p in programs if p["program_id"] == scenario["program_id"]), None)
            name = localized(program, "display_name", language) if program else scenario["program_id"]
            # A range whose ends are equal is a single number; printing "40 - 40" reads as a bug.
            span = (f"{scenario['currency']} {compact(scenario['low'])}"
                    if scenario["low"] == scenario["high"] else
                    f"{scenario['currency']} {compact(scenario['low'])} - {compact(scenario['high'])}")
            period = "per month" if scenario["period"] == "monthly" else "per year"
            suffix = ""
            if scenario["conditional_on_eligibility"]:
                suffix = " (once you qualify)"
            taxed = ""
            if scenario.get("after_tax"):
                after = scenario["after_tax"]
                after_span = (f"{scenario['currency']} {compact(after['low'])}" if after["low"] == after["high"] else
                              f"{scenario['currency']} {compact(after['low'])} - {compact(after['high'])}")
                rate = compact(after["rate_percent"])
                taxed = "; " + words["after_tax"].format(span=after_span, rate=rate)
            lines.append(f"- **{name}**: {span} {period}{suffix}{taxed}")
            # What the figure is net of stays next to it. A channel RPM read as ad revenue, or taxed
            # a second share, is the mistake this sentence prevents.
            # `note` is a bare tag map rather than an `i18n` block, so it is resolved the same
            # way and then falls back to the English source.
            note = scenario.get("note") or {}
            said = in_language({k: {"note": v} for k, v in note.items()}, language).get("note") \
                or note.get("en")
            if said:
                lines.append(f"  {said}")
            if any(i.get("source") == "benchmark" and i.get("reported") for i in scenario["inputs"]):
                reported_note = True
        lines.append("")
        present = {s["program_id"] for s in scenarios}

        def program_name(program_id: str) -> str:
            found = next((p for p in programs if p["program_id"] == program_id), None)
            return localized(found, "display_name", language) if found else program_id

        for scenario in scenarios:
            covered = [pid for pid in scenario.get("includes_programs") or [] if pid in present]
            if covered:
                inner = ", ".join(program_name(pid) for pid in covered)
                lines.append(words["includes"].format(outer=program_name(scenario["program_id"]), inner=inner))
                lines.append("")
        if any(not s.get("after_tax") for s in scenarios):
            lines.append(words["pre_tax"])
            lines.append("")
        # The formula, the inputs and where each came from stay in the native JSON: that object is
        # the product contract and what we verify against. The creator gets the figure. What does
        # NOT get simplified away is the range and this line - the live calculator's own note calls
        # a single-point figure a red line because it reads as a promise.
        if reported_note:
            lines.append(words["note_reported"])
            lines.append("")
        lines.append("This is an estimated range, not an amount the platform has promised you.")
        lines.append("")

    niche = value.get("niche_comparison")
    if niche:
        lines += [f"## {words['niches']}", ""]
        rate = niche["rate"]
        block = in_language(rate.get("i18n"), language)
        lines.append(block.get("note") or rate["note"])
        lines.append("")
        if niche["competition"]["status"] == "handed_back":
            joined = ", ".join(niche["niches"])
            lines.append(words["niche_handback"].format(niches=joined))
            lines.append("")

    rows = value.get("income_rows") or []
    if value.get("income_breakdown_requested") and rows:
        names = {}
        for row in rows:
            found = next((p for p in programs if f"R-{p['program_id']}" == row["row_id"]), None)
            if found:
                names[row["row_id"]] = localized(found, "display_name", language)
        lines += [f"## {words['income']}", ""]
        lines += income_rows.render_rows(rows, value.get("income_totals") or [], names)
        lines.append("")

    # Both origins are shown. Dropping the estimates would leave a creator reading a range built
    # on a number nobody told them about, with no assumption listed anywhere.
    shown = [a for a in value["assumption_ledger"]
             if a["origin"] in {"user_supplied", "model_estimate"}]
    if shown:
        lines += [f"## {words['assumptions']}", ""]
        for item in shown:
            detail = f" - {compact(item['value'])} {item['unit']}" if item.get("value") is not None else ""
            mark = (f" ({words['estimate_mark']})"
                    if item["origin"] == "model_estimate" else "")
            lines.append(f"- {item['statement']}{detail}{mark}")
        lines.append("")

    lines += [words["closing"], ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("plan", type=Path)
    # Any BCP-47 tag. The renderer's own labels are English whatever is passed; the tag picks
    # which translation of the rule library's content to read, and an unknown one falls back to
    # the source text rather than exiting. A closed choice list turned "pt-BR" into a usage error.
    parser.add_argument("--language", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        value = json.loads(args.plan.read_text(encoding="utf-8"))
        text = render(value, args.language)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
        print(json.dumps({"status": "ok", "path": str(args.output)}))
        return 0
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "failed", "error": f"{type(error).__name__}: {error}"}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
