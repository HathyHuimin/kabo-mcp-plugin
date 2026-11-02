#!/usr/bin/env python3
"""Validate that the delivered Markdown is the deterministic report projection."""

from __future__ import annotations

import sys

# Direct execution imports the sibling Composer below. The signed Skill directory is immutable
# evidence, so importing that sibling must not leave interpreter cache files beside its sources.
# Set the interpreter switch here, before the sibling import, instead of relying on the runner to
# supply PYTHONDONTWRITEBYTECODE. Limit it to the CLI path so importing this module as a library does
# not change bytecode policy for the host process.
if __name__ == "__main__":
    sys.dont_write_bytecode = True

import argparse
import json
import re
from pathlib import Path
from typing import Any, Iterator

from compose_review import PUBLIC_PROVIDER_LABELS, growth_for_review, load, render_markdown, report_scope
from estimate_growth import review_evidence


# This file ships to user machines, so it may not carry a list of supplier names: writing the
# vendors down here would publish exactly what the check exists to keep out of the report. The
# guard is therefore built from three things that are all knowable without such a list.
#
# 1. Field names of the pipeline's own lineage record. A Creator-facing report has no reason to
#    contain the words themselves.
STRUCTURAL_MARKERS = ("connector_id", "provider", "package_sha256", "source_commit")

# 2. Identifier *values* read out of the run's own documents. render_markdown() never projects any
#    of these keys, so one of their values appearing in the delivered Markdown means the semantic
#    step wrote it in by hand - which is how lineage escapes. Reading the value rather than pinning
#    a literal is what makes this survive a renamed or added source: it is covered the day it
#    appears in the run, not the day someone remembers to edit a constant.
#
#    `source_mode` is deliberately absent. Its values are the enum "public"/"owner", and "public" is
#    an ordinary word in a report about public content; banning it would fail valid deliveries.
IDENTIFIER_KEYS = (
    "connector_id", "provider", "package_sha256", "source_commit",
    "snapshot_id", "analysis_id", "report_id", "profile_id", "raw_sha256", "rule_version",
)
# Short values are enum-like or collide with ordinary words; only opaque identifiers are banned.
MIN_IDENTIFIER_LEN = 8

# 3. Shapes that only a tool, a package, a digest or an off-evidence host has. The pre-rename
#    version of this tuple matched `<something>-connector` / `<something>-adapter`, which is dead
#    twice over: the connectors are keyed on capability labels now, and a capability label is the
#    one source word the report is *allowed* to use (the runner is told to answer "where did this
#    come from" with the label). These match what a leak actually looks like in prose instead.
LINEAGE_PATTERNS = (
    ("digest or commit hash", re.compile(r"\b[0-9a-f]{40,64}\b", re.I)),
    ("packaged tool", re.compile(r"(?<![\w/])@[a-z0-9][\w.-]*/[\w.-]+", re.I)),
    ("pinned tool version", re.compile(r"\b[a-z][\w.-]{2,}@\d+(?:\.\d+)+\b", re.I)),
    ("shell invocation", re.compile(r"(?<![\w-])(?:npx|pipx|pip3?|python3?|node|curl|wget|bash)\s+[-\w@./]{2,}", re.I)),
)

# Hosts of the public platforms these reports are *about*. Naming one leaks nothing - the whole
# report is openly an analysis of that platform - so they are the only hosts allowed on top of the
# report's own evidence URLs. Anything else is either a supplier's domain or a link the
# deterministic projection did not put there.
PLATFORM_HOSTS = frozenset({
    "youtube.com", "youtu.be", "instagram.com", "instagr.am", "tiktok.com", "vm.tiktok.com",
})
URL_HOST = re.compile(r"(?:https?://|www\.)([a-z0-9](?:[a-z0-9.-]*[a-z0-9])?)", re.I)
BARE_HOST = re.compile(
    r"(?<![\w.@/-])((?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+"
    r"(?:com|net|org|io|ai|dev|app|co|sh|xyz|me|tv|cc|gg|so|to|fm))(?![\w-])",
    re.I,
)


