#!/usr/bin/env python3
"""Turn a recommendation into a slot a host can act on.

Everything the three benchmarked products do that we do not comes down to this object. One ends
on a countdown and a Remind Me, one ends inside the platform's own upload page, one ends on a
push. We
ended on a paragraph: the schema for this object has been in the package since V2.2 and nothing has
ever produced one, so `interaction` was null on every run ever published.

This schedules nothing. It records a decision, its provenance and the instant it points at; the
reminder, the queue and the upload are App services and stay there. Its one hard rule comes from
the schema and is worth restating: **a refusal must never be laundered into a recommendation.**
`engine_recommended` is emitted only when the engine actually recommended, and only for an instant
inside the window it named.

Three outcomes, three behaviours:

  * `recommended` — a proposed slot at the next occurrence of the named hour, `engine_recommended`.
  * `no_clear_window` — nothing. The answer is "publish now"; there is no future instant to hold,
    and manufacturing one would turn "waiting buys you nothing" into a reason to wait.
  * either `abstain_*` — nothing. The creator may still schedule whenever they like, but that is
    their decision to record, not ours to propose.

    python3 build_publish_schedule.py <publish_recommendation.json> --draft-ref <ref> --now <iso>
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

sys.dont_write_bytecode = True

_spec = importlib.util.spec_from_file_location(
    "compute_timing", Path(__file__).resolve().parent / "compute_timing.py")
_engine = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("compute_timing", _engine)
_spec.loader.exec_module(_engine)

WEEKDAY_INDEX = {name: index for index, name in enumerate(_engine.WEEKDAY_NAMES)}
# Recommendations written before the engine's machine values became English are still on disk and
# still get scheduled from. The engine emits none of these any more; reading them costs one dict.
WEEKDAY_INDEX.update({name: index for index, name in enumerate(
    ("\u5468\u4e00", "\u5468\u4e8c", "\u5468\u4e09", "\u5468\u56db",
     "\u5468\u4e94", "\u5468\u516d", "\u5468\u65e5"))})


def next_occurrence(now: datetime, hour: int, weekday: str | None) -> datetime:
    """The soonest instant at `hour` that satisfies `weekday`, at or after `now`.

    Strictly after the current hour, never inside it: a slot that opened twenty minutes ago is not
    something a creator can still hit, and offering it as the plan would be the countdown competitors
    run, without the countdown.
    """
    candidate = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    target = WEEKDAY_INDEX.get(weekday) if weekday else None
    if target is None and weekday in ("weekday", "weekend"):
        for _ in range(7):
            is_weekend = candidate.weekday() >= 5
            if (weekday == "weekend") == is_weekend:
                return candidate
            candidate += timedelta(days=1)
        return candidate
    if target is not None:
        for _ in range(7):
            if candidate.weekday() == target:
                return candidate
            candidate += timedelta(days=1)
    return candidate


def build(rec: dict, draft_ref: str, now: datetime, schedule_id: str | None = None) -> dict | None:
    if rec.get("outcome") != "recommended":
        return None
    recommendation = rec.get("recommendation") or {}
    windows = recommendation.get("windows") or []
    if not windows:
        return None
    window = windows[0]

    timezone_evidence = (rec.get("evidence_base") or {}).get("timezone") or {}
    zone_source = timezone_evidence.get("source")
    supplied_sources = {"creator_confirmed", "profile_confirmed", "assumed"}
    candidate_zone = str(window.get("zone") or timezone_evidence.get("analysis_zone") or "").strip()
    if zone_source == "not_supplied":
        zone, zone_name = ZoneInfo("UTC"), "UTC"
        zone_source, creator_zone = "not_supplied", None
    elif zone_source == "zone_unresolved":
        zone, zone_name = ZoneInfo("UTC"), "UTC"
        creator_zone = None
    elif not candidate_zone:
        zone, zone_name = ZoneInfo("UTC"), "UTC"
        zone_source, creator_zone = "not_supplied", None
    else:
        if zone_source not in supplied_sources:
            zone_source = "assumed"
        try:
            zone = ZoneInfo(candidate_zone)
            zone_name, creator_zone = candidate_zone, candidate_zone
        except (ZoneInfoNotFoundError, ValueError, TypeError):
            zone, zone_name = ZoneInfo("UTC"), "UTC"
            zone_source, creator_zone = "zone_unresolved", None

    hour = int(str(window["start_local"]).split(":")[0])
    weekday = window.get("weekday")
    scheduled = next_occurrence(now.astimezone(zone), hour,
                                None if weekday == "all" else weekday)

    return {
        "schema_version": "publish-schedule.v1",
        "schedule_id": schedule_id or f"psch-{rec['recommendation_id']}",
        "recommendation_ref": rec["recommendation_id"],
        "draft_ref": draft_ref,
        "scheduled_at": scheduled.isoformat(),
        "timezone": {"zone": creator_zone, "source": zone_source},
        "source": "engine_recommended",
        "confidence": recommendation.get("confidence"),
        "state": "proposed",
        "state_history": [{
            "state": "proposed", "at": now.isoformat(), "by": "engine",
            "note": (f"Next {window['start_local']}-{window['end_local']} in {zone_name}"
                     + ("" if weekday == "all" else f", limited to {weekday}")),
        }],
        "published_result_ref": None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recommendation", type=Path)
    parser.add_argument("--draft-ref", required=True)
    parser.add_argument("--now", required=True,
                        help="reference instant, passed in rather than read from the clock so "
                             "reruns stay byte-identical")
    parser.add_argument("--schedule-id", default=None)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    rec = json.loads(args.recommendation.read_text(encoding="utf-8"))
    schedule = build(rec, args.draft_ref, datetime.fromisoformat(args.now), args.schedule_id)
    if schedule is None:
        print(f"no schedule: outcome is {rec.get('outcome')!r}, which proposes no instant",
              file=sys.stderr)
        return 0
    text = json.dumps(schedule, ensure_ascii=False, indent=1)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
        print(f"wrote {args.output}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
