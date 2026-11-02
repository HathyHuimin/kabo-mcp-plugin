#!/usr/bin/env python3
"""Decide whether this creator's history can support a publish-time claim, and say why not.

The model narrates; it does not decide. Every number a recommendation rests on — bucket
occupancy, dispersion, the sample a comparison would need, the unlock threshold — is computed
here. Given the same PublishRequest, the same PublishHistory and the same RULE_VERSION this
produces byte-identical output.

Two rules drive the whole engine:

  Every bucketing scheme that was considered gets reported, not just the one that looks best.
  A stratum with no observations is unknown, never average.

    python3 compute_timing.py <publish_request.json> <publish_history.json> --full
        [--run-id <id>] [--generated-at <iso>] [--output <file>]

`--full` is what the Skill runs. Without it the engine block alone is emitted, carrying no
`schema_version`, `recommendation_id` or `refused_fallbacks`, and both
`validate_publish_recommendation.py` and `build_publish_schedule.py` then fail on it.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# This module imports a sibling at runtime when a peer snapshot is supplied. The skill directory is
# a frozen, hashed asset inventory, so it must not gain a `__pycache__` the moment it is executed.
sys.dont_write_bytecode = True

RULE_VERSION = "publish-timing.v4"

# What decides the answer. v2 gated on whether a rank test could detect a timing effect in the
# creator's own history; across five real accounts it never could, because publishers do not post
# into their own bad hours and observational history therefore has no control group. No amount of
# further posting creates one. So v3 stops asking whether an effect is provable and ranks the
# windows the creator has actually used, reporting how many metrics agree on the winner.
#
# Agreement is a confidence a creator can act on. "Four of five metrics put 06-09 first" is a
# statement they can weigh; "detectable superiority 0.66" is not.
GRANULARITIES = (3, 6, 12)

# An hour is what a creator can act on; a six-hour block is not a time, it is a third of a working
# day. The fixed grid also decides the answer by where its boundaries fall: on the Dealism cohort
# the 16-17h cluster sits inside `12-18`, is averaged against four weak hours, and loses to a
# `06-12` block holding five posts. So the hour is resolved first, on a circular kernel, and the
# grid is kept only as the fallback for histories too thin to carry it.
KERNEL_HALF_WIDTH_HOURS = 1.5

# Posts inside the +/-1.5h window before an hour can be read at all. Below this the "median" is one
# or two posts wearing a statistic's clothes.
MIN_POSTS_IN_HOUR_WINDOW = 5

# ...and this many of that hour's *own* posts before it can be named as the answer.
#
# Without it the neighbours decide. A first version pooled the three hours with equal weight per
# post, which sounds neutral and is not: an hour holding one post, sitting next to an hour holding
# five, inherits its neighbour's median wholesale. On the Dealism cohort that put the peak at 13:00
# — one post of its own — while the five strong posts were all at 14:00, and the recommendation was
# an hour early. The hour must carry its own evidence; neighbours only steady it.
MIN_OWN_POSTS_AT_HOUR = 3

# Weight of the hour itself against each neighbour when the three are combined. The neighbours are
# there to stop one lucky post at 14:00 from reading as a pattern, not to outvote 14:00.
CENTRE_WEIGHT = 2

# How far the peak must stand above the hours that are not its neighbours. Measured on the two real
# cohorts: YouTube's 16:00 reaches 4.28x and survives leave-one-out; Instagram's 17:00 reaches 1.34x
# and does not. The floor sits between them, nearer the failure, because a peak that only just
# clears it is exactly the one leave-one-out has to catch.
MIN_PEAK_PROMINENCE = 1.8

# Hours within this circular distance of the peak are its neighbours and cannot serve as its
# baseline: a kernel 1.5h wide shares posts with them by construction.
PEAK_NEIGHBOUR_RADIUS = 2

# What the peer signal is allowed to do. It breaks a tie inside the hours the creator's own history
# already ranks highest; it never introduces an hour their own history does not support, and it
# never moves a peak their own history resolved on its own.
PEER_TIE_BREAK_TOP_K = 3

# A window needs this many posts before it can be ranked at all. Below it a single viral post is
# the entire window. Measured across five accounts: at three hours with this floor, every one of
# them has between three and seven rankable windows.
MIN_POSTS_PER_WINDOW = 3

# Confidence thresholds on the share of metrics choosing the winner.
STRONG_AGREEMENT = 0.75
WINNER_N_FOR_HIGH = 5

ALPHA = 0.05
TARGET_POWER = 0.8

# A stratum holding one post contributes no ranking information and inflates nothing but the
# bucket count. Two is the floor to enter a comparison; whether the comparison can reach
# significance at all is a separate check, made per pair against the exact rank distribution.
MIN_GROUP_N = 2

SIGNIFICANCE_METHOD = (
    "Kruskal-Wallis omnibus per bucketing scheme on the raw metric, Holm-Bonferroni across the "
    "schemes tested, Mann-Whitney on the strongest pair. Rank based on purpose: view counts are "
    "long-tailed and a mean comparison mistakes two viral posts for a publishing pattern."
)
# What a creator would plausibly act on. 30% is the middle target and the one the unlock
# threshold is quoted against; the other two show how the requirement moves with ambition.
TARGET_EFFECTS = (0.2, 0.3, 0.5)

DAYPARTS = ("00-06", "06-12", "12-18", "18-24")

POWER_METHOD = ("two-sample comparison of means, n_per_group = 2 * (z_(1-alpha/2) + "
                "z_(1-beta))^2 * CV^2 / effect^2")

# Policy, not narrative. These two are constants because a boundary that can be reworded per run
# is not a boundary, and a refusal list that varies per run cannot tell a reader whether a fallback
# was declined or simply never built.
CAUSAL_BOUNDARY = (
    "Even at sufficient sample size, differences between publish-hour buckets in organic history "
    "are correlational. Topic, format, length, hook quality, post age and audience mix all vary "
    "across these buckets and none of them is controlled. A powered comparison would license the "
    "statement 'posts in this window performed better', never 'publishing in this window will "
    "perform better'."
)

# The line these draw is not "only this creator's own data". V3 reads comparable accounts when an
# upstream supplies them, and the second entry used to forbid that in as many words. The line that
# actually matters is whether a creator can go and check: a named account with a URL can be opened
# and disagreed with, a chart cannot be asked who it counted. So the criterion is stated instead of
# the proxy, and the peer signal is held to it by `validate_creator_shape.py`.
REFUSED_FALLBACKS = [
    "Generic niche or platform-wide best-time charts.",
    "Third-party 'best time to post' heuristics, and any aggregate of other accounts that cannot "
    "be opened and checked. Comparable accounts may inform the answer only while every one of them "
    "is named, linkable, and classified as comparable by the source that collected it.",
    "Peak-hour defaults such as weekday afternoons carried over from other tools.",
    "Filling empty hours or absent weekpart strata with the account mean, or with peer numbers.",
    "Reporting the single best-looking bucketing scheme while hiding the ones that fail.",
    "Peer samples collected by picking winners. Without a denominator they say when the hits went "
    "out, which cannot be turned into when to post.",
]


def z(p: float) -> float:
    return statistics.NormalDist().inv_cdf(p)


def required_n(cv: float, effect: float) -> int:
    """Sample per group for a two-sample comparison of means at the observed spread.

    Kept for reference only. It is no longer a gate, because it is wrong for this metric: the
    coefficient of variation assumes the mean is a representative centre, and view counts are
    long-tailed. On the Dealism cohort two viral videos put CV at 1.4579 against a median near 100,
    and CV enters the formula squared — so it asked for 371 posts per bucket, about 12 years of
    publishing, to answer a question the rank tests below settle on the sample already in hand.
    """
    return math.ceil(2 * (z(1 - ALPHA / 2) + z(TARGET_POWER)) ** 2 * cv ** 2 / effect ** 2)


# ------------------------------------------------------------------ distribution-free comparison
# What the creator asks is "does the hour I publish change how a post does". That is a question
# about whether one group tends to outrank another, and rank tests answer it without assuming any
# shape. A handful of viral posts moves a mean a long way and a rank ordering barely at all, which
# is exactly the robustness a view-count comparison needs.

def _chi2_sf(x: float, k: int) -> float:
    """Upper tail of a chi-square with k degrees of freedom, via the regularised gamma Q(k/2, x/2).

    Series below the transition point, Lentz's continued fraction above it. Standard Numerical
    Recipes decomposition; both branches converge to better than 1e-12 here.
    """
    if x <= 0:
        return 1.0
    a, xx = k / 2.0, x / 2.0
    log_gamma = math.lgamma(a)
    if xx < a + 1.0:
        term = 1.0 / a
        total = term
        n = a
        for _ in range(1000):
            n += 1.0
            term *= xx / n
            total += term
            if abs(term) < abs(total) * 1e-15:
                break
        return max(0.0, min(1.0, 1.0 - total * math.exp(-xx + a * math.log(xx) - log_gamma)))
    tiny = 1e-300
    b, c, d = xx + 1.0 - a, 1.0 / tiny, 1.0 / (xx + 1.0 - a)
    h = d
    for i in range(1, 1000):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < tiny:
            d = tiny
        c = b + an / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-15:
            break
    return max(0.0, min(1.0, h * math.exp(-xx + a * math.log(xx) - log_gamma)))


def _ranks(values: list[float]) -> tuple[list[float], float]:
    """Midranks, plus the tie term the rank-test variances need."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    tie_term = 0.0
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        shared = (i + j + 2) / 2.0
        for k in range(i, j + 1):
            ranks[order[k]] = shared
        run = j - i + 1
        tie_term += run ** 3 - run
        i = j + 1
    return ranks, tie_term


def smallest_reachable_p(n1: int, n2: int) -> float:
    """The two-sided p a Mann-Whitney can reach at these group sizes when the separation is total.

    This is the honest floor on a comparison's resolution. Three against three cannot go below
    0.1 however cleanly the groups separate, so calling it "not enough data" is a statement about
    arithmetic rather than about the account.
    """
    return min(1.0, 2.0 / math.comb(n1 + n2, n1))


def detectable_superiority(n1: int, n2: int) -> float:
    """How lopsided the head-to-head would have to be before this test would call it.

    A negative result is only as strong as the test's resolution, and rank tests on small groups
    have very little. Expressed as the probability that a post from the higher stratum beats one
    from the lower, picked at random — 0.5 is no difference, and this is the value at which the
    two-sided test at alpha would just reject. Reporting it stops "not significant" from being
    read as "no effect" when the groups are seven against four.
    """
    return round(0.5 + z(1 - ALPHA / 2) * math.sqrt((n1 + n2 + 1) / (12.0 * n1 * n2)), 4)


