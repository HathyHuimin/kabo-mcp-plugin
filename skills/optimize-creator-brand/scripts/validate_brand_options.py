#!/usr/bin/env python3
"""Validate the lean request and result contracts for optimize-creator-brand."""

from __future__ import annotations

import json
from datetime import datetime
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
# Sibling imports must not leave a __pycache__ in the signed, checksummed skill directory.
sys.dont_write_bytecode = True
from record_passages import load_page, quoted  # noqa: E402  - the same "is this the page's text" as rule checks

SPEC_SOURCES = Path(__file__).resolve().parent.parent / "references" / "sources" / "visual-spec-sources.v1.json"


TASKS = {
    "youtube_channel_name": {"count": 10, "max_chars": 40},
    "instagram_bio": {"count": 8, "max_chars": 150, "min_chars": 20},
    "instagram_username": {
        "count": 10,
        "pattern": re.compile(r"^[a-z0-9._]{3,30}$"),
    },
    "tiktok_bio": {"count": 8, "max_chars": 80, "min_chars": 10},
    "tiktok_username": {
        "count": 10,
        "pattern": re.compile(r"^[a-z0-9._]{2,24}$"),
    },
}
# delivery_language is a BCP-47 tag. English and 中文 are the v1 values and stay valid, so kits and
# briefs written before tags were accepted still validate.
LEGACY_DELIVERY_LANGUAGES = {"English", "中文"}
LANGUAGE_TAG = re.compile(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*")
# The visual spec is not a list of naming options, so it carries no count or character rule here.
# It is accepted as a request task and validated by validate_spec against its own contract.
SPEC_TASK = "visual_identity_spec"
BRIEF_TASK = "brand_strategy_brief"
HANDLE_SOURCES = {"user_supplied", "resolved_public"}
PLATFORMS = {"instagram", "tiktok", "youtube"}
OFFICIAL = {"official_help_page", "official_secondary"}
AUTHORITIES = OFFICIAL | {"creator_reported", "unofficial", "derived_working_size"}
KIT_TASK = "brand_kit"
KIT_MODES = {"account", "checklist"}
WORK_SELECTIONS = {"curated_highlight", "recent_sample"}
CHECKLIST_BASES = {"official", "inferred"}
# A number or work read from a public profile has to point at that platform's own site; an
# official checklist item may cite any platform's own help or marketplace pages.
OWN_DOMAINS = {"instagram": ("instagram.com",), "tiktok": ("tiktok.com",),
               "youtube": ("youtube.com", "youtu.be")}
OFFICIAL_DOMAINS = ("instagram.com", "facebook.com", "tiktok.com", "youtube.com", "youtu.be",
                    "support.google.com", "blog.youtube")
# A price anywhere in the kit's text: a currency next to a number. Rates are quoted in a separate
# pricing run; a figure that slips into the kit reads as the creator's rate card.
#
# A currency sign is any Unicode currency symbol ($, €, ₹, ₩ ...). A currency code is an ISO 4217
# code written in capitals next to the number; the codes a creator is most likely to type are also
# recognised in lower case. Codes that also read as ordinary words or names in a post title are
# left out (TOP 10, ALL, CUP, PEN, TRY, PHP 8, AMD Ryzen 5, SDG 4, CVE, SVC).
CURRENCY_CODES = frozenset("""
AED AFN ANG AOA ARS AUD AWG AZN BBD BDT BGN BHD BIF BMD BND BRL BSD BTN BWP BYN BZD CAD CDF CHF
CLP CNY COP CRC CZK DJF DKK DOP DZD EGP ERN ETB EUR FJD FKP GBP GHS GIP GMD GNF GTQ GYD HKD HNL
HTG HUF IDR ILS INR IQD IRR ISK JMD JOD JPY KES KGS KHR KMF KPW KRW KWD KYD KZT LAK LBP LKR LRD
LSL LYD MDL MGA MKD MMK MNT MRU MUR MVR MWK MXN MYR MZN NAD NGN NIO NOK NPR NZD OMR PAB PGK PKR
PLN PYG QAR RON RSD RUB RWF SAR SBD SCR SEK SGD SHP SLE SLL SRD SSP STN SYP SZL THB TJS TMT TND
TWD TZS UAH UGX USD UYU UZS VES VND VUV WST XAF XCD XOF XPF YER ZAR ZMW ZWL
""".split())
COMMON_CODES = frozenset({"usd", "eur", "gbp", "rmb", "cny", "jpy", "krw", "inr", "aud", "hkd", "twd", "sgd"})
AMOUNT = re.compile(r"\d[\d,.]*")
CODE_BEFORE = re.compile(r"(?<![A-Za-z])([A-Za-z]{3})\s?$")
CODE_AFTER = re.compile(r"^(?:[kKmM](?![A-Za-z]))?\s?([A-Za-z]{3})(?![A-Za-z])")
WORD_AFTER = re.compile(r"^(?:[kKmM](?![A-Za-z]))?\s?(?:dollars?|euros?|rupees?)(?![A-Za-z])", re.IGNORECASE)
# Unit words written straight after the number. They need a money boundary: 元 in 元素 (element)
# and 块 as a counter (8块腹肌, eight abs) are not money, while 3000元, 500块钱 and 5000円 are.
UNIT_AFTER = re.compile(r"^\s?(?:元(?!素)|円|원|[镑鎊]|块钱|塊錢|[块塊](?![^\W\d_]))")


def is_code(code: str) -> bool:
    return code.lower() in COMMON_CODES or (code.isupper() and code in CURRENCY_CODES)


def carries_price(value: str) -> bool:
    for match in AMOUNT.finditer(value):
        before = value[:match.start()]
        after = value[match.end():]
        sign_before = before.rstrip(" ")[-1:]
        sign_after = after.lstrip(" ")[:1]
        if (sign_before and unicodedata.category(sign_before) == "Sc") \
                or (sign_after and unicodedata.category(sign_after) == "Sc"):
            return True
        code = CODE_BEFORE.search(before)
        if code and is_code(code.group(1)):
            return True
        code = CODE_AFTER.match(after)
        if code and is_code(code.group(1)):
            return True
        if WORD_AFTER.match(after) or UNIT_AFTER.match(after):
            return True
    return False


def fail(message: str) -> None:
    raise ValueError(message)


def load(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def require_keys(value: dict[str, Any], required: set[str], allowed: set[str], label: str) -> None:
    missing = required - value.keys()
    extra = value.keys() - allowed
    if missing:
        fail(f"{label} missing keys: {sorted(missing)}")
    if extra:
        fail(f"{label} has unsupported keys: {sorted(extra)}")


def check_delivery_language(payload: dict[str, Any]) -> None:
    value = payload["delivery_language"]
    if not isinstance(value, str) or not (value in LEGACY_DELIVERY_LANGUAGES or LANGUAGE_TAG.fullmatch(value)):
        fail("delivery_language must be a BCP-47 tag such as en, zh-CN or pt-BR")


def text(value: Any, label: str, minimum: int, maximum: int) -> str:
    if not isinstance(value, str):
        fail(f"{label} must be a string")
    stripped = value.strip()
    if not minimum <= len(stripped) <= maximum:
        fail(f"{label} must contain {minimum}-{maximum} characters")
    return stripped


def validate_request(payload: Any) -> None:
    if not isinstance(payload, dict):
        fail("request must be an object")
    require_keys(
        payload,
        {"schema_version", "task", "brief", "delivery_language"},
        {"schema_version", "task", "brief", "delivery_language", "preferences", "requested_count"},
        "request",
    )
    if payload["schema_version"] != "creator-brand-request.v1":
        fail("request schema_version must be creator-brand-request.v1")
    if payload["task"] not in TASKS and payload["task"] not in {SPEC_TASK, BRIEF_TASK, KIT_TASK}:
        fail("request task is unsupported")
    text(payload["brief"], "brief", 3, 3000)
    check_delivery_language(payload)
    option_count(payload, "request")
    preferences = payload.get("preferences")
    if preferences is None:
        return
    if not isinstance(preferences, dict):
        fail("preferences must be an object")
    require_keys(preferences, set(), {"tone", "must_include", "avoid"}, "preferences")
    if "tone" in preferences:
        text(preferences["tone"], "preferences.tone", 1, 200)
    for key, maximum in (("must_include", 10), ("avoid", 20)):
        if key not in preferences:
            continue
        values = preferences[key]
        if not isinstance(values, list) or len(values) > maximum:
            fail(f"preferences.{key} must be an array with at most {maximum} items")
        for index, value in enumerate(values):
            text(value, f"preferences.{key}[{index}]", 1, 80)


def validate_result(payload: Any, request: dict[str, Any] | None = None) -> None:
    """With the request, the delivered count is bound to what the creator asked for, not to
    whatever requested_count the result copied."""
    if not isinstance(payload, dict):
        fail("result must be an object")
    require_keys(
        payload,
        {"schema_version", "task", "delivery_language", "options"},
        {"schema_version", "task", "delivery_language", "options", "recommended", "requested_count"},
        "result",
    )
    if payload["schema_version"] != "creator-brand-options.v1":
        fail("result schema_version must be creator-brand-options.v1")
    task = payload["task"]
    if task not in TASKS:
        fail("result task is unsupported")
    check_delivery_language(payload)
    options = payload["options"]
    expected = option_count(payload, "result")
    if isinstance(options, list) and len(options) != expected and "requested_count" not in payload:
        fail(f"{task} requires exactly {expected} options by default; when the creator asked for "
             "a different number up to that, set requested_count to it")
    if request is not None:
        validate_request(request)
        if request["task"] != task:
            fail(f"result task {task} does not match request task {request['task']}")
        asked = option_count(request, "request")
        if expected != asked:
            fail(f"result delivers {expected} options but the request asked for {asked}; copy the "
                 "request's requested_count, or omit it when the request has none")
    validate_option_set(task, options, expected_count=expected)
    check_recommended(payload, [o["value"] for o in options])


def option_count(payload: dict[str, Any], label: str) -> int | None:
    """The number of options to deliver: the creator's own number when they named one, else the
    task default. A larger number than the default is refused, not silently capped."""
    task = payload["task"]
    if "requested_count" not in payload:
        return TASKS[task]["count"] if task in TASKS else None
    if task not in TASKS:
        fail(f"{label} requested_count only applies to naming and bio tasks")
    requested = payload["requested_count"]
    default = TASKS[task]["count"]
    if isinstance(requested, bool) or not isinstance(requested, int) or not 1 <= requested <= default:
        fail(f"{label} requested_count must be an integer from 1 to {default} for {task}")
    return requested


def validate_option_set(task: str, options: Any, *, expected_count: int) -> None:
    """Shared option rules; the full result and Lite caller own their set sizes."""
    if task not in TASKS:
        fail("result task is unsupported")
    spec = TASKS[task]
    if not isinstance(options, list) or len(options) != expected_count:
        fail(f"{task} requires exactly {expected_count} options")

    seen: set[str] = set()
    username_task = task.endswith("_username")
    for index, option in enumerate(options):
        if not isinstance(option, dict):
            fail(f"options[{index}] must be an object")
        allowed = {"value", "rationale", "availability_status", "strategy"}
        require_keys(option, {"value", "rationale"}, allowed, f"options[{index}]")
        raw_value = option["value"]
        if isinstance(raw_value, str) and raw_value != raw_value.strip():
            fail(f"options[{index}].value must not have leading or trailing whitespace")
        value = text(raw_value, f"options[{index}].value", 2, 150)
        text(option["rationale"], f"options[{index}].rationale", 3, 300)
        normalized = value.casefold()
        if normalized in seen:
            fail(f"options[{index}].value duplicates another option")
        seen.add(normalized)

        if "pattern" in spec and not spec["pattern"].fullmatch(value):
            fail(f"options[{index}].value violates the {task} character or length rule")
        if "max_chars" in spec and len(value) > spec["max_chars"]:
            fail(f"options[{index}].value exceeds {spec['max_chars']} characters")
        if "min_chars" in spec and len(value) < spec["min_chars"]:
            fail(f"options[{index}].value is too thin for {task}")
        if task.endswith("_bio") and "#" in value:
            fail(f"options[{index}].value must not contain hashtags")
        if not username_task and "availability_status" in option:
            fail(f"options[{index}].availability_status is only valid for usernames")
        if username_task:
            status = option.get("availability_status", "unverified")
            if status not in {"unverified", "available", "taken", "check_failed"}:
                fail(f"options[{index}].availability_status is invalid")
            if task == "instagram_username" and status != "unverified":
                fail("Instagram username availability cannot be inferred by this Skill")



def check_recommended(payload: dict[str, Any], values: list[str]) -> None:
    shortlist = payload.get("recommended")
    if shortlist is None:
        return
    if not isinstance(shortlist, list) or len(shortlist) > 3:
        fail("recommended must be an array of at most three entries")
    if len(shortlist) > len(values):
        fail("recommended must not list more entries than there are options")
    picked: set[str] = set()
    for index, entry in enumerate(shortlist):
        if not isinstance(entry, dict):
            fail(f"recommended[{index}] must be an object")
        require_keys(entry, {"value", "reason"}, {"value", "reason"}, f"recommended[{index}]")
        text(entry["reason"], f"recommended[{index}].reason", 1, 300)
        value = entry["value"]
        # Recommending something that is not on the list is how a candidate that never went
        # through the platform rules reaches the creator as the preferred one.
        if value not in values:
            fail(f"recommended[{index}].value is not one of the options")
        if value in picked:
            fail(f"recommended[{index}].value is listed twice")
        picked.add(value)


def validate_spec(payload: Any) -> None:
    if not isinstance(payload, dict):
        fail("spec must be an object")
    require_keys(
        payload,
        {"schema_version", "task", "platform", "delivery_language", "palette",
         "typography", "surfaces", "measurements", "not_published"},
        {"schema_version", "task", "platform", "delivery_language", "palette",
         "typography", "surfaces", "measurements", "not_published"},
        "spec",
    )
    if payload["schema_version"] != "creator-brand-visual-spec.v1":
        fail("spec schema_version must be creator-brand-visual-spec.v1")
    if payload["task"] != SPEC_TASK:
        fail("spec task must be visual_identity_spec")
    if payload["platform"] not in PLATFORMS:
        fail("spec platform must be instagram, tiktok or youtube")
    check_delivery_language(payload)

    for key, minimum, maximum in (("palette", 2, 6), ("typography", 1, 3), ("surfaces", 1, 40)):
        values = payload[key]
        if not isinstance(values, list) or not minimum <= len(values) <= maximum:
            fail(f"spec {key} must be an array of {minimum} to {maximum} items")

    for index, surface in enumerate(payload["surfaces"]):
        if not isinstance(surface, dict):
            fail(f"surfaces[{index}] must be an object")
        require_keys(surface, {"surface", "direction"}, {"surface", "direction"}, f"surfaces[{index}]")
        text(surface["surface"], f"surfaces[{index}].surface", 1, 200)
        text(surface["direction"], f"surfaces[{index}].direction", 1, 1000)

    for index, colour in enumerate(payload["palette"]):
        if not isinstance(colour, dict):
            fail(f"palette[{index}] must be an object")
        require_keys(colour, {"name", "hex", "role"}, {"name", "hex", "role"}, f"palette[{index}]")
        text(colour["name"], f"palette[{index}].name", 1, 200)
        if not re.match(r"^#[0-9a-fA-F]{6}$", str(colour["hex"])):
            fail(f"palette[{index}].hex must be a six-digit hex colour")
        text(colour["role"], f"palette[{index}].role", 1, 200)

    for index, face in enumerate(payload["typography"]):
        if not isinstance(face, dict):
            fail(f"typography[{index}] must be an object")
        require_keys(face, {"role", "family", "availability"},
                     {"role", "family", "availability"}, f"typography[{index}]")
        text(face["role"], f"typography[{index}].role", 1, 200)
        text(face["family"], f"typography[{index}].family", 1, 200)
        # A face the creator cannot obtain is not a spec, it is a mood board.
        text(face["availability"], f"typography[{index}].availability", 1, 300)

    if not isinstance(payload["measurements"], list):
        fail("spec measurements must be an array")
    if not isinstance(payload["not_published"], list):
        fail("spec not_published must be an array")

    silent = set()
    for index, gap in enumerate(payload["not_published"]):
        if not isinstance(gap, dict):
            fail(f"not_published[{index}] must be an object")
        require_keys(gap, {"surface", "checked_source_id", "checked_url"},
                     {"surface", "checked_source_id", "checked_url"}, f"not_published[{index}]")
        text(gap["surface"], f"not_published[{index}].surface", 1, 200)
        text(gap["checked_source_id"], f"not_published[{index}].checked_source_id", 1, 200)
        text(gap["checked_url"], f"not_published[{index}].checked_url", 1, 2000)
        silent.add(str(gap["surface"]).strip().lower())

    for index, item in enumerate(payload["measurements"]):
        if not isinstance(item, dict):
            fail(f"measurements[{index}] must be an object")
        require_keys(item, {"surface", "claim", "authority"},
                     {"surface", "claim", "authority", "source_id", "source_url",
                      "retrieved_at", "quote", "basis"}, f"measurements[{index}]")
        text(item["surface"], f"measurements[{index}].surface", 1, 200)
        text(item["claim"], f"measurements[{index}].claim", 1, 500)
        authority = item["authority"]
        if authority not in AUTHORITIES:
            fail(f"measurements[{index}].authority is unsupported")
        surface = str(item["surface"]).strip().lower()
        if authority in OFFICIAL:
            # An official claim without the platform's own sentence and a retrieval date is
            # indistinguishable from a remembered number, which is the failure this guards.
            for field in ("source_id", "source_url", "retrieved_at", "quote"):
                if not text_or_none(item.get(field)):
                    fail(f"measurements[{index}] is {authority} so it must carry {field}")
            if surface in silent:
                fail(f"measurements[{index}] states an official size for '{item['surface']}', "
                     "but not_published records that the platform does not publish one")
        else:
            if not text_or_none(item.get("basis")):
                fail(f"measurements[{index}] is {authority} so it must carry basis, "
                     "naming whose figure it is and how it was arrived at")
            if authority == "derived_working_size":
                for field in ("source_id", "source_url", "retrieved_at", "quote"):
                    if item.get(field) is not None:
                        fail(f"measurements[{index}] is derived_working_size so {field} must be "
                             "null; a working size has no platform page behind it")

    # The renderer tells the creator that the figure above is a working size for every
    # not_published surface; without one that sentence points at nothing.
    working = {str(item["surface"]).strip().lower() for item in payload["measurements"]
               if item["authority"] == "derived_working_size"}
    for index, gap in enumerate(payload["not_published"]):
        if str(gap["surface"]).strip().lower() not in working:
            fail(f"not_published[{index}] names '{gap['surface']}' but no derived_working_size "
                 "measurement gives the working size the rendered note points to: add to measurements "
                 f"{{\"surface\": \"{gap['surface']}\", \"claim\": <the working size>, "
                 "\"authority\": \"derived_working_size\", \"basis\": <how it was arrived at>, "
                 "\"source_id\": null, \"source_url\": null, \"retrieved_at\": null, \"quote\": null}")


def string_items(values: list[Any], label: str, *, unique: bool) -> None:
    for index, value in enumerate(values):
        text(value, f"{label}[{index}]", 1, 600)
    if unique and len(values) != len(set(values)):
        fail(f"{label} contains duplicates")


def validate_brief(payload: Any) -> None:
    if not isinstance(payload, dict):
        fail("brief must be an object")
    keys = {"schema_version", "task", "delivery_language", "positioning", "audience",
            "content", "tone", "assumptions", "handoff"}
    require_keys(payload, keys, keys, "brief")
    if payload["schema_version"] != "creator-brand-strategy-brief.v1":
        fail("brief schema_version must be creator-brand-strategy-brief.v1")
    if payload["task"] != BRIEF_TASK:
        fail("brief task must be brand_strategy_brief")
    check_delivery_language(payload)
    text(payload["positioning"], "positioning", 1, 400)
    text(payload["tone"], "tone", 1, 400)

    audience = payload["audience"]
    if not isinstance(audience, dict):
        fail("audience must be an object")
    require_keys(audience, {"summary", "needs"}, {"summary", "needs"}, "audience")
    text(audience["summary"], "audience.summary", 1, 600)
    if not isinstance(audience["needs"], list) or not audience["needs"]:
        fail("audience.needs must be a non-empty array")
    string_items(audience["needs"], "audience.needs", unique=True)

    content = payload["content"]
    if not isinstance(content, dict):
        fail("content must be an object")
    require_keys(content, {"pillars", "formats", "exclusions"},
                 {"pillars", "formats", "exclusions"}, "content")
    if not isinstance(content["pillars"], list) or len(content["pillars"]) < 2:
        fail("content.pillars must list at least two pillars")
    for key in ("formats", "exclusions"):
        if not isinstance(content[key], list):
            fail(f"content.{key} must be an array")
    for key in ("pillars", "formats", "exclusions"):
        string_items(content[key], f"content.{key}", unique=True)

    assumptions = payload["assumptions"]
    if not isinstance(assumptions, list):
        fail("assumptions must be an array")
    string_items(assumptions, "assumptions", unique=False)

    handoff = payload["handoff"]
    if not isinstance(handoff, dict):
        fail("handoff must be an object")
    require_keys(handoff, {"status", "blocked_reason", "accounts_provenance", "creator_profile"},
                 {"status", "blocked_reason", "accounts_provenance", "creator_profile"}, "handoff")
    if handoff["status"] not in {"ready", "blocked"}:
        fail("handoff.status must be ready or blocked")

    provenance = handoff["accounts_provenance"]
    if not isinstance(provenance, list):
        fail("handoff.accounts_provenance must be an array")
    known: set[tuple[str, str]] = set()
    for index, entry in enumerate(provenance):
        if not isinstance(entry, dict):
            fail(f"handoff.accounts_provenance[{index}] must be an object")
        require_keys(entry, {"platform", "handle", "source"},
                     {"platform", "handle", "source"}, f"handoff.accounts_provenance[{index}]")
        if entry["source"] not in HANDLE_SOURCES:
            fail(f"handoff.accounts_provenance[{index}].source must be "
                 f"{sorted(HANDLE_SOURCES)} - a handle with no provenance is one nobody supplied")
        known.add((str(entry["platform"]), str(entry["handle"])))

    profile = handoff["creator_profile"]
    if handoff["status"] == "blocked":
        if profile is not None:
            fail("handoff is blocked so creator_profile must be null")
        if not text_or_none(handoff["blocked_reason"]):
            fail("handoff is blocked so blocked_reason must say what is missing")
        return

    if not isinstance(profile, dict):
        fail("handoff is ready so creator_profile must be an object")
    allowed = {"schema_version", "profile_id", "accounts", "markets", "audience",
               "positioning", "content_profile", "key_missing_info", "extensions"}
    require_keys(profile, {"schema_version", "profile_id", "accounts"}, allowed, "creator_profile")
    if profile["schema_version"] != "creator-profile.v3":
        fail("creator_profile schema_version must be creator-profile.v3")
    accounts = profile["accounts"]
    if not isinstance(accounts, list) or not accounts:
        fail("creator_profile.accounts must list at least one account")
    for index, account in enumerate(accounts):
        if not isinstance(account, dict):
            fail(f"creator_profile.accounts[{index}] must be an object")
        require_keys(account, {"platform", "handle"}, {"platform", "handle"},
                     f"creator_profile.accounts[{index}]")
        # The guard this contract exists for: a handle that reached the handoff without a
        # provenance row was invented, and downstream it reads as an account fact.
        if (str(account["platform"]), str(account["handle"])) not in known:
            fail(f"creator_profile.accounts[{index}] handle is not in accounts_provenance, "
                 "so nobody supplied it")
    missing = profile.get("key_missing_info")
    if assumptions and (not isinstance(missing, list) or set(assumptions) - set(missing)):
        fail("every assumption must also appear in creator_profile.key_missing_info, "
             "or the topic Skill reads an assumption as a confirmed account fact")


def strings_in(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings_in(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings_in(item)


def web_url(value: Any, label: str, domains: tuple[str, ...]) -> None:
    match = re.match(r"^https?://([^/?#:\s]+)", value) if isinstance(value, str) else None
    host = match.group(1).lower() if match else ""
    if not any(host == domain or host.endswith("." + domain) for domain in domains):
        fail(f"{label} must be an http(s) link on {', '.join(domains)}")


def iso_date(value: Any, label: str) -> None:
    try:
        datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        ok = bool(re.match(r"^\d{4}-\d{2}-\d{2}", str(value)))
    except ValueError:
        ok = False
    if not ok:
        fail(f"{label} must be an ISO date such as 2026-09-15")


def validate_kit(payload: Any) -> None:
    if not isinstance(payload, dict):
        fail("kit must be an object")
    keys = {"schema_version", "task", "delivery_language", "mode", "identity",
            "collaboration_formats", "representative_works", "metrics", "contact",
            "profile_checklist", "accounts_provenance", "missing", "rate_card"}
    require_keys(payload, keys, keys, "kit")
    if payload["schema_version"] != "creator-brand-kit.v1":
        fail("kit schema_version must be creator-brand-kit.v1")
    if payload["task"] != KIT_TASK:
        fail("kit task must be brand_kit")
    check_delivery_language(payload)
    mode = payload["mode"]
    if mode not in KIT_MODES:
        fail("kit mode must be account or checklist")

    identity = payload["identity"]
    if not isinstance(identity, dict):
        fail("identity must be an object")
    require_keys(identity, {"positioning", "niche", "audience"},
                 {"positioning", "niche", "audience"}, "identity")
    text(identity["positioning"], "identity.positioning", 1, 400)
    text(identity["niche"], "identity.niche", 1, 200)
    text(identity["audience"], "identity.audience", 1, 600)

    formats = payload["collaboration_formats"]
    if not isinstance(formats, list) or not formats:
        fail("collaboration_formats must be a non-empty array")
    string_items(formats, "collaboration_formats", unique=True)

    for key in ("accounts_provenance", "metrics", "representative_works",
                "profile_checklist", "missing"):
        if not isinstance(payload[key], list):
            fail(f"{key} must be an array")
    known: set[tuple[str, str]] = set()
    for index, entry in enumerate(payload["accounts_provenance"]):
        label = f"accounts_provenance[{index}]"
        if not isinstance(entry, dict):
            fail(f"{label} must be an object")
        require_keys(entry, {"platform", "handle", "source"}, {"platform", "handle", "source"}, label)
        text(entry["platform"], f"{label}.platform", 1, 40)
        text(entry["handle"], f"{label}.handle", 1, 200)
        if entry["source"] not in HANDLE_SOURCES:
            fail(f"{label}.source must be {sorted(HANDLE_SOURCES)} - a handle with no provenance "
                 "is one nobody supplied")
        known.add((str(entry["platform"]), str(entry["handle"])))

    if mode == "account" and not known:
        fail("an account kit needs at least one account in accounts_provenance")
    if mode == "checklist":
        # With no account there is nothing to attribute a number or a work to.
        for key in ("accounts_provenance", "metrics", "representative_works"):
            if payload[key]:
                fail(f"a checklist kit has no account, so {key} must be empty")

    def owned(item: dict[str, Any], label: str) -> None:
        text(item["platform"], f"{label}.platform", 1, 40)
        text(item["handle"], f"{label}.handle", 1, 200)
        if (str(item["platform"]), str(item["handle"])) not in known:
            fail(f"{label} belongs to an account that is not in accounts_provenance, "
                 "so nobody supplied it")

    for index, metric in enumerate(payload["metrics"]):
        label = f"metrics[{index}]"
        if not isinstance(metric, dict):
            fail(f"{label} must be an object")
        fields = {"name", "value", "unit", "platform", "handle", "source", "source_url", "retrieved_at"}
        require_keys(metric, fields, fields, label)
        text(metric["name"], f"{label}.name", 1, 120)
        text(metric["unit"], f"{label}.unit", 1, 120)
        value = metric["value"]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            fail(f"{label}.value must be a non-negative number")
        if metric["source"] not in HANDLE_SOURCES:
            fail(f"{label} has no recorded source - a number nobody can trace is not a kit fact")
        if metric["source"] == "resolved_public":
            if metric["platform"] not in OWN_DOMAINS:
                fail(f"{label}.platform must be instagram, tiktok or youtube")
            web_url(metric["source_url"], f"{label}.source_url", OWN_DOMAINS[metric["platform"]])
            iso_date(metric["retrieved_at"], f"{label}.retrieved_at")
        owned(metric, label)

    for index, work in enumerate(payload["representative_works"]):
        label = f"representative_works[{index}]"
        if not isinstance(work, dict):
            fail(f"{label} must be an object")
        fields = {"title", "url", "platform", "handle", "selection"}
        require_keys(work, fields, fields, label)
        text(work["title"], f"{label}.title", 1, 300)
        text(work["url"], f"{label}.url", 1, 2000)
        if work["platform"] not in OWN_DOMAINS:
            fail(f"{label}.platform must be instagram, tiktok or youtube")
        web_url(work["url"], f"{label}.url", OWN_DOMAINS[work["platform"]])
        if work["selection"] not in WORK_SELECTIONS:
            fail(f"{label}.selection must say whether it is a curated highlight or a recent sample")
        owned(work, label)

    contact = payload["contact"]
    if contact is not None:
        if not isinstance(contact, dict):
            fail("contact must be null or an object")
        require_keys(contact, {"channel", "value", "source"}, {"channel", "value", "source"}, "contact")
        text(contact["channel"], "contact.channel", 1, 80)
        text(contact["value"], "contact.value", 1, 300)
        if contact["source"] != "user_supplied":
            fail("contact can only come from the creator - it is never looked up or guessed")

    for index, item in enumerate(payload["profile_checklist"]):
        label = f"profile_checklist[{index}]"
        if not isinstance(item, dict):
            fail(f"{label} must be an object")
        require_keys(item, {"item", "basis", "source_url"}, {"item", "basis", "source_url"}, label)
        text(item["item"], f"{label}.item", 1, 300)
        if item["basis"] not in CHECKLIST_BASES:
            fail(f"{label}.basis must be official or inferred")
        if item["basis"] == "official":
            web_url(item["source_url"], f"{label}.source_url", OFFICIAL_DOMAINS)
    if mode == "checklist" and not payload["profile_checklist"]:
        fail("a checklist kit must list what brands look at in profile_checklist")

    string_items(payload["missing"], "missing", unique=True)
    if mode == "account" and not payload["metrics"] and not payload["missing"]:
        fail("an account kit with no metrics must say in missing what could not be obtained")

    rate_card = payload["rate_card"]
    if not isinstance(rate_card, dict):
        fail("rate_card must be an object")
    require_keys(rate_card, {"handled_by", "note"}, {"handled_by", "note"}, "rate_card")
    if rate_card["handled_by"] != "price-creator-brand-deals":
        fail("rate_card.handled_by must be price-creator-brand-deals")
    text(rate_card["note"], "rate_card.note", 1, 300)

    for value in strings_in(payload):
        if carries_price(value):
            fail(f"the kit carries a price ({value[:60]!r}); rates are quoted separately, never in the kit")


def text_or_none(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ""


def bare_url(value: Any) -> str:
    return re.sub(r"^https?://(www\.)?", "", str(value or "").strip().lower()).split("#")[0].rstrip("/")


def check_spec_pages(payload: dict[str, Any], snapshot_dir: str) -> list[str]:
    """Hold every official quote from a render_page source to the page this run persisted for it.

    A size quoted from memory reads exactly like one copied from the page. For a source the
    registry reads through render_page the page itself is on disk, so its quote must be the page's
    own characters and its source_url and retrieved_at the page's. A plain_fetch source leaves no
    persisted copy, and is held to its sentence and date by validate_spec alone.
    """
    registry = {entry.get("source_id"): entry for entry in load(str(SPEC_SOURCES)).get("sources") or []}
    errors: list[str] = []
    for index, item in enumerate(payload.get("measurements") or []):
        if not isinstance(item, dict) or item.get("authority") not in OFFICIAL:
            continue
        source_id = str(item.get("source_id"))
        entry = registry.get(source_id)
        if entry is None:
            errors.append(f"measurements[{index}].source_id {source_id!r} is not in the visual-spec source registry")
            continue
        if (entry.get("retrieval") or {}).get("method") != "js_rendered":
            continue
        try:
            _, page = load_page(Path(snapshot_dir) / f"{source_id}.envelope.json")
        except (OSError, ValueError) as error:
            errors.append(f"measurements[{index}] quotes {source_id!r}, but {snapshot_dir} holds no usable persisted "
                          f"render_page for it: persist the response before quoting it ({error})")
            continue
        if not quoted(str(item.get("quote") or ""), page["text"]):
            errors.append(f"measurements[{index}].quote is not in the page persisted for {source_id!r}: quote the "
                          "page's own characters, line breaks included")
        page_url = page.get("url") or page.get("fetched_url")
        if page_url and bare_url(item.get("source_url")) != bare_url(page_url):
            errors.append(f"measurements[{index}].source_url is not the page persisted for {source_id!r} ({page_url})")
        page_date = str(page.get("retrieved_at") or "")[:10]
        if page_date and str(item.get("retrieved_at") or "")[:10] != page_date:
            errors.append(f"measurements[{index}].retrieved_at is not the date that page was read ({page_date})")
    return errors


def main() -> int:
    args = sys.argv[1:]
    snapshot_dir = None
    request_path = None
    if len(args) == 4 and args[0] == "spec" and args[2] == "--snapshot-dir":
        snapshot_dir, args = args[3], args[:2]
    if len(args) == 4 and args[0] == "result" and args[2] == "--request":
        request_path, args = args[3], args[:2]
    if len(args) != 2 or args[0] not in {"request", "result", "spec", "brief", "kit"}:
        print("usage: validate_brand_options.py request|result|spec|brief|kit <json-file> "
              "(spec also takes --snapshot-dir <dir>; result also takes --request <request.json>)",
              file=sys.stderr)
        return 2
    try:
        payload = load(args[1])
        if args[0] == "kit":
            validate_kit(payload)
        elif args[0] == "brief":
            validate_brief(payload)
        elif args[0] == "spec":
            validate_spec(payload)
            if snapshot_dir is not None:
                errors = check_spec_pages(payload, snapshot_dir)
                if errors:
                    fail("; ".join(errors))
        elif args[0] == "request":
            validate_request(payload)
        else:
            validate_result(payload, load(request_path) if request_path else None)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        print(f"invalid: {error}", file=sys.stderr)
        return 1
    print(json.dumps({"valid": True, "kind": args[0]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
