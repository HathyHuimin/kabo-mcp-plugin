#!/usr/bin/env python3
"""Build a compact, deterministic model input from AccountAnalysis."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Sibling imports must not leave a __pycache__ in the signed, checksummed skill directory.
sys.dont_write_bytecode = True
from json_io import load_complete_envelope, load_complete_object
from public_identity import apply_identity, identity_from_envelope
from prepare_hook_context import build_research_context_ref
from account_analyzers import parse_time
from evidence_context import baseline_maturity_context, maturity_by_format, evidence_age


SCHEMA_VERSION = "account-review-synthesis-input.v1"


def load(path: Path) -> dict[str, Any]:
    return load_complete_object(path)


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def require(obj: dict[str, Any], keys: set[str], label: str) -> None:
    missing = sorted(keys - set(obj))
    if missing:
        raise ValueError(f"{label} missing required fields: {missing}")


def compact(value: Any) -> Any:
    """Drop empty values while preserving false and numeric zero."""
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            reduced = compact(child)
            if reduced is None or reduced == "" or reduced == [] or reduced == {}:
                continue
            result[key] = reduced
        return result
    if isinstance(value, list):
        return [reduced for child in value if (reduced := compact(child)) not in (None, "", [], {})]
    return value


def equal(label: str, left: Any, right: Any) -> None:
    if left != right:
        raise ValueError(f"{label} mismatch between rankings and outliers/baselines")


def highest_view_multiple(content: list[dict[str, Any]]) -> dict[str, Any] | None:
    scored: list[tuple[float, float, str, dict[str, Any]]] = []
    for row in content:
        multiple = row.get("view_multiple_vs_format_median")
        if not isinstance(multiple, (int, float)):
            continue
        views = row.get("metrics", {}).get("views")
        view_value = float(views) if isinstance(views, (int, float)) else 0.0
        scored.append((float(multiple), view_value, str(row.get("content_id") or ""), row))
    if not scored:
        return None
    scored.sort(reverse=True)
    winner = scored[0][3]
    return compact({
        "content_id": winner.get("content_id"),
        "title": winner.get("title"),
        "url": winner.get("url"),
        "format": winner.get("format"),
        "view_multiple_vs_format_median": winner.get("view_multiple_vs_format_median"),
        "rank_by_views": winner.get("rank_by_views"),
        **{key: winner[key] for key in ("published_at", "baseline_maturity", "baseline_cutoff_at") if key in winner},
    })


def durations_from_snapshot(snapshot: dict[str, Any], snapshot_id: Any) -> dict[str, float]:
    """content_id -> duration_seconds from the snapshot the analysis was built from."""
    if snapshot.get("snapshot_id") != snapshot_id:
        raise ValueError("snapshot does not match the analysis snapshot_id")
    durations: dict[str, float] = {}
    for item in snapshot.get("items") or []:
        value = item.get("duration_seconds") if isinstance(item, dict) else None
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            durations[str(item.get("content_id"))] = value
    return durations


def baseline_maturity_context(analysis: dict[str, Any]) -> dict[str, Any] | None:
    """Expose the Analyzer's existing inclusive-day rule without changing its baselines."""
    if not analysis["baselines"]:
        return None
    scope = analysis["scope"]
    window_end = scope.get("window", {}).get("end")
    anchor = parse_time(f"{window_end}T23:59:59+00:00") if window_end and "T" not in window_end else parse_time(window_end or scope["observed_at"])
    by_format = []
    for baseline in analysis["baselines"]:
        days = baseline["maturity_days"]
        if not isinstance(days, int) or isinstance(days, bool) or days < 1:
            raise ValueError("baseline maturity_days must be a positive integer")
        elapsed = days - 1
        by_format.append({
            "format": baseline["format"],
            "maturity_days": days,
            "cutoff_at": (anchor - timedelta(days=elapsed)).isoformat(),
            "minimum_elapsed_days_at_anchor": elapsed,
        })
    return {
        "convention": "publication_day_counts_as_day_one",
        "anchor_source": "scope.window.end" if window_end else "scope.observed_at",
        "anchor_at": anchor.isoformat(),
        "comparison": "published_at <= cutoff_at",
        "by_format": by_format,
    }