def mann_whitney(a: list[float], b: list[float]) -> dict:
    """Two-sided Mann-Whitney U with tie-corrected normal approximation."""
    ranks, tie_term = _ranks(a + b)
    n1, n2, total = len(a), len(b), len(a) + len(b)
    rank_sum_a = sum(ranks[:n1])
    u = rank_sum_a - n1 * (n1 + 1) / 2.0
    mu = n1 * n2 / 2.0
    report = {"u": round(u, 1),
              # Observed probability of superiority; the effect size the p-value is testing.
              "observed_superiority": round(u / (n1 * n2), 4),
              "detectable_superiority": detectable_superiority(n1, n2),
              "smallest_reachable_p": round(smallest_reachable_p(n1, n2), 6)}
    variance = n1 * n2 / (total * (total - 1.0)) * ((total ** 3 - total) / 12.0 - tie_term / 12.0)
    if variance <= 0:
        return {**report, "z": None, "p_value": 1.0}
    z_score = (u - mu) / math.sqrt(variance)
    return {**report, "z": round(z_score, 4),
            "p_value": round(math.erfc(abs(z_score) / math.sqrt(2)), 6)}


def kruskal_wallis(groups: dict) -> dict:
    """Omnibus rank test: do any of these buckets tend to outrank the others?"""
    flat = [v for values in groups.values() for v in values]
    ranks, tie_term = _ranks(flat)
    total = len(flat)
    h_stat, offset = 0.0, 0
    for values in groups.values():
        n = len(values)
        rank_sum = sum(ranks[offset:offset + n])
        h_stat += rank_sum ** 2 / n
        offset += n
    h_stat = 12.0 / (total * (total + 1)) * h_stat - 3 * (total + 1)
    correction = 1.0 - tie_term / (total ** 3 - total) if total ** 3 != total else 1.0
    if correction > 0:
        h_stat /= correction
    df = len(groups) - 1
    return {"h": round(h_stat, 4), "df": df,
            "p_value": round(_chi2_sf(h_stat, df), 6),
            "tie_corrected": tie_term > 0}


def holm(p_values: dict) -> dict:
    """Holm-Bonferroni across the schemes tested. Four bucketings on one history is four chances to
    find a pattern; without this the family-wise error rate is not the alpha we quote."""
    ordered = sorted(p_values.items(), key=lambda kv: kv[1])
    adjusted, running = {}, 0.0
    for index, (key, raw) in enumerate(ordered):
        running = max(running, min(1.0, raw * (len(ordered) - index)))
        adjusted[key] = round(running, 6)
    return adjusted


def parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def resolve_zone(name: str) -> ZoneInfo | None:
    """The creator's IANA zone, or None when the host supplied something that is not one."""
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        return None


def localise(posts: list, zone: ZoneInfo) -> list:
    """Restate every publish instant in the creator's zone, same instant, local wall clock.

    Platforms export an absolute instant — YouTube's `publishedAt` and Instagram's `timestamp` both
    carry +00:00 — so nothing is lost and nothing is guessed: `astimezone` is exact. Doing it once,
    here, is what lets every `parse(...).hour` below read as the creator's own clock without
    threading a zone through twenty call sites.
    """
    out = []
    for post in posts:
        moved = dict(post)
        moved["published_at"] = parse(post["published_at"]).astimezone(zone).isoformat()
        out.append(moved)
    return out


def bucket_report(scheme: str, assignments: list, universe: list,
                  values: list | None = None) -> dict:
    """One row per scheme. `universe` is every stratum the scheme can produce, so the empty
    ones stay visible instead of silently not existing.

    The per-bucket mean is reported even though it is the number most likely to be misread. A
    bare-baseline run showed why: without it nobody can see that the leading bucket is one post
    deep, or that the whole spread between buckets is smaller than the spread inside one. Hiding
    the outcome does not stop the wrong reading; showing it next to the sensitivity check does.
    """
    counts = {key: 0 for key in universe}
    grouped: dict = {key: [] for key in universe}
    for index, key in enumerate(assignments):
        counts[key] += 1
        if values is not None:
            grouped[key].append(values[index])
    nonempty = {k: v for k, v in counts.items() if v}
    report = {
        "scheme": scheme,
        "buckets_total": len(universe),
        "buckets_nonempty": len(nonempty),
        "max_bucket_n": max(nonempty.values()) if nonempty else 0,
        "min_bucket_n": min(nonempty.values()) if nonempty else 0,
        "zero_observation_strata": [k for k in universe if not counts[k]],
        "distribution": {str(k): v for k, v in nonempty.items()},
    }
    if values is not None and nonempty:
        report["bucket_mean"] = {str(k): round(statistics.mean(grouped[k]), 1)
                                 for k in universe if counts[k]}
        # The mean's leader, and the recommendation does not use it. `significance.method` says why
        # in the same object — a mean comparison mistakes two viral posts for a publishing pattern —
        # so an isolated run that took `leading_bucket` at face value got 17:00 while the ranking
        # said 12-15 and reported the object as contradicting itself. Say which statistic it is,
        # and give no leader at all where the scheme has nothing to lead.
        report["leading_bucket"] = (max(report["bucket_mean"], key=report["bucket_mean"].get)
                                    if len(nonempty) >= 2 else None)
        report["leading_bucket_statistic"] = "mean" if report["leading_bucket"] else None
        report["robustness"] = leave_one_out(grouped, counts)
    return report


def leave_one_out(grouped: dict, counts: dict) -> dict:
    """Does dropping a single post change which bucket leads? On a thin history it usually does,
    and a leader that survives no single deletion is not a finding."""
    occupied = {k: v for k, v in grouped.items() if counts[k]}
    if len(occupied) < 2:
        return {"leader_survives_any_single_deletion": None,
                "note": "Fewer than two occupied buckets; there is nothing to rank."}
    means = {k: statistics.mean(v) for k, v in occupied.items()}
    leader = max(means, key=means.get)
    for key, values in occupied.items():
        for index in range(len(values)):
            trimmed = values[:index] + values[index + 1:]
            if not trimmed:
                continue
            shuffled = dict(means)
            shuffled[key] = statistics.mean(trimmed)
            if max(shuffled, key=shuffled.get) != leader:
                return {"leader_survives_any_single_deletion": False,
                        "note": (f"Removing one post from {key} moves the lead away from "
                                 f"{leader}. The ranking rests on a single observation.")}
    return {"leader_survives_any_single_deletion": True,
            "note": f"{leader} still leads after removing any single post."}


def metric_values(posts: list, name: str) -> list:
    """Values for one metric, skipping rows that do not carry it. A partial export is normal."""
    return [p["metrics"][name] for p in posts if p["metrics"].get(name) is not None]


def metric_posts(posts: list, name: str) -> list:
    """Keep whole rows carrying the metric, so values and publish times stay paired."""
    return [post for post in posts if post["metrics"].get(name) is not None]


