"""Read the model's written analysis: text it authors, numbers it may only take from this run's evidence.

Code owns the conclusion, evidence and every computed number. The model contributes judgment as text:
interpretation, hypotheses, the data still needed and next steps. A number in the interpretation is
cited as a {fact:…} placeholder, or typed only when this run's evidence states it: a computed fact of
the question (rounded, scaled or as a percentage), a date in its evidence, a metric of one of its
records, the user's own request, or a retrieved title or caption quoted verbatim. Reading is lenient:
anything that cannot be used is left out of the report with a note, so a malformed part never
discards the rest.
"""
from __future__ import annotations

import json
import math
import re
import sys
from datetime import datetime
from typing import Any

sys.dont_write_bytecode = True
from render_traffic import fmt, percent

FACT = re.compile(r"\{fact:([0-9A-Za-z_.]+)(?:\|(number|percent))?\}")
SENTENCE = re.compile(r"(?:[^。！？!?.\n]|\.(?=\d))+(?:[。！？!?]|\.(?!\d)|$)")
STEP_FIELDS = ("do", "check", "then", "opening")
# A stated date: year-month-day, or month-day, with -, / or CJK date markers ("." only after a year).
DATE = re.compile(r"(?<![0-9A-Za-z_.,])(?:(\d{4})\s*(?:[-/.]|年)\s*(\d{1,2})\s*(?:[-/.]|月)\s*(\d{1,2})\s*日?"
                  r"|(\d{1,2})\s*(?:[-/]|月)\s*(\d{1,2})\s*日?)(?![0-9])")
# A stated figure: digits with optional thousands separators and decimals, then an optional percent or scale.
FIGURE = re.compile(r"(?<![0-9A-Za-z_.,])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?(?![0-9])\s*"
                    r"(%|％|万|萬|亿|億|[kKmM](?![A-Za-z]))?")
CLOCK = re.compile(r"T?\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?")
SCALES = {"万": 1e4, "萬": 1e4, "亿": 1e8, "億": 1e8, "k": 1e3, "m": 1e6}
QUOTE_FORMS = {"'": "['‘’`]", "‘": "['‘’`]", "’": "['‘’`]", '"': '["“”]', "“": '["“”]', "”": '["“”]'}
SKIP_KEYS = {"record_ids", "content_ids", "limits"}
# A signed change is stated with its direction. Deliveries are written in en or zh-CN only.
FALL = re.compile(r"下降|下滑|下跌|减少|降低|降至|降了|跌|回落|缩水|少了|更少|低于|更低|"
                  r"\b(?:drop(?:s|ped)?|declin(?:e|ed|es)|f[ae]ll(?:s|en)?|down|decreas(?:e|ed|es)|lower|less|fewer|lost)\b",
                  re.IGNORECASE)
RISE = re.compile(r"上升|上涨|增长|增加|提升|提高|升至|涨|多了|更多|高于|更高|"
                  r"\b(?:ris(?:e|es|en)|rose|up|increas(?:e|ed|es)|grew|grow(?:s|n)?|growth|gain(?:s|ed)?|higher|more)\b",
                  re.IGNORECASE)
CLAUSE = re.compile(r"[^，,；;、：:]+")


def texts(value: Any) -> list[str]:
    """A string or a list of strings, with anything else left out."""
    items = value if isinstance(value, list) else [value]
    return [item.strip() for item in items if isinstance(item, str) and item.strip()]


def first_text(value: Any) -> str | None:
    found = texts(value)
    return " ".join(found) if found else None


def lookup(finding: dict, path: str) -> Any:
    value: Any = finding.get("facts", [])
    for key in path.split("."):
        if isinstance(value, list) and key.isdigit() and int(key) < len(value):
            value = value[int(key)]
        elif isinstance(value, dict) and key in value:
            value = value[key]
        else:
            return None
    return value if type(value) in (int, float) and math.isfinite(value) else None


def fact_refs(finding: dict) -> dict[str, str]:
    """Every computed number of one question, keyed by the placeholder that cites it."""
    refs: dict[str, str] = {}

    def walk(value: Any, path: list[str]) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key not in {"record_ids", "content_ids", "limits"}:
                    walk(item, path + [key])
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, path + [str(index)])
        elif type(value) in (int, float) and not isinstance(value, bool) and math.isfinite(value):
            refs["{fact:" + ".".join(path) + "}"] = fmt(value)

    for index, fact in enumerate(finding.get("facts", [])):
        walk(fact, [str(index)])
    return refs