def build(
    analysis: dict[str, Any],
    *,
    profile_envelope: dict[str, Any] | None = None,
    user_question: str | None = None,
    snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    require(
        analysis,
        {
            "schema_version", "analysis_id", "snapshot_id", "platform", "source_mode",
            "scope", "coverage", "baselines", "rankings", "outliers",
            "pillar_performance", "funnel_signals", "claim_boundary",
        },
        "analysis",
    )
    if analysis["schema_version"] != "account-analysis.v3":
        raise ValueError("unexpected analysis schema_version")

    rankings = analysis["rankings"]
    outliers = analysis["outliers"]
    if not isinstance(rankings, list) or not isinstance(outliers, list):
        raise ValueError("rankings and outliers must be arrays")

    outlier_by_id: dict[str, dict[str, Any]] = {}
    for row in outliers:
        content_id = str(row["content_id"])
        if content_id in outlier_by_id:
            raise ValueError(f"duplicate outlier content_id: {content_id}")
        outlier_by_id[content_id] = row

    baseline_by_format: dict[str, dict[str, Any]] = {}
    for row in analysis["baselines"]:
        content_format = str(row["format"])
        if content_format in baseline_by_format:
            raise ValueError(f"duplicate baseline format: {content_format}")
        baseline_by_format[content_format] = row

    ranking_ids = [str(row["content_id"]) for row in rankings]
    if len(ranking_ids) != len(set(ranking_ids)):
        raise ValueError("rankings contain duplicate content_id")
    if set(ranking_ids) != set(outlier_by_id):
        raise ValueError("rankings and outliers contain different content IDs")
    if analysis["coverage"].get("content_total") != len(rankings):
        raise ValueError("coverage content_total does not match rankings")

    # The analysis drops media length; without it the synthesis reads as if no duration exists.
    durations = durations_from_snapshot(snapshot, analysis["snapshot_id"]) if snapshot else {}
    policies = maturity_by_format(analysis)

    content = []
    for ranking in rankings:
        content_id = str(ranking["content_id"])
        outlier = outlier_by_id[content_id]
        content_format = ranking["format"]
        equal(f"{content_id} format", content_format, outlier.get("format"))
        for field in ("format_confidence", "format_source"):
            equal(f"{content_id} {field}", ranking.get(field), outlier.get(field))
        equal(f"{content_id} views", ranking.get("metrics", {}).get("views"), outlier.get("views"))
        equal(
            f"{content_id} view multiple",
            ranking.get("view_multiple_vs_format_median"),
            outlier.get("view_multiple"),
        )

        baseline = baseline_by_format.get(content_format)
        expected_median = None
        expected_n = 0
        if baseline:
            expected_median = baseline.get("metrics", {}).get("views", {}).get("median")
            expected_n = baseline.get("sample_size", 0)
        equal(f"{content_id} baseline median", expected_median, outlier.get("baseline_median_views"))
        equal(f"{content_id} comparable sample", expected_n, outlier.get("comparable_n"))

        content.append(compact({
            "content_id": content_id,
            "title": ranking["title"],
            "url": ranking.get("url"),
            "published_at": ranking["published_at"],
            **evidence_age(ranking, policies),
            "format": content_format,
            "format_confidence": ranking.get("format_confidence"),
            "format_source": ranking.get("format_source"),
            "metrics": ranking["metrics"],
            "duration_seconds": durations.get(content_id),
            "rank_by_views": ranking["rank_by_views"],
            "view_multiple_vs_format_median": ranking.get("view_multiple_vs_format_median"),
        }))

    result = {
        "schema_version": SCHEMA_VERSION,
        "snapshot_id": analysis["snapshot_id"],
        "analysis_id": analysis["analysis_id"],
        "platform": analysis["platform"],
        "source_mode": analysis["source_mode"],
        "scope": compact(analysis["scope"]),
        "coverage": compact(analysis["coverage"]),
        "baselines": compact(analysis["baselines"]),
        "content": content,
        "pillar_performance": compact(analysis["pillar_performance"]),
        "funnel_signals": compact(analysis["funnel_signals"]),
        "claim_boundary": compact(analysis["claim_boundary"]),
    }
    scope = dict(result.get("scope") or {})
    maturity = baseline_maturity_context(analysis)
    if maturity:
        scope["baseline_maturity"] = maturity
    scope = apply_identity(scope, identity_from_envelope(profile_envelope or {}))
    if user_question and user_question.strip():
        scope["user_question"] = user_question.strip()
    pointer = highest_view_multiple(content)
    if pointer:
        scope["highest_view_multiple"] = pointer
    if scope:
        result["scope"] = compact(scope)
    result["hook_context"] = build_research_context_ref(analysis["platform"], "account_review")
    return compact(result)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--analysis", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--profile-envelope",
        type=Path,
        help="optional resolve_profile / resolve_channel envelope; copies bio and followers into scope",
    )
    parser.add_argument(
        "--snapshot",
        type=Path,
        help="optional public snapshot the analysis was built from; copies each item's duration_seconds",
    )
    parser.add_argument(
        "--user-question",
        help="the user's original request, copied into scope.user_question for the one synthesis pass",
    )
    parser.add_argument(
        "--print",
        action="store_true",
        help="also print the compact synthesis input, so it does not have to be read back",
    )
    args = parser.parse_args()
    try:
        envelope = load_complete_envelope(args.profile_envelope) if args.profile_envelope else None
        result = build(
            load(args.analysis),
            profile_envelope=envelope,
            user_question=args.user_question,
            snapshot=load(args.snapshot) if args.snapshot else None,
        )
        dump(args.output, result)
        if args.print:
            print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 0
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "failed", "error": f"{type(error).__name__}: {error}"}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
