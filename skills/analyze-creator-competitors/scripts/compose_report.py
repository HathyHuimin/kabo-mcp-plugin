#!/usr/bin/env python3
"""Compose a validated CompetitorResearchReport from one semantic skeleton."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
# Sibling imports must not leave a __pycache__ in the signed, checksummed skill directory.
sys.dont_write_bytecode = True
from competitor_gate_contract import eligibility_roles
from evidence_contract import (
    CREATOR_FACING_OUTLIER_MIN_MULTIPLE,
    RELIABLE_OUTLIER_MIN_SAMPLE,
)
from public_metrics import public_metric_entry
from select_accounts import assert_selection_matches_finalize, canonical_handle

SYNTHESIS_VERSION = "competitor-research-synthesis.v3.1"
REQUIRED_SYNTHESIS = {
    "schema_version", "report_id", "request_id", "status", "overview",
    "competitors", "featured_content_refs", "patterns", "gaps",
}
def seed_content_id(request: dict[str, Any]) -> str | None:
    subject = request.get("subject") if isinstance(request.get("subject"), dict) else {}
    value = subject.get("seed_content_ref")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def metric_values(item: dict[str, Any]) -> dict[str, float | None]:
    result: dict[str, float | None] = {}
    for name in ("views", "likes", "comments"):
        entry = public_metric_entry(item.get("metrics"), name)
        if entry is not None:
            result[name] = entry.get("value")
    return result


def title_excerpt(value: str) -> str:
    first = (value or "").strip().splitlines()[0].strip()
    return first[:96] + ("…" if len(first) > 96 else "")


def format_number(value: float) -> str:
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}".rstrip("0").rstrip(".")


def creator_url(platform: str, handle: str, creator_id: str | None = None) -> str:
    clean = handle.lstrip("@")
    if platform == "youtube":
        if creator_id:
            return f"https://www.youtube.com/channel/{creator_id}"
        return f"https://www.youtube.com/@{clean}"
    return f"https://www.instagram.com/{clean}/"


def account_label(platform: str, handle: str) -> str:
    """Render an account name without inventing a YouTube public @handle."""
    clean = handle.lstrip("@")
    return clean if platform == "youtube" else f"@{clean}"


def normalize_handle(value: str) -> str:
    return value.strip().lower().lstrip("@").rstrip("/")


HANDLE_TOKEN = re.compile(r"[A-Za-z0-9._]{3,}")


def hinted_creator_ids(creators: dict[str, dict[str, Any]], hint: str) -> list[str]:
    """Match a unique snapshot handle mentioned in the synthesis prose."""
    if not hint:
        return []
    tokens = {normalize_handle(token) for token in HANDLE_TOKEN.findall(hint)}
    found: list[str] = []
    for creator_id, creator in creators.items():
        handle = normalize_handle(str(creator.get("handle") or ""))
        if handle and handle in tokens:
            found.append(creator_id)
    return found


def resolve_account_ref(
    ref: str,
    creators: dict[str, dict[str, Any]],
    hint: str = "",
) -> str:
    """Resolve a canonical creator_id, public handle, or unique handle named in hint."""
    if ref in creators:
        return ref
    normalized = normalize_handle(ref)
    matches = [
        creator_id
        for creator_id, creator in creators.items()
        if normalize_handle(str(creator.get("handle") or "")) == normalized
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ValueError(f"competitor account_ref is ambiguous: {ref}")
    hinted = hinted_creator_ids(creators, hint)
    if len(hinted) == 1:
        return hinted[0]
    if len(hinted) > 1:
        raise ValueError(f"competitor account_ref is ambiguous: {ref}")
    raise ValueError(f"competitor account_ref is unresolved: {ref}")


def lookup_account_ref(
    ref: str,
    creators: dict[str, dict[str, Any]],
    hint: str = "",
) -> str | None:
    try:
        return resolve_account_ref(ref, creators, hint)
    except ValueError:
        return None


def known_outlier_multiples(analysis: dict[str, Any]) -> dict[str, float]:
    found: dict[str, float] = {}
    for item in analysis.get("account_outliers") or []:
        content_id = str(item.get("content_id") or "")
        multiple = item.get("account_median_multiple")
        if (
            content_id
            and isinstance(multiple, (int, float))
            and not isinstance(multiple, bool)
        ):
            found[content_id] = float(multiple)
    return found


def below_creator_facing_floor(ref: str, multiples: dict[str, float]) -> bool:
    multiple = multiples.get(str(ref))
    return multiple is not None and multiple < CREATOR_FACING_OUTLIER_MIN_MULTIPLE


def proof_point(
    content: dict[str, Any],
    creator: dict[str, Any],
    outlier: dict[str, Any] | None,
    platform: str,
) -> str:
    """Build one English proof sentence; the host relays it in the Creator's language."""
    handle = str(creator.get("handle") or creator.get("creator_id") or "unknown").lstrip("@")
    account = account_label(platform, handle)
    title = title_excerpt(str(content.get("title") or content.get("content_id")))
    metrics = metric_values(content)
    if outlier:
        value = float(outlier["views"])
        baseline = float(outlier["baseline_median_views"])
        ratio = float(outlier["account_median_multiple"])
        format_note = ""
        if platform == "youtube" and content.get("format_confidence") == "provisional":
            format_note = "; short/long format inferred from duration, not a platform label"
        return (
            f'{account}\'s “{title}” received {format_number(value)} public views, '
            f"versus a same-account, same-format median of {format_number(baseline)} "
            f"({ratio:.2f}×{format_note})"
        )
    for metric_name in ("views", "likes", "comments"):
        value = metrics.get(metric_name)
        if value is not None:
            label = {"views": "public views", "likes": "public likes", "comments": "public comments"}[metric_name]
            return f'{account}\'s “{title}” received {format_number(float(value))} {label}'
    raise ValueError(f"content {content.get('content_id')} has no public metric for proof")


