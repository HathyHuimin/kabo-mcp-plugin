#!/usr/bin/env python3
"""Render a validated IdeaSet as a compact, Creator-first decision surface."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from report_language import evidence_gaps, report_value_translations, translator


def load(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def ordered_ideas(data: dict) -> list[dict]:
    by_id = {item["idea_id"]: item for item in data["ideas"]}
    result: list[dict] = []
    for idea_id in data["overview"]["recommended_candidate_ids"]:
        if idea_id in by_id and by_id[idea_id] not in result:
            result.append(by_id[idea_id])
    for item in sorted(data["ideas"], key=lambda value: value["rank"]):
        if item not in result:
            result.append(item)
    return result


def minute_text(feasibility: dict, tr=lambda value: value, display=lambda value: value) -> str:
    """Format estimated duration or effort through the report UI translator."""
    estimate = feasibility.get("estimated_minutes")
    if not estimate:
        effort = feasibility.get("effort", "unknown")
        return tr('待确认') if effort == "unknown" else normalized_whitespace(display(effort))
    lower, upper = estimate["min"], estimate["max"]
    return (str(lower) + tr(' 分钟')) if lower == upper else (str(lower) + '–' + str(upper) + tr(' 分钟'))


def pending_asset_lines(idea: dict, request: dict | None = None) -> list[str]:
    """展示未来所需资源，不改变已声明可用资源的含义。"""
    feasibility = idea.get("production_feasibility") or {}
    if feasibility.get("status") != "requires_user_confirmation" or not feasibility.get("required_assets"):
        return []
    required = feasibility["required_assets"]
    if request is None:
        # 独立调试没有资源可用性证据，只列依赖，不断言资源缺失或已经拥有。
        label = "Required assets: "
        pending = required
    else:
        available = set((request.get("production_constraints") or {}).get("available_assets") or [])
        label = "Pending confirmation: "
        pending = [asset for asset in required if asset not in available]
    lines = (["- **Before production:** " + label + "; ".join(normalized_whitespace(asset) for asset in pending)]
             if pending else [])
    lines.extend("- **Before production:** " + normalized_whitespace(value)
                 for value in feasibility.get("assumptions") or [])
    return lines


def evidence_text(idea: dict, tr=lambda value: value, display=lambda value: value) -> str:
    """Preserve the primary evidence reference or localize the missing-evidence label."""
    primary = idea.get("primary_evidence_ref")
    if not primary:
        return tr('无外部证据；这是待验证方向')
    return normalized_whitespace(display(primary))


def shoot_text(idea: dict) -> str:
    return normalized_whitespace(idea["making_direction"])


SOURCE_LABELS = {
    "creator": "Creator 定位/受众资料",
    "account": "账号表现证据",
    "trend": "近期趋势证据",
    "competitor": "竞品证据",
    "video": "待改内容的视频分析",
    "comments": "观众评论证据",
}


def normalized_whitespace(value: str) -> str:
    return " ".join(str(value).split())


def gate_text(value: str, tr=lambda value: value) -> str:
    """Localize known input gates while retaining unknown source text verbatim."""
    prefix = "missing_required_source:"
    if value.startswith(prefix):
        source = value[len(prefix):]
        rendered = (tr('缺少用户明确要求使用的') + str(tr(SOURCE_LABELS.get(source, source))))
    elif value == "explicit_content_topic_or_upstream_evidence":
        rendered = tr('请明确一个要讲的内容主题，或提供可用的账号、趋势、竞品等上游证据')
    else:
        rendered = value
    return normalized_whitespace(rendered)


def action_text(value: str, tr=lambda value: value) -> str:
    """Localize supported next actions without rewriting creator-authored action text."""
    rendered = {
        "choose": tr('选择一个选题'),
        "revise": tr('修改选题'),
        "refresh_evidence": tr('补齐或刷新上游证据'),
        "provide_own_direction": tr('直接给出一个明确内容主题'),
        "answer_selection_question": tr('补充选题所需信息'),
        "answer_production_question": tr('补充制作条件'),
        "blocked": tr('当前不生成选题'),
    }.get(value, value)
    return normalized_whitespace(rendered)


def section_lines(markdown: str, heading: str) -> list[str] | None:
    """Return one exact Markdown heading's body without crossing a peer heading."""
    lines = markdown.splitlines()
    matches = [index for index, line in enumerate(lines) if line == heading]
    if len(matches) != 1:
        return None
    start = matches[0]
    level = len(heading) - len(heading.lstrip("#"))
    body: list[str] = []
    for line in lines[start + 1:]:
        stripped = line.lstrip()
        next_level = len(stripped) - len(stripped.lstrip("#"))
        if next_level and next_level <= level and stripped[next_level:].startswith(" "):
            break
        body.append(line)
    return body


