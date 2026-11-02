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
from evidence_contract import (
    CREATOR_FACING_OUTLIER_MIN_MULTIPLE,
    RELIABLE_OUTLIER_MIN_SAMPLE,
)
from sampling_policy import policy

SYNTHESIS_VERSION = "competitor-research-synthesis.v3.1"
REQUIRED_SYNTHESIS = {
    "schema_version", "report_id", "request_id", "status", "overview",
    "competitors", "featured_content_refs", "patterns", "gaps", "data_gaps",
}
UNRESOLVED_REASONS = {
    "publication_order_unverifiable": "the retrieved publication times could not establish its latest-post order",
    "video_classification_unverifiable": "the retrieved native formats could not establish its latest-video sample",
    "fewer_posts_than_requested": "the retrieved history did not supply the requested number of posts as of the frozen cutoff",
    "selected_posts_missing_title": "its selected latest posts had no usable title/caption evidence",
    "gate_excluded": "the discovery Gate classified it as excluded",
    "category_fit_below_60": "its category fit was below the direct-competitor floor",
    "category_and_audience_job_fit_below_55": "its category and audience-job fit were below the adjacent floor",
    "not_selected": "it ranked below the accounts selected for comparison",
    "history_not_retrieved": "its account history could not be retrieved",
    "no_posts_in_window": "it had no captioned posts in the requested window",
    "no_posts_in_retrieved_history": "the retrieved part of its history had no captioned posts in the requested window, and the rest was not retrieved",
}
def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def metric_values(item: dict[str, Any]) -> dict[str, float | None]:
    result: dict[str, float | None] = {}
    for name, metric in (item.get("metrics") or {}).items():
        if isinstance(metric, dict) and metric.get("status") in {"available", "zero"}:
            result[name] = metric.get("value")
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
    if platform == "tiktok":
        return f"https://www.tiktok.com/@{clean}"
    return f"https://www.instagram.com/{clean}/"


def account_label(platform: str, handle: str) -> str:
    """Render an account name without inventing a YouTube public @handle."""
    clean = handle.lstrip("@")
    return clean if platform == "youtube" else f"@{clean}"


def normalize_handle(value: str) -> str:
    return value.strip().lower().lstrip("@").rstrip("/")


HANDLE_TOKEN = re.compile(r"[A-Za-z0-9._]{3,}")


def hinted_creator_ids(creators: dict[str, dict[str, Any]], hint: str) -> list[str]:
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


