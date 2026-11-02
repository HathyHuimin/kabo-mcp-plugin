#!/usr/bin/env python3
"""Write the report: code-owned conclusion, evidence and limits around the model's written analysis."""
import argparse
import json
import math
import sys
from pathlib import Path

sys.dont_write_bytecode = True
from analyze_traffic import build, fields, load, require, text
from render_traffic import coverage_gaps, snapshot_context
from creator_evidence import evidence as creator_evidence


FINAL_BEGIN = "KABO_FINAL_REPLY_BEGIN"
FINAL_END = "KABO_FINAL_REPLY_END"


def answer_text(finding, answer, zh=False):
    """Render quantitative verdicts from assessed facts, not retyped numbers."""
    if isinstance(answer, dict):
        fields(answer, {"ref"})
        if answer["ref"] == "traffic_verdict":
            refs = {"recent_drop": "performance_change_verdict", "growth": "growth_verdict",
                    "traffic_sources": "traffic_source_change_verdict"}
            messages = []
            for fact in finding.get("facts", []):
                kind = finding.get("kind")
                comparable = (fact.get("change") or {}).get("ratio" if kind == "recent_drop" else "difference")
                if kind == "traffic_sources":
                    comparable = (fact.get("share_change") or {}).get("difference")
                measured = type(comparable) in (int, float) and math.isfinite(comparable)
                if kind in refs and (measured or (kind == "growth" and fact.get("target_evaluation"))):
                    message = answer_text({**finding, "facts": [fact]}, {"ref": refs[kind]}, zh)
                    if len(finding["facts"]) > 1:
                        message = str(fact.get("format", fact.get("source", fact["type"]))) + ": " + message
                    messages.append(message)
            if messages:
                return "\n\n".join(messages)
            return ("现有记录尚不足以完成所需比较；下方证据与缺口说明可判断的范围，不能据此确认原因或平台限制。" if zh else
                    "The available records do not yet support the requested comparison. The evidence and gaps below delimit what is known; they do not establish a cause or platform enforcement.")
        if answer["ref"] == "performance_change_verdict":
            require(finding.get("kind") == "recent_drop", "performance change verdict requires a recent-drop question")
            performance = [fact for fact in finding.get("facts", []) if fact.get("type") == "performance"]
            require(len(performance) == 1, "performance change verdict requires exactly one assessed performance fact")
            fact = performance[0]
            change = fact.get("change")
            require(isinstance(change, dict) and type(change.get("ratio")) in (int, float)
                    and math.isfinite(change["ratio"]),
                    "performance change verdict requires a finite comparison ratio")
            lower = change["ratio"] < 1
            limits = set(fact.get("limits", []))
            age_limited = bool(limits & {"different_observation_ages", "unknown_observation_ages"})
            if age_limited:
                messages = (
                    ("所选近期队列的当前累计中位数较前期低，但观察年龄不一致；这只能描述当前快照，不能确认同龄下滑、原因或平台限制。",
                     "The selected recent cohort has a lower current cumulative median, but the records have different observation ages. This snapshot cannot establish a same-age decline, its cause, or platform enforcement."),
                    ("所选近期队列的当前累计中位数未低于前期，但观察年龄不一致；当前快照没有复现同龄下滑，也不能确认正常波动、原因或平台限制。",
                     "The selected recent cohort does not have a lower current cumulative median, but the records have different observation ages. This snapshot does not establish a same-age decline, normal variation, its cause, or platform enforcement."),
                )
                return messages[0 if lower else 1][0 if zh else 1]
            messages = (
                ("同口径同龄比较显示近期中位数较低，构成表现下降信号；它仍不能确定原因或平台限制。",
                 "The same-basis, same-age comparison shows a lower recent median, which is a performance-decline signal; it still does not establish the cause or platform enforcement."),
                ("同口径同龄比较未显示近期中位数下降；它仍不能确定未来趋势、原因或平台限制。",
                 "The same-basis, same-age comparison does not show a lower recent median; it still does not establish the future trend, cause, or platform enforcement."),
            )
            return messages[0 if lower else 1][0 if zh else 1]
        if answer["ref"] == "traffic_source_change_verdict":
            require(finding.get("kind") == "traffic_sources",
                    "traffic source verdict requires a traffic-source question")
            facts = [fact for fact in finding.get("facts", []) if fact.get("type") == "traffic_sources"]
            require(len(facts) == 1, "traffic source verdict requires exactly one assessed source fact")
            fact = facts[0]
            share = (fact.get("share_change") or {}).get("difference")
            per_post = (fact.get("per_post_count_change") or {}).get("difference")
            require(type(share) in (int, float) and math.isfinite(share),
                    "traffic source verdict requires a measured share change")
            if type(per_post) in (int, float) and math.isfinite(per_post):
                share_word = ("下降" if share < 0 else "上升" if share > 0 else "持平") if zh else (
                    "fell" if share < 0 else "rose" if share > 0 else "was unchanged")
                count_word = ("下降" if per_post < 0 else "上升" if per_post > 0 else "持平") if zh else (
                    "fell" if per_post < 0 else "rose" if per_post > 0 else "was unchanged")
                return (f"同窗口比较中，该来源占比{share_word}，估算的单条来源量也{count_word}；这是来源结构与表现信号，不能单独证明平台限制。"
                        if zh else
                        f"On the matched window, this source's share {share_word} and its estimated per-record source amount {count_word}. This is a source-mix and performance signal, not proof of platform enforcement.")
            return ("现有同窗口数据可以判断该来源占比变化，但缺少匹配的总量，不能确定绝对来源量如何变化，也不能据此证明平台限制。"
                    if zh else
                    "The matched-window data establishes the source-share change, but without matching totals it cannot establish the absolute source-amount change or platform enforcement.")
        if answer["ref"] == "growth_verdict":
            require(finding.get("kind") == "growth", "growth verdict requires a growth question")
            facts = [fact for fact in finding.get("facts", [])
                     if fact.get("type") in {"performance", "period_totals"}]
            require(len(facts) == 1, "growth verdict requires exactly one assessed growth fact")
            fact = facts[0]
            target = fact.get("target_evaluation")
            if target is not None:
                last = target["groups"][-1]
                if target["basis"] == "each_record":
                    met = last["met_n"] == last["n"] and last["n"] > 0
                else:
                    met = bool(last["met"])
                return (("所选末期记录已达到用户定义的目标；当前证据仍只覆盖这些同口径阶段，是否稳定需要后续同龄记录复核。" if met else
                         "所选末期记录尚未达到用户定义的目标；孤立高值不能替代逐条或典型表现目标，具体瓶颈仍需对应的分发与观看证据。")
                        if zh else
                        ("The selected latest records meet the user's defined target. The evidence still covers only these matched-basis periods, so stability needs later same-age records." if met else
                         "The selected latest records do not meet the user's defined target. An isolated peak does not replace a per-record or typical-performance target, and the specific bottleneck still needs matching distribution and viewing evidence."))
            change = (fact.get("change") or {}).get("difference")
            require(type(change) in (int, float) and math.isfinite(change),
                    "growth verdict requires a finite period comparison")
            if fact["type"] == "period_totals":
                if zh:
                    direction = "提高" if change > 0 else "下降" if change < 0 else "没有变化"
                    return f"所选阶段的周期总量{direction}；这不等于单条表现也同样变化，也不能证明发布数量是原因。"
                direction = "improved" if change > 0 else "declined" if change < 0 else "was unchanged"
                return (f"The period total across the selected stages {direction}. "
                        "This does not establish the same change in per-post performance or prove posting volume as the cause.")
            direction = ("提高" if change > 0 else "下降" if change < 0 else "没有变化") if zh else (
                "improved" if change > 0 else "declined" if change < 0 else "was unchanged")
            return (f"所选阶段的典型表现{direction}；这只描述这些记录，不能单独确定长期趋势或具体原因。" if zh else
                    f"Typical performance across the selected periods {direction}. This describes only these records and does not by itself establish a long-term trend or its cause.")
        require(answer["ref"] == "audience_target_verdict" and finding.get("audience_target") is not None,
                "unknown structured answer reference")
        status = finding.get("audience_verdict", "unmeasured")
        provisional = finding["audience_target"]["definition"]["status"] == "provisional"
        messages = ({
            "met": ("同口径受众数据达到暂定操作目标；该目标仍待用户确认，且这不证明平台内部归类或处罚。",
                    "The same-basis audience data meets the provisional operating target, which still needs user confirmation and does not establish an internal platform category or penalty."),
            "not_met": ("同口径受众数据尚未达到暂定操作目标；该目标仍待用户确认，且这不证明平台内部归类或处罚。",
                        "The same-basis audience data does not meet the provisional operating target, which still needs user confirmation and does not establish an internal platform category or penalty."),
            "baseline_only": ("当前同口径受众数据可作为暂定目标基线，但该目标仍待用户确认，单个窗口也不能判断是否正在改善。",
                              "The current same-basis audience data can serve as a provisional target baseline, but the target still needs user confirmation and one window cannot establish improvement."),
            "unknown": ("当前同口径受众数据不足以判断是否达到暂定操作目标；该目标仍待用户确认，也不能据此判断平台内部归类或处罚。",
                        "The current same-basis audience data cannot establish whether the provisional operating target is met; the target still needs user confirmation and does not establish an internal platform category or penalty."),
            "mixed": ("不同同口径受众记录对暂定目标的判断不一致；该目标仍待用户确认，也不能据此判断平台内部归类或处罚。",
                      "Same-basis audience records give mixed results for the provisional target, which still needs user confirmation and does not establish an internal platform category or penalty."),
            "unmeasured": ("现有证据没有按暂定目标的维度和统计对象测量实际受众；该目标仍待用户确认，匹配情况尚无法判断。",
                           "The available evidence does not measure the actual audience on the provisional target's dimension and population; the target still needs user confirmation and fit remains unassessed."),
        } if provisional else {
            "met": ("同口径受众数据达到用户定义的目标；这描述实际触达结果，不证明平台内部归类或处罚。",
                    "The same-basis audience data meets the user's stated target. This describes observed reach, not an internal platform category or penalty."),
            "not_met": ("同口径受众数据尚未达到用户定义的目标；这描述实际触达结果，不证明平台内部归类或处罚。",
                        "The same-basis audience data does not yet meet the user's stated target. This describes observed reach, not an internal platform category or penalty."),
            "baseline_only": ("当前同口径受众数据可作为目标基线，但单个窗口不能判断目标是否正在改善，也不能证明平台内部归类或处罚。",
                              "The current same-basis audience data can serve as the target baseline, but one window cannot establish improvement or an internal platform category or penalty."),
            "unknown": ("当前同口径受众数据不足以确定是否达到用户定义的目标，也不能证明平台内部归类或处罚。",
                        "The current same-basis audience data cannot establish whether the user's target is met or an internal platform category or penalty."),
            "mixed": ("不同同口径受众记录对目标的判断不一致，不能合并成一个已达成或未达成结论，也不能证明平台内部归类或处罚。",
                      "Same-basis audience records give mixed target results; they cannot be collapsed into a met/not-met verdict or establish an internal platform category or penalty."),
            "unmeasured": ("现有证据没有按目标的维度和统计对象测量实际受众，因此不能判断是否与目标匹配，也不能据此判断平台内部归类或处罚。",
                           "The available evidence does not measure the actual audience on the target dimension and population, so target fit and any internal platform category or penalty remain unassessed."),
        })
        require(status in messages, "unknown audience verdict")
        return messages[status][0 if zh else 1]
    raise ValueError("unknown structured answer reference")


