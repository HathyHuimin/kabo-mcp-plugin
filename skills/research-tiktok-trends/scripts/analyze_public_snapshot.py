#!/usr/bin/env python3
"""Build deterministic public facts for Account, Trend, and Competitor tasks."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import statistics
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
# Sibling imports must not leave a __pycache__ in the signed, checksummed skill directory.
sys.dont_write_bytecode = True
from trend_gate_contract import classifications


RULE_VERSION = "public-content-facts.v1.3"
AVAILABLE = {"available", "zero"}
NON_COMPARABLE_FORMATS = {"unknown", "unknown_video", "format_unknown", "unclassified"}
TOKEN_RE = re.compile(r"[a-z0-9]+")
RELEVANCE_STOPWORDS = {
    "and", "the", "for", "with", "from", "this", "that", "content", "recent", "days",
    "instagram", "tiktok", "youtube", "video", "videos", "reel", "reels", "market", "mercado",
    "para", "com", "dos", "das", "uma", "como", "sobre", "conteudo", "recente",
}


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp must include timezone: {value}")
    return parsed.astimezone(timezone.utc)


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def tokens(value: str) -> set[str]:
    folded = unicodedata.normalize("NFKD", value.casefold())
    ascii_text = "".join(char for char in folded if not unicodedata.combining(char))
    return {
        token for token in TOKEN_RE.findall(ascii_text)
        if (len(token) >= 3 or token in {"ai", "ia"}) and token not in RELEVANCE_STOPWORDS
    }


def relevance_of(items: list[dict[str, Any]], request: dict[str, Any] | None) -> dict[str, Any] | None:
    """Build a deterministic candidate-recall layer for semantic relevance review.

    Literal request overlap is useful for recall and audit, but is not a reliable semantic judge.
    The single synthesis pass may select either direct or adjacent candidates and must reject
    incidental matches. Rows with no meaningful request overlap remain excluded.
    """
    if request is None:
        return None
    seed_texts = [str(value) for value in (request.get("seed_queries") or [])]
    source_texts = [str(request.get("niche") or ""), *seed_texts]
    request_terms = sorted(set().union(*(tokens(value) for value in source_texts)))
    # No usable terms means this layer has no opinion, not that every row is irrelevant.
    # `tokens()` keeps only `[a-z0-9]+` after NFKD, so a niche written entirely in a script without
    # Latin letters (Chinese, Japanese, Korean, Thai, Cyrillic, Arabic...) yields nothing. Excluding
    # the whole pool would delete the candidates this recall aid exists to rank, and returning no
    # layer at all leaves Compose and Validate without a candidate set. Every item stays a candidate
    # and the single semantic synthesis pass chooses among them.
    if not request_terms:
        content_ids = [row["content_id"] for row in items]
        return {
            "method": "request-token-overlap.v1.2-candidate-recall-no-terms",
            "request_terms": [],
            "core_terms": [],
            "minimum_core_term_matches": 0,
            "direct_content_ids": [],
            "adjacent_content_ids": [],
            "candidate_content_ids": content_ids,
            "excluded_content_ids": [],
            "matches": [],
        }
    seed_counts = Counter(term for value in seed_texts for term in tokens(value))
    core_terms = sorted(term for term, count in seed_counts.items() if count >= 2)
    if not core_terms:
        core_terms = request_terms[:]
    threshold = 2 if len(core_terms) >= 2 else 1
    direct, adjacent, excluded, matches = [], [], [], []
    for row in items:
        matched = sorted(tokens(str(row.get("title") or "")) & set(request_terms))
        matched_core = sorted(set(matched) & set(core_terms))
        if len(matched_core) >= threshold:
            role = "direct"
            direct.append(row["content_id"])
        elif matched:
            role = "adjacent"
            adjacent.append(row["content_id"])
        else:
            role = "excluded"
            excluded.append(row["content_id"])
        matches.append({
            "content_id": row["content_id"], "role": role,
            "matched_terms": matched, "matched_core_terms": matched_core,
        })
    return {
        "method": "request-token-overlap.v1.2-candidate-recall",
        "request_terms": request_terms,
        "core_terms": core_terms,
        "minimum_core_term_matches": threshold,
        "direct_content_ids": direct,
        "adjacent_content_ids": adjacent,
        "candidate_content_ids": [row["content_id"] for row in items if row["content_id"] in set(direct + adjacent)],
        "excluded_content_ids": excluded,
        "matches": matches,
    }


def semantic_relevance_of(
    items: list[dict[str, Any]],
    gate: dict[str, Any],
) -> dict[str, Any]:
    item_ids = {str(item["content_id"]) for item in items}
    roles = classifications(gate)

    def role_for(content_id: str) -> str | None:
        matches = [role for key, role in roles.items() if key == content_id or key.endswith(f":{content_id}")]
        if len(matches) > 1:
            raise ValueError(f"ambiguous semantic relevance rows for content_id: {content_id}")
        return matches[0] if matches else None

    by_id = {item_id: role_for(item_id) for item_id in item_ids}
    direct = sorted(item_id for item_id in item_ids if by_id.get(item_id) == "direct")
    adjacent = sorted(item_id for item_id in item_ids if by_id.get(item_id) == "adjacent")
    excluded = sorted(item_ids - set(direct) - set(adjacent))
    return {
        "method": "semantic-eligibility.v1",
        "request_terms": [],
        "core_terms": [],
        "minimum_core_term_matches": 0,
        "direct_content_ids": direct,
        "adjacent_content_ids": adjacent,
        "candidate_content_ids": direct + adjacent,
        "excluded_content_ids": excluded,
        "matches": [
            {
                "content_id": item_id,
                "role": by_id[item_id] if by_id.get(item_id) in {"direct", "adjacent"} else "excluded",
            }
            for item_id in sorted(item_ids)
        ],
    }


ALLOCATION_SCHEMA = "signature-content-allocation.v1"
ALLOCATION_RULE = "signature-allocation.v1"


def signature_allocation_of(
    allocation: dict[str, Any],
    request: dict[str, Any] | None,
) -> tuple[list[tuple[str, str | None]], list[dict[str, Any]]]:
    """名额分配文件里的 (content_key, 主打内容 id) 与每个主打内容的 {id, name, quota, filled}。

    名字取 Request 的 signature_content：分配文件只记 id，名字以 Request 为准，免得两处各写一份。
    """
    if allocation.get("schema") != ALLOCATION_SCHEMA or allocation.get("rule_version") != ALLOCATION_RULE:
        raise ValueError(f"allocation must be {ALLOCATION_SCHEMA} with rule_version {ALLOCATION_RULE}")
    if request is None:
        raise ValueError("--allocation requires --request: the signature content names come from the Request")
    entries = [
        item for item in ((request.get("creator_profile") or {}).get("signature_content") or [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    ]
    names = {item["id"]: str(item.get("name") or "") for item in entries}
    per_content = allocation.get("per_content")
    if not isinstance(per_content, dict) or set(per_content) != set(names):
        raise ValueError("allocation per_content must list exactly the Request's signature content ids")
    summary = []
    for item in entries:
        counts = per_content[item["id"]]
        quota, filled = (counts or {}).get("quota"), (counts or {}).get("filled")
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in (quota, filled)):
            raise ValueError(f"allocation per_content.{item['id']} needs non-negative integer quota and filled")
        summary.append({"id": item["id"], "name": names[item["id"]], "quota": quota, "filled": filled})
    slots = []
    for slot in allocation.get("slots") or []:
        key = slot.get("content_key") if isinstance(slot, dict) else None
        value = slot.get("signature_content_id") if isinstance(slot, dict) else None
        if not isinstance(key, str) or not key or (value is not None and value not in names):
            raise ValueError(f"invalid allocation slot: {slot!r}")
        slots.append((key, value))
    return slots, summary


def metric(item: dict[str, Any], name: str) -> float | None:
    entry = item.get("metrics", {}).get(name)
    if not isinstance(entry, dict) or entry.get("status") not in AVAILABLE:
        return None
    value = entry.get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{item.get('content_id')}.metrics.{name}.value must be a non-negative finite number")
    return float(value)


def sample_confidence(size: int) -> str:
    if size < 3:
        return "insufficient"
    if size < 5:
        return "low"
    if size < 10:
        return "medium"
    return "high"


def comparable_format(value: Any) -> bool:
    return str(value or "").strip().casefold() not in NON_COMPARABLE_FORMATS


def median(values: list[float]) -> float | None:
    return round(statistics.median(values), 6) if values else None


def validate_snapshot(snapshot: dict[str, Any]) -> tuple[datetime, datetime, datetime, set[str]]:
    required = {"schema_version", "snapshot_id", "observed_at", "platform", "scope", "source", "creators", "items"}
    missing = sorted(required - snapshot.keys())
    if missing:
        raise ValueError(f"snapshot missing fields: {missing}")
    if snapshot["schema_version"] != "public-content-snapshot.v1":
        raise ValueError("schema_version must be public-content-snapshot.v1")
    observed = parse_time(snapshot["observed_at"])
    window = snapshot.get("scope", {}).get("window", {})
    start, end = parse_time(window["start"]), parse_time(window["end"])
    if start > end or end > observed:
        raise ValueError("scope window must be ordered and must not end after observed_at")
    creator_ids = [row.get("creator_id") for row in snapshot["creators"]]
    if None in creator_ids or len(creator_ids) != len(set(creator_ids)):
        raise ValueError("creators must have unique non-empty creator_id values")
    return observed, start, end, set(creator_ids)


def normalized_items(snapshot: dict[str, Any], start: datetime, end: datetime, creator_ids: set[str]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    unique: dict[str, dict[str, Any]] = {}
    dropped = Counter()
    for source in snapshot["items"]:
        if not isinstance(source, dict):
            raise ValueError("every item must be an object")
        required = ("content_id", "creator_id", "url", "title", "published_at", "format", "metrics")
        missing = [key for key in required if source.get(key) in (None, "")]
        if missing:
            raise ValueError(f"item missing required fields {missing}: {source!r}")
        if source["creator_id"] not in creator_ids:
            raise ValueError(f"item references unknown creator_id: {source['creator_id']}")
        published = parse_time(source["published_at"])
        if not start <= published <= end:
            dropped["outside_window"] += 1
            continue
        row = dict(source)
        row["query_refs"] = sorted(set(source.get("query_refs") or []))
        key = str(row["content_id"])
        if key in unique:
            old = dict(unique[key])
            old_refs = old.pop("query_refs", [])
            new = dict(row)
            new_refs = new.pop("query_refs", [])
            if canonical_bytes(old) != canonical_bytes(new):
                raise ValueError(f"conflicting duplicate content_id: {key}")
            unique[key]["query_refs"] = sorted(set(old_refs + new_refs))
            dropped["duplicate_rows_merged"] += 1
        else:
            unique[key] = row
    return sorted(unique.values(), key=lambda row: (row["published_at"], row["content_id"])), dict(sorted(dropped.items()))


def analyze(
    snapshot: dict[str, Any],
    mode: str,
    focus_creator_id: str | None,
    request: dict[str, Any] | None = None,
    relevance_gate: dict[str, Any] | None = None,
    trend_enrichment: dict[str, Any] | None = None,
    allocation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    observed, start, end, creator_ids = validate_snapshot(snapshot)
    if focus_creator_id and focus_creator_id not in creator_ids:
        raise ValueError(f"unknown focus creator: {focus_creator_id}")
    if mode == "account" and not focus_creator_id:
        raise ValueError("account mode requires --focus-creator-id")
    items, dropped = normalized_items(snapshot, start, end, creator_ids)
    creator_meta = {row["creator_id"]: row for row in snapshot["creators"]}

    # Product collection/enrichment already computes account-relative baselines from
    # the complete provider feed. Recomputing them from the selected workflow Snapshot
    # would use a smaller, biased cohort. Keep local cohort math only for the legacy
    # Snapshot path where no authoritative enrichment exists.
    cohorts, baseline_by_group = [], {}
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    if trend_enrichment is None:
        for row in items:
            if comparable_format(row["format"]):
                groups[(row["creator_id"], row["format"])].append(row)
        for (creator_id, content_format), rows in sorted(groups.items()):
            values = {name: [value for row in rows if (value := metric(row, name)) is not None] for name in ("views", "likes", "comments")}
            medians = {name: median(data) for name, data in values.items()}
            baseline_by_group[(creator_id, content_format)] = medians
            cohorts.append({
                "creator_id": creator_id,
                "handle": creator_meta[creator_id]["handle"],
                "format": content_format,
                "sample_size": len(rows),
                "sample_confidence": sample_confidence(len(rows)),
                "median_metrics": medians,
            })

    metric_counts = {
        name: sum(metric(row, name) is not None for row in items)
        for name in ("views", "likes", "comments")
    }
    ranking_metric = next((name for name in ("views", "likes", "comments") if metric_counts[name]), None)
    evidence, ranked, outliers = [], [], []
    for row in items:
        evidence_id = "ev-content-" + hashlib.sha256(str(row["url"]).encode()).hexdigest()[:16]
        values = {name: metric(row, name) for name in ("views", "likes", "comments")}
        evidence.append({
            "evidence_id": evidence_id,
            "content_id": row["content_id"],
            "creator_id": row["creator_id"],
            "source_url": row["url"],
            "published_at": row["published_at"],
            "format": row["format"],
            "metrics": values,
            "query_refs": row.get("query_refs", []),
        })
        if ranking_metric is not None and values[ranking_metric] is not None:
            ranked.append({
                "evidence_id": evidence_id,
                "content_id": row["content_id"],
                "creator_id": row["creator_id"],
                "handle": creator_meta[row["creator_id"]]["handle"],
                "title": row["title"],
                "url": row["url"],
                "published_at": row["published_at"],
                "format": row["format"],
                "views": values["views"],
                "likes": values["likes"],
                "comments": values["comments"],
                "ranking_metric": ranking_metric,
                "ranking_value": values[ranking_metric],
            })
        if trend_enrichment is None and comparable_format(row["format"]):
            group_rows = groups[(row["creator_id"], row["format"])]
            baseline = baseline_by_group[(row["creator_id"], row["format"])]["views"]
            multiple = round(values["views"] / baseline, 6) if values["views"] is not None and baseline not in (None, 0) and len(group_rows) >= 3 else None
            if multiple is not None:
                outliers.append({
                    "evidence_id": evidence_id,
                    "content_id": row["content_id"],
                    "creator_id": row["creator_id"],
                    "handle": creator_meta[row["creator_id"]]["handle"],
                    "format": row["format"],
                    "views": values["views"],
                    "baseline_median_views": baseline,
                    "account_median_multiple": multiple,
                    "comparable_n": len(group_rows),
                    "sample_confidence": sample_confidence(len(group_rows)),
                    "evidence_strength": (
                        "directional"
                        if len(group_rows) < 5
                        else "moderate" if len(group_rows) < 10 else "strong"
                    ),
                })

    ranked.sort(key=lambda row: (-row["ranking_value"], row["published_at"], row["content_id"]))
    for index, row in enumerate(ranked, 1):
        row["rank"] = index
    signature_summary = None
    if allocation is not None:
        # 排名保留完整证据池；入选集合单独记，不能拿主打分类的 null 推断是否入选。
        slots, signature_summary = signature_allocation_of(allocation, request)
        selected_content_ids = []
        for row in ranked:
            content_id = str(row["content_id"])
            hits = [value for key, value in slots if key == content_id or key.endswith(f":{content_id}")]
            if len(hits) > 1:
                raise ValueError(f"ambiguous allocation slots for content_id: {content_id}")
            row["signature_content_id"] = hits[0] if hits else None
            if hits:
                selected_content_ids.append(content_id)
        if len(selected_content_ids) != len(slots):
            raise ValueError("allocation contains content outside the analyzed snapshot")
    outliers.sort(key=lambda row: (row["account_median_multiple"] is None, -(row["account_median_multiple"] or -1), row["content_id"]))

    # The product path consumes the server-owned baseline directly. Legacy Snapshot
    # flows retain the local calculation above for backward compatibility.
    if trend_enrichment is not None:
        enrichment_version = trend_enrichment.get("schema_version")
        if enrichment_version not in {
            "trend-enrichment.v1",
            "tiktok-trend-enrichment.v1",
            "youtube-trend-enrichment.v1",
        }:
            raise ValueError("trend enrichment must be Instagram, TikTok or YouTube enrichment v1")
        item_by_id = {str(row["content_id"]): row for row in items}
        evidence_by_id = {str(row["content_id"]): row for row in evidence}
        authoritative_outliers = []
        for source in trend_enrichment.get("items") or []:
            content_id = str(source.get("content_id") or source.get("video_id") or "")
            if not content_id:
                key = str(source.get("content_key") or "")
                matches = [item_id for item_id in item_by_id if key == item_id or key.endswith(f":{item_id}")]
                content_id = matches[0] if len(matches) == 1 else ""
            row = item_by_id.get(content_id)
            proof = evidence_by_id.get(content_id)
            value = (
                source.get("account_breakout")
                if enrichment_version == "youtube-trend-enrichment.v1"
                else source.get("outlier")
            )
            if (
                row is None
                or proof is None
                or not comparable_format(row["format"])
                or not isinstance(value, dict)
            ):
                continue
            target = (
                value.get("target_view_count")
                if enrichment_version == "youtube-trend-enrichment.v1"
                else value.get("target_play_count")
            )
            baseline = value.get("cohort_median")
            multiple = value.get("multiple")
            cohort_size = (
                value.get("comparable_count")
                if enrichment_version == "youtube-trend-enrichment.v1"
                else value.get("cohort_size")
            )
            numbers = (target, baseline, multiple, cohort_size)
            if not all(isinstance(number, (int, float)) and not isinstance(number, bool) for number in numbers):
                continue
            if cohort_size < 3 or baseline <= 0 or target < 0 or multiple < 0:
                continue
            authoritative_outliers.append({
                "evidence_id": proof["evidence_id"],
                "content_id": content_id,
                "creator_id": row["creator_id"],
                "handle": creator_meta[row["creator_id"]]["handle"],
                "format": row["format"],
                "views": float(target),
                "baseline_median_views": float(baseline),
                "account_median_multiple": float(multiple),
                "comparable_n": int(cohort_size),
                "sample_confidence": sample_confidence(int(cohort_size)),
                "outlier_bucket": value.get("bucket"),
                "evidence_strength": value.get("evidence_strength"),
                "signal_status": "outlier" if multiple >= 1.5 else "relevant_but_not_breakout",
                "formula_version": value.get("formula_version"),
                "format_confidence": source.get("format_confidence"),
                "maturity": source.get("maturity"),
            })
        outliers = sorted(authoritative_outliers, key=lambda row: (-row["account_median_multiple"], row["content_id"]))

    focus_summary = None
    if focus_creator_id:
        focus_items = [row for row in items if row["creator_id"] == focus_creator_id]
        timestamps = [parse_time(row["published_at"]) for row in focus_items]
        span_days = max((max(timestamps) - min(timestamps)).total_seconds() / 86400, 1) if len(timestamps) >= 2 else None
        focus_summary = {
            "creator_id": focus_creator_id,
            "handle": creator_meta[focus_creator_id]["handle"],
            "content_count": len(focus_items),
            "format_mix": dict(sorted(Counter(row["format"] for row in focus_items).items())),
            "observed_span_days": round(span_days, 3) if span_days is not None else None,
            "posts_per_week": round(len(focus_items) / span_days * 7, 6) if span_days else None,
            "top_content_ids": [row["content_id"] for row in ranked if row["creator_id"] == focus_creator_id][:10],
        }

    input_sha = digest(snapshot)
    request_sha = digest(request) if request is not None else None
    analysis_id = "public-analysis-" + hashlib.sha256(
        f"{mode}:{focus_creator_id}:{input_sha}:{request_sha}".encode()
    ).hexdigest()[:16]
    relevance = (
        semantic_relevance_of(items, relevance_gate)
        if mode == "trend" and relevance_gate is not None
        else relevance_of(items, request) if mode == "trend" else None
    )
    result = {
        "schema_version": "public-content-analysis.v1",
        "analysis_id": analysis_id,
        "mode": mode,
        "snapshot_ref": snapshot["snapshot_id"],
        "rule_version": RULE_VERSION,
        "data_quality": {
            "input_item_count": len(snapshot["items"]),
            "analyzed_item_count": len(items),
            "creator_count": len(creator_ids),
            "items_with_views": sum(metric(row, "views") is not None for row in items),
            "metric_coverage": metric_counts,
            "ranking_metric": ranking_metric,
            "dropped": dropped,
            "coverage_complete": bool(snapshot["source"].get("coverage_complete")),
            "source_mode": snapshot["source"].get("source_mode"),
            "source_row_coverage": snapshot["source"].get("pagination", {}),
        },
        "cohorts": cohorts,
        "ranked_content": ranked,
        "account_outliers": outliers,
        "focus_summary": focus_summary,
        "relevance": relevance,
        "claim_boundary": {
            "snapshot_count": 1,
            "allowed": ["current_public_performance", "raw_popularity", "account_relative_outlier"],
            "not_allowed": ["growth", "acceleration", "breakout_confirmed", "private_ctr", "retention", "audience_demographics", "conversion"],
        },
        "evidence": evidence,
        "reproducibility": {
            "input_sha256": input_sha,
            "request_sha256": request_sha,
            "formula_set_version": RULE_VERSION,
            "observed_at": observed.isoformat(),
            "window": {"start": start.isoformat(), "end": end.isoformat()},
        },
    }
    if allocation is not None:
        result["signature_content"] = signature_summary
        result["selected_content_ids"] = selected_content_ids
        result["reproducibility"]["allocation_sha256"] = digest(allocation)
    return result


def synthesis_view(analysis: dict[str, Any]) -> dict[str, Any]:
    """Expose selected evidence for writing; keep population statistics in the saved Analysis."""
    if "selected_content_ids" not in analysis:
        return analysis
    selected = set(analysis["selected_content_ids"])
    # 展示只是投影：不重排、不重新算基线，也不把完整分析误当成报告可选池。
    view = {key: analysis[key] for key in (
        "analysis_id", "data_quality", "claim_boundary", "signature_content", "selected_content_ids")}
    for key in ("ranked_content", "account_outliers", "evidence"):
        view[key] = [row for row in analysis[key] if row["content_id"] in selected]
    return view


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--mode", choices=["account", "trend", "competitor"], required=True)
    parser.add_argument("--focus-creator-id")
    parser.add_argument("--request", type=Path,
                        help="TrendResearchRequest used for deterministic direct/adjacent relevance gating")
    parser.add_argument("--relevance-gate", type=Path,
                        help="Semantic direct/adjacent/reject gate produced once over the candidate union")
    parser.add_argument("--trend-enrichment", type=Path,
                        help="Product-level exact detail and same-account baseline results")
    parser.add_argument("--allocation", type=Path,
                        help="signature-content-allocation.v1 seats, when the Creator's account profile is attached")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--print-synthesis", action="store_true",
                        help="Print selected evidence for synthesis; --output still saves the full Analysis")
    args = parser.parse_args()
    try:
        snapshot = json.loads(args.input.read_text(encoding="utf-8"))
        request = json.loads(args.request.read_text(encoding="utf-8")) if args.request else None
        relevance_gate = json.loads(args.relevance_gate.read_text(encoding="utf-8")) if args.relevance_gate else None
        trend_enrichment = json.loads(args.trend_enrichment.read_text(encoding="utf-8")) if args.trend_enrichment else None
        allocation = json.loads(args.allocation.read_text(encoding="utf-8")) if args.allocation else None
        result = analyze(snapshot, args.mode, args.focus_creator_id, request, relevance_gate, trend_enrichment,
                         allocation)
        text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text, encoding="utf-8")
        if args.print_synthesis:
            print(json.dumps(synthesis_view(result), ensure_ascii=False, indent=2, sort_keys=True))
        elif not args.output:
            print(text, end="")
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "failed", "error": f"{type(error).__name__}: {error}"}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
