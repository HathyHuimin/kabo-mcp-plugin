#!/usr/bin/env python3
"""Validate the minimal provider-compatible YouTube Trend query plan."""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any


CONNECTORS = {
    "a", "an", "and", "for", "from", "how", "in", "of", "on", "the", "to", "with",
    "a", "as", "com", "como", "da", "das", "de", "do", "dos", "e", "em", "na", "nas",
    "no", "nos", "o", "os", "para", "por",
    "avec", "comment", "dans", "de", "des", "du", "et", "la", "le", "les", "pour",
    "con", "como", "de", "del", "el", "en", "la", "las", "los", "para", "por", "y",
}


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


def is_acronym_or_code(term: str) -> bool:
    cleaned = re.sub(r"[^A-Za-z0-9]", "", term)
    if not 2 <= len(cleaned) <= 8:
        return False
    return cleaned.upper() == cleaned and any(character.isalpha() for character in cleaned)


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value


def bounded_text(value: Any, field: str, minimum: int, maximum: int) -> str:
    if not isinstance(value, str) or not minimum <= len(value.strip()) <= maximum:
        raise ValueError(f"{field} must contain {minimum} to {maximum} characters")
    return value.strip()


def validate(plan: dict[str, Any]) -> None:
    if set(plan) != {"schema_version", "request_id", "intents"}:
        raise ValueError("query plan must contain only schema_version, request_id, and intents")
    if plan.get("schema_version") != "trend-query-plan.v1":
        raise ValueError("schema_version must be trend-query-plan.v1")
    bounded_text(plan.get("request_id"), "request_id", 1, 10_000)
    intents = plan.get("intents")
    if not isinstance(intents, list) or not 1 <= len(intents) <= 2:
        raise ValueError("intents must contain one or two items")

    seen: set[str] = set()
    for index, intent in enumerate(intents):
        if not isinstance(intent, dict):
            raise ValueError(f"intents[{index}] must be an object")
        if set(intent) != {"intent_id", "search_query"}:
            raise ValueError(
                f"intents[{index}] must contain only intent_id and search_query"
            )
        intent_id = bounded_text(intent.get("intent_id"), f"intents[{index}].intent_id", 1, 64)
        if intent_id in seen:
            raise ValueError(f"duplicate intent_id: {intent_id}")
        seen.add(intent_id)
        query = bounded_text(intent.get("search_query"), f"intents[{index}].search_query", 2, 50)
        terms = query.split()
        # A query that uses an unspaced script passes on its character count; a query that already
        # passed as spaced words (mixed scripts included) keeps passing.
        characters = unspaced_length(query)
        by_characters = (
            characters is not None and UNSPACED_MIN_CHARACTERS <= characters <= UNSPACED_MAX_CHARACTERS
        )
        if not by_characters and not 2 <= len(terms) <= 9:
            if characters is not None:
                raise ValueError(
                    f"intents[{index}].search_query must contain {UNSPACED_MIN_CHARACTERS} to "
                    f"{UNSPACED_MAX_CHARACTERS} characters in an unspaced script, or 2 to 9 whitespace-delimited terms"
                )
            raise ValueError(
                f"intents[{index}].search_query must contain 2 to 9 whitespace-delimited terms"
            )
        normalized_terms = {re.sub(r"[^\w]", "", term).casefold() for term in terms}
        if not by_characters and len(terms) >= 6 and normalized_terms.isdisjoint(CONNECTORS):
            raise ValueError(
                f"intents[{index}].search_query with 6 to 9 terms must contain a grammatical connector"
            )
        if sum(is_acronym_or_code(term) for term in terms) >= 3:
            raise ValueError(
                f"intents[{index}].search_query must not contain three or more acronym/code tokens"
            )


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
        plan = load(args.plan)
        validate(plan)
        result: dict[str, Any] = {"valid": True, "intents": len(plan["intents"])}
        if args.request is not None:
            checked = validate_against_request(plan, load(args.request))
            if checked is not None:
                result.update(checked)
        print(json.dumps(result))
        return 0
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        print(
            json.dumps(
                {"valid": False, "error": f"{type(error).__name__}: {error}"},
                ensure_ascii=False,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
