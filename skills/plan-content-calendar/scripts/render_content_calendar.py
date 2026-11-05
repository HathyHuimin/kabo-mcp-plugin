#!/usr/bin/env python3
"""Render a concise creator-facing projection from ContentCalendar JSON."""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

# Sibling imports must not leave a __pycache__ in the signed, checksummed skill directory.
sys.dont_write_bytecode = True
from calendar_common import load_json, request_input_paths, safe_output_path, write_new_bytes
from validate_content_calendar import validate_calendar


ACTION_LABELS_ZH = {
    "save_calendar": "保存这份四周日历草稿",
    "upsert_calendar_slot": "只更新这个指定时段",
    "create_pre_publish_reminders": "创建发布前提醒",
    "activate_post_publish_reminders_after_receipt": "收到真实发布回执后再激活发布后关注提醒",
    "prepare_publish_handoff": "准备交给已授权的发布流程",
}

ACTION_LABELS_EN = {
    "save_calendar": "save this four-week calendar draft",
    "upsert_calendar_slot": "update only this requested slot",
    "create_pre_publish_reminders": "create pre-publish reminders",
    "activate_post_publish_reminders_after_receipt": "activate post-publish attention reminders after a real publish receipt",
    "prepare_publish_handoff": "prepare a handoff to an authorized publishing flow",
}

# One creator-facing wording per builder action, shared by every language branch. SKILL.md forbids
# printing the snake_case enum itself, so a branch that has no localization must still read from
# here rather than join the raw values.
REQUIRED_ACTIONS = {
    "create_script_package": "create the script and packaging",
    "approve_script_package": "approve the script and packaging",
    "complete_packaging": "complete the content packaging",
    "resolve_script_asset_placeholders": "supply the placeholder assets the script still needs",
    "resolve_missing_production_assets": "supply the missing production assets",
    "resolve_claim_placeholders": "verify or remove the unconfirmed claims",
    "attach_final_cover": "attach the final cover",
    "attach_final_media": "attach the final media",
    "attach_carousel_media_set": "attach the full set of carousel images or videos",
    "attach_final_video": "attach the final video",
    "attach_final_image": "attach the final image",
    "attach_final_audio": "attach the final audio",
    "confirm_publish_time": "confirm the publish time",
    "resolve_calendar_conflict": "resolve the calendar conflict",
    "resolve_past_due_production": "handle the production that is already past due",
    "confirm_platform_media_requirements": "confirm the platform media requirements",
}


def _preview_zh(slot: dict[str, Any]) -> str:
    preview = slot["content_preview"]
    return (
        f"受众问题：{preview['audience_problem']}｜内容承诺：{preview['content_promise']}｜"
        f"角度：{preview['angle']}｜开场 Hook：{preview['hook']['opening_line']}"
        f"（{preview['hook']['mechanism']}）"
    )


def _preview_en(slot: dict[str, Any]) -> str:
    preview = slot["content_preview"]
    return (
        f"Audience problem: {preview['audience_problem']} | Promise: {preview['content_promise']} | "
        f"Angle: {preview['angle']} | Opening hook: {preview['hook']['opening_line']} "
        f"({preview['hook']['mechanism']})"
    )


def _required_actions(values: list[str]) -> str:
    return ", ".join(REQUIRED_ACTIONS.get(value, value.replace("_", " ")) for value in values)


def _handoff_lines(
    calendar: dict[str, Any],
    labels: dict[str, str],
    brackets: tuple[str, str],
    language: str,
) -> list[str]:
    """Render scoped actions in plain language without implying authorization."""
    if calendar.get("intent") == "date_range_plan":
        labels = {**labels, "save_calendar": "保存这份日历草稿" if language == "zh" else "save this calendar draft"}
    scopes = ((calendar.get("interaction") or {}).get("runtime_handoff") or {}).get("action_scopes") or []
    opening, closing = brackets
    separator = "" if opening == "（" else " "
    lines = []
    for scope in scopes:
        count = len(scope["slot_ids"])
        count_label = (
            f"{count} 个槽位"
            if language == "zh"
            else f"{count} {'slot' if count == 1 else 'slots'}"
        )
        lines.append(
            f"{labels.get(scope['action'], scope['action'].replace('_', ' '))}"
            f"{separator}{opening}{count_label}: {', '.join(scope['slot_ids'])}{closing}"
        )
    return lines


