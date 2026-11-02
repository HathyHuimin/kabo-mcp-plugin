#!/usr/bin/env python3
"""Render a concise Creator-facing view from TrendResearchReport V3."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# Sibling imports must not leave a __pycache__ in the signed, checksummed skill directory.
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from report_basis import ENGLISH as BASIS_EN, basis_sentence, shown_multiple_sample_sizes  # noqa: E402

# The closing basis sentence is the one fixed sentence the host may hand over without relaying it,
# so it is written in the Creator's chat language: the template is chosen by the primary subtag of
# the `--language` the run passes, and a language not listed here (or none) falls back to English,
# which the host relays. These are the templates analyze-creator-competitors uses in render_reply.py.
BASIS: dict[str, dict[str, str]] = {
    "en": BASIS_EN,
    "zh": {"count": "基于 {count} 条公开内容", "no_count": "基于公开内容", "accounts": "（{accounts} 个账号）",
           "window": "，发布于 {since} 至 {until}", "multiple": "；倍数是与同账号同形式内容的基线相比（{n}）", "end": "。"},
    "pt": {"count": "Com base em {count} publicações públicas", "no_count": "Com base em publicações públicas",
           "accounts": " de {accounts} contas", "window": " publicadas de {since} a {until}",
           "multiple": "; os múltiplos comparam cada post com a linha de base do mesmo formato da mesma conta ({n})"},
    "es": {"count": "Basado en {count} publicaciones públicas", "no_count": "Basado en publicaciones públicas",
           "accounts": " de {accounts} cuentas", "window": " publicadas del {since} al {until}",
           "multiple": "; los múltiplos comparan cada publicación con la línea base del mismo formato de la misma cuenta ({n})"},
}


def basis_template(language: str | None) -> dict[str, str]:
    """The basis wording for a BCP 47 tag's primary subtag; English when absent or not listed."""
    primary = re.split(r"[-_]", str(language or "").strip())[0].lower()
    return BASIS.get(primary, BASIS["en"])


def content_map(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["content_id"]: item for item in report.get("top_content") or []}


# Fixed labels exist in English only. Model-written fields (title, summary, mechanism, fit, angle)
# stay in the Creator's language; the host relays fixed labels in that language.
LABELS = {
    "source": "source",
    "evidence": "Evidence",
    "direction": "Best direction for you:",
    "fit": "Why it fits you:",
    "next_action": "Test this first",
    "standouts": "Featured posts",
    "views": "{n} views",
    "multiple": "{m}× the account's usual",
}
# A shown multiple needs a same-account cohort this large (the Composer's reliable-outlier floor).
MIN_COHORT = 5
STANDOUT_LIMIT = 5


# Code-level identifiers of the recall, capacity and ranking implementation. No Creator-facing
# sentence needs them in any language, so they are safe to reject by pattern in model-written prose.
# Natural wording about scope is not matched. The measurement basis — window, analyzed count and the
# baseline size behind a shown multiple — is stated once by the fixed basis sentence the renderer
# appends after this check; everything else stays in the structured Report for follow-up questions.
INTERNAL_METHOD_PATTERN = re.compile(
    r"(?:"
    r"\brrf\b|"
    r"\b(?:search|popular|provider)\s+(?:route|lane)\b|"
    r"\b(?:route|lane|provider|request|candidate|api)\s+(?:union|quota|allocation)\b|"
    # A cohort sample size in prose is audit data; the basis sentence carries the one that matters.
    r"\bn\s*[=＝]\s*\d+"
    r")",
    re.IGNORECASE,
)


def proof_links(refs: list[str], content: dict[str, dict[str, Any]]) -> str:
    values = []
    for index, ref in enumerate(refs[:2], start=1):
        item = content.get(ref)
        if item:
            creator = str(item.get("creator_ref") or LABELS["source"])
            # Instagram and TikTok creator refs are handles; YouTube refs may instead be
            # display titles, so only preserve an @ that the Composer already verified.
            if item.get("platform") in {"instagram", "tiktok"} and not creator.startswith("@"):
                creator = f"@{creator}"
            values.append(f"[{LABELS['evidence']} {index} {creator}]({item['url']})")
    return ", ".join(values)


def sentence(value: str) -> str:
    return value.strip().rstrip("\u3002\uff1b;. ") + "."


