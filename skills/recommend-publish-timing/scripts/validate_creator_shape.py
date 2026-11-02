#!/usr/bin/env python3
"""Hold the Creator-facing timing result to the shape the analysis already has.

The frame has been in `SKILL.md` since V2.4 — lead with the time, then what it rests on, what it is
worth, what to discount. The reports never showed it. Six runs produced four disjoint sets of card
titles and card counts of five, one, one and one; one of those five had two pairs of duplicate
titles. Nothing checked the shape, so nothing held it, and the migration that flattened the SKILL.md
list took two of the three instructions with it without anything failing.

What this decides:

  * the blocks, and their titles, which follow from the outcome — a refusal and a recommendation
    answer different questions and must not arrive wearing the same headings;
  * that each block is lines rather than a paragraph, because `confidence_penalties` arrives as
    separate entries and merging them back into prose is how the caveats became one long sentence;
  * that every block cites something in the object it claims to be reading;
  * that the block standing on `practical_stakes` survives — it is the one the V2.5 migration
    silently deleted, and the one thing here no competitor offers;
  * that peer accounts, when they contributed, are named;
  * that the creator reads a clock and not a timezone.

What this does not decide: whether the reading is any good, whether the right signal was picked, or
whether the wording is natural. Those are read in benchmark review. A gate that returns a clean exit
either way cannot judge them.

    python3 validate_creator_shape.py <result.json> <publish_recommendation.json>
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path


# Fixed per outcome, by position and by what each block stands on. A creator who has read one of
# these knows where the answer lives without reading for it; a task that returns the same sequence
# whatever happened has stopped answering.
#
# The titles themselves are not checked against a list of strings. SKILL.md asks for them in the
# creator's language, and a gate holding them to Chinese literals made that instruction
# unfollowable for everyone else: an English report writing correct English headings failed on
# three of them. What the sequence means is carried by position and by the evidence each block
# cites, both of which are the same in every language.
BLOCKS = {
    "recommended": ("comparison", "stakes", "caveats"),
    "no_clear_window": ("comparison", "what_would_change"),
    "abstain_no_history": ("what_would_change",),
    "abstain_no_publish_time": ("what_would_change",),
}

# What each role in the sequence has to stand on. This is the half of the block table that is
# not the wording: block 2 reads the signals, block 3 reads `practical_stakes`, block 4 reads
# `confidence_penalties`. Checking the title told us a block was in the right place; checking
# what it cites tells us it is the right block, and it says the same thing in every language.
#
# `stakes` matters most of the three. The V2.5 migration deleted that block silently and both
# frozen runs shipped without it; it is the only place the answer says "this may not be worth
# your time", and no competitor read for V3 says that at all.
#
# `what_would_change` carries no required reference: the refusal outcomes have no prescribed
# section to stand on, and inventing one here would reject documents nothing ever asked to be
# shaped that way. The general rule — every block cites something that resolves — still holds.
ROLE_REFERENCES = {
    "comparison": ("signals", "hour_curve", "ranking", "cohort"),
    "stakes": ("stakes", "practical_stakes"),
    "caveats": ("penalties", "confidence_penalties"),
    "what_would_change": (),
}

# Two to four lines. One line is a sentence with a heading on it; five is the paragraph this gate
# exists to prevent, re-arriving as a list.
LINES_PER_BLOCK = (2, 4)

# What a line may cite, and where each section actually lives. The check used to be a prefix match,
# which let `signals.anything_at_all` through: it asserted that a reference was *shaped* like one,
# not that it led anywhere. The point of citing is that a reader can open the object at that
# reference and find the thing the sentence is about, so the reference is now resolved.
#
# `penalties` and `stakes` are short names the reports have always used; the object spells them
# `confidence_penalties` and `practical_stakes`. Both resolve, because renaming them in three
# recorded runs would rewrite documents nobody re-ran, and because the short name is the one
# SKILL.md teaches.
CITABLE_SECTIONS = {
    "signals": ("evidence_base", "signals"),
    "hour_curve": ("evidence_base", "hour_curve"),
    "penalties": ("recommendation", "confidence_penalties"),
    "confidence_penalties": ("recommendation", "confidence_penalties"),
    "ranking": ("evidence_base", "ranking"),
    "stakes": ("evidence_base", "practical_stakes"),
    "practical_stakes": ("evidence_base", "practical_stakes"),
    "cohort": ("evidence_base", "cohort"),
}

# Where a section is a list, an entry is named by its own identifier rather than by position, so a
# reference stays valid when the ordering changes.
LIST_KEYS = ("signal", "code", "name", "id")

# A container the reports skip over. Both `hour_curve.14` and `hour_curve.hours.14` appear in
# recorded runs and both name the same hour; neither spelling is wrong enough to reject a document
# over, so the walk descends through the container when a segment does not match directly.
TRANSPARENT = ("hours",)


def dig(node, path):
    for step in path:
        if not isinstance(node, dict) or step not in node:
            return None
        node = node[step]
    return node


def step_into(node, segment: str):
    """One segment of a reference. Dicts by key, lists by an entry's own identifier."""
    if isinstance(node, dict):
        if segment in node:
            return node[segment]
        for container in TRANSPARENT:
            inner = node.get(container)
            if isinstance(inner, dict) and segment in inner:
                return inner[segment]
        return None
    if isinstance(node, list):
        for item in node:
            if isinstance(item, dict) and any(str(item.get(k)) == segment for k in LIST_KEYS):
                return item
        return None
    return None