def _planning_blocker(value: str) -> str:
    """Spell out a planner-generated blocker code; never hand the raw enum to a creator."""
    if value == "production_capacity_unknown":
        return "Remaining production effort or capacity is missing, so feasibility cannot be judged."
    parts = value.split("_")
    if len(parts) == 7 and parts[0] == "week" and parts[2] == "needs" and parts[4] == "but" and parts[5] == "has":
        week, needed, available = parts[1], parts[3].removesuffix("m"), parts[6].removesuffix("m")
        if week.isdigit() and needed.isdigit() and available.isdigit():
            return f"Week {week} needs {needed} minutes of production, but only {available} minutes are available."
    return value.replace("_", " ")


def _blockers(production: dict[str, Any]) -> list[str]:
    return list(production["reported_blockers"]) + [
        _planning_blocker(value) for value in production["planning_blockers"]
    ]


def _production_zh(slot: dict[str, Any]) -> str:
    production = slot["production"]
    prep = slot["publish_prep"]["status"]
    prep_label = {"complete": "结构与声明素材完整", "partial": "结构或声明素材待补", "unavailable": "尚无可用素材包"}[prep]
    status_label = {
        "not_started": "未开始", "in_progress": "进行中", "blocked": "受阻",
        "complete": "已完成", "unknown": "未知",
    }[production["state"]]
    source_label = {
        "creator_confirmed": "Creator 确认", "workflow_system": "制作系统",
        "unavailable": "无可用进度来源",
    }[production["source"]]
    observed = production["as_of"] or "未提供"
    remaining = production["remaining_minutes"]
    if production["state"] == "complete":
        effort = "剩余 0 分钟（已完成）"
    else:
        effort = f"剩余 {remaining} 分钟" if remaining is not None else "剩余工时未知"
    due = production["due_at"] or "不适用"
    capacity = {"within": "容量内", "exceeded": "超出本周容量", "unknown": "容量未知"}[production["capacity_status"]]
    value = (
        f"实际制作状态 {status_label}（{source_label}；观测时间 {observed}）；{effort}；"
        f"发布准备：{prep_label}；制作截止 {due}（{production['due_status']}）；{capacity}"
    )
    blockers = _blockers(production)
    if blockers:
        value += f"；阻塞：{'、'.join(blockers)}"
    return value


def _production_en(slot: dict[str, Any]) -> str:
    production = slot["production"]
    prep = slot["publish_prep"]["status"]
    prep_label = {"complete": "structure and declared media complete", "partial": "structure or declared media incomplete", "unavailable": "no usable package"}[prep]
    status_label = production["state"].replace("_", " ")
    source_label = {
        "creator_confirmed": "Creator-confirmed", "workflow_system": "workflow system",
        "unavailable": "no available progress source",
    }[production["source"]]
    observed = production["as_of"] or "not supplied"
    remaining = production["remaining_minutes"]
    if production["state"] == "complete":
        effort = "0 minutes remaining (complete)"
    else:
        effort = f"{remaining} minutes remaining" if remaining is not None else "remaining effort unknown"
    due = production["due_at"] or "not applicable"
    value = (
        f"actual production status {status_label} ({source_label}; observed {observed}); {effort}; "
        f"publish preparation: {prep_label}; production due {due} ({production['due_status']}); "
        f"capacity {production['capacity_status']}"
    )
    blockers = _blockers(production)
    if blockers:
        value += f"; blockers: {', '.join(blockers)}"
    return value