def _figures(text: str) -> list[tuple[float, float, bool]]:
    """Each stated figure as (value, half of its last stated unit, whether it is a percentage)."""
    figures = []
    for whole, fraction, unit in FIGURE.findall(text):
        is_percent = unit in ("%", "％")
        scale = 1 if not unit or is_percent else SCALES[unit.lower()]
        value = float(whole.replace(",", "") + ("." + fraction if fraction else "")) * scale
        figures.append((value, 0.5 * 10 ** -len(fraction) * scale, is_percent))
    return figures


def _date_parts(match: re.Match) -> tuple[int | None, int, int]:
    if match.group(1):
        return int(match.group(1)), int(match.group(2)), int(match.group(3))
    return None, int(match.group(4)), int(match.group(5))


def _strings(value: Any, skip: bool = True) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for key, item in value.items() if not (skip and key in SKIP_KEYS)
                for text in _strings(item, skip)]
    if isinstance(value, list):
        return [text for item in value for text in _strings(item, skip)]
    return []


def _calendar_hours(value: dict) -> float | None:
    try:
        start = datetime.fromisoformat(str(value["start"]).replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(value["end"]).replace("Z", "+00:00"))
        return (end - start).total_seconds() / 3600
    except (KeyError, TypeError, ValueError):
        return None


def _numbers(value: Any) -> list[float]:
    """Every number a piece of evidence states, with the restatements code itself prints."""
    found: list[float] = []
    if isinstance(value, dict):
        if value.get("kind") == "calendar" and (hours := _calendar_hours(value)) is not None:
            found += [hours, hours / 24]
        for key, item in value.items():
            if key in SKIP_KEYS:
                continue
            if key != "difference":  # a signed change, matched with its direction by _changes
                found += _numbers(item)
    elif isinstance(value, list):
        for item in value:
            found += _numbers(item)
    elif type(value) in (int, float):
        found.append(float(value))
    elif isinstance(value, str):
        # The parts of a date or clock time are not figures; dates are matched whole.
        figures = _figures(DATE.sub(" ", CLOCK.sub(" ", value)))
        found += [figure / 100 if is_percent else figure for figure, _, is_percent in figures]
    return [number for number in found if math.isfinite(number)]


SIGNED_KEYS = {"ratio", "difference"}


def _changes(value: Any) -> list[float]:
    """Signed changes the evidence states: a difference, and the change a ratio implies (0.15 is down 85%)."""
    found: list[float] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in SKIP_KEYS:
                continue
            if key in SIGNED_KEYS and type(item) in (int, float) and math.isfinite(item):
                found.append(item - 1 if key == "ratio" else float(item))
            else:
                found += _changes(item)
    elif isinstance(value, list):
        for item in value:
            found += _changes(item)
    return found


def _quote_pattern(quote: str) -> re.Pattern:
    """A retrieved text as quoted: case, spacing and straight or curly quote marks may differ."""
    parts, previous_space = [], False
    for char in quote:
        if char.isspace():
            if not previous_space:
                parts.append(r"\s+")
            previous_space = True
            continue
        previous_space = False
        parts.append(QUOTE_FORMS.get(char, re.escape(char)))
    return re.compile("".join(parts), re.IGNORECASE)


