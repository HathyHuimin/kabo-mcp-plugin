#!/usr/bin/env python3
"""Load one complete JSON value. Truncated or empty files fail closed."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ENVELOPE_MARKERS = ("connector_id", "operation", "provider")


def load_complete_json(path: Path) -> Any:
    raw = path.read_text(encoding="utf-8")
    if not raw.strip():
        raise ValueError(f"{path}: empty file; not a complete JSON document")
    try:
        value, end = json.JSONDecoder().raw_decode(raw.lstrip())
    except json.JSONDecodeError as error:
        raise ValueError(f"{path}: truncated or invalid JSON ({error})") from error
    trailing = raw.lstrip()[end:].strip()
    if trailing:
        raise ValueError(f"{path}: trailing text after the first JSON value")
    return value


def load_complete_object(path: Path) -> dict[str, Any]:
    value = load_complete_json(path)
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def load_complete_envelope(path: Path) -> dict[str, Any]:
    envelope = load_complete_object(path)
    if any(key in envelope for key in ENVELOPE_MARKERS) and "data" not in envelope:
        raise ValueError(f"{path}: connector envelope is missing data; do not compose from a leftover file")
    return envelope


if __name__ == "__main__":
    raise SystemExit(0)
