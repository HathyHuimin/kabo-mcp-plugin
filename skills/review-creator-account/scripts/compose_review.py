#!/usr/bin/env python3
"""Bind one semantic synthesis to deterministic AccountAnalysis evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

# Importable both as `python3 scripts/compose_review.py` and when a test loads this file by
# path, where the interpreter would not otherwise have the sibling module on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Sibling imports must not leave a __pycache__ in the signed, checksummed skill directory.
sys.dont_write_bytecode = True
from extract_caption_phrases import caption_texts, extract_caption_phrases, extract_hashtags
from json_io import load_complete_envelope, load_complete_object
from public_identity import identity_from_envelope, identity_from_scope, merge_identity, quote_bio
from profile_contract import synthesis_schema, validate_profile
from estimate_growth import estimate, with_snapshot, time as growth_time
from account_analyzers import normalize_format
from synthesis_authoring import validate_authoring
from evidence_context import maturity_by_format, evidence_age


# Localize presentation only; preserve source titles, numbers and native evidence facts.
TEXT = {
    "scope_limits": "Scope and limits: ",
    "window": "Window: {start}–{end}",
    "window_start": "Window start: {value}",
    "window_end": "Window end: {value}",
    "scope_separator": "; ",
    "works": "What works",
    "hurts": "What is holding the account back",
    "next": "What to do next",
    "next_primary": "Do this first",
    "next_tests": "Worth testing",
    "next_stop": "Hold off on",
    "criterion": "Success criterion",
    "evidence_views": ("{value} view", "{value} views"),
    "evidence_likes": ("{value} like", "{value} likes"),
    "evidence_comments": ("{value} comment", "{value} comments"),
    "evidence_interact": "{value:.2f}% interactions/view",
    "multiple": "{value:.2f}x the creator's same-format median",
    "provisional_format": "short/long format inferred from duration, not a platform label",
    "published_at": "published {value}",
    "too_recent": "younger than the baseline cutoff {value}; cumulative counts are not an equal-age performance comparison",
    "maturity_unavailable": "no comparable age baseline; cumulative counts only",
    "identity_bio": "The public bio reads \"{bio}\".",
    "identity_followers": "About {followers} public followers.",
}

ZH_TEXT = {
    "scope_limits": "本轮范围与限制：",
    "window": "样本时间范围：{start}–{end}",
    "window_start": "样本起始时间：{value}",
    "window_end": "样本结束时间：{value}",
    "scope_separator": "；",
    "works": "什么有效", "hurts": "什么在拖后腿", "next": "接下来",
    "next_primary": "先做这一件", "next_tests": "可以测试的", "next_stop": "先不要做",
    "criterion": "判断标准",
    "evidence_views": ("{value} 次观看", "{value} 次观看"),
    "evidence_likes": ("{value} 个赞", "{value} 个赞"),
    "evidence_comments": ("{value} 条评论", "{value} 条评论"),
    "evidence_interact": "互动数 / 播放量 {value:.2f}%",
    "multiple": "本人同格式中位数的 {value:.2f} 倍",
    "provisional_format": "短/长视频格式按时长推断，并非平台标记",
    "published_at": "发布于 {value}",
    "too_recent": "晚于基线截止 {value}，累计数不代表等观察期表现比较",
    "maturity_unavailable": "无可比日龄基线，仅展示累计数",
    "identity_bio": "公开主页写着「{bio}」。",
    "identity_followers": "公开粉丝约 {followers}。",
}


def language_text(language: str) -> dict[str, Any]:
    # Other valid BCP-47 tags retain English artifact labels; delivery may translate
    # fixed descriptions only, preserving all analytical strings and evidence.
    return ZH_TEXT if language.lower() in {"zh", "zh-cn", "zh-sg", "zh-hans", "zh-hans-cn", "zh-hans-sg"} else TEXT


# Creator-facing names from the platform's public account-research capability catalog.
# Only these exact connector/provider pairs are public; all other source identities stay guarded.
PUBLIC_PROVIDER_LABELS = {
    "instagram-discovery": "Instagram discovery",
    "youtube-public": "YouTube public data",
    "tiktok-public": "TikTok public data",
}
# ASCII-only boundaries: Chinese prose often abuts the id directly ("来自instagram-discovery的"), and
# the delivery guard matches case-insensitively, so the substitution does too.
PUBLIC_CONNECTOR_ID = re.compile(
    r"(?<![A-Za-z0-9_-])(" + "|".join(re.escape(key) for key in PUBLIC_PROVIDER_LABELS) + r")(?![A-Za-z0-9_-])",
    re.IGNORECASE,
)


def relabel_public_connectors(value: Any) -> Any:
    """Replace public connector ids the model wrote into semantic text with their public labels.

    A model sometimes names its source by connector id ("instagram-discovery limited pagination").
    The id is an internal identifier the delivery guard rejects, while the label it maps to is the
    public capability name, so substituting here keeps the authored meaning and removes the leak
    deterministically instead of failing the whole delivery. Unknown identities are left untouched
    for the guard to catch.
    """
    if isinstance(value, str):
        if value.startswith(("http://", "https://")):
            return value  # evidence links stay literal
        return PUBLIC_CONNECTOR_ID.sub(lambda match: PUBLIC_PROVIDER_LABELS[match.group(1).lower()], value)
    if isinstance(value, list):
        return [relabel_public_connectors(item) for item in value]
    if isinstance(value, dict):
        return {key: relabel_public_connectors(item) for key, item in value.items()}
    return value


REPORT_SCOPE_KEYS = ("snapshot_id", "platform", "source_mode", "account", "window", "observed_at")

PLATFORM_STATUS_CHECK = {
    "title": "Rule out platform restrictions first",
    "youtube": "YouTube shows explicit status per video. Check each underperforming upload in YouTube Studio → Content → Restrictions for copyright, age, or monetization restrictions; stronger videos on the same channel do not clear it.",
    "tiktok": "TikTok shows explicit recommendation status per video. Check each underperforming upload in Analytics → Performance for an ineligible-for-For-You notice and appeal option; stronger videos on the same account do not clear it.",
    "instagram": "Instagram's explicit recommendation-eligibility surface is account-level Account Status. Open Settings → Account → Account Status and check recommendation eligibility, feature restrictions, and removed content before interpreting post performance.",
}


ZH_PLATFORM_STATUS_CHECK = {
    "title": "先排查平台限制",
    "youtube": "YouTube 按视频显示明确状态。先在 YouTube Studio → Content → Restrictions 中逐条检查表现较弱的视频是否有版权、年龄或变现限制；同频道其他视频表现较好，不能排除该条受限。",
    "tiktok": "TikTok 按视频显示明确的推荐状态。先在 Analytics → Performance 中逐条检查表现较弱的视频是否有不适合进入推荐页的提示及申诉入口；同账号其他视频表现较好，不能排除该条受限。",
    "instagram": "Instagram 的推荐资格在账号级「账号状态」中显示。先打开设置 → 账号 → 账号状态，检查推荐资格、功能限制及被移除内容，再解读帖子表现。",
}


def load(path: Path) -> dict[str, Any]:
    return load_complete_object(path)


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256(":".join(parts).encode()).hexdigest()[:16]
    return f"{prefix}-{digest}"


def resolve_patterns(patterns: list[dict[str, Any]], rankings: dict[str, dict[str, Any]], policies: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    resolved = []
    for pattern in patterns:
        examples = []
        for content_id in pattern["content_ids"]:
            if content_id not in rankings:
                raise ValueError(f"synthesis references unknown content_id: {content_id}")
            row = rankings[content_id]
            examples.append({
                "content_id": content_id,
                "title": row["title"],
                "url": row.get("url"),
                "format": row["format"],
                **evidence_age(row, policies),
                **({"format_confidence": row["format_confidence"]} if "format_confidence" in row else {}),
                **({"format_source": row["format_source"]} if "format_source" in row else {}),
                "metrics": dict(row["metrics"]),
                "view_multiple_vs_format_median": row.get("view_multiple_vs_format_median"),
            })
        resolved.append({"pattern": pattern["pattern"], "explanation": pattern["explanation"], "claim_type": pattern["claim_type"], "examples": examples})
    return resolved


NICHE_MAX_CHARS = 120


def normalize_niche(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("profile.niche must be one English sentence")
    text = " ".join(value.split())
    if not text:
        raise ValueError("profile.niche must be one English sentence")
    if len(text) > NICHE_MAX_CHARS:
        raise ValueError(f"profile.niche must be at most {NICHE_MAX_CHARS} characters")
    return text


def build_profile(analysis: dict[str, Any], synthesis: dict[str, Any]) -> dict[str, Any]:
    scope = analysis["scope"]
    account = scope.get("account", "unknown")
    profile_id = stable_id("creator", analysis["platform"], account)
    supplied = synthesis.get("profile", {})
    if not isinstance(supplied, dict):
        raise ValueError("profile must be object")
    allowed = set(synthesis_schema()["properties"]["profile"]["properties"])
    unknown = sorted(set(supplied) - allowed)
    if unknown:
        raise ValueError(
            "profile contains unsupported fields instead of a slim stable CreatorProfile: "
            + ", ".join(unknown)
        )
    supplied = dict(supplied)
    if "niche" in supplied:
        supplied["niche"] = normalize_niche(supplied["niche"])
    validate_profile(supplied, synthesis_fields=True)
    profile = {
        "schema_version": "creator-profile.v3",
        "profile_id": profile_id,
        "accounts": [{"platform": analysis["platform"], "handle": account}],
    }
    profile.update({key: value for key, value in supplied.items() if key in allowed and value not in (None, {}, [])})
    # Synthesis rewrites captions into prose and, when the report language differs from the
    # content language, translates them. Downstream retrieval matches literally against
    # captions, so the Creator's own wording is attached here from the analyzer evidence
    # instead of being reconstructed from the prose.
    captions = caption_texts(analysis)
    phrases = extract_caption_phrases(captions)
    if phrases:
        content_profile = dict(profile.get("content_profile") or {})
        content_profile["caption_phrases"] = phrases
        profile["content_profile"] = content_profile
    hashtags = extract_hashtags(captions)
    if hashtags:
        extensions = dict(profile.get("extensions") or {})
        style = dict(extensions.get("content_style") or {})
        style.setdefault("hashtags", hashtags)
        extensions["content_style"] = style
        profile["extensions"] = extensions
    validate_profile(profile)
    return profile


def compact_title(value: str, limit: int = 96) -> str:
    """Keep links readable while preserving the suffix that often distinguishes episodes."""
    title = " ".join(value.split())
    if len(title) <= limit:
        return title
    suffix_length = min(32, max(16, limit // 3))
    prefix_length = limit - suffix_length - 1
    return title[:prefix_length].rstrip() + "…" + title[-suffix_length:].lstrip()


def compact_count(value: int | float) -> str:
    """Render public counts for people; native JSON retains the exact number."""
    number = float(value)
    for threshold, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if abs(number) >= threshold:
            scaled = f"{number / threshold:.1f}".rstrip("0").rstrip(".")
            return scaled + suffix
    if number.is_integer():
        return f"{int(number):,}"
    return f"{number:,.2f}".rstrip("0").rstrip(".")


def metric_value(metrics: dict[str, Any], name: str) -> float | None:
    raw = (metrics or {}).get(name)
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, dict) and raw.get("status") in {"available", "zero"}:
        value = raw.get("value")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return None


def should_show_platform_status(report: dict[str, Any]) -> bool:
    """Keep the canned restriction page only when the public sample is empty or blocked."""
    coverage = report.get("data_coverage") or {}
    if coverage.get("content_total") in {None, 0}:
        return True
    return report.get("status") == "blocked"


def counted(key: str, value: float, text: dict[str, Any] = TEXT) -> str:
    singular, plural = text[key]
    return (singular if value == 1 else plural).format(value=compact_count(value))


def evidence_line(item: dict[str, Any], text: dict[str, Any] = TEXT) -> str:
    label = compact_title(item["title"])
    link = f"[{label}]({item['url']})" if item.get("url") else label
    facts = []
    metrics = item.get("metrics") or {}
    views = metric_value(metrics, "views")
    likes = metric_value(metrics, "likes")
    comments = metric_value(metrics, "comments")
    if views is not None:
        facts.append(counted("evidence_views", views, text))
    if likes is not None:
        facts.append(counted("evidence_likes", likes, text))
    if comments is not None:
        facts.append(counted("evidence_comments", comments, text))
    if views and views > 0 and (likes is not None or comments is not None):
        interactions = (likes or 0) + (comments or 0)
        facts.append(text["evidence_interact"].format(value=round(interactions / views * 100 + 1e-12, 2)))
    if item.get("view_multiple_vs_format_median") is not None:
        facts.append(text["multiple"].format(value=item["view_multiple_vs_format_median"]))
    if item.get("published_at"):
        facts.append(text["published_at"].format(value=item["published_at"]))
    if item.get("baseline_maturity") == "too_recent":
        facts.append(text["too_recent"].format(value=item["baseline_cutoff_at"]))
    elif item.get("baseline_maturity") == "unavailable":
        facts.append(text["maturity_unavailable"])
    if (
        item.get("view_multiple_vs_format_median") is not None
        and item.get("format_confidence") == "provisional"
    ):
        facts.append(text["provisional_format"])
    return link + (" (" + ", ".join(facts) + ")" if facts else "")


def identity_line(identity: dict[str, Any], text: dict[str, Any] = TEXT) -> str:
    parts = []
    biography = identity.get("biography")
    if isinstance(biography, str) and biography.strip():
        parts.append(text["identity_bio"].format(bio=quote_bio(biography)))
    followers = identity.get("followers")
    if followers is not None:
        parts.append(text["identity_followers"].format(followers=compact_count(followers)))
    return ("" if text is ZH_TEXT else " ").join(parts)


def render_action(item: dict[str, Any], prefix: str, text: dict[str, Any] = TEXT) -> str:
    rendered = f"{prefix}{item['action']}"
    if item.get("success_metric"):
        rendered += f" ({text['criterion']}: {item['success_metric']})"
    return rendered


def render_next_section(report: dict[str, Any], text: dict[str, Any] = TEXT) -> list[str]:
    recommendations = report.get("recommendations") or {}
    primary = list(recommendations.get("continue") or [])
    tests = list(recommendations.get("test") or [])
    stops = list(recommendations.get("stop") or [])
    lines = [f"## {text['next']}", ""]
    if primary:
        lines += [f"**{text['next_primary']}**", ""]
        for item in primary:
            lines.append(render_action(item, "- ", text))
        lines.append("")
    if tests:
        lines += [f"**{text['next_tests']}**", ""]
        if len(tests) == 1:
            lines.append(render_action(tests[0], "- ", text))
        else:
            for index, item in enumerate(tests, 1):
                lines.append(render_action(item, f"{index}. ", text))
        lines.append("")
    if stops:
        lines += [f"**{text['next_stop']}**", ""]
        for item in stops:
            lines.append(render_action(item, "- ", text))
        lines.append("")
    return lines


def scope_limits_line(report: dict[str, Any], text: dict[str, Any] = TEXT) -> str:
    """Project existing dates and authored gaps without inferring missing boundaries."""
    parts = []
    window = (report.get("scope") or {}).get("window") or {}
    start, end = window.get("start"), window.get("end")
    if start and end:
        parts.append(text["window"].format(start=start, end=end))
    elif start:
        parts.append(text["window_start"].format(value=start))
    elif end:
        parts.append(text["window_end"].format(value=end))
    parts.extend(report.get("data_gaps") or [])
    return text["scope_limits"] + text["scope_separator"].join(parts) if parts else ""


def growth_for_review(analysis: dict[str, Any], request: dict[str, Any] | None,
                      snapshot: dict[str, Any] | None, question: str | None) -> dict[str, Any] | None:
    """Recompute from this review's retained evidence, never from an authored result."""
    if request is None and snapshot is None and question is None:
        return None
    if not isinstance(request, dict) or not isinstance(snapshot, dict) or not isinstance(question, str) or not question.strip():
        raise ValueError("growth requires input, retained snapshot and original question together")
    if request.get("mode") != "content_scenario":
        raise ValueError("review growth requires content_scenario bound to its retained public snapshot")
    if "_snapshot_cadence" in request:
        raise ValueError("growth cadence must be derived from the retained snapshot")
    if not isinstance(analysis.get("source_mode"), str) or not analysis["source_mode"].startswith("public"):
        raise ValueError("review growth requires public snapshot analysis")
    if snapshot.get("snapshot_id") != analysis["snapshot_id"]:
        raise ValueError("growth snapshot_id does not match analysis")
    scope = request.get("scope") or {}
    if scope.get("platform") != analysis["platform"] or scope.get("account") != analysis["scope"]["account"]:
        raise ValueError("growth account/platform does not match analysis")
    if growth_time(snapshot["observed_at"]) != growth_time(analysis["scope"]["observed_at"]):
        raise ValueError("growth snapshot observation time does not match analysis")
    # The analyzer normalizes public format aliases (e.g. instagram_feed -> feed).
    # Apply that same adapter before cohort/cadence selection; do not make a valid
    # analyzer-selected format silently produce an empty growth cohort.
    normalized_snapshot = {**snapshot, "items": [
        {**row, "format": normalize_format(row.get("format"))} for row in snapshot.get("items", [])
    ]}
    normalized_request = {**request, "cohort": {
        **request.get("cohort", {}), "format": normalize_format((request.get("cohort") or {}).get("format"))
    }}
    bound = with_snapshot(normalized_request, normalized_snapshot, "snapshot.json")
    rankings = {row["content_id"]: row for row in analysis["rankings"]}
    for sample in bound["samples"]:
        source = rankings.get(sample["content_id"])
        if (source is None or source["format"] != normalize_format(sample["format"])
                or growth_time(source["published_at"]) != growth_time(sample["published_at"])
                or metric_value(source["metrics"], scope["metric"]) != sample["value"]):
            raise ValueError("growth snapshot content differs from deterministic analysis")
    if {sample["content_id"] for sample in bound["samples"]} != set(rankings):
        raise ValueError("growth snapshot content coverage differs from deterministic analysis")

    def digest(value: Any) -> str:
        return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                         separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    return {"snapshot_id": analysis["snapshot_id"], "input_sha256": digest(request),
            "question_sha256": hashlib.sha256(question.encode()).hexdigest(),
            "result": estimate(bound, question)}


