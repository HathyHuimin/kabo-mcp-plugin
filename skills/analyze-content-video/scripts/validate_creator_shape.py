#!/usr/bin/env python3
"""Hold the Creator-facing report to the shape the analysis already has.

The analysis frame has been stable since V2.2 — seventeen dimensions and a two-stage funnel. The
report was not: across nineteen runs the section count ranged from zero to ten, and three re-rolls
of one input produced ten, four and eight sections. Nothing checked it, so nothing held it.

What this decides:

  * the number of cards, which follows from whose video it is;
  * that every card cites an observation, so the report stands on the analysis rather than
    restating it from memory;
  * that a cited second names what is at that second;
  * that a line offered for the creator to say is not lifted from the video being torn down;
  * that a figure inside such a line came from the evidence;
  * that the card proposing the change actually carries the instruction;
  * that no field runs long enough to make the report something to read rather than act on.

What this does not decide: whether the columns actually pair. Line N of card 3 answering line N of
card 2 is a claim about meaning, and an earlier rule that tried to pin the first card to the
opening had to go — under this shape card one is strengths, and a video whose opening is its
weakest part would be forced to praise it. Pairing is read in benchmark review.

Nor does it decide wording, order within a card, or whether the writing is any good. Those
are read in benchmark review. A gate that returns a clean exit either way cannot judge them, and
the last attempt to make one judge them cost the reports their reasoning.

    python3 validate_creator_shape.py <result.json> <video_analysis.json> <content_evidence.json>
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import unicodedata
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from creator_measurement_basis import note_matches


# Strengths, then what holds it back, then what to do — the benchmarked three-card shape, three
# paired straight down so a creator can see which problem each change answers.
CARD_COUNT = 3
LINES_PER_CARD = 3

# Three cards, in this order, because a creator who knows where the answer lives reads faster
# than one who has to find out each time. Three sections cut from a benchmarked six-section
# layout, dropping the virality verdict and the title suggestions.
#
# What each card answers is fixed; what it is called is not. The titles used to be held to a
# two-language table, which meant SKILL.md could ask for them localized and the gate would reject
# every language but those two — a Portuguese or Japanese report writing correct headings failed
# on all three. The order and the content rules carry the meaning; they are the same in every
# language.
CARD_ROLES = ("strengths", "improve", "recommend")

# The last card is always the one that proposes the change, so it is the one carrying instructions.
# The first two are readings; demanding a hook from them would force an instruction where there is
# nothing to instruct.
CHANGE_CARD = 2
CHANGE_HOOK_MIN = 1
CHANGE_HOOK_MAX = 2

OBSERVATION_REF = re.compile(r"^observations\.(.+)$")

# A card that has to be read twice has not saved the creator any time. Three runs of the V3 shape
# put a median of 613 characters into a single `why_for_you` — three times the whole report's
# thesis — while every other field stayed short on its own. These caps hold each field to the
# scale the well-behaved ones already found, so length cannot pool in one place.
#
# Display width, not characters. The figures were set on Chinese reports and read as characters,
# which made them a different cap in every script: the same content in English runs two and a
# half to three times longer, against a ceiling calibrated for a script that carries about twice
# the meaning per character. A full-width character counts as two and everything else as one, so
# a Chinese report sits exactly where it did and a Latin-script one gets the room it always had.
FIELD_LIMITS = {
    "summary": 480, "data_note": 240,
    "card.why_for_you": 480, "card.action": 320,
    "hook.purpose": 160, "hook.visual_direction": 240,
}

# Digits only. Chinese numerals in "前三秒" are prose, not a claim about a measured quantity.
NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


def load(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def width(text: str) -> int:
    """How wide a string prints: a full-width character is two columns, everything else one.

    This is the unit every length rule here is stated in, so one cap covers every script instead
    of one cap per language.
    """
    return sum(2 if unicodedata.east_asian_width(character) in ("W", "F") else 1
               for character in text or "")


def normalise(text: str) -> str:
    """Compare lines by their words, so punctuation and casing do not hide a lift.

    The character class used to be ASCII plus CJK Unified Ideographs, which deleted every other
    script instead of normalising it: a Korean line came back empty and a Japanese one came back
    as the handful of kanji in it. Both checks that read this — whether a spoken line reproduces
    the analysed video's own sentence, and whether `data_note` repeats the report — then compared
    nothing against nothing and passed. The class below is Unicode-aware, so every script
    normalises the same way.

    NFKC first, because case folding does not fold canonical equivalence. The same accented
    letter written as one code point and as a letter plus a combining mark compares unequal —
    and worse, the combining mark is not alphanumeric, so the stripping step deletes it and the
    two forms end up genuinely different strings. NFKC also folds full-width Latin onto ASCII,
    which is what a CJK keyboard produces.
    """
    return re.sub(r"[\W_]+", " ", unicodedata.normalize("NFKC", text or "")).casefold().strip()


def repeated_note_statement(note: str, text: str) -> bool:
    """Detect a complete repeated note statement, not a shared evidence term.

    Preserve decimal periods; sentence punctuation, semicolons and newlines delimit the
    note's statements. Normalisation still folds Unicode, casing and punctuation.
    This checks explicit textual repetition, not semantic similarity or paraphrases.
    """
    boundaries = r"[\r\n\u2028\u2029;。!?]+|(?<!\d)\.|\.(?!\d)"
    statements = re.split(boundaries, unicodedata.normalize("NFKC", note or ""))
    others = [normalise(statement) for statement in
              re.split(boundaries, unicodedata.normalize("NFKC", text or ""))]
    return any(len(unit) >= 12 and any(unit in other for other in others)
               for statement in statements if (unit := normalise(statement)))


def quotes_the_video(line: str, cues: list[dict]) -> str | None:
    """Return the cue a spoken line reproduces, if it reproduces one.

    A short overlap is ordinary language, not a lift. What is being caught is a line handed to the
    creator to read aloud that is in fact the analysed creator's own sentence.
    """
    spoken = normalise(line)
    if len(spoken) < 12:
        return None
    for cue in cues:
        cue_text = normalise(cue.get("text", ""))
        if len(cue_text) < 12:
            continue
        if cue_text in spoken or spoken in cue_text:
            return cue.get("text", "")
    return None


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
# "千" before a measurement unit (千米, 千克, 千瓦, 千卡) is part of the unit, not a scale.
MAGNITUDES = {
    "": 1,
    "千": 1_000, "万": 10_000, "百万": 1_000_000, "千万": 10_000_000,
    "亿": 100_000_000, "万亿": 1_000_000_000_000,
    "k": 1_000, "thousand": 1_000,
    "m": 1_000_000, "million": 1_000_000,
    "b": 1_000_000_000, "bn": 1_000_000_000, "billion": 1_000_000_000,
}
FIGURE = re.compile(
    r"(\d+(?:[.,]\d+)*|(?<![A-Za-z0-9.])\.\d+)\s*(万亿|千万|百万|万|亿|千(?![米克瓦卡])|thousand|million|billion|bn|[kmb](?![a-z]))?",
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
    r"(?:\s*(万亿|千万|百万|万|亿|千(?![米克瓦卡])|(?i:thousand|million|billion|bn))|([kmbKMB])|\s+([KMB]))"
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
    # Rounding a count to a few significant figures is how people write it: 18,067,177 views is
    # "1800 万" or "1,800 万+" in a Chinese report, and 2,689,000 is "2.6M+" ("over 2.6M") as often
    # as "2.7M". Rounding only at the written decimal place called both invented, and a production
    # run of this Skill stopped on exactly that. So trailing zeros in a large written count are a
    # precision, not digits the source must hold, and cutting at the written precision counts the
    # same as rounding at it. Kept to counts of ten thousand or more with at least two significant
    # digits left, so a small round figure ("20 seconds", "100") still needs a source of its own.
    trailing = 0
    if decimals == 0 and target >= 1 and target == int(target):
        digits = str(int(target))
        trailing = len(digits) - len(digits.rstrip("0"))
        if target * scale < 10_000 or len(digits) - trailing < 2:
            trailing = 0
    step = 10.0 ** (trailing - decimals)
    # Small unscaled figures (seconds, multiples, ranks) keep plain rounding only.
    counted = scale > 1 or target * scale >= 10_000
    for value in values:
        if value == target and scale == 1:
            return True
        try:
            scaled = value / scale
            if round(scaled, decimals) == target:
                return True
            if counted and scaled > 0 and (
                    math.isclose(math.floor(scaled / step + 1e-9) * step, target)
                    or (trailing and math.isclose(round(scaled / step) * step, target))):
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
                # Opaque evidence IDs are structural links, not displayed numeric claims.
                if key != "evidence_refs":
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


def check(result: dict, analysis: dict, evidence: dict) -> list[str]:
    errors: list[str] = []
    errors.extend(envelope_errors(result, "analyze_content_video"))
    view = result.get("creator_view") or {}
    cards = view.get("cards") or []
    subject = analysis.get("subject_state")

    if subject not in {"own_published", "own_unpublished", "third_party", "unknown"}:
        return [f"unknown subject_state: {subject!r}"]

    if len(cards) != CARD_COUNT:
        errors.append(
            f"the report is {CARD_COUNT} cards — what works, what holds it back, what to do; "
            f"found {len(cards)}"
        )

    observations = {o["observation_id"]: o for o in analysis.get("observations") or []}
    borrowed_mechanisms_only = subject in {"third_party", "unknown"}
    if subject == "unknown":
        from compute_deterministic import compute, public_only_evidence_errors
        scope_errors = public_only_evidence_errors(evidence)
        errors += scope_errors
        if not scope_errors and analysis.get("deterministic") != compute(evidence, None, "unknown"):
            errors.append("unknown ownership must retain the public-only unassessed deterministic block")
        if (analysis.get("evidence_completeness") or {}).get("account_baseline") != "unavailable":
            errors.append("unknown ownership cannot declare an account baseline")
        if analysis.get("revisions"):
            errors.append("unknown ownership carries no source revisions")
    cues = ((evidence.get("transcript") or {}).get("cues")) or []

    for field in ("summary", "data_note"):
        limit = FIELD_LIMITS[field]
        if width(view.get(field) or "") > limit:
            errors.append(
                f"{field} runs {width(view.get(field) or '')} columns against a {limit} limit")

    # This line is mechanically derived. Extraction and citation cannot attest pixel viewing.
    images = evidence.get("images") or {}
    video_note = images.get("media_type") not in ("image", "image_carousel")
    if video_note:
        try:
            if not note_matches(view.get("data_note"), evidence, analysis, view):
                errors.append("creator_view.data_note disagrees with the recorded measurement basis")
        except ValueError as error:
            errors.append(str(error))

    # The one line saying how far to trust the report must not repeat the report.
    note = view.get("data_note") or ""
    if len(normalise(note)) >= 12:
        elsewhere = [("summary", view.get("summary") or "")]
        elsewhere += [(f"cards[{i}].why_for_you", c.get("why_for_you") or "")
                      for i, c in enumerate(cards)]
        for where, text in elsewhere:
            if repeated_note_statement(note, text):
                errors.append(
                    f"data_note repeats what {where} already says. A caveat belongs beside the "
                    "claim it weakens, once"
                )
                break

    seen_titles: dict = {}
    for index, card in enumerate(cards):
        title = str(card.get("title", "")).strip()
        label = f"card[{index}] {card.get('title', '')!r}"
        if not title:
            errors.append(
                f"card {index} carries no title. A creator finds the answer by its heading, and "
                "the heading is written in their language, not left out"
            )
        elif normalise(title) in seen_titles:
            errors.append(
                f"{label}: card {index} repeats the title of card "
                f"{seen_titles[normalise(title)]}. The three cards answer different questions "
                "and say so in their headings"
            )
        else:
            seen_titles[normalise(title)] = index
        # The schema allows `source_url` and nothing ever said what it was for, so two of three
        # isolated runs set it to the analysed video's own address and the document grew a bare
        # link under whichever card happened to carry it. It is evidence for no particular
        # sentence — the creator handed us this video — and the address is already on
        # `content_ref.permalink`.
        if card.get("source_url"):
            errors.append(
                f"{label}: carries a source_url. The creator supplied this video and the address "
                "is on content_ref.permalink; a moment worth opening is named by its timestamp"
            )
        hooks = card.get("hooks") or []
        for field in ("why_for_you", "action"):
            limit = FIELD_LIMITS[f"card.{field}"]
            if width(card.get(field) or "") > limit:
                errors.append(
                    f"{label}: {field} runs {width(card.get(field) or '')} columns against a "
                    f"{limit} limit"
                )

        # Three lines, so the three cards can be read straight down as columns.
        lines = [l for l in (card.get("why_for_you") or "").splitlines() if l.strip()]
        if len(lines) != LINES_PER_CARD:
            errors.append(
                f"{label}: carries {len(lines)} lines against {LINES_PER_CARD}. The cards pair line "
                "by line, and a column with a hole in it cannot be read down"
            )

        if index == CHANGE_CARD and not CHANGE_HOOK_MIN <= len(hooks) <= CHANGE_HOOK_MAX:
            errors.append(
                f"{label}: proposes the change, so it carries one or two hooks; found {len(hooks)}. "
                "A change the creator cannot execute without re-reading the report is a diagnosis "
                "wearing an action's clothes"
            )
        refs = card.get("evidence_refs") or []

        cited = []
        for ref in refs:
            match = OBSERVATION_REF.match(ref)
            if not match:
                continue
            if match.group(1) not in observations:
                errors.append(f"{label}: cites {ref}, which is not an observation in this analysis")
            else:
                cited.append(observations[match.group(1)])

        if not cited:
            errors.append(
                f"{label}: cites no observation. Every card carries the dimension, basis and "
                "confidence of the observation it stands on"
            )

        for position, hook in enumerate(hooks):
            where = f"{label} hooks[{position}]"
            for field in ("purpose", "visual_direction"):
                limit = FIELD_LIMITS[f"hook.{field}"]
                if width(hook.get(field) or "") > limit:
                    errors.append(
                        f"{where}: {field} runs {width(hook.get(field) or '')} columns against a "
                        f"{limit} limit"
                    )
            if hook.get("time_range") and not (hook.get("on_screen_text") or hook.get("visual_direction")):
                errors.append(
                    f"{where}: cites {hook['time_range']} with nothing at that moment. Name the "
                    "on-screen words or what the shot does, or drop the timestamp"
                )
            if borrowed_mechanisms_only:
                lifted = quotes_the_video(hook.get("spoken_line", ""), cues)
                if lifted:
                    errors.append(
                        f"{where}: hands the creator a line taken from the video being torn down "
                        f"({lifted!r}). What transfers is the mechanism, not the sentence"
                    )

    # Figures were checked only on hooks[].spoken_line until now, so a headline or a summary could
    # state anything. A real run opened with "261 万播放" and nothing looked at it.
    authored_view = dict(view)
    if video_note:
        # Its exact canonical value was checked above; do not add derived counts to the
        # source-number pool available to unrelated model-authored claims.
        authored_view.pop("data_note", None)
    errors.extend(figure_errors(authored_view, source_values(analysis, evidence)))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result", type=Path)
    parser.add_argument("analysis", type=Path)
    parser.add_argument("evidence", type=Path)
    args = parser.parse_args()

    errors = check(load(args.result), load(args.analysis), load(args.evidence))
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("OK: Creator-facing report matches the analysis frame")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
