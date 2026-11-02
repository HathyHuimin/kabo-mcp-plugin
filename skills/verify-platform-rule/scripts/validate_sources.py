#!/usr/bin/env python3
"""Check the registered source list against its own contract.

The list is the whole product. A rule question is answered from these pages or it is not answered,
so a row that lies about what it carries, or quietly stops saying where its changes appear, is
worse than a missing row: it looks like coverage.

The rule with the most history behind it is the watch_urls one. YouTube announced the 2027 Partner
Program thresholds on its blog on 2026-08-10 and stated them on a separate changes page, while the
eligibility page that a rule library cited said nothing - and still says nothing. A careful person
re-read that page and came away with "the thresholds are not published yet". They were, on a page
nobody had written down. Requiring every official help page to name where its changes surface, or
to say in a sentence why nowhere, is that failure turned into an assertion.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path

TOP_LEVEL = {"schema_version", "library_version", "compiled_at", "staleness_budget_days", "sources"}
SOURCE_FIELDS = {"source_id", "platform", "domains", "role", "title", "url", "source_authority",
                 "status", "verified_at", "retrieval", "watch_urls", "watch_urls_absent_reason",
                 "carries", "does_not_carry", "language"}
RETRIEVAL_FIELDS = {"method"}

PLATFORMS = {"youtube", "instagram", "tiktok", "bilibili", "twitch", "other"}
DOMAINS = {"monetization", "content_policy", "originality", "recommendations", "terms",
           "platform_changes", "product_help"}
ROLES = {"rule_source", "change_signal"}
AUTHORITIES = {"official_help_page", "official_secondary", "creator_reported", "unofficial"}
STATUSES = {"verified", "unverified_draft"}
RETRIEVAL_METHODS = {"plain_fetch", "js_rendered", "browser_rendered"}

SOURCE_ID_RE = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
LANGUAGE_TAG_RE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")


def text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def https(value: object) -> bool:
    return text(value) and str(value).startswith("https://")


def iso_date(value: object) -> bool:
    if not text(value) or not DATE_RE.match(str(value)):
        return False
    try:
        date.fromisoformat(str(value))
    except ValueError:
        return False
    return True


def check_retrieval(value: object, where: str, errors: list[str]) -> None:
    if not isinstance(value, dict) or set(value) != RETRIEVAL_FIELDS:
        errors.append(f"{where}.retrieval must carry exactly {sorted(RETRIEVAL_FIELDS)}")
        return
    if value["method"] not in RETRIEVAL_METHODS:
        errors.append(f"{where}.retrieval.method must be one of {sorted(RETRIEVAL_METHODS)}")


def check_source(source: object, index: int, errors: list[str]) -> str | None:
    where = f"sources[{index}]"
    if not isinstance(source, dict) or set(source) != SOURCE_FIELDS:
        errors.append(f"{where} must carry exactly {sorted(SOURCE_FIELDS)}")
        return None

    source_id = source["source_id"]
    if not text(source_id) or not SOURCE_ID_RE.match(str(source_id)):
        errors.append(f"{where}.source_id must be a lowercase dotted slug")
        source_id = None
    else:
        where = f"sources[{source_id}]"

    if source["platform"] not in PLATFORMS:
        errors.append(f"{where}.platform must be one of {sorted(PLATFORMS)}")
    domains = source["domains"]
    if not isinstance(domains, list) or not domains:
        errors.append(f"{where}.domains must be a non-empty array")
    else:
        for domain in domains:
            if not isinstance(domain, str) or domain not in DOMAINS:
                errors.append(f"{where}.domains has an invalid entry {domain!r}")
        valid = [domain for domain in domains if isinstance(domain, str)]
        if len(valid) != len(set(valid)):
            errors.append(f"{where}.domains contains duplicates")
    if source["role"] not in ROLES:
        errors.append(f"{where}.role must be one of {sorted(ROLES)}")
    # SKILL.md has said from the start that a cross-platform row is in scope as a change_signal and
    # never as a source, and nothing enforced it. It happened not to matter only because the one
    # `other` row is also unofficial, which the rule further down already catches - so the gap was
    # invisible rather than absent. A `platform: other` row that is an official page would have been
    # accepted here and refused by SKILL.md, and at runtime the code is what runs.
    if source["platform"] == "other" and source["role"] != "change_signal":
        errors.append(f"{where}.platform is 'other', so it covers several platforms at once and can "
                      "only be a change_signal: evidence is bound to the platform that published it, "
                      "and a cross-platform row cannot say what any one of them now states. Register "
                      "the platform's own page and quote that instead")
    # The language the page is written in decides whether a report owes its quotes a gloss.
    if not isinstance(source["language"], str) or not LANGUAGE_TAG_RE.match(source["language"]):
        errors.append(f"{where}.language must be the BCP-47 tag of the language the page is written "
                      "in, such as en or zh")
    if not text(source["title"]):
        errors.append(f"{where}.title must be non-empty")
    if not text(source["carries"]):
        errors.append(f"{where}.carries must be non-empty - a page nobody can say the purpose of "
                      "is a page nobody should be reading")
    if source["does_not_carry"] is not None and not text(source["does_not_carry"]):
        errors.append(f"{where}.does_not_carry must be null or non-empty")

    status = source["status"]
    if status not in STATUSES:
        errors.append(f"{where}.status must be one of {sorted(STATUSES)}")
    authority = source["source_authority"]
    if authority not in AUTHORITIES:
        errors.append(f"{where}.source_authority must be one of {sorted(AUTHORITIES)}")

    # verified_at records that somebody fetched the page, so it exists exactly when the row claims
    # to have been checked. A verified row with no date, or a draft carrying one, means the two
    # halves disagree and neither can be trusted.
    verified_at = source["verified_at"]
    if status == "verified":
        if not iso_date(verified_at):
            errors.append(f"{where} is verified but carries no valid verified_at date")
    elif verified_at is not None:
        errors.append(f"{where} is an unverified_draft but carries a verified_at date - one of the "
                      "two is wrong")

    # A page that needs a browser is a page a plain re-check reads as empty, so calling it
    # verified asserts a reading nobody without a browser can reproduce. The old test tried to hold
    # this by iterating the shipped rows - and went vacuous the moment the last browser_rendered row
    # was reclassified. It belongs in the validator, where an empty registry cannot silence it.
    retrieval = source.get("retrieval")
    if isinstance(retrieval, dict) and retrieval.get("method") == "browser_rendered" \
            and status == "verified":
        errors.append(f"{where} is browser_rendered and verified at the same time: a page a plain "
                      "fetch reads as empty cannot carry a reading anyone can reproduce. Record it "
                      "as unverified_draft, or reclassify the retrieval if it turned out to be "
                      "readable after decoding")

    url = source["url"]
    if url is None:
        if status != "unverified_draft":
            errors.append(f"{where}.url may only be null on an unverified_draft")
    elif not https(url):
        errors.append(f"{where}.url must be an https URL")

    check_retrieval(source["retrieval"], where, errors)

    watch_urls = source["watch_urls"]
    absent_reason = source["watch_urls_absent_reason"]
    if watch_urls is None:
        if not text(absent_reason):
            errors.append(f"{where}.watch_urls is null, so watch_urls_absent_reason must say why. "
                          "An official page whose changes appear somewhere nobody wrote down is "
                          "how a re-check reports a moved rule as unchanged.")
    else:
        if absent_reason is not None:
            errors.append(f"{where} has watch_urls, so watch_urls_absent_reason must be null")
        if not isinstance(watch_urls, list) or not watch_urls:
            errors.append(f"{where}.watch_urls must be null or a non-empty array")
        else:
            # Validate before deduplicating: set() over a list holding an unhashable entry raises,
            # and a validator that dies on malformed input is not validating it.
            usable = []
            for entry in watch_urls:
                if not https(entry):
                    errors.append(f"{where}.watch_urls has an entry that is not an https URL: "
                                  f"{entry!r}")
                else:
                    usable.append(entry)
            if len(usable) != len(set(usable)):
                errors.append(f"{where}.watch_urls contains duplicates")
            if url is not None and url in watch_urls:
                errors.append(f"{where}.watch_urls repeats its own url; it lists the OTHER pages a "
                              "change would surface on")

    # An unofficial source can be a reason to go and look. It can never be the thing looked at.
    if authority == "unofficial" and source["role"] == "rule_source":
        errors.append(f"{where} is unofficial, so its role must be change_signal - an unofficial "
                      "page may send a run to an official one and may never be quoted as one")

    return str(source_id) if source_id else None


def validate(value: object) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, dict) or set(value) != TOP_LEVEL:
        return [f"top-level fields must be exactly {sorted(TOP_LEVEL)}"]
    if value["schema_version"] != "platform-policy-sources.v1":
        errors.append("schema_version must be platform-policy-sources.v1")
    if not text(value["library_version"]):
        errors.append("library_version must be non-empty")
    if not iso_date(value["compiled_at"]):
        errors.append("compiled_at must be an ISO date")
    elif date.fromisoformat(value["compiled_at"]) > date.today():
        errors.append("compiled_at is in the future")
    budget = value["staleness_budget_days"]
    if not isinstance(budget, int) or isinstance(budget, bool) or budget <= 0:
        errors.append("staleness_budget_days must be a positive integer")

    sources = value["sources"]
    if not isinstance(sources, list) or not sources:
        return errors + ["sources must be a non-empty array"]

    seen: set[str] = set()
    for index, source in enumerate(sources):
        source_id = check_source(source, index, errors)
        if source_id is None:
            continue
        if source_id in seen:
            errors.append(f"duplicate source_id {source_id!r}")
        seen.add(source_id)
    return errors


def load_library(directory: str | Path) -> dict:
    """Merge every registry file in `directory` into one registry object.

    The registry is split into several files so that each stays small enough to read; the files
    share one header, and a header that differs between files is an error, not a merge choice.
    """
    files = sorted(Path(directory).glob("*.json"))
    if not files:
        raise ValueError(f"no source registry files in {directory}")
    header: dict | None = None
    sources: list = []
    for path in files:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
        if not isinstance(value, dict) or not isinstance(value.get("sources"), list):
            raise ValueError(f"{path.name}: not a source registry file")
        head = {k: v for k, v in value.items() if k != "sources"}
        if header is None:
            header = head
        elif head != header:
            raise ValueError(f"{path.name}: header fields differ from {files[0].name}")
        sources.extend(value["sources"])
    return {**(header or {}), "sources": sources}


def summarize(value: dict) -> dict:
    sources = value["sources"]
    needs_browser = sorted(s["source_id"] for s in sources
                           if s.get("retrieval", {}).get("method") == "browser_rendered")
    unlocated = sorted(s["source_id"] for s in sources if s.get("url") is None)
    return {
        "sources": len(sources),
        "rule_sources": sorted(s["source_id"] for s in sources if s.get("role") == "rule_source"),
        "change_signals": sorted(s["source_id"] for s in sources
                                 if s.get("role") == "change_signal"),
        "unverified_draft": sorted(s["source_id"] for s in sources
                                   if s.get("status") == "unverified_draft"),
        "needs_a_browser": needs_browser,
        "page_not_located": unlocated,
        "note": ("A browser_rendered page re-checked without a browser returns an empty body, and "
                 "an empty body cannot be told apart from an unchanged rule."),
    }


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: validate_sources.py <references/sources directory or one registry file>", file=sys.stderr)
        return 2
    if Path(argv[1]).is_dir():
        value = load_library(argv[1])
    else:
        with open(argv[1], encoding="utf-8") as handle:
            value = json.load(handle)
    errors = validate(value)
    report = {"valid": not errors, "errors": errors}
    if not errors:
        report["summary"] = summarize(value)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