def validate_concise(markdown: str, data: dict, request: dict | None = None) -> None:
    """Fail closed if a value is missing from the field or section that owns it."""
    tr = translator(data)
    values = report_value_translations(data)
    display = lambda value: values.get(value, value)
    ideas = ordered_ideas(data)
    lines = markdown.splitlines()
    missing: list[str] = []
    required_occurrences: dict[tuple[int, str], int] = {}
    section_references: dict[int, list[str]] = {}

    def require_line(section: list[str] | None, expected: str, label: str) -> None:
        if section is None:
            missing.append(label)
            return
        section_id = id(section)
        section_references[section_id] = section
        key = (section_id, expected)
        required_occurrences[key] = required_occurrences.get(key, 0) + 1
        if section.count(expected) < required_occurrences[key]:
            missing.append(label)

    def require_position(values: list[str], index: int, expected: str, label: str) -> None:
        if len(values) <= index or values[index] != expected:
            missing.append(label)

    if ideas:
        first = ideas[0]
        require_position(
            lines,
            0,
            (tr('# 先拍：') + str(normalized_whitespace(first['working_title']))),
            "title prologue",
        )
        require_position(
            lines,
            2,
            (tr('> **先选它的原因：** ') + str(normalized_whitespace(first['why_for_creator']))),
            "top-choice reason prologue",
        )
        require_position(lines, 4, (tr('状态：`') + normalized_whitespace(display(data['status'])) + tr('`。')), "status prologue")
        if data["overview"].get("portfolio_logic"):
            require_line(section_lines(markdown, tr('## 这批内容怎么展开')),
                         normalized_whitespace(data["overview"]["portfolio_logic"]),
                         "portfolio logic")
        if data["task_intent"] == "rework_content":
            diagnostic_section = section_lines(markdown, tr('## 先看诊断'))
            require_line(
                diagnostic_section,
                (tr('> **诊断：** ') + str(normalized_whitespace(first['why_for_creator']))),
                "rework diagnostic",
            )
    else:
        require_position(
            lines,
            0,
            f"# {normalized_whitespace(data['overview']['headline'])}",
            "title prologue",
        )
        require_position(
            lines,
            2,
            (tr('状态：`') + normalized_whitespace(display(data['status'])) + tr('`；任务：`') + normalized_whitespace(display(data['task_intent'])) + tr('`；输入模式：`') + normalized_whitespace(display(data['source_mode'])) + tr('`。')),
            "status/task/source metadata prologue",
        )
        require_position(
            lines,
            4,
            (tr('> **缺少这些信息的影响：** ') + str(normalized_whitespace(data['overview']['missing_context_effect']))),
            "missing-context effect prologue",
        )

    recommended = (data.get("overview") or {}).get("recommended_candidate_ids") or []
    top_choice = recommended[0] if recommended else None
    for idea in ideas:
        suffix = tr('（首选）') if idea["idea_id"] == top_choice else ""
        heading = f"### {idea['rank']}. {normalized_whitespace(idea['working_title'])}{suffix}"
        section = section_lines(markdown, heading)
        if section is None:
            missing.append(f"idea {idea['idea_id']} heading")
            continue
        require_line(
            section,
            (tr('- **为什么适合：** ') + str(normalized_whitespace(idea['why_for_creator']))),
            f"idea {idea['idea_id']} why_for_creator",
        )
        require_line(
            section,
            (tr('- **创作者适配：** ') + str(normalized_whitespace(idea['creator_fit']['rationale']))),
            f"idea {idea['idea_id']} creator_fit.rationale",
        )
        require_line(section, tr('- **制作量：** ') + minute_text(idea['production_feasibility'], tr, display),
                     f"idea {idea['idea_id']} production effort")
        require_line(section, tr('- **依据：** ') + evidence_text(idea, tr, display),
                     f"idea {idea['idea_id']} primary evidence presentation")
        for pending_line in pending_asset_lines(idea, request):
            require_line(section, pending_line, f"idea {idea['idea_id']} pending production assets")
        boundaries = tr("；").join(
            normalized_whitespace(item)
            for item in (idea.get("risks") or []) + (idea.get("must_differ") or [])
        ) or tr('无')
        require_line(
            section,
            (tr('- **不能越过：** ') + str(boundaries)),
            f"idea {idea['idea_id']} risks/must_differ",
        )

    gates = data.get("gates") or {}
    if ideas:
        gate_section = section_lines(markdown, tr('## 你来决定下一步'))
        for item in gates.get("selection_missing") or []:
            require_line(
                gate_section, (tr('- **选择前：** ') + str(gate_text(str(item), tr))), "selection gate",
            )
        for item in gates.get("production_missing") or []:
            require_line(
                gate_section, (tr('- **制作前：** ') + str(gate_text(str(item), tr))), "production gate",
            )
        for item in gates.get("blocked_reasons") or []:
            require_line(
                gate_section, (tr('- **阻塞：** ') + str(gate_text(str(item), tr))), "blocked reason",
            )
    else:
        blocked_section = section_lines(markdown, tr('## 当前阻塞'))
        for item in gates.get("blocked_reasons") or []:
            require_line(
                blocked_section, (tr('- **阻塞：** ') + str(gate_text(str(item), tr))), "blocked reason",
            )
        missing_section = section_lines(markdown, tr('## 需要补充'))
        for item in gates.get("selection_missing") or []:
            require_line(
                missing_section, (tr('- **选择前：** ') + str(gate_text(str(item), tr))), "selection gate",
            )
        for item in gates.get("production_missing") or []:
            require_line(
                missing_section, (tr('- **制作前：** ') + str(gate_text(str(item), tr))), "production gate",
            )

    evidence_section = section_lines(markdown, tr('## 证据边界'))
    for item in evidence_gaps(data):
        require_line(evidence_section, f"- {normalized_whitespace(item)}", "evidence gap")
    if ideas and data["status"] == "ready_for_selection":
        require_line(section_lines(markdown, tr('## 你来决定下一步')),
                     tr('回复编号即可选择；也可以说“改第 N 条”“换一批”或直接给自己的方向。'),
                     'localized selection action')
    elif not ideas:
        actions = data.get('interaction', {}).get('next_actions') or []
        if actions:
            require_line(section_lines(markdown, tr('## 下一步')),
                         tr('；').join(action_text(item, tr) for item in actions), 'localized next actions')
    if missing:
        raise ValueError(f"Creator-facing idea report dropped material boundaries: {missing}")


