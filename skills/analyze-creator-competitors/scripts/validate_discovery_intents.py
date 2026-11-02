#!/usr/bin/env python3
"""Validate the frozen competitor discovery intents before provider retrieval."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any


INSTAGRAM_LANES = {
    "local_category",
    "local_workflow",
    "english_category",
    "english_workflow",
}
# Popular Search only knows short topics Instagram has indexed. A buyer workflow phrase is not one:
# production 2026-10-14..21 saw 33 not-found against 29 hits on the workflow lanes,
# versus 9 against 50 on the category lanes, so workflow lanes send no popular_topic.
WORKFLOW_LANES = {"local_workflow", "english_workflow"}
CATEGORY_LANES = INSTAGRAM_LANES - WORKFLOW_LANES


def _tokens(value: str) -> list[str]:
    return re.findall(r"[^\W_]+(?:-[^\W_]+)*", value, flags=re.UNICODE)


# Same rule as analyze-tiktok-competitors validate_tiktok_query_plan.search_query_term_count_ok:
# scripts written without spaces (Chinese, Japanese, Korean) may use one 2-20 character phrase.
CJK_CHAR = re.compile(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]")


def _term_count_ok(value: str, minimum: int, maximum: int) -> bool:
    if minimum <= len(_tokens(value)) <= maximum:
        return True
    terms = value.split()
    return len(terms) == 1 and 2 <= len(CJK_CHAR.findall(terms[0])) <= 20


# Reuse the first three phrases verbatim; the fourth lane is a qualifier variant. Three is measured:
# pinning four with no variant dropped recall from 9 to 5 (5 in all three repeats).
MINIMUM_ANCHORED_LANES = 3
# A single caption often carries calls to action and deictic leftovers. Pinning them retrieves remix
# pages and resource funnels, not peers making the same content. Match the imperative/"these" shape;
# keep no brand list.
CAPTION_CTA_HEADS = {
    "follow",
    "subscribe",
    "comment",
    "type",
    "reply",
    "send",
    "dm",
    "评论",
    "回复",
}
CAPTION_DEICTIC_PREFIXES = (
    "all of these",
    "all these",
)


def _normalized(value: str) -> str:
    return " ".join(value.lower().split())


def _shares_caption_token(query: str, caption_tokens: set[str]) -> bool:
    """A variant may change the qualifier but must not drift back to description wording.

    `graphic design styles` matches `visual style` through style/styles. `brand design inspiration`
    shares nothing with the form phrase and, measured, swapped the six seats for logo/brand accounts.
    """
    for token in query.split():
        if token in caption_tokens:
            return True
        if len(token) < 4:
            continue
        if any(len(item) >= 4 and (token.startswith(item) or item.startswith(token)) for item in caption_tokens):
            return True
    return False


def _head_tokens(phrase: str) -> set[str]:
    """The token a variant must keep.

    The head noun sits at either end depending on language (`visual styles` vs `estilos de design`),
    so accept both ends instead of writing a parser per market.
    """
    tokens = _normalized(phrase).split()
    return {tokens[0], tokens[-1]} if tokens else set()


def _is_cta_or_deictic(phrase: str) -> bool:
    """Calls to action and deictic leftovers from one caption must not pin a lane.

    `Comment PIN`, `all of these styles` and `+100 MORE` are not content forms. Pinning them turned
    the pool into remix/official/adjacent accounts. A series title that still names the form qualifies.
    """
    norm = _normalized(phrase)
    tokens = norm.split()
    if not tokens:
        return True
    if re.split(r"\W+", norm, maxsplit=1)[0] in CAPTION_CTA_HEADS:
        return True
    if any(norm.startswith(prefix) for prefix in CAPTION_DEICTIC_PREFIXES):
        return True
    if re.fullmatch(r"\+?\d+\s+more", norm):
        return True
    if "i'll send" in norm or "i will send" in norm:
        return True
    return False


def _caption_phrases(subject: dict[str, Any]) -> list[str]:
    """Caption phrases that qualify to pin a lane: no single terms, no calls to action.

    Pinning beats the description because a phrase names a content form (`visual styles`). Topic
    hashtags (`technology`, `automation`) are single terms anyone can attach, so they are filtered by
    term count rather than a word list and the caller falls back to the description rule. Multi-term
    calls to action from a seed caption are dropped too. Do not pad when fewer than three remain.
    """
    value = subject.get("caption_phrases")
    if not isinstance(value, list):
        return []
    filtered = [
        phrase
        for phrase in value
        if isinstance(phrase, str)
        and len(_normalized(phrase).split()) >= 2
        and not _is_cta_or_deictic(phrase)
    ]
    # A seed post often leaves one series title of three or more terms; its nested two-term form
    # gets its own lane too. Add nothing when two or more phrases already qualify.
    if len(filtered) != 1:
        return filtered
    seen = {_normalized(phrase) for phrase in filtered}
    extras: list[str] = []
    for phrase in filtered:
        tokens = phrase.split()
        if len(tokens) < 3:
            continue
        prefix = " ".join(tokens[:2])
        key = _normalized(prefix)
        if key in seen:
            continue
        extras.append(prefix)
        seen.add(key)
    return filtered + extras


def _validate_caption_anchoring(subject: dict[str, Any], by_lane: dict[str, Any]) -> None:
    """Queries must reuse the Creator's own caption wording, not a description of them.

    The provider matches other creators' captions literally. Description-derived intents recalled
    52 accounts with 0 labelled competitors; the Creator's own phrases and a variant reached 13.
    Only enforced when upstream produced phrases; never block the run when it did not.
    """
    phrases = _caption_phrases(subject)
    if not phrases:
        return
    known = {_normalized(phrase) for phrase in phrases}
    heads = {token for phrase in phrases for token in _head_tokens(phrase)}
    queries = [_normalized(intent["search_query"]) for intent in by_lane.values()]

    # Which phrases to reuse is not the model's choice: two models picked nearly disjoint sets from
    # the same list (median recall 7 vs 9); pinning the first three reached 9 regardless of model.
    pinned = [_normalized(phrase) for phrase in phrases[:MINIMUM_ANCHORED_LANES]]
    # Each pinned phrase needs its own lane: phrases nest (visual styles inside visual styles remix),
    # so match one-to-one. Which lane holds which phrase is not constrained; lanes run together.
    remaining = list(queries)
    unmatched = []
    for phrase in pinned:
        hit = next((q for q in remaining if phrase in q), None)
        if hit is None:
            unmatched.append(phrase)
        else:
            remaining.remove(hit)
    if unmatched:
        raise ValueError(
            f"the first {len(pinned)} caption phrases must each be reused verbatim in a "
            f"search_query of its own; {unmatched} has no lane left in {queries}"
        )
    # Anchoring every lane on verbatim wording only reaches people who already talk like the Creator.
    leftover = [query for query in queries if query not in known]
    if not leftover:
        raise ValueError(
            "at least 1 search_query must keep a caption phrase's head token and change its "
            f"qualifier, so discovery reaches the rest of the community; head tokens {sorted(heads)}"
        )
    caption_tokens = {token for phrase in phrases for token in _normalized(phrase).split()}
    drifted = [query for query in leftover if not _shares_caption_token(query, caption_tokens)]
    if drifted:
        raise ValueError(
            "every leftover search_query must keep a caption-phrase token and change its "
            f"qualifier; drifted {drifted}; caption tokens {sorted(caption_tokens)}"
        )


def market_tokens(plan: dict[str, Any]) -> list[str]:
    """The compact market words the plan declares it wrote into its local lanes."""
    raw = plan.get("market_tokens")
    if raw is None:
        return []
    if not isinstance(raw, list) or not all(isinstance(t, str) and _tokens(t) and len(_tokens(t)) == 1 for t in raw):
        raise ValueError("market_tokens must be a list of single-word market tokens")
    return [t.strip() for t in raw]


def carries_market_token(query: str, declared: set[str]) -> bool:
    """A declared word counts as a whole word, or inside an unspaced CJK term (`日本料理`)."""
    for term in query.split():
        folded = term.casefold()
        if {token.casefold() for token in _tokens(folded)} & declared:
            return True
        if CJK_CHAR.search(folded) and any(CJK_CHAR.search(token) and token in folded for token in declared):
            return True
    return False


def _validate_local_market_cue(plan: dict[str, Any], by_lane: dict[str, Any]) -> None:
    """Declared market tokens must actually reach both local lanes.

    The plan declares which compact market word (the market's own spelling) it put into its local
    lanes; profile search strips exactly those words. Which market, and whether to name one, stays
    the planner's judgment from the Request — no country list lives here.
    """
    declared = {token.casefold() for token in market_tokens(plan)}
    if not declared:
        return
    for lane in ("local_category", "local_workflow"):
        if not carries_market_token(by_lane[lane]["search_query"], declared):
            raise ValueError(
                f"{lane}.search_query must carry one declared market token ({', '.join(sorted(declared))}); "
                "drop market_tokens if the request names no market"
            )


def _request_platforms(request: dict[str, Any]) -> list[str]:
    """Read the request's platform list.

    The contract is `platforms: ["instagram"]`. Agents sometimes write a
    singular `platform` string instead; treat that as the same Instagram-only
    signal so a missing plan.platform can be filled instead of aborting after
    collection already succeeded.
    """
    platforms = request.get("platforms")
    if isinstance(platforms, list) and platforms:
        return [str(value).strip().casefold() for value in platforms if isinstance(value, str)]
    singular = request.get("platform")
    if isinstance(singular, str) and singular.strip():
        return [singular.strip().casefold()]
    return []


def _resolve_plan_platform(request: dict[str, Any], plan: dict[str, Any]) -> str:
    """Resolve a single supported platform and reject request/plan mismatches."""
    requested = _request_platforms(request)
    if len(requested) != 1 or requested[0] not in {"instagram", "youtube"}:
        raise ValueError("request must specify exactly one platform: instagram or youtube")
    platform = plan.get("platform")
    if platform in {None, ""}:
        platform = requested[0]
        plan["platform"] = platform
    if platform != requested[0]:
        raise ValueError("plan platform must match the request platform")
    return platform


# The candidate collectors reject arguments outside these lengths without naming the field (the
# research call fails with "arguments do not match the capability schema"), so the plan is held to
# them here, where the error can say which field to fix. Lengths count characters, as the API does.
COLLECTOR_LIMITS = {
    "intent_id": (1, 64),
    "search_query": (2, 240),
    "popular_topic": (1, 80),
    "content_language": (2, 16),
    "locale.region": (2, 8),
}


def _check_length(label: str, value: Any, limit: str) -> None:
    minimum, maximum = COLLECTOR_LIMITS[limit]
    if not isinstance(value, str) or not minimum <= len(value.strip()) <= maximum:
        shown = len(value.strip()) if isinstance(value, str) else type(value).__name__
        raise ValueError(
            f"{label} must be {minimum} to {maximum} characters for the candidate collector (got {shown})"
        )


def _validate_collector_arguments(request: dict[str, Any], plan: dict[str, Any]) -> None:
    """Fields the run passes straight to the collector, beyond the intents themselves."""
    if plan.get("content_language") not in (None, ""):
        _check_length("content_language", plan["content_language"], "content_language")
    locale = request.get("locale")
    region = locale.get("region") if isinstance(locale, dict) else None
    if region not in (None, ""):
        _check_length("request locale.region (use a short region code such as US or BR)", region,
                      "locale.region")


def validate(request: dict[str, Any], plan: dict[str, Any]) -> None:
    if request.get("schema_version") != "competitor-research-request.v3":
        raise ValueError("request must be competitor-research-request.v3")
    if plan.get("schema_version") != "competitor-discovery-intents.v1":
        raise ValueError("plan must be competitor-discovery-intents.v1")
    platform = _resolve_plan_platform(request, plan)
    _validate_collector_arguments(request, plan)
    intents = plan.get("intents")
    if platform == "youtube":
        if not isinstance(intents, list) or not 1 <= len(intents) <= 2:
            raise ValueError("YouTube discovery plan must contain one or two intents")
        identifiers, queries = set(), set()
        for intent in intents:
            if not isinstance(intent, dict):
                raise ValueError("every intent must be an object")
            for field, seen in (("intent_id", identifiers), ("search_query", queries)):
                value = intent.get(field)
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(f"YouTube {field} must be a non-empty string")
                _check_length(f"YouTube {field}", value, field)
                normalized = _normalized(value)
                if normalized in seen:
                    raise ValueError(f"YouTube {field} values must be unique")
                seen.add(normalized)
        return
    if not isinstance(intents, list) or len(intents) != 4:
        raise ValueError("Instagram discovery plan must contain exactly four intents")

    by_lane: dict[str, dict[str, Any]] = {}
    for intent in intents:
        if not isinstance(intent, dict):
            raise ValueError("every intent must be an object")
        lane = intent.get("intent_id")
        if lane not in INSTAGRAM_LANES or lane in by_lane:
            raise ValueError("Instagram intents must use every canonical lane exactly once")
        if lane in WORKFLOW_LANES and "popular_topic" in intent:
            raise ValueError(
                f"{lane}.popular_topic is not allowed: a workflow phrase is not an Instagram topic, "
                "remove the field"
            )
        for field, minimum, maximum in (
            ("search_query", 2, 5),
            ("popular_topic", 2, 4),
        ):
            if field == "popular_topic" and field not in intent:
                continue
            value = intent.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{lane}.{field} must be a non-empty string")
            if not _term_count_ok(value, minimum, maximum):
                raise ValueError(f"{lane}.{field} must contain {minimum} to {maximum} terms")
            _check_length(f"{lane}.{field}", value, field)
        by_lane[lane] = intent
    if set(by_lane) != INSTAGRAM_LANES:
        raise ValueError("Instagram intents must use every canonical lane exactly once")

    subject = request.get("subject")
    description = subject.get("description") if isinstance(subject, dict) else None
    if not isinstance(description, str) or not description.strip():
        raise ValueError("request subject.description must be a non-empty string")
    _validate_caption_anchoring(subject, by_lane)
    _validate_local_market_cue(plan, by_lane)
    queries = [_normalized(intent["search_query"]) for intent in by_lane.values()]
    if len(set(queries)) != 4:
        raise ValueError("Instagram search_query values must be unique across the four lanes")


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: validate_discovery_intents.py <request.json> <plan.json>")
        return 2
    try:
        request = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
        plan_path = Path(argv[2])
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        missing_platform = plan.get("platform") in {None, ""}
        validate(request, plan)
        if missing_platform:
            plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        # Access failures are execution faults, not permission to keep retrying authored input.
        print(f"cannot access competitor discovery inputs: {exc}")
        return 1
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"invalid competitor discovery intents: {exc}")
        return 1
    print(json.dumps({"status": "valid", "schema_version": plan["schema_version"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
