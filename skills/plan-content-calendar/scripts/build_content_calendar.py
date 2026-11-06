#!/usr/bin/env python3
"""Build a deterministic four-week ContentCalendar from validated artifacts."""

from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

# Sibling imports must not leave a __pycache__ in the signed, checksummed skill directory.
sys.dont_write_bytecode = True
from calendar_common import (
    canonical_json,
    planning_end,
    get_zone,
    load_json,
    parse_offset_datetime,
    recommendation_contains_instant,
    request_input_paths,
    safe_artifact_path,
    safe_output_path,
    selected_idea_content_preview,
    selected_idea_semantic_key,
    write_new_bytes,
)
from validate_content_calendar import validate_calendar, validate_request


def skill_version() -> str:
    """The Skill's business version, from SKILL.md frontmatter `metadata.version` (no manifest ships)."""
    text = (Path(__file__).resolve().parents[1] / "SKILL.md").read_text(encoding="utf-8")
    match = re.search(r'^\s+version:\s*"?([^"\s]+)"?\s*$', text.split("\n---", 1)[0], re.MULTILINE)
    if match is None:
        raise ValueError("SKILL.md frontmatter has no metadata.version")
    return match.group(1)


def _local_datetime(day: date, hhmm: str, zone) -> datetime | None:
    """Materialize an unambiguous, existent wall time; otherwise keep timing unassigned."""

    wall = time.fromisoformat(hhmm)
    candidates: list[datetime] = []
    for fold in (0, 1):
        candidate = datetime.combine(day, wall).replace(tzinfo=zone, fold=fold)
        roundtrip = candidate.astimezone(timezone.utc).astimezone(zone)
        if roundtrip.replace(tzinfo=None) == datetime.combine(day, wall):
            candidates.append(candidate)
    unique_offsets = {candidate.utcoffset() for candidate in candidates}
    if len(unique_offsets) != 1:
        return None
    return candidates[0] if candidates else None


def _shift_in_zone(value: datetime, delta: timedelta, zone) -> datetime:
    """Shift an instant, then re-materialize it in the Calendar zone across DST."""

    return (value.astimezone(timezone.utc) + delta).astimezone(zone)


def _creator_time_source(recommendation: dict[str, Any] | None, instant: datetime) -> str:
    if recommendation and recommendation.get("outcome") == "recommended":
        if recommendation_contains_instant(recommendation, instant):
            return "creator_choice_within_recommendation"
        return "creator_override_of_recommendation"
    return "creator_choice_not_recommended"


def _target_matches(rule: dict[str, Any], target: dict[str, Any]) -> bool:
    return (
        rule.get("account_ref") == target.get("account_ref")
        and rule.get("platform") == target.get("platform")
        and (rule.get("publish_format") is None or rule.get("publish_format") == target.get("publish_format"))
        and (rule.get("production_format") is None or rule.get("production_format") == target.get("production_format"))
    )


def _target_slots(request: dict[str, Any]) -> list[dict[str, Any]]:
    """Materialize cadence dates inside the inclusive planning window."""
    start = date.fromisoformat(request["planning_window"]["start_date"])
    slots: list[dict[str, Any]] = []
    sequence = 0
    for week_index in range(1, request["planning_window"]["weeks"] + 1):
        week_start = start + timedelta(days=(week_index - 1) * 7)
        for rule_index, rule in enumerate(request["cadence_rules"]):
            weekdays = rule["preferred_weekdays"][: rule["posts_per_week"]]
            times = rule["preferred_local_times"]
            for position, weekday in enumerate(weekdays):
                sequence += 1
                offset = (weekday - week_start.isoweekday()) % 7
                planned_date = week_start + timedelta(days=offset)
                if planned_date > planning_end(request["planning_window"]):
                    continue
                slots.append({
                    "sequence": sequence,
                    "week_index": week_index,
                    "planned_date": planned_date,
                    "rule_index": rule_index,
                    "rule": rule,
                    "preferred_time": times[position % len(times)] if times else None,
                })
    return sorted(slots, key=lambda value: (value["planned_date"], value["rule_index"], value["sequence"]))


