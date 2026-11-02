#!/usr/bin/env python3
"""Validate the bounded, natural TikTok Trend query plan."""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any


# Scripts written without spaces between words (Han, Kana, Thai, Lao, Khmer, Myanmar). A natural
# query in them is one unbroken run, so its length is counted in characters instead of words.
UNSPACED_SCRIPT = re.compile(
    "[\u0e00-\u0eff\u1000-\u109f\u1780-\u17ff\u3040-\u30ff\u31f0-\u31ff"
    "\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff66-\uff9f\U00020000-\U0002fa1f]"
)
UNSPACED_MIN_CHARACTERS = 2
UNSPACED_MAX_CHARACTERS = 20


def _profile_intake():
    """This package's own attach_creator_profile.py: the seed matching every trend channel shares.

    Loaded by path, never through sys.modules, so a process that loaded another package's validator
    cannot hand this one a different copy; no bytecode is written into the signed package.
    """
    sys.dont_write_bytecode = True
    path = Path(__file__).with_name("attach_creator_profile.py")
    spec = importlib.util.spec_from_file_location(f"{path.parent.parent.name}:attach_creator_profile", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def unspaced_length(query: str) -> int | None:
    """Non-space character count when the query uses an unspaced script, otherwise None."""
    if not UNSPACED_SCRIPT.search(query):
        return None
    return len(re.sub(r"\s+", "", query))


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value


def validate(plan: dict[str, Any]) -> None:
    if set(plan) != {"schema_version", "request_id", "intents"}:
        raise ValueError("plan must contain only schema_version, request_id and intents")
    if plan.get("schema_version") != "tiktok-trend-query-plan.v1":
        raise ValueError("schema_version must be tiktok-trend-query-plan.v1")
    if not isinstance(plan.get("request_id"), str) or not plan["request_id"].strip():
        raise ValueError("request_id must be non-empty")
    intents = plan.get("intents")
    if not isinstance(intents, list) or not 1 <= len(intents) <= 2:
        raise ValueError("intents must contain one or two entries")
    seen: set[str] = set()
    for index, intent in enumerate(intents):
        if not isinstance(intent, dict) or set(intent) != {"intent_id", "search_query"}:
            raise ValueError(f"intents[{index}] must contain only intent_id and search_query")
        intent_id = intent.get("intent_id")
        query = intent.get("search_query")
        if not isinstance(intent_id, str) or not 1 <= len(intent_id.strip()) <= 64:
            raise ValueError(f"intents[{index}].intent_id is invalid")
        if intent_id in seen:
            raise ValueError(f"duplicate intent_id: {intent_id}")
        seen.add(intent_id)
        if not isinstance(query, str) or not 2 <= len(query.strip()) <= 80:
            raise ValueError(f"intents[{index}].search_query is invalid")
        # A query that uses an unspaced script passes on its character count; a query that already
        # passed as spaced words (mixed scripts included) keeps passing.
        characters = unspaced_length(query)
        if characters is not None and UNSPACED_MIN_CHARACTERS <= characters <= UNSPACED_MAX_CHARACTERS:
            continue
        terms = [term for term in re.split(r"\s+", query.strip()) if term]
        if not 2 <= len(terms) <= 5:
            if characters is not None:
                raise ValueError(
                    f"intents[{index}].search_query must contain {UNSPACED_MIN_CHARACTERS} to "
                    f"{UNSPACED_MAX_CHARACTERS} characters in an unspaced script, or two to five terms"
                )
            raise ValueError(f"intents[{index}].search_query must contain two to five terms")


def validate_against_request(plan: dict[str, Any], request: dict[str, Any]) -> dict[str, Any] | None:
    """Enforce the Creator's exclusions; the host judges queries against Request.niche."""
    queries = [str(intent.get("search_query") or "") for intent in plan.get("intents") or []]
    return {"excluded_topics_checked": _profile_intake().check_excluded(queries, request)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("plan", type=Path)
    parser.add_argument("--request", type=Path,
                        help="TrendResearchRequest; enforce excluded topics without requiring historical seed terms")
    args = parser.parse_args()
    try:
        value = load(args.plan)
        validate(value)
        result: dict[str, Any] = {"valid": True, "intents": len(value["intents"])}
        if args.request is not None:
            checked = validate_against_request(value, load(args.request))
            if checked is not None:
                result.update(checked)
        print(json.dumps(result))
        return 0
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        print(json.dumps({"valid": False, "error": f"{type(error).__name__}: {error}"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
