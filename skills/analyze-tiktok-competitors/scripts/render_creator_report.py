#!/usr/bin/env python3
"""Render a concise Creator-facing view from CompetitorResearchReport V3."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import math
import statistics
from pathlib import Path
from typing import Any

import sys
# Sibling imports must not leave a __pycache__ in the signed, checksummed skill directory.
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from report_basis import basis_sentence, shown_multiple_sample_sizes  # noqa: E402
from evidence_contract import RELIABLE_OUTLIER_MIN_SAMPLE
from compose_report import UNRESOLVED_REASONS, title_excerpt
from report_presentation import Display, load as load_presentation


MARKDOWN_LINK_RE = re.compile(r"\[[^\]\n]*\]\([^\)\n]*\)")
NESTED_MARKDOWN_LINK_RE = re.compile(
    r"\[[^\]\n]*\[[^\]\n]*\]\([^\)\n]*\)[^\]\n]*\]\([^\)\n]*\)"
)

# A bounded author-contract guard, not a parser of arbitrary-language metric meaning.
# Key findings cannot carry authored metrics; existing deterministic pattern proofs own them.
FINDING_BASELINE_MULTIPLE_RE = re.compile(
    r"(?<![\w.])\d+(?:\.\d+)?\s*[x×]\s+(?:(?:the|its)\s+)?"
    r"same-account,?\s+same-format\s+(?:median|baseline)\b", re.IGNORECASE,
)


def content_map(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["content_id"]: item for item in report.get("top_content") or []}


def display_title(value: str) -> str:
    first = (value or "").strip().splitlines()[0].strip()
    return first[:72] + ("…" if len(first) > 72 else "")


# Fixed labels exist once, in English. Model-written prose (titles, mechanisms, gaps) stays in the
# Creator's language; the host relays these labels in that language.
LABELS = {
    "adjacent_evidence": "Adjacent evidence:",
    "media_evidence": "Media evidence:",
    "differentiation": "Your differentiation:",
    "gaps": "What's missing",
    "next_step": "Next step",
}
SEPARATOR = ", "
PROOF_SEPARATOR = "; "
SENTENCE_TERMINATORS = ".!?\u3002\uff01\uff1f\u2026"


def end_sentence(value: str) -> str:
    """Close model prose once, keeping its own terminal mark in any script."""
    text = str(value).rstrip().rstrip("\uff1b;\uff0c,\u3001 ")
    if text and text[-1] in SENTENCE_TERMINATORS:
        return text
    return text + "."


def proof_links(refs: list[str], content: dict[str, dict[str, Any]], separator: str = SEPARATOR, display: Display | None = None) -> str:
    values = []
    for ref in refs[:2]:
        item = content.get(ref)
        if item:
            title = display_title(str(item.get("title") or item["account_ref"]))
            if display:
                title = quote_text(display, ref, title, str(item.get("title") or item["account_ref"]))
            values.append(f"[{title}]({item['url']})")
    return separator.join(values)


def quote_text(display: Display, ref: str, excerpt: str, original: str) -> str:
    slot_id = f"quote:{ref}:{hashlib.sha256(excerpt.encode()).hexdigest()}"
    text = display.text(slot_id, "source_quote", excerpt,
                        f"top_content/{ref}/title", source_identity=original)
    if display.supplied is not None and not display.identity(slot_id):
        label = display.text("label:translation", "label", "translated source", "renderer/translation")
        return f"{text} ({label})"
    # The label's slot set must not depend on the model's text, including identity translations.
    display.text("label:translation", "label", "translated source", "renderer/translation")
    return text


def authored(text: str, report: dict[str, Any], display: Display | None) -> str:
    """Only replace explicitly attributed source titles, never edit authored prose."""
    if not display:
        return text
    for ref, item in content_map(report).items():
        title = str(item.get("title") or "")
        if not title:
            continue
        forms = [(left + title + right, left, right) for left, right in (("“", "”"), ('"', '"'), ("'", "'"))]
        forms.append(("[" + title + "](", "[", "]("))
        for needle, left, right in forms:
            if needle in text:
                text = text.replace(needle, left + quote_text(display, ref, title, title) + right)
    return text


def proof_text(display: Display | None, ref: str, proof: str, item: dict[str, Any]) -> str:
    if display is None:
        return proof
    original = str(item.get("title") or item.get("content_id"))
    excerpt = title_excerpt(original)
    quote = "“" + excerpt + "”"
    if proof.count(quote) != 1:
        raise ValueError(f"proof source quotation is ambiguous: {ref}")
    translated = quote_text(display, ref, excerpt, original)
    # Facts bind to the original source, while the one title insertion is itself source-bound.
    rendered = display.text(f"proof:{ref}", "mechanical_proof", proof,
                            f"patterns/selected_content_ref={ref}/proof_points", insertions={"quote": quote})
    return rendered.replace(quote, "“" + translated + "”", 1)


def select_pattern_evidence(
    pattern: dict[str, Any],
    report: dict[str, Any],
    content: dict[str, dict[str, Any]],
) -> list[tuple[str, str]]:
    """Choose two strong proof points, preferring distinct accounts."""
    outliers = {
        str(item.get("content_id")): item
        for item in report.get("outliers") or []
        if item.get("content_id") is not None and item.get("ratio") is not None
    }
    proof_points = pattern.get("proof_points") or []
    report_scores: list[float] = []
    for report_item in content.values():
        report_views = (report_item.get("metrics") or {}).get("views")
        if not isinstance(report_views, (int, float)) or isinstance(report_views, bool):
            continue
        report_score = math.log1p(float(report_views))
        report_outlier = outliers.get(str(report_item.get("content_id")))
        if report_outlier:
            report_ratio = float(report_outlier["ratio"])
            report_n = int((report_outlier.get("baseline") or {}).get("sample_size") or 0)
            report_support = (
                math.sqrt(report_n / (report_n + RELIABLE_OUTLIER_MIN_SAMPLE))
                if report_n > 0 else 0.0
            )
            if report_ratio > 0:
                report_score += math.log(report_ratio) * report_support
        report_scores.append(report_score)
    evidence_floor = statistics.median(report_scores) if len(report_scores) >= 4 else float("-inf")
    candidates = []
    for index, ref in enumerate(pattern.get("content_refs") or []):
        item = content.get(str(ref))
        if not item or index >= len(proof_points):
            continue
        outlier = outliers.get(str(ref))
        metrics = item.get("metrics") or {}
        views = metrics.get("views")
        evidence_score = math.log1p(float(views)) if isinstance(views, (int, float)) and not isinstance(views, bool) else 0.0
        if outlier:
            ratio = float(outlier["ratio"])
            sample_size = int((outlier.get("baseline") or {}).get("sample_size") or 0)
            sample_support = (
                math.sqrt(sample_size / (sample_size + RELIABLE_OUTLIER_MIN_SAMPLE))
                if sample_size > 0 else 0.0
            )
            if ratio > 0:
                evidence_score += math.log(ratio) * sample_support
        candidates.append({
            "index": index,
            "ref": str(ref),
            "proof": str(proof_points[index]),
            "account_ref": str(item.get("account_ref") or ""),
            "ratio": float(outlier["ratio"]) if outlier else None,
            "evidence_score": evidence_score,
            "views": views,
            "likes": metrics.get("likes"),
            "comments": metrics.get("comments"),
        })
    candidates.sort(
        key=lambda item: (
            item["evidence_score"],
            item["views"] is not None,
            item["views"] if item["views"] is not None else float("-inf"),
            item["likes"] is not None,
            item["likes"] if item["likes"] is not None else float("-inf"),
            item["comments"] is not None,
            item["comments"] if item["comments"] is not None else float("-inf"),
            -item["index"],
        ),
        reverse=True,
    )
    if not candidates:
        return []
    strong_candidates = [item for item in candidates if item["evidence_score"] > evidence_floor]
    if strong_candidates:
        candidates = strong_candidates
    elif evidence_floor != float("-inf"):
        candidates = candidates[:1]
    selected = [candidates[0]]
    different_account = next(
        (item for item in candidates[1:] if item["account_ref"] != candidates[0]["account_ref"]),
        None,
    )
    if different_account:
        selected.append(different_account)
    elif len(candidates) > 1:
        selected.append(candidates[1])
    return [(item["ref"], item["proof"]) for item in selected]


def mask_protected(text: str, report: dict[str, Any]) -> tuple[str, list[str]]:
    protected: list[str] = []

    def mask(value: str) -> str:
        token = f"\x00KABO_PROTECTED_{len(protected)}\x00"
        protected.append(value)
        return token

    result = MARKDOWN_LINK_RE.sub(lambda match: mask(match.group(0)), text)
    titles = {
        str(item.get("title") or "")
        for item in report.get("top_content") or []
        if str(item.get("title") or "")
    }
    for title in sorted(titles, key=len, reverse=True):
        result = re.sub(re.escape(title), lambda match: mask(match.group(0)), result)
    return result, protected


def restore_protected(text: str, protected: list[str]) -> str:
    result = text
    for index, value in enumerate(protected):
        result = result.replace(f"\x00KABO_PROTECTED_{index}\x00", value)
    return result


def link_accounts(text: str, report: dict[str, Any]) -> str:
    result, protected = mask_protected(text, report)
    competitors = sorted(report.get("competitors") or [], key=lambda item: len(str(item.get("handle") or "")), reverse=True)
    for item in competitors:
        clean = str(item.get("handle") or "").lstrip("@")
        platform = str(item.get("platform") or "")
        label = clean if platform == "youtube" else "@" + clean
        url = str(item.get("url") or "")
        if not clean or not url:
            continue
        candidates = ["@" + clean, clean] if platform == "youtube" else [label]
        pattern = "|".join(re.escape(candidate) for candidate in sorted(candidates, key=len, reverse=True))

        def link_first(_: re.Match[str]) -> str:
            token = f"\x00KABO_PROTECTED_{len(protected)}\x00"
            protected.append(f"[{label}]({url})")
            return token

        result = re.sub(pattern, link_first, result, count=1)
    return restore_protected(result, protected)


def validate_rendered_markdown(markdown: str, report: dict[str, Any]) -> None:
    content = content_map(report)
    expected_titles = set()
    for pattern in report.get("patterns") or []:
        for ref, proof in select_pattern_evidence(pattern, report, content):
            title = str((content.get(ref) or {}).get("title") or "")
            if title and title in proof:
                expected_titles.add(title)
    missing_titles = sorted(title for title in expected_titles if title not in markdown)
    if missing_titles:
        raise ValueError(f"rendered report changed source title: {missing_titles[0]}")
    if NESTED_MARKDOWN_LINK_RE.search(markdown):
        raise ValueError("rendered report contains a nested Markdown link")


def render_gap_section(report: dict[str, Any], content: dict[str, dict[str, Any]], display: Display | None = None) -> list[str]:
    gaps = [item for item in report.get("gaps") or [] if isinstance(item, dict) and item.get("summary")]
    if not gaps:
        return []
    label = display.text("label:gaps", "label", LABELS["gaps"], "renderer/gaps") if display else LABELS["gaps"]
    lines = [f"## {label}", ""]
    for gap in gaps[:4]:
        refs = proof_links(list(gap.get("content_refs") or [])[:2], content, display=display)
        summary = end_sentence(authored(gap["summary"], report, display))
        if refs:
            summary += f" ({refs})"
        lines += [link_accounts(summary, report), ""]
    return lines


def render(report: dict[str, Any], display: Display | None = None) -> str:
    if display:
        display.assert_report(report)
    overview = report["overview"]
    content = content_map(report)
    relationships = {
        str(item.get("account_ref")): str(item.get("relationship") or "")
        for item in report.get("competitors") or []
    }
    def label(key: str) -> str:
        return display.text("label:" + key, "label", LABELS[key], "renderer/" + key) if display else LABELS[key]
    lines = [f"# {link_accounts(authored(overview['title'], report, display), report)}", "", link_accounts(authored(overview["summary"], report, display), report), ""]
    # These are authored conclusions, including evidence that does not support a claim.
    # Keep them complete and ordered; sampling metadata remains in the structured report.
    for index, finding in enumerate(overview.get("key_findings") or []):
        # Protect exact source-title quotations, not arbitrary claims placed in link labels.
        titles = {str(item.get("title") or "") for item in report.get("top_content") or []}
        visible = re.sub(r"\[([^\]\n]*)\]\([^\)\n]*\)",
                         lambda match: "" if match[1] in titles else match[1], finding)
        for title in titles - {""}:
            for left, right in (("\"", "\""), ("“", "”"), ("'", "'")):
                visible = visible.replace(left + title + right, "")
        if FINDING_BASELINE_MULTIPLE_RE.search(visible):
            raise ValueError(
                f"overview.key_findings[{index}]: remove authored metrics from the finding. "
                "Only an already-supported pattern's deterministic proof may display them; "
                "do not invent a pattern, account or evidence to satisfy rendering."
            )
        lines += [authored(finding, report, display), ""]
    if (report.get("scope") or {}).get("sampling"):
        # Only the explicit latest-N contract needs this inventory. Preserve the
        # date-mode report byte for byte, including the existing basis sentence.
        samples_label = display.text("label:samples", "label", "Compared samples", "renderer/samples") if display else "Compared samples"
        lines += ["## " + samples_label, ""]
        for sample in report["coverage"]["account_samples"]:
            window = sample.get("window")
            dates = f"published {window['since']} to {window['until']}" if window else "no analyzable publication dates"
            reason = sample.get("reason")
            note = f"; {UNRESOLVED_REASONS.get(reason, str(reason).replace('_', ' '))}" if reason else ""
            sample_text = f"@{sample['handle']}: {sample['analyzed_count']} analyzed of {sample['requested_count']} requested; {dates}{note}."
            if display:
                sample_text = display.text("sample:" + str(sample.get("account_ref") or sample["handle"]), "sample", sample_text, "coverage/account_samples/" + str(sample.get("account_ref") or sample["handle"]))
            lines += ["- " + sample_text]
        unavailable = ["@" + sample["handle"] for sample in report["coverage"]["account_samples"] if sample["analyzed_count"] == 0]
        missing = "Not compared: " + (", ".join(unavailable) or "none") + "."
        if display:
            missing = display.text("sample:not_compared", "sample", missing, "coverage/account_samples")
        lines += ["", missing, ""]
    cited: list[str] = []
    for pattern in report.get("patterns", [])[:3]:
        selected = select_pattern_evidence(pattern, report, content)
        cited += [ref for ref, _ in selected]
        refs = proof_links([ref for ref, _ in selected], content, display=display)
        mechanism = end_sentence(authored(pattern["mechanism"], report, display))
        proof = PROOF_SEPARATOR.join(proof_text(display, ref, value, content[ref]).rstrip("\u3002\uff1b;. ") for ref, value in selected)
        selected_relationships = {
            relationships.get(str(content[ref].get("account_ref")))
            for ref, _ in selected if ref in content
        }
        evidence_label = ""
        if selected_relationships and "direct" not in selected_relationships:
            evidence_label = label("adjacent_evidence" if "adjacent" in selected_relationships else "media_evidence") + " "
        paragraph = mechanism + (" " + evidence_label + link_accounts(proof, report) + "." if proof else "")
        lines += [
            f"## {authored(pattern['pattern'], report, display)}", "",
            paragraph + (f" ({refs})" if refs else ""), "",
            f"**{label('differentiation')}** {authored(pattern['differentiation'], report, display)}", "",
        ]
    lines += render_gap_section(report, content, display)
    lines += [f"## {label('next_step')}", "", authored(overview["next_action"], report, display)]
    markdown = "\n".join(lines).strip() + "\n"
    # Raw Report titles remain immutable; a source-bound presentation validates each title
    # before inserting its verified display text instead of requiring the untranslated original.
    if display is None:
        validate_rendered_markdown(markdown, report)
    elif NESTED_MARKDOWN_LINK_RE.search(markdown):
        raise ValueError("rendered report contains a nested Markdown link")
    # One fixed sentence of measurement basis; the rest stays in the Report for follow-up questions.
    basis = basis_sentence(report, shown_multiple_sample_sizes(report, cited))
    if basis and display:
        basis = display.text("basis", "measurement_basis", basis, "coverage/scope/baselines")
    if display:
        display.contract()
    return markdown + (f"\n*{basis}*\n" if basis else "")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--print-presentation-contract", action="store_true")
    parser.add_argument("--contract-output", type=Path)
    parser.add_argument("--presentation", type=Path)
    parser.add_argument("--language")
    args = parser.parse_args()
    try:
        raw = args.report.read_bytes()
        report = json.loads(raw)
        if args.print_presentation_contract and (not args.contract_output or args.presentation):
            raise ValueError("print contract requires exclusive --contract-output and no --presentation")
        if args.contract_output and not args.print_presentation_contract:
            raise ValueError("--contract-output requires --print-presentation-contract")
        display = Display(raw, args.language, load_presentation(args.presentation) if args.presentation else None) if args.print_presentation_contract or args.presentation else None
        markdown = render(report, display)
        if args.print_presentation_contract:
            # Exclusive creation preserves the first contract and never overwrites a report.
            with args.contract_output.open("x", encoding="utf-8") as stream:
                stream.write(json.dumps(display.contract(), ensure_ascii=False, indent=2) + "\n")
            print(json.dumps(display.contract(), ensure_ascii=False))
        else:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(markdown, encoding="utf-8")
            if display is not None and display.kept_source:
                print(json.dumps({"status": "ok", "kept_source_text": display.kept_source}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "failed", "error": f"{type(error).__name__}: {error}"}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