def walk(value: Any) -> Iterator[tuple[str | None, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield key, child
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def source_identity_values(*documents: Any) -> list[str]:
    """Run-internal identifier strings, read from the run's own documents.

    No name is written down here; whatever this run carries under a lineage key is what must not
    reach the Creator. Values shorter than MIN_IDENTIFIER_LEN are skipped: they are enums, not
    identities, and matching them against free prose produces false failures rather than catches.
    """
    found: list[str] = []
    for document in documents:
        for key, child in walk(document):
            if key not in IDENTIFIER_KEYS or not isinstance(child, str):
                continue
            text = child.strip()
            if len(text) >= MIN_IDENTIFIER_LEN and text not in found:
                found.append(text)
    return found


def envelope_identity_values(envelopes: list[dict]) -> list[str]:
    """Supplier strings as the platform itself reported them for this run.

    The stored connector envelopes are the audit record and do name their source. Passing them in
    restores a literal supplier check without any supplier name shipping inside this package. Only
    an exact top-level public connector/provider pair is excluded; nested and unknown identities
    remain guarded. Optional - the shape and identifier layers stand when no envelope is passed.
    """
    found: list[str] = []
    for envelope in envelopes:
        source = envelope
        connector = envelope.get("connector_id")
        public_label = PUBLIC_PROVIDER_LABELS.get(connector) if isinstance(connector, str) else None
        if public_label is not None and envelope.get("provider") == public_label:
            source = {key: value for key, value in envelope.items() if key != "provider"}
        for key, child in walk(source):
            if key not in ("connector_id", "provider") or not isinstance(child, str):
                continue
            text = child.strip()
            if len(text) >= 3 and text not in found:
                found.append(text)
    return found


def evidence_hosts(report: dict) -> set[str]:
    """Hosts of the URLs the deterministic projection itself puts in the Markdown."""
    hosts = set(PLATFORM_HOSTS)
    for _, child in walk(report):
        if not isinstance(child, str):
            continue
        for match in URL_HOST.finditer(child):
            hosts.add(match.group(1).lower().removeprefix("www."))
    # 示例标题由组装脚本从证据原样抄来（如帖文里的「Ridge.com/MKBHD」），其中的裸域名是来源文字，不是泄露；
    # 模型写的分析文字不在此列，仍按外来域名拦。
    for key in ("what_works", "what_hurts"):
        for pattern in report.get(key) or []:
            for example in pattern.get("examples") or []:
                for match in BARE_HOST.finditer(str(example.get("title") or "")):
                    hosts.add(match.group(1).lower().removeprefix("www."))
    return hosts


def foreign_hosts(markdown: str, allowed: set[str]) -> list[str]:
    seen: list[str] = []
    for pattern in (URL_HOST, BARE_HOST):
        for match in pattern.finditer(markdown):
            host = match.group(1).lower().removeprefix("www.")
            if host in allowed or host in seen:
                continue
            if any(host.endswith(f".{permitted}") for permitted in allowed):
                continue
            seen.append(host)
    return seen


# A stated figure: digits with optional thousands separators and decimals, and an optional 万/K/M scale.
FIGURE = re.compile(r"(?<![0-9A-Za-z_.,])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?\s*(万|[KkMm](?![A-Za-z]))?")
SCALES = {"万": 1e4, "k": 1e3, "m": 1e6}


def stated_figures(text: str) -> Iterator[tuple[float, float]]:
    """Each figure with three or more significant digits, as (value, half of its last stated unit)."""
    for whole, fraction, unit in FIGURE.findall(text):
        digits = (whole.replace(",", "") + fraction).lstrip("0")
        if len(digits) < 3:
            continue
        scale = SCALES.get(unit.lower(), 1) if unit else 1
        yield float(whole.replace(",", "") + ("." + fraction if fraction else "")) * scale, 0.5 * 10 ** -len(fraction) * scale


def growth_figures(result: dict) -> list[float]:
    if result.get("status") != "computed":
        return []
    return [result["estimate"]["value"], result["basis"]["plan"]["posts"], *result["estimate"]["sensitivity"].values()]


def strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def conflicting_growth_figures(synthesis: dict, growth: dict, request: dict, snapshot: dict) -> list[float]:
    """Figures from another assessed growth choice that the synthesis states instead of the bound one.

    The synthesis is written by a model that also saw `--assess`; a total, item count or quartile total
    from a different plan would contradict the deterministic section beside it.
    """
    scope = request["scope"]
    evidence = review_evidence({"schema_version": "growth-review.v1",
                                "scope": {"platform": scope["platform"], "account": scope["account"]},
                                "horizon_days": request["horizon_days"], "metrics": [scope["metric"]]},
                               snapshot, "snapshot.json")
    bound = growth_figures(growth["result"])
    others = [figure for entry in evidence["metrics"] for candidate in entry["candidates"]
              for figure in growth_figures(candidate)]
    found = []
    for text in strings(synthesis):
        for value, half in stated_figures(text):
            def near(figure: float) -> bool:
                return abs(figure - value) <= half + 1e-9
            if any(map(near, others)) and not any(map(near, bound)):
                found.append(value)
    return found


def validate(report: dict, synthesis: dict, markdown: str, envelopes: list[dict] | None = None,
             *, analysis: dict | None = None, growth_input: dict | None = None,
             growth_snapshot: dict | None = None, growth_question: str | None = None) -> None:
    has_inputs = any(value is not None for value in (growth_input, growth_snapshot, growth_question))
    if "growth_estimate" in report or has_inputs:
        if analysis is None or not has_inputs:
            raise ValueError("growth report validation requires analysis and original growth inputs")
        if report.get("scope") != report_scope(analysis):
            raise ValueError("growth report scope differs from deterministic analysis")
        expected_growth = growth_for_review(analysis, growth_input, growth_snapshot, growth_question)
        if report.get("growth_estimate") != expected_growth:
            raise ValueError("report growth differs from its bound deterministic calculation")
        conflicts = conflicting_growth_figures(synthesis, expected_growth, growth_input, growth_snapshot)
        if conflicts:
            raise ValueError(f"synthesis states growth figures from another assessed choice: {conflicts}; "
                             "state the bound figures printed by drive.py growth")
    expected = render_markdown(report, synthesis.get("output_language", "en"))
    if markdown != expected:
        raise ValueError("creator-facing Markdown differs from the deterministic report projection")

    urls = {
        example.get("url")
        for key in ("what_works", "what_hurts")
        for pattern in report.get(key, [])
        for example in pattern.get("examples", [])[:3]
        if example.get("url")
    }
    missing_urls = sorted(url for url in urls if url not in markdown)
    if missing_urls:
        raise ValueError(f"creator-facing Markdown is missing evidence URLs: {missing_urls}")

    missing_actions = [
        item["action"]
        for key in ("continue", "stop", "test")
        for item in (report.get("recommendations") or {}).get(key, [])
        if item["action"] not in markdown
    ]
    if missing_actions:
        raise ValueError(f"creator-facing Markdown is missing recommended actions: {missing_actions}")

    lowered = markdown.lower()
    exposed = [token for token in STRUCTURAL_MARKERS if token in lowered]
    for value in source_identity_values(report, synthesis) + envelope_identity_values(envelopes or []):
        if value.lower() in lowered and value not in exposed:
            exposed.append(value)
    exposed += [
        f"{label}: {match.group(0)}"
        for label, pattern in LINEAGE_PATTERNS
        for match in [pattern.search(markdown)] if match
    ]
    exposed += [
        f"host outside this run's evidence: {host}"
        for host in foreign_hosts(markdown, evidence_hosts(report))
    ]
    if exposed:
        raise ValueError(f"creator-facing Markdown exposes internal implementation details: {exposed}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--synthesis", type=Path, required=True)
    parser.add_argument("--markdown", type=Path, required=True)
    parser.add_argument(
        "--envelope", type=Path, action="append", default=[],
        help="Repeatable. A connector evidence envelope stored by this run; its source strings become "
             "forbidden literals in the Markdown. Pass every envelope the run collected.",
    )
    parser.add_argument("--analysis", type=Path)
    parser.add_argument("--growth-input", type=Path)
    parser.add_argument("--growth-snapshot", type=Path)
    parser.add_argument("--growth-question-file", type=Path)
    args = parser.parse_args()
    try:
        validate(
            load(args.report),
            load(args.synthesis),
            args.markdown.read_text(encoding="utf-8"),
            [load(path) for path in args.envelope],
            analysis=load(args.analysis) if args.analysis else None,
            growth_input=load(args.growth_input) if args.growth_input else None,
            growth_snapshot=load(args.growth_snapshot) if args.growth_snapshot else None,
            growth_question=args.growth_question_file.read_text(encoding="utf-8") if args.growth_question_file else None,
        )
        print(json.dumps({"status": "passed"}))
        return 0
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "failed", "error": f"{type(error).__name__}: {error}"}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
