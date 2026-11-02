#!/usr/bin/env python3
"""Turn the model's numbered author score lines into a `competitor-discovery-gate.v3`.

The Instagram discovery Gate is the model reading every projected author and scoring four fits.
Written as one JSON object per handle it is thousands of output tokens (measured 1.9k–4.3k per run,
35–64 s), none of which carries judgment beyond the four numbers. Here each author is one line,

    <n> <category_fit> <audience_job_fit> <sustained_focus> <locale_fit>

where `n` is the author's 1-based position in the printed table. This script checks that every
author is scored exactly once with integers 0–100 and writes the same Gate JSON `select_accounts.py`
already reads. A few authors left unscored (at most one in twenty) are scored 0, or
rejected on YouTube, and named in the output: refusing the whole Gate for one forgotten line of 68
spent the run's last correction and closed it (plugins, 2026-10-27).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

FIELDS = ("category_fit", "audience_job_fit", "sustained_focus", "locale_fit")


def tolerated(missing: list[int], total: int, unscored: list[int] | None) -> bool:
    """Whether these unscored numbers are few enough to fill in; records them in `unscored`."""
    if len(missing) > total // 20:
        return False
    if unscored is not None:
        unscored.extend(missing)
    return True


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected an object")
    return value


def build(gate_input: dict[str, Any], lines: str, unscored: list[int] | None = None) -> dict[str, Any]:
    if gate_input.get("schema_version") != "competitor-discovery-gate-input.v1":
        raise ValueError("input must be competitor-discovery-gate-input.v1")
    authors = gate_input.get("authors")
    if not isinstance(authors, list) or not authors:
        raise ValueError("gate input has no authors to score")
    scored: dict[int, dict[str, int]] = {}
    for line_no, raw in enumerate(lines.splitlines(), start=1):
        text = raw.split("#", 1)[0].strip()
        if not text:
            continue
        tokens = text.replace(",", " ").replace("\t", " ").split()
        if len(tokens) != 1 + len(FIELDS):
            raise ValueError(f"line {line_no}: expected `n {' '.join(FIELDS)}`, got {text!r}")
        head = tokens[0].rstrip(".")
        if not head.isdigit() or not 1 <= int(head) <= len(authors):
            raise ValueError(f"line {line_no}: author number {tokens[0]!r} is not between 1 and {len(authors)}")
        n = int(head)
        if n in scored:
            raise ValueError(f"line {line_no}: author {n} is scored twice")
        values = {}
        for field, token in zip(FIELDS, tokens[1:]):
            if not token.isdigit() or not 0 <= int(token) <= 100:
                raise ValueError(f"line {line_no}: {field} {token!r} is not an integer 0–100")
            values[field] = int(token)
        scored[n] = values
    missing = [n for n in range(1, len(authors) + 1) if n not in scored]
    if missing and tolerated(missing, len(authors), unscored):
        scored.update({n: {field: 0 for field in FIELDS} for n in missing})
    elif missing:
        preview = ", ".join(map(str, missing[:20])) + (" …" if len(missing) > 20 else "")
        raise ValueError(f"{len(missing)} author(s) not scored: {preview}; score every numbered line exactly once")
    return {
        "schema_version": "competitor-discovery-gate.v3",
        "request_id": str(gate_input.get("request_id") or ""),
        "items": [
            {"handle": str(author["handle"]), "semantic_scores": scored[index]}
            for index, author in enumerate(authors, start=1)
        ],
    }


LABELS = {"d": "direct_refs", "direct": "direct_refs", "a": "adjacent_refs", "adjacent": "adjacent_refs",
          "r": "reject_refs", "reject": "reject_refs"}


def build_labels(gate_input: dict[str, Any], lines: str, unscored: list[int] | None = None) -> dict[str, Any]:
    """The YouTube Gate: `<n> d|a|r` per numbered channel -> `competitor-discovery-gate.v2`.

    Each line used to be a 24-character channel id retyped into one of three arrays — 1.4k–1.5k output
    tokens a run, and one channel id written twice in two runs (one creator case, 2026-09-18/19), each
    costing a repair turn. The number maps back to the projected channel; a number given twice is
    refused the way a repeated id always was.
    """
    if gate_input.get("schema_version") != "competitor-discovery-gate-input.v1":
        raise ValueError("input must be competitor-discovery-gate-input.v1")
    authors = gate_input.get("authors")
    if not isinstance(authors, list) or not authors:
        raise ValueError("gate input has no channels to classify")
    labelled: dict[int, str] = {}
    for line_no, raw in enumerate(lines.splitlines(), start=1):
        text = raw.split("#", 1)[0].strip()
        if not text:
            continue
        tokens = text.replace(",", " ").replace("\t", " ").split()
        if len(tokens) != 2:
            raise ValueError(f"line {line_no}: expected `n d|a|r`, got {text!r}")
        head = tokens[0].rstrip(".")
        if not head.isdigit() or not 1 <= int(head) <= len(authors):
            raise ValueError(f"line {line_no}: channel number {tokens[0]!r} is not between 1 and {len(authors)}")
        n = int(head)
        if n in labelled:
            raise ValueError(f"line {line_no}: channel {n} appears more than once; classify every channel exactly once")
        label = LABELS.get(tokens[1].lower())
        if label is None:
            raise ValueError(f"line {line_no}: {tokens[1]!r} is not d (direct), a (adjacent) or r (reject)")
        labelled[n] = label
    missing = [n for n in range(1, len(authors) + 1) if n not in labelled]
    if missing and tolerated(missing, len(authors), unscored):
        labelled.update({n: "reject_refs" for n in missing})
    elif missing:
        preview = ", ".join(map(str, missing[:20])) + (" …" if len(missing) > 20 else "")
        raise ValueError(f"{len(missing)} channel(s) not classified: {preview}; classify every numbered line exactly once")
    gate: dict[str, Any] = {"schema_version": "competitor-discovery-gate.v2",
                            "request_id": str(gate_input.get("request_id") or ""),
                            "direct_refs": [], "adjacent_refs": [], "reject_refs": []}
    for index, author in enumerate(authors, start=1):
        gate[labelled[index]].append(str(author["handle"]))
    return gate


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True, help="competitor-discovery-gate-input.json")
    parser.add_argument("--scores", type=Path, required=True, help="numbered score lines")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--labels", action="store_true", help="YouTube: `n d|a|r` lines -> competitor-discovery-gate.v2")
    args = parser.parse_args()
    try:
        lines = args.scores.read_text(encoding="utf-8")
        unscored: list[int] = []
        gate = (build_labels(load(args.input), lines, unscored) if args.labels
                else build(load(args.input), lines, unscored))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(gate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        count = len(gate["items"]) if "items" in gate else sum(len(gate[k]) for k in ("direct_refs", "adjacent_refs", "reject_refs"))
        result: dict[str, Any] = {"status": "ok", "authors": count, "path": str(args.output)}
        if unscored:
            result["unscored"] = unscored
            result["note"] = (f"{len(unscored)} numbered line(s) were missing and count as "
                              f"{'reject' if args.labels else 'scored 0'}: {', '.join(map(str, unscored))}")
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "failed", "error": f"{type(error).__name__}: {error}"}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