def compose(
    request: dict[str, Any],
    snapshot: dict[str, Any],
    analysis: dict[str, Any],
    synthesis: dict[str, Any],
) -> dict[str, Any]:
    missing = sorted(REQUIRED_SYNTHESIS - synthesis.keys())
    if missing:
        raise ValueError(f"synthesis missing fields: {', '.join(missing)}")
    if synthesis.get("schema_version") != SYNTHESIS_VERSION:
        raise ValueError(f"synthesis schema_version must be {SYNTHESIS_VERSION}")
    if synthesis.get("request_id") != request.get("request_id"):
        raise ValueError("synthesis request_id does not match request")

    creators = {str(item["creator_id"]): item for item in snapshot.get("creators") or []}
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
    competitors: list[dict[str, Any]] = []
    for item in synthesis.get("competitors") or []:
        supplied_ref = str(item.get("account_ref") or "")
        ref = lookup_account_ref(supplied_ref, creators, str(item.get("summary") or ""))
        if ref is None:
            if supplied_ref and supplied_ref not in omitted_unresolved_refs:
                omitted_unresolved_refs.append(supplied_ref)
            continue
        creator = creators[ref]
        if ref in selected_accounts or any(row["account_ref"] == ref for row in omitted_zero_sample_accounts):
            # A repeated account (the same ref twice, or a handle that resolves to an id already
            # listed) adds no evidence; keep its first entry instead of discarding the whole report.
            continue
        sample_size = sum(
            1 for content in contents.values()
            if str(content.get("creator_id")) == ref and str(content.get("content_id")) in evidence_ids
        )
        if sample_size < 1:
            omitted_zero_sample_accounts.append({
                "account_ref": ref,
                "handle": str(creator.get("handle") or ref).lstrip("@"),
            })
            continue
        selected_accounts.append(ref)
        handle = str(creator.get("handle") or ref).lstrip("@")
        platform = str(snapshot.get("platform") or request["platforms"][0])
        competitors.append({
            "account_ref": ref,
            "handle": handle,
            "url": creator.get("source_url") or creator_url(platform, handle, ref),
            "platform": platform,
            "relationship": item.get("relationship"),
            "sample_size": sample_size,
            "summary": item.get("summary"),
        })

    selected_set = set(selected_accounts)

    def usable_content_ref(ref: str) -> bool:
        content = contents.get(str(ref))
        if not content or str(ref) not in evidence_ids:
            return False
        return str(content.get("creator_id")) in selected_set

    refs: list[str] = []
    for ref in synthesis.get("featured_content_refs") or []:
        if not usable_content_ref(str(ref)):
            if str(ref) not in omitted_content_refs:
                omitted_content_refs.append(str(ref))
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
                    f"{account_label(str(snapshot.get('platform') or request['platforms'][0]), str(item.get('handle') or item['creator_id']))} same-account same-format public sample"
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
                str(snapshot.get("platform") or request["platforms"][0]),
            )
            for ref in usable_pattern_refs
        ]
        patterns.append(pattern)

    kept_patterns: list[dict[str, Any]] = []
    omitted_thin_patterns = 0
    for pattern in patterns:
        if len(set(pattern.get("account_refs") or [])) < 2 or len(set(pattern.get("content_refs") or [])) < 2:
            omitted_thin_patterns += 1
            continue
        kept_patterns.append(pattern)
    patterns = kept_patterns

    source = snapshot.get("source") or {}
    unresolved_accounts = [row for row in source.get("unresolved_accounts") or [] if isinstance(row, dict)]
    unresolved_refs = {str(row.get("account_ref")) for row in unresolved_accounts}
    data_quality = analysis.get("data_quality") or {}
    coverage_complete = bool(data_quality.get("coverage_complete"))
    status = synthesis.get("status")
    if status == "complete" and (
        not coverage_complete
        or unresolved_accounts
        or omitted_zero_sample_accounts
        or omitted_unresolved_refs
        or omitted_content_refs
        or omitted_thin_patterns
    ):
        status = "partial"
    eligible_count = sum(
        1 for item in contents.values()
        if str(item.get("creator_id")) in selected_set and str(item.get("content_id")) in evidence_ids
    )
    data_gaps = list(synthesis["data_gaps"])
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
    if omitted_thin_patterns:
        note = (
            f"{omitted_thin_patterns} synthesis pattern(s) no longer had two accounts and two in-report content refs after unresolved citations were dropped, so those patterns were omitted."
        )
        if note not in data_gaps:
            data_gaps.append(note)
    for row in unresolved_accounts:
        reason = str(row.get("reason") or "")
        note = (
            f"{account_label(str(snapshot.get('platform') or request['platforms'][0]), str(row.get('handle') or row.get('account_ref')))} "
            f"was not compared: {UNRESOLVED_REASONS.get(reason, reason)}."
        )
        if note not in data_gaps:
            data_gaps.append(note)
    for omitted in omitted_zero_sample_accounts:
        if omitted["account_ref"] in unresolved_refs:
            continue
        handle = omitted["handle"]
        note = (
            f"{account_label(str(snapshot.get('platform') or request['platforms'][0]), handle)} passed account eligibility but had no analyzable content in the requested window; "
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
            "window": source["sampling"]["window"] if policy(request) else request["window"],
            "locale": request["locale"],
            "formats": request["formats"],
            "source_mode": source.get("source_mode", "provided_snapshot"),
        },
        "coverage": {
            "raw_count": int(data_quality.get("analyzed_item_count", len(evidence_ids))),
            "eligible_count": eligible_count,
            "competitor_count": len(competitors),
            "coverage_complete": coverage_complete,
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
    if policy(request):
        audit = source.get("sampling") or {}
        if audit.get("policy") != request["sampling"]:
            raise ValueError("snapshot sampling policy does not match the frozen request")
        report["scope"]["sampling"] = dict(request["sampling"])
        report["coverage"]["account_samples"] = audit["accounts"]
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--analysis", type=Path, required=True)
    parser.add_argument("--synthesis", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = compose(load(args.request), load(args.snapshot), load(args.analysis), load(args.synthesis))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "failed", "error": f"{type(error).__name__}: {error}"}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    sys.exit(main())
