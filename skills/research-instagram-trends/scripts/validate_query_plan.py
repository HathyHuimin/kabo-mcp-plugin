#!/usr/bin/env python3
"""Validate a compact Instagram Trend query plan, including identifier uniqueness."""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value


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


def bounded_text(value: Any, field: str, minimum: int, maximum: int) -> str:
    if not isinstance(value, str) or not minimum <= len(value.strip()) <= maximum:
        raise ValueError(f"{field} must contain {minimum} to {maximum} characters")
    return value.strip()


MINIMUM_ANCHORED_INTENTS = 2
MINIMUM_VARIANT_INTENTS = 2

# How many intents may carry a `popular_topic` depends on the Request window, because how much
# of Popular lands inside the window is a function of the window rather than a property of the
# route. Against a one-month window every measured row resolved months to years early, which is
# what the old flat cap of one was calibrated on; against a one-year window seventeen of
# twenty-three resolved inside it. Sizing the route to the window is the whole point of the rule,
# so a caller that does not pass a Request gets the conservative short-window cap.
MAXIMUM_POPULAR_INTENTS_SHORT_WINDOW = 1
LONG_WINDOW_DAYS = 31


def normalized(value: str) -> str:
    return " ".join(value.lower().split())


def head_tokens(phrase: str) -> set[str]:
    """The tokens a variant has to keep for it to still be about the same thing.

    A noun phrase carries its head at one end and its qualifier at the other, and which end
    depends on the language — `visual styles` is head-final, `estilos de design` head-initial.
    Accepting either end keeps this check usable across the markets the skill serves without
    hard-coding a parser for each.
    """
    tokens = normalized(phrase).split()
    return {tokens[0], tokens[-1]} if tokens else set()


