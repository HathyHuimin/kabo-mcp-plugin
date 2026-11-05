#!/usr/bin/env python3
"""Fail-closed validator for ContentCalendarRequest and ContentCalendar."""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import re
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

# Sibling imports must not leave a __pycache__ in the signed, checksummed skill directory.
sys.dont_write_bytecode = True
from calendar_common import (
    planning_end,
    CALENDAR_SCHEMA,
    REQUEST_SCHEMA,
    artifact_key,
    datetime_matches_zone,
    file_sha256,
    get_zone,
    load_artifact_ref,
    load_json,
    parse_offset_datetime,
    recommendation_contains_instant,
    require,
    safe_artifact_path,
    selected_idea_content_preview,
    selected_idea_semantic_key,
    UPSTREAM_SCHEMAS,
    validate_asset_ref,
    validate_against_schema,
)


def _load_upstream_validator(name: str, filename: str):
    path = Path(__file__).resolve().parent / "upstream" / filename
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load vendored upstream validator: {filename}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Owner validators resolve schemas relative to their native Skill root.
    # The vendored bytes stay identical; only their schema roots are rebound to
    # the immutable snapshots packaged with this Calendar Skill.
    if filename == "validate_script_package.py":
        upstream = Path(__file__).resolve().parent.parent / "references"
        module.REQUEST_SCHEMA = upstream / "upstream-script-package-request.v1.schema.json"
        module.PACKAGE_SCHEMA = upstream / "upstream-script-package.v1.schema.json"
        module.SELECTED_IDEA_SCHEMA = upstream / "upstream-selected-idea.v2.schema.json"
    return module


UPSTREAM_PACKAGE_VALIDATOR = _load_upstream_validator(
    "plan_calendar_upstream_package_validator", "validate_script_package.py"
)
UPSTREAM_TIMING_VALIDATOR = _load_upstream_validator(
    "plan_calendar_upstream_timing_validator", "validate_publish_recommendation.py"
)
# The frozen V3 timing validator predates SelectedIdea v2, while V3 final Ideas
# emits it with the same stable selected_idea_id. Keep the owner bytes frozen
# and declare this Calendar-only compatibility adapter explicitly.
UPSTREAM_TIMING_VALIDATOR.ARTIFACT_ID_FIELDS["selected-idea.v2"] = "selected_idea_id"
UPSTREAM_TIMING_VALIDATOR.ARTIFACT_ID_FIELDS["creator-profile.v3"] = "profile_id"

TIMEZONE_SOURCE_RANK = {
    "platform_utc_only": -1,
    "assumed": 0,
    "profile_confirmed": 1,
    "creator_confirmed": 2,
}


def effective_request_timezone_source(value: Any) -> Any:
    """Map an honest missing-zone request to the timing engine's lowest provenance."""

    return "platform_utc_only" if value == "not_supplied" else value


def _window_contains(request_window: Any, observed_window: Any) -> bool:
    """Compare timing windows at their declared date granularity."""

    if request_window is None:
        return True
    if observed_window is None or not isinstance(request_window, dict) or not isinstance(observed_window, dict):
        return False
    try:
        requested_since = date.fromisoformat(str(request_window["since"])[:10])
        requested_until = date.fromisoformat(str(request_window["until"])[:10])
        observed_since = date.fromisoformat(str(observed_window["since"])[:10])
        observed_until = date.fromisoformat(str(observed_window["until"])[:10])
    except (KeyError, TypeError, ValueError):
        return False
    return requested_since <= observed_since <= observed_until <= requested_until


def _append_schema_errors(value: Any, schema: Path, label: str, errors: list[str]) -> None:
    try:
        errors.extend(f"{label} schema {error}" for error in validate_against_schema(value, schema))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        errors.append(f"{label} schema could not be evaluated: {type(error).__name__}: {error}")


def _artifact_refs(request: dict[str, Any]):
    single_slot = request.get("single_slot_decision") or {}
    pending_request_ref = single_slot.get("pending_request_ref")
    if pending_request_ref is not None:
        yield "single-slot pending request", pending_request_ref, "content-calendar-request.v1"
    proposal_ref = single_slot.get("proposal_ref")
    if proposal_ref is not None:
        yield "single-slot pending proposal", proposal_ref, "content-calendar.v1"
    for ref in request.get("candidate_idea_set_refs") or []:
        yield "candidate idea set", ref, "idea-set.v3-candidate1"
    for item in request.get("items") or []:
        prefix = f"item {item.get('item_id')!r}"
        yield f"{prefix} selected idea", item.get("selected_idea_ref"), "selected-idea.v2"
        if item.get("script_package_request_ref") is not None:
            yield f"{prefix} script package request", item.get("script_package_request_ref"), "script-package-request.v1"
        if item.get("script_package_ref") is not None:
            yield f"{prefix} script package", item.get("script_package_ref"), "script-package.v1"
        timing = item.get("timing") or {}
        if timing.get("publish_request_ref") is not None:
            yield f"{prefix} publish request", timing.get("publish_request_ref"), "publish-request.v1"
        if timing.get("publish_history_ref") is not None:
            yield f"{prefix} publish history", timing.get("publish_history_ref"), "publish-history.v2"
        if timing.get("publish_recommendation_ref") is not None:
            yield f"{prefix} publish recommendation", timing.get("publish_recommendation_ref"), "publish-recommendation.v2"
        if timing.get("publish_schedule_ref") is not None:
            yield f"{prefix} publish schedule", timing.get("publish_schedule_ref"), "publish-schedule.v1"