def resolves(ref: str, rec: dict) -> bool:
    """True when `ref` names something actually present in this recommendation."""
    head, _, tail = str(ref).partition(".")
    path = CITABLE_SECTIONS.get(head)
    if path is None:
        return False
    node = dig(rec, path)
    if node is None:
        return False
    for segment in (s for s in tail.split(".") if s):
        node = step_into(node, segment)
        if node is None:
            return False
    return True

# Display width, not characters. The caps were set on Chinese reports and read as characters, so
# the same content in English — two and a half to three times as long — hit a ceiling calibrated
# for a script that carries about twice the meaning per character. Counting width (a full-width
# character is 2, everything else 1) leaves every Chinese report exactly where it was and gives a
# Latin-script report the room the Chinese one always had.
FIELD_LIMITS = {"summary": 400, "data_note": 240, "block.action": 320}

# The other end of the same problem, in the same units. Both frozen runs shipped at 275 and 325
# characters against baselines of 1205 and 1146, and the audit that measured the collapse passed
# them anyway because its floor was `expected_min_ratio: 0.0`. The block and line counts already
# stop a report from collapsing into one paragraph; these stop it from satisfying them with stubs.
MIN_LINE_WIDTH = 24
MIN_SUMMARY_WIDTH = 40

# The creator is already in their own timezone. Naming it is system detail; naming a different one
# asks them to do a conversion the engine exists to do for them.
# The offset half needs the lookbehind. Without it `14:00-15:00` matches — the hyphen in an ordinary
# time range reads as the sign of an offset — and the gate quietly forbids the most natural way to
# write a window. Three independent runs all detoured around it, one of them saying so in as many
# words, which is a gate shaping the prose rather than checking it.
ZONE_IN_TEXT = re.compile(
    r"\b(?:UTC|GMT|[A-Z][a-z]+/[A-Z][a-z_]+)\b|(?<![\d:])[+-]\d{2}:\d{2}\b|北京时间|格林威治")