def _reminders_zh(slot: dict[str, Any]) -> str:
    reminders = slot.get("reminders") or []
    pre = [value for value in reminders if value["type"] == "pre_publish"]
    post = [value for value in reminders if value["type"] == "post_publish_attention"]
    pre_text = "、".join(f"{value['at']}（提前 {abs(value['offset_minutes'])} 分钟）" for value in pre)
    if not pre_text:
        pre_text = "无（未配置、无精确发布时间或提醒窗口已过）"
    post_text = "、".join(f"真实发布回执后 +{value['offset_minutes']} 分钟" for value in post) or "无"
    return f"发布前提醒：{pre_text}；发布后关注模板：{post_text}（收到回执前不激活）"


def _reminders_en(slot: dict[str, Any]) -> str:
    reminders = slot.get("reminders") or []
    pre = [value for value in reminders if value["type"] == "pre_publish"]
    post = [value for value in reminders if value["type"] == "post_publish_attention"]
    pre_text = ", ".join(f"{value['at']} ({abs(value['offset_minutes'])} minutes before)" for value in pre)
    if not pre_text:
        pre_text = "none (not configured, no exact publish time, or the reminder window has passed)"
    post_text = ", ".join(f"real publish receipt +{value['offset_minutes']} minutes" for value in post) or "none"
    return f"Pre-publish reminders: {pre_text}; post-publish attention templates: {post_text} (inactive until receipt)"


def _post_offsets(calendar: dict[str, Any]) -> list[int]:
    return sorted({
        int(reminder["offset_minutes"])
        for slot in calendar.get("slots") or []
        for reminder in slot.get("reminders") or []
        if reminder.get("type") == "post_publish_attention"
    })


def _render_focus_zh(calendar: dict[str, Any]) -> str:
    decision = calendar["slot_decision"]
    slot = (calendar.get("slots") or [None])[0]
    confirmed = decision["confirmation_status"] == "confirmed"
    lines = [
        "# 指定时点内容安排" if confirmed else "# 指定时点内容建议",
        "",
        f"合同状态：`{calendar['status']}`",
        f"目标时间：{decision['target_publish_at']}",
        f"结果：{decision.get('selected_item_id') or '尚未选出'}（{decision['outcome']}；依据：{decision['selection_basis']}）",
        f"说明：{decision['rationale']}",
        "",
        "## 候选核对",
        "",
    ]
    for evaluation in decision["candidate_evaluations"]:
        lines.append(
            f"- {evaluation['item_id']}｜可占用：{'是' if evaluation['eligible'] else '否'}｜"
            f"结构准备：{'完成' if evaluation['operationally_ready'] else '未完成'}｜{evaluation['reason']}"
        )
    if slot is not None:
        lines.extend([
            "",
            "## 当前选择",
            "",
            f"- {slot['title']}｜{slot['target']['platform']}/{slot['target']['publish_format']}｜{slot['publish_at']}",
            f"- {_preview_zh(slot)}",
            f"- 槽位状态：{slot['planning_state']}；结构准备：{slot['publish_prep']['status']}（不等于平台媒体预检通过）",
            f"- 制作进度：{_production_zh(slot)}",
            f"- 提醒：{_reminders_zh(slot)}",
        ])
        if slot["required_actions"]:
            lines.append(f"- 待办：{_required_actions(slot['required_actions'])}")
    if calendar.get("gaps"):
        labels = {
            "publish_prep_incomplete": "脚本、包装或经校验的最终素材尚未准备完成。",
            "timing_unavailable": "没有 Creator 已确认的精确时间；未使用通用最佳时间兜底。",
            "capacity_unknown": "剩余制作工时或容量不足，当前不能判断是否做得完。",
            "capacity_exceeded": "剩余制作工时超过已提供容量。",
            "timezone_assumed": "时区仍是假设值；确认时区前不会请求任何执行 handoff。",
            "constraint_conflict": "该时点与既有承诺、黑名单日期或间隔规则冲突。",
            "production_overdue": "尚未完成内容的制作截止时间已经过去。",
            "production_progress_unavailable": "没有 Creator 或制作系统确认的实际制作进度；不能用脚本/素材结构代替。",
            "production_incomplete": "至少一条内容的实际制作状态尚未完成。",
            "single_slot_choice_required": "现有证据不足以替 Creator 静默选择内容。",
            "single_slot_confirmation_required": "需要 Creator 明确确认建议的内容。",
            "near_duplicate_selected_ideas": "多条已选内容除编号外具有相同标题、承诺、角度、Hook 和结构；请确认这是有意系列，或替换重复内容。",
        }
        lines.extend(["", "## 需要你确认", ""])
        for gap in calendar["gaps"]:
            lines.append(f"- {labels.get(gap['gap_type'], gap['detail'])}")
        lines.append("- 当前状态：仍需处理以上问题。")
    if decision["requires_creator_confirmation"]:
        lines.extend(["", "请确认这条内容，或明确改选另一个候选；确认前不会写入日历、创建提醒或准备发布。"])
    else:
        lines.extend(["", f"Creator 已于 {decision['confirmed_at']} 确认；Runtime 仍需对具体外部写操作单独确认。"])
    offsets = _post_offsets(calendar)
    post_text = f"发布后关注模板：真实发布回执后的 {('/'.join(str(value) for value in offsets))} 分钟。" if offsets else "未请求发布后关注提醒。"
    handoffs = _handoff_lines(calendar, ACTION_LABELS_ZH, ("（", "）"), "zh")
    if handoffs:
        lines.extend(["", "## 确认后可执行", ""] + [f"- {value}" for value in handoffs] + ["- 上述动作均尚未授权，本轮没有执行。"])
    lines.extend([
        "",
        "## 执行边界",
        "",
        "这是 focus_slot_patch，不是完整四周日历，也不会覆盖其他槽位。Skill 没有上传、排程或发布；确认后只可交给 Runtime 做受限槽位 upsert。",
        post_text,
        "",
    ])
    return "\n".join(lines)


