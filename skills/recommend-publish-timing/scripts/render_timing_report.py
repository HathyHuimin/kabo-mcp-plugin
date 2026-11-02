#!/usr/bin/env python3
"""Render the two documents a timing run produces.

They are projections of two different objects and that separation is the point. The audit document
is built from the PublishRecommendation: every bucketing scheme, the rank tests, the power estimate
kept for reference, the refused fallbacks and the causal boundary. The creator's document is built
from `creator_view` on the task result — the same object the shape gate checks and the same one the
host ships — so the visible report and the structured result cannot drift apart.

They used to be one function reading one object, and the creator half was the delivery path for
nobody: the frozen runs shipped model-written prose that shared no sentence with what it produced.

    python3 render_timing_report.py <publish_recommendation.json> <out_dir> [--result <result.json>]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# A tested-and-empty result is rendered as an answer, not as a refusal with softer wording.
NO_CLEAR_WINDOW = "no_clear_window"

SCHEME_LABEL = {
    "hour_of_day": "by hour",
    "daypart_6h": "by six-hour block",
    "weekpart": "weekday vs weekend",
    "daypart_x_weekpart": "block × weekday/weekend",
}


def label(scheme: str) -> str:
    base = scheme.replace("_utc", "").replace("_local", "")
    return SCHEME_LABEL.get(base, base)


def reason_block(rec: dict) -> dict:
    """A refusal explains itself in `abstain_reason`; a ranked result in `finding`. Reading
    whichever is present would blur them, so the outcome decides which one is read."""
    key = "finding" if rec.get("outcome") == NO_CLEAR_WINDOW else "abstain_reason"
    return rec.get(key) or {}


SCOPE_STRINGS = {
    "zh": {
        "heading": "这次看到了什么",
        "scope": ("本次依据 {window} 的公开 {platform} 数据，共采集 {total} 条{fmt}；"
                  "其中发布满 {maturity} 天的 {mature} 条纳入比较，比较用的指标是{metric}。"),
        "caveat": "公开计数器不是留存数据{missing}；以下结论适合用来安排下一轮发布，不代表因果关系。",
        "missing": "，没有{names}",
        "recheck": "满 {maturity} 天后复看：{hour} 点这一小时的{metric}中位数当前是 {hour_median}，由 {n} 条内容支撑。",
        "join": " · ",
    },
    "en": {
        "heading": "What this run saw",
        "scope": ("From public {platform} data over {window}: {total} {fmt} collected; the "
                  "{mature} of them past {maturity} days are compared, ranked on {metric}."),
        "caveat": ("Public counters are not retention data{missing}. Use this to schedule the next "
                   "posts, not as a causal claim."),
        "missing": ", and no {names}",
        "recheck": ("Re-read after {maturity} days: the {hour}:00 hour's median {metric} is "
                    "{hour_median} today, carried by {n} posts."),
        "join": " · ",
    },
}

PLATFORM_WORDS = {"youtube": "YouTube", "instagram": "Instagram", "tiktok": "TikTok"}

FORMAT_WORDS = {"zh": {"short": " Shorts", "reel": " Reel", "video": " 视频"},
                "en": {"short": " Shorts", "reel": " Reels", "video": " videos"}}


def lang_of(view: dict) -> str:
    return "zh" if str(view.get("language") or "").lower().startswith("zh") else "en"


def linked(label: str, url: str | None) -> str:
    """A label with a URL becomes a link; without one it stays a label.

    Never a bare URL and never a dead link: an account the upstream could not resolve is still a
    real account and still names itself, so it is printed rather than dropped.
    """
    return f"[{label}]({url})" if url else label


def scope_block(rec: dict, code: str) -> list[str]:
    """What was read, before what was concluded.

    The basis used to be one line at the foot of the document, which is where a reader looks last
    and a skimmer never looks at all. Sample size, window and baseline decide how much of the
    answer to believe, so they are stated before the answer is defended.
    """
    text = SCOPE_STRINGS[code]
    base = rec.get("evidence_base") or {}
    cohort = base.get("cohort") or {}
    window = cohort.get("window") or ""
    if ".." in window:
        start, _, end = window.partition("..")
        window = f"{start.strip()[:10]} 至 {end.strip()[:10]}" if code == "zh" \
            else f"{start.strip()[:10]} to {end.strip()[:10]}"
    # The ranked metric is named on `dispersion`; there is no account-level median anywhere in the
    # object, so none is quoted. A baseline the renderer computed would be a number the product
    # contract never agreed to.
    metric = (base.get("dispersion") or {}).get("metric") or ""
    if not (cohort.get("mature_n") and metric):
        return []

    fmt = FORMAT_WORDS[code].get(str(cohort.get("format") or "").lower(), "")
    raw_platform = str(cohort.get("platform") or "")
    platform = PLATFORM_WORDS.get(raw_platform.lower(), raw_platform.title())
    lines = ["", f"## {text['heading']}", "", text["scope"].format(
        window=window, platform=platform,
        total=cohort.get("total_n"), fmt=fmt, maturity=cohort.get("maturity_days"),
        mature=cohort.get("mature_n"), metric=metric_word(metric, code))]

    # The panel records which metrics the platform did not hand over. Naming them here is the
    # difference between "we did not look" and "it was not available".
    absent = [m.get("name") for m in base.get("metric_panel") or []
              if m.get("scope") == "owner" and not m.get("posts_carrying")]
    missing = text["missing"].format(names="、".join(absent) if code == "zh" else ", ".join(absent)) \
        if absent else ""
    lines += ["", text["caveat"].format(missing=missing)]
    return lines


def metric_word(metric: str, code: str) -> str:
    if code != "zh":
        return metric.replace("_", " ")
    return {"view_count": "播放", "like_count": "点赞", "comment_count": "评论",
            "like_rate": "点赞率", "comment_rate": "评论率"}.get(metric, metric)


_EXISTING_LINK = re.compile(r"(\[[^\]]*\]\([^)]*\))")


def link_handles(text: str, rec: dict) -> str:
    """Turn every mention of a comparable account into a link to that account's channel.

    These accounts are the one place the report points outside the creator's own history, so they
    are the one place the comparison can be checked -- and it should be checkable wherever the
    name is read, not only the first time. A handle named in the argument and again in the caveats
    is two places a reader might stop.

    The substitution is driven by the signal's account list, never by guessing what looks like a
    handle: each match is an exact occurrence of a handle the analysis actually used, so it can
    only ever link to that account. Longest first, so "AI Hustlers | Noah & Adam" is not consumed
    by a shorter handle nested inside it, and existing links are stepped over rather than nested.

    `channel_url` only. `sample_url` is one of the account's posts and the analysis layer keeps the
    two apart on purpose; a handle that opens a single video would misreport what the cohort is, so
    an account without a channel page is left as plain text rather than pointed somewhere wrong.
    """
    signal = next((s for s in (rec.get("evidence_base") or {}).get("signals") or []
                   if s.get("signal") == "peer_accounts_hour"), None)
    lookup: dict[str, str] = {}
    for account in (signal or {}).get("accounts") or []:
        # Handles arrive as the platform reports them, trailing spaces and all.
        handle = " ".join(str(account.get("handle") or "").split())
        if handle and account.get("channel_url"):
            lookup.setdefault(handle, account["channel_url"])
    if not lookup:
        return text

    # Handles as the platform reports them carry things a writer drops: an emoji, a trademark sign,
    # a " | N8N Automation" suffix. One recorded run wrote "Sabrina Ramonov", "Santhej Kallada",
    # "AI Hustlers" and "Wapikon" for four accounts whose handles end in 🍄, "| N8N Automation",
    # "| Noah & Adam" and "™" -- so eight names in that sentence were links and four were not, in
    # the same list. A shortened handle is still that account, so a prefix counts, but only when it
    # can be one account: an ambiguous prefix links nothing rather than guessing between two.
    # Uniqueness is judged against the real handles only. Judging it against `lookup` while adding
    # to `lookup` poisons it: every intermediate prefix of "Santhej Kallada | N8N Automation" gets
    # added first, and by the time the useful stem is reached it looks like it belongs to a dozen
    # accounts. Short suffixes ("Wapikon™") still resolved, long ones never did, which is exactly
    # the half-linked line this is here to fix.
    handles = list(lookup)
    for handle in handles:
        for cut in range(len(handle) - 1, 3, -1):
            stem = handle[:cut].rstrip(" |·-—")
            if len(stem) < 4 or stem in lookup:
                continue
            if sum(1 for h in handles if h.startswith(stem)) == 1:
                lookup[stem] = lookup[handle]

    pattern = re.compile("|".join(re.escape(h) for h in
                                  sorted(lookup, key=len, reverse=True)))
    parts = _EXISTING_LINK.split(text)
    for index, part in enumerate(parts):
        if index % 2:  # the captured group: an existing link, left alone
            continue
        parts[index] = pattern.sub(lambda m: f"[{m.group(0)}]({lookup[m.group(0)]})", part)
    return "".join(parts)


def recheck_line(rec: dict, code: str) -> str | None:
    """A recommendation about timing can only be confirmed by later posts, so the document says
    what to compare against rather than leaving `再攒十几条之后重跑一次` unmeasurable."""
    text = SCOPE_STRINGS[code]
    base = rec.get("evidence_base") or {}
    cohort = base.get("cohort") or {}
    window = ((rec.get("recommendation") or {}).get("windows") or [{}])[0]
    # The hour is carried as `start_local` ("14:00"); there is no separate integer field.
    start = str(window.get("start_local") or "")
    if ":" not in start or window.get("resolution") != "hour":
        return None
    hour = start.split(":")[0].lstrip("0") or "0"
    entry = ((base.get("hour_curve") or {}).get("hours") or {}).get(hour) or {}
    hour_median, supporting = entry.get("own_median"), window.get("supporting_n")
    if hour_median is None or not supporting:
        return None
    return text["recheck"].format(
        maturity=cohort.get("maturity_days"), hour=hour, n=supporting,
        metric=metric_word((base.get("dispersion") or {}).get("metric") or "", code),
        hour_median=f"{hour_median:g}")


def creator_view(result: dict, rec: dict | None = None) -> str:
    """Render the Creator-facing document from `creator_view`, not from the engine object.

    The old version built its own document out of the PublishRecommendation with every heading
    hard-coded, and it was the delivery path for nobody: the two frozen runs shipped 275 and 325
    characters of model-written prose sharing not one sentence with what this produced. A renderer
    that disagrees with the thing that ships is worse than no renderer, because it makes the shape
    look settled while the reports drift.

    The block sequence is the analysis frame projected outward — the time, then what it rests on,
    what it is worth, what to discount — and which sequence applies follows from the outcome.
    `validate_creator_shape.py` holds the object to that; this only presents it.
    """
    view = result.get("creator_view") or {}
    code = lang_of(view)
    out = [f"# {view.get('headline', '')}".rstrip()]
    if view.get("summary"):
        out += ["", view["summary"]]
    # The basis goes between the answer and the argument for it, so it is read without deferring
    # the answer. Rendered from the engine object, never from `creator_view`: a sample size the
    # model retyped is a sample size that can be retyped wrong.
    if rec:
        out += scope_block(rec, code)
    for card in view.get("cards") or []:
        out += ["", f"## {card.get('title', '')}".rstrip(), ""]
        for line in (card.get("why_for_you") or "").splitlines():
            if line.strip():
                out.append(f"- {line.strip()}")
        if card.get("action"):
            out += ["", card["action"]]
    actions = [a for a in (view.get("next_actions") or []) if str(a).strip()]
    if actions:
        out += [""] + [f"- {a}" for a in actions]
        recheck = recheck_line(rec, code) if rec else None
        if recheck:
            out += ["", recheck]
    if view.get("data_note"):
        out += ["", view["data_note"]]
    document = "\n".join(out).rstrip() + "\n"
    # Applied to the finished document so a handle is linked wherever it is read -- the argument,
    # the caveats and the next steps all name these accounts. Enumerating fields would leave some
    # mentions plain and make the linked ones look like a selection.
    return link_handles(document, rec) if rec else document


def full_view(rec: dict) -> str:
    base = rec["evidence_base"]
    cohort, power, dispersion = base["cohort"], base["power"], base["dispersion"]
    out = [f"# PublishRecommendation {rec['recommendation_id']}", "",
           f"- Outcome: **{rec['outcome']}**",
           f"- Request: `{rec['request_ref']}`",
           f"- Formula set: `{rec['reproducibility']['formula_set_version']}` · "
           f"snapshot `{(cohort.get('snapshot_sha256') or '')[:12]}`", ""]

    out += ["## Cohort", "", "| field | value |", "|---|---|"]
    for key, value in cohort.items():
        out.append(f"| {key} | {value} |")
    zone = base.get("timezone") or {}
    out += ["", f"Analysis zone **{zone.get('analysis_zone')}** ({zone.get('source')})."]
    if zone.get("note"):
        out += ["", f"> {zone['note']}"]

    out += ["", "## Every bucketing scheme that was evaluated", "",
            "Reporting only the scheme that happens to look best is the failure this contract "
            "exists to prevent.", "",
            "| scheme | buckets | non-empty | largest | smallest | strata with no observation |",
            "|---|---:|---:|---:|---:|---|"]
    for scheme in base["bucketing"]:
        empty = scheme.get("zero_observation_strata") or []
        # Crossed strata are named `weekend|00-06`; an unescaped pipe silently eats the column.
        shown = (", ".join(str(k).replace("|", "\\|") for k in empty) if len(empty) <= 6
                 else f"{len(empty)} strata")
        out.append(f"| `{scheme['scheme']}` ({label(scheme['scheme'])}) | {scheme['buckets_total']} "
                   f"| {scheme['buckets_nonempty']} | {scheme['max_bucket_n']} "
                   f"| {scheme['min_bucket_n']} | {shown or '—'} |")

    out += ["", "### Occupancy and outcome", "",
            "The per-bucket mean is shown with its sensitivity check, never alone.", ""]
    for scheme in base["bucketing"]:
        means = scheme.get("bucket_mean") or {}
        cells = ", ".join(
            f"`{k}`={v}" + (f" (mean {means[k]})" if k in means else "")
            for k, v in (scheme.get("distribution") or {}).items())
        out.append(f"- **{scheme['scheme']}** — {cells or 'empty'}")
        robustness = scheme.get("robustness") or {}
        if robustness.get("note"):
            out.append(f"  - {robustness['note']}")

    stakes = base.get("practical_stakes") or {}
    age = base.get("age_accumulation") or {}
    if stakes or age:
        out += ["", "## Is the question worth answering", ""]
        for note in (stakes.get("note"), stakes.get("gap_note"), age.get("note")):
            if note:
                out.append(f"- {note}")

    significance = base.get("significance") or {}
    if significance:
        out += ["", "## The test that decides this", "",
                significance.get("method", ""), "",
                f"Corrected across **{significance.get('distinct_tests_corrected_for')}** distinct "
                f"partitions at alpha {significance.get('alpha')}.", "",
                "| scheme | tested | omnibus p | Holm | widest pair | observed | detectable |",
                "|---|---|---:|---:|---|---:|---:|"]
        for scheme in significance.get("schemes") or []:
            if not scheme.get("testable"):
                out.append(f"| {label(scheme['scheme'])} | no — {scheme.get('not_testable_because','')[:70]} "
                           f"| | | | | |")
                continue
            pair, kw = scheme["strongest_pair"], scheme["kruskal_wallis"]
            twin = " (same partition as an earlier scheme)" if scheme.get("duplicate_of") else ""
            # Crossed bucket keys contain a pipe, which would end the table cell early.
            higher, lower = (pair["higher"].replace("|", "＋"), pair["lower"].replace("|", "＋"))
            out.append(f"| {label(scheme['scheme'])}{twin} | yes | {kw['p_value']} | "
                       f"{scheme.get('p_holm')} | {higher} vs {lower} | "
                       f"{pair['mann_whitney']['observed_superiority']} | "
                       f"{pair['mann_whitney']['detectable_superiority']} |")
        out += ["", "The widest pair is chosen after seeing the medians, so its own p-value is "
                    "optimistic and decides nothing; the Holm-corrected omnibus does. `detectable` "
                    "is how lopsided a window would have to be before this sample could call it.",
                "", f"Any scheme significant: **{significance.get('any_significant')}**"
                    f"{'' if not significance.get('decisive_scheme') else '  ·  decisive: **' + significance['decisive_scheme'] + '**'}."]

    # Reference only. This estimate stopped being the gate: it assumes the mean is a representative
    # centre, and on a long-tailed metric a couple of viral posts push its sample requirement into
    # the hundreds for a question the ranks above settle on the history in hand.
    out += ["", "## Spread and the old power estimate (reference, not the gate)", "",
            f"Spread: mean {dispersion['mean']}, sd {dispersion['stdev']}, "
            f"CV {dispersion['coefficient_of_variation']}.", "",
            f"`{power['method']}` at alpha {power.get('alpha')}, power {power.get('target_power')}.",
            "", "| difference to detect | posts a mean comparison would need per group |", "|---|---:|"]
    for target in power.get("targets") or []:
        out.append(f"| {int(target['target_relative_effect'] * 100)}% "
                   f"| {target['required_n_per_group']} |")
    out += ["", f"Largest group actually available: **{power['actual_max_group_n']}**. "
                f"These numbers are kept for comparison and no outcome depends on them."]

    if reason_block(rec):
        reason = reason_block(rec)
        out += ["", f"## Why this abstains (`{reason['code']}`)", "", reason["statement"], "",
                "**Missing**", ""]
        out += [f"- {item}" for item in reason.get("missing") or []]
        threshold = reason.get("unlock_threshold") or {}
        out += ["", "**Unlock threshold**", "", "| field | value |", "|---|---|"]
        for key, value in threshold.items():
            if key != "also_requires":
                out.append(f"| {key} | {value} |")
        out += [""] + [f"- {item}" for item in threshold.get("also_requires") or []]

    out += ["", "## Fallbacks deliberately not used", "",
            "Listed so a reader can tell a refusal from a feature that was never built.", ""]
    out += [f"- {item}" for item in rec.get("refused_fallbacks") or []]
    out += ["", "## Causal boundary", "", rec["causal_boundary"],
            "", "## Limitations", ""]
    out += [f"- {item}" for item in rec.get("limitations") or []]

    repro = rec["reproducibility"]
    out += ["", "## Reproducibility", "",
            f"Run `{repro['run_id']}` · generated {repro['generated_at']} · "
            f"input `{(repro.get('input_sha256') or '')[:16]}`", "", f"```\n{repro.get('command')}\n```", ""]
    return "\n".join(out) + "\n"


def main() -> int:
    """Two documents from two different objects.

    The audit document is a projection of the engine and needs nothing else. The creator's document
    is a projection of `creator_view`, so it is only written when a task result is supplied — there
    is nothing for this module to invent if one is not.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recommendation", type=Path)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--result", type=Path, default=None,
                        help="CreatorTaskResult carrying the creator_view to render")
    args = parser.parse_args()

    rec = json.loads(args.recommendation.read_text(encoding="utf-8"))
    args.out_dir.mkdir(parents=True, exist_ok=True)
    written = ["PUBLISH_TIMING_FULL.md"]
    (args.out_dir / "PUBLISH_TIMING_FULL.md").write_text(full_view(rec), encoding="utf-8")
    if args.result:
        result = json.loads(args.result.read_text(encoding="utf-8"))
        (args.out_dir / "PUBLISH_TIMING.md").write_text(creator_view(result, rec), encoding="utf-8")
        written.insert(0, "PUBLISH_TIMING.md")
    print(f"wrote {', '.join(written)} into {args.out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
