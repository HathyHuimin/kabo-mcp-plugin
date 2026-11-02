#!/usr/bin/env python3
"""Place several of this run's Envelopes with one command, in fetch order.

`persist_envelope.py` takes one Envelope per invocation, so a stage that fetched six accounts turns
into six `python3 <95-character skill path>/persist_envelope.py …` clauses chained with `&&`. The
model has to type all of it: measured 2026-09-18 on a competitor run, those chained commands were
1.6–2.7 KB and each took 25–33 s of model output, which is where the round trips saved by batching
went. This wrapper takes the connector and operation once and the targets as a short list; the
placement rules, ordering and failure behaviour are `persist_envelope.py`'s own — it is called once
per target, in the order given.

    persist_many.py --connector instagram-discovery --operation list_reels \\
      --dest reels-a-p1.json --dest reels-b-p1.json
    persist_many.py --connector instagram-discovery --operation search_profiles --any-status \\
      --into profile-discovery-source.json --at results --slot 0 --slot 1 --slot 2
    persist_many.py --connector tiktok-public --operation list_account_posts --any-status \\
      --into account-history-envelopes.json --at runs --slot alpha --slot beta --handle alpha --handle beta
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

from collections import Counter  # noqa: E402

from persist_envelope import (  # noqa: E402
    default_staging_roots,
    file_time,
    parse_time,
    persist,
    run_dir_of,
    run_started,
)

MISSING_RECORD = "no complete host record"


def staged_here(target: Path) -> Counter[str] | None:
    """What this run's staging actually holds, by operation; None outside a run directory."""
    run_dir = run_dir_of(target)
    if run_dir is None:
        return None
    since = run_started(target)
    roots = default_staging_roots()
    held: Counter[str] = Counter()
    seen: set[Path] = set()
    for root in roots:
        # The same place persist_envelope.py reads: static_host.py stage writes every response
        # flat into this run's staging directory.
        try:
            metas = list(root.glob("*.meta")) if root.is_dir() else []
        except OSError:
            continue
        for meta_path in metas:
            try:
                already = meta_path.resolve() in seen
                seen.add(meta_path.resolve())
            except OSError:
                already = False
            if already:
                continue
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(meta, dict):
                continue
            recorded = parse_time(meta.get("staged_at")) or file_time(meta_path)
            if since is not None and (recorded is None or recorded < since):
                continue
            held[str(meta.get("operation") or meta.get("schema_version") or "?")] += 1
    return held


def explain_missing(error: str, target: Path, operation: str) -> str:
    """Turn "no host record" into what to do next, because the model read it as a broken run.

    Measured 2026-09-18: the subject's `list_reels` was never sent, placement failed with the bare
    message, and the model concluded the run directory was at fault — it reserved a second one and
    redid the subject search inside it. The record was missing because the call was; the directory
    was fine. Saying which operations *did* land makes that visible in one line.
    """
    if MISSING_RECORD not in error or "before this run directory was reserved" in error:
        return error
    held = staged_here(target)
    if held is None:
        return error
    have = ", ".join(f"{name} x{count}" for name, count in sorted(held.items())) or "nothing yet"
    return (f"{error}. This run's staging holds: {have}; no unplaced {operation}. "
            f"The {operation} call was most likely never sent, or not sent yet: send it, then run this "
            f"same command again. The run directory is fine — keep using it.")


def reject_repeated_scope(argv: list[str]) -> None:
    """One command places one operation's Envelopes; a repeated scope flag is a mistake, not a batch.

    `--connector` and `--operation` are single-valued, so argparse would let the later value win
    without a word and every placement would then be looked up under the wrong operation. Measured
    2026-09-18 on an Instagram competitor run: one command passed `resolve_profile` and `list_reels`
    together, placed neither, and the recovery — listing the staging directory, refetching the
    subject's Reels — cost about 45 s. Worse, that refetch left a second staged copy of the same
    response, which later slid into the first competitor's slot and had to be unpicked by hand with
    a chain of `mv`. Failing here costs one command; failing silently cost a quarter of that run.
    """
    for flag in ("--connector", "--operation"):
        seen = sum(1 for arg in argv if arg == flag or arg.startswith(f"{flag}="))
        if seen > 1:
            raise ValueError(
                f"{flag} was given {seen} times, but one persist_many.py call places the Envelopes of "
                f"exactly one connector operation. Split it: one persist_many.py per operation, chained "
                f"with && in the same command."
            )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--connector", required=True)
    parser.add_argument("--operation", required=True)
    parser.add_argument("--any-status", action="store_true")
    parser.add_argument("--drained", type=Path)
    parser.add_argument("--staging", type=Path, action="append", help="staging root to search instead of the defaults")
    parser.add_argument("--dest", type=Path, action="append", default=[],
                        help="one output file per Envelope, in fetch order")
    parser.add_argument("--handle", action="append", default=[],
                        help="verified handle for the matching --dest / --slot, in the same order")
    parser.add_argument("--into", type=Path, help="bundle file the Envelopes are placed inside")
    parser.add_argument("--at", action="append", default=[],
                        help="position segments before the slot index, e.g. --at results")
    parser.add_argument("--slot", action="append", default=[],
                        help="one position per Envelope inside --into/--at, in fetch order: a 0-based "
                             "index for a list slot, or a name for a keyed slot such as a run name")
    args = parser.parse_args()
    results = []
    try:
        reject_repeated_scope(sys.argv[1:])
        if bool(args.dest) == bool(args.into):
            raise ValueError("give either --dest (one per Envelope) or --into with --slot")
        targets = args.dest if args.dest else args.slot
        if not targets:
            raise ValueError("nothing to place: pass --dest or --slot at least once")
        if args.handle and len(args.handle) != len(targets):
            raise ValueError("--handle must be given once per target, in the same order")
        for index, target in enumerate(targets):
            handle = args.handle[index] if args.handle else None
            common = {"connector_id": args.connector, "operation": args.operation,
                      "any_status": args.any_status, "drained": args.drained, "handle": handle,
                      "staging_roots": args.staging}
            if args.dest:
                results.append(persist(Path(target), **common))
            else:
                slot = f"[{target}]" if str(target).lstrip("-").isdigit() else str(target)
                results.append(persist(into=args.into, at=[*args.at, slot], **common))
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        message = f"{type(error).__name__}: {error}"
        first = args.dest[0] if args.dest else args.into
        if first is not None:
            message = explain_missing(message, Path(first).resolve(), args.operation)
        print(json.dumps({"status": "failed", "placed": len(results), "error": message}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": "ok", "placed": len(results), "results": results}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