def _render_focus_en(calendar: dict[str, Any]) -> str:
    decision = calendar["slot_decision"]
    slot = (calendar.get("slots") or [None])[0]
    confirmed = decision["confirmation_status"] == "confirmed"
    lines = [
        "# Confirmed content assignment" if confirmed else "# Content recommendation for the requested time",
        "",
        f"Contract status: `{calendar['status']}`",
        f"Target: {decision['target_publish_at']}",
        f"Result: {decision.get('selected_item_id') or 'not selected'} ({decision['outcome']}; basis {decision['selection_basis']})",
        f"Why: {decision['rationale']}",
        "",
        "## Candidate check",
        "",
    ]
    for evaluation in decision["candidate_evaluations"]:
        lines.append(f"- {evaluation['item_id']} | eligible {evaluation['eligible']} | structurally ready {evaluation['operationally_ready']} | {evaluation['reason']}")
    if slot is not None:
        lines.extend([
            "",
            "## Current assignment",
            "",
            f"- {slot['title']} | {slot['target']['platform']}/{slot['target']['publish_format']} | {slot['publish_at']}",
            f"- {_preview_en(slot)}",
            f"- Slot state: {slot['planning_state']}; structural prep: {slot['publish_prep']['status']} (not a platform media preflight)",
            f"- Production progress: {_production_en(slot)}",
            f"- Reminders: {_reminders_en(slot)}",
        ])
        if slot["required_actions"]:
            lines.append(f"- Next: {_required_actions(slot['required_actions'])}")
    if calendar.get("gaps"):
        labels = {
            "publish_prep_incomplete": "The script, packaging, or hash-verified final media is incomplete.",
            "timing_unavailable": "No Creator-confirmed exact time is available; no generic best-time fallback was used.",
            "capacity_unknown": "Remaining production effort or capacity is missing, so feasibility is unknown.",
            "capacity_exceeded": "Remaining production effort exceeds the supplied capacity.",
            "timezone_assumed": "The timezone is still assumed; no execution handoff is requested until it is confirmed.",
            "constraint_conflict": "The slot conflicts with an existing commitment, blackout, or spacing rule.",
            "production_overdue": "The production deadline for unfinished content has passed.",
            "production_progress_unavailable": "Actual production progress was not confirmed by the Creator or a workflow system; package structure is not a substitute.",
            "production_incomplete": "At least one item is reported as not yet complete.",
            "single_slot_choice_required": "The evidence cannot silently choose content for the Creator.",
            "single_slot_confirmation_required": "The Creator must explicitly confirm the proposed content.",
            "near_duplicate_selected_ideas": "Multiple selected items have the same title, promise, angle, hook and outline after numbering is removed. Confirm an intentional series or replace the repeats.",
        }
        lines.extend(["", "## Decisions needed", ""])
        for gap in calendar["gaps"]:
            lines.append(f"- {labels.get(gap['gap_type'], gap['detail'])}")
        lines.append("- Current state: the issues above still need a decision.")
    if decision["requires_creator_confirmation"]:
        lines.extend(["", "Confirm this item or choose another candidate. No calendar, reminder or publish handoff is requested before confirmation."])
    else:
        lines.extend(["", f"Creator confirmed at {decision['confirmed_at']}; Runtime confirmation is still required for each scoped external write."])
    offsets = _post_offsets(calendar)
    post_text = f"Post-publish templates: {('/'.join(str(value) for value in offsets))} minutes after a real publish receipt." if offsets else "No post-publish attention reminder was requested."
    handoffs = _handoff_lines(calendar, ACTION_LABELS_EN, ("(", ")"), "en")
    if handoffs:
        lines.extend(["", "## Available after confirmation", ""] + [f"- {value}" for value in handoffs] + ["- None of these actions is authorized or executed in this result."])
    lines.extend([
        "",
        "## Execution boundary",
        "",
        "This is a focus_slot_patch, not a full four-week calendar. It cannot replace other slots. The Skill did not upload, schedule or publish anything; after confirmation Runtime may only perform a scoped slot upsert.",
        post_text,
        "",
    ])
    return "\n".join(lines)