def verdict(finding: dict, zh: bool) -> str:
    ref = "audience_target_verdict" if finding.get("audience_target") is not None else "traffic_verdict"
    return answer_text(finding, {"ref": ref}, zh)


def compose(assessment: dict, analyses: dict, language="zh-CN") -> str:
    """Code writes the conclusion, evidence and limits; the model's written analysis fills the rest."""
    zh = language == "zh-CN"
    gaps = [gap for gap in coverage_gaps(assessment) if gap != "imported_public_snapshot_not_fixed_age_history"]
    parts = []
    for finding in assessment["questions"]:
        analysis = analyses.get(finding["id"], {})
        section = ["## " + finding["ask"], "", verdict(finding, zh)]
        if analysis.get("interpretation"):
            section += ["", analysis["interpretation"]]
        need = analysis.get("need")
        if need:
            section += ["", "### 先补充这些数据" if zh else "### Data to add first"]
            section += ["- " + item for item in need["items"]]
            if need.get("why"):
                section += ["", ("为什么：" if zh else "Why: ") + need["why"]]
        steps = analysis.get("next_steps") or []
        if steps:
            section += ["", "### 建议的下一步（尚未执行）" if zh else "### Suggested next steps (not yet performed)"]
            labels = (("看什么", "结果怎么用", "开头方案") if zh else ("What to check", "Then", "Opening"))
            for step in steps:
                section += ["", "**" + step["do"] + "**"]
                section += [f"- {label}：{step[key]}" if zh else f"- {label}: {step[key]}"
                            for label, key in zip(labels, ("check", "then", "opening")) if step.get(key)]
        elif finding.get("intent") == "act" and not need:
            section += ["", "本次没有形成可执行的下一步。" if zh else "No next step was formed this time."]
        body = creator_evidence(finding, zh, analysis.get("detail") == "full")
        if body:
            section += ["", body.rstrip()]
        if analysis.get("hypotheses"):
            section += ["", "### 可能解释（尚未证实）" if zh else "### Possible explanations (unconfirmed)"]
            section += ["- " + h for h in analysis["hypotheses"]]
        if analysis.get("notes"):
            section += ["", "部分分析未通过数字核对或内容不完整，已省略。" if zh else
                        "Part of the analysis failed the number check or was incomplete and was left out."]
        parts.append("\n".join(section))
    return (("# 流量问题分析\n\n这些数值描述表现，不证明平台限流或具体原因。\n\n" if zh else
             "# Traffic analysis\n\nThese measurements describe performance, not platform enforcement or its causes.\n\n")
            + (snapshot_context(assessment, zh) + "\n\n" if assessment.get("imported_snapshot") else "")
            + (("样本与证据限制：\n" if zh else "Coverage gaps:\n")
               + "\n".join("- " + gap for gap in gaps) + "\n\n" if gaps else "")
            + "\n\n".join(parts) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("assessment", type=Path)
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--analysis", type=Path)
    parser.add_argument("--synthesis", type=Path, required=True)
    parser.add_argument("--language", choices=["en", "zh-CN"], default="zh-CN")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        assessment = load(args.assessment)
        source_analysis = load(args.analysis) if args.analysis else None
        require(assessment == build(load(args.case), source_analysis),
                "assessment is not bound to the provided source inputs")
        from synthesis import read
        analyses, _ = read(args.synthesis.read_text(encoding="utf-8"), assessment, source_analysis)
        report = compose(assessment, analyses, args.language)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
        print(json.dumps({"status": "ok", "creator_report": str(args.output)}, ensure_ascii=False), file=sys.stderr)
        # Frame the exact creator reply so the host can relay one bounded payload
        # without a second synthesis. The saved report remains plain Markdown.
        print(FINAL_BEGIN)
        print(report, end="")
        print(FINAL_END)
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