def load(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def lines_of(text: str) -> list:
    return [line for line in (text or "").splitlines() if line.strip()]


def width(text: str) -> int:
    """How wide a string prints: a full-width character is two columns, everything else one.

    This is the unit every length rule here is stated in, so one cap covers every script instead
    of one cap per language.
    """
    return sum(2 if unicodedata.east_asian_width(character) in ("W", "F") else 1
               for character in text or "")



NUMBER = re.compile(r"\d+(?:[.,]\d+)*")

# Number grounding across the whole Creator-facing block, not just the line read aloud.
#
# The rounding rule is ported from `compare_rounds.py`, which learned it the hard way: exact token
# matching called `3.1` invented when the object held `3.1043`, and one stability round wrote
# "3 倍多" purely to get past the check — the measuring instrument changing the thing it measured.
#
# Magnitude words are part of the same problem, in every script the reports are written in. A
# localized report writes "261 万播放" for 2,605,682 and an English one writes "2.6M views" for the
# same number; a check that reads only the token `261` or `2.6` calls a correctly rounded figure a
# fabrication. The unit is a scale on the source, so it is divided out before comparing. Both sets
# are accepted at once because the model may reach for either — the report's language does not
# decide which notation a figure arrives in.
#
# The single letters need the lookahead: without it "5 km" reads as five thousand and "3 more"
# reads as three million. "万亿" is listed before "万" so "5万亿" reads as five trillion, not
# 5万 followed by a stray 亿, and ".5B" reads as 0.5B, not 5B. A dot after a letter, digit or dot
# is not a leading-dot decimal: "obs.14" in a reference stays 14, as it always read.
MAGNITUDES = {
    "": 1,
    "万": 10_000, "亿": 100_000_000, "万亿": 1_000_000_000_000,
    "k": 1_000, "thousand": 1_000,
    "m": 1_000_000, "million": 1_000_000,
    "b": 1_000_000_000, "bn": 1_000_000_000, "billion": 1_000_000_000,
}
FIGURE = re.compile(
    r"(\d+(?:[.,]\d+)*|(?<![A-Za-z0-9.])\.\d+)\s*(万亿|万|亿|thousand|million|billion|bn|[kmb](?![a-z]))?",
    re.IGNORECASE)
# A clock is not a figure the creator stands behind, and splitting one produces two that nobody
# wrote: `14:00` yields `14` and `00`. Hours are governed by the block rules above, which already
# know a time range from a UTC offset, so times are lifted out before figures are read.
CLOCK = re.compile(r"\d{1,2}:\d{2}(?::\d{2})?")
# A scaled figure in the source must stand on its own: nothing alphanumeric right before the
# number or right after the unit. Without that, "5 billionaires" reads as five billion and an
# identifier fragment such as "-5b2e" in a UUID or "c5b3" in a hash reads as 5B, and either would
# ground a figure nobody stated. A single letter after a space counts only in capitals: speech
# recognition writes "27 B", while "5 m tall" is metres, not five million. Attached letters
# ("5M", "27b") count in either case, and ".5 billion" is read as 0.5 billion.
# The report side reads ".5" as 0.5, so the source keeps that reading next to the bare digits.
LEADING_DOT = re.compile(r"(?<![A-Za-z0-9.])\.\d+")
SOURCE_FIGURE = re.compile(
    r"(?<![A-Za-z0-9.])(\d+(?:[.,]\d+)*|\.\d+)"
    r"(?:\s*(万亿|万|亿|(?i:thousand|million|billion|bn))|([kmbKMB])|\s+([KMB]))"
    r"(?![A-Za-z0-9])")


def _strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for child in node.values():
            yield from _strings(child)
    elif isinstance(node, list):
        for child in node:
            yield from _strings(child)


def source_values(*objects) -> set:
    # The source can carry a magnitude word too: a transcript says "27 B" or "27 billion" and a
    # caption says "27B" for a model's parameter count. Reading only the digits keeps 27, and the
    # report's "27B" is then compared as 27 billion against 27 and called invented. Each scaled
    # figure is kept alongside its bare digits, so a source "27B" grounds "27B" and "27 billion",
    # while a bare 27 elsewhere still does not ground "27B". Scaled figures are read from the
    # string values themselves, not the JSON text, so a transcript line break between "27" and
    # "billion" stays whitespace instead of becoming a literal "\n".
    values = set()
    for obj in objects:
        text = json.dumps(obj, ensure_ascii=False)
        for raw in NUMBER.findall(text) + LEADING_DOT.findall(text):
            try:
                values.add(float(raw.replace(",", "")))
            except ValueError:
                continue
        for text in _strings(obj):
            for raw, *units in SOURCE_FIGURE.findall(text):
                unit = next(u for u in units if u)
                try:
                    values.add(float(raw.replace(",", "")) * MAGNITUDES[unit.lower()])
                except (ValueError, KeyError):
                    continue
    return values


def is_grounded(token: str, unit: str, values: set) -> bool:
    """True when the object holds this figure, or holds something it rounds from at this scale."""
    plain = token.replace(",", "")
    try:
        target = float(plain)
    except ValueError:
        return True
    scale = MAGNITUDES.get((unit or "").lower(), 1)
    decimals = len(plain.split(".")[1]) if "." in plain else 0
    for value in values:
        if value == target and scale == 1:
            return True
        try:
            if round(value / scale, decimals) == target:
                return True
        except (ValueError, ZeroDivisionError, OverflowError):
            continue
    return False


def figure_errors(view: dict, values: set) -> list[str]:
    errors: list[str] = []
    seen: set = set()

    def walk(node, path: str) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                walk(child, f"{path}.{key}")
        elif isinstance(node, list):
            for index, child in enumerate(node):
                walk(child, f"{path}[{index}]")
        elif isinstance(node, str):
            for token, unit in FIGURE.findall(CLOCK.sub(" ", node)):
                if (token, unit) in seen or is_grounded(token, unit, values):
                    continue
                seen.add((token, unit))
                errors.append(
                    f"{path} states {token}{unit}, which appears nowhere in the analysis or the "
                    f"evidence. Every figure the creator reads is one they may be asked to stand "
                    f"behind")

    walk(view, "creator_view")
    return errors


# The delivered object's envelope, which nothing checked until now. Across three real runs of this
# Skill the `creator_view` block — the part this gate already read — came back with the same six
# keys every time, while the envelope around it came back three different ways: one run carried
# `schema_version`/`task`, one carried `status`/`request_id`/`attribution` instead, and one invented
# the version string `creator-task-result.v1`, which exists nowhere in this repo. What the gate
# checks stays still; what it does not check drifts.
#
# The contract is not new: `creator-task-result.v2-final` is what this repo's golden result already
# conforms to. The runtime deliveries drifted off it, so this reads the golden's shape back onto
# them. Checked by hand rather than with jsonschema because the published package imports only the
# standard library, and its package-contract test enforces that.
SCHEMA_VERSION = "creator-task-result.v2-final"
# The PublishRecommendation's own blocks, plus the four SKILL.md names outright. A result that
# carries any of them has copied the audit object rather than pointed at it.
# An id is a reference and stays welcome; what may not come across is a block of the audit object.
AUDIT_BLOCKS = ("evidence_base", "calculations", "reproducibility", "limitations", "evidence",
                "finding", "abstain_reason", "causal_boundary", "refused_fallbacks",
                "recommendation")
ENVELOPE_REQUIRED = ("schema_version", "result_id", "task", "status", "creator_view",
                     "details", "evidence_index", "interaction")
VIEW_REQUIRED = ("language", "headline", "summary", "cards", "next_actions", "data_note")
CARD_REQUIRED = ("title", "why_for_you", "action", "evidence_refs")


def envelope_errors(result: dict, task: str) -> list[str]:
    errors: list[str] = []
    for key in ENVELOPE_REQUIRED:
        if key not in result:
            errors.append(
                f"the result is missing {key!r}. The envelope is part of the contract: a delivery "
                f"that declares no version, id, task or status cannot be archived or read back")
    found = result.get("schema_version")
    if found is not None and found != SCHEMA_VERSION:
        errors.append(
            f"schema_version is {found!r}; the contract is {SCHEMA_VERSION!r}. A version string "
            f"invented per run names nothing")
    if result.get("task") not in (None, task):
        errors.append(f"task is {result.get('task')!r}; this Skill delivers {task!r}")
    # SKILL.md has said since V3 that `details` is the host's half of the result and not the audit
    # ledger. Nothing enforced it, and on 2026-09-23 a run copied the whole PublishRecommendation
    # in: `evidence_base` alone ran 9 KB, against 0.1-1.3 KB for `details` across the twenty-eight
    # runs before it. The audit layer travels alongside as its own object; duplicated into the one
    # the App renders, the two copies drift and the creator's result grows an audit layer nobody
    # asked it to carry.
    carried = [key for key in AUDIT_BLOCKS if key in (result.get("details") or {})]
    if carried:
        errors.append(
            f"details carries {', '.join(sorted(carried))}, which belong to the PublishRecommendation "
            f"travelling beside this result. Reference it with `recommendation_ref`; do not copy it in")
    if "status" in result and not str(result.get("status") or "").strip():
        errors.append("status is empty. A partial result must never be presented as complete")
    view = result.get("creator_view")
    if isinstance(view, dict):
        for key in VIEW_REQUIRED:
            if key not in view:
                errors.append(f"creator_view is missing {key!r}")
        for index, card in enumerate(view.get("cards") or []):
            for key in CARD_REQUIRED:
                if not str(card.get(key) or "").strip() and not card.get(key):
                    errors.append(
                        f"card[{index}] is missing {key!r}. Every card carries what to do about it; "
                        f"a card with a diagnosis and no action is an observation")
    return errors


def check(result: dict, rec: dict) -> list[str]:
    errors: list[str] = []
    errors.extend(envelope_errors(result, "recommend_publish_timing"))
    view = result.get("creator_view") or {}
    cards = view.get("cards") or []
    outcome = rec.get("outcome")

    if outcome not in BLOCKS:
        return [f"unknown outcome: {outcome!r}"]
    expected = BLOCKS[outcome]

    if len(cards) != len(expected):
        errors.append(
            f"{outcome} is {len(expected)} block(s) — {' / '.join(expected)}; found {len(cards)}")

    for field in ("summary", "data_note"):
        limit = FIELD_LIMITS[field]
        if width(view.get(field) or "") > limit:
            errors.append(
                f"{field} runs {width(view.get(field) or '')} columns against a {limit} limit")
    if width(view.get("summary") or "") < MIN_SUMMARY_WIDTH:
        errors.append(
            f"summary is {width(view.get('summary') or '')} columns. A report that says the time "
            f"and stops has not said what it rests on")

    seen_titles: dict = {}
    for index, card in enumerate(cards):
        title = str(card.get("title", "")).strip()
        label = f"block[{index}] {title!r}"
        if not title:
            errors.append(
                f"block {index} carries no title. A creator finds the answer by its heading, and "
                "the heading is written in their language, not left out")
        elif title.casefold() in seen_titles:
            errors.append(
                f"{label}: block {index} repeats the title of block "
                f"{seen_titles[title.casefold()]}. Each block answers a different question and "
                "says so in its heading")
        else:
            seen_titles[title.casefold()] = index

        lines = lines_of(card.get("why_for_you"))
        low, high = LINES_PER_BLOCK
        if not low <= len(lines) <= high:
            errors.append(
                f"{label}: carries {len(lines)} line(s), against {low}-{high}. One fact per line — "
                "the penalties arrive as separate entries and must not be merged back into prose")
        for position, line in enumerate(lines):
            if width(line.strip()) < MIN_LINE_WIDTH:
                errors.append(
                    f"{label} line {position} is {width(line.strip())} columns. A line that short "
                    "meets the count without carrying a fact")

        if width(card.get("action") or "") > FIELD_LIMITS["block.action"]:
            errors.append(f"{label}: action runs {width(card.get('action') or '')} columns against "
                          f"{FIELD_LIMITS['block.action']}")

        refs = card.get("evidence_refs") or []
        if not refs:
            errors.append(f"{label}: cites nothing. Every block stands on something in the object")
        for ref in refs:
            if not resolves(str(ref), rec):
                errors.append(
                    f"{label}: cites {ref!r}, which does not resolve in this recommendation. "
                    f"Citable sections are {', '.join(sorted(set(CITABLE_SECTIONS)))}; name a list "
                    "entry by its own identifier, not its position")

    # Each block in the sequence stands on what its role reads. A result with the right number of
    # blocks and three distinct headings used to pass while carrying the same content three times,
    # or the stakes block where the comparison belongs.
    for index, role in enumerate(expected):
        wanted = ROLE_REFERENCES.get(role) or ()
        if not wanted or index >= len(cards):
            continue
        heads = {str(ref).partition(".")[0] for ref in cards[index].get("evidence_refs") or []}
        if not heads & set(wanted):
            errors.append(
                f"block {index} is the {role} block and cites {sorted(heads) or 'nothing'}; it "
                f"has to stand on one of {', '.join(wanted)}. The sequence is what the creator "
                f"navigates by, and a block is in the sequence by what it reads, not by its "
                f"heading")

    # Peer data is admissible because a creator can check it. Unattributed, it is the generic chart.
    signals = (rec.get("evidence_base") or {}).get("signals") or []
    peer = next((s for s in signals if s.get("signal") == "peer_accounts_hour"), None)
    text = json.dumps(view, ensure_ascii=False)
    if peer and peer.get("status") in ("available", "peer_assisted") and peer.get("hour") is not None:
        named = [a.get("handle") for a in peer.get("accounts") or [] if a.get("handle")]
        # Triggered by the signal having spoken, not by the report saying so. The wording that
        # used to arm this check was the Chinese for "comparable accounts", which left the rule
        # SKILL.md states as a hard one unenforced in every other language.
        if named and not any(str(handle) in text for handle in named):
            errors.append(
                "the result talks about comparable accounts without naming one. The whole reason "
                "peer data is allowed here is that the creator can open it and disagree")

    for field, value in (("headline", view.get("headline")), ("summary", view.get("summary")),
                         ("data_note", view.get("data_note"))):
        if ZONE_IN_TEXT.search(str(value or "")):
            errors.append(
                f"{field} names a timezone. The creator is already in theirs — show the clock "
                f"({ZONE_IN_TEXT.search(str(value)).group(0)!r} found)")
    for index, card in enumerate(cards):
        for key in ("why_for_you", "action"):
            found = ZONE_IN_TEXT.search(str(card.get(key) or ""))
            if found:
                errors.append(f"block[{index}].{key} names a timezone ({found.group(0)!r}); "
                              "the zone belongs in the structured object, not the report")
    errors.extend(figure_errors(result.get("creator_view") or {}, source_values(rec)))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result", type=Path)
    parser.add_argument("recommendation", type=Path)
    args = parser.parse_args()
    errors = check(load(args.result), load(args.recommendation))
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("OK: Creator-facing timing result matches the analysis frame")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