def _render_zh(calendar: dict[str, Any]) -> str:
    """Render the calendar and its exact range for a Chinese-speaking creator."""
    if calendar.get("projection_mode") == "focus_slot_patch":
        return _render_focus_zh(calendar)
    summary = calendar["summary"]
    lines = [
        "# 内容日历" if calendar.get("intent") == "date_range_plan" else "# 四周内容日历",
        "",
        f"合同状态：`{calendar['status']}`",
        "",
        f"结论：已安排 {summary['planned_slot_count']}/{summary['target_slot_count']} 个内容位；"
        f"仍有 {summary['unfilled_slot_count']} 个空位；"
        f"{summary['timed_slot_count']} 个已有明确发布时间，{summary['production_complete_count']} 个实际制作已完成，"
        f"{summary['prep_complete_count']} 个结构准备完整；"
        f"{summary['distinct_content_count']} 个内容方向可区分（结构完整不等于平台媒体预检通过）。",
        "",
        f"时间范围：{calendar['planning_window']['start_date']} 至 {calendar['planning_window']['end_date']}"
        f"（{calendar['planning_window']['timezone']}）",
        "",
    ]
    decision = calendar.get("slot_decision")
    if decision:
        chosen = decision.get("selected_item_id") or "尚未选择"
        lines.extend([
            "## 指定时点选内容",
            "",
            f"- 目标时间：{decision['target_publish_at']}",
            f"- 结果：{chosen}（{decision['outcome']}；依据：{decision['selection_basis']}）",
            f"- 说明：{decision['rationale']}",
            "- 该结果仍需 Creator 确认。",
            "",
        ])
    by_week: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for slot in calendar.get("slots") or []:
        by_week[slot["week_index"]].append(slot)
    empty_by_week: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for slot in calendar.get("unfilled_slots") or []:
        empty_by_week[slot["week_index"]].append(slot)
    for week in range(1, calendar["planning_window"]["weeks"] + 1):
        lines.extend([f"## 第 {week} 周", "",
                      f"本周：已安排 {len(by_week[week])} 个内容位；空缺 {len(empty_by_week[week])} 个。", ""])
        if not by_week[week] and not empty_by_week[week]:
            lines.extend(["- 暂无已选内容。", ""])
            continue
        for slot in by_week[week]:
            timing = slot["publish_at"] or "时间待确认"
            prep = {"complete": "结构准备完整（未做平台媒体预检）", "partial": "结构或素材待补", "unavailable": "尚无素材包"}[slot["publish_prep"]["status"]]
            timing_meta = slot["timing"]
            evidence = f"{timing_meta['source']}；结论 {timing_meta['recommendation_outcome'] or '无'}；置信度 {timing_meta['recommendation_confidence'] or '无'}；排程状态 {timing_meta['schedule_state'] or '无'}"
            lines.append(f"- {slot['planned_date']}｜{slot['title']}｜{slot['target']['platform']}/{slot['target']['publish_format']}｜{timing}｜{prep}")
            lines.append(f"  - {_preview_zh(slot)}")
            lines.append(f"  - 时间依据：{evidence}")
            lines.append(f"  - 制作进度：{_production_zh(slot)}")
            lines.append(f"  - 提醒：{_reminders_zh(slot)}")
            if slot["required_actions"]:
                lines.append(f"  - 下一步：{_required_actions(slot['required_actions'])}")
        for slot in empty_by_week[week]:
            target = slot["target"]
            lines.append(f"- 空位｜{slot['planned_date']}｜{target['account_ref']}｜{target['platform']}/{target['publish_format'] or '任意发布格式'}｜{slot['reason']}")
        lines.append("")
    if calendar.get("unscheduled"):
        lines.extend(["## 未进入本窗口的内容", ""])
        for entry in calendar["unscheduled"]:
            commitment = f"｜原承诺时间 {entry['existing_commitment_at']}（{entry.get('existing_schedule_state') or 'creator_locked'}）" if entry.get("existing_commitment_at") else ""
            identity = entry.get("item_id") or ((entry.get("candidate_idea_set_ref") or {}).get("artifact_id")) or entry["entry_id"]
            lines.append(f"- {identity}｜{entry['reason']}{commitment}｜{entry['next_action']}")
        lines.append("")
    if calendar.get("gaps"):
        lines.extend(["## 需要你确认", ""])
        labels = {
            "selection_shortfall": "已选内容不足，无法填满目标频率。",
            "publish_prep_incomplete": "部分脚本、包装或经校验的最终素材尚未准备完成。",
            "timing_unavailable": "部分内容没有 Creator 已确认的精确时间；未使用通用最佳时间兜底。",
            "capacity_unknown": "制作容量或单条工时不足，当前不能判断是否做得完。",
            "capacity_exceeded": "至少一周的制作工时超过已提供容量。",
            "timezone_assumed": "时区仍是假设值，确认前不要接受精确时间。",
            "constraint_conflict": "已锁定承诺与频率、黑名单日期或间隔规则冲突；承诺仍显示且没有被移动。",
            "blackout_slots": "黑名单日期对应的目标内容位保留为空。",
            "production_overdue": "至少一条内容的制作截止时间早于本次请求时间，需要恢复安排。",
            "production_progress_unavailable": "至少一条内容没有可靠的实际制作进度。",
            "production_incomplete": "至少一条内容的实际制作状态尚未完成。",
            "single_slot_choice_required": "指定时点仍有多个可用内容，证据不足以替 Creator 静默决定。",
            "single_slot_confirmation_required": "需要 Creator 明确确认建议的内容。",
            "near_duplicate_selected_ideas": "多条已选内容除编号外具有相同标题、承诺、角度、Hook 和结构；请确认这是有意系列，或替换重复内容。",
        }
        for gap in calendar["gaps"]:
            lines.append(f"- {labels.get(gap['gap_type'], gap['detail'])}")
        lines.append("")
    handoffs = _handoff_lines(calendar, ACTION_LABELS_ZH, ("（", "）"), "zh")
    if handoffs:
        lines.extend(["## 确认后可执行", ""] + [f"- {value}" for value in handoffs] + ["- 上述动作均尚未授权，本轮没有执行。", ""])
    # A draft requested without export has no CSV/ICS; the boundary must not claim files that do not exist.
    exported = bool(calendar.get("exports"))
    lines.extend([
        "## 发布边界",
        "",
        ("这份结果只是待确认的日历、CSV/ICS 和提醒交接。" if exported else "这份结果只是待确认的日历草稿和提醒交接，按你的要求没有导出 CSV/ICS。")
        + "它没有上传、定时发布或改写任何平台状态；真正发布仍需 Runtime 获得你的确认，并由已授权 Connector 执行。",
        ("发布前提醒可投影到 ICS；" if exported else "") + (
            f"发布后 {'/'.join(str(value) for value in _post_offsets(calendar))} 分钟关注提醒只是以 connector_published_at 为锚的模板，只有收到真实发布回执后才能由 Runtime 激活。"
            if _post_offsets(calendar) else "本次未请求发布后关注提醒。"
        ),
        "",
    ])
    return "\n".join(lines)