def figure(value: float) -> str:
    """A factor as written in the formula: thousands separators, no exponent, at most four decimals."""
    return f"{value:,.4f}".rstrip("0").rstrip(".")


def render_growth(growth: dict[str, Any], language: str) -> list[str]:
    """Keep the amount, calculation and measurement boundaries in one delivery unit."""
    result = growth["result"]
    zh = language_text(language) is ZH_TEXT
    lines = ["## 条件估算" if zh else "## Conditional estimate", ""]
    if result["status"] == "needs_input":
        lines += ["当前无法给出数值估算或概率，仍缺少：" if zh else "No numeric estimate or probability is available; required inputs:"]
        lines += ["- " + item for item in result["required_inputs"]]
        return lines + [""]
    amount, basis = result["estimate"], result["basis"]
    timing, plan = amount["time_basis"], basis["plan"]
    ages = timing["content_age_days"]
    metric = {"views": "播放", "likes": "点赞", "comments": "评论"}.get(result["scope"]["metric"], result["scope"]["metric"])
    if zh:
        lines += [f"**条件总量：{amount['value']:,.2f} 次{metric}。这是新内容发布后 {ages['min']:.1f}～{ages['max']:.1f} 天、达到历史样本同等观察年龄时的估算，不是发布窗口结束时的累计{metric}。**", "",
                  f"依据：{basis['sample_size']} 条同格式样本，中位数 {figure(basis['median_per_content'])} × 计划发布 {figure(plan['posts'])} 条 = {amount['value']:,.2f}。发布窗口 {figure(timing['publication_window_days'])} 天；假设每条新内容达到该历史中位数。"]
        if "period" in plan:
            period = {"day": "天", "week": "周"}[plan["period"]]
            lines.append(f"发布计划按用户给出的每{period} {figure(plan['posts_per_period'])} 条折算；等效条数不是取整后的日历排期。")
        if plan["origin"] == "observed_cadence":
            lines.append("计划沿用已捕获样本的发布频率；样本可能不完整，这不是账号增长率。")
        if timing["deadline_comparison_to_scenario"] == "lower":
            lines.append(f"截止日累计无法给出具体数字，但通常低于本情景：多数样本的观察年龄（中位 {ages['median']:.1f} 天）长于窗口内新发内容到截止日最多能有的 {figure(timing['publication_window_days'])} 天。低多少需有具体排期和每条内容在截止日对应年龄的累计表现。")
        else:
            lines.append("截止日总量及其高于或低于本情景的关系仍无法确定；需有具体排期和每条内容在截止日对应年龄的累计表现。")
        lines += ["",
                  "敏感性情景（相同观察年龄，逐条假设达到历史分位数；不是批次分位数、预测上下界，也不是发生概率）："]
    else:
        lines += [f"**Conditional batch total: {amount['value']:,.2f} {result['scope']['metric']}. New content observed {ages['min']:.1f}–{ages['max']:.1f} days after each publication; not a total reached by the horizon end.**", "",
                  f"Basis: {basis['sample_size']} same-format samples; median {figure(basis['median_per_content'])} × {figure(plan['posts'])} planned items = {amount['value']:,.2f}. Publication window: {figure(timing['publication_window_days'])} days; assume each new item reaches this historical median."]
        if "period" in plan:
            lines.append(f"Publishing frequency: {figure(plan['posts_per_period'])} per {plan['period']}; equivalent counts are not a rounded calendar posting schedule.")
        if plan["origin"] == "observed_cadence":
            lines.append("The plan continues captured publishing pace; the sample may be selective and this is not an account growth rate.")
        if timing["deadline_comparison_to_scenario"] == "lower":
            lines.append(f"The deadline total is not identified, but it is usually lower than this scenario: most samples were observed older (median {ages['median']:.1f} days) than an item published in the {figure(timing['publication_window_days'])}-day window can be by its end. How much lower needs a publication schedule and comparable cumulative performance at each item's deadline age.")
        else:
            lines.append("Deadline total and its relation to this scenario remain unidentified; a publication schedule and comparable cumulative performance at each item's deadline age are required.")
        lines += ["",
                  "Sensitivity scenarios at the same observation ages (each item matches an observed quartile; not batch quantiles, forecast bounds, or occurrence probabilities):"]
    for key, label_zh, label_en in (("lower_quartile", "下四分位数", "Lower quartile"), ("median", "中位数", "Median"), ("upper_quartile", "上四分位数", "Upper quartile")):
        lines.append(f"- {label_zh if zh else label_en}：{figure(basis['reference_levels'][key])} × {figure(plan['posts'])} = {amount['sensitivity'][key]:,.2f}")
    lines += ["", "这是基于历史样本和明确假设的条件计算，不是经过校准的预测或结果保证。" if zh else "This is conditional arithmetic from historical samples and explicit assumptions, not a calibrated forecast or a guarantee.", ""]
    return lines