def reliable_outlier(item: dict[str, Any]) -> bool:
    multiple = item.get("account_median_multiple")
    if (
        isinstance(multiple, bool)
        or not isinstance(multiple, (int, float))
        or multiple < CREATOR_FACING_OUTLIER_MIN_MULTIPLE
    ):
        return False
    sample = item.get("comparable_n")
    return (
        isinstance(sample, int)
        and not isinstance(sample, bool)
        and sample >= RELIABLE_OUTLIER_MIN_SAMPLE
    )


def relationship_for_creator(
    ref: str,
    creator: dict[str, Any],
    roles: dict[str, str],
) -> str:
    handle = normalize_handle(str(creator.get("handle") or ""))
    if ref in roles and roles[ref] in {"direct", "adjacent", "media"}:
        return roles[ref]
    for key, role in roles.items():
        if role in {"direct", "adjacent", "media"} and (
            key == ref or normalize_handle(key) == handle
        ):
            return role
    return "unspecified"


def included_handle_set(selection: dict[str, Any] | None) -> set[str] | None:
    if selection is None:
        return None
    if selection.get("schema_version") != "competitor-account-selection.v1":
        raise ValueError("expected competitor-account-selection.v1")
    return {canonical_handle(str(value)) for value in selection.get("included_handles") or []}


def creator_on_selection(ref: str, creator: dict[str, Any], included: set[str] | None) -> bool:
    if included is None:
        return True
    handle = canonical_handle(str(creator.get("handle") or ref))
    return handle in included or canonical_handle(ref) in included


def snapshot_handle_set(creators: dict[str, dict[str, Any]]) -> set[str]:
    found: set[str] = set()
    for creator_id, creator in creators.items():
        found.add(canonical_handle(str(creator.get("handle") or creator_id)))
        found.add(canonical_handle(creator_id))
    return found


def stub_selected_creator(platform: str, handle: str) -> dict[str, Any]:
    clean = canonical_handle(handle)
    return {
        "creator_id": clean,
        "handle": clean,
        "source_url": creator_url(platform, clean),
    }


