#!/usr/bin/env python3
"""Write the Creator-facing reply for a validated CompetitorResearchReport V3, in the Report's language.

The reply used to be the last model turn of a run: the model re-read the rendered report and retyped
it as a relay card in the Creator's language — about 1,400 output tokens, measured 25–46 s on
2026-09-18, for text whose every sentence already sat in the validated Report (the synthesis prose is
written in the Creator's language) and whose numbers the Composer had already attached. This writes
that relay deterministically: overview, the compared accounts, up to three findings with their
strongest proof and the Creator's differentiation, then what nobody is doing, closed by one fixed
sentence of measurement basis (analyzed posts, accounts, window and the baseline size behind a shown
multiple). Known zero-sample accounts are named separately and never counted as compared. Other raw
data gaps and the next-step plan stay in the Report for follow-up questions.

The existing labels follow the Report's `scope.locale.language` primary subtag, with English for
unlisted languages. The zero-sample notice is one of those labels. When `synthesis-evidence.json` sits next
to the Report, one line gives each compared account's posting cadence beside the subject's, and whether it posts
more or less often — "can't tell" where either count is a lower bound. When the Creator named more accounts than
were compared, one label line says so (and names the discovery cap when that is the reason). Everything else is the Report's own text and data.

usage: render_reply.py <competitor-research-report.json> <creator-reply.md>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

from report_basis import ENGLISH as BASIS_EN, basis_sentence, shown_multiple_sample_sizes  # noqa: E402
from render_creator_report import (  # noqa: E402
    content_map,
    display_title,
    end_sentence,
    link_accounts,
    select_pattern_evidence,
    comparison_basis,
    zero_sample,
    zero_sample_refs,
    ZERO_SAMPLES,
)

LABELS: dict[str, dict[str, str]] = {
    "en": {
        "roster": "Compared accounts", "direct": "direct", "adjacent": "adjacent", "media": "media",
        "adjacent_evidence": "Adjacent evidence:", "media_evidence": "Media evidence:",
        "differentiation": "How you can differ:", "gaps": "What nobody is doing yet",
        "views": "{n} views", "multiple": "{m}× the account's usual",
        "not_compared": "Requested accounts not compared (no verified public evidence; no substitute was used): {refs}.",
        "zero_samples": ZERO_SAMPLES, "standouts": "Featured posts",
        "cadence": "Posting cadence in the window", "you": "you",
        "posts": "{n} posts ({w}/week)", "posts_at_least": "at least {n} posts",
        "more": "more often than you", "less": "less often than you", "similar": "about as often as you",
        "unknown_note": "A count marked at least is only a lower bound, so it is compared only where it already exceeds the other count.", "comma": ", ", "stop": ". ",
        "count_cap": "You asked for {requested} accounts; one run discovers at most {cap} automatically (accounts you name are not capped), and this one compared {compared}. Ask for another round that excludes these accounts to extend the list.",
        "count_short": "You asked for {requested} accounts; {compared} had verifiable public evidence and were compared.",
    },
    "zh": {
        "roster": "对比的账号", "direct": "直接竞品", "adjacent": "相邻参考", "media": "媒体参考",
        "adjacent_evidence": "相邻参考证据：", "media_evidence": "媒体参考证据：",
        "differentiation": "你可以怎么不同：", "gaps": "还没人做的",
        "views": "{n} 次播放", "multiple": "该账号常态的 {m} 倍",
        "not_compared": "以下指定账号没有可核实的公开数据，未纳入对比，也没有用其他账号替代：{refs}。",
        "zero_samples": "以下选中账号在所选时间窗内没有可分析的内容（0 条），未参与内容表现对比：{refs}。",
        "standouts": "重点内容",
        "cadence": "时间窗内的发帖频率", "you": "你",
        "posts": "{n} 条（每周 {w} 条）", "posts_at_least": "至少 {n} 条",
        "more": "比你发得多", "less": "比你发得少", "similar": "和你差不多",
        "unknown_note": "标「至少」的只是下限，只有下限已超过对方时才判断谁发得更多。", "comma": "，", "stop": "。",
        "count_cap": "你要的是 {requested} 个账号；每次自动发现最多 {cap} 个（你点名的账号不受此限），这次纳入了 {compared} 个。可以再跑一轮、排除已比较的账号来补足。",
        "count_short": "你要的是 {requested} 个账号；有可核实公开数据、纳入对比的是 {compared} 个。",
    },
    "pt": {
        "roster": "Contas comparadas", "direct": "direto", "adjacent": "adjacente", "media": "mídia",
        "adjacent_evidence": "Evidência adjacente:", "media_evidence": "Evidência de mídia:",
        "differentiation": "Como você pode se diferenciar:", "gaps": "O que ninguém está fazendo ainda",
        "views": "{n} visualizações", "multiple": "{m}× o normal da conta",
        "not_compared": "Contas solicitadas não comparadas (sem evidência pública verificável; nenhuma substituta foi usada): {refs}.",
        "zero_samples": "Contas selecionadas sem comparação de desempenho (0 publicações analisáveis no período solicitado): {refs}.",
        "standouts": "Publicações em destaque",
        "cadence": "Frequência de publicação no período", "you": "você",
        "posts": "{n} publicações ({w}/semana)", "posts_at_least": "pelo menos {n} publicações",
        "more": "publica mais que você", "less": "publica menos que você", "similar": "publica quase o mesmo que você",
        "unknown_note": "Onde diz pelo menos, a contagem é só um limite inferior e só é comparada quando já supera a outra.",
        "comma": ", ", "stop": ". ",
        "count_cap": "Você pediu {requested} contas; cada execução descobre automaticamente no máximo {cap} (contas que você indicar não têm limite), e esta comparou {compared}. Peça outra rodada excluindo estas contas para ampliar a lista.",
        "count_short": "Você pediu {requested} contas; {compared} tinham evidência pública verificável e foram comparadas.",
    },
    "es": {
        "roster": "Cuentas comparadas", "direct": "directo", "adjacent": "adyacente", "media": "medio",
        "adjacent_evidence": "Evidencia adyacente:", "media_evidence": "Evidencia de medios:",
        "differentiation": "Cómo puedes diferenciarte:", "gaps": "Lo que nadie está haciendo aún",
        "views": "{n} reproducciones", "multiple": "{m}× lo habitual de la cuenta",
        "not_compared": "Cuentas solicitadas no comparadas (sin evidencia pública verificable; no se usó ninguna sustituta): {refs}.",
        "zero_samples": "Cuentas seleccionadas sin comparación de rendimiento (0 publicaciones analizables en el periodo solicitado): {refs}.",
        "standouts": "Publicaciones destacadas",
        "cadence": "Frecuencia de publicación en el periodo", "you": "tú",
        "posts": "{n} publicaciones ({w}/semana)", "posts_at_least": "al menos {n} publicaciones",
        "more": "publica más que tú", "less": "publica menos que tú", "similar": "publica casi lo mismo que tú",
        "unknown_note": "Donde dice al menos, el conteo es solo un límite inferior y solo se compara cuando ya supera al otro.",
        "comma": ", ", "stop": ". ",
        "count_cap": "Pediste {requested} cuentas; cada ejecución descubre automáticamente como máximo {cap} (las cuentas que nombres no tienen límite), y esta comparó {compared}. Pide otra ronda que excluya estas cuentas para ampliar la lista.",
        "count_short": "Pediste {requested} cuentas; {compared} tenían evidencia pública verificable y se compararon.",
    },
}
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
# select_accounts.py discovers at most this many accounts in one run (its `target` / `limit`).
RUN_ACCOUNT_CAP = 6
# Creator-facing outlier proof needs at least five comparables and at least 1.5× (SKILL step 5).
MIN_COMPARABLES = 5
MIN_MULTIPLE = 1.5


def labels_for(report: dict[str, Any]) -> dict[str, str]:
    language = str(((report.get("scope") or {}).get("locale") or {}).get("language") or "en")
    return LABELS.get(language.split("-")[0].lower(), LABELS["en"])


def number(value: float) -> str:
    return f"{int(round(value)):,}"


def proof_line(ref: str, report: dict[str, Any], content: dict[str, dict[str, Any]], text: dict[str, str]) -> str | None:
    item = content.get(ref)
    if not item:
        return None
    handles = {str(c.get("account_ref")): c for c in report.get("competitors") or []}
    account = handles.get(str(item.get("account_ref")), {})
    handle = str(account.get("handle") or "").lstrip("@")
    who = (f"[@{handle}]({account['url']})" if handle and account.get("url") and account.get("platform") != "youtube"
           else f"[{handle}]({account['url']})" if handle and account.get("url") else "")
    parts = [part for part in (who, f"[{display_title(str(item.get('title') or ref))}]({item['url']})") if part]
    views = (item.get("metrics") or {}).get("views")
    if isinstance(views, (int, float)) and not isinstance(views, bool):
        parts.append(text["views"].format(n=number(views)))
    for outlier in report.get("outliers") or []:
        if str(outlier.get("content_id")) != ref or outlier.get("ratio") is None:
            continue
        comparables = int((outlier.get("baseline") or {}).get("sample_size") or 0)
        ratio = float(outlier["ratio"])
        if comparables >= MIN_COMPARABLES and ratio >= MIN_MULTIPLE:
            parts.append(text["multiple"].format(m=f"{ratio:.1f}"))
    return "- " + " · ".join(parts)


def cadence_text(cadence: Any, text: dict[str, str]) -> str | None:
    """«N posts (W/week)», or «at least N posts» for a lower bound, which has no rate."""
    if not isinstance(cadence, dict):
        return None
    if cadence.get("count_is_lower_bound"):
        count = window_count(cadence)
        return text["posts_at_least"].format(n=count) if count is not None else None
    if not isinstance(cadence.get("posts_in_window"), int):
        return None
    return text["posts"].format(n=cadence["posts_in_window"], w=f"{float(cadence.get('posts_per_week') or 0):.1f}")


def window_count(cadence: dict[str, Any]) -> int | None:
    """The in-window count; a lower bound is written as `posts_in_window_at_least` (no rate)."""
    key = "posts_in_window_at_least" if cadence.get("count_is_lower_bound") else "posts_in_window"
    count = cadence.get(key, cadence.get("posts_in_window"))
    return count if isinstance(count, int) and not isinstance(count, bool) else None


# Two exact counts in the same window this close are "about the same".
SIMILAR_SHARE = 0.15


def cadence_relation(cadence: Any, subject: Any) -> str | None:
    """How often an account posts against the subject, from the counts alone.

    A lower bound ranks only in its own direction: an account that posted at least 50 times when the
    subject posted exactly 21 does post more, but at least 20 against 21 may be 200 and tells nothing
    (plugins, 2026-10-27: a synthesis called competitors "materially faster" than a subject whose
    count was a one-page lower bound; 2026-10-28: at least 50 against an exact 21 was left unranked
    while the report rightly said "much heavier posting"). Two lower bounds never rank.
    """
    if not isinstance(cadence, dict) or not isinstance(subject, dict):
        return None
    theirs_floor, mine_floor = bool(cadence.get("count_is_lower_bound")), bool(subject.get("count_is_lower_bound"))
    mine, theirs = window_count(subject), window_count(cadence)
    if mine is None or theirs is None:
        return "unknown" if theirs_floor or mine_floor else None
    similar = abs(theirs - mine) <= SIMILAR_SHARE * max(theirs, mine)
    if theirs_floor and mine_floor:
        return "unknown"
    if theirs_floor:
        return "more" if theirs > mine and not similar else "unknown"
    if mine_floor:
        return "less" if mine > theirs and not similar else "unknown"
    if similar:
        return "similar"
    return "more" if theirs > mine else "less"


def cadence_line(report: dict[str, Any], evidence: dict[str, Any] | None, text: dict[str, str]) -> str | None:
    """Every compared account's posting cadence next to the subject's, from `synthesis-evidence.json`.

    The synthesis was asked to compare these numbers account by account and wrote them for three of
    six accounts, the validator passing it (plugins, 2026-10-27); printed here
    from the evidence, no account is left out and a lower bound always says so.
    """
    if not evidence:
        return None
    by_ref = {str(a.get("account_ref")): a.get("cadence") for a in evidence.get("accounts") or []}
    parts = []
    # Any "at least" on the line gets the one sentence saying how a lower bound is compared.
    bounded = bool((evidence.get("subject_cadence") or {}).get("count_is_lower_bound"))
    for item in report.get("competitors") or []:
        if zero_sample(item):
            continue
        cadence = by_ref.get(str(item.get("account_ref")))
        value = cadence_text(cadence, text)
        handle = str(item.get("handle") or "").lstrip("@")
        if value and handle:
            relation = cadence_relation(cadence, evidence.get("subject_cadence"))
            bounded = bounded or bool(cadence.get("count_is_lower_bound"))
            parts.append(f"{handle if item.get('platform') == 'youtube' else '@' + handle}: {value}"
                         + (f"{text['comma']}{text[relation]}" if relation in ("more", "less", "similar") else ""))
    if not parts:
        return None
    subject = cadence_text(evidence.get("subject_cadence"), text)
    name = str((((report.get("scope") or {}).get("subject") or {}).get("name")) or "").strip()
    if subject:
        parts.insert(0, f"{name} ({text['you']}): {subject}" if name else f"{text['you']}: {subject}")
    line = f"**{text['cadence']}** " + " · ".join(parts)
    return f"{line}{text['stop']}{text['unknown_note']}" if bounded else line


def render_reply(report: dict[str, Any], evidence: dict[str, Any] | None = None) -> str:
    text = labels_for(report)
    content = content_map(report)
    overview = report["overview"]
    lines = [f"## {link_accounts(overview['title'], report)}", "", link_accounts(overview["summary"], report), ""]
    roster = []
    for item in report.get("competitors") or []:
        if zero_sample(item):
            continue
        handle = str(item.get("handle") or "").lstrip("@")
        if not handle:
            continue
        label = handle if item.get("platform") == "youtube" else "@" + handle
        name = f"[{label}]({item['url']})" if item.get("url") else label
        relationship = text.get(str(item.get("relationship") or ""), "")
        roster.append(f"{name} ({relationship})" if relationship else name)
    if roster:
        lines += [f"**{text['roster']}** " + ", ".join(roster), ""]
    cadence = cadence_line(report, evidence, text)
    if cadence:
        lines += [cadence, ""]
    empty = zero_sample_refs(report)
    if empty:
        lines += [link_accounts(text["zero_samples"].format(refs=", ".join(empty)), report), ""]
    relationships = {str(item.get("account_ref")): str(item.get("relationship") or "")
                     for item in report.get("competitors") or []}
    cited: list[str] = []
    for index, pattern in enumerate((report.get("patterns") or [])[:3], start=1):
        lines += [f"### {index}. {pattern['pattern']}", "", link_accounts(end_sentence(pattern["mechanism"]), report), ""]
        selected = select_pattern_evidence(pattern, report, content)
        cited += [ref for ref, _ in selected]
        # The same downgrade render_creator_report.py marks: a finding whose proof comes from no direct
        # competitor is adjacent (or media) evidence, and says so before the proof.
        kinds = {relationships.get(str(content[ref].get("account_ref"))) for ref, _ in selected if ref in content}
        if kinds and "direct" not in kinds:
            lines.append(f"*{text['adjacent_evidence' if 'adjacent' in kinds else 'media_evidence']}*")
        for ref, _ in selected:
            line = proof_line(ref, report, content, text)
            if line:
                lines.append(line)
        lines += ["", f"**{text['differentiation']}** {link_accounts(pattern['differentiation'], report)}", ""]
    if not report.get("patterns"):
        # A synthesis with no pattern still names its strongest posts; without them the reply said
        # only that an account beat its usual, with no post, views or multiple to show it (plugins,
        # 2026-10-27).
        standouts = [line for line in (proof_line(str(item.get("content_id")), report, content, text)
                                       for item in (report.get("top_content") or [])[:3]) if line]
        if standouts:
            cited += [str(item.get("content_id")) for item in (report.get("top_content") or [])[:3]]
            lines += [f"### {text['standouts']}", "", *standouts, ""]
    gaps = [g for g in report.get("gaps") or [] if isinstance(g, dict) and g.get("summary")]
    if gaps:
        lines += [f"### {text['gaps']}", ""]
        lines += [f"- {link_accounts(end_sentence(g['summary']), report)}" for g in gaps[:2]]
    missing = [str(ref) for ref in (report.get("coverage") or {}).get("requested_not_compared") or []]
    if missing:
        lines += ["", text["not_compared"].format(refs=", ".join(missing))]
    # A Creator who asked for more accounts than were compared was told only how many were
    # verified, and read that as all that qualified (2026-10). Past the discovery cap
    # the cap is the reason; under it, only the evidence is.
    requested = (report.get("coverage") or {}).get("requested_competitor_count")
    compared = len(roster)
    if isinstance(requested, int) and requested > compared:
        key = "count_cap" if requested > RUN_ACCOUNT_CAP >= compared else "count_short"
        lines += ["", text[key].format(requested=requested, cap=RUN_ACCOUNT_CAP, compared=compared)]
    language = str(((report.get("scope") or {}).get("locale") or {}).get("language") or "en")
    template = BASIS.get(language.split("-")[0].lower(), BASIS["en"])
    basis = basis_sentence(comparison_basis(report), shown_multiple_sample_sizes(report, cited), template)
    if basis:
        lines += ["", f"*{basis}*"]
    return "\n".join(lines).strip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--evidence", type=Path,
                        help="synthesis-evidence.json; defaults to the one next to the report when it is there")
    args = parser.parse_args()
    try:
        report = json.loads(args.report.read_text(encoding="utf-8"))
        source = args.evidence or args.report.parent / "synthesis-evidence.json"
        evidence = json.loads(source.read_text(encoding="utf-8")) if source.is_file() else None
        args.output.write_text(render_reply(report, evidence), encoding="utf-8")
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "failed", "error": f"{type(error).__name__}: {error}"}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