def render_markdown(report: dict[str, Any], output_language: str = "en") -> str:
    text = language_text(output_language)
    lines = ["# " + report["headline"], "", report["diagnosis"], ""]
    scope_limits = scope_limits_line(report, text)
    if scope_limits:
        lines += [scope_limits, ""]
    if should_show_platform_status(report):
        platform = report["scope"]["platform"]
        status_text = ZH_PLATFORM_STATUS_CHECK if text is ZH_TEXT else PLATFORM_STATUS_CHECK
        lines += [f"## {status_text['title']}", "", status_text[platform], ""]
    for title, key in ((text["works"], "what_works"), (text["hurts"], "what_hurts")):
        rows = report[key]
        if not rows:
            continue
        lines += [f"## {title}", ""]
        for row in rows:
            lines += [f"### {row['pattern']}", "", row["explanation"], ""]
            for item in row["examples"][:3]:
                lines.append(f"- {evidence_line(item, text)}")
            lines.append("")
    if report.get("growth_estimate") is not None:
        lines += render_growth(report["growth_estimate"], output_language)
    lines += render_next_section(report, text)
    return "\n".join(lines).rstrip() + "\n"


def report_scope(analysis: dict[str, Any]) -> dict[str, Any]:
    merged = {
        **analysis["scope"],
        "snapshot_id": analysis["snapshot_id"],
        "platform": analysis["platform"],
        "source_mode": analysis["source_mode"],
    }
    return {key: merged[key] for key in REPORT_SCOPE_KEYS if key in merged}