def ensure_selected_creators(
    creators: dict[str, dict[str, Any]],
    included: set[str] | None,
    platform: str,
) -> None:
    """Keep included handles resolvable when Snapshot omitted the creator row.

    The assembler only emits creators that have at least one content item.
    Profile-discovered accounts with zero in-window Reels therefore never
    appear in snapshot.creators, while synthesis still names them by handle.
    """
    if not included:
        return
    present = snapshot_handle_set(creators)
    for handle in included:
        if handle in present:
            continue
        creators[handle] = stub_selected_creator(platform, handle)
        present.add(handle)


def analyzed_sample_size(
    ref: str,
    contents: dict[str, dict[str, Any]],
    evidence_ids: set[str],
) -> int:
    return sum(
        1
        for content in contents.values()
        if str(content.get("creator_id")) == ref and str(content.get("content_id")) in evidence_ids
    )


def zero_sample_summary(platform: str, handle: str) -> str:
    label = account_label(platform, handle)
    return f"{label} was selected but had no analyzable content in the requested window."


def first_evidence_title(
    ref: str,
    contents: dict[str, dict[str, Any]],
    evidence_ids: set[str],
) -> str:
    for content in contents.values():
        if str(content.get("creator_id")) != ref:
            continue
        if str(content.get("content_id")) not in evidence_ids:
            continue
        title = str(content.get("title") or "").strip()
        if title:
            return title
    return ""