def concise(data: dict, request: dict | None = None) -> str:
    """Render the selection report with localized fields, gates, and next actions."""
    tr = translator(data)
    values = report_value_translations(data)
    display = lambda value: values.get(value, value)
    ideas = ordered_ideas(data)
    if not ideas:
        lines = [
            f"# {normalized_whitespace(data['overview']['headline'])}",
            "",
            (tr('状态：`') + normalized_whitespace(display(data['status'])) + tr('`；任务：`') + normalized_whitespace(display(data['task_intent'])) + tr('`；输入模式：`') + normalized_whitespace(display(data['source_mode'])) + tr('`。')),
            "",
            (tr('> **缺少这些信息的影响：** ') + str(normalized_whitespace(data['overview']['missing_context_effect']))),
            "",
        ]
        blocked = data.get("gates", {}).get("blocked_reasons") or []
        if blocked:
            lines.extend([tr('## 当前阻塞'), ""])
            lines.extend((tr('- **阻塞：** ') + str(gate_text(reason, tr))) for reason in blocked)
            lines.append("")
        selection_missing = data.get("gates", {}).get("selection_missing") or []
        production_missing = data.get("gates", {}).get("production_missing") or []
        if selection_missing or production_missing:
            lines.extend([tr('## 需要补充'), ""])
            lines.extend((tr('- **选择前：** ') + str(gate_text(item, tr))) for item in selection_missing)
            lines.extend((tr('- **制作前：** ') + str(gate_text(item, tr))) for item in production_missing)
            lines.append("")
        next_actions = data.get("interaction", {}).get("next_actions") or []
        if next_actions:
            lines.extend([tr('## 下一步'), "", tr("；").join(action_text(item, tr) for item in next_actions), ""])
        data_gaps = evidence_gaps(data)
        if data_gaps:
            lines.extend([tr('## 证据边界'), ""])
            lines.extend(f"- {normalized_whitespace(item)}" for item in data_gaps)
            lines.append("")
        markdown = "\n".join(lines)
        validate_concise(markdown, data, request)
        return markdown
    first = ideas[0]
    lines = [
        (tr('# 先拍：') + str(normalized_whitespace(first['working_title']))),
        "",
        (tr('> **先选它的原因：** ') + str(normalized_whitespace(first['why_for_creator']))),
        "",
        (tr('状态：`') + normalized_whitespace(display(data['status'])) + tr('`。')),
        "",
    ]
    if data["source_mode"] == "brief_only":
        lines.extend(
            [
                tr('> 证据边界：以下方向只来自你的 brief，是待验证假设；没有声称账号或趋势已验证。'),
                "",
            ]
        )
    if data["task_intent"] == "capacity_plan":
        estimates = [item["production_feasibility"].get("estimated_minutes") for item in ideas]
        if all(estimates):
            lines.extend([(tr('> 全部方案按上界合计 ') + str(sum((item['max'] for item in estimates))) + tr(' 分钟。')), ""])
    if data["task_intent"] == "rework_content":
        lines.extend(
            [
                tr('## 先看诊断'),
                "",
                (tr('> **诊断：** ') + str(normalized_whitespace(first['why_for_creator']))),
                "",
            ]
        )
    if data["overview"].get("portfolio_logic"):
        lines.extend([tr('## 这批内容怎么展开'), "", normalized_whitespace(data["overview"]["portfolio_logic"]), ""])
    lines.extend([tr('## 可选方向'), ""])
    recommended = data["overview"]["recommended_candidate_ids"]
    top_choice = recommended[0] if recommended else None
    for idea in ideas:
        suffix = tr('（首选）') if idea["idea_id"] == top_choice else ""
        hook = idea["hook_preview"]
        lines.extend(
            [
                f"### {idea['rank']}. {normalized_whitespace(idea['working_title'])}{suffix}",
                "",
                (tr('- **开口就说：** ') + str(normalized_whitespace(hook['opening_line']))),
            ]
        )
        if hook.get("progressive_line"):
            lines.append((tr('- **再推进：** ') + str(normalized_whitespace(hook['progressive_line']))))
        if idea.get("hook_options"):
            lines.append(tr('- **三个不同机制：**'))
            for option in idea["hook_options"]:
                lines.append(
                    f"  - {normalized_whitespace(option['mechanism'])}: "
                    f"{normalized_whitespace(option['opening_line'])}"
                    f" ({normalized_whitespace(option['rationale'])})"
                )
        lines.extend(
            [
                (tr('- **为什么适合：** ') + str(normalized_whitespace(idea['why_for_creator']))),
                (tr('- **创作者适配：** ') + str(normalized_whitespace(idea['creator_fit']['rationale']))),
                (tr('- **怎么拍：** ') + str(shoot_text(idea))),
                (tr('- **制作量：** ') + str(minute_text(idea['production_feasibility'], tr, display))),
                (tr('- **依据：** ') + str(evidence_text(idea, tr, display))),
                (tr('- **不能越过：** ') + str(tr('；').join((normalized_whitespace(item) for item in idea['risks'] + idea['must_differ'])) or tr('无'))),
                "",
            ]
        )
        lines.extend(pending_asset_lines(idea, request))
        if pending_asset_lines(idea, request):
            lines.append("")
    selection_questions = data["gates"]["selection_missing"]
    production_questions = data["gates"]["production_missing"]
    lines.extend([tr('## 你来决定下一步'), ""])
    if data["status"] == "ready_for_selection":
        lines.append(tr('回复编号即可选择；也可以说“改第 N 条”“换一批”或直接给自己的方向。'))
    elif selection_questions:
        lines.append(tr('先回答下面的选择门问题；回答后再选择，不会自动进入脚本：'))
        for item in selection_questions:
            lines.append((tr('- **选择前：** ') + str(gate_text(item, tr))))
    else:
        lines.append(tr('当前输入被安全门阻塞；补齐声明缺口后再生成或选择。'))
        for item in data["gates"].get("blocked_reasons") or []:
            lines.append((tr('- **阻塞：** ') + str(gate_text(item, tr))))
    if production_questions:
        lines.extend(["", tr('选择后、制作前再确认：')])
        for item in production_questions:
            lines.append((tr('- **制作前：** ') + str(gate_text(item, tr))))
    data_gaps = evidence_gaps(data)
    if data_gaps:
        lines.extend(["", tr('## 证据边界'), ""])
        lines.extend(f"- {normalized_whitespace(item)}" for item in data_gaps)
    lines.append("")
    markdown = "\n".join(lines)
    validate_concise(markdown, data, request)
    return markdown


