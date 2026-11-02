#!/usr/bin/env python3
"""Compose TrendResearchReport V3 from one compact semantic synthesis."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


SYNTHESIS_VERSION = "trend-research-synthesis.v3.3"
REQUIRED_SYNTHESIS = {
    "schema_version", "report_id", "request_id", "status", "overview",
    "featured_content_refs", "patterns", "emerging_creators", "data_gaps",
}

# Fixed wording exists in English only. Model-written fields keep the Creator's language; the host
# relays these deterministic proof and gap sentences in that language.
TEXTS = {
    "outlier": "{creator}'s content received {views} {view_unit}, {multiple:.2f}x the same-account, same-format median of {baseline}",
    "metric": {"views": "{value} {view_unit}", "likes": "{value} public likes", "comments": "{value} public comments"},
    "proof_prefix": "{creator}'s content received ",
    "metric_separator": ", ",
    "cohort": "{creator} same-account, same-format public sample",
    "coverage_gap": "Discovery coverage was incomplete; this report represents the collected public sample, not the entire platform.",
    "ranking_gap": "The public sample had no usable {view_unit}, so deterministic ranking used {metric}; no outlier multiple is calculated without a same-account {view_noun} baseline.",
    "momentum_gap": "Only one comparable snapshot is available, so rising, falling, acceleration, and confirmed breakout claims are unsupported.",
    "empty_overview": {
        "title": "Start with a verifiable content test",
        "summary": "No matching content could be verified, so trends, growth, and conversion effects are not established. The following is a proposed test, not an observed finding.",
        "key_findings": [],
        "next_action": "Choose footage relevant to your topic and test two different openings while keeping the rest unchanged. Compare publicly observable performance before deciding the next adjustment.",
    },
}

VIEW_TERMS = {
    "instagram": {"unit": "public plays", "noun": "play-count"},
    "youtube": {"unit": "public views", "noun": "view-count"},
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


def format_number(value: float) -> str:
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}".rstrip("0").rstrip(".")


def creator_reference(creator: dict[str, Any], platform: str) -> str:
    """Return an @handle only when the upstream identity marks it as verified."""
    handle = str(creator.get("handle") or "").strip().lstrip("@")
    verified = creator.get("handle_verified") is True
    # Legacy Instagram snapshots contain provider handles but predate handle_verified.
    if platform == "instagram" and creator.get("handle_verified") is not False:
        verified = bool(handle)
    if handle and verified:
        return f"@{handle}"
    title = str(creator.get("title") or "").strip()
    return title or handle or str(creator.get("creator_id") or "unknown")


def proof_point(
    content: dict[str, Any],
    creator: dict[str, Any],
    outlier: dict[str, Any] | None,
    platform: str,
    language: str | None = None,
) -> str:
    """`language` is accepted from older callers and does not change the output."""
    text = TEXTS
    view_terms = VIEW_TERMS.get(platform, VIEW_TERMS["instagram"])
    creator_ref = creator_reference(creator, platform)
    multiple = outlier.get("account_median_multiple") if outlier else None
    if multiple is not None:
        return text["outlier"].format(
            creator=creator_ref,
            views=format_number(float(outlier["views"])),
            baseline=format_number(float(outlier["baseline_median_views"])),
            multiple=float(multiple),
            view_unit=view_terms["unit"],
        )
    metrics = metric_values(content)
    values = [
        text["metric"][name].format(
            value=format_number(float(metrics[name])),
            view_unit=view_terms["unit"],
        )
        for name in ("views", "likes", "comments") if metrics.get(name) is not None
    ]
    if not values:
        raise ValueError(f"content {content.get('content_id')} has no public metric for proof")
    return text["proof_prefix"].format(creator=creator_ref) + text["metric_separator"].join(values)


def reliable_outlier(value: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return only an outlier that is strong enough for Creator-facing evidence."""
    if not isinstance(value, dict):
        return None
    multiple = value.get("account_median_multiple")
    comparable_n = value.get("comparable_n")
    if (
        isinstance(multiple, bool)
        or not isinstance(multiple, (int, float))
        or multiple < 1.5
        or isinstance(comparable_n, bool)
        or not isinstance(comparable_n, int)
        or comparable_n < 5
        or value.get("evidence_strength") not in {"moderate", "strong"}
    ):
        return None
    return value