def compose(
    request: dict[str, Any],
    snapshot: dict[str, Any],
    analysis: dict[str, Any],
    synthesis: dict[str, Any],
    eligibility: dict[str, Any] | None = None,
    selection: dict[str, Any] | None = None,
    discovery: dict[str, Any] | None = None,
    eligibility_input: dict[str, Any] | None = None,
) -> dict[str, Any]:
    missing = sorted(REQUIRED_SYNTHESIS - synthesis.keys())
    if missing:
        raise ValueError(f"synthesis missing fields: {', '.join(missing)}")
    if synthesis.get("schema_version") != SYNTHESIS_VERSION:
        raise ValueError(f"synthesis schema_version must be {SYNTHESIS_VERSION}")
    if synthesis.get("request_id") != request.get("request_id"):
        raise ValueError("synthesis request_id does not match request")
    if selection is not None:
        assert_selection_matches_finalize(
            selection,
            discovery,
            eligibility,
            eligibility_input=eligibility_input,
        )

    included = included_handle_set(selection)
    platform = str(snapshot.get("platform") or request["platforms"][0])
    creators = {str(item["creator_id"]): item for item in snapshot.get("creators") or []}
    ensure_selected_creators(creators, included, platform)
    contents = {str(item["content_id"]): item for item in snapshot.get("items") or []}
    evidence_ids = {str(item["content_id"]) for item in analysis.get("evidence") or []}
    outliers = {
        str(item["content_id"]): item
        for item in analysis.get("account_outliers") or []
        if reliable_outlier(item)
    }

    selected_accounts: list[str] = []
    omitted_zero_sample_accounts: list[dict[str, str]] = []
    omitted_unresolved_refs: list[str] = []
    omitted_content_refs: list[str] = []
    omitted_weak_featured: list[str] = []
    competitors: list[dict[str, Any]] = []
    listed: set[str] = set()
    multiples = known_outlier_multiples(analysis)

    def append_competitor(
        ref: str,
        creator: dict[str, Any],
        relationship: Any,
        summary: Any,
        sample_size: int,
    ) -> None:
        handle = str(creator.get("handle") or ref).lstrip("@")
        if sample_size < 1:
            omitted_zero_sample_accounts.append({"account_ref": ref, "handle": handle})
            summary = summary or zero_sample_summary(platform, handle)
        else:
            selected_accounts.append(ref)
        row = {
            "account_ref": ref,
            "handle": handle,
            "url": creator_url(platform, handle, ref),
            "platform": platform,
            "relationship": relationship,
            "sample_size": sample_size,
        }
        # A synthesis may omit competitor summaries (0.5.7); the roster then shows handle + relationship.
        if summary:
            row["summary"] = summary
        competitors.append(row)
        listed.add(ref)

    for item in synthesis.get("competitors") or []:
        supplied_ref = str(item.get("account_ref") or "")
        ref = lookup_account_ref(supplied_ref, creators, str(item.get("summary") or ""))
        if ref is None:
            if supplied_ref and supplied_ref not in omitted_unresolved_refs:
                omitted_unresolved_refs.append(supplied_ref)
            continue
        creator = creators[ref]
        if not creator_on_selection(ref, creator, included):
            continue
        if ref in listed:
            # A repeated account (the same ref twice, or a handle that resolves to an id already
            # listed) adds no evidence; keep its first entry instead of discarding the whole report.
            continue
        sample_size = analyzed_sample_size(ref, contents, evidence_ids)
        if sample_size < 1 and included is None:
            omitted_zero_sample_accounts.append({
                "account_ref": ref,
                "handle": str(creator.get("handle") or ref).lstrip("@"),
            })
            listed.add(ref)
            continue
        append_competitor(ref, creator, item.get("relationship"), item.get("summary"), sample_size)

    roles = eligibility_roles(eligibility) if eligibility else {}
    for ref, creator in creators.items():
        if ref in listed or not creator_on_selection(ref, creator, included):
            continue
        sample_size = analyzed_sample_size(ref, contents, evidence_ids)
        if sample_size < 1 and included is None:
            continue
        if sample_size < 1:
            append_competitor(ref, creator, relationship_for_creator(ref, creator, roles), None, 0)
            continue
        hint = first_evidence_title(ref, contents, evidence_ids)
        label = account_label(platform, str(creator.get("handle") or ref).lstrip("@"))
        summary = f"{label}: {hint}"
        if not hint:
            summary = label
        append_competitor(ref, creator, relationship_for_creator(ref, creator, roles), summary, sample_size)

    if included is not None:
        order = [canonical_handle(str(value)) for value in (selection or {}).get("included_handles") or []]
        competitors.sort(
            key=lambda row: order.index(canonical_handle(row["handle"]))
            if canonical_handle(row["handle"]) in order
            else len(order)
        )

    selected_set = set(selected_accounts)
    seed_ref = seed_content_id(request)

    def usable_content_ref(ref: str) -> bool:
        content = contents.get(str(ref))
        if not content or str(ref) not in evidence_ids:
            return False
        return str(content.get("creator_id")) in selected_set or str(ref) == seed_ref

    refs: list[str] = []
    for ref in synthesis.get("featured_content_refs") or []:
        if not usable_content_ref(str(ref)):
            if str(ref) not in omitted_content_refs:
                omitted_content_refs.append(str(ref))
            continue
        if below_creator_facing_floor(str(ref), multiples):
            if str(ref) not in omitted_weak_featured:
                omitted_weak_featured.append(str(ref))
            continue
        if str(ref) not in refs:
            refs.append(str(ref))
    for item in (synthesis.get("patterns") or []) + (synthesis.get("gaps") or []):
        for ref in item.get("content_refs") or []:
            if not usable_content_ref(str(ref)):
                if str(ref) not in omitted_content_refs:
                    omitted_content_refs.append(str(ref))
                continue
            if str(ref) not in refs:
                refs.append(str(ref))

    top_content: list[dict[str, Any]] = []
    for rank, ref in enumerate(refs, 1):
        item = contents[str(ref)]
        top_content.append({
            "rank": rank,
            "content_id": str(ref),
            "account_ref": str(item["creator_id"]),
            "title": str(item.get("title") or ""),
            "url": item["url"],
            "platform": snapshot.get("platform"),
            "format": item["format"],
            **({"format_confidence": item["format_confidence"]} if item.get("format_confidence") else {}),
            **({"format_source": item["format_source"]} if item.get("format_source") else {}),
            "published_at": item["published_at"],
            "metrics": metric_values(item),
        })

    final_outliers: list[dict[str, Any]] = []
    for ref in refs:
        item = outliers.get(str(ref))
        if not item or any(item.get(key) is None for key in (
            "views", "baseline_median_views", "account_median_multiple", "comparable_n"
        )):
            continue
        final_outliers.append({
            "content_id": str(ref),
            "account_ref": str(item["creator_id"]),
            "format": item["format"],
            "evidence_strength": "reliable",
            **({"format_confidence": item["format_confidence"]} if item.get("format_confidence") else {}),
            **({"format_source": item["format_source"]} if item.get("format_source") else {}),
            "metric": "views",
            "value": item["views"],
            "baseline": {
                "value": item["baseline_median_views"],
                "sample_size": item["comparable_n"],
                "cohort": (
                    f"{account_label(platform, str(item.get('handle') or item['creator_id']))} same-account same-format public sample"
                ),
            },
            "ratio": item["account_median_multiple"],
        })

    top_ids = {str(item["content_id"]) for item in top_content}
    patterns: list[dict[str, Any]] = []
    for item in synthesis.get("patterns") or []:
        pattern = dict(item)
        pattern_account_refs: list[str] = []
        for raw_ref in item.get("account_refs") or []:
            resolved = lookup_account_ref(
                str(raw_ref),
                creators,
                str(item.get("mechanism") or item.get("pattern") or ""),
            )
            if resolved is None:
                if str(raw_ref) not in omitted_unresolved_refs:
                    omitted_unresolved_refs.append(str(raw_ref))
                continue
            if resolved not in selected_set:
                continue
            if resolved not in pattern_account_refs:
                pattern_account_refs.append(resolved)
        usable_pattern_refs = [
            str(ref) for ref in item.get("content_refs") or [] if str(ref) in top_ids
        ]
        pattern["account_refs"] = pattern_account_refs
        pattern["content_refs"] = usable_pattern_refs
        pattern["proof_points"] = [
            proof_point(
                contents[str(ref)],
                creators[str(contents[str(ref)]["creator_id"])],
                outliers.get(str(ref)),
                platform,
            )
            for ref in usable_pattern_refs
        ]
        patterns.append(pattern)

    if patterns:
        pattern_refs = {
            ref
            for item in patterns
            for ref in item.get("account_refs") or []
        }
        missing_from_patterns = [ref for ref in selected_accounts if ref not in pattern_refs]
        if missing_from_patterns:
            first = patterns[0]
            first["account_refs"] = list(first.get("account_refs") or []) + missing_from_patterns

    kept_patterns: list[dict[str, Any]] = []
    omitted_thin_patterns = 0
    for pattern in patterns:
        account_refs = list(pattern.get("account_refs") or [])
        content_refs = list(pattern.get("content_refs") or [])
        if len(set(account_refs)) < 2 or len(set(content_refs)) < 2:
            omitted_thin_patterns += 1
            continue
        kept_patterns.append(pattern)
    patterns = kept_patterns

    source = snapshot.get("source") or {}
    data_quality = analysis.get("data_quality") or {}
    coverage_complete = bool(data_quality.get("coverage_complete"))
    status = synthesis.get("status")
    if status == "complete" and (
        not coverage_complete
        or omitted_zero_sample_accounts
        or omitted_unresolved_refs
        or omitted_content_refs
        or omitted_weak_featured
        or omitted_thin_patterns
    ):
        status = "partial"
    eligible_count = sum(
        1 for item in contents.values()
        if str(item.get("creator_id")) in selected_set and str(item.get("content_id")) in evidence_ids
    )
    data_gaps = list(synthesis.get("data_gaps") or [])
    requested_not_compared: list[str] = []
    if discovery and selection:
        included_requested = {canonical_handle(str(value)) for value in selection.get("included_handles") or []}
        for identity in discovery.get("requested_refs") or []:
            if canonical_handle(str(identity.get("handle") or "")) not in included_requested:
                note = f"Requested account {identity['ref']} could not be included with verified comparison evidence; no substitute was used."
                data_gaps.append(note)
                requested_not_compared.append(str(identity["ref"]))
                coverage_complete = False
                if status == "complete":
                    status = "partial"

    if omitted_unresolved_refs:
        joined = ", ".join(omitted_unresolved_refs)
        note = (
            f"Synthesis account refs could not be resolved in the final snapshot: {joined}. Those refs were omitted from content findings."
        )
        if note not in data_gaps:
            data_gaps.append(note)
    if omitted_content_refs:
        joined = ", ".join(omitted_content_refs)
        note = (
            f"Synthesis cited content outside analyzed evidence: {joined}. Those refs were omitted from content findings."
        )
        if note not in data_gaps:
            data_gaps.append(note)
    if omitted_weak_featured:
        joined = ", ".join(omitted_weak_featured)
        note = (
            f"Synthesis featured samples below {CREATOR_FACING_OUTLIER_MIN_MULTIPLE:.1f}×: {joined}. Those items were omitted from featured content."
        )
        if note not in data_gaps:
            data_gaps.append(note)
    if omitted_thin_patterns:
        note = (
            f"{omitted_thin_patterns} synthesis pattern(s) no longer had two accounts and two in-report content refs after unresolved citations were dropped, so those patterns were omitted."
        )
        if note not in data_gaps:
            data_gaps.append(note)
    for omitted in omitted_zero_sample_accounts:
        handle = omitted["handle"]
        note = (
            f"{account_label(platform, handle)} passed account eligibility but had no analyzable content in the requested window; "
            "it remains in the eligibility audit and is excluded from content-performance findings."
        )
        if note not in data_gaps:
            data_gaps.append(note)

    report = {
        "schema_version": "competitor-research-report.v3",
        "report_id": synthesis["report_id"],
        "request_id": synthesis["request_id"],
        "status": status,
        "scope": {
            "subject": request["subject"],
            "platforms": request["platforms"],
            "window": request["window"],
            "locale": request["locale"],
            "formats": request["formats"],
            "source_mode": source.get("source_mode", "provided_snapshot"),
        },
        "coverage": {
            "raw_count": int(data_quality.get("analyzed_item_count", len(evidence_ids))),
            "eligible_count": eligible_count,
            "competitor_count": len(competitors),
            "coverage_complete": coverage_complete,
            **({"requested_not_compared": requested_not_compared} if requested_not_compared else {}),
            **({"requested_competitor_count": request["requested_competitor_count"]}
               if isinstance(request.get("requested_competitor_count"), int) else {}),
        },
        "overview": synthesis["overview"],
        "competitors": competitors,
        "top_content": top_content,
        "outliers": final_outliers,
        "patterns": patterns,
        "gaps": [
            {
                **dict(item),
                "content_refs": [
                    str(ref) for ref in item.get("content_refs") or [] if str(ref) in top_ids
                ],
            }
            if isinstance(item, dict) else item
            for item in synthesis.get("gaps") or []
        ],
        "data_gaps": data_gaps,
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--analysis", type=Path, required=True)
    parser.add_argument("--synthesis", type=Path, required=True)
    parser.add_argument("--eligibility", type=Path)
    parser.add_argument("--selection", type=Path)
    parser.add_argument("--discovery", type=Path)
    parser.add_argument("--eligibility-input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = compose(
            load(args.request),
            load(args.snapshot),
            load(args.analysis),
            load(args.synthesis),
            eligibility=load(args.eligibility) if args.eligibility else None,
            selection=load(args.selection) if args.selection else None,
            discovery=load(args.discovery) if args.discovery else None,
            eligibility_input=load(args.eligibility_input) if args.eligibility_input else None,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "failed", "error": f"{type(error).__name__}: {error}"}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    sys.exit(main())