def full(data: dict, request: dict | None = None) -> str:
    """Render the complete idea report, including localized empty values and interactions."""
    tr = translator(data)
    values = report_value_translations(data)
    display = lambda value: values.get(value, value)
    lines = [
        tr('# Idea 决策与证据报告'),
        "",
        (tr('状态：`') + normalized_whitespace(display(data['status'])) + tr('`；任务：`') + normalized_whitespace(display(data['task_intent'])) + tr('`；输入模式：`') + normalized_whitespace(display(data['source_mode'])) + tr('`。')),
        "",
    ]
    for idea in ordered_ideas(data):
        lines.extend(
            [
                f"## {idea['rank']}. {idea['working_title']}",
                "",
                (tr('- 推荐理由：') + str(idea['why_for_creator'])),
                f"{tr('- Audience problem：')}{idea['audience_problem']}",
                f"{tr('- Promise：')}{idea['content_promise']}",
                f"{tr('- Angle：')}{idea['angle']}",
                f"{tr('- Main Hook：')}{idea['hook_preview']['opening_line']}",
                f"{tr('- Progressive Hook：')}{idea['hook_preview'].get('progressive_line') or tr('无')}",
                f"{tr('- Shoot：')}{shoot_text(idea)}",
                f"{tr('- Time：')}{minute_text(idea['production_feasibility'], tr, display)}",
                f"{tr('- Assets：')}{', '.join(idea['production_feasibility']['assets_used']) or tr('无')}",
                *pending_asset_lines(idea, request),
                f"{tr('- Primary evidence：')}{evidence_text(idea, tr, display)}",
                "",
                tr('### Evidence lineage'),
                "",
                f"{tr('- source context refs: ')}{', '.join(idea['source_context_refs']) or tr('无')}",
                f"{tr('- evidence refs: ')}{', '.join(idea['evidence_refs']) or tr('无')}",
                "",
                tr('### Outline'),
                "",
            ]
        )
        for beat in idea["content_outline"]:
            lines.append(f"{beat['order']}. **{beat['beat']}** — {beat['purpose']}")
        if idea.get("hook_options"):
            lines.extend(["", tr('### Hook options'), ""])
            for option in idea["hook_options"]:
                lines.append(
                    f"- `{option['option_id']}` {option['mechanism']}: {option['opening_line']} — {option['rationale']}"
                )
        lines.extend(["", tr('### Risks / boundaries'), ""])
        for risk in idea["risks"]:
            lines.append(f"- {risk}")
        for boundary in idea["must_differ"]:
            lines.append(f"{tr('- Must differ：')}{boundary}")
        lines.append("")
    lines.extend(
        [
            tr('## Gates'),
            "",
            f"```json\n{json.dumps({key: [gate_text(value, tr) for value in values] for key, values in data['gates'].items()}, ensure_ascii=False, indent=2)}\n```",
            "",
            tr('## Evidence summary'),
            "",
            f"```json\n{json.dumps({**data['evidence_summary'], 'data_gaps': evidence_gaps(data)}, ensure_ascii=False, indent=2)}\n```",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("idea_set", type=Path)
    parser.add_argument("--request", type=Path, help="Optional validated request for resource availability")
    parser.add_argument("--concise", type=Path, required=True)
    parser.add_argument("--full", type=Path, required=True)
    args = parser.parse_args()
    data = load(args.idea_set)
    request = load(args.request) if args.request else None
    args.concise.write_text(concise(data, request), encoding="utf-8")
    args.full.write_text(full(data, request), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