def compose(
    analysis: dict[str, Any],
    synthesis: dict[str, Any],
    identity: dict[str, Any] | None = None,
    *,
    growth_input: dict[str, Any] | None = None,
    growth_snapshot: dict[str, Any] | None = None,
    growth_question: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    validate_authoring(analysis, synthesis)
    if synthesis.get("snapshot_id") != analysis.get("snapshot_id"):
        raise ValueError("synthesis snapshot_id does not match analysis")
    if synthesis.get("analysis_id") != analysis.get("analysis_id"):
        raise ValueError("synthesis analysis_id does not match analysis")
    synthesis = relabel_public_connectors(synthesis)
    policies = maturity_by_format(analysis)
    rankings = {row["content_id"]: row for row in analysis["rankings"]}
    profile = build_profile(analysis, synthesis)
    resolved_identity = merge_identity(identity_from_scope(analysis.get("scope")), identity or {})
    diagnosis = synthesis["diagnosis"]
    intro = identity_line(resolved_identity, language_text(synthesis["output_language"]))
    if intro:
        diagnosis = intro + "\n\n" + diagnosis
    report = {
        "schema_version": "account-review-report.v3",
        "report_id": stable_id("account-review", analysis["analysis_id"], synthesis["diagnosis"]),
        "profile_id": profile["profile_id"],
        "status": synthesis["status"],
        "scope": report_scope(analysis),
        "data_coverage": analysis["coverage"],
        "headline": synthesis["headline"],
        "diagnosis": diagnosis,
        "baselines": analysis["baselines"],
        "what_works": resolve_patterns(synthesis["what_works"], rankings, policies),
        "what_hurts": resolve_patterns(synthesis["what_hurts"], rankings, policies),
        "recommendations": synthesis["recommendations"],
        "data_gaps": synthesis["data_gaps"],
    }
    growth = growth_for_review(analysis, growth_input, growth_snapshot, growth_question)
    if growth is not None:
        report["growth_estimate"] = growth
    return report, profile, render_markdown(report, synthesis["output_language"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--analysis", type=Path, required=True)
    parser.add_argument("--synthesis", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--check-existing", action="store_true", help="read-only: recompute and verify the three existing report files")
    parser.add_argument(
        "--profile-envelope",
        type=Path,
        help="optional resolve_profile / resolve_channel envelope used only for the public identity line",
    )
    parser.add_argument("--growth-input", type=Path)
    parser.add_argument("--growth-snapshot", type=Path)
    parser.add_argument("--growth-question-file", type=Path)
    args = parser.parse_args()
    try:
        envelope = load_complete_envelope(args.profile_envelope) if args.profile_envelope else None
        report, profile, markdown = compose(
            load(args.analysis),
            load(args.synthesis),
            identity_from_envelope(envelope or {}),
            growth_input=load(args.growth_input) if args.growth_input else None,
            growth_snapshot=load(args.growth_snapshot) if args.growth_snapshot else None,
            growth_question=args.growth_question_file.read_text(encoding="utf-8") if args.growth_question_file else None,
        )
        if args.check_existing:
            expected = {"account_review.json": json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                        "creator_profile.json": json.dumps(profile, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                        "ACCOUNT_REVIEW.md": markdown}
            for name, text in expected.items():
                path = args.output_dir / name
                if path.is_symlink() or path.read_bytes() != text.encode("utf-8"):
                    raise ValueError(f"existing report differs from current validated source: {name}")
            return 0
        dump(args.output_dir / "account_review.json", report)
        dump(args.output_dir / "creator_profile.json", profile)
        (args.output_dir / "ACCOUNT_REVIEW.md").write_text(markdown, encoding="utf-8")
        return 0
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "failed", "error": f"{type(error).__name__}: {error}"}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
