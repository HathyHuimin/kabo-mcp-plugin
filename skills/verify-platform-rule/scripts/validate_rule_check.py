#!/usr/bin/env python3
"""Check one run's findings against the contract, with the source registry as the authority.

Two rules here carry the weight, and both exist because the alternative is a report that reads
exactly the same whether or not anybody looked anything up.

The first: a claim either quotes a page or records that the pages successfully read did not cover
it. `no_official_text` is a real result for this check, not proof that the platform has never
published the answer. Its mechanism explanation stays in its own field rather than mixed into a
list of quotes. Once the two sit in one list, the reader has no way to tell which sentences the
platform stands behind.

The second: what a source is allowed to settle comes from the registry, not from the run. An
unofficial archive can tell you a document moved. It cannot tell you what it now says, and a run
that quotes one as though it could has produced a confident answer from a source nobody vetted.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
# Sibling imports must not leave a __pycache__ in the installed skill directory.
sys.dont_write_bytecode = True
from record_passages import load_page, quoted  # noqa: E402  - one definition of "is this the page's text"

TOP_LEVEL_REQUIRED = {"schema_version", "platform", "region", "region_source",
                      "delivery_language", "checked_at", "claims"}
TOP_LEVEL_OPTIONAL = {"unreachable_sources"}
CLAIM_REQUIRED = {"claim_id", "statement", "verdict", "evidence"}
CLAIM_OPTIONAL = {"inference", "scope", "unreached_source"}
EVIDENCE_REQUIRED = {"source_id", "url", "fetched_url", "quote", "quote_source",
                     "retrieved_at", "source_authority"}
EVIDENCE_OPTIONAL = {"effective_from", "gloss"}
UNREACHABLE_FIELDS = {"source_id", "url", "reason"}
QUOTE_SOURCES = {"raw_document", "host_extract"}

PLATFORMS = {"youtube", "instagram", "tiktok", "bilibili", "twitch", "other"}
REGION_SOURCES = {"user_stated", "account_declared", "unknown"}
VERDICTS = {"confirmed", "refuted", "partly_true", "no_official_text", "not_checked"}
# The two verdicts that stand without a quote, for opposite reasons.
UNSOURCED_VERDICTS = {"no_official_text", "not_checked"}
AUTHORITIES = {"official_help_page", "official_secondary", "creator_reported", "unofficial"}
WEAK_AUTHORITIES = {"creator_reported", "unofficial"}

REGION_RE = re.compile(r"^([A-Z]{2}|UNKNOWN)$")
LANGUAGE_TAG_RE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def iso_date(value: object) -> bool:
    if not text(value) or not DATE_RE.match(str(value)):
        return False
    try:
        date.fromisoformat(str(value)[:10])
    except ValueError:
        return False
    return True


def primary_language(tag: object) -> str:
    return str(tag).split("-", 1)[0].lower()


def page_identity(url: object) -> str:
    """Ignore presentation variants, never parameters that can identify another document.

    Help pages such as faq_detail?id=... share a path. Dropping the entire query aliases distinct
    articles and lets one source supply another's quote. Unknown parameters stay significant.
    """
    try:
        parsed = urlsplit(str(url))
    except ValueError:
        # Invalid URLs are rejected by the surrounding contract; identity must not crash it.
        return str(url).split("#", 1)[0]
    presentation = {"hl", "gl", "lang", "language", "locale", "region"}
    query = sorted((key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
                   if key not in presentation)
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, urlencode(query), ""))


def host(url: object) -> str:
    """The host of an https URL, lowercased. Empty when it does not parse as one."""
    text_url = str(url)
    if not text_url.startswith("https://"):
        return ""
    return text_url[len("https://"):].split("/", 1)[0].split("@")[-1].lower()


def load_registry(path: str) -> dict[str, dict]:
    if os.path.isdir(path):
        from validate_sources import load_library
        value = load_library(path)
    else:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
    return {s["source_id"]: s for s in value.get("sources", []) if isinstance(s, dict)
            and text(s.get("source_id"))}


def nested_objects(value: Any) -> Iterator[dict[str, Any]]:
    """Yield objects from direct or host-wrapped JSON without interpreting page text."""
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from nested_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from nested_objects(child)
    elif isinstance(value, str) and value.lstrip().startswith("{"):
        try:
            child = json.loads(value)
        except json.JSONDecodeError:
            return
        yield from nested_objects(child)


def load_page_texts(snapshot_dir: str) -> dict[str, list[str]]:
    """Index exact text from successful persisted official-page-render Envelopes by URL."""
    root = Path(snapshot_dir)
    if not root.is_dir():
        raise ValueError(f"snapshot directory does not exist: {snapshot_dir}")
    result: dict[str, list[str]] = {}
    for path in sorted(root.rglob("*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for item in nested_objects(value):
            if (item.get("connector_id") != "official-page-render"
                    or item.get("operation") != "render_page"
                    or item.get("status") not in {"completed", "completed_partial"}):
                continue
            data = item.get("data")
            if not isinstance(data, dict) or not text(data.get("text")):
                continue
            for key in ("url", "fetched_url"):
                if text(data.get(key)):
                    result.setdefault(page_identity(data[key]), []).append(str(data["text"]))
    return result


def check_evidence(item: object, where: str, registry: dict[str, dict],
                   verdict: str, language: str, platform: str, errors: list[str],
                   page_texts: dict[str, list[str]] | None = None) -> None:
    if not isinstance(item, dict):
        errors.append(f"{where} must be an object")
        return
    extra = set(item) - (EVIDENCE_REQUIRED | EVIDENCE_OPTIONAL)
    missing = EVIDENCE_REQUIRED - set(item)
    if extra:
        errors.append(f"{where} has unknown fields {sorted(extra)}")
    if missing:
        errors.append(f"{where} is missing {sorted(missing)}")
        return

    if not text(item["url"]) or not str(item["url"]).startswith("https://"):
        errors.append(f"{where}.url must be an https URL")
    if not text(item["quote"]):
        errors.append(f"{where}.quote must be non-empty - a verdict with no words from the "
                      "platform is this Skill's opinion wearing the platform's authority")
    if not iso_date(item["retrieved_at"]):
        errors.append(f"{where}.retrieved_at must be an ISO date")
    authority = item["source_authority"]
    if authority not in AUTHORITIES:
        errors.append(f"{where}.source_authority must be one of {sorted(AUTHORITIES)}")

    if item["quote_source"] not in QUOTE_SOURCES:
        errors.append(f"{where}.quote_source must be one of {sorted(QUOTE_SOURCES)}")
    gloss = item.get("gloss")
    if gloss is not None and not text(gloss):
        errors.append(f"{where}.gloss must be null or a non-empty translation")

    source_id = item["source_id"]
    row = registry.get(str(source_id))
    if row is None:
        errors.append(f"{where}.source_id {source_id!r} is not in the source registry - a run may "
                      "only read pages the registry lists")
        return
    # A report whose evidence body stays in a language its reader does not read is a report the
    # person it was written for cannot use. The registry says what language each page is in; a
    # registry written before it said so held only English pages.
    page_language = row.get("language") or "en"
    if gloss is None and primary_language(page_language) != primary_language(language):
        errors.append(f"{where} has no gloss, but the page is in {page_language!r} and the report "
                      f"is being delivered in {language!r} - a quote the reader cannot read is not "
                      "evidence to them")
    # A registry row can be a rule_source and still be a draft nobody has read yet - the
    # creator_insider row is exactly that, and its own does_not_carry says "anything binding".
    # Checking role and authority but not status left a run free to stake a verdict on a page the
    # registry itself says has never been opened.
    if row.get("status") != "verified":
        errors.append(f"{where}.source_id {source_id!r} is registered as "
                      f"{row.get('status')!r}: nobody has read that page yet, so it cannot settle "
                      "a claim. Record the correction in analysis/registry-corrections.json and "
                      "drop the claim - never edit the registry to get past this: it is "
                      "packaged content, and an edited registry no longer matches the reviewed package")
    # A page is evidence about the platform that published it. Checking status and role but not
    # platform let a verified YouTube help page settle a claim in a TikTok report - the quote would
    # be real, the date would be real, and the answer would be about the wrong company.
    row_platform = row.get("platform")
    if row_platform not in (platform, "other"):
        errors.append(f"{where}.source_id {source_id!r} is a {row_platform!r} page, but this "
                      f"report is about {platform!r}. A real quote from the wrong platform is "
                      "still the wrong answer")
    if row.get("role") != "rule_source":
        errors.append(f"{where}.source_id {source_id!r} has role {row.get('role')!r}; only a "
                      "rule_source may be quoted. A change_signal reports that something moved, "
                      "never what it moved to")
    if authority in AUTHORITIES and row.get("source_authority") != authority:
        errors.append(f"{where}.source_authority is {authority!r} but the registry records "
                      f"{row.get('source_authority')!r} for {source_id!r}")
    # Step 2 pushes a run toward a region-parameterised variant of the same page, and the
    # registry stores the base form. Only presentation parameters may vary: content identifiers
    # such as TikTok's id still distinguish different pages on the same path.
    if row.get("url") is not None and page_identity(item["url"]) != page_identity(row["url"]):
        errors.append(f"{where}.url is a different page from the one registered for "
                      f"{source_id!r}")
    # Following the platform's own redirect is allowed; landing somewhere else and citing the
    # registered form is how an unapproved host gets quoted under an official platform's name.
    # The cited URL alone cannot catch that, because it is the one that did not change.
    if not str(item["fetched_url"]).startswith("https://"):
        errors.append(f"{where}.fetched_url must be an https URL - it is where the bytes actually "
                      "came from, and equals url when nothing redirected")
    elif row.get("url") is not None and host(item["fetched_url"]) != host(row["url"]):
        errors.append(f"{where} was fetched from {host(item['fetched_url'])!r} but is cited as "
                      f"{host(row['url'])!r}. A redirect off the platform's own host is not that "
                      "platform speaking: record it in unreachable_sources, or register the new "
                      "page before quoting it")

    # A creator's report and an unofficial page are worth relaying. Neither is worth staking a
    # "confirmed" on: the creator acts on that word, and nobody vetted what stands behind it.
    if verdict == "confirmed" and authority in WEAK_AUTHORITIES:
        errors.append(f"{where} carries authority {authority!r}, so this claim cannot be "
                      "confirmed - relay it as partly_true and say who said it")

    if page_texts is not None and text(item.get("quote")):
        pages: list[str] = []
        for url in (item.get("url"), item.get("fetched_url")):
            if text(url):
                pages.extend(page_texts.get(page_identity(url), []))
        if not pages:
            errors.append(
                f"{where}.quote has no successful persisted official-page-render snapshot for "
                "its URL - a report cannot claim verbatim evidence when the fetched bytes are absent"
            )
        elif not any(quoted(str(item["quote"]), page) for page in pages):
            errors.append(
                f"{where}.quote is not verbatim in the persisted page text - only Markdown "
                "presentation may be set aside; preserve the words, punctuation, and line breaks"
            )


def check_claim(claim: object, index: int, registry: dict[str, dict], language: str,
                platform: str, unreached: set[str], errors: list[str],
                page_texts: dict[str, list[str]] | None = None) -> str | None:
    where = f"claims[{index}]"
    if not isinstance(claim, dict):
        errors.append(f"{where} must be an object")
        return None
    extra = set(claim) - (CLAIM_REQUIRED | CLAIM_OPTIONAL)
    missing = CLAIM_REQUIRED - set(claim)
    if extra:
        errors.append(f"{where} has unknown fields {sorted(extra)}")
    if missing:
        errors.append(f"{where} is missing {sorted(missing)}")
        return None

    claim_id = claim["claim_id"]
    if not text(claim_id):
        errors.append(f"{where}.claim_id must be non-empty")
        claim_id = None
    else:
        where = f"claims[{claim_id}]"
    if not text(claim["statement"]):
        errors.append(f"{where}.statement must be non-empty")

    verdict = claim["verdict"]
    if verdict not in VERDICTS:
        errors.append(f"{where}.verdict must be one of {sorted(VERDICTS)}")
        return str(claim_id) if claim_id else None

    evidence = claim["evidence"]
    if not isinstance(evidence, list):
        errors.append(f"{where}.evidence must be an array")
        return str(claim_id) if claim_id else None

    scope = claim.get("scope")
    if verdict == "partly_true":
        if not text(scope):
            errors.append(f"{where} is partly_true, so scope must say which half is true and what "
                          "limits it. Without it the report shows a verdict word and a block of "
                          "the platform's own language, and the reader is left doing the "
                          "reasoning they asked for")
    elif scope is not None:
        errors.append(f"{where} is {verdict!r}, so scope must be null - it exists to split a "
                      "half-right claim, and on a settled one it can only editorialise")

    inference = claim.get("inference")
    if verdict in UNSOURCED_VERDICTS:
        if evidence:
            errors.append(f"{where} is {verdict!r} but carries evidence - if a page settles it, "
                          "the verdict is not that no page was read")
        if not text(inference):
            errors.append(f"{where} is {verdict!r}, so inference must explain the mechanism. "
                          "Saying nothing leaves the creator with a question mark where this Skill "
                          "does have something useful and unofficial to say")
        # not_checked is a statement about this run, and it has to name the page it failed on.
        # Checking only that SOME page went unread let one unrelated failure vouch for every
        # not_checked claim in the document.
        pointer = claim.get("unreached_source")
        if verdict == "not_checked":
            if not text(pointer):
                errors.append(f"{where} is not_checked, so unreached_source must name the page "
                              "that would have settled it - a non-empty unreachable list is not "
                              "the same as one that covers this claim")
            elif str(pointer) not in unreached:
                errors.append(f"{where}.unreached_source {pointer!r} is not in "
                              "unreachable_sources: the page it blames was never recorded as "
                              "unread")
        elif pointer is not None:
            errors.append(f"{where} is {verdict!r}, so unreached_source must be null")
    else:
        if not evidence:
            errors.append(f"{where} is {verdict!r} but quotes nothing. Only no_official_text and "
                          "not_checked may stand without a source")
        if claim.get("unreached_source") is not None:
            errors.append(f"{where} is {verdict!r}, so unreached_source must be null - it exists to "
                          "name a page that could not be read, and this claim quotes one")
        if inference is not None:
            errors.append(f"{where} carries an inference alongside quoted evidence. Keep them "
                          "apart: once they share a list the reader cannot tell which sentences "
                          "the platform stands behind")
        for position, item in enumerate(evidence):
            check_evidence(item, f"{where}.evidence[{position}]", registry, verdict,
                           language, platform, errors, page_texts)

    return str(claim_id) if claim_id else None


def validate(value: object, registry: dict[str, dict],
             page_texts: dict[str, list[str]] | None = None) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, dict):
        return ["the document must be an object"]
    extra = set(value) - (TOP_LEVEL_REQUIRED | TOP_LEVEL_OPTIONAL)
    missing = TOP_LEVEL_REQUIRED - set(value)
    if extra:
        errors.append(f"unknown top-level fields {sorted(extra)}")
    if missing:
        return errors + [f"missing top-level fields {sorted(missing)}"]

    if value["schema_version"] != "rule-check.v1":
        errors.append("schema_version must be rule-check.v1")
    if value["platform"] not in PLATFORMS:
        errors.append(f"platform must be one of {sorted(PLATFORMS)}")
    if not text(value["region"]) or not REGION_RE.match(str(value["region"])):
        errors.append("region must be an ISO 3166-1 alpha-2 code or UNKNOWN - never a guess, "
                      "because several of these pages serve different text by country")
    if value["region_source"] not in REGION_SOURCES:
        errors.append(f"region_source must be one of {sorted(REGION_SOURCES)}")
    if value["region"] == "UNKNOWN" and value["region_source"] != "unknown":
        errors.append("region is UNKNOWN, so region_source must be unknown too")
    if not iso_date(value["checked_at"]):
        errors.append("checked_at must be an ISO date")
    language = value["delivery_language"]
    if not isinstance(language, str) or not LANGUAGE_TAG_RE.match(language):
        errors.append("delivery_language must be a BCP-47 tag such as en, zh-CN or pt-BR")
        language = "en"

    claims = value["claims"]
    if not isinstance(claims, list) or not claims:
        return errors + ["claims must be a non-empty array"]

    # Shape first, then read. This validator has now been crashed three times by the same habit -
    # set() over unhashables, .get on a string, and iterating whatever unreachable_sources happens
    # to be - and a validator that dies on malformed input is not validating it.
    raw_unreachable = value.get("unreachable_sources")
    if raw_unreachable is None:
        raw_unreachable = []
    elif not isinstance(raw_unreachable, list):
        errors.append("unreachable_sources must be an array")
        raw_unreachable = []

    # Every page this run failed to read, by whichever handle it was recorded under. A
    # not_checked claim has to point at one of these - see check_claim.
    unreached = {str(item.get(key)) for item in raw_unreachable
                 if isinstance(item, dict) for key in ("source_id", "url")
                 if text(item.get(key))}

    seen: set[str] = set()
    for index, claim in enumerate(claims):
        claim_id = check_claim(claim, index, registry, language, value["platform"],
                               unreached, errors, page_texts)
        if claim_id is None:
            continue
        if claim_id in seen:
            errors.append(f"duplicate claim_id {claim_id!r}")
        seen.add(claim_id)

    for index, item in enumerate(raw_unreachable):
        where = f"unreachable_sources[{index}]"
        if not isinstance(item, dict) or not set(item) <= UNREACHABLE_FIELDS:
            errors.append(f"{where} may carry only {sorted(UNREACHABLE_FIELDS)}")
            continue
        if not text(item.get("reason")):
            errors.append(f"{where}.reason must be non-empty")
        source_id, url = item.get("source_id"), item.get("url")
        # A watch page is a bare string in the registry with no id of its own. Requiring an id here
        # meant a watch fetch that failed had nowhere to be recorded, so it went unreported - and an
        # unread watch page is precisely the silence this Skill refuses to read as stability.
        if text(source_id) == text(url):
            errors.append(f"{where} must carry exactly one of source_id or url: the first names a "
                          "registered page, the second a watch page that has no id")
            continue
        if text(source_id) and str(source_id) not in registry:
            errors.append(f"{where}.source_id {source_id!r} is not in the source registry")
        if text(url) and not str(url).startswith("https://"):
            errors.append(f"{where}.url must be an https URL")

    return errors


def check_against_pages(value: object, snapshot_dir: str) -> list[str]:
    """Hold every raw_document quote to the page this run persisted for its source.

    The registry says which page may be quoted; only the page itself says what it said. A quote that
    was paraphrased, re-spaced or had its line breaks joined reads like the platform and is not in
    its page, and a URL or date retyped from a rendering can drift from the fetch it names.
    """
    errors: list[str] = []
    if not isinstance(value, dict) or not isinstance(value.get("claims"), list):
        return errors
    pages: dict[str, dict | None] = {}
    for index, claim in enumerate(value["claims"]):
        if not isinstance(claim, dict) or not isinstance(claim.get("evidence"), list):
            continue
        for number, item in enumerate(claim["evidence"]):
            where = f"claims[{index}].evidence[{number}]"
            if not isinstance(item, dict) or item.get("quote_source") != "raw_document":
                continue
            source_id = str(item.get("source_id"))
            if source_id not in pages:
                # A source id names one file in the snapshot directory, never a path out of it; the file
                # must be a usable render_page envelope with text, the same test record_passages applies.
                safe = re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", source_id) is not None
                try:
                    pages[source_id] = load_page(Path(snapshot_dir) / f"{source_id}.envelope.json")[1] if safe else None
                except (OSError, ValueError):
                    pages[source_id] = None
            page = pages[source_id]
            if page is None:
                errors.append(f"{where} quotes {source_id!r}, but no persisted page for it is in "
                              f"{snapshot_dir}: persist the render_page response before quoting it")
                continue
            if text(item.get("quote")) and not quoted(str(item["quote"]), page["text"]):
                errors.append(f"{where}.quote is not in the page persisted for {source_id!r}: quote the "
                              "page's own characters, line breaks included")
            if page.get("fetched_url") and item.get("fetched_url") != page["fetched_url"]:
                errors.append(f"{where}.fetched_url is not where the persisted page was fetched from "
                              f"({page['fetched_url']!r})")
            if page.get("url") and page_identity(item.get("url")) != page_identity(page["url"]):
                errors.append(f"{where}.url is not the page persisted for {source_id!r}")
            if (isinstance(page.get("retrieved_at"), str) and iso_date(item.get("retrieved_at"))
                    and str(item["retrieved_at"])[:10] != page["retrieved_at"][:10]):
                errors.append(f"{where}.retrieved_at is not the date the persisted page was read "
                              f"({page['retrieved_at'][:10]})")
    return errors


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Validate one rule-check.v1 document.")
    parser.add_argument("document")
    parser.add_argument("--sources", required=True,
                        help="the references/sources directory (or one registry file)")
    parser.add_argument("--snapshot-dir",
                        help="run snapshot directory containing persisted connector Envelopes")
    args = parser.parse_args(argv[1:])

    registry = load_registry(args.sources)
    with open(args.document, encoding="utf-8") as handle:
        value = json.load(handle)
    errors = validate(value, registry)
    if args.snapshot_dir:
        try:
            page_texts = load_page_texts(args.snapshot_dir)
        except ValueError as error:
            print(json.dumps({"valid": False, "errors": [str(error)]}, ensure_ascii=False, indent=2))
            return 1
        errors = validate(value, registry, page_texts)
        errors += check_against_pages(value, args.snapshot_dir)
    print(json.dumps({"valid": not errors, "errors": errors}, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