def _render_en(calendar: dict[str, Any]) -> str:
    """Render the calendar and its exact range for an English-speaking creator."""
    if calendar.get("projection_mode") == "focus_slot_patch":
        return _render_focus_en(calendar)
    summary = calendar["summary"]
    lines = [
        "# Content calendar" if calendar.get("intent") == "date_range_plan" else "# Four-week content calendar",
        "",
        f"Contract status: `{calendar['status']}`",
        "",
        f"Outcome: {summary['planned_slot_count']}/{summary['target_slot_count']} slots planned; "
        f"{summary['unfilled_slot_count']} slots unfilled; "
        f"{summary['timed_slot_count']} have an exact publish time, {summary['production_complete_count']} are reported production-complete, "
        f"{summary['prep_complete_count']} are structurally ready, "
        f"and {summary['distinct_content_count']} content directions are distinguishable (structural readiness is not platform media preflight).",
        "",
        f"Window: {calendar['planning_window']['start_date']} through {calendar['planning_window']['end_date']} "
        f"({calendar['planning_window']['timezone']})",
        "",
    ]
    decision = calendar.get("slot_decision")
    if decision:
        lines.extend([
            "## Content for the requested time",
            "",
            f"- Target: {decision['target_publish_at']}",
            f"- Result: {decision.get('selected_item_id') or 'not selected'} ({decision['outcome']}; basis {decision['selection_basis']})",
            f"- Why: {decision['rationale']}",
            "- Creator confirmation is still required.",
            "",
        ])
    by_week: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for slot in calendar.get("slots") or []:
        by_week[slot["week_index"]].append(slot)
    empty_by_week: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for slot in calendar.get("unfilled_slots") or []:
        empty_by_week[slot["week_index"]].append(slot)
    for week in range(1, calendar["planning_window"]["weeks"] + 1):
        lines.extend([f"## Week {week}", "",
                      f"This week: {len(by_week[week])} slots planned; {len(empty_by_week[week])} slots unfilled.", ""])
        if not by_week[week] and not empty_by_week[week]:
            lines.extend(["- No selected content yet.", ""])
            continue
        for slot in by_week[week]:
            lines.append(
                f"- {slot['planned_date']} | {slot['title']} | {slot['target']['platform']} | "
                f"{slot['publish_at'] or 'time to confirm'} | prep {slot['publish_prep']['status']} | "
                f"timing {slot['timing']['source']}/{slot['timing']['recommendation_outcome'] or 'none'}/"
                f"{slot['timing']['recommendation_confidence'] or 'none'} | schedule {slot['timing']['schedule_state'] or 'none'}"
            )
            lines.append(f"  - {_preview_en(slot)}")
            lines.append(f"  - Production progress: {_production_en(slot)}")
            lines.append(f"  - Reminders: {_reminders_en(slot)}")
            if slot["required_actions"]:
                lines.append(f"  - Next: {_required_actions(slot['required_actions'])}")
        for slot in empty_by_week[week]:
            target = slot["target"]
            lines.append(f"- Empty | {slot['planned_date']} | {target['account_ref']} | {target['platform']}/{target['publish_format'] or 'any publish format'} | {slot['reason']}")
        lines.append("")
    if calendar.get("unscheduled"):
        lines.extend(["## Content outside this window", ""])
        for entry in calendar["unscheduled"]:
            commitment = f" | existing commitment {entry['existing_commitment_at']} ({entry.get('existing_schedule_state') or 'creator_locked'})" if entry.get("existing_commitment_at") else ""
            identity = entry.get("item_id") or ((entry.get("candidate_idea_set_ref") or {}).get("artifact_id")) or entry["entry_id"]
            lines.append(f"- {identity} | {entry['reason']}{commitment} | {entry['next_action']}")
        lines.append("")
    if calendar.get("gaps"):
        lines.extend(["## Decisions needed", ""])
        gap_labels = {
            "near_duplicate_selected_ideas": "Multiple selected items have the same title, promise, angle, hook and outline after numbering is removed. Confirm an intentional series or replace the repeats.",
        }
        lines.extend(f"- {gap_labels.get(gap['gap_type'], gap['detail'])}" for gap in calendar["gaps"])
        lines.append("")
    handoffs = _handoff_lines(calendar, ACTION_LABELS_EN, ("(", ")"), "en")
    if handoffs:
        lines.extend(["## Available after confirmation", ""] + [f"- {value}" for value in handoffs] + ["- None of these actions is authorized or executed in this result.", ""])
    exported = bool(calendar.get("exports"))
    lines.extend([
        "## Publishing boundary",
        "",
        ("This is a reviewable calendar plus CSV/ICS and reminder handoffs. " if exported
         else "This is a reviewable calendar draft plus reminder handoffs; as requested, no CSV/ICS was exported. ")
        + "It did not upload, schedule or publish anything. A Runtime must obtain your confirmation before an authorized Connector performs any external write.",
        ("Pre-publish reminders can be projected into ICS. " if exported else "") + (
            f"Post-publish templates at {'/'.join(str(value) for value in _post_offsets(calendar))} minutes stay anchored to connector_published_at and can be activated only after a real publish receipt."
            if _post_offsets(calendar) else "No post-publish attention reminder was requested."
        ),
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("calendar", type=Path)
    parser.add_argument("request", type=Path)
    parser.add_argument("--run-root", type=Path, default=None)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    request_path = args.request.resolve()
    run_root = (args.run_root or request_path.parent).resolve()
    try:
        calendar = load_json(args.calendar.resolve())
        request = load_json(request_path)
        errors = validate_calendar(calendar, request, request_path, run_root, args.calendar.resolve())
        if errors:
            raise ValueError("invalid calendar:\n- " + "\n- ".join(errors))
        text = _render_zh(calendar) if str(request.get("language", "")).lower().startswith("zh") else _render_en(calendar)
        protected = request_input_paths(request, request_path, run_root) | {args.calendar.resolve()}
        output_path = safe_output_path(args.output, run_root, protected, "--output")
    except (OSError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_new_bytes(output_path, text.encode("utf-8"))
    print(f"WROTE {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
