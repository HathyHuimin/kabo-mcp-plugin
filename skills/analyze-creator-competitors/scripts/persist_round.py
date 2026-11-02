#!/usr/bin/env python3
"""Place this run's profile and account-history Envelopes by whose they are, not by call order.

`persist_many.py` binds each response to a file by position: the n-th `--dest` takes the n-th
unplaced response. That leaves two things to the model — typing every target in the right order,
and never letting a stray response into the queue — and on 2026-09-18 an Instagram competitor
run got both wrong. One command named two operations, placed nothing, and the recovery refetched
the subject's Reels; the spare copy then took the first competitor's slot, every later page slid
one account down, and the model spent a chain of six `mv` putting them back. About 45 s, and had
it not noticed, the report would have credited one account's Reels to another.

Here nobody names a target. Each response says whose it is:

  resolve_profile        data.profile.username
  get_channel            rows[0].id (matched) / custom_url (file label)
  list_channel_uploads   data.channel.id (matched) / custom_url (file label)
  list_reels, list_posts the one account present in *every* row, as poster or co-author

The last rule is the careful one. A feed is mostly the account's own posts, but collaborations
are recorded under whoever started them, so "most frequent poster" names the wrong account: on
that same run one page was 7/12 another brand's, and a majority vote filed it under that brand.
The owner is instead the account that appears in each row one way or the other — on the 2026-09-18
pages that rule named every non-empty page correctly and refused none. When no single account
spans every row it refuses rather than guesses.

The accounts this round may place are the discovery roster — `selected_handles` then
`reserve_handles` from `competitor-discovery-selection.json`. A response whose owner is outside it
(a duplicate of the subject's own history, say) is reported and left unplaced, which is exactly
the copy that caused the slide above. An empty page carries no rows to read an owner from; it
goes, in fetch order, to the roster accounts that no readable page of that operation claimed.
Two empty pages swapped between two accounts still say the same thing about both.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

from persist_many import staged_here  # noqa: E402
from persist_envelope import (  # noqa: E402
    Candidate,
    already_persisted,
    default_staging_roots,
    drained_candidates,
    identity,
    in_order,
    row_count,
    run_dir_of,
    run_lock,
    run_started,
    staged_candidates,
)

SELECTION = "competitor-discovery-selection.json"
PROFILE_OPERATIONS = ("resolve_profile", "get_channel")
HISTORY_OPERATIONS = ("list_reels", "list_posts", "list_channel_uploads")
PREFIX = {
    "resolve_profile": "profile",
    "get_channel": "profile",
    "list_reels": "reels",
    "list_posts": "posts",
    "list_channel_uploads": "uploads",
}
SAFE_LABEL = re.compile(r"[^A-Za-z0-9._-]+")


def clean(value: Any) -> str | None:
    if isinstance(value, (str, int)) and str(value).strip():
        return str(value).strip().lstrip("@")
    return None


def people(row: dict[str, Any]) -> set[str]:
    """Every account a feed row is credited to: its poster and each co-author."""
    media = row.get("media") if isinstance(row.get("media"), dict) else row
    found: set[str] = set()
    user = media.get("user") if isinstance(media.get("user"), dict) else {}
    if clean(user.get("username")):
        found.add(clean(user.get("username")).lower())
    for author in media.get("coauthor_producers") or []:
        if isinstance(author, dict) and clean(author.get("username")):
            found.add(clean(author.get("username")).lower())
    return found


def owner_of(envelope: dict[str, Any]) -> tuple[str, str] | None:
    """(roster key, file label) for whoever this response is about, or None when it does not say."""
    operation = envelope.get("operation")
    data = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
    if operation == "resolve_profile":
        profile = data.get("profile") if isinstance(data.get("profile"), dict) else {}
        name = clean(profile.get("username"))
        return (name.lower(), name) if name else None
    if operation in ("get_channel", "list_channel_uploads"):
        if operation == "get_channel":
            rows = data.get("rows") if isinstance(data.get("rows"), list) else []
            channel = rows[0] if rows and isinstance(rows[0], dict) else {}
        else:
            channel = data.get("channel") if isinstance(data.get("channel"), dict) else {}
        channel_id = clean(channel.get("id"))
        if not channel_id:
            return None
        return channel_id, clean(channel.get("custom_url")) or channel_id
    if operation in ("list_reels", "list_posts"):
        rows = [row for row in data.get("rows") or [] if isinstance(row, dict)]
        credited = [found for found in (people(row) for row in rows) if found]
        if not credited:
            return None
        everywhere = set.intersection(*credited)
        if len(everywhere) != 1:
            return None
        name = next(iter(everywhere))
        return name, name
    return None


def roster_of(run_dir: Path) -> list[str]:
    path = run_dir / SELECTION
    try:
        selection = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(
            f"{SELECTION} is needed to know which accounts this round may place ({error}); "
            "run select_accounts.py discover first"
        ) from error
    roster: list[str] = []
    for key in ("selected_handles", "reserve_handles"):
        for handle in selection.get(key) or []:
            name = clean(handle)
            if name and name.lower() not in [entry.lower() for entry in roster]:
                roster.append(name)
    if not roster:
        raise ValueError(f"{SELECTION} lists no selected or reserve handles")
    return roster


def next_page(run_dir: Path, prefix: str, label: str) -> int:
    pattern = re.compile(rf"^{re.escape(prefix)}-{re.escape(label)}-p(\d+)\.json$")
    taken = [int(match.group(1)) for path in run_dir.glob(f"{prefix}-*.json")
             if (match := pattern.match(path.name))]
    return max(taken, default=0) + 1


def target_for(run_dir: Path, operation: str, label: str) -> Path:
    prefix = PREFIX[operation]
    safe = SAFE_LABEL.sub("_", label)
    if operation in PROFILE_OPERATIONS:
        return run_dir / f"{prefix}-{safe}.json"
    return run_dir / f"{prefix}-{safe}-p{next_page(run_dir, prefix, safe)}.json"


def candidates_for(run_dir: Path, operations: list[str], drained: Path | None,
                   problems: list[str]) -> list[Candidate]:
    anchor = run_dir / ".persist-round.json"
    since = run_started(anchor)
    roots = default_staging_roots()
    found: list[Candidate] = []
    for operation in operations:
        found += staged_candidates(roots, None, operation, since, problems)
    if not found and drained is not None:
        for operation in operations:
            found += drained_candidates(drained, None, operation, problems)
    taken = Counter(already_persisted(anchor, None, None, drained))
    unplaced: list[Candidate] = []
    for candidate in in_order(found):
        key = identity(candidate.envelope)
        if taken[key] > 0:
            taken[key] -= 1
            continue
        unplaced.append(candidate)
    return unplaced


def write(target: Path, candidate: Candidate) -> None:
    if candidate.body is not None:
        target.write_bytes(candidate.body)
    else:
        target.write_text(json.dumps(candidate.envelope, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    target.chmod(0o600)


def place_round(run_dir: Path, operations: list[str], drained: Path | None = None) -> dict[str, Any]:
    roster = roster_of(run_dir)
    by_key = {name.lower(): name for name in roster}
    problems: list[str] = []
    placed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    with run_lock(run_dir):
        pending = candidates_for(run_dir, operations, drained, problems)
        if not pending:
            detail = f" ({'; '.join(problems[:5])})" if problems else ""
            held = staged_here(run_dir / ".persist-round.json") or {}
            have = ", ".join(f"{name} x{count}" for name, count in sorted(held.items())) or "nothing yet"
            raise ValueError(
                f"no unplaced host record of {', '.join(operations)} for this run{detail}. This run's staging "
                f"holds: {have}. If a call is missing there it was never sent, or not sent yet: send it, then run "
                "this same command again. If this came from `drive.py <stage> --placed` and that stage never "
                "printed calls for you to make in this run, run the same stage again without `--placed`: it "
                "fetches the round itself, or places one already handed over. The run directory is fine — keep "
                "using it, and do not hand-write a truncated tool result.")
        blind: dict[str, list[Candidate]] = {}
        claimed: dict[str, set[str]] = {operation: set() for operation in operations}
        for candidate in pending:
            operation = str(candidate.envelope.get("operation"))
            owner = owner_of(candidate.envelope)
            if owner is None:
                # Only an empty history page may be matched by order: it says the same thing
                # ("nothing in the window") whichever roster account it lands on. A page with rows
                # that names no single owner, or a profile that names nobody, is exactly the case
                # order would get wrong, so it is reported and left for an explicit placement.
                if operation in HISTORY_OPERATIONS and not row_count(candidate.envelope):
                    blind.setdefault(operation, []).append(candidate)
                else:
                    skipped.append({"source": candidate.source, "operation": operation, "owner": None,
                                    "reason": "its rows name no single account; place it with persist_many.py"})
                continue
            key, label = owner
            if key.lower() not in by_key:
                skipped.append({"source": candidate.source, "operation": operation, "owner": label,
                                "reason": "not in the discovery roster"})
                continue
            target = target_for(run_dir, operation, label)
            if operation in PROFILE_OPERATIONS and target.exists():
                skipped.append({"source": candidate.source, "operation": operation, "owner": label,
                                "reason": f"{target.name} is already placed"})
                continue
            write(target, candidate)
            claimed[operation].add(key.lower())
            placed.append({"path": target.name, "operation": operation, "account": by_key[key.lower()],
                           "status": candidate.envelope.get("status"), "rows": row_count(candidate.envelope),
                           "matched_by": "content"})
        for operation, pages in blind.items():
            # Roster order is the order discovery froze and the calls were made in.
            open_accounts = [name for name in roster if name.lower() not in claimed.get(operation, set())]
            for candidate in pages:
                if not open_accounts:
                    skipped.append({"source": candidate.source, "operation": operation, "owner": None,
                                    "reason": "says nothing about its account and no roster account is left for it"})
                    continue
                account = open_accounts.pop(0)
                target = target_for(run_dir, operation, account)
                write(target, candidate)
                placed.append({"path": target.name, "operation": operation, "account": account,
                               "status": candidate.envelope.get("status"), "rows": row_count(candidate.envelope),
                               "matched_by": "fetch order (the response names no account)"})
    return {"status": "ok", "placed": placed, "skipped": skipped}


def report(result: dict[str, Any]) -> str:
    lines = [f"persist_round: placed {len(result['placed'])}, skipped {len(result['skipped'])}"]
    for row in result["placed"]:
        rows = "?" if row["rows"] is None else row["rows"]
        note = "" if row["matched_by"] == "content" else f"  [{row['matched_by']}]"
        lines.append(f"  {row['path']:36s} {row['operation']:22s} {row['account']:24s} {row['status']}, {rows} rows{note}")
    for row in result["skipped"]:
        lines.append(f"  skipped {row['operation']} of {row['owner'] or '?'}: {row['reason']} ({row['source']})")
    profiles = [row["path"] for row in result["placed"] if row["operation"] in PROFILE_OPERATIONS]
    if profiles:
        lines.append("eligibility input: " + " ".join(f"--profile-envelope {path}" for path in profiles))
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--operation", action="append", required=True,
                        choices=[*PROFILE_OPERATIONS, *HISTORY_OPERATIONS],
                        help="an operation this round fetched; repeat for each")
    parser.add_argument("--drained", type=Path, help="a directory of this run that already holds response files")
    args = parser.parse_args()
    try:
        run_dir = run_dir_of(Path.cwd() / ".persist-round.json")
        if run_dir is None:
            raise ValueError("run this from inside the run directory (<YYYYMMDDTHHMMSS>Z-<id>)")
        result = place_round(run_dir, list(dict.fromkeys(args.operation)), args.drained)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "failed", "error": f"{type(error).__name__}: {error}"}, ensure_ascii=False))
        return 2
    print(report(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