def _project_prep(
    item: dict[str, Any],
    package: dict[str, Any] | None,
    source_script_package: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    assets = item.get("publication_assets") or {}
    empty = {
        "headline": None,
        "caption_or_description": None,
        "hashtags_or_tags": [],
        "cover_brief_ref": None,
        "media_refs": list(assets.get("media_refs") or []),
        "cover_asset_ref": assets.get("cover_ref"),
    }
    if package is None:
        return {"status": "unavailable", **empty}, ["create_script_package"]

    packaging = package.get("packaging")
    actions: list[str] = []
    if package.get("status") != "approved_for_production" or package.get("approval_status") != "approved":
        actions.append("approve_script_package")
    if not isinstance(packaging, dict):
        actions.append("complete_packaging")
        return {"status": "partial", **empty}, actions

    headlines = {value.get("headline_id"): value for value in packaging.get("headline_options") or []}
    recommended = headlines.get(packaging.get("recommended_headline_id")) or {}
    headline = recommended.get("text")
    caption = packaging.get("caption_or_description")
    tags = list(packaging.get("hashtags_or_tags") or [])
    cover = packaging.get("cover_or_thumbnail")
    if not headline:
        actions.append("complete_packaging")
    if not caption:
        actions.append("complete_packaging")
    if not cover:
        actions.append("complete_packaging")
    effective_script = package.get("script") or (source_script_package or {}).get("script")
    if isinstance(effective_script, dict) and any(
        scene.get("asset_status") != "available" for scene in effective_script.get("scenes") or []
    ):
        actions.append("resolve_script_asset_placeholders")
    if (package.get("production_plan") or {}).get("missing_assets"):
        actions.append("resolve_missing_production_assets")
    if any(claim.get("status") in {"placeholder", "prohibited"} for claim in package.get("claim_ledger") or []):
        actions.append("resolve_claim_placeholders")
    media_refs = assets.get("media_refs") or []
    if assets.get("status") != "confirmed" or not media_refs:
        actions.append("attach_final_media")
    target = item.get("target") or {}
    publish_format = "".join(character for character in str(target.get("publish_format") or "").lower() if character.isalnum())
    youtube_video = target.get("platform") == "youtube" and target.get("publish_format") in {"youtube_long", "youtube_short"}
    if target.get("idea_format") == "carousel" or any(token in publish_format for token in ("carousel", "album", "multiimage")):
        media_family = "carousel"
    elif youtube_video or any(token in publish_format for token in ("reel", "short", "video", "tiktok")):
        media_family = "video"
    elif any(token in publish_format for token in ("image", "photo", "picture", "pin")):
        media_family = "image"
    elif any(token in publish_format for token in ("audio", "podcast")):
        media_family = "audio"
    else:
        media_family = "unknown"
    if media_family == "carousel" and len([ref for ref in media_refs if ref.get("media_type") in {"image", "video"}]) < 2:
        actions.append("attach_carousel_media_set")
    elif media_family == "video" and not any(ref.get("media_type") == "video" for ref in media_refs):
        actions.append("attach_final_video")
    elif media_family == "image" and not any(ref.get("media_type") == "image" for ref in media_refs):
        actions.append("attach_final_image")
    elif media_family == "audio" and not any(ref.get("media_type") == "audio" for ref in media_refs):
        actions.append("attach_final_audio")
    elif media_family == "unknown":
        actions.append("confirm_platform_media_requirements")
    cover_required = target.get("platform") == "youtube"
    if cover_required and (assets.get("status") != "confirmed" or not assets.get("cover_ref")):
        actions.append("attach_final_cover")
    return ({
        "status": "complete" if not actions else "partial",
        "headline": headline,
        "caption_or_description": caption,
        "hashtags_or_tags": tags,
        "cover_brief_ref": f"{package.get('package_id')}#packaging.cover_or_thumbnail" if cover else None,
        "media_refs": list(assets.get("media_refs") or []),
        "cover_asset_ref": assets.get("cover_ref"),
    }, sorted(set(actions)))


def _exact_timing(
    item: dict[str, Any],
    loaded: dict[str, Any],
    creator_decision_at: datetime | None = None,
) -> tuple[datetime | None, str, dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None, bool, str | None]:
    timing = item.get("timing") or {}
    recommendation = loaded.get("recommendation")
    schedule = loaded.get("schedule")
    creator_locked = timing.get("creator_locked_at")
    schedule_at = parse_offset_datetime(schedule["scheduled_at"]) if schedule else None
    request_ref = timing.get("publish_request_ref")
    history_ref = timing.get("publish_history_ref")
    recommendation_ref = timing.get("publish_recommendation_ref")
    schedule_ref = timing.get("publish_schedule_ref")

    if creator_decision_at is not None:
        if schedule_at == creator_decision_at and schedule is not None and schedule.get("state") in {"accepted", "scheduled"}:
            return (
                creator_decision_at,
                schedule.get("source"),
                request_ref,
                history_ref,
                recommendation_ref,
                schedule_ref,
                True,
                schedule.get("state"),
            )
        source = _creator_time_source(recommendation, creator_decision_at)
        return creator_decision_at, source, request_ref, history_ref, recommendation_ref, schedule_ref, False, (schedule or {}).get("state")

    if creator_locked is not None:
        chosen = parse_offset_datetime(creator_locked)
        if schedule_at == chosen:
            return (
                chosen,
                schedule.get("source"),
                request_ref,
                history_ref,
                recommendation_ref,
                schedule_ref,
                True,
                schedule.get("state"),
            )
        source = _creator_time_source(recommendation, chosen)
        return chosen, source, request_ref, history_ref, recommendation_ref, schedule_ref, True, (schedule or {}).get("state")
    if schedule is not None:
        return (
            schedule_at,
            schedule.get("source"),
            request_ref,
            history_ref,
            recommendation_ref,
            schedule_ref,
            schedule.get("state") in {"accepted", "scheduled"},
            schedule.get("state"),
        )
    return None, "unassigned", request_ref, history_ref, recommendation_ref, None, False, None


def _reminders(publish_at: datetime | None, request: dict[str, Any], zone) -> list[dict[str, Any]]:
    if publish_at is None:
        return []
    values: list[dict[str, Any]] = []
    preferences = request.get("reminder_preferences") or {}
    created_at = parse_offset_datetime(request["created_at"])
    for minutes in sorted(preferences.get("pre_publish_minutes") or [], reverse=True):
        reminder_at = _shift_in_zone(publish_at, -timedelta(minutes=minutes), zone)
        # Never hand Runtime a reminder whose trigger was already in the past
        # when this immutable request was created.  A later offset may still be
        # viable, so filter each reminder independently.
        if reminder_at <= created_at:
            continue
        values.append({"type": "pre_publish", "anchor": "planned_publish_at", "offset_minutes": -minutes, "at": reminder_at.isoformat(), "state": "proposed"})
    for minutes in sorted(preferences.get("post_publish_attention_minutes") or []):
        values.append({"type": "post_publish_attention", "anchor": "connector_published_at", "offset_minutes": minutes, "at": None, "state": "proposed"})
    return values


def _time_actions_safe(
    slot: dict[str, Any],
    request: dict[str, Any],
    blocked_item_ids: set[str],
) -> bool:
    """Return whether reminder/publish handoffs are safe for one slot."""

    publish_at = slot.get("publish_at")
    return bool(
        publish_at
        and parse_offset_datetime(publish_at) > parse_offset_datetime(request["created_at"])
        and request["planning_window"]["timezone_source"] != "assumed"
        and slot.get("item_id") not in blocked_item_ids
        and "resolve_calendar_conflict" not in (slot.get("required_actions") or [])
    )


def _slot_decision(request: dict[str, Any], artifacts: dict[str, Any]) -> dict[str, Any] | None:
    query = request.get("single_slot_decision")
    if not isinstance(query, dict):
        return None
    target_at = parse_offset_datetime(query["target_publish_at"])
    evaluations: list[dict[str, Any]] = []
    item_map = {item["item_id"]: item for item in request.get("items") or []}
    for item_id in query["candidate_item_ids"]:
        item = item_map[item_id]
        loaded = artifacts["items"][item_id]
        package = loaded.get("package")
        exact, _, _, _, _, _, locked, schedule_state = _exact_timing(item, loaded)
        reason = "eligible"
        eligible = True
        if item.get("target") != query.get("target"):
            eligible, reason = False, "target_mismatch"
        elif exact is not None and exact != target_at and schedule_state in {"accepted", "scheduled"}:
            eligible, reason = False, "immutable_schedule_conflict"
        elif exact is not None and exact != target_at and schedule_state == "proposed":
            eligible, reason = False, "proposed_schedule_conflict"
        elif exact is not None and exact != target_at and locked:
            eligible, reason = False, "creator_locked_time_conflict"
        prep, actions = _project_prep(item, package, loaded.get("source_script_package"))
        ready = (
            eligible
            and prep["status"] == "complete"
            and (item.get("production_progress") or {}).get("state") == "complete"
        )
        if eligible and not ready:
            reason = "publish_prep_incomplete"
        evaluations.append({
            "item_id": item_id,
            "eligible": eligible,
            "operationally_ready": ready,
            "existing_commitment_at_target": (
                eligible
                and exact == target_at
                and (locked or schedule_state in {"accepted", "scheduled"})
            ),
            "reason": reason,
        })

    commitments = [value["item_id"] for value in evaluations if value["existing_commitment_at_target"]]
    eligible = [value["item_id"] for value in evaluations if value["eligible"]]
    ready = [value["item_id"] for value in evaluations if value["operationally_ready"]]
    preferred = query.get("creator_preferred_item_id")
    selected: str | None = None
    outcome = "no_eligible_item"
    basis = "none"
    rationale = "No candidate can occupy the requested slot without changing a locked commitment or target."
    confirmation_status = query.get("confirmation_status")
    confirmed_item_id = query.get("confirmed_item_id")
    if confirmation_status == "confirmed":
        confirmed_evaluation = next((value for value in evaluations if value["item_id"] == confirmed_item_id), None)
        if len(commitments) > 1 or (commitments and confirmed_item_id not in commitments):
            raise ValueError("confirmed single-slot item conflicts with an existing accepted, scheduled, or Creator-locked commitment")
        if confirmed_evaluation is None or not confirmed_evaluation["eligible"]:
            raise ValueError("confirmed single-slot item is not eligible at the requested target and time")
        selected, outcome, basis = confirmed_item_id, "confirmed_slot_assignment", "creator_confirmation"
        rationale = "The Creator confirmed this selected item for the exact target time; external calendar, reminder and publish writes still require Runtime confirmation."
    elif len(commitments) == 1:
        selected, outcome, basis = commitments[0], "selected_existing_commitment", "existing_commitment"
        rationale = "One candidate already has an accepted, scheduled, or Creator-locked commitment at this exact time."
    elif len(commitments) > 1:
        outcome = "conflicting_existing_commitments"
        rationale = "More than one accepted, scheduled, or Creator-locked commitment already occupies this exact time; resolve that conflict before choosing content."
    elif preferred is not None and preferred in eligible:
        selected, outcome, basis = preferred, "selected_creator_preference", "creator_preference"
        rationale = "The creator explicitly preferred this already-selected item for the requested slot."
    elif len(ready) == 1:
        selected, outcome, basis = ready[0], "selected_unique_ready", "unique_operational_readiness"
        rationale = "Exactly one eligible item has approved Packaging and verified final publication assets."
    elif len(ready) > 1:
        ready_items = [item_map[item_id] for item_id in ready]
        best_priority = min(item["priority"] for item in ready_items)
        best = [item for item in ready_items if item["priority"] == best_priority]
        if len(best) == 1:
            selected = best[0]["item_id"]
            if best[0]["priority_source"] == "creator":
                outcome, basis = "selected_creator_priority", "creator_priority"
                rationale = "The Creator's explicit priority uniquely ranks this operationally ready item first."
            else:
                outcome, basis = "selected_upstream_priority", "upstream_priority"
                rationale = "The reviewed upstream SelectedIdea order uniquely ranks this operationally ready item first; Creator confirmation is still required."
        else:
            outcome = "needs_creator_choice"
            rationale = "More than one operationally ready item shares the best trusted priority; the Calendar will not break the tie silently."
    elif eligible:
        outcome = "needs_creator_choice"
        rationale = "More than one selected item remains eligible and the evidence does not justify choosing on the creator's behalf."
    return {
        "decision_id": query["decision_id"],
        "idempotency_key": query["idempotency_key"],
        "target_publish_at": query["target_publish_at"],
        "target": query["target"],
        "candidate_item_ids": list(query["candidate_item_ids"]),
        "candidate_evaluations": evaluations,
        "selected_item_id": selected,
        "outcome": outcome,
        "selection_basis": basis,
        "confirmation_status": confirmation_status,
        "confirmed_at": query.get("confirmed_at"),
        "rationale": rationale,
        "requires_creator_confirmation": confirmation_status != "confirmed",
    }


def _constraint_conflicting_slots(
    planned_date: date,
    exact: datetime | None,
    assigned: list[dict[str, Any]],
    constraints: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return every already-assigned slot that conflicts with this placement.

    A conflict is symmetric.  The caller must block both the new slot and every
    returned slot rather than letting input order silently choose a winner.
    """

    conflicts: dict[str, dict[str, Any]] = {}
    same_day = [slot for slot in assigned if slot["planned_date"] == planned_date.isoformat()]
    if len(same_day) >= constraints["max_posts_per_day"]:
        conflicts.update((slot["slot_id"], slot) for slot in same_day)
    if exact is not None and constraints["min_publish_gap_hours"] > 0:
        minimum = timedelta(hours=constraints["min_publish_gap_hours"])
        for slot in assigned:
            if slot.get("publish_at") is None:
                continue
            if abs(parse_offset_datetime(slot["publish_at"]) - exact) < minimum:
                conflicts[slot["slot_id"]] = slot
    return list(conflicts.values())


def _gap(gap_type: str, refs: list[str], detail: str, count: int | None = None) -> dict[str, Any]:
    return {
        "gap_type": gap_type,
        "count": count if count is not None else max(1, len(refs)),
        "affected_refs": refs,
        "detail": detail,
    }


def build(request: dict[str, Any], request_path: Path, run_root: Path) -> dict[str, Any]:
    """Build a deterministic calendar from validated requests and immutable artifacts."""
    errors, artifacts = validate_request(request, request_path, run_root)
    if errors:
        raise ValueError("invalid request:\n- " + "\n- ".join(errors))

    zone = get_zone(request["planning_window"]["timezone"])
    start = date.fromisoformat(request["planning_window"]["start_date"])
    end = planning_end(request["planning_window"])
    slot_decision = _slot_decision(request, artifacts)
    choose_mode = request.get("intent") == "choose_for_slot"
    choose_confirmed = choose_mode and (request.get("single_slot_decision") or {}).get("confirmation_status") == "confirmed"
    target_slots = [] if choose_mode else _target_slots(request)
    blackout = set((request.get("capacity") or {}).get("blackout_dates") or [])
    used: set[int] = set()
    assigned: list[dict[str, Any]] = []
    unscheduled: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    constraint_refs: set[str] = set()
    overdue_refs: set[str] = set()

    ordered_items = []
    for item in request.get("items") or []:
        if choose_mode and (slot_decision is None or item["item_id"] != slot_decision.get("selected_item_id")):
            continue
        loaded = artifacts["items"][item["item_id"]]
        decision_at = parse_offset_datetime(slot_decision["target_publish_at"]) if choose_mode and slot_decision else None
        exact, source, publish_request_ref, history_ref, recommendation_ref, schedule_ref, locked, schedule_state = _exact_timing(item, loaded, decision_at)
        if choose_confirmed:
            locked = True
        ordered_items.append((0 if exact is not None else 1, item["priority"], item["item_id"], item, loaded, exact, source, publish_request_ref, history_ref, recommendation_ref, schedule_ref, locked, schedule_state))

    for _, _, _, item, loaded, exact, source, publish_request_ref, history_ref, recommendation_ref, schedule_ref, locked, schedule_state in sorted(ordered_items, key=lambda value: value[:3]):
        target = item["target"]
        target_slot: dict[str, Any] | None = None
        locked_conflicts: list[str] = []
        if exact is not None:
            planned = exact.astimezone(zone).date()
            if planned < start or planned > end:
                unscheduled.append({"entry_id": f"uns-{item['item_id']}", "item_id": item["item_id"], "candidate_idea_set_ref": None, "existing_commitment_at": exact.isoformat(), "existing_schedule_ref": schedule_ref, "existing_schedule_state": schedule_state, "reason": "locked_time_outside_window", "next_action": "Keep the commitment unchanged and open a Calendar window that contains it; this Skill will not move it."})
                continue
            week_index = ((planned - start).days // 7) + 1
            if choose_mode:
                target_slot = {"sequence": 0, "week_index": week_index, "planned_date": planned, "rule_index": -1, "rule": {}, "preferred_time": None, "synthetic": True}
            elif locked:
                same_week = [slot for slot in target_slots if slot["sequence"] not in used and slot["week_index"] == week_index and _target_matches(slot["rule"], target)]
                target_slot = same_week[0] if same_week else {"sequence": 0, "week_index": week_index, "planned_date": planned, "rule_index": -1, "rule": {}, "preferred_time": None, "synthetic": True}
                if target_slot.get("synthetic"):
                    locked_conflicts.append("no_matching_cadence")
                elif target_slot["planned_date"] != planned:
                    locked_conflicts.append("cadence_day_mismatch")
                target_slot = {**target_slot, "planned_date": planned}
            else:
                matching = [slot for slot in target_slots if slot["sequence"] not in used and slot["planned_date"] == planned and _target_matches(slot["rule"], target)]
                target_slot = matching[0] if matching else None
        else:
            matching = [slot for slot in target_slots if slot["sequence"] not in used and _target_matches(slot["rule"], target) and slot["planned_date"].isoformat() not in blackout]
            target_slot = matching[0] if matching else None

        if target_slot is None:
            has_rule = any(_target_matches(slot["rule"], target) for slot in target_slots)
            reason = "locked_time_conflict" if exact is not None else ("no_available_slot" if has_rule else "no_matching_cadence")
            unscheduled.append({"entry_id": f"uns-{item['item_id']}", "item_id": item["item_id"], "candidate_idea_set_ref": None, "existing_commitment_at": exact.isoformat() if exact is not None else None, "existing_schedule_ref": schedule_ref, "existing_schedule_state": schedule_state, "reason": reason, "next_action": "Resolve the cadence or choose another slot; the Calendar will not move a locked time or create cadence."})
            continue

        planned_date = target_slot["planned_date"]
        if planned_date.isoformat() in blackout:
            if locked:
                locked_conflicts.append("blackout_date")
            else:
                unscheduled.append({"entry_id": f"uns-{item['item_id']}", "item_id": item["item_id"], "candidate_idea_set_ref": None, "existing_commitment_at": exact.isoformat() if exact is not None else None, "existing_schedule_ref": schedule_ref, "existing_schedule_state": schedule_state, "reason": "blackout_date", "next_action": "Remove the blackout or choose another creator-confirmed slot."})
                continue
        publish_at = exact
        if (
            publish_at is None
            and target_slot.get("preferred_time")
            and request["planning_window"]["timezone_source"] != "assumed"
        ):
            publish_at = _local_datetime(planned_date, target_slot["preferred_time"], zone)
            if publish_at is not None:
                source = "creator_choice_not_recommended"
        conflicting_slots = _constraint_conflicting_slots(
            planned_date,
            publish_at,
            assigned,
            request["constraints"],
        )
        if conflicting_slots:
            if locked:
                locked_conflicts.append("spacing_or_daily_limit")
                # A locked/accepted collision is symmetric.  Mark every prior
                # participant now so input order or priority cannot silently
                # select the first item as the safe winner.
                for existing in conflicting_slots:
                    existing["required_actions"] = sorted(set(
                        (existing.get("required_actions") or []) + ["resolve_calendar_conflict"]
                    ))
                    existing["reminders"] = []
                    constraint_refs.add(existing["item_id"])
            else:
                unscheduled.append({"entry_id": f"uns-{item['item_id']}", "item_id": item["item_id"], "candidate_idea_set_ref": None, "existing_commitment_at": exact.isoformat() if exact is not None else None, "existing_schedule_ref": schedule_ref, "existing_schedule_state": schedule_state, "reason": "locked_time_conflict" if exact is not None else "no_available_slot", "next_action": "Resolve max-posts-per-day or minimum-gap constraints."})
                continue

        if target_slot["sequence"]:
            used.add(target_slot["sequence"])
        selected = loaded["selected"]
        package = loaded.get("package")
        prep, required_actions = _project_prep(item, package, loaded.get("source_script_package"))
        if publish_at is None:
            required_actions.append("confirm_publish_time")
        if locked_conflicts:
            required_actions.append("resolve_calendar_conflict")
            constraint_refs.add(item["item_id"])

        progress = item["production_progress"]
        production_state = progress["state"]
        remaining_minutes = progress["remaining_minutes"]
        due_at = None
        due_status = "not_applicable" if production_state == "complete" else "unknown"
        if publish_at is not None and production_state in {"not_started", "in_progress", "blocked"}:
            due_at = _shift_in_zone(publish_at, -timedelta(hours=request["constraints"]["production_lead_hours"]), zone).isoformat()
            due_status = "past_due" if parse_offset_datetime(due_at) < parse_offset_datetime(request["created_at"]) else "on_time"
            if due_status == "past_due":
                required_actions.append("resolve_past_due_production")
                overdue_refs.add(item["item_id"])
        recommendation = loaded.get("recommendation") or {}
        recommendation_body = recommendation.get("recommendation") or {}
        schedule = loaded.get("schedule") or {}
        slot = {
            "slot_id": f"slot-focus-{item['item_id']}" if choose_mode else (f"slot-commitment-{item['item_id']}" if target_slot.get("synthetic") else f"slot-{target_slot['week_index']}-{target_slot['sequence']}"),
            "week_index": target_slot["week_index"],
            "item_id": item["item_id"],
            "title": selected["idea"]["working_title"],
            "content_preview": selected_idea_content_preview(selected),
            "planning_state": "locked" if locked else "proposed",
            "planned_date": planned_date.isoformat(),
            "publish_at": publish_at.isoformat() if publish_at is not None else None,
            "target": target,
            "selected_idea_ref": item["selected_idea_ref"],
            "script_package_request_ref": item.get("script_package_request_ref"),
            "script_package_ref": item.get("script_package_ref"),
            "production": {
                "state": production_state,
                "source": progress["source"],
                "as_of": progress["as_of"],
                "remaining_minutes": remaining_minutes,
                "reported_blockers": list(progress["blockers"]),
                "due_at": due_at,
                "due_status": due_status,
                "capacity_status": "unknown",
                "planning_blockers": [],
            },
            "timing": {
                "source": source if publish_at is not None else "unassigned",
                "publish_request_ref": publish_request_ref,
                "publish_history_ref": history_ref,
                "recommendation_ref": recommendation_ref,
                "recommendation_outcome": recommendation.get("outcome"),
                "recommendation_confidence": recommendation_body.get("confidence"),
                "publish_now": recommendation_body.get("publish_now"),
                "schedule_ref": schedule_ref,
                "schedule_state": schedule_state,
                "confidence": schedule.get("confidence") if publish_at is not None and source == "engine_recommended" else None,
            },
            "publish_prep": prep,
            "reminders": [] if locked_conflicts else _reminders(publish_at, request, zone),
            "required_actions": sorted(set(required_actions)),
        }
        assigned.append(slot)

    for ref in request.get("candidate_idea_set_refs") or []:
        unscheduled.append({"entry_id": f"uns-candidate-{ref['artifact_id']}", "item_id": None, "candidate_idea_set_ref": ref, "existing_commitment_at": None, "existing_schedule_ref": None, "existing_schedule_state": None, "reason": "candidate_requires_selection", "next_action": "Ask the creator to select or edit an Idea; never promote a candidate silently."})

    capacity = request["capacity"]
    capacity_overrides = {value["week_index"]: value["minutes"] for value in capacity.get("week_overrides") or []}
    week_totals: dict[int, int] = defaultdict(int)
    week_unknown: set[int] = set()
    for slot in assigned:
        if slot["production"]["state"] == "complete":
            continue
        remaining = slot["production"]["remaining_minutes"]
        if remaining is None:
            week_unknown.add(slot["week_index"])
        else:
            week_totals[slot["week_index"]] += remaining
    any_exceeded = False
    any_unknown = capacity["status"] == "unavailable" and not assigned
    for slot in assigned:
        week_index = slot["week_index"]
        available = capacity_overrides.get(week_index, capacity.get("weekly_minutes"))
        if slot["production"]["state"] == "complete":
            slot["production"]["capacity_status"] = "within"
        elif capacity["status"] == "unavailable" or week_index in week_unknown or available is None:
            slot["production"]["capacity_status"] = "unknown"
            slot["production"]["planning_blockers"].append("production_capacity_unknown")
            any_unknown = True
        elif week_totals[week_index] > available:
            slot["production"]["capacity_status"] = "exceeded"
            slot["production"]["planning_blockers"].append(f"week_{week_index}_needs_{week_totals[week_index]}m_but_has_{available}m")
            any_exceeded = True
        else:
            slot["production"]["capacity_status"] = "within"

    unfilled_slots: list[dict[str, Any]] = []
    if choose_mode:
        if slot_decision is None or slot_decision.get("selected_item_id") is None:
            focus_target = (request.get("single_slot_decision") or {}).get("target") or {}
            focus_at = parse_offset_datetime((request.get("single_slot_decision") or {}).get("target_publish_at"))
            unfilled_slots.append({
                "slot_id": "empty-focus",
                "week_index": ((focus_at.astimezone(zone).date() - start).days // 7) + 1,
                "planned_date": focus_at.astimezone(zone).date().isoformat(),
                "target": {key: focus_target.get(key) for key in ("account_ref", "platform", "production_format", "publish_format")},
                "reason": "single_slot_choice_required",
            })
    else:
        for target_slot in target_slots:
            if target_slot["sequence"] in used:
                continue
            rule = target_slot["rule"]
            planned_date = target_slot["planned_date"].isoformat()
            unfilled_slots.append({
                "slot_id": f"empty-{target_slot['week_index']}-{target_slot['sequence']}",
                "week_index": target_slot["week_index"],
                "planned_date": planned_date,
                "target": {key: rule.get(key) for key in ("account_ref", "platform", "production_format", "publish_format")},
                "reason": "blackout_date" if planned_date in blackout else "no_assigned_content",
            })

    target_count = 1 if choose_mode else len(target_slots)
    planned_count = len(assigned)
    unfilled = len(unfilled_slots)
    selection_shortfall = 0 if choose_mode else max(0, target_count - len(request.get("items") or []))
    if selection_shortfall:
        gaps.append(_gap("selection_shortfall", [], f"The requested cadence needs {target_count} selected items but the request contains {len(request.get('items') or [])}.", selection_shortfall))
    semantic_groups: dict[str, list[str]] = defaultdict(list)
    for slot in assigned:
        selected = artifacts["items"][slot["item_id"]]["selected"]
        semantic_groups[selected_idea_semantic_key(selected)].append(slot["item_id"])
    repeated_groups = [sorted(group) for group in semantic_groups.values() if len(group) > 1] if not choose_mode else []
    repeated_groups.sort()
    repeated_refs: list[str] = []
    if repeated_groups:
        repeated_refs = sorted({item_id for group in repeated_groups for item_id in group})
        gaps.append(
            _gap(
                "near_duplicate_selected_ideas",
                repeated_refs,
                "Selected Ideas repeat the same title, promise, angle, Hook and outline after numbering is removed. Confirm that the repetition is intentional or replace the repeated items before treating this as a varied calendar plan.",
                len(repeated_refs),
            )
        )
    package_gap_refs = [slot["item_id"] for slot in assigned if slot["publish_prep"]["status"] != "complete"]
    if package_gap_refs:
        gaps.append(_gap("publish_prep_incomplete", package_gap_refs, "Approved Packaging fields or hash-verified declared publication assets are structurally incomplete; platform Connector preflight is still downstream even when complete."))
    timing_gap_refs = [slot["item_id"] for slot in assigned if slot["publish_at"] is None]
    if timing_gap_refs:
        no_clear = [slot["item_id"] for slot in assigned if slot["item_id"] in timing_gap_refs and slot["timing"]["recommendation_outcome"] == "no_clear_window"]
        detail = "The timing analysis found no clear winning window; publish_now is preserved, but an exact creator-confirmed time is still required." if no_clear else "No exact creator-confirmed or PublishSchedule time is available; no generic best time was substituted."
        gaps.append(_gap("timing_unavailable", timing_gap_refs, detail))
    if any_unknown:
        gaps.append(_gap("capacity_unknown", [slot["item_id"] for slot in assigned if slot["production"]["capacity_status"] == "unknown"], "Production capacity cannot be claimed feasible until missing estimates or weekly capacity are supplied."))
    if any_exceeded:
        gaps.append(_gap("capacity_exceeded", [slot["item_id"] for slot in assigned if slot["production"]["capacity_status"] == "exceeded"], "At least one week exceeds the supplied production capacity."))
    production_unknown_refs = [slot["item_id"] for slot in assigned if slot["production"]["state"] == "unknown"]
    if production_unknown_refs:
        gaps.append(_gap("production_progress_unavailable", production_unknown_refs, "Actual production progress was not supplied by the Creator or a workflow system; structural package readiness is not a substitute."))
    production_incomplete_refs = [slot["item_id"] for slot in assigned if slot["production"]["state"] in {"not_started", "in_progress", "blocked"}]
    if production_incomplete_refs:
        gaps.append(_gap("production_incomplete", production_incomplete_refs, "At least one item is not reported complete; keep its deadline and remaining work visible and do not prepare it for publishing."))
    if request["planning_window"]["timezone_source"] == "assumed":
        gaps.append(_gap("timezone_assumed", [], "Confirm the creator's IANA timezone before accepting exact times.", 1))
    constraint_refs.update(entry.get("item_id") for entry in unscheduled if entry["reason"] in {"locked_time_conflict", "blackout_date", "no_available_slot", "no_matching_cadence", "locked_time_outside_window"} and entry.get("item_id"))
    if constraint_refs:
        gaps.append(_gap("constraint_conflict", sorted(constraint_refs), "Locked times, blackout dates or cadence constraints conflict; committed times remain visible and were not moved."))
    # Reminders are executable-facing truth, not merely decorative slot data.
    # Any conflict or near-duplicate ambiguity blocks the whole affected group:
    # keep the VEVENT/calendar draft visible for review, but emit no reminder
    # records for those slots and therefore no VALARM/post-receipt templates.
    blocked_reminder_items = set(constraint_refs) | set(repeated_refs)
    for slot in assigned:
        if slot["item_id"] in blocked_reminder_items:
            slot["reminders"] = []
    blackout_slot_dates = sorted({slot["planned_date"].isoformat() for slot in target_slots if slot["planned_date"].isoformat() in blackout})
    if blackout_slot_dates:
        gaps.append(_gap("blackout_slots", blackout_slot_dates, "Cadence slots on blackout dates remain visibly unfilled."))
    if overdue_refs:
        gaps.append(_gap("production_overdue", sorted(overdue_refs), "The production due time is earlier than this request's created_at; the Calendar preserves the publish commitment and flags recovery."))
    if slot_decision is not None and slot_decision["selected_item_id"] is None:
        gaps.append(_gap("single_slot_choice_required", slot_decision["candidate_item_ids"], slot_decision["rationale"]))
    elif slot_decision is not None and slot_decision["requires_creator_confirmation"]:
        gaps.append(_gap("single_slot_confirmation_required", [slot_decision["selected_item_id"]], "Confirm or change the proposed content assignment before any calendar, reminder or publish handoff is requested."))

    capacity_status = "exceeded" if any_exceeded else ("unknown" if any_unknown else "within")
    choose_handoff_ready = bool(
        choose_confirmed
        and not package_gap_refs
        and not timing_gap_refs
        and not constraint_refs
        and not overdue_refs
        and not any_unknown
        and not any_exceeded
        and request["planning_window"]["timezone_source"] != "assumed"
    )
    if planned_count == 0 and request.get("candidate_idea_set_refs") and not choose_mode:
        status = "needs_user_selection"
        next_action = "select_more_ideas"
    elif slot_decision is not None and slot_decision["selected_item_id"] is None:
        status = "partial_needs_user_input"
        next_action = "choose_slot_item"
    elif choose_mode:
        if choose_handoff_ready:
            status = "ready_for_review"
            next_action = "execute_confirmed_handoff"
        else:
            status = "partial_needs_user_input"
            if not choose_confirmed:
                next_action = "confirm_slot_choice"
            elif package_gap_refs:
                next_action = "complete_packages"
            else:
                next_action = "resolve_constraints"
    elif any_exceeded or constraint_refs or overdue_refs or production_unknown_refs or production_incomplete_refs:
        status = "partial_needs_user_input"
        next_action = "resolve_constraints"
    elif package_gap_refs:
        status = "partial_needs_user_input"
        next_action = "complete_packages"
    elif repeated_groups:
        status = "partial_needs_user_input"
        next_action = "review_content_diversity"
    elif selection_shortfall or timing_gap_refs or any_unknown or request["planning_window"]["timezone_source"] == "assumed":
        status = "partial_needs_user_input"
        next_action = "select_more_ideas" if selection_shortfall else "resolve_constraints"
    else:
        status = "ready_for_review"
        next_action = "review_calendar"

    decisions = []
    for index, gap in enumerate(gaps, start=1):
        decisions.append({"decision_id": f"decision-{index}", "question": gap["detail"], "affected_refs": gap["affected_refs"]})
    source_refs: dict[tuple[str, str, str, str], dict[str, Any]] = {}

    # `safe_artifact_path` 内部是拿 `run_root.resolve()` 去判越界的(calendar_common.py),
    # 返回的也是已解析路径。这里若直接用未解析的 `run_root` 去 relative_to,两端形态就不一致:
    # macOS 上 `/var` 是 `/private/var` 的符号链接,调用方传进来的临时目录是 `/var/...`,
    # 而 `resolved` 是 `/private/var/...`,于是抛
    # `'/private/var/.../selected-1.json' is not in the subpath of '/var/.../'`。
    # 生产入口(main)本来就 `.resolve()` 过 run_root,所以线上不触发;直接调 `build()` /
    # `validate_calendar()` 的调用方(测试、生成脚本)传原始临时目录时才会炸。
    # 在闭包外解析一次:既与 safe_artifact_path 的既有约定一致,也避免在 items 循环里
    # 每条都多一次 syscall。
    resolved_run_root = run_root.resolve()

    def add_source_ref(ref: Any, owner_path: Path) -> None:
        if not isinstance(ref, dict):
            return
        resolved = safe_artifact_path(owner_path, run_root, str(ref.get("path", "")))
        normalized = {
            "path": resolved.relative_to(resolved_run_root).as_posix(),
            "artifact_id": ref["artifact_id"],
            "schema_version": ref["schema_version"],
            "sha256": ref["sha256"],
        }
        key = (
            normalized["schema_version"],
            normalized["artifact_id"],
            normalized["path"],
            normalized["sha256"],
        )
        source_refs[key] = normalized

    for item in request.get("items") or []:
        for ref in [
            item["selected_idea_ref"],
            item.get("script_package_request_ref"),
            item.get("script_package_ref"),
            (item.get("timing") or {}).get("publish_request_ref"),
            (item.get("timing") or {}).get("publish_history_ref"),
            (item.get("timing") or {}).get("publish_recommendation_ref"),
            (item.get("timing") or {}).get("publish_schedule_ref"),
        ]:
            add_source_ref(ref, request_path)
        loaded = artifacts["items"].get(item["item_id"], {})
        package_request = loaded.get("package_request") or {}
        package_request_ref = item.get("script_package_request_ref")
        if package_request_ref is not None:
            package_request_path = artifacts["paths"].get(("script-package-request.v1", str(package_request_ref.get("artifact_id"))))
            if package_request_path is not None:
                for ref in [
                    package_request.get("creator_profile"),
                    package_request.get("source_script_package"),
                    *(package_request.get("video_analysis_sources") or []),
                ]:
                    add_source_ref(ref, package_request_path)
        publish_request = loaded.get("publish_request") or {}
        publish_request_ref = (item.get("timing") or {}).get("publish_request_ref")
        if publish_request_ref is not None:
            publish_request_path = artifacts["paths"].get(("publish-request.v1", str(publish_request_ref.get("artifact_id"))))
            if publish_request_path is not None:
                content = publish_request.get("content") or {}
                creator = publish_request.get("creator") or {}
                for ref in [content.get("source_idea_ref"), content.get("source_script_ref"), creator.get("profile_ref")]:
                    add_source_ref(ref, publish_request_path)
    for ref in request.get("candidate_idea_set_refs") or []:
        add_source_ref(ref, request_path)
    single_slot = request.get("single_slot_decision") or {}
    for ref in (single_slot.get("pending_request_ref"), single_slot.get("proposal_ref")):
        if ref is not None:
            add_source_ref(ref, request_path)

    if choose_mode:
        decision_id = (request.get("single_slot_decision") or {}).get("decision_id")
        phase = "confirmed" if choose_confirmed else "proposal"
        calendar_id = f"cal-{request['request_id']}-{decision_id}-{phase}"
    else:
        calendar_id = f"cal-{request['request_id']}"
    sorted_slots = sorted(assigned, key=lambda value: (value["planned_date"], value["publish_at"] or "", value["slot_id"]))
    action_scopes: list[dict[str, Any]] = []
    blocked_time_action_items = blocked_reminder_items

    def append_time_action_scopes() -> None:
        safe_slots = [
            slot for slot in sorted_slots
            if _time_actions_safe(slot, request, blocked_time_action_items)
        ]
        pre_reminder_ids = [
            slot["slot_id"] for slot in safe_slots
            if any(reminder["type"] == "pre_publish" for reminder in slot.get("reminders") or [])
        ]
        if pre_reminder_ids:
            action_scopes.append({"action": "create_pre_publish_reminders", "slot_ids": pre_reminder_ids})
        post_reminder_ids = [
            slot["slot_id"] for slot in safe_slots
            if any(reminder["type"] == "post_publish_attention" for reminder in slot.get("reminders") or [])
        ]
        if post_reminder_ids:
            action_scopes.append({"action": "activate_post_publish_reminders_after_receipt", "slot_ids": post_reminder_ids})
        publish_ids = [
            slot["slot_id"] for slot in safe_slots
            if slot["publish_prep"]["status"] == "complete"
            and slot["production"]["state"] == "complete"
            and slot["timing"]["schedule_state"] != "scheduled"
            and slot["production"]["due_status"] in {"on_time", "not_applicable"}
            and slot["production"]["capacity_status"] == "within"
            and not slot["production"]["reported_blockers"]
            and not slot["production"]["planning_blockers"]
        ]
        if publish_ids:
            action_scopes.append({"action": "prepare_publish_handoff", "slot_ids": publish_ids})

    if not choose_mode and sorted_slots:
        action_scopes.append({"action": "save_calendar", "slot_ids": [slot["slot_id"] for slot in sorted_slots]})
        append_time_action_scopes()
    elif choose_handoff_ready and sorted_slots:
        action_scopes.append({"action": "upsert_calendar_slot", "slot_ids": [slot["slot_id"] for slot in sorted_slots]})
        append_time_action_scopes()
    requested_actions = [scope["action"] for scope in action_scopes]
    confirmation_slot_ids = sorted({slot_id for scope in action_scopes for slot_id in scope["slot_ids"]})

    return {
        "schema_version": "content-calendar.v1",
        "calendar_id": calendar_id,
        "request_id": request["request_id"],
        "created_at": request["created_at"],
        "intent": request["intent"],
        "projection_mode": "focus_slot_patch" if choose_mode else "full_calendar",
        "status": status,
        "planning_window": {
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "weeks": request["planning_window"]["weeks"],
            "timezone": request["planning_window"]["timezone"],
            "timezone_source": request["planning_window"]["timezone_source"],
        },
        "summary": {
            "target_slot_count": target_count,
            "planned_slot_count": planned_count,
            "unfilled_slot_count": unfilled,
            "timed_slot_count": sum(slot["publish_at"] is not None for slot in assigned),
            "capacity_status": capacity_status,
            "prep_complete_count": sum(slot["publish_prep"]["status"] == "complete" for slot in assigned),
            "production_complete_count": sum(slot["production"]["state"] == "complete" for slot in assigned),
            "distinct_content_count": len(semantic_groups),
        },
        "slot_decision": slot_decision,
        "slots": sorted_slots,
        "unfilled_slots": unfilled_slots,
        "unscheduled": unscheduled,
        "gaps": gaps,
        "interaction": {
            "next_action": next_action,
            "required_decisions": decisions,
            "runtime_handoff": {
                "requires_user_confirmation": True,
                "requested_actions": requested_actions,
                "action_scopes": action_scopes,
                "publish_execution": "not_performed",
                "confirmation_scope": {"status": "not_granted", "calendar_id": calendar_id, "slot_ids": confirmation_slot_ids, "expires_at": None},
            },
        },
        "exports": [],
        "provenance": {
            "candidate_skill": "plan-content-calendar@" + skill_version(),
            "source_artifact_refs": [source_refs[key] for key in sorted(source_refs)],
            "decision_rationales": [
                "Only SelectedIdea artifacts enter calendar slots; candidate IdeaSets remain awaiting selection.",
                "Exact publish times come only from PublishSchedule or explicit creator cadence, lock or single-slot inputs.",
                "Packaging and production readiness are projected without rewriting upstream content.",
            ],
            "refused_fallbacks": [
                "No generic platform best-time fallback.",
                "No silent Idea selection or generated replacement Packaging.",
                "No upload, schedule, publish or external write side effect.",
                "No Trial Reels, content-variant or frequency experiment in the first release.",
            ],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", type=Path)
    parser.add_argument("--run-root", type=Path, default=None)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    request_path = args.request.resolve()
    run_root = (args.run_root or request_path.parent).resolve()
    try:
        request = load_json(request_path)
        calendar = build(request, request_path, run_root)
        errors = validate_calendar(calendar, request, request_path, run_root)
        if errors:
            raise ValueError("generated calendar failed validation:\n- " + "\n- ".join(errors))
        output_path = safe_output_path(args.output, run_root, request_input_paths(request, request_path, run_root), "--output")
    except (OSError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_new_bytes(output_path, canonical_json(calendar).encode("utf-8"))
    print(f"WROTE {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