def append_once(values: list[str], value: str) -> None:
    if value and value not in values:
        values.append(value)


def evidence_ids(value: Any, found: dict[str, str] | None = None) -> dict[str, str]:
    """Every `evidence_id -> content_id` pair the Analysis prints side by side."""
    found = {} if found is None else found
    if isinstance(value, dict):
        evidence, content = value.get("evidence_id"), value.get("content_id")
        if isinstance(evidence, str) and evidence and isinstance(content, (str, int)) and str(content):
            found.setdefault(evidence, str(content))
        for child in value.values():
            evidence_ids(child, found)
    elif isinstance(value, list):
        for child in value:
            evidence_ids(child, found)
    return found


def content_refs_of(synthesis: dict[str, Any], analysis: dict[str, Any]) -> dict[str, Any]:
    """The synthesis with every evidence_id it cites replaced by that item's content_id.

    The Analysis shows each item under both ids, and the synthesis cites content_id; an evidence_id in
    its place named the same item and still stopped Compose — 5 of 10 runs of 0.3.45–0.3.47, each
    repaired by an extra model turn. The mapping is the Analysis's own, so nothing is guessed.
    """
    mapping = evidence_ids(analysis)
    if not mapping:
        return synthesis
    mapped = json.loads(json.dumps(synthesis))

    def swap(refs: Any) -> Any:
        return [mapping.get(str(ref), ref) for ref in refs] if isinstance(refs, list) else refs

    if "featured_content_refs" in mapped:
        mapped["featured_content_refs"] = swap(mapped["featured_content_refs"])
    for key in ("patterns", "emerging_creators"):
        for item in mapped.get(key) or []:
            if isinstance(item, dict) and "content_refs" in item:
                item["content_refs"] = swap(item["content_refs"])
    return mapped


