#!/usr/bin/env python3
"""Render a CreatorTaskResult as the Markdown the creator reads.

This replaces a renderer that had drifted out of the delivery path. The old one built its own
document from the VideoAnalysis object with every heading hard-coded in English, which no
localized report could use; it was called by the package self-test and by nothing else, and the
reports that actually shipped were written by the model against `creator_view`. Rendering from
`creator_view` instead means the structured object and the visible document cannot disagree.

The section sequence is not a layout choice. It is the analysis frame projected outward — the
opening, then how the rest held, then what to change — and which sequence applies follows from
whose video it is. `validate_creator_shape.py` holds the object to that; this only presents it.

    python3 render_video_report.py <result.json> <output.md>
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


# `0.000–4.017s` is how the analysis carries a position, and the millisecond is load-bearing there:
# the shape gate resolves a card's timestamp against the observation that licenses it. It is not
# how a creator reads one. A creator scrubs a timeline, and a timeline is labelled `0:04`, so the
# document prints the timecode and the structured object keeps the precise seconds.
_SECONDS = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*s?\s*$")
_RANGE_SEPARATORS = ("–", "—", "-", "~")


def timecode(seconds: float) -> str:
    """`15.207` -> `0:15`. Truncates rather than rounds: a cue at 15.9s is still inside the
    fifteenth second, and rounding it to 0:16 would point past the thing it names."""
    total = int(seconds)
    return f"{total // 60}:{total % 60:02d}"


def format_time_range(value: str) -> str:
    """`0.000–4.017s` -> `[0:00–0:04]`, `15.207s` -> `[0:15]`.

    Anything that does not parse as seconds is returned untouched. A range whose halves cannot
    both be read is left alone rather than half-converted, because a document showing `0:00–4.017s`
    reads as a bug and an unconverted one only reads as unformatted.
    """
    text = str(value or "").strip()
    if not text:
        return text
    for separator in _RANGE_SEPARATORS:
        if separator in text:
            left, _, right = text.partition(separator)
            # A bare left half inherits the unit from the right: `0.000–4.017s` is two seconds
            # values, not a number and a duration.
            first, second = _SECONDS.match(left), _SECONDS.match(right)
            if first and second:
                return f"[{timecode(float(first.group(1)))}–{timecode(float(second.group(1)))}]"
            return text
    single = _SECONDS.match(text)
    return f"[{timecode(float(single.group(1)))}]" if single else text


# Separating a position from a duration in prose the model wrote.
#
# A range is always a position: "0–3s" points at the opening even though both halves are bare
# integers, in any language. A lone figure is the hard case, and the rule used to be "a decimal
# point makes it a position" — which held only because the recorded runs were Chinese, where a
# duration is written with 秒 and never with `s`. In English a duration is written exactly like a
# position: "Cut every 2.5s" came out as "Cut every [0:02]", which says "at two seconds" about
# something that means "for two and a half".
#
# There is no wording-free way to tell the two apart, so the renderer stops guessing at the
# wording and reads the figure instead. A lone figure is a position when either holds:
#
#   * this result records that instant — the `evidence_index` entries carry `t_start_s` and
#     `t_end_s`, and each hook carries its own `time_range`; or
#   * it is written to a precision only a measurement has. Two or more decimal places comes off a
#     frame index or a transcript cue (`11.138s`, `12.88s`); a length a person writes carries one
#     at most ("every 2.5s", "the last 3s").
#
# Both halves are needed. The first alone leaves a real position the model mentioned but no
# evidence entry lists — and a frame-precision figure reaching the document is what
# `test_the_rendered_report_carries_no_raw_frame_times` exists to stop. The second alone would
# convert a duration someone happened to write as `2.50s`.
#
# The conversion happens here and not in `creator_view`, because the shape gate grounds every
# number in the prose against the numbers in the analysis: 15.207 is in the evidence and 15 is
# not, so a timecode written upstream would be rejected as an invented figure. The object keeps
# the precise seconds; the document shows the timecode.
_NUM = r"\d+(?:\.\d+)?"
_PROSE_RANGE = re.compile(rf"({_NUM})s?\s*[–—~-]\s*({_NUM})s")
_PROSE_POINT = re.compile(r"(\d+\.\d+)s")


def evidence_positions(result: dict) -> set:
    """Every instant this result says exists, as the renderer may state it."""
    found: set = set()
    for entry in result.get("evidence_index") or []:
        for key in ("t_start_s", "t_end_s"):
            value = entry.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                found.add(round(float(value), 3))
    for card in (result.get("creator_view") or {}).get("cards") or []:
        for hook in card.get("hooks") or []:
            for number in re.findall(_NUM, str(hook.get("time_range") or "")):
                found.add(round(float(number), 3))
    return found


def prose_timecodes(text: str, positions: set | None = None) -> str:
    """Rewrite positions in already-written prose as timecodes, leaving durations alone.

    `positions` is the set of instants this result carries. A lone figure outside it is not
    rewritten, because nothing says it is a moment rather than a length.
    """
    known = positions or set()

    def _range(match: re.Match) -> str:
        return f"[{timecode(float(match.group(1)))}–{timecode(float(match.group(2)))}]"

    def _point(match: re.Match) -> str:
        figure = match.group(1)
        decimals = len(figure.partition(".")[2])
        if decimals < 2 and round(float(figure), 3) not in known:
            return match.group(0)
        return f"[{timecode(float(figure))}]"

    return _PROSE_POINT.sub(_point, _PROSE_RANGE.sub(_range, text or ""))


# The connective tissue this file adds around the model's own words, in Chinese and English.
#
# The card titles, findings and actions arrive already written in the creator's language because
# the analysis wrote them there; these labels are this renderer's own. The host relays the
# finished report unchanged, so an English-only table left "What to change" and "Say this" inside
# otherwise Chinese reports. A zh view gets the Chinese labels; every other language gets English,
# and for a language other than zh or en the host localizes headings and labels as SKILL.md says.
# `colon` follows the label: Chinese prose takes the full-width mark.
STRINGS = {
    "zh": {"why": "现在的效果", "action": "要改什么", "reuse": "怎么复用", "say": "这样说",
           "at": "位置", "on_screen": "画面文字", "shot": "镜头", "next": "下一步",
           "colon": "："},
    "en": {"why": "What it does now", "action": "What to change", "reuse": "How to reuse it",
           "say": "Say this", "at": "Where", "on_screen": "On screen", "shot": "Shot",
           "next": "Next", "colon": ": "},
}


def lang_of(view: dict) -> str:
    """`zh`, `zh-CN`, `zh-Hant` read as Chinese; anything else, or nothing, as English."""
    return "zh" if str(view.get("language") or "").strip().lower().startswith("zh") else "en"


def render_hook(hook: dict, words: dict) -> list[str]:
    """Two lines: where and what to do, then the line to say.

    An earlier version gave each hook three or four bullets — position, shot, line, rationale —
    and a card with two hooks became eight lines of prose the creator had to read before finding
    the instruction. The rationale rides with the direction instead of claiming its own line.
    """
    lines = []
    # A timestamp only earns its place when something at that moment is named, so the two are
    # printed together or not at all.
    if hook.get("time_range"):
        detail = hook.get("on_screen_text") or hook.get("visual_direction") or ""
        purpose = f" ({hook['purpose']})" if hook.get("purpose") else ""
        lines.append(f"- **{format_time_range(hook['time_range'])}**"
                     + (f" — {detail}" if detail else "") + purpose)
    elif hook.get("visual_direction"):
        lines.append(f"- **{words['shot']}**{words['colon']}{hook['visual_direction']}")
    if hook.get("spoken_line"):
        lines.append(f"- **{words['say']}**{words['colon']}{hook['spoken_line']}")
    return lines


def render(result: dict) -> str:
    view = result.get("creator_view") or {}
    words = STRINGS[lang_of(view)]
    out: list[str] = []

    # The package markdown validator anchors the document to the structured headline, so this
    # cannot be reworded here.
    out.append(f"# {view.get('headline', '').strip()}")
    if view.get("summary"):
        out.append("")
        out.append(view["summary"].strip())
    # How far to trust the report goes right after the answer: not ahead of it, where it would
    # defer the answer, and not at the end, where it becomes the closing caveat block SKILL.md
    # forbids.
    if view.get("data_note"):
        out.append("")
        out.append(f"*{view['data_note'].strip()}*")

    for card_index, card in enumerate(view.get("cards") or []):
        out.append("")
        out.append(f"## {card.get('title', '').strip()}")
        # Three lines, rendered as three lines. The cards pair straight down, and a paragraph
        # hides the pairing the shape exists to show.
        body = [l.strip() for l in (card.get("why_for_you") or "").splitlines() if l.strip()]
        if body:
            out.append("")
            out.extend(line if line.startswith(("-", "*", "①", "②", "③")) else f"- {line}"
                       for line in body)
        if card.get("metric"):
            out.append("")
            out.append(f"**{card['metric']}**")
        for hook in card.get("hooks") or []:
            hook_lines = render_hook(hook, words)
            if hook_lines:
                out.append("")
                out.extend(hook_lines)
        if card.get("action"):
            out.append("")
            # The first card is always the strengths card: its action says how to reuse a
            # mechanism that already works. Labelling that "What to change" renders a positive
            # reading as a defect.
            action_label = words["reuse"] if card_index == 0 else words["action"]
            out.append(f"**{action_label}**{words['colon']}{card['action'].strip()}")
        # No `source_url` branch. The schema allows the field and SKILL.md never described it, so
        # two of three isolated runs set it and the renderer printed a naked URL under the change
        # instruction. Labelling it was the wrong repair -- it made a meaningless link tidy instead
        # of asking what it was for, and the answer is nothing: the creator supplied this video,
        # the link is evidence for no particular sentence, and it sat under whichever card happened
        # to carry it. The address is on `content_ref.permalink`; a moment worth opening is named
        # by its timecode. SKILL.md now forbids setting one and the shape gate rejects it.

    next_actions = [a for a in (view.get("next_actions") or []) if str(a).strip()]
    if next_actions:
        out.append("")
        out.append(f"## {words['next']}")
        out.append("")
        out.extend(f"{i}. {a.strip()}" for i, a in enumerate(next_actions, 1))

    # Applied to the finished document rather than field by field. The six recorded runs put
    # positions in `summary`, `why_for_you`, `hooks[].visual_direction`, `hooks[].time_range` and
    # `next_actions` -- converting an enumerated list of fields missed four of those and shipped
    # one line reading "[0:59] … 口播也在 56.9s 停在半句上". Whatever the model writes next goes
    # through the same rewrite. Idempotent: a rendered `[0:15]` carries no trailing `s` and cannot
    # match again.
    #
    # The headline is the one exception, and it is not a style exception. The markdown validator
    # anchors the document to `creator_view.headline`, so the first line has to match that string
    # byte for byte; one recorded run opens with "留住人的是 0.199s 那句话" and rewriting it broke
    # the anchor. A headline naming the first instant is also the one place the raw figure reads
    # better than a timecode -- 0.199s is the point, and [0:00] loses it.
    document = "\n".join(out).strip()
    headline, separator, body = document.partition("\n")
    return headline + separator + prose_timecodes(body, evidence_positions(result)) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    result = json.loads(args.result.read_text(encoding="utf-8"))
    if not (result.get("creator_view") or {}).get("headline"):
        print("ERROR: creator_view.headline is required to render", file=sys.stderr)
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(result), encoding="utf-8")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