class Grounding:
    """Where a typed number in one question's interpretation may come from."""

    def __init__(self, values: list[float], dates: set[tuple[int | None, int, int]], quotes: list[str],
                 changes: list[float] | None = None):
        self.values = values
        self.changes = changes or []
        self.dates = dates
        # Only real text is quoted around a number; a bare-digit title would ground any digits.
        texts = {" ".join(quote.split()) for quote in quotes}
        self.quotes = [_quote_pattern(quote) for quote in sorted(texts, key=len, reverse=True)
                       if len(quote) >= 4 and any(char.isalpha() for char in quote)]

    @staticmethod
    def _near(known: float, value: float, half: float) -> bool:
        return abs(known - value) <= half + 1e-9 * max(1.0, abs(known))

    def states_figure(self, value: float, half: float, is_percent: bool, clause: str = "") -> bool:
        """A typed figure states a known value when it rounds to it; a percentage states a fraction.

        A change is typed as its size, so its clause must not state the opposite direction:
        a 0.15 ratio may be "down 85%" or "an 85% change", never "up 85%".
        """
        if is_percent:
            value, half = value / 100, half / 100
        if any(self._near(abs(known), value, half) for known in self.values):
            return True
        fall, rise = bool(FALL.search(clause)), bool(RISE.search(clause))
        return any(self._near(abs(change), value, half) and not (rise and not fall if change < 0 else fall and not rise)
                   for change in self.changes)

    def states_date(self, year: int | None, month: int, day: int) -> bool:
        return (year, month, day) in self.dates if year else (None, month, day) in self.dates

    def unstated(self, sentence: str) -> bool:
        """Whether the sentence still types a digit once every number this evidence states is removed."""
        def date(match: re.Match) -> str:
            return " " if self.states_date(*_date_parts(match)) else match.group(0)

        def figure(match: re.Match) -> str:
            (value, half, is_percent), = _figures(match.group(0))
            clause = next((part.group(0) for part in CLAUSE.finditer(match.string)
                           if part.start() <= match.start() < part.end()), match.string)
            return " " if self.states_figure(value, half, is_percent, clause) else match.group(0)

        return bool(re.search(r"\d", FIGURE.sub(figure, DATE.sub(date, sentence))))


def grounding(finding: dict, analysis: dict | None = None, request: str | None = None) -> Grounding:
    """One question's evidence: its computed facts and labels, its own records, retrieved texts and the request."""
    values = _numbers(finding.get("facts", []))
    changes = _changes(finding.get("facts", []))
    texts = _strings(finding)
    record_ids = {text for fact in finding.get("facts", []) if isinstance(fact, dict)
                  for text in _strings(fact.get("record_ids", []), skip=False)}
    quotes: list[str] = []
    rows = analysis.get("rankings") if isinstance(analysis, dict) else None
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        quotes += [row[key] for key in ("title", "caption") if isinstance(row.get(key), str)]
        if str(row.get("content_id")) in record_ids:
            values += _numbers(row.get("metrics", {}))
            texts += [row["published_at"]] if isinstance(row.get("published_at"), str) else []
    if isinstance(request, str):
        values += _numbers(request)
        texts.append(request)
    dates: set[tuple[int | None, int, int]] = set()
    for text in texts:
        for match in DATE.finditer(text):
            year, month, day = _date_parts(match)
            if 1 <= month <= 12 and 1 <= day <= 31:
                dates |= {(year, month, day), (None, month, day)}
    return Grounding(values, dates, quotes, changes)


def resolve(finding: dict, value: str, typed_numbers: bool = False,
            ground: Grounding | None = None) -> tuple[str, list[str]]:
    """Fill fact placeholders; drop a sentence citing an unknown fact, or typing a number its evidence does not state.

    Without `ground`, no typed digit is accepted where numbers may not be typed.
    """
    cited: list[tuple[str, str]] = []
    quoted: list[str] = []

    def shield(match: re.Match) -> str:
        # Placeholder paths contain dots; hide them before splitting sentences on full stops.
        cited.append((match.group(1), match.group(2) or "number"))
        return f"\x00{len(cited) - 1}\x00"

    def shield_quote(match: re.Match) -> str:
        # A quoted title is retrieved text: its digits are stated, and its punctuation ends no sentence.
        quoted.append(match.group(0))
        return f"\x01{len(quoted) - 1}\x01"

    def fill(match: re.Match) -> str:
        path, form = cited[int(match.group(1))]
        return (percent if form == "percent" else fmt)(lookup(finding, path))

    def unquote(text: str) -> str:
        return re.sub(r"\x01(\d+)\x01", lambda m: quoted[int(m.group(1))], text)

    shielded = FACT.sub(shield, value)
    for pattern in ground.quotes if ground else []:
        shielded = pattern.sub(shield_quote, shielded)
    kept, dropped = [], []
    for sentence in SENTENCE.findall(shielded):
        if not sentence.strip():
            continue
        paths = [cited[int(i)][0] for i in re.findall(r"\x00(\d+)\x00", sentence)]
        typed = re.sub(r"\x00\d+\x00|\x01\d+\x01", " ", sentence)
        if any(lookup(finding, path) is None for path in paths) or (
                not typed_numbers and (ground.unstated(typed) if ground else re.search(r"\d", typed))):
            dropped.append(unquote(re.sub(r"\x00(\d+)\x00",
                                          lambda m: "{fact:" + cited[int(m.group(1))][0] + "}", sentence)).strip())
            continue
        kept.append(unquote(re.sub(r"\x00(\d+)\x00", fill, sentence)))
    return "".join(kept).strip(), dropped