def validate_request(
    request: dict[str, Any], request_path: Path, run_root: Path
) -> tuple[list[str], dict[str, Any]]:
    """Validate planning bounds and all cross-artifact scheduling constraints."""
    errors: list[str] = []
    artifacts: dict[str, Any] = {"items": {}, "candidates": {}, "paths": {}}
    _append_schema_errors(request, REQUEST_SCHEMA, "request", errors)
    if errors:
        return errors, artifacts

    planning = request.get("planning_window") or {}
    start = date.fromisoformat(planning["start_date"])
    end = planning_end(planning)
    days = (end - start).days + 1
    require(1 <= days <= 91, "planning window must contain 1 to 91 days", errors)
    require(planning["weeks"] == (days + 6) // 7, "weeks must equal the inclusive date range rounded up to whole weeks", errors)
    if request["intent"] == "date_range_plan":
        require("end_date" in planning, "date_range_plan requires an explicit inclusive end_date", errors)
    else:
        require(planning["weeks"] == 4 and days == 28, "four_week_plan and choose_for_slot retain their 28-day window", errors)

    try:
        get_zone(planning.get("timezone"))
    except ValueError as error:
        errors.append(str(error))

    items = request.get("items") or []
    item_ids = [item.get("item_id") for item in items]
    require(len(item_ids) == len(set(item_ids)), "item_id values must be unique", errors)
    selected_ids = [artifact_key(item.get("selected_idea_ref")) for item in items]
    require(len(selected_ids) == len(set(selected_ids)), "a SelectedIdea may appear in only one calendar item; repurposing is LATER", errors)
    request_created = parse_offset_datetime(request["created_at"])
    for item in items:
        progress = item.get("production_progress") or {}
        if progress.get("as_of") is not None:
            try:
                require(
                    parse_offset_datetime(progress["as_of"]) <= request_created,
                    f"{item.get('item_id')}: production_progress.as_of cannot be in the future relative to request.created_at",
                    errors,
                )
            except (TypeError, ValueError):
                pass

    rules = request.get("cadence_rules") or []
    rule_keys: list[tuple[Any, Any, Any]] = []
    for index, rule in enumerate(rules):
        weekdays = rule.get("preferred_weekdays") or []
        require(
            isinstance(rule.get("posts_per_week"), int)
            and rule.get("posts_per_week", 0) <= len(weekdays),
            f"cadence_rules[{index}]: posts_per_week requires at least that many preferred_weekdays",
            errors,
        )
        key = (rule.get("account_ref"), rule.get("platform"), rule.get("publish_format"), rule.get("production_format"))
        rule_keys.append(key)
    require(len(rule_keys) == len(set(rule_keys)), "cadence rule targets must be unique", errors)
    for left_index, left in enumerate(rules):
        for right in rules[left_index + 1:]:
            same_owner = left.get("account_ref") == right.get("account_ref") and left.get("platform") == right.get("platform")
            publish_overlap = left.get("publish_format") is None or right.get("publish_format") is None or left.get("publish_format") == right.get("publish_format")
            production_overlap = left.get("production_format") is None or right.get("production_format") is None or left.get("production_format") == right.get("production_format")
            require(not (same_owner and publish_overlap and production_overlap), "cadence rules must not overlap through wildcard targets", errors)

    overrides = ((request.get("capacity") or {}).get("week_overrides") or [])
    weeks = [item.get("week_index") for item in overrides]
    require(all(week <= planning["weeks"] for week in weeks), "capacity override is outside the planning window", errors)
    require(len(weeks) == len(set(weeks)), "capacity week_overrides must have unique week_index values", errors)

    loaded_by_identity: dict[tuple[str, str], dict[str, Any]] = {}
    for label, ref, expected_schema in _artifact_refs(request):
        if not isinstance(ref, dict):
            errors.append(f"{label}: missing artifact reference")
            continue
        path, artifact = load_artifact_ref(ref, request_path, run_root, expected_schema, label, errors)
        if artifact is not None:
            identity = (expected_schema, str(ref.get("artifact_id")))
            existing_path = artifacts["paths"].get(identity)
            if existing_path is not None:
                require(existing_path == path, f"{label}: artifact identity {identity} is bound to conflicting paths or bytes", errors)
            else:
                loaded_by_identity[identity] = artifact
                artifacts["paths"][identity] = path

    # Never run shape-dependent cross-contract checks on an artifact set that
    # already failed identity, hash, JSON, or upstream-schema validation.
    if errors:
        return errors, artifacts

    for ref in request.get("candidate_idea_set_refs") or []:
        key = ("idea-set.v3-candidate1", str(ref.get("artifact_id")))
        artifact = loaded_by_identity.get(key)
        if artifact is not None:
            artifacts["candidates"][ref.get("artifact_id")] = artifact
            require(
                artifact.get("status") == "ready_for_selection" and bool(artifact.get("ideas")),
                f"candidate IdeaSet {ref.get('artifact_id')}: only ready_for_selection with at least one candidate may enter Calendar backlog",
                errors,
            )

    for item in items:
        item_id = item.get("item_id")
        selected_ref = item.get("selected_idea_ref") or {}
        package_request_ref = item.get("script_package_request_ref")
        package_ref = item.get("script_package_ref")
        timing = item.get("timing") or {}
        publish_request_ref = timing.get("publish_request_ref")
        history_ref = timing.get("publish_history_ref")
        rec_ref = timing.get("publish_recommendation_ref")
        schedule_ref = timing.get("publish_schedule_ref")
        selected = loaded_by_identity.get(("selected-idea.v2", str(selected_ref.get("artifact_id"))))
        package_request = loaded_by_identity.get(("script-package-request.v1", str((package_request_ref or {}).get("artifact_id")))) if package_request_ref else None
        package = loaded_by_identity.get(("script-package.v1", str((package_ref or {}).get("artifact_id")))) if package_ref else None
        publish_request = loaded_by_identity.get(("publish-request.v1", str((publish_request_ref or {}).get("artifact_id")))) if publish_request_ref else None
        history = loaded_by_identity.get(("publish-history.v2", str((history_ref or {}).get("artifact_id")))) if history_ref else None
        recommendation = loaded_by_identity.get(("publish-recommendation.v2", str((rec_ref or {}).get("artifact_id")))) if rec_ref else None
        schedule = loaded_by_identity.get(("publish-schedule.v1", str((schedule_ref or {}).get("artifact_id")))) if schedule_ref else None
        artifacts["items"][item_id] = {
            "selected": selected,
            "package_request": package_request,
            "package": package,
            "publish_request": publish_request,
            "history": history,
            "recommendation": recommendation,
            "schedule": schedule,
            "source_script_package": None,
        }

        target = item.get("target") or {}
        if selected is not None:
            require(selected.get("selection_status") in {"selected", "selected_with_edits"}, f"{item_id}: idea must be selected", errors)
            idea = selected.get("idea") or {}
            require(target.get("platform") in (idea.get("platforms") or []), f"{item_id}: target platform is not in SelectedIdea", errors)
            require(target.get("idea_format") in (idea.get("formats") or []), f"{item_id}: target idea_format is not in SelectedIdea", errors)

        require((package is None) == (package_request is None), f"{item_id}: ScriptPackage and ScriptPackageRequest must be supplied together", errors)
        if package is not None and package_request is not None and selected is not None:
            require(package.get("request_id") == package_request.get("request_id"), f"{item_id}: ScriptPackage request_id mismatch", errors)
            package_request_path = artifacts["paths"].get(("script-package-request.v1", str(package_request_ref.get("artifact_id"))))
            selected_path = artifacts["paths"].get(("selected-idea.v2", str(selected_ref.get("artifact_id"))))
            nested_selected_ref = package_request.get("selected_idea") or {}
            if package_request_path is not None:
                try:
                    nested_selected_path = safe_artifact_path(
                        package_request_path,
                        run_root,
                        str(nested_selected_ref.get("path", "")),
                    )
                    require(nested_selected_path == selected_path, f"{item_id}: ScriptPackageRequest selected_idea path does not resolve to the bound SelectedIdea", errors)
                except ValueError as error:
                    errors.append(f"{item_id}: ScriptPackageRequest selected_idea: {error}")
                nested_refs = []
                for field in ("creator_profile", "source_script_package"):
                    if package_request.get(field):
                        nested_refs.append((field, package_request[field]))
                nested_refs.extend((f"video_analysis_sources[{index}]", ref) for index, ref in enumerate(package_request.get("video_analysis_sources") or []))
                for label, nested_ref in nested_refs:
                    try:
                        safe_artifact_path(package_request_path, run_root, str(nested_ref.get("path", "")))
                    except ValueError as error:
                        errors.append(f"{item_id}: ScriptPackageRequest {label}: {error}")
                # The frozen upstream validator resolves paths without a run-root
                # boundary. Call it only after Calendar's safe-path preflight has
                # left the whole request error-free, so an escaping ref is never
                # dereferenced by the legacy code.
                if not errors:
                    try:
                        errors.extend(
                            f"{item_id}: upstream ScriptPackage validator: {error}"
                            for error in UPSTREAM_PACKAGE_VALIDATOR.validate_package(
                                package,
                                package_request,
                                package_request_path,
                                run_root=run_root,
                            )
                        )
                    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
                        errors.append(f"{item_id}: upstream ScriptPackage validator could not run: {type(error).__name__}: {error}")
            package_profile_ref = package_request.get("creator_profile")
            if package_profile_ref is not None and package_request_path is not None:
                try:
                    package_profile_path = safe_artifact_path(
                        package_request_path,
                        run_root,
                        str(package_profile_ref.get("path", "")),
                    )
                    require(package_profile_path.is_file(), f"{item_id}: ScriptPackageRequest creator_profile path not found", errors)
                    if package_profile_path.is_file():
                        package_profile = load_json(package_profile_path)
                        require(package_profile_ref.get("schema_version") == "creator-profile.v3", f"{item_id}: ScriptPackageRequest creator_profile must use creator-profile.v3", errors)
                        require(package_profile.get("schema_version") == "creator-profile.v3", f"{item_id}: ScriptPackageRequest CreatorProfile artifact must use creator-profile.v3", errors)
                        require(package_profile_ref.get("artifact_id") == package_profile.get("profile_id"), f"{item_id}: ScriptPackageRequest creator_profile artifact_id mismatch", errors)
                        require(package_profile_ref.get("sha256") == file_sha256(package_profile_path), f"{item_id}: ScriptPackageRequest creator_profile sha256 mismatch", errors)
                        errors.extend(
                            f"{item_id}: ScriptPackageRequest CreatorProfile upstream schema {error}"
                            for error in validate_against_schema(package_profile, UPSTREAM_SCHEMAS["creator-profile.v3"])
                        )
                        require(
                            any(
                                account.get("platform") == target.get("platform")
                                and account.get("handle") == target.get("account_ref")
                                for account in package_profile.get("accounts") or []
                            ),
                            f"{item_id}: ScriptPackageRequest CreatorProfile has no account matching the Calendar target",
                            errors,
                        )
                except (OSError, ValueError, json.JSONDecodeError, AttributeError) as error:
                    errors.append(f"{item_id}: invalid ScriptPackageRequest CreatorProfile artifact: {type(error).__name__}: {error}")
            snapshot = package.get("input_snapshot") or {}
            frozen = selected.get("idea") or {}
            constraints = selected.get("confirmed_constraints") or {}
            require(snapshot.get("selected_idea_id") == selected.get("selected_idea_id"), f"{item_id}: ScriptPackage selected_idea_id mismatch", errors)
            require(snapshot.get("source_candidate_id") == selected.get("source_candidate_id"), f"{item_id}: ScriptPackage source_candidate_id mismatch", errors)
            require(snapshot.get("working_title") == frozen.get("working_title"), f"{item_id}: ScriptPackage working_title mismatch", errors)
            require(snapshot.get("content_promise") == frozen.get("content_promise"), f"{item_id}: ScriptPackage content_promise mismatch", errors)
            require(snapshot.get("audience") == constraints.get("audience"), f"{item_id}: ScriptPackage audience mismatch", errors)
            require(snapshot.get("desired_audience_action") == constraints.get("desired_audience_action"), f"{item_id}: ScriptPackage desired_audience_action mismatch", errors)
            require(snapshot.get("language") == constraints.get("language"), f"{item_id}: ScriptPackage language mismatch", errors)
            require(snapshot.get("platform") == target.get("platform"), f"{item_id}: ScriptPackage platform mismatch", errors)
            require(snapshot.get("format") == target.get("production_format"), f"{item_id}: ScriptPackage production_format mismatch", errors)
            require(set(snapshot.get("source_context_refs") or []) == set(frozen.get("source_context_refs") or []), f"{item_id}: ScriptPackage source_context_refs mismatch", errors)
            require(set(snapshot.get("evidence_refs") or []) == set(frozen.get("evidence_refs") or []), f"{item_id}: ScriptPackage evidence_refs mismatch", errors)
            require(set(frozen.get("must_differ") or []) <= set(snapshot.get("must_differ") or []), f"{item_id}: ScriptPackage dropped must_differ constraints", errors)
            require(snapshot.get("required_assets") == frozen.get("required_assets"), f"{item_id}: ScriptPackage required_assets mismatch", errors)
            require(snapshot.get("risks") == frozen.get("risks"), f"{item_id}: ScriptPackage risks mismatch", errors)
            if package.get("mode") == "packaging_only":
                source_script_ref = package_request.get("source_script_package")
                package_request_path = artifacts["paths"].get(("script-package-request.v1", str(package_request_ref.get("artifact_id"))))
                source_script_package = None
                if isinstance(source_script_ref, dict) and package_request_path is not None:
                    try:
                        source_script_path = safe_artifact_path(package_request_path, run_root, str(source_script_ref.get("path", "")))
                        if source_script_path.is_file():
                            source_script_package = load_json(source_script_path)
                    except (OSError, ValueError, json.JSONDecodeError) as error:
                        errors.append(f"{item_id}: invalid source_script_package: {type(error).__name__}: {error}")
                artifacts["items"][item_id]["source_script_package"] = source_script_package
                require(source_script_package is not None, f"{item_id}: packaging_only requires a readable source ScriptPackage", errors)
                if source_script_package is not None:
                    source_snapshot_value = source_script_package.get("input_snapshot")
                    source_snapshot = source_snapshot_value if isinstance(source_snapshot_value, dict) else {}
                    source_script = source_script_package.get("script")
                    source_schema_errors = validate_against_schema(
                        source_script_package,
                        UPSTREAM_SCHEMAS["script-package.v1"],
                    )
                    errors.extend(
                        f"{item_id}: packaging_only source ScriptPackage upstream schema {error}"
                        for error in source_schema_errors
                    )
                    require(source_script_package.get("schema_version") == "script-package.v1", f"{item_id}: source ScriptPackage schema mismatch", errors)
                    require(
                        source_script_package.get("status") == "approved_for_production"
                        and source_script_package.get("approval_status") == "approved",
                        f"{item_id}: packaging_only source ScriptPackage must be approved_for_production and approved",
                        errors,
                    )
                    require(isinstance(source_script, dict), f"{item_id}: packaging_only source ScriptPackage must contain a script", errors)
                    required_source_checks = {
                        "idea_alignment", "hook_payoff", "single_cta", "evidence_and_claims",
                        "originality", "duration", "production_feasibility", "packaging_consistency",
                    }
                    source_checks_value = source_script_package.get("quality_checks")
                    source_checks = source_checks_value if isinstance(source_checks_value, list) else []
                    source_check_names = {check.get("check") for check in source_checks if isinstance(check, dict)}
                    require(required_source_checks <= source_check_names, f"{item_id}: packaging_only source ScriptPackage is missing required quality checks", errors)
                    require(not any(check.get("status") != "pass" for check in source_checks if isinstance(check, dict)), f"{item_id}: packaging_only source ScriptPackage requires all quality checks to pass", errors)
                    if not source_schema_errors and isinstance(source_script, dict):
                        require(
                            not any(scene.get("asset_status") != "available" for scene in source_script.get("scenes") or []),
                            f"{item_id}: packaging_only source ScriptPackage contains unresolved scene assets",
                            errors,
                        )
                    source_plan_value = source_script_package.get("production_plan")
                    source_plan = source_plan_value if isinstance(source_plan_value, dict) else {}
                    require(not (source_plan.get("missing_assets") or []), f"{item_id}: packaging_only source ScriptPackage contains missing production assets", errors)
                    source_claims_value = source_script_package.get("claim_ledger")
                    source_claims = source_claims_value if isinstance(source_claims_value, list) else []
                    require(
                        not any(claim.get("status") in {"placeholder", "prohibited"} for claim in source_claims if isinstance(claim, dict)),
                        f"{item_id}: packaging_only source ScriptPackage contains unresolved claims",
                        errors,
                    )
                    require(source_snapshot.get("selected_idea_id") == selected.get("selected_idea_id"), f"{item_id}: source ScriptPackage selected_idea_id mismatch", errors)
                    require(source_snapshot.get("platform") == target.get("platform"), f"{item_id}: source ScriptPackage platform mismatch", errors)
                    require(source_snapshot.get("format") == target.get("production_format"), f"{item_id}: source ScriptPackage production_format mismatch", errors)
                    require(source_snapshot.get("language") == constraints.get("language"), f"{item_id}: source ScriptPackage language mismatch", errors)
                    if isinstance(source_script, dict):
                        require(source_script.get("format") == target.get("production_format"), f"{item_id}: source script format mismatch", errors)
                        require(source_script.get("language") == constraints.get("language"), f"{item_id}: source script language mismatch", errors)

        if recommendation is not None or schedule is not None or history is not None:
            require(publish_request is not None, f"{item_id}: Recommendation/Schedule requires its PublishRequest artifact", errors)
        if recommendation is not None:
            require(history is not None, f"{item_id}: PublishRecommendation requires its PublishHistory artifact", errors)
        if publish_request is not None and selected is not None:
            content = publish_request.get("content") or {}
            creator = publish_request.get("creator") or {}
            history_meta = publish_request.get("history") or {}
            if history is None:
                require(
                    history_meta.get("source_mode") == "unavailable" and history_meta.get("status") == "unavailable",
                    f"{item_id}: available or partial PublishRequest history requires a bound PublishHistory artifact",
                    errors,
                )
                require(history_meta.get("history_ref") is None, f"{item_id}: unbound PublishRequest history_ref must be null", errors)
                require(history_meta.get("snapshot_sha256") is None, f"{item_id}: unavailable PublishRequest history snapshot_sha256 must be null", errors)
            require(creator.get("account_ref") == target.get("account_ref"), f"{item_id}: PublishRequest account_ref mismatch", errors)
            require(content.get("platform") == target.get("platform"), f"{item_id}: PublishRequest platform mismatch", errors)
            require(content.get("format") == target.get("publish_format"), f"{item_id}: PublishRequest publish_format mismatch", errors)
            expected_language = ((package or {}).get("input_snapshot") or {}).get("language") or (selected.get("confirmed_constraints") or {}).get("language")
            if content.get("language") is not None:
                require(content.get("language") == expected_language, f"{item_id}: PublishRequest language mismatch", errors)
            if package is not None:
                duration_script = package.get("script")
                if duration_script is None and package.get("mode") == "packaging_only":
                    duration_script = (artifacts["items"][item_id].get("source_script_package") or {}).get("script")
                expected_duration = ((duration_script or {}).get("estimated_duration_seconds"))
                if content.get("planned_duration_s") is not None:
                    require(expected_duration is not None and content.get("planned_duration_s") == expected_duration, f"{item_id}: PublishRequest planned_duration_s mismatch", errors)
            zone = (request.get("planning_window") or {}).get("timezone")
            request_zone_source = creator.get("timezone_source")
            calendar_zone_source = (request.get("planning_window") or {}).get("timezone_source")
            effective_request_zone_source = effective_request_timezone_source(request_zone_source)
            if request_zone_source == "not_supplied":
                require(creator.get("timezone") == "", f"{item_id}: PublishRequest not_supplied timezone must be empty", errors)
            else:
                require(creator.get("timezone") == zone, f"{item_id}: PublishRequest timezone mismatch", errors)
            require(effective_request_zone_source in TIMEZONE_SOURCE_RANK, f"{item_id}: PublishRequest timezone_source is unsupported by Calendar", errors)
            if effective_request_zone_source in TIMEZONE_SOURCE_RANK and calendar_zone_source in TIMEZONE_SOURCE_RANK:
                require(TIMEZONE_SOURCE_RANK[calendar_zone_source] >= TIMEZONE_SOURCE_RANK[effective_request_zone_source], f"{item_id}: Calendar timezone_source cannot downgrade PublishRequest provenance", errors)
            source_idea = content.get("source_idea_ref") or {}
            require(source_idea.get("artifact_id") == selected.get("selected_idea_id"), f"{item_id}: PublishRequest source_idea_ref mismatch", errors)
            require(source_idea.get("schema_version") == "selected-idea.v2", f"{item_id}: PublishRequest source_idea_ref schema mismatch", errors)
            require(source_idea.get("sha256") == selected_ref.get("sha256"), f"{item_id}: PublishRequest source_idea_ref sha256 mismatch", errors)
            publish_request_path = artifacts["paths"].get(("publish-request.v1", str(publish_request_ref.get("artifact_id"))))
            selected_path = artifacts["paths"].get(("selected-idea.v2", str(selected_ref.get("artifact_id"))))
            if publish_request_path is not None:
                try:
                    nested_idea_path = safe_artifact_path(publish_request_path, run_root, str(source_idea.get("path", "")))
                    require(nested_idea_path == selected_path, f"{item_id}: PublishRequest source_idea_ref path does not resolve to the bound SelectedIdea", errors)
                except ValueError as error:
                    errors.append(f"{item_id}: PublishRequest source_idea_ref: {error}")
            if package is not None and content.get("source_script_ref") is not None:
                source_script = content["source_script_ref"]
                require(source_script.get("artifact_id") == package.get("package_id"), f"{item_id}: PublishRequest source_script_ref mismatch", errors)
                require(source_script.get("schema_version") == "script-package.v1", f"{item_id}: PublishRequest source_script_ref schema mismatch", errors)
                require(source_script.get("sha256") == package_ref.get("sha256"), f"{item_id}: PublishRequest source_script_ref sha256 mismatch", errors)
                if publish_request_path is not None:
                    package_path = artifacts["paths"].get(("script-package.v1", str(package_ref.get("artifact_id"))))
                    try:
                        nested_script_path = safe_artifact_path(publish_request_path, run_root, str(source_script.get("path", "")))
                        require(nested_script_path == package_path, f"{item_id}: PublishRequest source_script_ref path does not resolve to the bound ScriptPackage", errors)
                    except ValueError as error:
                        errors.append(f"{item_id}: PublishRequest source_script_ref: {error}")
            else:
                require(content.get("source_script_ref") is None, f"{item_id}: PublishRequest source_script_ref must be null without a bound ScriptPackage", errors)
            profile_ref = creator.get("profile_ref")
            if profile_ref is not None:
                require(isinstance(profile_ref, dict), f"{item_id}: PublishRequest creator.profile_ref must be an artifact reference", errors)
                if isinstance(profile_ref, dict) and publish_request_path is not None:
                    try:
                        profile_path = safe_artifact_path(publish_request_path, run_root, str(profile_ref.get("path", "")))
                    except ValueError as error:
                        errors.append(f"{item_id}: PublishRequest creator.profile_ref: {error}")
                        profile_path = None
                    if profile_path is not None:
                        if not profile_path.is_file():
                            errors.append(f"{item_id}: PublishRequest creator.profile_ref path not found: {profile_ref.get('path')}")
                        else:
                            try:
                                profile = load_json(profile_path)
                                require(profile_ref.get("schema_version") == "creator-profile.v3", f"{item_id}: PublishRequest creator.profile_ref must use creator-profile.v3", errors)
                                require(profile.get("schema_version") == "creator-profile.v3", f"{item_id}: CreatorProfile artifact must use creator-profile.v3", errors)
                                require(profile_ref.get("artifact_id") == profile.get("profile_id"), f"{item_id}: PublishRequest creator.profile_ref artifact_id mismatch", errors)
                                require(profile_ref.get("sha256") == file_sha256(profile_path), f"{item_id}: PublishRequest creator.profile_ref sha256 mismatch", errors)
                                profile_schema_errors = validate_against_schema(
                                    profile,
                                    UPSTREAM_SCHEMAS["creator-profile.v3"],
                                )
                                errors.extend(
                                    f"{item_id}: CreatorProfile upstream schema {error}"
                                    for error in profile_schema_errors
                                )
                                accounts = profile.get("accounts") or []
                                if not profile_schema_errors:
                                    require(
                                        any(account.get("platform") == target.get("platform") and account.get("handle") == target.get("account_ref") for account in accounts),
                                        f"{item_id}: CreatorProfile has no account matching the Calendar target",
                                        errors,
                                    )
                                package_profile_ref = (package_request or {}).get("creator_profile")
                                if package_profile_ref is not None:
                                    require(
                                        all(package_profile_ref.get(key) == profile_ref.get(key) for key in ("artifact_id", "schema_version", "sha256")),
                                        f"{item_id}: ScriptPackageRequest and PublishRequest bind different CreatorProfile artifacts",
                                        errors,
                                    )
                                    package_request_path = artifacts["paths"].get(("script-package-request.v1", str((package_request_ref or {}).get("artifact_id"))))
                                    if package_request_path is not None:
                                        try:
                                            package_profile_path = safe_artifact_path(package_request_path, run_root, str(package_profile_ref.get("path", "")))
                                            require(package_profile_path == profile_path, f"{item_id}: ScriptPackageRequest and PublishRequest CreatorProfile paths differ", errors)
                                        except ValueError as error:
                                            errors.append(f"{item_id}: ScriptPackageRequest creator_profile: {error}")
                            except (OSError, ValueError, json.JSONDecodeError) as error:
                                errors.append(f"{item_id}: invalid CreatorProfile artifact: {type(error).__name__}: {error}")

        # PublishHistory is a useful partial-chain input even when the timing
        # engine abstains before emitting a Recommendation. Bind it to the
        # PublishRequest independently so removing a Recommendation cannot
        # bypass provenance, ownership, window, maturity, or count checks.
        if history is not None and publish_request is not None:
            history_meta = publish_request.get("history") or {}
            history_source = history.get("source") or {}
            coverage = history.get("coverage") or {}
            require(history.get("platform") == target.get("platform"), f"{item_id}: PublishHistory platform mismatch", errors)
            require(history.get("account_ref") == target.get("account_ref"), f"{item_id}: PublishHistory account_ref mismatch", errors)
            require(history_meta.get("source_mode") == history_source.get("mode"), f"{item_id}: PublishRequest history source_mode mismatch", errors)
            require(history_meta.get("snapshot_sha256") == history_source.get("snapshot_sha256"), f"{item_id}: PublishRequest history snapshot_sha256 mismatch", errors)
            if history_source.get("mode") in {"connector", "provided_export"} and history_meta.get("status") in {"available", "partial"}:
                require(
                    bool(re.fullmatch(r"[0-9a-f]{64}", str(history_source.get("snapshot_sha256") or ""))),
                    f"{item_id}: available PublishHistory requires an auditable 64-character lowercase hex snapshot_sha256",
                    errors,
                )
            if not (history_meta.get("status") == "unavailable" and history_source.get("window") is None):
                require(_window_contains(history_meta.get("window"), history_source.get("window")), f"{item_id}: PublishHistory observed window is outside the PublishRequest history window", errors)
            if history_meta.get("source_mode") == "unavailable" or history_meta.get("status") == "unavailable":
                require(not (history.get("posts") or []), f"{item_id}: unavailable history cannot carry post rows", errors)
                if recommendation is not None:
                    require(recommendation.get("outcome") == "abstain_no_history", f"{item_id}: unavailable history must produce abstain_no_history", errors)
            require(history_meta.get("maturity_days") == (history.get("maturity") or {}).get("days"), f"{item_id}: PublishRequest history maturity_days mismatch", errors)
            require(history_meta.get("post_count_total") == coverage.get("catalog_posts"), f"{item_id}: PublishRequest history post_count_total mismatch", errors)
            require(history_meta.get("post_count_usable") == coverage.get("cohort_mature_with_metric"), f"{item_id}: PublishRequest history post_count_usable mismatch", errors)
            catalog_count = coverage.get("catalog_posts")
            cohort_total = coverage.get("cohort_total")
            cohort_with_metric = coverage.get("cohort_with_metric")
            cohort_mature = coverage.get("cohort_mature_with_metric")
            require(
                0 <= cohort_mature <= cohort_with_metric <= cohort_total <= catalog_count,
                f"{item_id}: PublishHistory coverage counts must be monotonic",
                errors,
            )
            if history_meta.get("source_mode") == "unavailable":
                require(history_meta.get("status") == "unavailable", f"{item_id}: unavailable history source_mode requires unavailable status", errors)
                require(history_source.get("snapshot_sha256") is None, f"{item_id}: unavailable PublishHistory snapshot_sha256 must be null", errors)
                require(history_source.get("window") is None, f"{item_id}: unavailable PublishHistory source window must be null", errors)
                require(all(value == 0 for value in (catalog_count, cohort_total, cohort_with_metric, cohort_mature)), f"{item_id}: unavailable PublishHistory coverage counts must all be zero", errors)
                require(history_meta.get("post_count_total") == 0 and history_meta.get("post_count_usable") == 0, f"{item_id}: unavailable PublishRequest history counts must be zero", errors)
            elif history_meta.get("status") == "unavailable":
                require(cohort_with_metric == 0 and cohort_mature == 0, f"{item_id}: unavailable connector/export history cannot claim metric-bearing cohort rows", errors)
            publish_request_path = artifacts["paths"].get(("publish-request.v1", str(publish_request_ref.get("artifact_id"))))
            history_path = artifacts["paths"].get(("publish-history.v2", str(history_ref.get("artifact_id"))))
            if publish_request_path is not None:
                history_locator = history_meta.get("history_ref")
                if history_locator == history.get("history_id"):
                    pass
                else:
                    try:
                        nested_history_path = safe_artifact_path(publish_request_path, run_root, str(history_locator or ""))
                        require(nested_history_path == history_path, f"{item_id}: PublishRequest history_ref does not resolve to the bound PublishHistory", errors)
                    except ValueError as error:
                        errors.append(f"{item_id}: PublishRequest history_ref: {error}")

        if recommendation is not None and publish_request is not None:
            require(recommendation.get("request_ref") == publish_request.get("request_id"), f"{item_id}: Recommendation request_ref mismatch", errors)
            cohort = (recommendation.get("evidence_base") or {}).get("cohort") or {}
            require(cohort.get("platform") == target.get("platform"), f"{item_id}: Recommendation cohort platform mismatch", errors)
            require(cohort.get("format") == target.get("publish_format"), f"{item_id}: Recommendation cohort format mismatch", errors)
            analysis_zone = ((recommendation.get("evidence_base") or {}).get("timezone") or {}).get("analysis_zone")
            require(analysis_zone == (request.get("planning_window") or {}).get("timezone"), f"{item_id}: Recommendation timezone mismatch", errors)
            recommendation_zone_source = ((recommendation.get("evidence_base") or {}).get("timezone") or {}).get("source")
            request_zone_source = (publish_request.get("creator") or {}).get("timezone_source")
            expected_recommendation_source = effective_request_timezone_source(request_zone_source)
            require(recommendation_zone_source == expected_recommendation_source, f"{item_id}: Recommendation timezone source does not match PublishRequest", errors)
            if history is not None:
                history_source = history.get("source") or {}
                coverage = history.get("coverage") or {}
                require(cohort.get("source_mode") == history_source.get("mode"), f"{item_id}: Recommendation cohort source_mode mismatch", errors)
                require(cohort.get("snapshot_sha256") == history_source.get("snapshot_sha256"), f"{item_id}: Recommendation cohort snapshot_sha256 mismatch", errors)
                require(cohort.get("maturity_days") == (history.get("maturity") or {}).get("days"), f"{item_id}: Recommendation cohort maturity_days mismatch", errors)
                require(cohort.get("total_n") == coverage.get("cohort_total"), f"{item_id}: Recommendation cohort total_n mismatch", errors)
                mature_posts = [
                    post for post in history.get("posts") or []
                    if post.get("is_mature") and post.get("product_type") == cohort.get("format")
                ]
                mature_posts.sort(key=lambda post: parse_offset_datetime(post.get("published_at")))
                zone = get_zone((request.get("planning_window") or {}).get("timezone"))
                expected_window = None
                if mature_posts:
                    first = parse_offset_datetime(mature_posts[0]["published_at"]).astimezone(zone).isoformat()
                    last = parse_offset_datetime(mature_posts[-1]["published_at"]).astimezone(zone).isoformat()
                    expected_window = f"{first} .. {last}"
                require(cohort.get("window") == expected_window, f"{item_id}: Recommendation cohort window does not match mature PublishHistory rows", errors)
            publish_request_path = artifacts["paths"].get(("publish-request.v1", str(publish_request_ref.get("artifact_id"))))
            if publish_request_path is not None and not errors:
                try:
                    errors.extend(
                        f"{item_id}: upstream Publish Timing validator: {error}"
                        for error in UPSTREAM_TIMING_VALIDATOR.validate(
                            recommendation,
                            history,
                            publish_request,
                            schedule,
                            publish_request_path,
                        )
                    )
                except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
                    errors.append(f"{item_id}: upstream Publish Timing validator could not run: {type(error).__name__}: {error}")

        assets = item.get("publication_assets") or {}
        media_asset_ids: list[str] = []
        media_paths: list[Path] = []
        media_hashes: list[str] = []
        for asset_index, asset_ref in enumerate(assets.get("media_refs") or []):
            media_asset_ids.append(asset_ref.get("asset_id"))
            media_hashes.append(asset_ref.get("sha256"))
            media_path = validate_asset_ref(asset_ref, request_path, run_root, f"{item_id} media asset {asset_index}", errors)
            if media_path is not None:
                media_paths.append(media_path)
        require(len(media_asset_ids) == len(set(media_asset_ids)), f"{item_id}: media asset_id values must be unique", errors)
        require(len(media_paths) == len(set(media_paths)), f"{item_id}: media refs must resolve to distinct files", errors)
        require(len(media_hashes) == len(set(media_hashes)), f"{item_id}: media refs must contain distinct bytes", errors)
        if assets.get("cover_ref") is not None:
            validate_asset_ref(assets["cover_ref"], request_path, run_root, f"{item_id} cover asset", errors)
            require(assets["cover_ref"].get("media_type") == "image", f"{item_id}: cover asset media_type must be image", errors)

        creator_locked_at = timing.get("creator_locked_at")
        locked_instant = None
        if creator_locked_at is not None:
            try:
                locked_instant = parse_offset_datetime(creator_locked_at)
            except (TypeError, ValueError) as error:
                errors.append(f"{item_id}: invalid creator_locked_at: {error}")
            require(datetime_matches_zone(creator_locked_at, (request.get("planning_window") or {}).get("timezone")), f"{item_id}: creator_locked_at is not a valid wall time in the Calendar timezone", errors)

        if schedule is not None:
            require(recommendation is not None, f"{item_id}: PublishSchedule requires its PublishRecommendation artifact", errors)
            if recommendation is not None:
                require(schedule.get("recommendation_ref") == recommendation.get("recommendation_id"), f"{item_id}: PublishSchedule recommendation_ref mismatch", errors)
            # Calendar uses the immutable Creator-approved SelectedIdea as the
            # stable cross-Skill content identity. A ScriptPackage is a
            # replaceable production version and remains separately traceable
            # through PublishRequest.content.source_script_ref.
            expected_draft = artifact_key(selected_ref)
            require(
                schedule.get("draft_ref") == expected_draft,
                f"{item_id}: PublishSchedule draft_ref must equal the bound SelectedIdea id",
                errors,
            )
            require(schedule.get("state") not in {"published", "abandoned"}, f"{item_id}: published or abandoned schedules cannot enter a future calendar", errors)
            require(schedule.get("published_result_ref") is None, f"{item_id}: future PublishSchedule published_result_ref must be null", errors)
            schedule_history = schedule.get("state_history") or []
            require(bool(schedule_history) and schedule_history[-1].get("state") == schedule.get("state"), f"{item_id}: PublishSchedule state_history must end at current state", errors)
            history_instants = []
            for entry in schedule_history:
                try:
                    history_instants.append(parse_offset_datetime(entry.get("at")))
                except (TypeError, ValueError):
                    pass
            require(
                len(history_instants) == len(schedule_history)
                and all(left < right for left, right in zip(history_instants, history_instants[1:])),
                f"{item_id}: PublishSchedule state_history timestamps must be strictly increasing",
                errors,
            )
            allowed_transitions = {
                "proposed": {"accepted", "abandoned"},
                "accepted": {"scheduled", "abandoned"},
                "scheduled": {"published", "abandoned"},
                "published": set(),
                "abandoned": set(),
            }
            for previous, current in zip(schedule_history, schedule_history[1:]):
                require(
                    current.get("state") in allowed_transitions.get(previous.get("state"), set()),
                    f"{item_id}: PublishSchedule state_history contains an invalid transition {previous.get('state')} -> {current.get('state')}",
                    errors,
                )
            schedule_zone = (schedule.get("timezone") or {}).get("zone")
            require(schedule_zone == (request.get("planning_window") or {}).get("timezone"), f"{item_id}: PublishSchedule timezone mismatch", errors)
            schedule_zone_source = (schedule.get("timezone") or {}).get("source")
            request_zone_source = effective_request_timezone_source(
                ((publish_request or {}).get("creator") or {}).get("timezone_source")
            )
            calendar_zone_source = (request.get("planning_window") or {}).get("timezone_source")
            if schedule_zone_source in TIMEZONE_SOURCE_RANK and request_zone_source in TIMEZONE_SOURCE_RANK and calendar_zone_source in TIMEZONE_SOURCE_RANK:
                require(TIMEZONE_SOURCE_RANK[request_zone_source] <= TIMEZONE_SOURCE_RANK[schedule_zone_source] <= TIMEZONE_SOURCE_RANK[calendar_zone_source], f"{item_id}: PublishSchedule timezone source is not a monotonic provenance upgrade", errors)
            try:
                scheduled = parse_offset_datetime(schedule.get("scheduled_at"))
            except (TypeError, ValueError) as error:
                errors.append(f"{item_id}: invalid PublishSchedule scheduled_at: {error}")
                scheduled = None
            if scheduled is not None:
                require(datetime_matches_zone(schedule.get("scheduled_at"), schedule_zone), f"{item_id}: scheduled_at is not a valid wall time in the declared timezone", errors)
            source = schedule.get("source")
            confidence = schedule.get("confidence")
            if source == "engine_recommended":
                require(recommendation is not None and recommendation.get("outcome") == "recommended", f"{item_id}: an abstention cannot be laundered into engine_recommended", errors)
                if recommendation is not None and scheduled is not None:
                    require(recommendation_contains_instant(recommendation, scheduled), f"{item_id}: engine schedule is outside the recommended window", errors)
                    expected_confidence = (recommendation.get("recommendation") or {}).get("confidence")
                    require(confidence == expected_confidence, f"{item_id}: engine schedule confidence does not match Recommendation", errors)
            elif source == "creator_override_of_recommendation":
                require(recommendation is not None and recommendation.get("outcome") == "recommended", f"{item_id}: creator_override_of_recommendation requires a real Recommendation", errors)
                if recommendation is not None and scheduled is not None and recommendation.get("outcome") == "recommended":
                    require(not recommendation_contains_instant(recommendation, scheduled), f"{item_id}: creator_override_of_recommendation must be outside the recommended window", errors)
                require(confidence is None, f"{item_id}: non-engine schedule confidence must be null", errors)
            else:
                require(recommendation is not None and recommendation.get("outcome") != "recommended", f"{item_id}: creator_choice_not_recommended requires a non-recommended outcome", errors)
                require(confidence is None, f"{item_id}: non-engine schedule confidence must be null", errors)
            if locked_instant is not None and scheduled is not None:
                require(locked_instant == scheduled, f"{item_id}: creator_locked_at conflicts with PublishSchedule", errors)

    single = request.get("single_slot_decision")
    if isinstance(single, dict):
        ids = single.get("candidate_item_ids") or []
        require(set(ids) <= set(item_ids), "single_slot_decision candidate_item_ids must identify request items", errors)
        preferred = single.get("creator_preferred_item_id")
        require(preferred is None or preferred in ids, "single_slot_decision creator_preferred_item_id must be one of the candidates", errors)
        confirmed = single.get("confirmed_item_id")
        require(confirmed is None or confirmed in ids, "single_slot_decision confirmed_item_id must be one of the candidates", errors)
        if single.get("confirmation_status") == "confirmed":
            require(confirmed is not None and single.get("confirmed_at") is not None, "confirmed single_slot_decision requires confirmed_item_id and confirmed_at", errors)
            pending_request_ref = single.get("pending_request_ref") or {}
            proposal_ref = single.get("proposal_ref") or {}
            pending_request = loaded_by_identity.get(("content-calendar-request.v1", str(pending_request_ref.get("artifact_id"))))
            proposal = loaded_by_identity.get(("content-calendar.v1", str(proposal_ref.get("artifact_id"))))
            require(pending_request is not None, "confirmed single_slot_decision requires a readable pending request artifact", errors)
            require(proposal is not None, "confirmed single_slot_decision requires a readable pending proposal artifact", errors)
            if pending_request is not None and proposal is not None:
                pending_single = pending_request.get("single_slot_decision") or {}
                require(pending_request.get("intent") == "choose_for_slot", "single_slot_decision pending request must use choose_for_slot", errors)
                require(pending_single.get("confirmation_status") == "pending", "single_slot_decision pending request is not pending", errors)
                require(request.get("request_id") != pending_request.get("request_id"), "confirmed single_slot_decision must use a new immutable request_id", errors)
                frozen_current = copy.deepcopy(request)
                frozen_current["single_slot_decision"] = copy.deepcopy(pending_single)
                frozen_current["request_id"] = pending_request.get("request_id")
                frozen_current["created_at"] = pending_request.get("created_at")
                require(frozen_current == pending_request, "confirmed single_slot_decision changed the pending request outside confirmation fields", errors)

                pending_request_path = artifacts["paths"].get(("content-calendar-request.v1", str(pending_request_ref.get("artifact_id"))))
                proposal_path = artifacts["paths"].get(("content-calendar.v1", str(proposal_ref.get("artifact_id"))))
                if pending_request_path is not None and proposal_path is not None:
                    proposal_errors = validate_calendar(
                        proposal,
                        pending_request,
                        pending_request_path,
                        run_root,
                        proposal_path,
                    )
                    errors.extend(f"single_slot_decision pending proposal: {error}" for error in proposal_errors)
                prior = proposal.get("slot_decision") or {}
                require(proposal.get("intent") == "choose_for_slot" and proposal.get("projection_mode") == "focus_slot_patch", "single_slot_decision proposal must be a focus-slot Calendar", errors)
                require(prior.get("confirmation_status") == "pending" and prior.get("requires_creator_confirmation") is True, "single_slot_decision proposal is not awaiting Creator confirmation", errors)
                require(proposal.get("request_id") == pending_request.get("request_id"), "single_slot_decision proposal request_id mismatch", errors)
                for field in ("decision_id", "idempotency_key", "target_publish_at", "target", "candidate_item_ids"):
                    require(prior.get(field) == single.get(field), f"single_slot_decision {field} changed after the pending proposal", errors)
                require(prior.get("selected_item_id") == confirmed, "confirmed_item_id must match the item selected in the pending proposal", errors)
                try:
                    proposal_created = parse_offset_datetime(proposal.get("created_at"))
                    confirmation_request_created = parse_offset_datetime(request.get("created_at"))
                    confirmed_at = parse_offset_datetime(single.get("confirmed_at"))
                    target_at = parse_offset_datetime(single.get("target_publish_at"))
                    require(proposal_created <= confirmation_request_created <= confirmed_at, "single_slot_decision confirmation request timestamp is outside the proposal/confirmation interval", errors)
                    require(confirmed_at >= proposal_created, "single_slot_decision confirmed_at precedes the pending proposal", errors)
                    require(confirmed_at <= target_at, "single_slot_decision confirmation arrived after the target slot", errors)
                except (TypeError, ValueError) as error:
                    errors.append(f"single_slot_decision confirmation timeline is invalid: {error}")
        target = single.get("target") or {}
        target_at = single.get("target_publish_at")
        zone_name = (request.get("planning_window") or {}).get("timezone")
        require(datetime_matches_zone(target_at, zone_name), "single_slot_decision target_publish_at is not valid in the Calendar timezone", errors)
        try:
            target_instant = parse_offset_datetime(target_at)
            request_created = parse_offset_datetime(request.get("created_at"))
            require(request_created <= target_instant, "single_slot_decision target_publish_at must not precede request.created_at", errors)
            local_date = target_instant.astimezone(get_zone(zone_name)).date()
            start = date.fromisoformat((request.get("planning_window") or {}).get("start_date"))
            require(start <= local_date <= planning_end(request["planning_window"]), "single_slot_decision target_publish_at is outside the planning window", errors)
        except (TypeError, ValueError):
            target_instant = None
        commitments_at_target: list[str] = []
        for item in items:
            creator_locked_instant = None
            creator_locked_value = (item.get("timing") or {}).get("creator_locked_at")
            if creator_locked_value is not None:
                try:
                    creator_locked_instant = parse_offset_datetime(creator_locked_value)
                except (TypeError, ValueError, AttributeError):
                    pass
            if item.get("item_id") in ids:
                require(item.get("target") == target, f"single_slot_decision candidate {item.get('item_id')} target mismatch", errors)
            else:
                loaded = artifacts.get("items", {}).get(item.get("item_id"), {})
                schedule = loaded.get("schedule") or {}
                scheduled_value = schedule.get("scheduled_at")
                same_instant = False
                if scheduled_value is not None and target_instant is not None:
                    try:
                        same_instant = parse_offset_datetime(scheduled_value) == target_instant
                    except (TypeError, ValueError, AttributeError):
                        pass
                locked_same_instant = creator_locked_instant is not None and creator_locked_instant == target_instant
                require(
                    not (
                        item.get("target") == target
                        and (
                            (schedule.get("state") in {"accepted", "scheduled"} and same_instant)
                            or locked_same_instant
                        )
                    ),
                    f"single_slot_decision omitted a creator-locked, accepted, or scheduled commitment {item.get('item_id')} at the target time",
                    errors,
                )
            loaded = artifacts.get("items", {}).get(item.get("item_id"), {})
            schedule = loaded.get("schedule") or {}
            scheduled_instant = None
            if schedule.get("scheduled_at") is not None:
                try:
                    scheduled_instant = parse_offset_datetime(schedule.get("scheduled_at"))
                except (TypeError, ValueError, AttributeError):
                    pass
            has_schedule_commitment = schedule.get("state") in {"accepted", "scheduled"} and scheduled_instant == target_instant
            has_creator_commitment = creator_locked_instant is not None and creator_locked_instant == target_instant
            if item.get("target") == target and (has_schedule_commitment or has_creator_commitment):
                commitments_at_target.append(item.get("item_id"))
            if single.get("confirmation_status") == "confirmed" and item.get("item_id") == confirmed and target_instant is not None:
                if scheduled_instant is not None and scheduled_instant != target_instant:
                    require(False, "confirmed single-slot item conflicts with its existing PublishSchedule", errors)
                creator_locked = (item.get("timing") or {}).get("creator_locked_at")
                if creator_locked is not None:
                    try:
                        require(parse_offset_datetime(creator_locked) == target_instant, "confirmed single-slot item conflicts with creator_locked_at", errors)
                    except (TypeError, ValueError):
                        pass
        if single.get("confirmation_status") == "confirmed":
            require(len(commitments_at_target) <= 1, "confirmed single-slot assignment conflicts with multiple accepted or scheduled commitments", errors)
            require(not commitments_at_target or confirmed in commitments_at_target, "confirmed single-slot item would overwrite another accepted or scheduled commitment", errors)

    return errors, artifacts


def validate_calendar(
    calendar: dict[str, Any], request: dict[str, Any], request_path: Path, run_root: Path,
    calendar_path: Path | None = None,
) -> list[str]:
    errors, artifacts = validate_request(request, request_path, run_root)
    _append_schema_errors(calendar, CALENDAR_SCHEMA, "calendar", errors)
    if errors:
        return errors
    require(calendar.get("request_id") == request.get("request_id"), "calendar request_id mismatch", errors)
    require(calendar.get("created_at") == request.get("created_at"), "calendar created_at must be copied from request", errors)
    require(calendar.get("intent") == request.get("intent"), "calendar intent mismatch", errors)
    require(calendar.get("projection_mode") == ("focus_slot_patch" if request.get("intent") == "choose_for_slot" else "full_calendar"), "calendar projection_mode mismatch", errors)
    require(calendar.get("planning_window", {}).get("timezone") == request.get("planning_window", {}).get("timezone"), "calendar timezone mismatch", errors)

    request_items = {item.get("item_id"): item for item in request.get("items") or []}
    request_created_at = parse_offset_datetime(request["created_at"])
    slot_ids: list[str] = []
    item_ids: list[str] = []
    semantic_keys: list[str] = []
    semantic_key_by_item: dict[str, str] = {}
    forbidden_keys = {"experiment", "trial_reel", "published", "oauth_token", "access_token"}

    def walk_keys(value: Any):
        if isinstance(value, dict):
            for key, child in value.items():
                yield key
                yield from walk_keys(child)
        elif isinstance(value, list):
            for child in value:
                yield from walk_keys(child)

    require(not (forbidden_keys & set(walk_keys(calendar))), "calendar contains an execution or LATER-only field", errors)

    for slot in calendar.get("slots") or []:
        slot_id = slot.get("slot_id")
        item_id = slot.get("item_id")
        slot_ids.append(slot_id)
        item_ids.append(item_id)
        item = request_items.get(item_id)
        require(item is not None, f"{slot_id}: slot item is not in request", errors)
        if item is None:
            continue
        loaded = artifacts.get("items", {}).get(item_id, {})
        selected = loaded.get("selected") or {}
        package = loaded.get("package")
        require(slot.get("selected_idea_ref") == item.get("selected_idea_ref"), f"{slot_id}: SelectedIdea ref changed", errors)
        require(slot.get("script_package_request_ref") == item.get("script_package_request_ref"), f"{slot_id}: ScriptPackageRequest ref changed", errors)
        require(slot.get("script_package_ref") == item.get("script_package_ref"), f"{slot_id}: ScriptPackage ref changed", errors)
        require(slot.get("target") == item.get("target"), f"{slot_id}: target changed", errors)
        require(slot.get("title") == (selected.get("idea") or {}).get("working_title"), f"{slot_id}: title is not the SelectedIdea title", errors)
        require(slot.get("content_preview") == selected_idea_content_preview(selected), f"{slot_id}: content_preview is not the SelectedIdea projection", errors)
        semantic_key = selected_idea_semantic_key(selected)
        semantic_keys.append(semantic_key)
        semantic_key_by_item[item_id] = semantic_key

        progress = item.get("production_progress") or {}
        production = slot.get("production") or {}
        require(production.get("state") == progress.get("state"), f"{slot_id}: production state changed", errors)
        require(production.get("source") == progress.get("source"), f"{slot_id}: production source changed", errors)
        require(production.get("as_of") == progress.get("as_of"), f"{slot_id}: production as_of changed", errors)
        require(production.get("remaining_minutes") == progress.get("remaining_minutes"), f"{slot_id}: production remaining_minutes changed", errors)
        require(production.get("reported_blockers") == progress.get("blockers"), f"{slot_id}: production blockers changed", errors)

        timing = slot.get("timing") or {}
        publish_at = slot.get("publish_at")
        if timing.get("source") == "unassigned":
            require(publish_at is None, f"{slot_id}: unassigned timing must not contain publish_at", errors)
            require(timing.get("confidence") is None, f"{slot_id}: unassigned timing confidence must be null", errors)
        elif timing.get("source") != "engine_recommended":
            require(timing.get("confidence") is None, f"{slot_id}: creator timing confidence must be null", errors)
        if publish_at is None:
            require(not (slot.get("reminders") or []), f"{slot_id}: reminders require an exact publish_at", errors)
        for reminder in slot.get("reminders") or []:
            if reminder.get("type") == "post_publish_attention":
                require(reminder.get("anchor") == "connector_published_at" and reminder.get("at") is None, f"{slot_id}: post-publish reminders must wait for connector_published_at", errors)
            else:
                require(reminder.get("anchor") == "planned_publish_at" and reminder.get("at") is not None, f"{slot_id}: pre-publish reminders must anchor to planned_publish_at", errors)
                if reminder.get("at") is not None:
                    try:
                        require(parse_offset_datetime(reminder["at"]) > request_created_at, f"{slot_id}: stale pre-publish reminder must not be proposed", errors)
                    except (TypeError, ValueError):
                        pass

        prep = slot.get("publish_prep") or {}
        if package is None:
            require(prep.get("status") == "unavailable", f"{slot_id}: missing ScriptPackage must stay unavailable", errors)
            require(prep.get("headline") is None and prep.get("caption_or_description") is None, f"{slot_id}: Calendar invented Packaging content", errors)
        elif package.get("status") != "approved_for_production" or package.get("approval_status") != "approved":
            require(prep.get("status") != "complete", f"{slot_id}: unapproved ScriptPackage cannot produce complete prep", errors)

    require(len(slot_ids) == len(set(slot_ids)), "slot_id values must be unique", errors)
    require(len(item_ids) == len(set(item_ids)), "an item may appear in only one slot", errors)

    summary = calendar.get("summary") or {}
    require(summary.get("planned_slot_count") == len(calendar.get("slots") or []), "summary planned_slot_count mismatch", errors)
    require(summary.get("unfilled_slot_count") == len(calendar.get("unfilled_slots") or []), "summary unfilled_slot_count mismatch", errors)
    require(summary.get("timed_slot_count") == sum(slot.get("publish_at") is not None for slot in calendar.get("slots") or []), "summary timed_slot_count mismatch", errors)
    require(summary.get("prep_complete_count") == sum((slot.get("publish_prep") or {}).get("status") == "complete" for slot in calendar.get("slots") or []), "summary prep_complete_count mismatch", errors)
    require(summary.get("production_complete_count") == sum((slot.get("production") or {}).get("state") == "complete" for slot in calendar.get("slots") or []), "summary production_complete_count mismatch", errors)
    require(summary.get("distinct_content_count") == len(set(semantic_keys)), "summary distinct_content_count mismatch", errors)
    require((calendar.get("interaction") or {}).get("runtime_handoff", {}).get("publish_execution") == "not_performed", "Skill may not claim a publish side effect", errors)
    handoff = (calendar.get("interaction") or {}).get("runtime_handoff", {})
    confirmation = handoff.get("confirmation_scope") or {}
    require(confirmation.get("status") == "not_granted", "Skill may not grant a standing confirmation", errors)
    require(confirmation.get("calendar_id") == calendar.get("calendar_id"), "confirmation scope calendar_id mismatch", errors)
    action_scopes = handoff.get("action_scopes") or []
    actions = [scope.get("action") for scope in action_scopes]
    require(len(actions) == len(set(actions)), "runtime handoff action scopes must have unique actions", errors)
    require(handoff.get("requested_actions") == actions, "requested_actions must exactly match ordered action_scopes", errors)
    known_slot_ids = {slot.get("slot_id") for slot in calendar.get("slots") or []}
    blocked_time_action_items = {
        item_id
        for gap in calendar.get("gaps") or []
        if gap.get("gap_type") in {"constraint_conflict", "near_duplicate_selected_ideas"}
        for item_id in gap.get("affected_refs") or []
    }
    slots = calendar.get("slots") or []
    independently_conflicted_items: set[str] = set()
    constraints = request.get("constraints") or {}
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for slot in slots:
        by_day[str(slot.get("planned_date"))].append(slot)
    for day_slots in by_day.values():
        if len(day_slots) > int(constraints.get("max_posts_per_day", 1)):
            independently_conflicted_items.update(str(slot.get("item_id")) for slot in day_slots)
    minimum_gap = timedelta(hours=int(constraints.get("min_publish_gap_hours", 0)))
    if minimum_gap > timedelta(0):
        for index, left in enumerate(slots):
            if left.get("publish_at") is None:
                continue
            for right in slots[index + 1:]:
                if right.get("publish_at") is None:
                    continue
                try:
                    conflicts = abs(
                        parse_offset_datetime(left["publish_at"])
                        - parse_offset_datetime(right["publish_at"])
                    ) < minimum_gap
                except (TypeError, ValueError):
                    conflicts = False
                if conflicts:
                    independently_conflicted_items.update({str(left.get("item_id")), str(right.get("item_id"))})
    require(
        independently_conflicted_items <= blocked_time_action_items,
        "constraint conflict must block every participant symmetrically",
        errors,
    )
    for slot in slots:
        if str(slot.get("item_id")) in independently_conflicted_items:
            require(
                "resolve_calendar_conflict" in (slot.get("required_actions") or []),
                f"{slot.get('slot_id')}: symmetric conflict participant lacks resolve_calendar_conflict",
                errors,
            )

    semantic_groups: dict[str, list[str]] = defaultdict(list)
    for item_id, key in semantic_key_by_item.items():
        semantic_groups[key].append(item_id)
    independently_repeated_items = {
        item_id
        for group in semantic_groups.values()
        if len(group) > 1 and request.get("intent") != "choose_for_slot"
        for item_id in group
    }
    require(
        independently_repeated_items <= blocked_time_action_items,
        "near-duplicate group must block every participant",
        errors,
    )
    for slot in slots:
        if str(slot.get("item_id")) in blocked_time_action_items:
            require(
                not (slot.get("reminders") or []),
                f"{slot.get('slot_id')}: conflicted or near-duplicate slot must not carry reminder truth",
                errors,
            )

    def time_action_safe(slot: dict[str, Any]) -> bool:
        publish_at = slot.get("publish_at")
        try:
            future_publish = publish_at is not None and parse_offset_datetime(publish_at) > request_created_at
        except (TypeError, ValueError):
            future_publish = False
        return bool(
            future_publish
            and request["planning_window"]["timezone_source"] != "assumed"
            and slot.get("item_id") not in blocked_time_action_items
            and "resolve_calendar_conflict" not in (slot.get("required_actions") or [])
        )

    scoped_slot_ids = sorted({slot_id for scope in action_scopes for slot_id in (scope.get("slot_ids") or [])})
    require(all(set(scope.get("slot_ids") or []) <= known_slot_ids for scope in action_scopes), "runtime handoff action scope contains an unknown slot", errors)
    require(confirmation.get("slot_ids") == scoped_slot_ids, "confirmation scope slot_ids mismatch", errors)
    if request.get("intent") == "choose_for_slot":
        confirmed = ((request.get("single_slot_decision") or {}).get("confirmation_status") == "confirmed")
        if not confirmed:
            require(not action_scopes, "single-slot recommendation cannot request calendar or publish side effects before Creator confirmation", errors)
        elif calendar.get("status") != "ready_for_review":
            require(not action_scopes, "blocked confirmed single-slot assignment cannot request Runtime handoffs", errors)
        else:
            require("upsert_calendar_slot" in actions and "save_calendar" not in actions, "confirmed single-slot assignment must use scoped upsert, never replace the full calendar", errors)
    for scope in action_scopes:
        if scope.get("action") == "create_pre_publish_reminders":
            eligible = {
                slot.get("slot_id") for slot in calendar.get("slots") or []
                if time_action_safe(slot)
                and any(reminder.get("type") == "pre_publish" and reminder.get("at") is not None for reminder in slot.get("reminders") or [])
            }
            require(set(scope.get("slot_ids") or []) <= eligible, "pre-publish reminder scope contains a stale, conflicted or otherwise ineligible slot", errors)
        if scope.get("action") == "activate_post_publish_reminders_after_receipt":
            eligible = {
                slot.get("slot_id") for slot in calendar.get("slots") or []
                if time_action_safe(slot)
                and any(reminder.get("type") == "post_publish_attention" and reminder.get("anchor") == "connector_published_at" and reminder.get("at") is None for reminder in slot.get("reminders") or [])
            }
            require(set(scope.get("slot_ids") or []) <= eligible, "post-publish reminder scope contains a stale, conflicted or otherwise ineligible slot", errors)
        if scope.get("action") == "prepare_publish_handoff":
            eligible = {
                slot.get("slot_id") for slot in calendar.get("slots") or []
                if time_action_safe(slot)
                and (slot.get("publish_prep") or {}).get("status") == "complete"
                and (slot.get("production") or {}).get("state") == "complete"
                and (slot.get("timing") or {}).get("schedule_state") != "scheduled"
                and (slot.get("production") or {}).get("due_status") in {"on_time", "not_applicable"}
                and (slot.get("production") or {}).get("capacity_status") == "within"
                and not ((slot.get("production") or {}).get("reported_blockers") or [])
                and not ((slot.get("production") or {}).get("planning_blockers") or [])
            }
            require(set(scope.get("slot_ids") or []) <= eligible, "prepare_publish_handoff scope contains a stale, conflicted, production-blocked or already scheduled slot", errors)
    if calendar.get("slots") and all((slot.get("timing") or {}).get("schedule_state") == "scheduled" for slot in calendar.get("slots") or []):
        require("prepare_publish_handoff" not in (handoff.get("requested_actions") or []), "already scheduled slots must not be prepared for scheduling again", errors)
    try:
        from build_content_calendar import build as build_expected_calendar
        expected = build_expected_calendar(request, request_path, run_root)
        actual_core = copy.deepcopy(calendar)
        actual_core["exports"] = []
        require(actual_core == expected, "calendar body differs from the deterministic Builder result", errors)
    except (OSError, ValueError) as error:
        errors.append(f"deterministic calendar replay failed: {type(error).__name__}: {error}")
    exports = calendar.get("exports") or []
    if exports:
        require(len(exports) == 2 and sorted(export.get("format") for export in exports) == ["csv", "ics"], "exports must contain exactly one CSV and one ICS projection", errors)
    # `path` 下面是 `.resolve()` 过的,所以越界判定的另一端也必须是解析态:macOS 上 `/var` 是
    # `/private/var` 的符号链接,调用方传进来的临时目录是 `/var/...`,而 `path` 是
    # `/private/var/...`,两端形态不一致会让一条完全合法的导出被判成
    # `export path escapes run root`。生产入口(main)本来就 `.resolve()` 过 run_root,所以线上
    # 不触发;直接调 `validate_calendar()` 的调用方(测试、生成脚本)传原始临时目录时才会炸。
    # 在循环外解析一次,与 calendar_common.py 的 safe_artifact_path / safe_output_path 同一约定。
    resolved_run_root = run_root.resolve()
    for export in exports:
        try:
            relative = Path(export.get("path", ""))
            require(not relative.is_absolute(), "export path must be run-relative", errors)
            path = (run_root / relative).resolve()
            require(
                path == resolved_run_root or resolved_run_root in path.parents,
                f"export path escapes run root: {relative}",
                errors,
            )
        except (TypeError, ValueError) as error:
            errors.append(f"invalid export path: {error}")
            continue
        if not path.is_file():
            errors.append(f"export file not found: {relative}")
            continue
        require(export.get("sha256") == file_sha256(path), f"export sha256 mismatch: {relative}", errors)
        from calendar_projection import csv_bytes, ics_bytes
        expected_bytes = csv_bytes(calendar) if export.get("format") == "csv" else ics_bytes(calendar)
        require(path.read_bytes() == expected_bytes, f"export bytes differ from deterministic {export.get('format')} projection: {relative}", errors)
        expected_count = len(calendar.get("slots") or [])
        require(export.get("record_count") == expected_count, f"export record_count mismatch: {relative}", errors)
    return errors


def _print_errors(errors: list[str]) -> int:
    if not errors:
        return 0
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    request_parser = sub.add_parser("request")
    request_parser.add_argument("request", type=Path)
    request_parser.add_argument("--run-root", type=Path, default=None)
    calendar_parser = sub.add_parser("calendar")
    calendar_parser.add_argument("calendar", type=Path)
    calendar_parser.add_argument("request", type=Path)
    calendar_parser.add_argument("--run-root", type=Path, default=None)
    args = parser.parse_args()

    if args.command == "request":
        request_path = args.request.resolve()
        errors, _ = validate_request(load_json(request_path), request_path, (args.run_root or request_path.parent).resolve())
        code = _print_errors(errors)
        if code == 0:
            print("VALID request")
        return code

    request_path = args.request.resolve()
    errors = validate_calendar(
        load_json(args.calendar.resolve()),
        load_json(request_path),
        request_path,
        (args.run_root or request_path.parent).resolve(),
        args.calendar.resolve(),
    )
    code = _print_errors(errors)
    if code == 0:
        print("VALID calendar")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