def compose(
    request: dict[str, Any],
    snapshot: dict[str, Any],
    analysis: dict[str, Any],
    synthesis: dict[str, Any],
    language: str | None = None,
    *,
    reference_candidates: list[dict[str, Any]] | None = None,
    reference_gates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """`language` is accepted from older callers and does not change the output."""
    text = TEXTS
    missing = sorted(REQUIRED_SYNTHESIS - synthesis.keys())
    if missing:
        raise ValueError(f"synthesis missing fields: {', '.join(missing)}")
    if synthesis.get("schema_version") != SYNTHESIS_VERSION:
        raise ValueError(f"synthesis schema_version must be {SYNTHESIS_VERSION}")
    if synthesis.get("request_id") != request.get("request_id"):
        raise ValueError("synthesis request_id does not match request")
    synthesis = content_refs_of(synthesis, analysis)

    creators = {str(item["creator_id"]): item for item in snapshot.get("creators") or []}
    contents = {str(item["content_id"]): item for item in snapshot.get("items") or []}
    relevance = analysis.get("relevance") or {}
    candidate_ids = (
        set(relevance.get("direct_content_ids") or [])
        if relevance.get("method") == "semantic-eligibility.v1"
        else set(relevance.get("candidate_content_ids") or [])
    )
    if not candidate_ids and relevance.get("method") != "semantic-eligibility.v1":
        candidate_ids = set(relevance.get("direct_content_ids") or []) | set(relevance.get("adjacent_content_ids") or [])
    ranked = {str(item["content_id"]): item for item in analysis.get("ranked_content") or []}
    outliers = {str(item["content_id"]): item for item in analysis.get("account_outliers") or []}

    refs: list[str] = []
    for ref in synthesis.get("featured_content_refs") or []:
        append_once(refs, str(ref))
    for item in (synthesis.get("patterns") or []) + (synthesis.get("emerging_creators") or []):
        for ref in item.get("content_refs") or []:
            append_once(refs, str(ref))
    # 有可引用证据时，空 synthesis 也不能把已入选的视频全部丢掉。
    if not refs:
        allowed = set(analysis["selected_content_ids"]) if "selected_content_ids" in analysis else candidate_ids
        refs = [ref for ref in ranked if ref in allowed and ref in candidate_ids]
    # Composer 和独立 Validator 共用入选校验，避免生成报告时绕过分配结果。
    sys.dont_write_bytecode = True
    path = Path(__file__).with_name("validate_output.py")
    spec = importlib.util.spec_from_file_location(f"{path.parent.parent.name}:report_selection", path)
    validator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validator)
    selection_problems = validator.selection_errors(analysis, set(refs))
    if selection_problems:
        raise ValueError("; ".join(selection_problems))
    for ref in refs:
        if ref not in candidate_ids or ref not in contents or ref not in ranked:
            raise ValueError(f"content ref is not resolved candidate evidence: {ref}")
        if str(contents[ref].get("creator_id")) not in creators:
            raise ValueError(f"content ref has unresolved creator: {ref}")

    evidence_rank = {
        str(item["content_id"]): int(item["evidence_rank"])
        for item in snapshot.get("items") or []
        if isinstance(item.get("evidence_rank"), int) and item["evidence_rank"] > 0
    }

    def evidence_order(value: str) -> tuple[int, str]:
        return (evidence_rank.get(value, int(ranked[value]["rank"])), value)

    def mechanism_proof_order(value: str) -> tuple[bool, float, bool, float]:
        # Display only the model-selected refs, strongest public metric first. Python's
        # stable sort retains authored order on exact ties; this never changes ranking.
        views = metric_values(contents[value]).get("views")
        multiple = (outliers.get(value) or {}).get("account_median_multiple")
        return (views is None, -(views or 0), multiple is None, -(multiple or 0))

    top_content: list[dict[str, Any]] = []
    for position, ref in enumerate(sorted(refs, key=evidence_order), start=1):
        item = contents[ref]
        outlier = reliable_outlier(outliers.get(ref))
        baseline = None
        multiple = None
        if outlier and outlier.get("account_median_multiple") is not None:
            creator = creators[str(item["creator_id"])]
            creator_ref = creator_reference(creator, str(snapshot.get("platform") or ""))
            baseline = {
                "metric": "views",
                "value": outlier["baseline_median_views"],
                "sample_size": outlier["comparable_n"],
                "cohort": text["cohort"].format(creator=creator_ref),
            }
            multiple = outlier["account_median_multiple"]
        creator = creators[str(item["creator_id"])]
        creator_ref = creator_reference(creator, str(snapshot.get("platform") or ""))
        top_content.append({
            "rank": position,
            "content_id": ref,
            "creator_ref": creator_ref,
            "title": str(item.get("title") or ""),
            "url": item["url"],
            "platform": snapshot.get("platform"),
            "format": item["format"],
            "published_at": item["published_at"],
            "metrics": metric_values(item),
            "baseline": baseline,
            "outlier_multiple": multiple,
        })

    patterns: list[dict[str, Any]] = []
    for item in synthesis.get("patterns") or []:
        pattern = dict(item)
        ordered_refs = sorted(
            (str(ref) for ref in item.get("content_refs") or []),
            key=mechanism_proof_order,
        )
        pattern["content_refs"] = ordered_refs
        pattern["proof_points"] = [
            proof_point(
                contents[ref],
                creators[str(contents[ref]["creator_id"])],
                reliable_outlier(outliers.get(ref)),
                str(snapshot.get("platform") or ""),
            )
            for ref in ordered_refs
        ]
        patterns.append(pattern)

    emerging_creators = []
    for item in synthesis.get("emerging_creators") or []:
        creator = dict(item)
        creator["content_refs"] = sorted(
            (str(ref) for ref in item.get("content_refs") or []),
            key=evidence_order,
        )
        emerging_creators.append(creator)

    data_quality = analysis.get("data_quality") or {}
    claim_boundary = analysis.get("claim_boundary") or {}
    coverage_complete = bool(data_quality.get("coverage_complete"))
    status = synthesis.get("status")
    if status == "complete" and (not coverage_complete or not refs):
        status = "partial"
    # Model-written gaps are kept verbatim in whatever language they were written; the script does not
    # classify their wording. The deterministic gaps are appended once each. `data_gaps` is never
    # rendered, so an overlap between the two costs nothing on the Creator-facing side.
    data_gaps: list[str] = []
    for value in synthesis.get("data_gaps") or []:
        append_once(data_gaps, str(value))
    if not coverage_complete:
        append_once(data_gaps, text["coverage_gap"])
    ranking_metric = data_quality.get("ranking_metric")
    if ranking_metric and ranking_metric != "views":
        platform = str(snapshot.get("platform") or "instagram")
        view_terms = VIEW_TERMS.get(platform, VIEW_TERMS["instagram"])
        append_once(
            data_gaps,
            text["ranking_gap"].format(
                metric=ranking_metric,
                view_unit=view_terms["unit"],
                view_noun=view_terms["noun"],
            ),
        )
    snapshot_count = int(claim_boundary.get("snapshot_count") or 1)
    if snapshot_count < 2:
        append_once(data_gaps, text["momentum_gap"])

    overview = synthesis["overview"]
    if not (candidate_ids & contents.keys() & ranked.keys()):
        # IG owns this no-evidence branch. Preserve the raw synthesis for audit, but do not
        # turn an empty verified evidence set into positive claims through free-form prose.
        overview = {**text["empty_overview"], "key_findings": []}
        patterns = []
        emerging_creators = []
        if status != "blocked":
            status = "partial"

    source = snapshot.get("source") or {}
    report = {
        "schema_version": "trend-research-report.v3",
        "report_id": synthesis["report_id"],
        "request_id": synthesis["request_id"],
        "status": status,
        "scope": {
            "niche": request["niche"],
            "platforms": request["platforms"],
            "window": request["window"],
            "locale": request["locale"],
            "formats": request["formats"],
            "source_mode": source.get("source_mode", "provided_snapshot"),
            "snapshot_count": snapshot_count,
        },
        "coverage": {
            "raw_count": int(data_quality.get("analyzed_item_count", len(analysis.get("evidence") or []))),
            "eligible_count": len(candidate_ids),
            "coverage_complete": coverage_complete,
        },
        "overview": overview,
        "top_content": top_content,
        "patterns": patterns,
        "emerging_creators": emerging_creators,
        "data_gaps": data_gaps,
    }
    if not refs and reference_candidates is not None:
        videos = validator.reference_fallback().select(request, reference_candidates, reference_gates or [])
        if videos:
            report["reference_videos"] = videos
            report["status"] = "partial"
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--analysis", type=Path, required=True)
    parser.add_argument("--synthesis", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--language", help="accepted from older callers; fixed wording is English")
    parser.add_argument("--reference-candidates", type=Path, action="append")
    parser.add_argument("--reference-gate", type=Path, action="append", default=[])
    args = parser.parse_args()
    try:
        result = compose(load(args.request), load(args.snapshot), load(args.analysis), load(args.synthesis),
                         reference_candidates=[load(path) for path in args.reference_candidates] if args.reference_candidates is not None else None,
                         reference_gates=[load(path) for path in args.reference_gate])
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "failed", "error": f"{type(error).__name__}: {error}"}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    sys.exit(main())