def read(raw: str | dict, assessment: dict, source_analysis: dict | None = None,
         request: str | None = None) -> tuple[dict, list[dict]]:
    """Return the usable analysis per question and the issues found while reading it.

    `source_analysis` (the run's AccountAnalysis) and `request` (the user's original words) are the
    evidence a typed interpretation number may also come from.
    """
    issues: list[dict] = []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError as error:
            return {}, [{"path": "synthesis", "message": f"not valid JSON ({error}); send one JSON object"}]
    if not isinstance(raw, dict):
        return {}, [{"path": "synthesis", "message": "send one JSON object with a questions object"}]
    questions = raw.get("questions")
    if isinstance(questions, list):  # [{id, ...}] is read as the keyed form.
        questions = {str(q.get("id")): q.get("delivery", q) for q in questions if isinstance(q, dict)}
    if not isinstance(questions, dict):
        return {}, [{"path": "questions", "message": "questions must be an object keyed by question id"}]
    findings = {q["id"]: q for q in assessment["questions"]}
    for qid in questions:
        if qid not in findings:
            issues.append({"path": f"questions.{qid}",
                           "message": f"unknown question id; use one of {list(findings)}"})
    result = {}
    for qid, finding in findings.items():
        item = questions.get(qid)
        if not isinstance(item, dict):
            continue
        analysis: dict = {"notes": []}
        ground = grounding(finding, source_analysis, request)
        interpretation = first_text(item.get("interpretation"))
        if interpretation:
            analysis["interpretation"], dropped = resolve(finding, interpretation, ground=ground)
            for sentence in dropped:
                issues.append({"path": f"questions.{qid}.interpretation",
                               "message": "interpretation types only numbers this question's evidence states (a printed "
                                          "fact, date or record metric, the user's request, or a retrieved title or caption "
                                          "quoted verbatim); cite computed numbers as {fact:…} from the list printed with the "
                                          f"evidence; this sentence types or cites a number with no source: {sentence}"})
            if dropped:
                analysis["notes"].append("dropped_interpretation")
        def cited(field: str, value: str | None) -> str | None:
            """Other fields may type proposed numbers; cited facts are still filled or the sentence is dropped."""
            if not value:
                return value
            filled, dropped = resolve(finding, value, typed_numbers=True, ground=ground)
            for sentence in dropped:
                issues.append({"path": f"questions.{qid}.{field}",
                               "message": f"cites an unknown {{fact:…}}; use one printed with the evidence: {sentence}"})
                analysis["notes"].append("dropped_" + field.split("[")[0])
            return filled or None

        hypotheses = item.get("hypotheses")
        hypotheses = [h.get("text") if isinstance(h, dict) else h for h in hypotheses] \
            if isinstance(hypotheses, list) else hypotheses
        analysis["hypotheses"] = [h for h in (cited("hypotheses", h) for h in texts(hypotheses)) if h]
        need = item.get("need")
        if isinstance(need, (list, str)):
            need = {"items": need}
        if isinstance(need, dict):
            items = [i for i in (cited("need", i) for i in texts(need.get("items"))) if i]
            if items:
                analysis["need"] = {"items": items, "why": cited("need", first_text(need.get("why")))}
        steps = item.get("next_steps") or []
        if isinstance(steps, (dict, str)):
            steps = [steps]
        analysis["next_steps"] = []
        for index, step in enumerate(steps if isinstance(steps, list) else []):
            step = {"do": step} if isinstance(step, str) else step
            filled = {key: cited(f"next_steps[{index}]", first_text(step.get(key)))
                      for key in STEP_FIELDS} if isinstance(step, dict) else {}
            if not filled.get("do"):
                issues.append({"path": f"questions.{qid}.next_steps[{index}]", "message": "a next step needs do"})
                analysis["notes"].append("dropped_step")
                continue
            analysis["next_steps"].append({key: value for key, value in filled.items() if value})
        if item.get("detail") == "full":
            analysis["detail"] = "full"
        result[qid] = analysis
    return result, issues