def stem(token: str) -> str:
    """Drop one trailing plural `s` so `styles` and `style` compare equal.

    Only the variant check uses this. The anchored check stays verbatim because SKILL.md
    requires those intents to repeat a caption phrase exactly.

    Measured 2026-09-17 against a real plan whose caption phrases said `VISUAL STYLES`:
    `design style transformations` and `aesthetic style names` were both rejected as off-format
    purely over the plural, and the variant floor then failed on a plan that was on format.
    Three of six real queries passed before, five after.

    Take off a single trailing `s`, never an `ss` ending, and never `es` as a unit — `es` is
    what the first attempt did, and it turns `styles` into `styl` while `style` stays put, so
    the two match even less than before.
    """
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def window_spans_more_than_a_month(request: dict[str, Any]) -> bool:
    """Whether the Request window is long enough for Popular to land inside it.

    An unreadable or absent window counts as short. Falling back to the tighter cap keeps a
    malformed Request from silently unlocking the wider budget.
    """
    window = request.get("window")
    if not isinstance(window, dict):
        return False
    bounds = []
    for field in ("since", "until"):
        value = window.get(field)
        if not isinstance(value, str):
            return False
        try:
            bounds.append(dt.datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            return False
    return (bounds[1] - bounds[0]) > dt.timedelta(days=LONG_WINDOW_DAYS)


def maximum_popular_intents(request: dict[str, Any] | None, intents: int) -> int:
    if request is not None and window_spans_more_than_a_month(request):
        return intents
    return MAXIMUM_POPULAR_INTENTS_SHORT_WINDOW


def caption_phrases(request: dict[str, Any]) -> list[str]:
    profile = request.get("creator_profile")
    if not isinstance(profile, dict):
        return []
    phrases = profile.get("observed_caption_phrases")
    if not isinstance(phrases, list):
        return []
    return [phrase.strip() for phrase in phrases if isinstance(phrase, str) and phrase.strip()]


def validate_against_request(plan: dict[str, Any], request: dict[str, Any]) -> None:
    """Require the plan to search both in the Creator's own wording and around it.

    Discovery matches literally against captions other creators write, so an intent phrased in
    analyst vocabulary retrieves a pool with no same-format content in it however accurately it
    describes the format. That is why some intents must repeat a caption phrase verbatim.

    But the Creator's wording is only one community's name for their format, so anchoring every
    intent on it retrieves a strict subset of that format: measured on one Creator, the variant
    `graphic design styles` surfaced nine confirmed same-format accounts where their own
    `visual styles` surfaced three. Variants must therefore keep a caption phrase's head token
    and change the qualifier, which is what reaches the rest of the community without drifting
    onto a different subject.

    A saved profile supplies optional vocabulary and preferences. It cannot replace the current
    Request.niche or require coverage of historical content classes. Source-post caption rules
    below apply only without a saved profile.
    """
    intake = _profile_intake()
    # 排除话题只检查一次；画像存在时不再强制历史 seed 或 caption phrase。
    intake.check_excluded([str(intent["search_query"]) for intent in plan["intents"]], request)
    if intake.profile_fields_attached(request):
        return
    phrases = caption_phrases(request)
    if not phrases:
        return
    known = {normalized(phrase) for phrase in phrases}
    heads = {token for phrase in phrases for token in head_tokens(phrase)}
    queries = [normalized(intent["search_query"]) for intent in plan["intents"]]

    anchored = [query for query in queries if query in known]
    required = min(MINIMUM_ANCHORED_INTENTS, len(known))
    if len(anchored) < required:
        raise ValueError(
            f"at least {required} search_query values must repeat an observed caption phrase "
            f"verbatim; got {len(anchored)} of {len(queries)} against {sorted(known)}"
        )

    # Compare the variant floor on stemmed tokens so a plural in the caption phrase does not
    # reject an on-format variant. The anchored floor above stays verbatim on purpose.
    stemmed_heads = {stem(token) for token in heads}
    variants = [
        query for query in queries
        if query not in known and stemmed_heads & {stem(token) for token in query.split()}
    ]
    # A short plan cannot carry both floors at once; ask only for the slots it actually has.
    wanted = min(MINIMUM_VARIANT_INTENTS, max(len(queries) - required, 0))
    if len(variants) < wanted:
        raise ValueError(
            f"at least {wanted} search_query values must keep a caption phrase's head token and "
            "change its qualifier, so discovery reaches the rest of the community making this "
            f"format; got {len(variants)} of {len(queries)} against head tokens {sorted(heads)}"
        )


def validate(plan: dict[str, Any], request: dict[str, Any] | None = None) -> None:
    if set(plan) != {"schema_version", "request_id", "intents"}:
        raise ValueError("query plan must contain only schema_version, request_id, and intents")
    if plan.get("schema_version") != "trend-query-plan.v1":
        raise ValueError("schema_version must be trend-query-plan.v1")
    bounded_text(plan.get("request_id"), "request_id", 1, 10_000)
    intents = plan.get("intents")
    if not isinstance(intents, list) or not 1 <= len(intents) <= 6:
        raise ValueError("intents must contain one to six items")
    seen: set[str] = set()
    popular = 0
    for index, intent in enumerate(intents):
        if not isinstance(intent, dict):
            raise ValueError(f"intents[{index}] must be an object")
        if not set(intent) <= {"intent_id", "search_query", "popular_topic"}:
            raise ValueError(
                f"intents[{index}] must contain only intent_id, search_query, and popular_topic"
            )
        intent_id = bounded_text(intent.get("intent_id"), f"intents[{index}].intent_id", 1, 64)
        if intent_id in seen:
            raise ValueError(f"duplicate intent_id: {intent_id}")
        seen.add(intent_id)
        bounded_text(intent.get("search_query"), f"intents[{index}].search_query", 2, 240)
        if "popular_topic" in intent:
            popular += 1
            bounded_text(intent.get("popular_topic"), f"intents[{index}].popular_topic", 1, 80)
    allowed = maximum_popular_intents(request, len(intents))
    if popular > allowed:
        raise ValueError(
            f"at most {allowed} intent(s) may carry a popular_topic for this Request window; "
            f"got {popular}. Popular is capped at "
            f"{MAXIMUM_POPULAR_INTENTS_SHORT_WINDOW} only while the window spans "
            f"{LONG_WINDOW_DAYS} days or fewer, because its all-time top posts then resolve "
            "outside the window; omit the extra popular_topic fields, without changing the "
            "frozen Request window. Passing --request is what lets this check read the window at all"
        )


DISCOVERY_WINDOWS = ("last-hour", "last-day", "last-week", "last-month", "last-year")


def collection_input(plan: dict[str, Any], request: dict[str, Any], window: str) -> dict[str, Any]:
    """Project the already-required validation step into the MCP input contract.

    Request locale is nullable; MCP optional strings must instead be omitted. The query-plan
    artifact and the exact Request window stay untouched. This does not choose a new source,
    expand the research window, or add a provider call.
    """
    validate(plan, request)
    validate_against_request(plan, request)
    if plan["request_id"] != request.get("request_id"):
        raise ValueError("query plan and Request request_id must match")
    if window not in DISCOVERY_WINDOWS:
        raise ValueError("discovery window must be a supported collector enum")
    result = {
        "platform": "instagram", "window": window,
        "intents": [dict(intent) for intent in plan["intents"]],
        "budget": {"search_pages_per_intent": 2, "popular_pages_per_intent": 1,
                   "challenger_pages_per_intent": 1, "max_provider_calls": 24,
                   "max_candidates": 160, "max_detail_calls": 0},
    }
    if _profile_intake().profile_fields_attached(request):
        # Audio and duration per Reel, for the eligibility filter and the Gate. Asked only with a
        # profile attached, so a run without one sends the collector exactly what it always did.
        result["include"] = ["media_signals"]
    locale = request.get("locale")
    if locale is not None and not isinstance(locale, dict):
        raise ValueError("locale must be an object or null")
    locale = locale or {}
    for name, maximum in (("region", 8), ("language", 16)):
        if locale.get(name) is not None:
            result[name] = bounded_text(locale[name], f"locale.{name}", 2, maximum)
    return result


def save_collection_input(path: Path, value: dict[str, Any]) -> None:
    if path.exists():
        if load(path) == value:
            return
        raise ValueError("collector output already contains different arguments; use a new output path")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("plan", type=Path)
    parser.add_argument(
        "--request",
        type=Path,
        help="TrendResearchRequest; enforces that intents reuse observed caption phrases verbatim",
    )
    parser.add_argument("--collector-output", type=Path, help="Write validated MCP collection arguments")
    parser.add_argument("--discovery-window", choices=DISCOVERY_WINDOWS)
    args = parser.parse_args()
    try:
        plan = load(args.plan)
        # The Request has to be read before the plan is validated: the popular_topic cap is a
        # function of its window, and validating without it silently applies the short-window cap.
        request = load(args.request) if args.request is not None else None
        if bool(args.collector_output) != bool(args.discovery_window):
            raise ValueError("--collector-output and --discovery-window must be provided together")
        if args.collector_output is not None and request is None:
            raise ValueError("--collector-output requires --request")
        validate(plan, request)
        anchored = None
        profiled = False
        if request is not None:
            validate_against_request(plan, request)
            anchored = len(caption_phrases(request))
            profiled = _profile_intake().profile_fields_attached(request)
        result = {"valid": True, "intents": len(plan["intents"])}
        if anchored is not None and not profiled:
            result["caption_phrases_checked"] = anchored
        excluded = len(_profile_intake().excluded_topics(request)) if request is not None else 0
        if excluded:
            result["excluded_topics_checked"] = excluded
        if request is not None:
            # Recorded so a finished run says which regime it ran under. Three separate rules
            # switch on this one fact, and a run that silently took the short-window branch is
            # otherwise indistinguishable from one that had nothing to find.
            result["window_spans_more_than_a_month"] = window_spans_more_than_a_month(request)
        result["popular_intents"] = sum(
            1 for intent in plan["intents"] if intent.get("popular_topic") is not None
        )
        if args.collector_output is not None:
            save_collection_input(args.collector_output, collection_input(plan, request, args.discovery_window))
            result["collector_input"] = str(args.collector_output)
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
