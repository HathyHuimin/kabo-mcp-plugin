#!/usr/bin/env python3
"""Project the evidence a competitor synthesis may cite, one compact row per analyzed item.

The synthesis step needs, per included account, which analyzed items exist, what each one is
about, and how it performed against its own account. Those facts are spread across the Analysis
(evidence, outliers), the Snapshot (titles) and the eligibility and selection files. Without this
projection the model queries them one `jq` at a time; measured 2026-09-17 on a YouTube run, six
such queries before a single synthesis write.

The citation allowlist here is the same rule the skill states: eligible direct, adjacent or media
accounts that are also in `included_handles`, intersected with Snapshot items and `analysis.evidence`,
plus the request's seed item when it is analyzed. Nothing is ranked or dropped semantically; rows are
only ordered by the Analyzer's own `evidence_score`, then views.

Each account also carries its posting cadence in the request window, and `subject_cadence` gives the
subject's own from its placed history page. A user asking to compare posting cadence otherwise got only
"a predictable rhythm": the dates were in the Snapshot but no count reached the synthesis (plugins,
2026-10-27). A count is a lower bound when the fetched pages stop inside the
window without a terminal page.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from datetime import datetime
from typing import Any

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_public_snapshot import dig, parse_timestamp, rows_of, terminal_page, unwrap  # noqa: E402

MAX_TITLE_CHARS = 100
MAX_ROWS_PER_ACCOUNT = 12
MIN_COMPARABLES = 5
MIN_MULTIPLE = 1.5


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def canonical_token(value: str) -> str:
    stripped = (value or "").strip().removeprefix("@")
    if not stripped:
        return ""
    if re.fullmatch(r"UC[\w-]{22}", stripped):
        return stripped
    return stripped.casefold()


def token_aliases(value: Any) -> set[str]:
    token = canonical_token(str(value or ""))
    if not token:
        return set()
    aliases = {token}
    if ":" in token:
        tail = canonical_token(token.split(":", 1)[1])
        if tail:
            aliases.add(tail)
    return aliases


def ref_tokens(values: Any) -> set[str]:
    tokens: set[str] = set()
    for value in values or []:
        if isinstance(value, dict):
            for key in ("account_ref", "handle", "creator_id"):
                tokens.update(token_aliases(value.get(key)))
        else:
            tokens.update(token_aliases(value))
    return tokens


def short(text: Any) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= MAX_TITLE_CHARS else flat[: MAX_TITLE_CHARS - 1] + "…"


def number(value: Any) -> float | None:
    if isinstance(value, dict):
        value = value.get("value")
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def cadence(dates: list[datetime], since: datetime, until: datetime, complete: bool) -> dict[str, Any]:
    """Posts inside [since, until] and the weekly rate they imply; `complete` says the count is exact.

    A count that is only a lower bound carries no rate or gap: given one, the synthesis ranked accounts
    by it and called competitors faster than a subject whose fetched page simply ended (plugins,
    2026-10-27). It is `posts_in_window_at_least` alone.
    """
    inside = sorted(moment for moment in dates if since <= moment <= until)
    if not complete:
        return {"posts_in_window_at_least": len(inside), "count_is_lower_bound": True}
    days = max((until - since).total_seconds() / 86400, 1)
    gaps = [(later - earlier).total_seconds() / 86400 for earlier, later in zip(inside, inside[1:])]
    return {
        "posts_in_window": len(inside),
        "posts_per_week": round(len(inside) * 7 / days, 1),
        "median_gap_days": round(sorted(gaps)[len(gaps) // 2], 1) if gaps else None,
        "count_is_lower_bound": False,
    }


def window_of(request: dict[str, Any] | None) -> tuple[datetime, datetime] | None:
    window = (request or {}).get("window") if isinstance((request or {}).get("window"), dict) else {}
    try:
        return parse_timestamp(window.get("since"), "window.since"), parse_timestamp(window.get("until"), "window.until")
    except (ValueError, TypeError):
        return None


def history_dates(envelope: dict[str, Any]) -> list[datetime]:
    dates = []
    for raw in rows_of(envelope):
        row = unwrap(raw)
        value = dig(row, ("published_at",)) or dig(row, ("publishedAt",)) or dig(row, ("snippet", "publishedAt")) \
            or dig(row, ("taken_at",)) or dig(row, ("taken_at_timestamp",)) or dig(row, ("timestamp",))
        try:
            dates.append(parse_timestamp(value, "published_at"))
        except (ValueError, TypeError):
            continue
    return dates


def subject_cadence(history: dict[str, Any] | None, window: tuple[datetime, datetime] | None) -> dict[str, Any] | None:
    if not history or window is None:
        return None
    dates = history_dates(history)
    if not dates:
        return None
    since, until = window
    return cadence(dates, since, until, terminal_page(history) or min(dates) < since)


def build(
    analysis: dict[str, Any],
    snapshot: dict[str, Any],
    selection: dict[str, Any],
    eligibility: dict[str, Any],
    request: dict[str, Any] | None,
    subject_history: dict[str, Any] | None = None,
) -> dict[str, Any]:
    relationships = {
        name: ref_tokens(eligibility.get(f"{name}_refs")) for name in ("direct", "adjacent", "media")
    }
    included = ref_tokens(selection.get("included_handles"))
    creators = {
        str(row.get("creator_id")): row
        for row in snapshot.get("creators") or []
        if isinstance(row, dict) and row.get("creator_id")
    }
    items = {
        str(row.get("content_id")): row
        for row in snapshot.get("items") or []
        if isinstance(row, dict) and row.get("content_id")
    }
    evidence = {
        str(row.get("content_id")): row
        for row in analysis.get("evidence") or []
        if isinstance(row, dict) and row.get("content_id")
    }
    outliers = {
        str(row.get("content_id")): row
        for row in analysis.get("account_outliers") or []
        if isinstance(row, dict) and row.get("content_id")
    }
    subject = (request or {}).get("subject") if isinstance((request or {}).get("subject"), dict) else {}
    seed_ref = str(subject.get("seed_content_ref") or "")

    def account_of(creator_id: str) -> tuple[set[str], str | None]:
        creator = creators.get(creator_id) or {}
        tokens = token_aliases(creator_id) | token_aliases(creator.get("handle"))
        return tokens, creator.get("handle")

    accounts: dict[str, dict[str, Any]] = {}
    allowlist: list[str] = []
    seed_row: dict[str, Any] | None = None
    for content_id, item in items.items():
        if content_id not in evidence:
            continue
        creator_id = str(item.get("creator_id") or "")
        tokens, handle = account_of(creator_id)
        relationship = next((name for name, refs in relationships.items() if not refs.isdisjoint(tokens)), None)
        outlier = outliers.get(content_id) or {}
        multiple = number(outlier.get("account_median_multiple"))
        comparables = outlier.get("comparable_n")
        metrics = item.get("metrics") if isinstance(item.get("metrics"), dict) else {}
        row = {
            "content_id": content_id,
            "title": short(item.get("title") or item.get("caption")),
            "published_at": item.get("published_at"),
            "format": item.get("format"),
            "views": number(metrics.get("views")) if metrics else number(evidence[content_id].get("metrics", {}).get("views")),
            "outlier_multiple": round(multiple, 2)
            if multiple is not None and isinstance(comparables, int) and comparables >= MIN_COMPARABLES and multiple >= MIN_MULTIPLE
            else None,
            "evidence_score": outlier.get("evidence_score"),
        }
        eligible = relationship is not None and not included.isdisjoint(tokens)
        if content_id == seed_ref:
            seed_row = {**row, "account_ref": creator_id, "handle": handle}
        if not eligible:
            if content_id == seed_ref:
                allowlist.append(content_id)
            continue
        allowlist.append(content_id)
        account = accounts.setdefault(
            creator_id,
            {"account_ref": creator_id, "handle": handle, "relationship": relationship, "evidence": []},
        )
        account["evidence"].append(row)

    window = window_of(request)
    snapshot_complete = (snapshot.get("source") or {}).get("coverage_complete") is True
    dates_by_creator: dict[str, list[datetime]] = {}
    for item in items.values():
        try:
            dates_by_creator.setdefault(str(item.get("creator_id") or ""), []).append(
                parse_timestamp(item.get("published_at"), "published_at"))
        except (ValueError, TypeError):
            continue
    rendered = []
    for account in accounts.values():
        rows = sorted(
            account["evidence"],
            key=lambda row: (row["evidence_score"] is None, -(row["evidence_score"] or 0), -(row["views"] or 0), row["content_id"]),
        )
        rendered.append({
            **{key: account[key] for key in ("account_ref", "handle", "relationship")},
            "evidence_count": len(rows),
            "evidence": rows[:MAX_ROWS_PER_ACCOUNT],
            **({"evidence_not_listed": len(rows) - MAX_ROWS_PER_ACCOUNT} if len(rows) > MAX_ROWS_PER_ACCOUNT else {}),
            **({"cadence": cadence(dates, window[0], window[1], snapshot_complete or min(dates) < window[0])}
               if window is not None and (dates := dates_by_creator.get(account["account_ref"])) else {}),
        })
    order = {"direct": 0, "adjacent": 1, "media": 2}
    rendered.sort(key=lambda row: (order.get(row["relationship"], 3), -row["evidence_count"], str(row["handle"] or "")))
    with_evidence = set().union(*(token_aliases(row["account_ref"]) | token_aliases(row["handle"]) for row in rendered)) if rendered else set()
    without = [
        handle for handle in selection.get("included_handles") or []
        if token_aliases(handle).isdisjoint(with_evidence)
    ]
    return {
        "schema_version": "competitor-synthesis-evidence.v1",
        "accounts": rendered,
        "included_without_evidence": without,
        "seed_item": seed_row,
        **({"subject_cadence": own} if (own := subject_cadence(subject_history, window)) is not None else {}),
        "citation_allowlist": sorted(set(allowlist)),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--analysis", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--eligibility", type=Path, required=True)
    parser.add_argument("--request", type=Path)
    parser.add_argument("--subject-history", type=Path, help="the subject's placed history page, for its cadence")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--print", action="store_true", help="also print the projection")
    args = parser.parse_args()
    try:
        projection = build(
            load(args.analysis),
            load(args.snapshot),
            load(args.selection),
            load(args.eligibility),
            load(args.request) if args.request else None,
            load(args.subject_history) if args.subject_history and args.subject_history.is_file() else None,
        )
        text = json.dumps(projection, ensure_ascii=False, separators=(",", ":"))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(projection, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"status": "ok", "accounts": len(projection["accounts"]), "citations": len(projection["citation_allowlist"]), "path": str(args.output)}))
        if args.print:
            print(text)
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "failed", "error": f"{type(error).__name__}: {error}"}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
