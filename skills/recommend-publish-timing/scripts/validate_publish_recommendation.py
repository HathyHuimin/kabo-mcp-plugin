#!/usr/bin/env python3
"""Validate a PublishRecommendation against its history, request and any schedule taken from it.

JSON Schema already says whether the fields are the right shape. This checks the things a schema
cannot: that a refusal was not quietly softened into advice, that the reported buckets add up to
the cohort they claim to describe, that a scheme which failed was not hidden, and that nobody
stated a local publishing hour from timestamps that never carried one.

    python3 validate_publish_recommendation.py <publish_recommendation.json>
                                               [<publish_history.json>] [<publish_request.json>]
                                               [--schedule <publish_schedule.json>]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

# Only two genuine refusals remain: no rows at all, and a source that reports no publish hour.
# Everything the engine used to refuse on — thin samples, an unconfirmed zone, age skew — is now
# a stated caveat, because refusing on those left five real accounts with no answer at all.
ABSTAIN = {"abstain_no_history", "abstain_no_publish_time"}

# Not a refusal: the windows were ranked and no window won a majority of the metrics. The product
# shows it as an answer — publish now — so its rules are the opposite of a refusal's.
NO_CLEAR_WINDOW = "no_clear_window"

# "more data" is exactly the answer this module exists to refuse. An unlock threshold has to be
# something a creator can count towards.
VAGUE = re.compile(r"\b(more data|more posts|a while|eventually|soon)\b", re.I)

# A powered comparison licenses "posts in this window performed better", never a promise.
CAUSAL = re.compile(r"\b(will (?:perform|get|do|reach)|guarantees?|ensures?|because you post(?:ed)? at)\b", re.I)

# The causal boundary states the forbidden sentence in order to forbid it. Scanning must therefore
# ignore quoted spans, or the one paragraph doing this job correctly is the one that fails.
QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")

# A zone is unplaceable only when the runtime could not resolve the identifier it was handed.
# `assumed` used to live here, which meant every run carried the caveat and no run ever stated a
# local hour; the host is the authority on the creator's zone and an absolute instant converts into
# it exactly, so a supplied-and-resolvable zone is placeable whatever its provenance says.
UNPLACEABLE_ZONES = {"not_supplied", "zone_unresolved"}

ARTIFACT_ID_FIELDS = {
    "selected-idea.v1": "selected_idea_id",
    "selected-idea.v2": "selected_idea_id",
    "script-package.v1": "package_id",
    "creator-profile.v1": "profile_id",
}


def require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def validate_request_artifacts(request: dict, request_path: Path) -> list[str]:
    errors: list[str] = []
    refs = [
        ("content.source_idea_ref", (request.get("content") or {}).get("source_idea_ref")),
        ("content.source_script_ref", (request.get("content") or {}).get("source_script_ref")),
        ("creator.profile_ref", (request.get("creator") or {}).get("profile_ref")),
    ]
    for label, ref in refs:
        if ref is None:
            continue
        require(isinstance(ref, dict), f"{label} must be an artifact reference", errors)
        if not isinstance(ref, dict):
            continue
        value = ref.get("path", "")
        artifact_path = Path(value)
        if not artifact_path.is_absolute():
            artifact_path = (request_path.parent / artifact_path).resolve()
        require(artifact_path.is_file(), f"{label} path not found: {value}", errors)
        if not artifact_path.is_file():
            continue
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        schema_version = artifact.get("schema_version")
        require(ref.get("schema_version") == schema_version, f"{label} schema_version mismatch", errors)
        id_field = ARTIFACT_ID_FIELDS.get(schema_version)
        require(id_field is not None, f"{label} uses unsupported schema_version {schema_version}", errors)
        if id_field:
            require(ref.get("artifact_id") == artifact.get(id_field), f"{label} artifact_id mismatch", errors)
        declared = ref.get("sha256")
        if declared is not None:
            actual = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
            require(declared == actual, f"{label} sha256 mismatch", errors)
    return errors


def validate(rec: dict, history: dict | None, request: dict | None,
             schedule: dict | None, request_path: Path | None = None) -> list[str]:
    errors: list[str] = []
    outcome = rec.get("outcome")
    base = rec.get("evidence_base") or {}
    cohort = base.get("cohort") or {}
    bucketing = base.get("bucketing") or []
    power = base.get("power") or {}
    significance = base.get("significance") or {}
    reason = rec.get("abstain_reason")

    ranking = base.get("ranking") or {}
    recommendation = rec.get("recommendation") or {}
    windows = recommendation.get("windows") or []

    # --- a refusal must stay a refusal ---
    if outcome in ABSTAIN:
        require(rec.get("recommendation") is None,
                "an abstaining outcome still carries a recommendation", errors)
        require(isinstance(reason, dict) and reason,
                "an abstaining outcome must say what is missing", errors)
        # Scanned on `missing` only. The statement is allowed to say "more posts would not help",
        # which is the opposite of the vague threshold this rule exists to catch.
        for text in list((reason or {}).get("missing") or []):
            require(not VAGUE.search(text or ""),
                    f"what is missing is stated as a vague quantity: {text[:60]!r}", errors)

    elif outcome == NO_CLEAR_WINDOW:
        # An answer, not a refusal: it must not smuggle a window in, must not arrive under the
        # field a refusal uses, and must not hand out a posting quota for a question already asked.
        reason = rec.get("finding")
        require(rec.get("abstain_reason") is None,
                "a ranked result is filed under abstain_reason, where it reads as a refusal", errors)
        require(isinstance(reason, dict) and reason,
                "no window won, but no finding explains why", errors)
        require(not windows, "no window won, yet a window is offered", errors)
        require(recommendation.get("publish_now") is True,
                "no window won, so the answer must be to publish now rather than to wait", errors)
        # The field this used to guard no longer exists — the schema rejects it outright. What is
        # still reachable is a quota written into the prose, which is the same promise in a place
        # no field check can see.
        for text in [(reason or {}).get("statement") or ""] + list(
                (reason or {}).get("what_would_change_this") or []):
            require(not VAGUE.search(text),
                    f"the comparison already ran, yet the finding asks for more data: {text[:60]!r}",
                    errors)
        # "Publish N more and come back" is the answer this outcome exists to refuse: the
        # comparison already ran and found no preferred window, so there is no threshold to walk
        # towards. This used to be a phrase list, which meant it only held in the language the
        # phrases were written in. The quota can only be stated as a number to reach, and the
        # object has no field to put one in — so the check is that no such field appeared.
        for key in ("unlock_threshold", "missing", "required_n", "threshold"):
            require(key not in (reason or {}),
                    f"the finding carries {key!r}. The comparison already ran; a finding that "
                    f"hands the creator a number to reach is a refusal wearing another name",
                    errors)
        require(ranking.get("winner") is None,
                "the ranking names a winner but the outcome says none was found", errors)

    elif outcome == "recommended":
        require(bool(windows), "outcome is 'recommended' but no window is given", errors)
        require(recommendation.get("publish_now") in (False, None),
                "a window was recommended and publish_now is also set", errors)
        winner = ranking.get("winner")
        offered = windows[0] if windows else {}
        by_hour = offered.get("resolution") == "hour"
        # The grid vote is still computed on every run as the audit layer and the fallback, so the
        # prose consistency check below reads it whichever resolution answered.
        votes = ranking.get("metric_votes") or {}

        # Two resolutions, two different things to check against. An hour comes from the kernel
        # curve and is checked against it; a block comes from the grid vote and is checked against
        # that. Checking an hour against the grid was the first thing this validator did after the
        # engine changed, and it failed a correct answer — the grid's winner is a different object
        # answering a coarser question, not a second opinion on the same one.
        if by_hour:
            hours = ((base.get("hour_curve") or {}).get("hours") or {})
            key = str(int(offered.get("start_local", "-1:00").split(":")[0]))
            reading = hours.get(key) or hours.get(int(key)) if hours else None
            require(reading is not None,
                    f"the recommended hour {key} was never read by the kernel curve", errors)
            # `supporting_n` is the hour's own posts; the neighbourhood count is a separate field.
            # Reading them off the same number would let 13 be quoted where 5 is the evidence.
            require(reading is None or offered.get("supporting_n") == reading.get("own_n"),
                    "the hour claims a supporting count the curve does not report", errors)
            require(reading is None or offered.get("neighbourhood_n") == reading.get("n"),
                    "the neighbourhood count does not match the kernel window", errors)
            require((offered.get("supporting_n") or 0) <= (offered.get("neighbourhood_n") or 0),
                    "the hour claims more of its own posts than its neighbourhood holds", errors)
            signal = next((s for s in base.get("signals") or []
                           if s.get("signal") == "own_history_hour"), None)
            require(signal is not None,
                    "an hour was recommended without the signal that resolved it", errors)
            require(signal is None or signal.get("hour") == int(key),
                    "the recommended hour is not the one signal 1 resolved", errors)
            # The whole justification for letting peer accounts touch the answer is that a creator
            # can go and check them. An hour they broke the tie on, with no handle attached, is a
            # generic best-time chart wearing a citation.
            if signal and signal.get("peer_broke_tie"):
                peer = next((s for s in base.get("signals") or []
                             if s.get("signal") == "peer_accounts_hour"), None)
                require(bool((peer or {}).get("accounts")),
                        "peer accounts decided the hour without naming which accounts", errors)
                require(all(a.get("handle") for a in (peer or {}).get("accounts") or []),
                        "a peer account contributed without a handle to check it by", errors)
        else:
            require(winner is not None, "a window was recommended but the ranking names no winner",
                    errors)
            if winner and windows:
                start = offered.get("start_local", "").replace(":00", "")
                require(start and winner.startswith(start),
                        f"the offered window {start!r} is not the ranked winner {winner!r}", errors)
                entry = next((w for w in ranking.get("windows") or []
                              if w.get("window") == winner), None)
                require(entry is not None and entry.get("rankable") is True,
                        "the recommended window was not rankable in the first place", errors)
                require(entry is None or offered.get("supporting_n") == entry.get("n"),
                        "the window claims a supporting count the ranking does not report", errors)
            # Agreement has to be a majority, recomputed rather than trusted: a plurality winner
            # would be a coin toss presented as a recommendation.
            if votes and winner:
                agreeing = sum(1 for w in votes.values() if w == winner)
                require(agreeing * 2 > len(votes),
                        f"{agreeing} of {len(votes)} metrics chose {winner}, not a majority", errors)
                require(offered.get("metrics_agreeing") == agreeing
                        and offered.get("metrics_compared") == len(votes),
                        "the window misreports how many metrics agreed", errors)

        # Whatever the resolution, an hour without the zone it is stated in is not a time. The
        # fields are named `start_local` and for two versions they held UTC; the zone travels
        # beside them now so the host cannot mistake one for the other.
        require(bool(offered.get("zone")),
                "the window does not say which zone its hours are in", errors)

        # Read off the fields, not out of the sentences. Two helpers used to count votes
        # independently and disagreed inside one object -- the granularity reason said five
        # metrics pointed at the same window while the recommendation beside it said three. The
        # check that caught it read the prose, which meant it only worked in the language the
        # prose happened to be in; every place that states the count now carries it as data.
        claims = []
        granularity = ranking.get("granularity") or {}
        if isinstance(granularity, dict) and "metrics_ranked" in (granularity.get("params") or {}):
            claims.append(("ranking.granularity", granularity["params"]["metrics_ranked"]))
        for index, penalty in enumerate(recommendation.get("confidence_penalties") or []):
            params = penalty.get("params") or {}
            if "metrics_ranked" in params:
                claims.append((f"penalty[{index}] {penalty.get('code')!r}",
                               params["metrics_ranked"]))
        if "metrics_ranked" in ((rec.get("finding") or {}).get("params") or {}):
            claims.append(("finding", rec["finding"]["params"]["metrics_ranked"]))
        for where, claimed in claims:
            require(claimed == len(votes),
                    f"{where} says {claimed} metrics could rank, but the vote map holds "
                    f"{len(votes)}", errors)

        # A window scoped to every day cannot rest on a half of the week that holds no posts.
        observed = ranking.get("weekparts_observed")
        if windows and observed:
            scope = windows[0].get("weekday")
            require(scope != "all" or len(observed) == 2,
                    f"the window is scoped to all days, but only {observed} was ever published in",
                    errors)

    # --- confidence must follow from what the object itself reports ---
    if recommendation:
        penalties = recommendation.get("confidence_penalties") or []
        require(all("costs_confidence" in p for p in penalties),
                "a caveat does not say whether it threatens the ranking or only its labels", errors)
        if outcome == "recommended" and windows:
            agreeing = windows[0].get("metrics_agreeing") or 0
            compared = windows[0].get("metrics_compared") or 1
            if windows[0].get("resolution") == "hour":
                # An hour's ceiling comes from peer corroboration, not metric agreement: the
                # engine gives low when peers broke the tie, high when the peer hour lands within
                # one hour of the answer, and medium otherwise.
                signals = base.get("signals") or []
                own = next((s for s in signals if s.get("signal") == "own_history_hour"), {})
                peer_hour = next((s.get("hour") for s in signals
                                  if s.get("signal") == "peer_accounts_hour"), None)
                start = int(str(windows[0].get("start_local") or "0").split(":")[0])
                near = (peer_hour is not None
                        and min(abs(peer_hour - start), 24 - abs(peer_hour - start)) <= 1)
                base_level = ("low" if own.get("peer_broke_tie")
                              else "high" if near else "medium")
            else:
                base_level = ("high" if agreeing / compared >= 0.75
                              and (windows[0].get("supporting_n") or 0) >= 5
                              else "medium" if agreeing / compared > 0.5 else "low")
            for penalty in penalties:
                if penalty.get("costs_confidence"):
                    base_level = {"high": "medium", "medium": "low", "low": "low"}[base_level]
            require(recommendation.get("confidence") == base_level,
                    f"confidence is {recommendation.get('confidence')} but agreement and the "
                    f"stated caveats give {base_level}", errors)
        # A single metric cannot corroborate itself, and the object has to admit that.
        if len(ranking.get("metric_votes") or {}) < 2:
            require(any(p.get("code") == "single_metric_only" for p in penalties),
                    "one metric decided this ranking and nothing says so", errors)

    # --- every scheme considered, not just the flattering one ---
    if outcome == "abstain_no_publish_time":
        # Nothing was bucketed on purpose: with every timestamp at midnight the schemes would each
        # report one occupied bucket, which reads like a finding and is an artefact.
        require(all(not (s.get("distribution") or {}) for s in bucketing),
                "publish time was never observed, yet a bucket distribution is reported", errors)
    else:
        require(len(bucketing) > 1,
                "only one bucketing scheme is reported; the schemes that failed must be shown too",
                errors)

    mature_n = cohort.get("mature_n")
    for scheme in bucketing:
        distribution = scheme.get("distribution") or {}
        zero = scheme.get("zero_observation_strata") or []
        name = scheme.get("scheme")
        if mature_n is not None and distribution:
            require(sum(distribution.values()) == mature_n,
                    f"{name}: the buckets hold {sum(distribution.values())} posts but the cohort "
                    f"has {mature_n}", errors)
        require(scheme.get("buckets_nonempty") == len(distribution),
                f"{name}: buckets_nonempty disagrees with the distribution", errors)
        require(len(zero) == scheme.get("buckets_total", 0) - scheme.get("buckets_nonempty", 0),
                f"{name}: the empty strata are not all listed; an unlisted empty bucket reads as "
                f"average", errors)
        require(not (set(map(str, zero)) & set(distribution)),
                f"{name}: a stratum is listed as both empty and occupied", errors)
        if distribution:
            require(scheme.get("max_bucket_n") == max(distribution.values())
                    and scheme.get("min_bucket_n") == min(distribution.values()),
                    f"{name}: max/min disagree with the distribution", errors)
        means = scheme.get("bucket_mean")
        if means is not None:
            require(set(means) == set(distribution),
                    f"{name}: a per-bucket mean exists for a bucket with no posts, or is missing "
                    f"for one that has them", errors)
            # A mean without the sensitivity check next to it is the number that gets quoted alone.
            require(isinstance(scheme.get("robustness"), dict) and scheme["robustness"].get("note"),
                    f"{name}: per-bucket means are reported without the leave-one-out check", errors)

    # --- an unplaceable zone no longer forbids a window; it must be labelled as one ---
    # Refusing here is what left every real account without an answer. The ranking of UTC windows
    # is valid on its own; what is missing is the translation into the creator's clock, and that
    # has to be said out loud rather than swallowed.
    zone = base.get("timezone") or {}
    if zone.get("source") in UNPLACEABLE_ZONES and (rec.get("recommendation") or {}).get("windows"):
        expected_penalty = (
            "timezone_not_supplied" if zone.get("source") == "not_supplied"
            else "timezone_unresolved"
        )
        require(any(p.get("code") == expected_penalty
                    for p in (rec["recommendation"].get("confidence_penalties") or [])),
                "a window is given in an unresolved zone without saying the hours are not local",
                errors)
    if zone.get("source") in UNPLACEABLE_ZONES:
        require(any(s.get("scheme", "").endswith("_utc") for s in bucketing) or not bucketing,
                "the zone is not placeable but the schemes are named as if they were local",
                errors)

    # --- dispersion must be recomputable ---
    dispersion = base.get("dispersion") or {}
    if dispersion.get("mean"):
        cv = dispersion["stdev"] / dispersion["mean"]
        require(abs(cv - dispersion.get("coefficient_of_variation", 0)) < 5e-4,
                "the coefficient of variation does not follow from the mean and standard deviation",
                errors)

    # --- boundaries that must never be optional ---
    require(bool(rec.get("refused_fallbacks")),
            "refused_fallbacks is empty: a reader cannot tell abstention from an unbuilt feature",
            errors)
    require(bool(rec.get("causal_boundary")), "causal_boundary is required", errors)
    prose = ([rec.get("causal_boundary", "")] + list(rec.get("limitations") or [])
             + [(reason or {}).get("statement", "")]
             + list((reason or {}).get("missing") or []))
    for text in prose:
        require(not CAUSAL.search(QUOTED.sub("", text or "")),
                f"a causal promise appears in prose that must stay correlational: {text[:60]!r}",
                errors)
    panel = base.get("metric_panel") or []
    if any("lifetime" in (e.get("period") or "") for e in panel):
        require("maturity" in json.dumps(rec.get("limitations") or []).lower()
                or "older" in json.dumps(rec.get("limitations") or []).lower(),
                "a lifetime metric is used without stating the maturity confound", errors)
    # Direction has to travel with every metric: skip rate ranked like views puts the worst window
    # on top, and nothing downstream could tell.
    checked = list(panel) + list((history or {}).get("metric_panel") or [])
    require(all(e.get("direction") in ("higher_is_better", "lower_is_better") for e in checked),
            "a metric is carried without saying which direction is good", errors)

    # --- the numbers must match the history they claim to come from ---
    if history is not None:
        require(cohort.get("platform") == history.get("platform"),
                "the cohort platform does not match the history platform", errors)
        require(cohort.get("snapshot_sha256") == history["source"]["snapshot_sha256"],
                "the cohort cites a different snapshot than the history supplied", errors)
        mature = [p for p in history["posts"]
                  if p["is_mature"] and p["product_type"] == cohort.get("format")]
        require(cohort.get("mature_n") == len(mature),
                f"cohort.mature_n is {cohort.get('mature_n')} but the history holds {len(mature)} "
                f"mature {cohort.get('format')} rows", errors)
        require([e["name"] for e in panel] == [e["name"] for e in history["metric_panel"]],
                "the recommendation reports a different metric panel than the history carries",
                errors)
        # One panel, one population. Mixing an owner figure with a public counter in the same
        # ranking would compare two different audiences and call the difference a publish hour.
        require(len({e["scope"] for e in history["metric_panel"]}) == 1,
                "the panel mixes owner metrics with public counters in one ranking", errors)
        if any(e["scope"] == "public" for e in history["metric_panel"]):
            require("public" in json.dumps(rec.get("limitations") or []).lower(),
                    "a public counter was used without saying so in the limitations", errors)
        if mature:
            span = (datetime.fromisoformat(max(p["published_at"] for p in mature))
                    - datetime.fromisoformat(min(p["published_at"] for p in mature))).days
            require(str(span) in json.dumps(rec.get("limitations") or []),
                    "the limitations do not state how short the observed window is", errors)

    if request is not None:
        if request_path is not None:
            errors.extend(validate_request_artifacts(request, request_path))
        require(rec.get("request_ref") == request.get("request_id"),
                "request_ref does not match the request", errors)
        require(cohort.get("format") == request["content"]["format"],
                "the cohort is not the format the request asked about", errors)
        require(cohort.get("platform") == request["content"]["platform"],
                "the cohort is not the platform the request asked about", errors)
        if history is not None:
            require(history.get("platform") == request["content"]["platform"],
                    "the history is not the platform the request asked about", errors)

    # --- a schedule must not launder a refusal into advice ---
    if schedule is not None:
        require(schedule.get("recommendation_ref") == rec.get("recommendation_id"),
                "the schedule references a different recommendation", errors)
        if outcome != "recommended":
            require(schedule.get("source") != "engine_recommended",
                    "a schedule claims the engine recommended this time, but the engine abstained",
                    errors)
            require(schedule.get("confidence") is None,
                    "a schedule carries a confidence although no recommendation was made", errors)
        require(bool(schedule.get("draft_ref")),
                "a schedule with no draft_ref cannot be attributed back to the content", errors)
        states = [entry["state"] for entry in schedule.get("state_history") or []]
        require(states and states[-1] == schedule.get("state"),
                "state_history does not end at the current state", errors)

    return errors


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("recommendation")
    ap.add_argument("history", nargs="?")
    ap.add_argument("request", nargs="?")
    ap.add_argument("--schedule")
    args = ap.parse_args()

    load = lambda p: json.loads(Path(p).read_text(encoding="utf-8")) if p else None
    errors = validate(load(args.recommendation), load(args.history), load(args.request),
                      load(args.schedule), Path(args.request).resolve() if args.request else None)
    for error in errors:
        print(f"FAIL {error}")
    print("OK" if not errors else f"{len(errors)} problem(s)")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