def validate_rendered_markdown(markdown: str, report: dict[str, Any], language: str | None = None) -> None:
    """Fail closed when model-written Creator-facing prose drops its Creator fit or exposes code identifiers."""
    patterns = report.get("patterns") or []
    required = [
        sentence(str(item.get("creator_fit") or ""))
        for item in patterns[:3]
        if str(item.get("creator_fit") or "").strip()
    ]
    missing = [value for value in required if value and value not in markdown]
    if missing:
        raise ValueError(f"creator-facing report dropped material boundaries: {missing}")
    leaked = {match.group(0) for match in INTERNAL_METHOD_PATTERN.finditer(markdown)}
    if leaked:
        raise ValueError(f"creator-facing report exposes internal research mechanics: {sorted(leaked)}")


def render(report: dict[str, Any], language: str | None = None) -> str:
    """`language` is the Creator's chat language; it selects only the closing basis sentence's wording."""
    overview = report["overview"]
    content = content_map(report)
    lines = [f"# {overview['title']}", "", overview["summary"], ""]
    for pattern in report.get("patterns", [])[:3]:
        refs = proof_links(pattern["content_refs"], content)
        proof = "; ".join(pattern["proof_points"][:2])
        evidence = f" ({refs})" if refs else ""
        lines += [
            f"## {pattern['pattern']}", "",
            sentence(pattern["mechanism"]) + (f" {proof}" if proof else "") + evidence, "",
            f"**{LABELS['fit']}** {sentence(pattern['creator_fit'])}", "",
            f"**{LABELS['direction']}** {sentence(pattern['angle'])} {pattern['differentiation'].strip()}", "",
        ]
    lines += [f"## {LABELS['next_action']}", "", overview["next_action"]]
    markdown = "\n".join(lines).strip() + "\n"
    validate_rendered_markdown(markdown, report)
    # A synthesis with no pattern still has its ranked posts; without them the report named the
    # finding but no post, so "which Reels beat their account's usual" got no Reel. Source titles
    # are not model prose, so they go in after the prose check.
    standouts = [line for line in (standout_line(item) for item in standout_items(report)) if line]
    if standouts:
        heading = f"## {LABELS['next_action']}"
        block = "\n".join([f"## {LABELS['standouts']}", "", *standouts, "", ""])
        markdown = markdown.replace(heading, block + heading, 1)
    basis = basis_sentence(report, shown_multiple_sample_sizes(report, cited_refs(report)), basis_template(language))
    return markdown + (f"\n*{basis}*\n" if basis else "")


def standout_items(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Without patterns, the first ranked posts (the Composer's order) stand in as the proof."""
    if report.get("patterns"):
        return []
    return [item for item in (report.get("top_content") or []) if item.get("url")][:STANDOUT_LIMIT]


def shown_multiple(item: dict[str, Any]) -> float | None:
    multiple = item.get("outlier_multiple")
    cohort = (item.get("baseline") or {}).get("sample_size")
    if isinstance(multiple, (int, float)) and isinstance(cohort, int) and cohort >= MIN_COHORT:
        return float(multiple)
    return None


def standout_line(item: dict[str, Any]) -> str | None:
    creator = str(item.get("creator_ref") or LABELS["source"])
    if item.get("platform") in {"instagram", "tiktok"} and not creator.startswith("@"):
        creator = f"@{creator}"
    title = " ".join(str(item.get("title") or "").split())
    title = title[:80].rstrip() + ("…" if len(title) > 80 else "")
    title = re.sub(r"[\\\[\]()*_`<>]", "", title).strip()
    parts = [f"[{title or creator}]({item['url']})", creator]
    metrics = item.get("metrics") or {}
    views = next((metrics[key] for key in ("views", "plays", "play_count") if isinstance(metrics.get(key), (int, float))), None)
    if views is not None:
        parts.append(LABELS["views"].format(n=f"{int(round(views)):,}"))
    multiple = shown_multiple(item)
    if multiple is not None:
        parts.append(LABELS["multiple"].format(m=f"{multiple:.1f}"))
    return "- " + " · ".join(parts)


def cited_refs(report: dict[str, Any]) -> list[str]:
    """The refs whose proof the report shows: the patterns' first two each, or the featured posts."""
    content = content_map(report)
    refs = [ref for pattern in (report.get("patterns") or [])[:3]
            for ref in (pattern.get("content_refs") or [])[:2] if ref in content]
    return refs + [str(item["content_id"]) for item in standout_items(report) if shown_multiple(item) is not None]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--language", help="the Creator's chat language (BCP 47, such as zh-CN); it selects the "
                                           "basis sentence's wording, English when absent or not listed")
    args = parser.parse_args()
    try:
        report = json.loads(args.report.read_text(encoding="utf-8"))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(render(report, args.language), encoding="utf-8")
        return 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "failed", "error": f"{type(error).__name__}: {error}"}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
