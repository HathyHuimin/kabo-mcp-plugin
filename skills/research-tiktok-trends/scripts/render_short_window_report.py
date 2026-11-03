#!/usr/bin/env python3
"""Render the two-layer short-window report: heat first, then the exact window. Never claim heat is in-window."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, timezone
from fractions import Fraction
import hashlib
import json
import os
import re
import unicodedata
from pathlib import Path


# One English source contract; translated prose is authored per report, never a locale table.
LABELS = {
    "title_prefix": "Hottest first, then the exact window",
    "intro": "The heat layer is ordered by public plays and always shows the publish time. Do not read those rows as in-window.",
    "heat_heading": "Hottest now (may be outside the window)",
    "window_heading": "Inside the window",
    "empty_heat": "No dated public posts in this layer either.",
    "empty_window": "No dated posts fall inside this window. The heat layer is still usable to copy, but it does not show acceleration inside this window.",
    "plays_unit": "plays",
    "unknown_plays": "plays unknown",
    "unknown_handle": "unknown",
    "unknown_date": "date unknown",
    "no_title": "(no title)",
}
PRESENTATION_SCHEMA = "short-window-presentation.v1"
TEXT_PRESENTATION_SCHEMA = "short-window-presentation-text.v1"
MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July",
               "August", "September", "October", "November", "December")
CALENDAR_DATE = re.compile(
    r"(?<![0-9A-Za-z_])(?P<month_name>" + "|".join(MONTH_NAMES) + r")\s+"
    r"(?P<english_day>[0-9]{1,2})(?:,\s*|\s+)(?P<english_year>[0-9]{4})(?![0-9A-Za-z_]|[.,][0-9])"
    r"|(?<![0-9A-Za-z_])(?<![0-9][.,])(?P<year>[0-9]{4})\s*年\s*(?P<month>[0-9]{1,2})\s*月\s*"
    r"(?P<day>[0-9]{1,2})\s*日", re.IGNORECASE)


def decimal_digits(text: str) -> str:
    """Normalize only Nd digits for comparison, never source text or identity fields."""
    return "".join(str(unicodedata.decimal(char)) if unicodedata.category(char) == "Nd"
                   else char for char in text)


def numeric_facts(text: str) -> Counter:
    """Compare complete supported calendar dates by value, other numbers literally."""
    dates = Counter()

    def replace(match):
        if match.group("month_name"):
            month = next(index for index, name in enumerate(MONTH_NAMES, 1)
                         if name.lower() == match.group("month_name").lower())
            year, day = int(match.group("english_year")), int(match.group("english_day"))
        else:
            year, month, day = (int(match.group(key)) for key in ("year", "month", "day"))
        try:
            value = date(year, month, day)
        except ValueError:
            return match.group()  # An invalid date gets no equivalence exception.
        dates[("calendar_date", value.isoformat())] += 1
        return " "

    remaining = CALENDAR_DATE.sub(replace, decimal_digits(text))
    return Counter(re.findall(r"[0-9]+(?:[.,][0-9]+)*", remaining)) + dates


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def item_digest(item: dict) -> str:
    return digest(json.dumps(item, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode("utf-8"))


def displayed_rows(layers: dict):
    for layer in ("heat", "in_window"):
        for index, item in enumerate((layers.get(layer) or [])[:12]):
            if isinstance(item, dict):
                yield f"{layer}:{index}", item


def authoring_excerpt(item: dict) -> str:
    """Project the original 80-character excerpt to its inline display; keep the raw item intact."""
    return (item.get("title") or "")[:80].replace("\r", " ").replace("\n", " ")


def presentation_window_hours(layers: dict) -> Fraction:
    """Derive exact elapsed UTC hours for the English author sources without float rounding."""
    window = layers.get("window") if isinstance(layers, dict) else None
    if not isinstance(window, dict) or any(not isinstance(window.get(key), str) for key in ("since", "until")):
        raise ValueError("presentation window needs timezone-aware since and until")
    for value in (window["since"], window["until"]):
        # Builder-style ISO clocks only; Python accepts ambiguous fractional hours/minutes
        # and silently truncates excess precision (including some fractional UTC offsets).
        match = re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]"
            r"(?:[.,](?P<fraction>[0-9]+))?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])", value)
        if match is None or (match.group("fraction") or "")[6:].strip("0"):
            raise ValueError("presentation window needs HH:MM:SS, Z or +/-HH:MM and exact microsecond precision")
    try:
        endpoints = [datetime.fromisoformat(window[key].replace("Z", "+00:00")) for key in ("since", "until")]
        if any(value.tzinfo is None or value.utcoffset() is None for value in endpoints):
            raise ValueError("timezone is required")
        delta = endpoints[1].astimezone(timezone.utc) - endpoints[0].astimezone(timezone.utc)
    except (ValueError, OverflowError) as error:
        raise ValueError("presentation window needs representable timezone-aware endpoints") from error
    micros = (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds
    if micros <= 0:
        raise ValueError("presentation window must have until after since")
    return Fraction(micros, 3600000000)


def report_limitations(layers: dict) -> list[str]:
    notes = []
    gate = layers.get("topic_gate")
    if gate:
        counts = gate["counts"]
        notes.append(f"Topic review retained {counts['on_niche']} of {sum(counts.values())} candidates "
                     "using source text. This is model judgment, not video or audio verification.")
        from content_language_gate import notes as language_notes
        notes.extend(language_notes(gate))
        undated = (layers.get("coverage") or {}).get("undated_count", 0)
        if undated:
            notes.append(f"{undated} on-topic candidates lack a publish date and were not ranked.")
    for note in layers.get("limitations", []):
        if isinstance(note, str) and note.strip() and note not in notes:
            notes.append(note)
    return notes


def report_labels(layers: dict) -> dict[str, str]:
    labels = dict(LABELS)
    if layers.get("source_policy") == "instagram-week-search.v1":
        labels.update(title_prefix="Verified posts in the requested week",
                      intro="Both lists use original post dates inside the exact window and are ordered by public plays. They do not measure acceleration.",
                      heat_heading="Highest public plays in the verified week",
                      empty_window="No verified on-topic posts fall inside this window.")
    return labels


def presentation_contract(layers: dict, language: str, layers_sha256: str) -> dict:
    # Validate the same layer structure before emitting a skeleton. No report is written here.
    hours = presentation_window_hours(layers)
    render(layers)
    if not isinstance(language, str) or not language.strip() or len(language) > 64:
        raise ValueError("a presentation needs an explicit delivery language")
    if not isinstance(layers_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", layers_sha256):
        raise ValueError("layers_sha256 must bind the actual layers file bytes")
    labels = report_labels(layers)
    sources = {**labels, "intro": f"The exact window spans {hours} hours. " + labels["intro"],
               "window_heading": f"Inside the {hours}-hour window"}
    notes = report_limitations(layers)
    if notes:
        sources["limitations_heading"] = "Coverage and limitations"
        sources.update({f"limitation:{index}": note for index, note in enumerate(notes)})
    return {
        "schema_version": PRESENTATION_SCHEMA,
        "language": language,
        "layers_sha256": layers_sha256,
        "labels": {key: {"source": source, "source_sha256": digest(source.encode("utf-8")),
                          "text": None} for key, source in sources.items()},
        "rows": [{"slot": slot, "item_sha256": item_digest(item),
                  "source_excerpt": authoring_excerpt(item),
                  "source_excerpt_sha256": digest(authoring_excerpt(item).encode("utf-8")),
                  "text": None if item.get("title") else ""}
                 for slot, item in displayed_rows(layers)],
    }


def literal_tokens(text: str, *, calendar_dates: bool = False) -> tuple[Counter, Counter, Counter]:
    """Protect literal facts inside a source excerpt; metadata is never taken from prose."""
    return (numeric_facts(text) if calendar_dates else Counter(re.findall(r"[0-9]+(?:[.,][0-9]+)*", decimal_digits(text))),
            Counter(re.findall(r"https?://[^\s<>\[\]()]+", text)),
            Counter(re.findall(r"(?<![\w@])@[\w.]+", text)))


class PresentationTextError(ValueError):
    """Only authored prose is correctable; source/shape/binding faults stay fatal."""
    def __init__(self, issues):
        self.issues = issues
        super().__init__("; ".join(item["error"] for item in issues))


def translated_text(source: str, text, where: str) -> str:
    def reject(message):
        raise PresentationTextError([{"field": f"{where}.text", "source": source,
                                      "error": f"{where}.text {message}"}])

    if not isinstance(text, str) or (source and not text.strip()) or (not source and text != ""):
        reject("must translate the complete source (empty only for an empty source)")
    if len(text) > 4000 or any(ord(char) < 32 for char in text):
        reject("must be bounded inline prose")
    # Compare decimal digit values, retaining original URL/handle spellings and source bindings.
    # Date equivalence only explains a numeric spelling difference; it is not a semantic validator.
    if (literal_tokens(source) != literal_tokens(text)
            and literal_tokens(source, calendar_dates=True) != literal_tokens(text, calendar_dates=True)):
        def shown(counter):
            return sorted(counter.elements())
        source_numbers, source_urls, source_handles = literal_tokens(source)
        text_numbers, text_urls, text_handles = literal_tokens(text)
        reject("must preserve source numeric, URL and handle literals: the source has numbers "
               f"{shown(source_numbers)}, URLs {shown(source_urls)} and handles {shown(source_handles)}; the text has "
               f"numbers {shown(text_numbers)}, URLs {shown(text_urls)} and handles {shown(text_handles)}. "
               "Translate only this source text; never add numbers, links or handles from outside it")
    return text


def text_presentation_contract(contract: dict) -> dict:
    """Author only prose; bind immutable sources to the exact layers bytes at render time."""
    return {
        "schema_version": TEXT_PRESENTATION_SCHEMA,
        "language": contract["language"],
        "layers_sha256": contract["layers_sha256"],
        "labels": {key: {"text": item["text"]} for key, item in contract["labels"].items()},
        "rows": [{"slot": item["slot"], "text": item["text"]} for item in contract["rows"]],
    }


def validate_presentation(layers: dict, presentation: dict, language: str, layers_sha256: str):
    expected = presentation_contract(layers, language, layers_sha256)
    if not isinstance(presentation, dict) or set(presentation) != set(expected):
        raise ValueError("presentation must have exactly the generated contract fields")
    text_only = presentation["schema_version"] == TEXT_PRESENTATION_SCHEMA
    if presentation["schema_version"] not in (PRESENTATION_SCHEMA, TEXT_PRESENTATION_SCHEMA):
        raise ValueError("presentation.schema_version does not match this report")
    for key in ("language", "layers_sha256"):
        if presentation[key] != expected[key]:
            raise ValueError(f"presentation.{key} does not match this report")
    labels = presentation["labels"]
    if not isinstance(labels, dict) or set(labels) != set(expected["labels"]):
        raise ValueError("presentation labels must match the complete generated key set")
    # Check every immutable field before classifying any prose as correctable. A bad
    # later row must not be hidden by an earlier label's missing translation.
    texts = []
    for key, original in expected["labels"].items():
        item = labels[key]
        fields = {"text"} if text_only else set(original)
        if not isinstance(item, dict) or set(item) - {"text"} != fields - {"text"}:
            raise ValueError(f"labels.{key} must retain its generated fields")
        if not text_only and any(item[field] != original[field] for field in ("source", "source_sha256")):
            raise ValueError(f"labels.{key} source binding changed")
        texts.append(("label", key, original["source"], item.get("text"), f"labels.{key}"))
    rows = presentation["rows"]
    if not isinstance(rows, list) or len(rows) != len(expected["rows"]):
        raise ValueError("presentation must contain every displayed row exactly once")
    # Order is part of the generated contract, so duplicates, omissions and reordering all fail.
    for item, original in zip(rows, expected["rows"]):
        fields = {"slot", "text"} if text_only else set(original)
        if not isinstance(item, dict) or set(item) - {"text"} != fields - {"text"}:
            raise ValueError("presentation row must retain its generated fields")
        if any(item[field] != original[field] for field in fields if field != "text"):
            raise ValueError(f"presentation row {original['slot']} source binding changed")
        texts.append(("row", original["slot"], original["source_excerpt"], item.get("text"), original["slot"]))
    output_labels, output_rows, issues = {}, {}, []
    for kind, key, source, text, where in texts:
        try:
            value = translated_text(source, text, where)
        except PresentationTextError as error:
            issues.extend(error.issues)
        else:
            (output_labels if kind == "label" else output_rows)[key] = value
    if issues:
        raise PresentationTextError(issues)
    return output_labels, output_rows


def prose(value: str) -> str:
    # Authored text is inline data, never a Markdown headline, link, image or new report section.
    escaped = re.sub(r"([\\`*_{}\[\]()#!])", r"\\\1", str(value))
    return cell(escaped)


def cell(value) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("|", "&#124;")
        .replace("\n", " ")
        .replace("\r", " ")
    )


def views_text(views) -> str:
    if type(views) is not int:
        return "plays unknown"
    return f"{views:,} plays"


def row_line(item: dict) -> str:
    handle = cell(item.get("handle") or "unknown")
    title = cell((item.get("title") or "")[:80] or "(no title)")
    published = cell(item.get("published_at") or "date unknown")
    url = cell(item.get("url") or "")
    return f"- @{handle} · {published} · {views_text(item.get('views'))} — {title} {url}".rstrip()


def render_presentation(layers: dict, presentation: dict, language: str, layers_sha256: str) -> str:
    labels, titles = validate_presentation(layers, presentation, language, layers_sha256)
    window = layers.get("window") or {}
    heat = layers.get("heat") or []
    inside = layers.get("in_window") or []
    since, until = cell(window.get("since")), cell(window.get("until"))

    def line(slot: str, item: dict) -> str:
        # Only prose is authored. Identity, precision, order and measured values remain mechanical.
        handle = cell(item.get("handle")) if item.get("handle") else prose(labels["unknown_handle"])
        published = cell(item.get("published_at")) if item.get("published_at") else prose(labels["unknown_date"])
        views = item.get("views")
        measured = f"{views:,} {prose(labels['plays_unit'])}" if type(views) is int else prose(labels["unknown_plays"])
        title = prose(titles[slot] if item.get("title") else labels["no_title"])
        return f"- @{handle} · {published} · {measured} — {title} {cell(item.get('url') or '')}".rstrip()

    lines = [f"# {prose(labels['title_prefix'])} {since} → {until}", "",
             prose(labels["intro"]), "", f"## {prose(labels['heat_heading'])}"]
    lines.extend(line(slot, item) for slot, item in displayed_rows(layers) if slot.startswith("heat:"))
    if not heat:
        lines.append(f"- {prose(labels['empty_heat'])}")
    lines.extend(["", f"## {prose(labels['window_heading'])} ({since} → {until})", ""])
    if inside:
        lines.extend(line(slot, item) for slot, item in displayed_rows(layers) if slot.startswith("in_window:"))
    else:
        lines.append(prose(labels["empty_window"]))
    if report_limitations(layers):
        lines.extend(["", f"## {prose(labels['limitations_heading'])}", ""])
        lines.extend(f"- {prose(labels[f'limitation:{index}'])}"
                     for index, _ in enumerate(report_limitations(layers)))
    return "\n".join(lines) + "\n"


def render(layers: dict, language=None, presentation=None, layers_sha256=None) -> str:
    """Without a presentation, preserve the published output, including ignored language args."""
    if presentation is not None:
        return render_presentation(layers, presentation, language, layers_sha256)
    if layers.get("schema_version") != "short-window-layers.v1":
        raise ValueError("input must be short-window-layers.v1")
    window = layers.get("window") or {}
    heat = layers.get("heat") or []
    inside = layers.get("in_window") or []
    if not isinstance(heat, list) or not isinstance(inside, list):
        raise ValueError("heat and in_window must be arrays")
    since = cell(window.get("since"))
    until = cell(window.get("until"))
    labels = report_labels(layers)
    lines = [
        f"# {labels['title_prefix']} {since} → {until}",
        "",
        (labels["intro"] if layers.get("source_policy") else
         "The heat layer is ordered by public plays and always shows the publish time. **Do not read those rows as in-window.**"),
        "",
        f"## {labels['heat_heading']}",
    ]
    lines.extend(row_line(item) for item in heat[:12] if isinstance(item, dict))
    if not heat:
        lines.append("- No dated public posts in this layer either.")
    lines.extend(["", f"## Inside the window ({since} → {until})", ""])
    if inside:
        lines.extend(row_line(item) for item in inside[:12] if isinstance(item, dict))
    else:
        lines.append(labels["empty_window"])
    notes = report_limitations(layers)
    if notes:
        lines.extend(["", "## Coverage and limitations", ""])
        lines.extend(f"- {prose(note)}" for note in notes)
    return "\n".join(lines) + "\n"


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate presentation field: {key}")
        result[key] = value
    return result


def paths_alias(left: Path, right: Path) -> bool:
    """Resolve symlinks and existing hard links before protecting report inputs."""
    return left.resolve() == right.resolve() or (
        left.exists() and right.exists() and left.samefile(right))


def require_open_run(layers: Path) -> None:
    """Respect a local reservation when present; Web/legacy need no invented manifest."""
    cwd = Path.cwd().resolve()
    directories = {cwd}
    def within_cwd(path):
        try:
            path.relative_to(cwd)
            return True
        except ValueError:
            return False

    # The host cwd is authoritative even when layers live in analysis/ or are a
    # symlink. Inspect only the input parents and ancestors bounded by that cwd;
    # never search unrelated home/work directories for a reservation.
    for parent in (Path(os.path.abspath(layers)).parent, layers.resolve().parent):
        directories.add(parent)
        if within_cwd(parent):
            directories.update(ancestor for ancestor in parent.parents
                               if within_cwd(ancestor))
    for directory in sorted(directories):
        reservation = directory / "run-manifest.json"
        try:
            reservation.lstat()
        except FileNotFoundError:
            continue
        if reservation.is_symlink():
            raise ValueError("authoring lifecycle cannot use a symlinked run reservation")
        record = json.loads(reservation.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
        if not isinstance(record, dict) or record.get("status") != "running":
            raise ValueError("authoring correction requires an open run; never reopen a terminal run")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("layers", type=Path)
    parser.add_argument("output", type=Path, nargs="?")
    parser.add_argument("--language", help="explicit delivery language for a presentation; older calls keep English labels")
    parser.add_argument("--print-presentation-contract", action="store_true")
    parser.add_argument("--contract-output", type=Path, help="exclusively create the generated skeleton; never overwrite one")
    parser.add_argument("--text-only", action="store_true", help="print mode: create a prose-only skeleton; show immutable author sources on stdout")
    parser.add_argument("--presentation", type=Path, help="complete model-authored text bound to the generated skeleton")
    parser.add_argument("--export-deliverable", type=Path, help="also write the same validated UTF-8 report bytes to the host's deliverable path")
    lifecycle = parser.add_mutually_exclusive_group()
    lifecycle.add_argument("--allow-authoring-correction", action="store_true",
                           help="keep an open authoring stage unfinished for correctable prose; valid reports render normally")
    lifecycle.add_argument("--verify-report", action="store_true",
                           help="strictly recompute and verify the existing report without rewriting it")
    args = parser.parse_args()
    try:
        raw = args.layers.read_bytes()
        layers = json.loads(raw.decode("utf-8"))
        if args.print_presentation_contract:
            if args.output or args.presentation or args.export_deliverable or args.allow_authoring_correction or args.verify_report:
                raise ValueError("print mode cannot write a report or consume a presentation")
            contract = presentation_contract(layers, args.language, digest(raw))
            skeleton = text_presentation_contract(contract) if args.text_only else contract
            if args.contract_output:
                args.contract_output.parent.mkdir(parents=True, exist_ok=True)
                with args.contract_output.open("x", encoding="utf-8") as stream:
                    stream.write(json.dumps(skeleton, ensure_ascii=False, indent=2) + "\n")
            result = {"status": "needs_presentation", "contract": skeleton,
                      "path": str(args.contract_output) if args.contract_output else None}
            if args.text_only:
                result["presentation_schema"] = skeleton["schema_version"]
                # The writable contract must be identical to the generated file.
                # Keep complete translation sources separate, keyed by label/slot;
                # evidence hashes are derived from layers, never retyped by authors.
                result["authoring_sources"] = {
                    "labels": {key: item["source"] for key, item in contract["labels"].items()},
                    "rows": [{"slot": item["slot"], "source_excerpt": item["source_excerpt"]}
                             for item in contract["rows"]],
                }
            print(json.dumps(result, ensure_ascii=False))
            return 0
        if not args.output or args.contract_output or args.text_only:
            raise ValueError("final render needs an output; --contract-output belongs only to print mode")
        if args.allow_authoring_correction or args.verify_report:
            if not args.presentation:
                raise ValueError("authoring lifecycle modes require a bound presentation")
            require_open_run(args.layers)
        if args.verify_report and args.export_deliverable:
            raise ValueError("report verification is read-only; export only in the successful render stage")
        if args.export_deliverable:
            inputs = [args.layers]
            if args.presentation:
                inputs.append(args.presentation)
            if (any(paths_alias(args.output, item) for item in inputs)
                    or any(paths_alias(args.export_deliverable, item) for item in [*inputs, args.output])):
                raise ValueError("deliverable export must not replace its inputs or primary report")
        if args.presentation:
            if args.output.resolve() in (args.layers.resolve(), args.presentation.resolve()):
                raise ValueError("report output must not replace its layers or presentation")
            presentation = json.loads(args.presentation.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
            try:
                text = render(layers, args.language, presentation, digest(raw))
            except PresentationTextError as error:
                if not args.allow_authoring_correction:
                    raise
                # No report/export writes, directory creation, fetch or run mutation precede
                # this return. Only the named authored text may change in this same run.
                arguments = [str(args.layers), str(args.output), "--language", args.language,
                             "--presentation", str(args.presentation), "--allow-authoring-correction"]
                if args.export_deliverable:
                    arguments.extend(["--export-deliverable", str(args.export_deliverable)])
                print(json.dumps({"status": "needs_correction", "corrections": error.issues,
                                  "next_stage": {"script": "scripts/render_short_window_report.py",
                                                 "arguments": arguments},
                                  "message": "Unfinished authoring: correct only the named text once against its source, then repeat this stage once in the same open run. If correction is still needed, stop and report the unfinished translation. Do not refetch, reopen a terminal run or deliver an earlier report."}, ensure_ascii=False))
                return 0
        else:
            text = render(layers, args.language)
        if args.verify_report:
            if args.output.is_symlink():
                raise ValueError("report verification cannot follow a symlink")
            if args.output.read_bytes() != text.encode("utf-8"):
                raise ValueError("existing report does not match the strictly validated current layers and presentation")
            print(json.dumps({"status": "ok", "path": str(args.output)}))
            return 0
        args.output.parent.mkdir(parents=True, exist_ok=True)
        if args.export_deliverable:
            args.export_deliverable.parent.mkdir(parents=True, exist_ok=True)
            report_bytes = text.encode("utf-8")
            args.output.write_bytes(report_bytes)
            args.export_deliverable.write_bytes(report_bytes)
        else:
            args.output.write_text(text, encoding="utf-8")
        print(json.dumps({"status": "ok", "path": str(args.output)}))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "failed", "error": f"{type(error).__name__}: {error}"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