def build_bucketing(posts: list, suffix: str, primary: str) -> list[dict]:
    posts = metric_posts(posts, primary)
    hours = [parse(p["published_at"]).hour for p in posts]
    dayparts = [DAYPARTS[h // 6] for h in hours]
    weekparts = ["weekend" if parse(p["published_at"]).weekday() >= 5 else "weekday" for p in posts]
    crossed = [f"{w}|{d}" for w, d in zip(weekparts, dayparts)]
    values = metric_values(posts, primary) or None
    return [
        bucket_report(f"hour_of_day{suffix}", hours, list(range(24)), values),
        bucket_report(f"daypart_6h{suffix}", dayparts, list(DAYPARTS), values),
        bucket_report(f"weekpart{suffix}", weekparts, ["weekday", "weekend"], values),
        bucket_report(f"daypart_x_weekpart{suffix}", crossed,
                      [f"{w}|{d}" for w in ("weekday", "weekend") for d in DAYPARTS], values),
    ]


def practical_stakes(posts: list, mean: float, metric_name: str) -> dict:
    """What is actually on the table, and whether the question is worth the wait.

    A bare-baseline run made the point that no amount of sample size answers: at 60–240 views a
    post, a 30% timing difference is about 45 views. Telling a creator to spend five months
    collecting evidence for that is pointing them at the wrong lever, and the contract should say
    so rather than only reporting that it cannot answer yet.
    """
    values = sorted(metric_values(posts, metric_name))
    times = sorted(parse(p["published_at"]) for p in posts)
    gaps = [(b - a).days for a, b in zip(times, times[1:])]
    return {
        "metric_range": [values[0], values[-1]],
        "detectable_absolute_effect_at_30_percent": round(mean * 0.3, 1),
        "note": (f"A 30 percent difference on this account is about {round(mean * 0.3)} "
                 f"{metric_name} per post, against a range of {values[0]} to {values[-1]}. "
                 f"Publish time is unlikely to be the largest lever available at this scale; "
                 f"hook and topic vary far more."),
        "longest_publishing_gap_days": max(gaps) if gaps else 0,
        "gap_note": (f"The longest break in this window is {max(gaps)} days. A break that long "
                     f"changes reach on its own, and posts either side of it are not really the "
                     f"same cohort." if gaps and max(gaps) >= 14 else None),
    }


def age_accumulation_check(posts: list, primary: str) -> dict:
    """Test the maturity confound instead of only asserting it.

    Split the cohort by age and compare. On the sampled account the older half averaged almost
    exactly what the newer half did, which means lifetime views plateau quickly here and the
    confound, while real in principle, is small in practice. Stating the caveat without measuring
    it would have been the easier and less honest option.
    """
    posts = metric_posts(posts, primary)
    if len(posts) < 4:
        return {"measured": False, "note": "Too few posts carrying the metric to split by age."}
    ordered = sorted(posts, key=lambda p: p["published_at"])
    half = len(ordered) // 2
    older = statistics.mean(metric_values(ordered[:half], primary))
    newer = statistics.mean(metric_values(ordered[-half:], primary))
    ratio = (older - newer) / newer if newer else 0
    # An odd cohort has no middle: the two halves cover len-1 posts and the median-aged one enters
    # neither. That is fine as a split and was not fine as a silent one — an isolated run read
    # `dispersion.mean` of 148.737 against halves of 145.3 and 141.9, could not reconcile them, and
    # reported the object as self-contradicting. It was one excluded post, so now it says so.
    excluded = len(ordered) - 2 * half
    return {
        "measured": True,
        "older_half_mean": round(older, 1),
        "newer_half_mean": round(newer, 1),
        "posts_per_half": half,
        "posts_excluded_from_both_halves": excluded,
        "relative_gap": round(ratio, 3),
        "note": (f"The older half averages {round(older, 1)} against {round(newer, 1)} for the "
                 f"newer half, a gap of {round(ratio * 100, 1)} percent. "
                 + (f"Each half holds {half} of the {len(ordered)} posts; the median-aged post "
                    f"belongs to neither, so these two means do not average back to the cohort mean. "
                    if excluded else f"Each half holds {half} posts. ")
                 + ("Lifetime accrual is visible in this cohort and inflates older buckets."
                    if abs(ratio) > 0.15 else
                    "Lifetime accrual is not visibly inflating the older posts here, so the "
                    "confound is real in principle but small in this cohort.")),
    }


def bucket_keys(posts: list, scheme: str) -> list:
    if scheme.startswith("hour_of_day"):
        return [parse(p["published_at"]).hour for p in posts]
    if scheme.startswith("daypart_x_weekpart"):
        return [f"{'weekend' if parse(p['published_at']).weekday() >= 5 else 'weekday'}"
                f"|{DAYPARTS[parse(p['published_at']).hour // 6]}" for p in posts]
    if scheme.startswith("daypart_6h"):
        return [DAYPARTS[parse(p["published_at"]).hour // 6] for p in posts]
    return ["weekend" if parse(p["published_at"]).weekday() >= 5 else "weekday" for p in posts]


def load_peer_signal(request: dict, primary: str, zone_name: str) -> dict:
    """Signal 3: the comparable accounts, when an upstream has handed us some.

    Absent by default. A creator asking when to post has not necessarily run a competitor study,
    and this signal reporting `unavailable` is the normal case rather than a degraded one.
    """
    absent = {"signal": "peer_accounts_hour", "status": "not_provided", "hour": None,
              "accounts": [], "posts_used": 0,
              "code": "peer_sample_not_supplied", "params": {},
              "detail": "No comparable-account sample was supplied this run, so this signal "
                        "did not take part."}
    ref = request.get("peer_snapshot_ref")
    if not ref:
        return absent
    path = Path(ref)
    if not path.is_absolute():
        base = request.get("__request_path__")
        for candidate in ([Path(base).parent / ref] if base else []) + [Path.cwd() / ref]:
            if candidate.exists():
                path = candidate
                break
    if not path.exists():
        absent["status"] = "unreadable"
        absent["code"] = "peer_sample_unreadable"
        absent["params"] = {"ref": str(ref)}
        absent["detail"] = f"The comparable-account sample could not be read: {ref}"
        return absent
    try:
        import peer_hour_profile
    except ImportError:
        absent["status"] = "unreadable"
        absent["code"] = "peer_module_unavailable"
        absent["params"] = {}
        absent["detail"] = "The comparable-account module is not available."
        return absent
    document = json.loads(path.read_text(encoding="utf-8"))
    return peer_hour_profile.profile(document, request["creator"]["account_ref"], primary, zone_name)


def weekpart_signal(posts: list, panel: list, note: dict | None) -> dict:
    """Signal 2. Supplementary by design: it narrows the day, never the hour.

    When it has nothing to say it has to say which nothing. "Not enough posts" and "the metrics
    each picked a different day" are different facts and lead the creator to different next steps —
    one is fixed by posting more, the other is not fixed by anything.
    """
    if note:
        return {"signal": "own_history_weekpart", "status": "available", "hour": None,
                "stronger": note["stronger"], "resolution": note.get("resolution"),
                "code": f"weekpart_{note.get('resolution') or 'resolved'}",
                "params": dict(note.get("params") or {}),
                "detail": note["statement"]}

    by_day: dict = {index: 0 for index in range(7)}
    for post in posts:
        by_day[parse(post["published_at"]).weekday()] += 1
    eligible = [index for index, count in by_day.items() if count >= MIN_OWN_POSTS_AT_HOUR]
    weekend = sum(count for index, count in by_day.items() if index >= 5)
    weekday = sum(by_day.values()) - weekend

    if min(weekday, weekend) < MIN_POSTS_PER_WINDOW:
        thin = "weekend" if weekend < weekday else "weekday"
        return {"signal": "own_history_weekpart", "status": "insufficient_sample", "hour": None,
                "code": "weekpart_half_too_thin",
                "params": {"thin_half": thin, "weekday_n": weekday, "weekend_n": weekend,
                           "minimum_posts_per_window": MIN_POSTS_PER_WINDOW},
                "detail": (f"Too few {thin} posts to tell the halves apart "
                           f"({weekday} on weekdays, {weekend} at weekends).")}
    if len(eligible) < 4:
        return {"signal": "own_history_weekpart", "status": "insufficient_sample", "hour": None,
                "code": "too_few_days_used",
                "params": {"days_eligible": len(eligible),
                           "minimum_own_posts": MIN_OWN_POSTS_AT_HOUR},
                "detail": (f"Only {len(eligible)} day(s) of the week hold {MIN_OWN_POSTS_AT_HOUR} "
                           f"posts or more of their own, which is not enough to name one day.")}
    return {"signal": "own_history_weekpart", "status": "metrics_disagree", "hour": None,
            "code": "weekday_metrics_disagree", "params": {},
            "detail": ("The metrics each picked a different best day, so no day carries a "
                       "majority.")}


def audience_signal() -> dict:
    """Signal 4, declared and empty.

    A benchmarked competitor answers this question from subscriber-activity data. We have no
    connector that reports when an audience is online, and there is no dimension in the analytics we do hold that stands
    in for it — views by day says when people watched, which is a statement about distribution, not
    about when the audience was awake. Declaring the slot keeps the gap visible instead of letting
    the answer look more complete than it is.
    """
    return {"signal": "audience_online", "status": "no_connector", "hour": None,
            "code": "audience_online_unavailable", "params": {},
            "detail": "No source reports when this audience is online, so this signal did not "
                      "take part."}


def candidate_hours(request: dict) -> set | None:
    """Hours the host proposed. None when it proposed nothing, which is the usual case."""
    windows = request.get("candidate_windows") or []
    if not windows:
        return None
    hours = set()
    for window in windows:
        try:
            start = int(str(window["start_local"]).split(":")[0])
            end = int(str(window["end_local"]).split(":")[0])
        except (KeyError, ValueError):
            continue
        span = (end - start) % 24 or 24
        for offset in range(span):
            hours.add((start + offset) % 24)
    return hours or None


def compute(request: dict, history: dict) -> dict:
    content = request["content"]
    creator = request["creator"]
    panel = history["metric_panel"]
    # Every descriptive count and timing assignment uses the primary-metric cohort.
    # Secondary-only rows cannot inflate its sample or shift values onto other timestamps.
    posts = metric_posts([p for p in history["posts"]
                          if p["is_mature"] and p["product_type"] == content["format"]],
                         panel[0]["name"])
    posts.sort(key=lambda p: p["published_at"])

    # The first panel entry is the one the descriptive blocks below are keyed on. It is the metric
    # a creator names when they say "how did it do", and the panel is ordered to put it first.
    metric = panel[0]
    coverage = history["coverage"]
    maturity_days = history["maturity"]["days"]

    cohort = {
        "platform": history["platform"],
        "format": content["format"],
        "maturity_days": maturity_days,
        "mature_n": len(posts),
        # The cohort's own total, not the account catalogue. `catalog_posts` counts every
        # product type; this cohort is one format, so it reported 35 where the truth was 24.
        # yutian caught this in V2.5 and it is adopted here so the two stop diverging.
        "total_n": coverage["cohort_total"],
        "window": f"{posts[0]['published_at']} .. {posts[-1]['published_at']}" if posts else None,
        "source_mode": history["source"]["mode"],
        "snapshot_sha256": history["source"]["snapshot_sha256"],
    }

    metric_block = {
        "name": metric["name"],
        "period": metric["period"],
        "denominator": metric["denominator"],
        "denominator_known": metric["denominator_known"],
        "maturity_confound": (
            f"Lifetime {metric['name']} accumulate, so an older post carries more of the metric "
            f"than a newer one regardless of the hour it was published. Restricting to posts at "
            f"least {maturity_days} days old reduces but does not remove this."
        ) if metric["accumulates_with_age"] else None,
    }

    # Every export carries an absolute instant — YouTube's publishedAt and Instagram's timestamp
    # both come with an offset — so the creator's local hour is a conversion, not a guess, and the
    # only thing needed to perform it is the zone. The host supplies that. An earlier version
    # declared local time unrecoverable and answered in UTC on every run; the information was there
    # the whole time and the answer was simply never converted.
    zone_value = str(creator.get("timezone") or "").strip()
    zone_source = creator.get("timezone_source")
    supplied_sources = {"creator_confirmed", "profile_confirmed", "assumed"}
    # Provenance wins over a stray value. A producer that says not_supplied must never smuggle a
    # resolvable machine/operator zone into the Creator's local-hour analysis.
    zone = None if zone_source == "not_supplied" or not zone_value else resolve_zone(zone_value)
    if zone is not None:
        normalized_source = zone_source if zone_source in supplied_sources else "assumed"
        zone_block = {"analysis_zone": zone_value, "source": normalized_source,
                      "note": None}
        suffix = "_local"
        posts = localise(posts, zone)
        if posts:
            cohort["window"] = f"{posts[0]['published_at']} .. {posts[-1]['published_at']}"
    elif zone_source == "not_supplied" or not zone_value:
        zone_block = {
            "analysis_zone": "UTC",
            "source": "not_supplied",
            "note": ("No creator timezone was supplied, so the hours below remain in UTC. "
                     "UTC is an analysis fallback, not an assumed creator timezone."),
        }
        suffix = "_utc"
    else:
        zone_block = {
            "analysis_zone": "UTC",
            "source": "zone_unresolved",
            "note": (f"The request carries timezone {zone_value!r}, which is not an "
                     f"IANA zone this runtime can resolve, so the hours below stay in UTC."),
        }
        suffix = "_utc"

    primary = metric["name"]

    if not posts:
        return no_history_result(cohort, metric_block, zone_block, history, suffix, primary)

    # Before anything is bucketed: was the independent variable observed at all? A source that
    # reports every post at midnight has no hour to bucket, and one that estimates the date has
    # no day either. This outranks sample size — more posts from the same source add nothing.
    if history["timestamps"].get("time_of_day_recoverable") is False:
        return no_publish_time_result(posts, cohort, metric_block, zone_block, history, suffix, primary)

    values = metric_values(posts, primary)
    mean = statistics.mean(values)
    stdev = statistics.stdev(values) if len(values) > 1 else 0.0
    cv = stdev / mean if mean else 0.0
    dispersion = {"metric": metric["name"], "mean": round(mean, 3), "stdev": round(stdev, 3),
                  "coefficient_of_variation": round(cv, 4)}

    bucketing = build_bucketing(posts, suffix, primary)
    # The coarsest scheme that still contrasts times of day is the one an account can realistically
    # reach; the finer ones are reported but never set the threshold.
    daypart = next(b for b in bucketing if b["scheme"].startswith("daypart_6h"))
    actual_max = max(b["max_bucket_n"] for b in bucketing
                     if not b["scheme"].startswith("weekpart"))

    targets = [{"target_relative_effect": e, "required_n_per_group": required_n(cv, e)}
               for e in TARGET_EFFECTS]
    needed = next(t["required_n_per_group"] for t in targets if t["target_relative_effect"] == 0.3)
    power = {
        "method": POWER_METHOD,
        "alpha": ALPHA,
        "target_power": TARGET_POWER,
        "targets": targets,
        "actual_max_group_n": actual_max,
        "powered": actual_max >= needed,
    }

    # The rank tests stay, but as an audit layer: "is this ranking also statistically separable"
    # is worth recording and decides nothing. What decides is the ranking below.
    significance = significance_report(posts, bucketing, primary)
    ranking = rank_windows(posts, panel)
    ranking["analysis_zone"] = zone_block["analysis_zone"]
    ranking["weekday_note"] = weekday_note(posts, panel)
    age = age_accumulation_check(posts, primary)

    # Signals, in the order they carry weight. Each one states its own status, so a missing source
    # shows up as a named gap rather than shortening the answer without saying why. Only signal 1
    # can resolve an hour on its own; the rest corroborate, narrow the day, or declare themselves
    # absent. That asymmetry is deliberate: the question is about this creator's account.
    peer = load_peer_signal(request, primary, zone_block["analysis_zone"])
    hour_signal = resolve_hour(posts, panel, peer)
    signals = [hour_signal, weekpart_signal(posts, panel, ranking.get("weekday_note")),
               peer, audience_signal()]

    hour = hour_signal["hour"]
    proposed = candidate_hours(request)
    constrained = None
    if hour is not None and proposed is not None and hour not in proposed:
        entry = panel[0]
        curve = hour_curve(posts, entry["name"])
        allowed = [h for h in top_hours(curve, entry["direction"] == "lower_is_better", 24)
                   if h in proposed]
        constrained = {"engine_hour": hour, "allowed": sorted(proposed),
                       "chosen": allowed[0] if allowed else None}
        hour = allowed[0] if allowed else None

    curve_block = hour_curve(posts, primary)
    evidence_base = {"cohort": cohort, "metric_panel": panel, "timezone": zone_block,
                     "bucketing": bucketing, "dispersion": dispersion, "power": power,
                     "significance": significance, "ranking": ranking,
                     "hour_curve": {"kernel_half_width_hours": KERNEL_HALF_WIDTH_HOURS,
                                    "minimum_posts_in_window": MIN_POSTS_IN_HOUR_WINDOW,
                                    "minimum_own_posts": MIN_OWN_POSTS_AT_HOUR,
                                    "minimum_prominence": MIN_PEAK_PROMINENCE,
                                    "hours": curve_block,
                                    "host_constrained": constrained},
                     "signals": signals,
                     "practical_stakes": practical_stakes(posts, mean, metric["name"]),
                     "age_accumulation": age}

    # Everything below is a ranking, not a hypothesis test. The only refusals left are the two
    # cases where there is genuinely nothing to rank; thin samples, an unconfirmed zone and age
    # skew subtract from confidence and say so, because refusing on them left five real accounts
    # with no answer at all.
    penalties = confidence_penalties(posts, ranking, zone_block, metric, age, maturity_days,
                                     hour, peer, curve_block)

    # An hour when the history can carry one; the grid only when it cannot. The grid is kept
    # because a coarse answer beats no answer, but it is second: its boundaries are arbitrary and
    # on the one cohort where both can answer they disagree, with the hour reading 2.8x higher on
    # the same metric. A block that wins because the grid split its rival is not a finding.
    if hour is not None:
        return {"outcome": "recommended",
                "recommendation": build_recommendation(ranking, penalties, hour_signal, hour,
                                                       zone_block, peer, curve_block, posts, panel),
                "evidence_base": evidence_base, "abstain_reason": None, "finding": None}

    if ranking["winner"] is None:
        return {"outcome": "no_clear_window",
                "recommendation": {"windows": [], "publish_now": True, "confidence": "low",
                                   "confidence_penalties": penalties},
                "evidence_base": evidence_base, "abstain_reason": None,
                "finding": no_clear_window_finding(posts, ranking, metric, evidence_base)}

    return {"outcome": "recommended",
            "recommendation": build_recommendation(ranking, penalties, hour_signal, None,
                                                   zone_block, peer, curve_block, posts, panel),
            "evidence_base": evidence_base, "abstain_reason": None, "finding": None}



def confidence_penalties(posts: list, ranking: dict, zone_block: dict, metric: dict,
                         age: dict, maturity_days: int, hour: int | None = None,
                         peer: dict | None = None, curve: dict | None = None) -> list:
    """Everything that makes the ranking less trustworthy, stated rather than used to refuse.

    Each of these used to be its own abstention. Refusing on them is what left every real account
    without an answer; a creator can weigh "the winning window's posts are eight days older" and
    decide for themselves, which is more useful than silence.
    """
    # Two different kinds of caveat, and conflating them made every account come out "low".
    # `costs_confidence` marks the ones that could make the ranking itself wrong. A missing
    # timezone does not: the windows really did rank that way, the labels are just in UTC until
    # the creator confirms a zone. Same for windows never tried — they are absent from the
    # comparison, not evidence against its result.
    out = []
    # Only when the zone genuinely could not be resolved. Every run for two versions carried this
    # caveat, because the producers declared local time unrecoverable and nothing ever read the
    # zone the request already carried. With the conversion in place there is normally nothing to
    # warn about, and a caveat that fires every time teaches the creator to skip the whole block.
    if zone_block["source"] == "not_supplied":
        out.append({"code": "timezone_not_supplied", "costs_confidence": False, "params": {},
                    "statement": ("No timezone was supplied for this run, so the hours below "
                                  "are stated in UTC. The environment's own zone was not "
                                  "substituted for yours.")})
    elif zone_block["source"] == "zone_unresolved":
        out.append({"code": "timezone_unresolved", "costs_confidence": False, "params": {},
                    "statement": ("The hours below are in UTC: the timezone identifier this "
                                  "run received could not be resolved, so they were not "
                                  "converted to your local time.")})

    winner = ranking["winner"]
    if winner:
        rows = next(w for w in ranking["windows"] if w["window"] == winner)
        if rows["n"] < WINNER_N_FOR_HIGH:
            out.append({"code": "thin_winning_window", "costs_confidence": True,
                        "params": {"window": winner, "supporting_n": rows["n"],
                                   "threshold_n": WINNER_N_FOR_HIGH},
                        "statement": (f"{winner} rests on {rows['n']} posts. A few more posts "
                                      f"could change this ranking.")})
        # A window can win simply by holding the older posts when the metric keeps accruing.
        if metric.get("accumulates_with_age"):
            inside, outside = [], []
            newest = max(parse(p["published_at"]) for p in posts)
            hours = ranking["granularity_hours"]
            for post in posts:
                days = (newest - parse(post["published_at"])).days
                (inside if window_of(post, hours) == winner else outside).append(days)
            if inside and outside:
                mean_all = statistics.mean(inside + outside)
                skew = (statistics.mean(inside) - statistics.mean(outside)) / mean_all if mean_all else 0
                accrual = (age or {}).get("relative_gap") or 0.0
                if skew > 0.15 and accrual > 0.3:
                    out.append({"code": "winner_holds_older_posts", "costs_confidence": True,
                                "params": {
                                    "window": winner,
                                    "days_older": round(statistics.mean(inside)
                                                        - statistics.mean(outside)),
                                    "accrual_percent": round(accrual * 100),
                                    "metric": metric["name"]},
                                "statement": (
                                    f"Posts in {winner} are on average "
                                    f"{round(statistics.mean(inside) - statistics.mean(outside))} "
                                    f"days older than the rest, and on this account older posts "
                                    f"carry {round(accrual * 100)}% more {metric['name']} simply "
                                    f"by having been up longer. This window may be winning on "
                                    f"age rather than on publish time.")})

    # One metric agreeing with itself is not agreement. A single-metric panel can still rank the
    # windows, but nothing cross-checks the ranking, so it can never be more than a weak signal.
    if len(ranking["metric_votes"]) < 2:
        out.append({"code": "single_metric_only", "costs_confidence": True,
                    "params": {"metric": metric["name"],
                               "metrics_ranked": len(ranking["metric_votes"])},
                    "statement": (f"One metric ({metric['name']}) ranked these windows and "
                                  f"nothing cross-checks it. Confidence rises with the number "
                                  f"of metrics that can rank and agree.")})

    # Agreement is computed over the metrics that could actually rank, so a metric that ties across
    # every window silently leaves the denominator. That makes the agreement number honest and the
    # panel narrower at the same time, and the creator has to be told the second half: on Dealism's
    # Shorts the comment median is 0 in all four windows, so the whole engagement side of the panel
    # is silent and agreement rests on views and likes alone.
    abstained = ranking.get("abstaining_metrics") or {}
    if abstained and ranking["metric_votes"]:
        names = ", ".join(sorted(abstained))
        out.append({"code": "metrics_could_not_rank", "costs_confidence": True,
                    "params": {"metrics_abstaining": sorted(abstained),
                               "metrics_ranked": len(ranking["metric_votes"])},
                    "statement": (f"{names} read the same in every window, so they could not "
                                  f"separate them and did not take part. That leaves "
                                  f"{len(ranking['metric_votes'])} metric(s) ranking: this "
                                  f"account carries no usable difference on the others yet.")})

    if hour is not None and curve is not None:
        rows = (curve.get(hour) or {}).get("n", 0)
        if rows < WINNER_N_FOR_HIGH:
            out.append({"code": "thin_winning_hour", "costs_confidence": True,
                        "params": {"hour": hour, "neighbourhood_n": rows,
                                   "threshold_n": WINNER_N_FOR_HIGH},
                        "statement": (f"The hours around {hour:02d}:00 rest on {rows} posts. "
                                      f"A few more posts could change this result.")})
        # The peers are the only outside check this module has. When they point somewhere else it
        # does not overturn the creator's own history — but saying nothing would let a number that
        # one source contradicts read as though everything agreed.
        if peer and peer.get("hour") is not None and circular_distance(peer["hour"], hour) > 1:
            out.append({"code": "peers_disagree", "costs_confidence": True,
                        "params": {"peer_hour": peer["hour"], "own_hour": hour},
                        "statement": (f"Comparable accounts do best around "
                                      f"{peer['hour']:02d}:00, which is not where your own "
                                      f"history lands. The answer follows your own history, but "
                                      f"the two disagree — worth re-reading after a few more "
                                      f"posts.")})

    if ranking["never_tried"]:
        out.append({"code": "windows_never_tried", "costs_confidence": False,
                    "params": {"windows": list(ranking["never_tried"])},
                    "statement": (f"You have never published in "
                                  f"{', '.join(ranking['never_tried'])}, so those windows were "
                                  f"not compared — not because they are worse, but because "
                                  f"there is nothing to read.")})
    return out


def build_recommendation(ranking: dict, penalties: list, hour_signal: dict, hour: int | None,
                         zone_block: dict, peer: dict, curve: dict,
                         posts: list, panel: list) -> dict:
    """The window, at the finest resolution the evidence carries.

    Two shapes, one object: a single hour when signal 1 resolved one, otherwise the grid block that
    won the metric vote. Both carry the zone they are stated in, because an hour without one is not
    a time — the field names say `local` and for two versions they held UTC.
    """
    votes = len(ranking["metric_votes"])

    # "all" says the window holds on any day. It cannot say that about a day the creator has
    # never published on: this account's 19 mature posts are all weekday posts, and an isolated
    # run reading the object cold flagged a window scoped to "all" against a weekend stratum with
    # zero observations. Narrow it to what was observed.
    weekday = "all"
    note = ranking.get("weekday_note")
    if note and note.get("metrics_agreeing", 0) >= 2:
        weekday = note["stronger"]
    elif ranking.get("weekparts_observed") and len(ranking["weekparts_observed"]) == 1:
        weekday = ranking["weekparts_observed"][0]

    if hour is not None:
        agreeing = len(metrics_peaking_at(posts, panel, hour))
        votes = len(metrics_readable_at_hour(posts, panel))
        window = {"weekday": weekday,
                  "start_local": f"{hour:02d}:00", "end_local": f"{(hour + 1) % 24:02d}:00",
                  "zone": zone_block["analysis_zone"], "resolution": "hour",
                  "supporting_n": (curve.get(hour) or {}).get("own_n", 0),
                  "neighbourhood_n": (curve.get(hour) or {}).get("n", 0),
                  "metrics_agreeing": agreeing, "metrics_compared": votes}
        # A peer signal landing on the same hour is the one thing in this module that corroborates
        # the creator's own history with evidence from outside it. Nothing else can raise the
        # ceiling, which is why every run before this one came out `low`.
        corroborated = (peer.get("hour") is not None
                        and circular_distance(peer["hour"], hour) <= 1)
        if hour_signal.get("peer_broke_tie"):
            confidence = "low"
        elif corroborated:
            confidence = "high"
        else:
            confidence = "medium"
    else:
        winner = ranking["winner"]
        rows = next(w for w in ranking["windows"] if w["window"] == winner)
        start, end = winner.split("-")
        window = {"weekday": weekday, "start_local": f"{start}:00", "end_local": f"{end}:00",
                  "zone": zone_block["analysis_zone"],
                  "resolution": f"{ranking['granularity_hours']}h",
                  "supporting_n": rows["n"],
                  "metrics_agreeing": sum(1 for w in ranking["metric_votes"].values() if w == winner),
                  "metrics_compared": votes}
        if ranking["agreement"] >= STRONG_AGREEMENT and rows["n"] >= WINNER_N_FOR_HIGH:
            confidence = "high"
        elif ranking["agreement"] > 0.5:
            confidence = "medium"
        else:
            confidence = "low"

    # Only the caveats that could make the ranking wrong cost a level.
    for penalty in penalties:
        if penalty.get("costs_confidence"):
            confidence = {"high": "medium", "medium": "low", "low": "low"}[confidence]

    return {"windows": [window], "publish_now": False, "confidence": confidence,
            "confidence_penalties": penalties}


def no_clear_window_finding(posts: list, ranking: dict, metric: dict, evidence: dict) -> dict:
    """No window won. That is an answer: publish now rather than wait for an hour the data does
    not prefer. It carries no threshold to reach, because the comparison already ran."""
    votes = ranking["metric_votes"]
    spread = sorted(set(votes.values()))
    if not votes:
        headline = (f"Across your {len(posts)} posts no two windows hold "
                    f"{MIN_POSTS_PER_WINDOW} comparable posts each, so there are no windows to "
                    f"compare.")
    else:
        headline = (f"{len(votes)} metrics point at {len(spread)} different windows "
                    f"({', '.join(f'{m} to {w}' for m, w in sorted(votes.items()))}), and no "
                    f"window carries a majority.")
    # `practical_stakes.note` is an English audit string and used to be concatenated straight into
    # this sentence, so a Chinese answer switched language halfway down. The renderer avoided this
    # by rewriting the numbers rather than passing the prose through; the same rule applies here.
    # The figures stay available in `evidence_base.practical_stakes` for whoever writes the report.
    stakes = (evidence.get("practical_stakes") or {}).get(
        "detectable_absolute_effect_at_30_percent")
    return {
        "code": "no_window_preferred",
        "sensitivity": "metrics_disagree" if votes else "too_few_comparable_windows",
        "params": {
            "posts": len(posts),
            "minimum_posts_per_window": MIN_POSTS_PER_WINDOW,
            "metrics_ranked": len(votes),
            "distinct_windows": len(spread),
            "metric_votes": dict(sorted(votes.items())),
            "metric": metric["name"],
            "detectable_absolute_effect_at_30_percent": stakes,
        },
        "statement": (f"{headline} Waiting for a \"better window\" here spends a publishing "
                      f"slot and buys nothing measurable, so the right move is to publish now."
                      + (f" Even if a difference exists, at this scale a 30% timing effect is "
                         f"worth about {round(stakes)} per post." if stakes else "")),
        # The second line is only an answer when there is somewhere untried to go. On a history
        # that has already used every window it reads as "do something you have already done".
        "what_would_change_this": [
            f"Read it against a different outcome metric. This round compared the "
            f"{metric['name']} group; completion, clicks and conversion may still move with "
            f"publish time and were not measured here.",
        ] + ([
            f"Publish a few posts in {', '.join(ranking['never_tried'])}, which this history has "
            f"never used, so they enter the comparison — that is a scheduling experiment, not a "
            f"reason to post more.",
        ] if ranking.get("never_tried") else []),
    }


def circular_distance(a: int, b: int) -> int:
    """Hours apart on a 24-hour ring. 23:00 and 00:00 are one hour apart, not twenty-three."""
    gap = abs(a - b) % 24
    return min(gap, 24 - gap)


def hour_post_counts(posts: list) -> dict:
    """How many posts each hour holds of its own. Read when the curve came back empty and the
    reason has to be named."""
    counts: dict[int, int] = {}
    for post in posts:
        hour = parse(post["published_at"]).hour
        counts[hour] = counts.get(hour, 0) + 1
    return counts


def hour_curve(posts: list, metric_name: str) -> dict:
    """A reading per hour: that hour's own median, steadied by the hours either side of it.

    A fixed grid asks which box a post falls in; the box edges are arbitrary and they decide the
    answer. This asks a different question — how does the metric read at this hour — and lets the
    neighbours steady the estimate, which is what makes a single hour readable at all on a history
    that would leave most hours empty. It wraps at midnight, because 23:00 and 01:00 are two hours
    apart and no grid built out of boxes can say so.

    Each hour is summarised before the three are combined, and the middle one is weighted heaviest.
    Pooling the raw posts instead — every post one vote, which sounds like the neutral choice — lets
    whichever neighbour happens to hold the most posts decide, and it did: it put this cohort's peak
    one hour before the five posts that caused it.
    """
    by_hour: dict[int, list] = {h: [] for h in range(24)}
    for post in posts:
        by_hour[parse(post["published_at"]).hour].append(post)

    own = {}
    for hour in range(24):
        values = metric_values(by_hour[hour], metric_name)
        if values:
            own[hour] = (statistics.median(values), len(values))

    span = int(KERNEL_HALF_WIDTH_HOURS)
    curve = {}
    for hour in range(24):
        if own.get(hour, (0, 0))[1] < MIN_OWN_POSTS_AT_HOUR:
            continue
        window_n = sum(own.get((hour + offset) % 24, (0, 0))[1]
                       for offset in range(-span, span + 1))
        if window_n < MIN_POSTS_IN_HOUR_WINDOW:
            continue
        summary = []
        for offset in range(-span, span + 1):
            neighbour = own.get((hour + offset) % 24)
            if neighbour is None:
                continue
            summary.extend([neighbour[0]] * (CENTRE_WEIGHT if offset == 0 else 1))
        curve[hour] = {"median": round(statistics.median(summary), 4), "n": window_n,
                       "own_n": own[hour][1], "own_median": round(own[hour][0], 4)}
    return curve


def curve_peak(curve: dict, lower_better: bool) -> int | None:
    """The best hour, or None when two hours tie for it.

    A tie is not a peak. Picking either one hands the creator a specific hour the evidence does not
    single out, which is the failure the whole module exists to avoid.
    """
    if not curve:
        return None
    best = (min if lower_better else max)(c["median"] for c in curve.values())
    leaders = [h for h, c in curve.items() if c["median"] == best]
    return leaders[0] if len(leaders) == 1 else None


def peak_prominence(curve: dict, peak: int, lower_better: bool) -> float | None:
    """How far the peak stands above the hours that do not share its posts.

    Neighbours are excluded because the kernel gives them the peak's own posts; comparing against
    them would be comparing the peak with itself.
    """
    baseline_hours = [h for h in curve if circular_distance(h, peak) > PEAK_NEIGHBOUR_RADIUS]
    if not baseline_hours:
        return None
    baseline = statistics.median(curve[h]["median"] for h in baseline_hours)
    top = curve[peak]["median"]
    if lower_better:
        return round(baseline / top, 4) if top else None
    return round(top / baseline, 4) if baseline else None


def peak_survives_leave_one_out(posts: list, metric_name: str, peak: int,
                                lower_better: bool) -> bool:
    """Drop any single post from the peak's own window; is it still the peak?

    A peak that rests on one post is a post, not a pattern. This is the same check the bucketing
    report already runs on the grid, applied to the hour the answer is actually given at.
    """
    # Only the peak hour's own posts. The neighbours steady the reading; they are not what the
    # recommendation rests on, so removing one of theirs is not the test.
    window = [p for p in posts if parse(p["published_at"]).hour == peak]
    for dropped in window:
        remaining = [p for p in posts if p is not dropped]
        if curve_peak(hour_curve(remaining, metric_name), lower_better) != peak:
            return False
    return True


def top_hours(curve: dict, lower_better: bool, k: int) -> list:
    """The k best-reading hours, whether or not any of them stands out."""
    return sorted(curve, key=lambda h: curve[h]["median"], reverse=not lower_better)[:k]


def metrics_peaking_at(posts: list, panel: list, hour: int) -> list:
    """Which panel metrics put their own kernel peak on this hour, or the hour beside it.

    The grid's vote count answers a different question — which six-hour block the metrics prefer —
    and reading it beside an hour would report agreement about something nobody was asked. Each
    metric is given the same kernel and asked about the same hour.
    """
    agreeing = []
    for entry in panel:
        curve = hour_curve(posts, entry["name"])
        peak = curve_peak(curve, entry["direction"] == "lower_is_better")
        if peak is not None and circular_distance(peak, hour) <= 1:
            agreeing.append(entry["name"])
    return agreeing


def metrics_readable_at_hour(posts: list, panel: list) -> list:
    """Metrics whose kernel curve separates the hours at all. A metric flat across every hour —
    a comment median of zero everywhere — has no first choice and must leave the denominator, or
    agreement reads lower than it is for a reason that has nothing to do with time."""
    readable = []
    for entry in panel:
        curve = hour_curve(posts, entry["name"])
        if len(curve) >= 2 and len({c["median"] for c in curve.values()}) > 1:
            readable.append(entry["name"])
    return readable


def resolve_hour(posts: list, panel: list, peer: dict | None) -> dict:
    """Signal 1: can this creator's own history name a single hour?

    Returns the signal whether or not it can. A signal that reports "I could not read an hour, and
    here is how close I came" is worth more to the layer above than silence, because that layer has
    to decide between falling back to the grid and saying there is no window at all.
    """
    entry = panel[0]
    name, lower_better = entry["name"], entry["direction"] == "lower_is_better"
    curve = hour_curve(posts, name)
    signal = {
        "signal": "own_history_hour",
        "status": "available" if curve else "insufficient_sample",
        "metric": name,
        "hour": None,
        "hours_read": len(curve),
        "prominence": None,
        "robust": None,
        "peer_broke_tie": False,
        "code": None,
        "params": {},
        "detail": None,
    }
    if not curve:
        # Two different thresholds empty the curve and they are different facts. An hour needs
        # posts of its own before it can be named, and then its neighbourhood needs enough to
        # steady the reading. Reporting the neighbourhood rule when the own-hour rule is what
        # failed tells a creator their posts are too spread out when they are too thin per hour.
        own_counts = hour_post_counts(posts)
        best_own = max(own_counts.values(), default=0)
        if best_own < MIN_OWN_POSTS_AT_HOUR:
            signal["code"] = "no_hour_has_enough_own_posts"
            signal["params"] = {"minimum_own_posts": MIN_OWN_POSTS_AT_HOUR,
                                "most_posts_in_any_hour": best_own}
            signal["detail"] = (f"No single hour holds {MIN_OWN_POSTS_AT_HOUR} posts of its own "
                                f"— the fullest holds {best_own} — so no hour can be named.")
        else:
            signal["code"] = "hour_curve_too_thin"
            signal["params"] = {"kernel_half_width_hours": KERNEL_HALF_WIDTH_HOURS,
                                "minimum_posts_in_window": MIN_POSTS_IN_HOUR_WINDOW}
            signal["detail"] = (f"No hour has {MIN_POSTS_IN_HOUR_WINDOW} posts within "
                                f"{KERNEL_HALF_WIDTH_HOURS} hours of it, so the hours cannot be "
                                f"told apart.")
        return signal

    peak = curve_peak(curve, lower_better)
    if peak is not None:
        signal["prominence"] = peak_prominence(curve, peak, lower_better)
        prominent = (signal["prominence"] or 0) >= MIN_PEAK_PROMINENCE
        signal["robust"] = peak_survives_leave_one_out(posts, name, peak, lower_better) if prominent else None
        if prominent and signal["robust"]:
            signal["hour"] = peak
            signal["code"] = "hour_peak_robust"
            signal["params"] = {"hour": peak, "metric": name,
                                "prominence": signal["prominence"],
                                "own_n": curve[peak]["own_n"], "neighbourhood_n": curve[peak]["n"]}
            signal["detail"] = (f"At {peak:02d}:00 the median {name} is "
                                f"{signal['prominence']}x the rest of the day. The hour itself "
                                f"holds {curve[peak]['own_n']} posts and "
                                f"{curve[peak]['n']} with its neighbours; remove any single "
                                f"post from it and it still comes first.")
            return signal

    # Own history reads the hours but cannot single one out. The peers may break the tie — but only
    # inside the hours this creator's own history already puts at the top. Anything else would be
    # answering from someone else's account, which is the fallback this module refuses.
    plausible = top_hours(curve, lower_better, PEER_TIE_BREAK_TOP_K)
    peer_hour = (peer or {}).get("hour")
    if peer_hour is not None and peer_hour in plausible:
        signal["hour"] = peer_hour
        signal["peer_broke_tie"] = True
        signal["status"] = "peer_assisted"
        signal["code"] = "hour_peer_broke_tie"
        signal["params"] = {"hour": peer_hour, "plausible_hours": sorted(plausible)}
        signal["detail"] = (f"Your own history puts "
                            f"{', '.join(f'{h:02d}:00' for h in sorted(plausible))} at the top "
                            f"without separating them; comparable accounts do best at "
                            f"{peer_hour:02d}:00, so start there.")
        return signal

    signal["status"] = "no_single_hour"
    if peak is None:
        signal["code"] = "hours_tied"
        signal["params"] = {"hours_read": len(curve)}
        signal["detail"] = (f"The hours can be read, but several tie at the top and cannot be "
                            f"separated ({len(curve)} hours read).")
    elif signal["prominence"] is None:
        # `peak_prominence` returns None when there is nothing to stand above: every other hour
        # is inside the peak's own kernel, or the baseline it would divide by is zero. That is
        # not the same fact as "measured, and too low", and treating it as zero put the Python
        # literal `None` into a sentence the creator reads ("only Nonex the rest of the day").
        signal["code"] = "hour_peak_prominence_unmeasurable"
        signal["params"] = {"hour": peak, "hours_read": len(curve),
                            "minimum_prominence": MIN_PEAK_PROMINENCE}
        signal["detail"] = (f"{peak:02d}:00 reads highest, but there is no hour far enough from "
                            f"it to compare against — every other hour this history can read "
                            f"shares its posts, so how far it stands above the day cannot be "
                            f"measured.")
    elif not signal["prominence"] >= MIN_PEAK_PROMINENCE:
        signal["code"] = "hour_peak_not_prominent"
        signal["params"] = {"hour": peak, "prominence": signal["prominence"],
                            "minimum_prominence": MIN_PEAK_PROMINENCE}
        signal["detail"] = (f"{peak:02d}:00 reads best, but only {signal['prominence']}x the "
                            f"rest of the day — not enough to name a single hour.")
    else:
        signal["code"] = "hour_peak_not_robust"
        signal["params"] = {"hour": peak, "prominence": signal["prominence"]}
        signal["detail"] = (f"{peak:02d}:00 reads best ({signal['prominence']}x), but "
                            f"removing one post from the window changes which hour leads, so it "
                            f"cannot carry a specific hour.")
    return signal


def window_label(hour: int, hours: int) -> str:
    start = hour // hours * hours
    return f"{start:02d}-{(start + hours) % 24:02d}"


def window_of(post: dict, hours: int) -> str:
    return window_label(parse(post["published_at"]).hour, hours)


def majority_winner(votes: dict) -> str | None:
    """The window more than half the metrics put first. A plurality is not enough: five metrics
    naming four different windows have not agreed on anything, and the top one of those would be a
    coin toss dressed as a recommendation."""
    if not votes:
        return None
    tally: dict = {}
    for window in votes.values():
        tally[window] = tally.get(window, 0) + 1
    best = max(tally, key=tally.get)
    return best if tally[best] * 2 > len(votes) else None


def choose_granularity(posts: list, panel: list) -> tuple:
    """Three hours if the history supports a stable answer there, coarser only when it does not.

    Two things can force a step down the ladder. Too few windows hold enough posts to rank — or
    the metrics rank them but do not agree, which is what happens when the slices are so fine that
    each holds a different handful of posts. Both are the same problem seen from different sides:
    the slicing is finer than the evidence.

    Coarsening is never driven by empty windows. An empty window means the creator has never
    published there; that is information to report, not a defect to smooth away. respond.io has
    only ever used four of the eight three-hour slots, and merging slots would hide that.
    """
    def occupancy(hours: int) -> dict:
        counts: dict = {}
        for post in posts:
            counts[window_of(post, hours)] = counts.get(window_of(post, hours), 0) + 1
        return counts

    for hours in GRANULARITIES:
        counts = occupancy(hours)
        rankable = [w for w, n in counts.items() if n >= MIN_POSTS_PER_WINDOW]
        if len(rankable) < 2:
            continue
        votes = metric_votes(posts, panel, hours, rankable)
        if majority_winner(votes) is not None:
            # "a majority of metrics" reads as several agreeing. On a single-metric panel it
            # is one metric agreeing with itself, and the same object says two lines later that
            # nothing corroborates the ranking. Say the count instead of the word.
            agreed = (f"{len(votes)} metrics that could rank point at the same one"
                      if len(votes) > 1 else
                      f"the only metric that could rank ({next(iter(votes))}) picked one")
            granularity = {"code": "majority_at_this_granularity",
                           "params": {"granularity_hours": hours, "windows_rankable": len(rankable),
                                      "windows_total": 24 // hours,
                                      "minimum_posts_per_window": MIN_POSTS_PER_WINDOW,
                                      "metrics_ranked": len(votes),
                                      "sole_metric": next(iter(votes)) if len(votes) == 1 else None}}
            return hours, (f"{hours}-hour blocks: {len(rankable)}/{24 // hours} windows hold at "
                           f"least {MIN_POSTS_PER_WINDOW} mature posts each, and "
                           f"{agreed}."), counts, granularity
        # Remember the finest slicing that could at least be ranked, in case nothing agrees at any
        # level: reporting the disagreement at 3 hours is more honest than reporting it at 12.
        finest = (hours, (f"{hours}-hour blocks: {len(rankable)}/{24 // hours} windows carry "
                          f"enough posts, but no majority of metrics agrees; widening the "
                          f"blocks produced no agreement either, so the finest slicing is "
                          f"reported as it stands."), counts,
                  {"code": "no_majority_at_any_granularity",
                   "params": {"granularity_hours": hours, "windows_rankable": len(rankable),
                              "windows_total": 24 // hours,
                              "minimum_posts_per_window": MIN_POSTS_PER_WINDOW,
                              "metrics_ranked": len(votes)}})
        if hours == GRANULARITIES[0]:
            fallback = finest
    try:
        return fallback
    except NameError:
        hours = GRANULARITIES[-1]
        counts = occupancy(hours)
        # Fewer than two, which is one or none — not always none.
        rankable = [w for w, n in counts.items() if n >= MIN_POSTS_PER_WINDOW]
        return hours, (f"Even in {hours}-hour blocks, fewer than two windows hold "
                       f"{MIN_POSTS_PER_WINDOW} posts each, so no two windows can be compared "
                       f"at all."), counts, {
            "code": "too_few_comparable_windows",
            "params": {"granularity_hours": hours, "windows_rankable": len(rankable),
                       "windows_total": 24 // hours,
                       "minimum_posts_per_window": MIN_POSTS_PER_WINDOW,
                       "metrics_ranked": 0}}


def metric_votes(posts: list, panel: list, hours: int, rankable: list) -> dict:
    """Which window each metric puts first, at one granularity.

    Shares the abstention rule with `rank_windows`: a metric tied at the top names no first choice.
    The two used to count independently and disagreed out loud — on the Dealism cohort the
    granularity reason said "5 metrics point at the same one" while the recommendation beside it
    said 3, because only this copy still let a four-way tie at zero cast a vote.
    """
    grouped: dict = {}
    for post in posts:
        grouped.setdefault(window_of(post, hours), []).append(post)
    votes = {}
    for entry in panel:
        name, lower_better = entry["name"], entry["direction"] == "lower_is_better"
        medians = {}
        for window in rankable:
            values = metric_values(grouped[window], name)
            if len(values) >= MIN_POSTS_PER_WINDOW:
                medians[window] = statistics.median(values)
        if len(medians) < 2:
            continue
        best = min(medians.values()) if lower_better else max(medians.values())
        leaders = [w for w, v in medians.items() if v == best]
        if len(leaders) > 1:
            continue
        votes[name] = leaders[0]
    return votes


def rank_windows(posts: list, panel: list) -> dict:
    """Rank every usable window by every metric, then count which window each metric puts first."""
    hours, reason, counts, granularity = choose_granularity(posts, panel)
    grouped: dict = {}
    for post in posts:
        grouped.setdefault(window_of(post, hours), []).append(post)

    universe = [window_label(h, hours) for h in range(0, 24, hours)]
    rankable = sorted(w for w in grouped if len(grouped[w]) >= MIN_POSTS_PER_WINDOW)
    never_tried = [w for w in universe if w not in grouped]

    # Only metrics that at least two rankable windows both carry can vote: a metric present in one
    # window and absent in the other has no comparison to offer.
    votes, per_metric_rank, abstained = {}, {}, {}
    for entry in panel:
        name, lower_better = entry["name"], entry["direction"] == "lower_is_better"
        medians = {}
        for window in rankable:
            values = metric_values(grouped[window], name)
            if len(values) >= MIN_POSTS_PER_WINDOW:
                medians[window] = statistics.median(values)
        if len(medians) < 2:
            abstained[name] = "carried by fewer than two rankable windows"
            continue
        order = sorted(medians, key=lambda w: medians[w], reverse=not lower_better)
        # Competition ranking, so a tie reads as a tie. Enumerating the sorted order instead gave
        # tied windows 1, 2, 3, 4 in alphabetical order — on the Dealism cohort every window had a
        # comment median of 0 and the metric still "chose" 00-06, because that label sorts first.
        per_metric_rank[name] = {}
        for index, window in enumerate(order):
            if index and medians[window] == medians[order[index - 1]]:
                per_metric_rank[name][window] = per_metric_rank[name][order[index - 1]]
            else:
                per_metric_rank[name][window] = index + 1
        leaders = [w for w in order if per_metric_rank[name][w] == 1]
        if len(leaders) > 1:
            # A metric that cannot separate its own top windows has no first choice to cast.
            abstained[name] = (f"tied across {len(leaders)} windows at {medians[leaders[0]]}, "
                               "so it names no first choice")
            continue
        votes[name] = order[0]

    winner = majority_winner(votes)
    agreement = (round(sum(1 for w in votes.values() if w == winner) / len(votes), 4)
                 if winner and votes else 0.0)

    windows = []
    for window in universe:
        rows = grouped.get(window, [])
        medians = {}
        for entry in panel:
            values = [p["metrics"].get(entry["name"]) for p in rows]
            values = [v for v in values if v is not None]
            medians[entry["name"]] = round(statistics.median(values), 4) if values else None
        windows.append({
            "window": window,
            "n": len(rows),
            "rankable": window in rankable,
            "medians": medians,
            "rank_by_metric": {m: per_metric_rank[m].get(window) for m in per_metric_rank},
        })

    return {
        "granularity_hours": hours,
        "granularity_reason": reason,
        "granularity": granularity,
        "minimum_posts_per_window": MIN_POSTS_PER_WINDOW,
        "windows": windows,
        "never_tried": never_tried,
        # Which halves of the week the cohort actually contains. A window scoped to "all" days
        # cannot mean "any day" when one of them holds no posts at all.
        "weekparts_observed": sorted({("weekend" if parse(p["published_at"]).weekday() >= 5
                                       else "weekday") for p in posts}),
        "metric_votes": votes,
        "abstaining_metrics": abstained,
        "winner": winner,
        "agreement": agreement,
        "dissenting_metrics": sorted(m for m, w in votes.items() if w != winner),
    }


# The machine value for a day of the week, indexed the way `datetime.weekday()` is (Monday
# first). Lower-case English because these travel in structured fields — `weekday_note.stronger`,
# `per_metric`, `days_compared` and `recommendation.windows[].weekday` — where a downstream
# reader matches on them. Chinese day names used to sit here and reached `plan-content-calendar`
# as machine values; it still accepts them, so already-published recommendations keep resolving,
# but nothing emits them any more. The creator-facing name is the renderer's to produce.
WEEKDAY_NAMES = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def weekday_note(posts: list, panel: list) -> dict | None:
    """Which day, if any. Seven first, then the weekday/weekend split, then nothing.

    Supplementary either way. It never narrows the window into a day-and-hour cell: seven days
    times twenty-four hours is 168 cells and no creator's history fills them, so the day and the
    hour are read separately and reported separately.

    Naming a single weekday is worth more to a creator than "weekdays are better" — it is the
    difference between a rule and a slot in their calendar. It is also a seven-way split of the
    same posts, so it is held to the same floor as an hour: a day answers only with enough of its
    own posts behind it, and only when the metrics agree on it.
    """
    seven = weekday_detail(posts, panel)
    if seven:
        return seven
    return weekpart_detail(posts, panel)


def weekday_detail(posts: list, panel: list) -> dict | None:
    """One named day of the week, when the history can carry the seven-way split."""
    groups: dict = {index: [] for index in range(7)}
    for post in posts:
        groups[parse(post["published_at"]).weekday()].append(post)
    eligible = [index for index, rows in groups.items() if len(rows) >= MIN_OWN_POSTS_AT_HOUR]
    # Two days to compare, and most of the week actually used. On a history that only ever ran
    # Tuesdays and Thursdays, "Tuesday is stronger" is a statement about two days, not about a week.
    if len(eligible) < 4:
        return None

    agreeing, stronger = 0, None
    per_metric = {}
    for entry in panel:
        name, lower_better = entry["name"], entry["direction"] == "lower_is_better"
        medians = {}
        for index in eligible:
            values = metric_values(groups[index], name)
            if len(values) >= MIN_OWN_POSTS_AT_HOUR:
                medians[index] = statistics.median(values)
        if len(medians) < 2 or len(set(medians.values())) == 1:
            continue
        best = min(medians, key=medians.get) if lower_better else max(medians, key=medians.get)
        leaders = [d for d, value in medians.items() if value == medians[best]]
        if len(leaders) > 1:
            continue
        per_metric[name] = best
        if stronger is None:
            stronger = best
        agreeing += 1 if best == stronger else 0
    if stronger is None or agreeing < 2:
        return None
    return {"stronger": WEEKDAY_NAMES[stronger], "resolution": "weekday",
            "code": "one_day_is_stronger",
            "weekday_n": len(groups[stronger]), "weekend_n": None,
            "metrics_agreeing": agreeing, "per_metric": {m: WEEKDAY_NAMES[d]
                                                         for m, d in per_metric.items()},
            "days_compared": sorted(WEEKDAY_NAMES[d] for d in eligible),
            "params": {"stronger": WEEKDAY_NAMES[stronger],
                       "metrics_agreeing": agreeing,
                       "posts_on_stronger": len(groups[stronger]),
                       "days_compared": [WEEKDAY_NAMES[d] for d in sorted(eligible)]},
            "statement": (f"{WEEKDAY_NAMES[stronger].capitalize()} posts do better on "
                          f"{agreeing} metric(s) (that day holds {len(groups[stronger])} posts; "
                          f"the days compared were "
                          f"{', '.join(WEEKDAY_NAMES[d] for d in sorted(eligible))}).")}


def weekpart_detail(posts: list, panel: list) -> dict | None:
    """The two-way split, kept as the fallback for histories too thin for seven days."""
    groups: dict = {"weekday": [], "weekend": []}
    for post in posts:
        key = "weekend" if parse(post["published_at"]).weekday() >= 5 else "weekday"
        groups[key].append(post)
    if min(len(groups["weekday"]), len(groups["weekend"])) < MIN_POSTS_PER_WINDOW:
        return None
    agreeing, stronger = 0, None
    for entry in panel:
        name, lower_better = entry["name"], entry["direction"] == "lower_is_better"
        medians = {}
        for key, rows in groups.items():
            values = [p["metrics"].get(name) for p in rows if p["metrics"].get(name) is not None]
            if len(values) >= MIN_POSTS_PER_WINDOW:
                medians[key] = statistics.median(values)
        if len(medians) < 2:
            continue
        best = min(medians, key=medians.get) if lower_better else max(medians, key=medians.get)
        if stronger is None:
            stronger = best
        agreeing += 1 if best == stronger else 0
    if stronger is None or agreeing < 2:
        return None
    label = "Weekend" if stronger == "weekend" else "Weekday"
    return {"stronger": stronger, "resolution": "weekpart",
            "code": "one_half_of_the_week_is_stronger",
            "weekday_n": len(groups["weekday"]),
            "weekend_n": len(groups["weekend"]), "metrics_agreeing": agreeing,
            "params": {"stronger": stronger, "metrics_agreeing": agreeing,
                       "weekday_n": len(groups["weekday"]),
                       "weekend_n": len(groups["weekend"])},
            "statement": (f"{label} posts do better on {agreeing} metric(s) "
                          f"({len(groups['weekday'])} on weekdays, "
                          f"{len(groups['weekend'])} at weekends).")}


def grouped_values(posts: list, scheme: str, primary: str) -> dict:
    """Bucket the metric by one scheme, keeping only strata that can enter a comparison at all."""
    by_bucket: dict[str, list[float]] = {}
    for post, key in zip(posts, bucket_keys(posts, scheme)):
        value = post["metrics"].get(primary)
        if value is not None:
            by_bucket.setdefault(str(key), []).append(value)
    return {k: v for k, v in by_bucket.items() if len(v) >= MIN_GROUP_N}


def scheme_significance(posts: list, scheme: str, primary: str) -> dict:
    """Test one bucketing scheme for a real difference, and say what it could have detected.

    Two separate questions live here and conflating them is what produced the old nonsense answer.
    First: can these group sizes reach significance at all? Second: given that they can, do they?
    A scheme that fails the first question is untested, not negative.
    """
    groups = grouped_values(posts, scheme, primary)
    report = {"scheme": scheme,
              "group_n": {k: len(v) for k, v in sorted(groups.items())},
              "group_median": {k: round(statistics.median(v), 1) for k, v in sorted(groups.items())}}
    if len(groups) < 2:
        report.update({"testable": False,
                       "not_testable_because": (
                           f"Fewer than two strata hold at least {MIN_GROUP_N} mature posts, so "
                           f"there is nothing to compare."),
                       "kruskal_wallis": None, "strongest_pair": None})
        return report

    ordered = sorted(groups, key=lambda k: statistics.median(groups[k]))
    low, high = ordered[0], ordered[-1]
    reachable = smallest_reachable_p(len(groups[high]), len(groups[low]))
    report["smallest_reachable_p_on_strongest_pair"] = round(reachable, 6)
    if reachable > ALPHA:
        report.update({"testable": False,
                       "not_testable_because": (
                           f"With {len(groups[high])} and {len(groups[low])} posts the smallest "
                           f"two-sided p a rank test can reach is {reachable:.3f}, above alpha "
                           f"{ALPHA}. No arrangement of these posts could be called significant."),
                       "kruskal_wallis": None, "strongest_pair": None})
        return report

    report["testable"] = True
    report["not_testable_because"] = None
    report["kruskal_wallis"] = kruskal_wallis(groups)
    report["strongest_pair"] = {
        # This pair was chosen after seeing the medians, so its p-value is optimistic and is not
        # what any outcome is decided on — the Holm-corrected omnibus above is. It is reported
        # because a creator needs to see which two windows the claim is about.
        "selection": "post_hoc_widest_median_gap",
        "p_is_decision_statistic": False,
        "higher": high, "lower": low,
        "median_higher": round(statistics.median(groups[high]), 1),
        "median_lower": round(statistics.median(groups[low]), 1),
        "n_higher": len(groups[high]), "n_lower": len(groups[low]),
        "mann_whitney": mann_whitney(groups[high], groups[low]),
    }
    return report


def partition_signature(posts: list, scheme: str) -> frozenset:
    """Which posts land together, ignoring what the buckets are called.

    Two schemes can be the same test under different names: with no weekend posts at all,
    `daypart_x_weekpart` relabels `daypart_6h` and nothing else. Correcting for both as if they
    were separate looks at the same evidence twice and pays the multiplicity penalty twice — on
    the Instagram cohort it pushed a p of 0.580 to 1.000.
    """
    groups: dict = {}
    for index, key in enumerate(bucket_keys(posts, scheme)):
        groups.setdefault(str(key), []).append(index)
    return frozenset(frozenset(v) for v in groups.values() if len(v) >= MIN_GROUP_N)


def significance_report(posts: list, bucketing: list, primary: str) -> dict:
    """Every scheme tested, corrected for the number of *distinct* tests that were run."""
    posts = metric_posts(posts, primary)
    schemes = [scheme_significance(posts, b["scheme"], primary) for b in bucketing]

    # Deduplicate before correcting: the first scheme producing a given partition is the one
    # tested, and any later scheme with the same partition inherits its result.
    seen: dict = {}
    for entry in schemes:
        if not entry["testable"]:
            continue
        signature = partition_signature(posts, entry["scheme"])
        if signature in seen:
            entry["duplicate_of"] = seen[signature]
        else:
            seen[signature] = entry["scheme"]
            entry["duplicate_of"] = None

    tested = {s["scheme"]: s["kruskal_wallis"]["p_value"]
              for s in schemes if s["testable"] and not s.get("duplicate_of")}
    adjusted = holm(tested) if tested else {}
    for entry in schemes:
        twin = entry.get("duplicate_of")
        if twin:
            adjusted[entry["scheme"]] = adjusted[twin]
    for entry in schemes:
        entry["p_holm"] = adjusted.get(entry["scheme"])
    significant = [s for s in schemes if s["p_holm"] is not None and s["p_holm"] <= ALPHA]
    decisive = min(significant, key=lambda s: s["p_holm"])["scheme"] if significant else None
    return {
        "method": SIGNIFICANCE_METHOD,
        "alpha": ALPHA,
        "minimum_group_n": MIN_GROUP_N,
        "schemes": schemes,
        "schemes_testable": sorted(tested),
        "distinct_tests_corrected_for": len(tested),
        "any_scheme_testable": bool(tested),
        "any_significant": bool(significant),
        "decisive_scheme": decisive,
    }


def no_publish_time_result(posts, cohort, metric_block, zone_block, history, suffix, primary) -> dict:
    """The rows exist and carry the metric; the publish time does not exist. Reporting a bucketing
    here would put every post in one bucket and call it a finding."""
    stamps = history["timestamps"]
    values = metric_values(posts, primary)
    mean = statistics.mean(values)
    stdev = statistics.stdev(values) if len(values) > 1 else 0.0
    return {
        "outcome": "abstain_no_publish_time",
        "recommendation": None,
        "evidence_base": {
            "cohort": cohort, "timezone": zone_block,
            # Deliberately not bucketed. With every timestamp at midnight the schemes would all
            # report one occupied bucket, which reads like a finding and is an artefact.
            "bucketing": [{"scheme": f"not_evaluated{suffix}", "buckets_total": 0,
                           "buckets_nonempty": 0, "max_bucket_n": 0, "min_bucket_n": 0,
                           "zero_observation_strata": [], "distribution": {}}],
            "metric_panel": history["metric_panel"],
            "dispersion": {"metric": metric_block["name"], "mean": round(mean, 3),
                           "stdev": round(stdev, 3),
                           "coefficient_of_variation": round(stdev / mean, 4) if mean else 0.0},
            "power": {"method": POWER_METHOD, "alpha": ALPHA, "target_power": TARGET_POWER,
                      "targets": [], "actual_max_group_n": 0, "powered": False},
            "practical_stakes": practical_stakes(posts, mean, metric_block["name"]),
            "age_accumulation": age_accumulation_check(posts, primary),
        },
        "abstain_reason": {
            "code": "publish_time_not_observed",
            "params": {"posts": len(posts),
                       "date_precision": stamps.get("date_precision")},
            "statement": (
                f"The {len(posts)} posts in this cohort carry the metric, but not a publish time. "
                f"Every timestamp in this source reads midnight, and the date itself is "
                f"{'an estimate' if stamps.get('date_precision') == 'estimated_day' else 'day-resolution only'}. "
                f"There is no hour to compare, so this is not a thin sample — the independent "
                f"variable was never observed. Collecting more posts from the same source would "
                f"add rows and no information."),
            "missing": [
                "A real publish timestamp with an hour, from a source that reports it rather than "
                "infers it.",
                stamps.get("precision_note") or "A provider that does not estimate the date.",
            ],
            "unlock_threshold": {
                "current_total_mature_posts": len(posts),
                "also_requires": [
                    "A different data source, not more data. Owner analytics reports the real "
                    "publish instant; the public paths available here do not.",
                ],
            },
        },
    }


def no_history_result(cohort, metric_block, zone_block, history, suffix, primary) -> dict:
    empty = build_bucketing([], suffix, primary)
    return {
        "outcome": "abstain_no_history",
        "recommendation": None,
        "evidence_base": {
            # `metric_panel`, like every other branch. This one carried a lone `metric` block as
            # well, which no consumer reads and the schema does not allow — invisible for as long
            # as the branch itself could not run.
            "cohort": cohort, "metric_panel": history["metric_panel"], "timezone": zone_block,
            "bucketing": empty,
            "dispersion": {"metric": metric_block["name"], "mean": 0.0, "stdev": 0.0,
                           "coefficient_of_variation": 0.0},
            "power": {"method": POWER_METHOD, "alpha": ALPHA, "target_power": TARGET_POWER,
                      "targets": [], "actual_max_group_n": 0, "powered": False},
        },
        "abstain_reason": {
            "code": "no_usable_history",
            "params": {"posts": 0},
            "statement": ("No mature post of this format carries the metric, so there is nothing to "
                          "compare. This is not a small sample; it is an empty one."),
            "missing": ["At least one mature post of the requested format carrying the metric."],
            "unlock_threshold": {"current_total_mature_posts": 0,
                                 "also_requires": [history["coverage"]["coverage_note"]]},
        },
    }


def derive_limitations(request: dict, history: dict, engine: dict) -> list[str]:
    """Facts about what this history can and cannot carry. Derived, not narrated: every clause
    below is read straight off the coverage block or the cohort."""
    coverage = history["coverage"]
    cohort = engine["evidence_base"]["cohort"]
    lines = []
    if coverage.get("posts_with_insights") is not None:
        lines.append(
            f"Insight coverage is {coverage['posts_with_insights']} of {coverage['catalog_posts']} "
            f"catalogue posts; {coverage['catalog_posts'] - coverage['posts_with_insights']} carry "
            f"no per-post insights, so the usable history is both small and non-random.")
    if cohort["window"]:
        first, last = (parse(t.strip()) for t in cohort["window"].split(".."))
        span = (last - first).days
        lines.append(
            f"The mature window spans {span} days" +
            (", which is too short to separate a time-of-day effect from a trend in the account's "
             "overall reach." if span < 90 else
             ", long enough that the account's overall reach may have moved within it; a "
             "time-of-day effect and a trend are not separated here."))
    other = coverage["cohort_total"] - coverage["cohort_with_metric"]
    lines.append(
        f"Only {cohort['format']} is analysed" +
        (f"; {other} post(s) of this format carry no metric and were excluded." if other else "."))
    panel = history["metric_panel"]
    primary = panel[0]["name"]
    missing_primary = sum(1 for post in history["posts"]
                          if post["is_mature"] and post["product_type"] == cohort["format"]
                          and post["metrics"].get(primary) is None)
    if missing_primary:
        lines.append(f"{missing_primary} mature post(s) carry no {primary} value and were "
                     "excluded from the timing cohort; missing values were not counted as zero.")
    if any(e["accumulates_with_age"] for e in panel):
        accruing = [e["name"] for e in panel if e["accumulates_with_age"]]
        lines.append(
            f"{', '.join(accruing)} accumulate over a post's life, so an older post carries more of "
            f"them regardless of the hour it went out. Restricting to mature posts reduces this "
            f"maturity confound; it does not remove it.")
    ranked = list((engine.get("evidence_base", {}).get("ranking", {}).get("metric_votes") or {}))
    silent = [e["name"] for e in panel if e["name"] not in ranked]
    lines.append(
        f"The panel holds {len(panel)} metric(s) — {', '.join(e['name'] for e in panel)} — of which "
        f"{len(ranked)} could rank the windows"
        + (f"; {', '.join(silent)} produced the same value in every window and named no first "
           f"choice, so {'it' if len(silent) == 1 else 'they'} took no part in the comparison. "
           if silent else ". ")
        + "Metrics outside this panel were not evaluated.")
    if any(e["scope"] == "public" for e in panel):
        lines.append(
            "These are public counters, not owner metrics. They count a different population than "
            "the account's own analytics and must never be presented as owner performance.")
    note = (history.get("timestamps") or {}).get("precision_note")
    if note:
        lines.append(note)
    return lines


def assemble(request: dict, history: dict, run_id: str, generated_at: str) -> dict:
    """The full PublishRecommendation. Every number in it comes from compute(); the two policy
    strings are constants; the limitations are derived from coverage."""
    engine = compute(request, history)
    return {
        "schema_version": "publish-recommendation.v2",
        "recommendation_id": f"prec-{request['creator']['account_ref']}-{run_id}",
        "request_ref": request["request_id"],
        **engine,
        "causal_boundary": CAUSAL_BOUNDARY,
        "refused_fallbacks": list(REFUSED_FALLBACKS),
        "limitations": derive_limitations(request, history, engine),
        "reproducibility": {
            "run_id": run_id,
            "generated_at": generated_at,
            "formula_set_version": RULE_VERSION,
            "input_sha256": history["source"]["snapshot_sha256"],
            "command": ("python3 skills/recommend-publish-timing/scripts/compute_timing.py "
                        "<request> <history> --full"),
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("request")
    ap.add_argument("history")
    ap.add_argument("--full", action="store_true",
                    help="emit a complete PublishRecommendation rather than the engine block alone")
    ap.add_argument("--run-id", default="unset")
    ap.add_argument("--generated-at", default="1970-01-01T00:00:00+00:00",
                    help="passed in rather than read from the clock, so reruns stay byte-identical")
    ap.add_argument("--output")
    args = ap.parse_args()

    request = json.loads(Path(args.request).read_text(encoding="utf-8"))
    # Relative refs inside the request are relative to the request, not to whoever invoked us.
    request["__request_path__"] = str(Path(args.request).resolve())
    history = json.loads(Path(args.history).read_text(encoding="utf-8"))
    result = (assemble(request, history, args.run_id, args.generated_at) if args.full
              else compute(request, history))
    text = json.dumps(result, ensure_ascii=False, indent=1)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
