#!/usr/bin/env python3
"""Semantic validator for Account Review V3 artifacts, without external deps."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Sibling imports must not leave a __pycache__ in the signed, checksummed skill directory.
sys.dont_write_bytecode = True
from json_io import load_complete_object


LANGUAGE_TAG = re.compile(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*")


def load(path: Path) -> dict[str, Any]:
    return load_complete_object(path)


def require(obj: dict[str, Any], keys: set[str], label: str) -> None:
    missing = sorted(keys - set(obj))
    if missing:
        raise ValueError(f"{label} missing required fields: {missing}")


def validate(analysis: dict[str, Any], synthesis: dict[str, Any], report: dict[str, Any], profile: dict[str, Any]) -> None:
    from evidence_context import maturity_by_format, evidence_age
    from synthesis_authoring import validate_authoring
    validate_authoring(analysis, synthesis)
    require(analysis, {"analysis_id", "snapshot_id", "baselines", "rankings", "coverage"}, "analysis")
    require(synthesis, {"analysis_id", "snapshot_id", "output_language", "headline", "what_works", "what_hurts"}, "synthesis")
    require(
        report,
        {"report_id", "profile_id", "headline", "data_coverage", "baselines", "what_works", "what_hurts"},
        "report",
    )
    require(profile, {"schema_version", "profile_id", "accounts"}, "profile")
    if analysis.get("schema_version") != "account-analysis.v3":
        raise ValueError("unexpected analysis schema_version")
    if synthesis.get("schema_version") != "account-review-synthesis.v3":
        raise ValueError("unexpected synthesis schema_version")
    if report.get("schema_version") != "account-review-report.v3":
        raise ValueError("unexpected report schema_version")
    if profile.get("schema_version") != "creator-profile.v3":
        raise ValueError("unexpected profile schema_version")
    if synthesis["analysis_id"] != analysis["analysis_id"] or synthesis["snapshot_id"] != analysis["snapshot_id"]:
        raise ValueError("synthesis provenance mismatch")
    # output_language names the language the model wrote the semantic strings in. It is not
    # checked against the characters used: quoted native titles, brand names and handles keep
    # their own script in any report language.
    if not isinstance(synthesis["output_language"], str) or not LANGUAGE_TAG.fullmatch(synthesis["output_language"]):
        raise ValueError("synthesis output_language must be a BCP-47 language tag such as en, zh-CN or pt-BR")
    if report["profile_id"] != profile["profile_id"]:
        raise ValueError("report/profile ID mismatch")
    if report["headline"] != synthesis["headline"]:
        raise ValueError("report headline differs from semantic synthesis")
    if report["data_coverage"] != analysis["coverage"]:
        raise ValueError("report data coverage differs from deterministic analysis")
    if report["baselines"] != analysis["baselines"]:
        raise ValueError("report baselines differ from deterministic analysis")

    ranking = {row["content_id"]: row for row in analysis["rankings"]}
    policies = maturity_by_format(analysis)
    for key in ("what_works", "what_hurts"):
        if len(report[key]) != len(synthesis[key]):
            raise ValueError(f"{key} patterns differ from synthesis")
        for row in synthesis[key]:
            if any(content_id not in ranking for content_id in row["content_ids"]):
                raise ValueError(f"{key} references unknown content")
        for row, authored in zip(report[key], synthesis[key]):
            if row.get("claim_type") != authored["claim_type"]:
                raise ValueError(f"{key} claim type differs from synthesis")
            for example in row["examples"]:
                source = ranking.get(example["content_id"])
                if source is None:
                    raise ValueError(f"{key} report references unknown content")
                for field, value in evidence_age(source, policies).items():
                    if example.get(field) != value:
                        raise ValueError(f"{key} example altered evidence age: {field}")
                for field in (
                    "url", "metrics", "view_multiple_vs_format_median",
                    "format_confidence", "format_source",
                ):
                    if example.get(field) != source.get(field):
                        raise ValueError(f"{key} example altered analyzer field: {field}")

    forbidden_profile_keys = {"baseline", "baselines", "rankings", "views", "reach", "confidence", "confirmation_status", "evidence_refs", "basis", "revision"}
    def walk(value: Any) -> None:
        if isinstance(value, dict):
            found = forbidden_profile_keys & set(value)
            if found:
                raise ValueError(f"profile contains forbidden keys: {sorted(found)}")
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(profile)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--analysis", type=Path, required=True)
    parser.add_argument("--synthesis", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    args = parser.parse_args()
    try:
        validate(load(args.analysis), load(args.synthesis), load(args.report), load(args.profile))
        print(json.dumps({"status": "passed"}))
        return 0
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "failed", "error": f"{type(error).__name__}: {error}"}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
