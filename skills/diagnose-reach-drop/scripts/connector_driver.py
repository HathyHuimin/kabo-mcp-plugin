#!/usr/bin/env python3
"""The mechanical half of a skill driver: hand connector calls to the model, place, run scripts.

A skill's `drive.py` runs everything between two model judgments — which connector call follows, where
each response is placed, which deterministic script runs next — and prints only what the next judgment
reads. This module is the part of that which does not depend on the skill: fetch rounds recorded by what
they asked for, the hand-over of each round to the model as `call:` lines naming public MCP tools
(`--placed` picks the staged responses up), one more hand-over of a round whose calls briefly failed,
the run directory, the stage lock and the stage state file. `outbox_dir()` names no outbox in this
package, so scripts never fetch by themselves: every round is handed over.

Where a stage runs. The first stage of a run (`start`, or `fetch`) works in the run `static_host.py
init` reserved; running it again there is accepted only as the same request (the same arguments, with
`--placed` to place calls it handed over). Every later stage runs only from inside the run directory: from anywhere else — this skill's own directory above all, whose files a signature check
covers — it stops before writing anything and names the directory to `cd` into. One stage at a time
holds a run directory (`.drive.lock`), and the state file is replaced whole, never rewritten in place.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import persist_envelope as pe  # noqa: E402
from persist_envelope import USABLE_STATUSES, run_dir_of  # noqa: E402

SCRIPTS = Path(__file__).resolve().parent
SKILL_DIR = SCRIPTS.parent
REFERENCES = SKILL_DIR / "references"

REQUEST_SCHEMA = "kabo-connector-request.v1"
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
# The host takes at most four calls per request file and the platform at most four requests per
# `data_connector_batch_run`; both limits are enforced there, so they are mirrored here.
CALLS_PER_REQUEST = 4
BATCH_LIMIT = 4
# The platform's per-account ceiling on discovery requests in flight.
IN_FLIGHT_LIMIT = 8
# A rate-limited request is retried once, after at most this long.
RETRY_WAIT_MAX = 5.0
# A host with the outbox renames a request within one poll (250 ms); one that has none never does.
CLAIM_SECONDS = 4.0
RESPONSE_SECONDS = 900.0
HANDOVER_PREFIX = "call: "
STATE = ".drive.json"
LOCK = ".drive.lock"
# A lock whose owner process is gone is stale at once. A live owner holds it for as long as a stage
# can run (a fetch waits at most RESPONSE_SECONDS); past that, the pid is taken for another process
# that reused the number. Only a lock whose owner cannot be read (written this instant) is judged by
# its age alone.
UNREADABLE_LOCK_SECONDS = 60.0
LIVE_LOCK_SECONDS = 1800.0
# Where a stage copies a record it will never place: a rate-limited response its retry replaced.
# Inside the run directory, so the placement scripts count it as taken.
SUPERSEDED = ".superseded"

# The host's record of each request of the last fetch, paired with the file it staged (None when it
# could not say which). Read it as `connector_driver.LAST_RECORDS`, after `fetch_round`.
LAST_RECORDS: list[tuple[dict[str, Any], Path | None]] | None = None


class StageError(Exception):
    """A stage cannot go on; the message says what to fix."""


class NoOutbox(Exception):
    """This host does not fetch for scripts."""


class Busy(Exception):
    """Another stage holds this run directory."""


def say(text: str = "") -> None:
    print(text, flush=True)


def outbox_dir() -> Path | None:
    return None


def response_dir(requests: Path) -> Path:
    """Where the host answers: its own directory, which commands here can read but not change."""
    return requests.parent.parent / "connector-outbox-host" / requests.name


TURN_FILE = "turn.json"
TURN_SCHEMA = "kabo-connector-turn.v1"
TURN_TOKEN = re.compile(r"^[0-9a-f]{32}$")
# This process's turn token, per outbox, read once: a stage started in one turn keeps that turn's
# token, so a request it writes late, after that turn ended, never lands on the next turn.
_TURN_TOKENS: dict[str, str | None] = {}


def turn_token() -> str | None:
    """The host's token for the turn this stage runs in (`connector-outbox-host/<thread>/turn.json`).

    A host that issues none (an older one) has no file: requests then go without a token, as before.
    """
    directory = outbox_dir()
    if directory is None:
        return None
    key = str(directory)
    if key not in _TURN_TOKENS:
        token = None
        try:
            value = json.loads((response_dir(directory) / TURN_FILE).read_text(encoding="utf-8"))
            candidate = value.get("turn_token") if isinstance(value, dict) else None
            if (value.get("schema") == TURN_SCHEMA and value.get("thread_id") in (None, directory.name)
                    and isinstance(candidate, str) and TURN_TOKEN.match(candidate)):
                token = candidate
        except (OSError, ValueError, AttributeError):
            token = None
        _TURN_TOKENS[key] = token
    return _TURN_TOKENS[key]


def run_call(connector: str, operation: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"tool": "data_connector_run",
            "arguments": {"connector_id": connector, "operation": operation, "params": params}}


def batch_calls(requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """`data_connector_batch_run` calls of at most four requests each, in order."""
    calls = []
    for start in range(0, len(requests), BATCH_LIMIT):
        chunk = requests[start:start + BATCH_LIMIT]
        if len(chunk) == 1:
            only = chunk[0]
            calls.append(run_call(only["connector_id"], only["operation"], only["params"]))
        else:
            calls.append({"tool": "data_connector_batch_run", "arguments": {"requests": chunk}})
    return calls


def weight(call: dict[str, Any]) -> int:
    """Platform requests a call keeps in flight: a batch its requests, a collector the whole allowance."""
    if call.get("tool") == "data_connector_batch_run":
        return len(call["arguments"].get("requests") or [])
    return 1 if call.get("tool") == "data_connector_run" else IN_FLIGHT_LIMIT


def fetch(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Have the host make these calls in waves the account can carry; one result per call, in order.

    The platform keeps at most eight discovery requests in flight per account; larger inputs are split
    into waves to avoid avoidable rate limits.
    """
    results: list[dict[str, Any]] = []
    wave: list[dict[str, Any]] = []
    for call in calls:
        if wave and sum(map(weight, wave)) + weight(call) > IN_FLIGHT_LIMIT:
            results += send(wave)
            wave = []
        wave.append(call)
    if wave:
        results += send(wave)
    return results


def send(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One wave: all of these calls at once; returns one result per call, in order."""
    directory = outbox_dir()
    if directory is None:
        raise NoOutbox()
    directory.mkdir(parents=True, exist_ok=True)
    token = turn_token()
    ids = []
    for start in range(0, len(calls), CALLS_PER_REQUEST):
        request_id = uuid.uuid4().hex[:16]
        request: dict[str, Any] = {"schema": REQUEST_SCHEMA, "calls": calls[start:start + CALLS_PER_REQUEST]}
        if token:
            request["turn_token"] = token
        body = json.dumps(request)
        temp = directory / f".{request_id}.tmp"
        temp.write_text(body, encoding="utf-8")
        temp.rename(directory / f"{request_id}.request.json")
        ids.append(request_id)
    deadline = time.monotonic() + CLAIM_SECONDS
    while time.monotonic() < deadline and all((directory / f"{i}.request.json").exists() for i in ids):
        time.sleep(0.1)
    if all((directory / f"{i}.request.json").exists() for i in ids):
        # Nothing was taken. Withdraw by renaming, which cannot race the host: a request it takes in
        # the same instant is no longer there to rename, and is then waited for like any other.
        taken = []
        for request_id in ids:
            try:
                (directory / f"{request_id}.request.json").rename(directory / f".{request_id}.withdrawn")
                (directory / f".{request_id}.withdrawn").unlink(missing_ok=True)
            except FileNotFoundError:
                taken.append(request_id)
        if not taken:
            raise NoOutbox()
        raise StageError("the host accepted only some connector request files; wait for those calls "
                         "to finish, then run this stage again with --placed")
    # A host that took one request takes the rest as its per-conversation queue frees up.
    responses: list[dict[str, Any]] = []
    deadline = time.monotonic() + RESPONSE_SECONDS
    answers = response_dir(directory)
    for request_id in ids:
        path = answers / f"{request_id}.response.json"
        while not path.exists():
            if time.monotonic() > deadline:
                raise StageError("the host accepted the connector calls but never answered; run this stage "
                                 "again with --placed once the calls have finished")
            time.sleep(0.2)
        responses.append(json.loads(path.read_text(encoding="utf-8")))
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass  # read-only here by design; the host clears answered responses itself
    refused = [r for r in responses if r.get("status") != "done"]
    if refused and len(refused) == len(responses):
        # None of these calls ran: a host whose allowlist predates them. The model makes them
        # instead, exactly as on a host without the outbox.
        raise NoOutbox(f"it refused these calls: {refused[0].get('reason')}")
    if refused:
        raise StageError(f"the host refused some of the connector calls: {refused[0].get('reason')}")
    return [result for response in responses for result in response.get("results") or []]


def requests_of(call: dict[str, Any]) -> list[dict[str, Any]]:
    arguments = call.get("arguments") or {}
    if call.get("tool") == "data_connector_batch_run":
        return list(arguments.get("requests") or [])
    if call.get("tool") == "data_connector_run":
        return [arguments]
    return []


def records_of(calls: list[dict[str, Any]], results: list[dict[str, Any]]) -> list[tuple[dict[str, Any], Path | None]]:
    """Each connector request with the host's own record of its response, where the host says which.

    The host stages a call's Envelopes in request order and reports their file names, so a request
    and its record pair up exactly when the counts agree. When they do not (a record too large to
    stage), that call's requests get None and are placed the old way, by content.
    """
    records: list[tuple[dict[str, Any], Path | None]] = []
    for call, result in zip(calls, results):
        requests = requests_of(call)
        files = [Path(batch.get("directory") or "") / name
                 for batch in result.get("staged") or [] for name in batch.get("names") or []]
        paired = len(files) == len(requests) and all(f.is_file() for f in files)
        records += [(request, files[i] if paired else None) for i, request in enumerate(requests)]
    return records


# Failures worth one retry before a stage reads them as an answer, with the default wait in seconds:
# a rate limit, and a transient upstream outage (HTTP 503 seen on Instagram Feed reads, 2026-10-15,
# that ended two whole account reviews although the same read succeeded minutes later).
RETRIED_ONCE = {"rate_limited": 1.0, "service_unavailable": 3.0}


# A call the host could not complete at all: it answered `error` and staged no response, so the
# request or its answer was lost between this machine and the platform (a dropped connection, a
# gateway error, a platform reply with no result). The platform may well have done the work; nothing
# of it reached this run. Read-only calls are sent again once, alone, after this many seconds.
LOST_RETRY_WAIT = 2.5
# The calls a stage may send twice: they only read. Anything else (a job submission, a write) is never
# repeated here, whatever came back.
READ_ONLY_TOOLS = frozenset({"data_connector_run", "data_connector_batch_run",
                             "collect_instagram_trend_candidates", "collect_youtube_trend_candidates",
                             "enrich_instagram_shortlist", "enrich_youtube_shortlist"})
# Fixed wording for a round that stopped because calls were lost on the way even after that retry.
# The host model relays it to the user in the user's own language.
LOST_NOTE = ("Tell the user: the network or the data service was briefly interrupted. This is not a "
             "problem with their request or anything they entered, and not something any social platform "
             "did; do not guess another cause. Ask them to try again in a few minutes.")


def staged_count(result: dict[str, Any]) -> int:
    return sum(len(batch.get("names") or []) for batch in result.get("staged") or [] if isinstance(batch, dict))


def lost(call: dict[str, Any], result: dict[str, Any]) -> bool:
    """Whether the host lost this read-only call on the way: an error with no staged response at all."""
    return (call.get("tool") in READ_ONLY_TOOLS and result.get("status") == "error"
            and staged_count(result) == 0 and not result.get("receipt"))


def retry_lost(calls: list[dict[str, Any]], results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Send the calls the host lost on the way once more, alone; a recovered call's result replaces its error."""
    again = [i for i, (call, result) in enumerate(zip(calls, results)) if lost(call, result)]
    if not again:
        return results
    time.sleep(LOST_RETRY_WAIT)
    try:
        retried = fetch([calls[i] for i in again])
    except NoOutbox:
        retried = []  # the host took nothing this time: those calls stay lost
    results = list(results)
    for i, result in zip(again, retried):
        if result.get("status") != "refused":  # a refused retry was never made; keep the original error
            results[i] = result
    still = [i for i in again if lost(calls[i], results[i])]
    say(f"[drive] {len(again)} call(s) came back with nothing (lost on the way); retried once, "
        f"{len(again) - len(still)} recovered")
    if still:
        remember_lost([str(request.get("operation") or "") for i in still for request in requests_of(calls[i])]
                      + [str(calls[i].get("tool")) for i in still])
    return results


def remember_lost(operations: list[str]) -> None:
    """Record in this run's state which operations (and tools) were lost on the way, for a later stop to explain."""
    if run_dir_of(Path.cwd() / STATE) is None:
        return
    known = [str(op) for op in state().get("lost_operations") or []]
    save_state(lost_operations=sorted({*known, *(op for op in operations if op)}))


def lost_in_transit(operations: list[str] | None = None) -> bool:
    """Whether this run lost calls on the way that the retry did not recover (of these operations, if named)."""
    try:
        known = set(state().get("lost_operations") or [])
    except StageError:
        return False
    return bool(known if operations is None else known & set(operations))


def rate_limited(path: Path | None) -> float | None:
    """Seconds to wait before retrying this record's request, or None when it is not retried."""
    if path is None:
        return None
    try:
        text = path.read_text(encoding="utf-8")
        envelope = pe.unwrap_envelope(text) or json.loads(text)
    except (OSError, ValueError):
        return None
    if not isinstance(envelope, dict):
        return None
    if envelope.get("status") != "failed" or envelope.get("error_code") not in RETRIED_ONCE:
        return None
    return float(envelope.get("retry_after_seconds") or RETRIED_ONCE[envelope["error_code"]])


def supersede(path: Path | None) -> None:
    """Keep a replaced record in the run directory, so placing by content never takes it.

    The staging area still holds a rate-limited response after its retry replaced it. A placement
    that reads the staging area in fetch order (`persist_envelope.py`, `persist_many.py`) takes the
    earliest response not yet in the run directory — that failed one, unless a copy of it is here.
    """
    if path is None or run_dir_of(Path.cwd() / STATE) is None:
        return
    try:
        text = path.read_text(encoding="utf-8")
        envelope = pe.unwrap_envelope(text) or json.loads(text)
    except (OSError, ValueError):
        return
    if not isinstance(envelope, dict):
        return
    directory = Path(SUPERSEDED)
    directory.mkdir(exist_ok=True)
    number = len(list(directory.glob("*.json"))) + 1
    target = directory / f"{number:02d}-{envelope.get('operation') or 'response'}.json"
    target.write_text(json.dumps(envelope, ensure_ascii=False) + "\n", encoding="utf-8")
    target.chmod(0o600)


def hand_over(calls, why=""):
    from static_host import public_calls
    say("Make each call below as its own individual call to the named public MCP tool (never combined into one "
        "batch call), save each complete result as its own file in the run's host-results/ folder and run static_host.py fetch on it (static_host.py stage when fetch "
        "reports no ready download), then rerun this command with --placed:")
    for call in calls:
        for public in public_calls(call):
            say(HANDOVER_PREFIX + json.dumps(public, ensure_ascii=False))
    raise SystemExit(0)


def fetch_or_hand_over(calls: list[dict[str, Any]], placed: bool,
                       hand_over_calls: Callable[[], None] | None = None, optional: bool = False) -> None:
    """Fetch through the outbox; without one, print the calls for the model and stop the stage.

    `hand_over_calls` prints the hand-over its own way instead (it ends the stage itself).

    `optional` says this round's subjects can go without an answer: its caller places what came back
    and names the subjects that got nothing. Only a round whose placement does that passes it.

    If local persistence fails after a fetch, retry the write, never switch Connector operations or
    refetch the account: the stage is run again with `--placed`, which skips this fetch.
    """
    global LAST_RECORDS
    LAST_RECORDS = None
    if placed:
        return
    started = time.monotonic()
    try:
        results = fetch(calls)
    except NoOutbox as why:
        if hand_over_calls is not None:
            hand_over_calls()
        hand_over(calls, str(why))
    # Before anything reads a lost call as «no profile» or «no more history», and before a stage
    # spends a reserve in its place.
    results = retry_lost(calls, results)
    failed = [r for r in results if r.get("status") == "refused"]
    if failed and not optional:
        raise StageError(f"connector call refused: {failed[0].get('message')}")
    if failed:
        # Refusing here is a thinner answer, not a broken run, so the round goes on to place what
        # did come back. Raising instead threw away the responses that had already returned: a turn
        # whose retrieval budget ran out on the first batch of a round delivered nothing at all,
        # though the stage was written to carry on with the subjects it had.
        say(f"[drive] the host refused {len(failed)} of {len(results)} call(s) in this round "
            f"({str(failed[0].get('message') or '')[:200]}); placing what did come back")
    records = records_of(calls, results)
    # A rate-limited or briefly unavailable request is retried once, alone, before anything reads it as «no profile» or
    # «no more history»; its retry's record replaces the failed one.
    again = [i for i, (_, path) in enumerate(records) if rate_limited(path) is not None]
    if again:
        time.sleep(min(RETRY_WAIT_MAX, max(rate_limited(records[i][1]) or 1 for i in again)))
        retry_calls = [run_call(records[i][0]["connector_id"], records[i][0]["operation"], records[i][0].get("params") or {})
                       for i in again]
        retried = records_of(retry_calls, fetch(retry_calls))
        for i, (_, path) in zip(again, retried):
            if path is not None:
                supersede(records[i][1])
                records[i] = (records[i][0], path)
        say(f"[drive] retried {len(again)} rate-limited or unavailable request(s); "
            f"{sum(rate_limited(records[i][1]) is None for i in again)} recovered")
    LAST_RECORDS = records
    kept = sum(staged_count(r) for r in results)
    if results and kept == 0 and all(r.get("status") == "error" for r in results):
        # Nothing came back at all, even after the retry: the data service or the network failed, not
        # an artifact of this run. Placing would only report a missing response, which reads like a
        # call never sent.
        raise StageError(f"every connector call failed on the way ({str(results[0].get('message') or '')[:200]}), "
                         "and the driver already sent them once more; nothing was fetched and nothing is wrong "
                         "with this run's files. Stop here without trying other tools. " + LOST_NOTE)
    for index, (call, result) in enumerate(zip(calls, results)):
        if result.get("status") == "error":
            staged = staged_count(result)
            after = " (nothing came back, after one retry)" if lost(call, result) else ""
            say(f"[drive] call {index + 1} ({result.get('tool')}) reported an error"
                f"{f' ({staged} response(s) kept)' if staged else after}: {str(result.get('message') or '')[:300]}")
    say(f"[drive] fetched {len(calls)} call(s) in {time.monotonic() - started:.1f}s")


def place_record(operation: str, dest: str | Path, by_content: Callable[[], Any], nth: int = 0,
                 any_status: bool = True) -> None:
    """Place the response to this round's `nth` request of `operation` at `dest`.

    From the host's own record of that request when the fetch named one — the final attempt, after a
    rate-limit retry — byte for byte, which is what `persist_envelope.py --dest` copies. Otherwise (the
    model made the call, `--placed`) `by_content` places it, by what the staging area holds. Without
    `any_status` a record that is not a completed response goes to `by_content` too, which refuses it
    the way it always did.
    """
    records = [path for request, path in (LAST_RECORDS or []) if request.get("operation") == operation]
    record = records[nth] if nth < len(records) else None
    if record is None or not record.is_file():
        by_content()
        # By content, a rate-limited response the model retried is followed by its retry: set the
        # limited one aside as superseded and take the next, as the outbox path does.
        for _ in range(3):
            if rate_limited(Path(dest)) is None:
                return
            kept = Path(dest).read_bytes()
            supersede(Path(dest))
            try:
                by_content()
            except StageError:
                Path(dest).write_bytes(kept)  # no retry was made: the limited response is the answer
                return
        return
    if not any_status:
        try:
            envelope = pe.unwrap_envelope(record.read_text(encoding="utf-8")) or {}
        except (OSError, UnicodeDecodeError):
            envelope = {}
        if envelope.get("status") not in USABLE_STATUSES:
            by_content()
            return
    target = Path(dest)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(record.read_bytes())
    target.chmod(0o600)


def round_key(calls: list[dict[str, Any]]) -> str:
    return hashlib.sha256(json.dumps(calls, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def typed_records_since(roots: list[Path], since: datetime) -> int:
    """Typed tool results (a collector's, an enrichment's) the host staged since then."""
    count = 0
    for root in roots:
        for base in [root]:
            try:
                metas = sorted(base.rglob("*.meta")) if base.is_dir() else []
            except OSError:
                continue
            for meta_path in metas:
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if not isinstance(meta, dict) or "connector_id" in meta or not meta.get("schema_version"):
                    continue
                recorded = pe.parse_time(meta.get("staged_at")) or pe.file_time(meta_path)
                if recorded is not None and recorded >= since and meta_path.with_suffix(".json").is_file():
                    count += 1
    return count


def made_since(calls: list[dict[str, Any]], handed_at: str) -> bool:
    """Whether the host recorded a response for every one of these calls since they were handed over."""
    since = pe.parse_time(handed_at)
    if since is None:
        return False
    roots = pe.default_staging_roots()
    wanted: Counter[tuple[str, str]] = Counter()
    typed = 0
    for call in calls:
        requests = requests_of(call)
        if not requests:
            typed += 1
        for request in requests:
            wanted[(str(request.get("connector_id")), str(request.get("operation")))] += 1
    problems: list[str] = []
    for (connector, operation), count in wanted.items():
        if len(pe.staged_candidates(roots, connector, operation, since, problems)) < count:
            return False
    return not typed or typed_records_since(roots, since) >= typed


def round_responses(calls: list[dict[str, Any]], handed_at: str) -> list[dict[str, Any]] | None:
    """The responses the model's calls of this round left since the hand-over, one per request.

    From the host's staging and from what a runner drained into `snapshot/`. None unless every
    request of the round has exactly one response: a missing one, or one the model already sent again
    on its own, leaves the round to placement as it always was.
    """
    since = pe.parse_time(handed_at)
    if since is None:
        return None
    wanted: Counter[tuple[str, str]] = Counter()
    for call in calls:
        requests = requests_of(call)
        if not requests:
            return None  # a typed tool result carries no connector status to judge
        for request in requests:
            wanted[(str(request.get("connector_id")), str(request.get("operation")))] += 1
    roots = pe.default_staging_roots()
    problems: list[str] = []
    found: list[dict[str, Any]] = []
    for (connector, operation), count in wanted.items():
        candidates = pe.staged_candidates(roots, connector, operation, since, problems)
        candidates += [c for c in pe.drained_candidates(Path("snapshot"), connector, operation, problems)
                       if c.recorded_at is not None and c.recorded_at >= since]
        if len(candidates) != count:
            return None
        found += [c.envelope for c in candidates]
    return found


def retry_handed_failures(calls: list[dict[str, Any]], key: str, handed: dict[str, Any]) -> None:
    """Hand a round over once more when the data service briefly failed on calls the model made.

    The driver retries a rate limit or a transient outage (HTTP 5xx, read as `service_unavailable`)
    on its own fetches; a call the model made itself after a hand-over had no retry, and a single
    5xx on one Feed page ended a whole account review (2026-10-14). Placement takes responses by
    content in fetch order, so a retry of one call would slide into the next call's slot: the whole
    round is set aside (copied into the run, where placement counts it as taken) and handed over
    again, once. If the second answer fails too, it is placed as the answer, as before.
    """
    if key in (state().get("retried_rounds") or []):
        return
    responses = round_responses(calls, str(handed.get(key) or ""))
    failed = [e for e in responses or [] if e.get("status") == "failed" and e.get("error_code") in RETRIED_ONCE]
    if not failed:
        return
    directory = Path(SUPERSEDED)
    directory.mkdir(exist_ok=True)
    for envelope in responses or []:
        number = len(list(directory.glob("*.json"))) + 1
        target = directory / f"{number:02d}-{envelope.get('operation') or 'response'}.json"
        target.write_text(json.dumps(envelope, ensure_ascii=False) + "\n", encoding="utf-8")
        target.chmod(0o600)
    time.sleep(min(RETRY_WAIT_MAX, max(float(e.get("retry_after_seconds") or RETRIED_ONCE[e["error_code"]])
                                       for e in failed)))
    save_state(retried_rounds=[*(state().get("retried_rounds") or []), key], handed={**handed, key: now_iso()})
    codes = ", ".join(sorted({str(e["error_code"]) for e in failed}))
    hand_over(calls, f"the data service briefly failed ({codes}) on {len(failed)} of these calls; "
                     "make the whole round once more")


def fetch_round(calls: list[dict[str, Any]], args: argparse.Namespace, place_responses,
                hand_over_calls: Callable[[], None] | None = None, optional: bool = False) -> bool:
    """One fetch round, done at most once per run: fetch (or hand over), then place the responses.

    Each round is recorded by what it asked for, so running a stage again — with `--placed` after a
    hand-over, or after fixing an artifact the stage rejected — skips the rounds already fetched and
    placed, and `--placed` applies to the first round still open. A round with different calls (a new
    identity query, a further page cursor) is a new round. Returns False when it had been done.

    A round handed over is recorded too. Run again without `--placed`, it is placed all the same when
    the host recorded a response for each of its calls since the hand-over, and handed over again only
    when it did not: making the same calls twice left a second response behind, which a later round of
    the same operation then took as its own. `hand_over_calls`, when given, prints the hand-over in
    place of the `call:` lines (and ends the stage). `optional` is passed on to the fetch: a round
    whose placement carries on without some of its answers says so here.
    """
    key = round_key(calls)
    current = state()
    done = list(current.get("rounds") or [])
    if key in done:
        return False
    handed = dict(current.get("handed") or {})
    if not args.placed and key in handed:
        if made_since(calls, str(handed[key])):
            say("[drive] the calls handed over earlier were made; placing their responses (as --placed would)")
            args.placed = True
        else:
            say("[drive] no response to the calls handed over earlier was recorded; handing them over again")
    try:
        if hand_over_calls is None:
            fetch_or_hand_over(calls, args.placed, optional=optional)
        else:
            fetch_or_hand_over(calls, args.placed, hand_over_calls=hand_over_calls, optional=optional)
    except SystemExit as stop:
        if stop.code in (0, None):
            save_state(handed={**handed, key: now_iso()})
        raise
    if args.placed and hand_over_calls is None and key in handed:
        retry_handed_failures(calls, key, handed)
    args.placed = False
    place_responses()
    handed.pop(key, None)
    save_state(rounds=[*done, key], handed=handed)
    return True


def script(name: str, *args: str, show: bool = False) -> str:
    """Run one of this skill's scripts in the run directory; stop the stage with its output if it fails."""
    completed = subprocess.run([sys.executable, str(SCRIPTS / name), *args], cwd=Path.cwd(),
                               capture_output=True, text=True)
    output = (completed.stdout or "") + (completed.stderr or "")
    if completed.returncode != 0:
        raise StageError(f"{name} failed:\n{output.strip()}")
    if show:
        say(output.rstrip())
    return completed.stdout or ""


def show_schema(*names: str) -> None:
    for name in names:
        say((REFERENCES / name).read_text(encoding="utf-8").rstrip())


def load(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def state() -> dict[str, Any]:
    """This run's state; none yet when the file is absent. An unreadable one stops the stage: read as
    empty, it made every round fetch again and every placement start over."""
    try:
        text = Path(STATE).read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise StageError(f"{Path.cwd() / STATE} is not readable JSON ({error}); this run's progress cannot be "
                         "trusted. Start the request again in a new run from static_host.py init")
    if not isinstance(value, dict):
        raise StageError(f"{Path.cwd() / STATE} does not hold this run's state; start the request again in a "
                         "new run from static_host.py init")
    return value


def save_state(**updates: Any) -> None:
    """Update the state file by replacing it whole: a stage killed mid-write leaves the old one."""
    value = state()
    value.update(updates)
    temp = Path(f"{STATE}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, STATE)


def epoch(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()


def usable(path: Path) -> bool:
    try:
        return load(path).get("status") in USABLE_STATUSES
    except (OSError, json.JSONDecodeError, AttributeError):
        return False


def slug(text: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return value[:48].strip("-")


def complete_artifact(path: str | Path, fields: dict[str, Any], *, intent_ids: bool = False,
                      intent_defaults: dict[str, str] | None = None) -> dict[str, Any]:
    """Fill missing mechanical fields while preserving every field already written.

    `intent_defaults` copies one intent field into another when that one is missing.
    """
    target = Path(path)
    if not target.is_file():
        return {}  # not written: the step that reads it says so, as before
    document = load(target)
    if not isinstance(document, dict):
        raise StageError(f"{target} must hold one JSON object")
    before = json.dumps(document, sort_keys=True)
    for key, value in fields.items():
        if key not in document and value is not None:
            document[key] = value
    intents = document.get("intents")
    if isinstance(intents, list) and (intent_ids or intent_defaults):
        taken = {str(i.get("intent_id")) for i in intents if isinstance(i, dict) and i.get("intent_id")}
        for number, intent in enumerate(intents, start=1):
            if not isinstance(intent, dict):
                continue
            for field, source in (intent_defaults or {}).items():
                if field not in intent and isinstance(intent.get(source), str):
                    intent[field] = intent[source]
            if intent_ids and not intent.get("intent_id"):
                base = slug(str(intent.get("search_query") or "")) or f"intent-{number}"
                candidate, suffix = base, 2
                while candidate in taken:
                    candidate, suffix = f"{base}-{suffix}", suffix + 1
                intent["intent_id"] = candidate
                taken.add(candidate)
    if json.dumps(document, sort_keys=True) != before:
        target.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return document


def last_days(days: int) -> dict[str, str]:
    """The window of the last `days` days, ending now (UTC, whole seconds)."""
    until = datetime.now(timezone.utc).replace(microsecond=0)
    since = datetime.fromtimestamp(until.timestamp() - days * 86400, timezone.utc)
    return {"since": since.isoformat().replace("+00:00", "Z"), "until": until.isoformat().replace("+00:00", "Z")}


def run_id() -> str:
    """This run's id: the run directory's own name."""
    run = run_dir_of(Path.cwd() / STATE)
    return run.name if run is not None else Path.cwd().name


def skill_name() -> str:
    # The Skill's name is its directory name (the SKILL.md frontmatter requires them to match).
    return SKILL_DIR.name


def in_skill_directory(path: Path) -> bool:
    """Whether `path` is this skill's own directory or inside it (where a signature check covers every file)."""
    here = path.resolve()
    return here == SKILL_DIR or SKILL_DIR in here.parents


def latest_run_directory() -> Path | None:
    from static_host import run_dir
    return run_dir()


def driver_command(run: Path | str, stage: str = "<stage>") -> str:
    """The command that runs `stage` of this skill from `run`: both paths absolute, so it works from any
    directory the shell happens to be in (`scripts/drive.py` is relative to the skill, not the run)."""
    return f"cd '{run}' && python3 '{SCRIPTS / 'drive.py'}' {stage}"


def go_to_run_directory(problem: str, stage: str = "<stage>") -> StageError:
    latest = latest_run_directory()
    where = (f"Run it from the run directory the first stage printed — the most recent one of this skill is "
             f"{latest}: {driver_command(latest, stage)} (with the rest of the same command)" if latest else
             f"Run it from the run directory the first stage printed: {driver_command('<that directory>', stage)} "
             "(with the rest of the same command)")
    return StageError(f"{problem}; nothing was written. {where}")


LOCKED: Path | None = None
# The artifacts the model writes with a heredoc before a stage (set by run_stage).
MODEL_FILES: tuple[str, ...] = ()


def lock_held(text: str, age: float) -> bool:
    """Whether the lock's owner still runs: its pid is alive and it started within LIVE_LOCK_SECONDS.

    A lock whose owner cannot be read (half-written this instant) falls back to its file's age.
    """
    try:
        owner = json.loads(text)
        pid = int(owner.get("pid"))
    except (ValueError, TypeError, AttributeError):
        return age < UNREADABLE_LOCK_SECONDS
    if pid == os.getpid():
        return False  # this process: a stage run again within it
    started = pe.parse_time(owner.get("started_at"))
    if started is not None and (datetime.now(timezone.utc) - started).total_seconds() > LIVE_LOCK_SECONDS:
        return False  # longer than any stage runs: the pid now belongs to some other process
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def acquire_lock(stage: str) -> None:
    """Hold the run directory for this stage: two stages at once would fetch and place the same calls twice."""
    global LOCKED
    lock = Path.cwd() / LOCK
    if LOCKED == lock:
        return
    release_lock()
    body = json.dumps({"pid": os.getpid(), "stage": stage, "started_at": now_iso()})
    for _ in range(3):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            try:
                text = lock.read_text(encoding="utf-8")
                age = time.time() - lock.stat().st_mtime
            except OSError:
                continue
            if lock_held(text, age):
                try:
                    other = json.loads(text).get("stage") or "another stage"
                except (ValueError, AttributeError):
                    other = "another stage"
                raise Busy(f"drive.py {other} is still running in {Path.cwd()} (started {age:.0f}s ago). Wait for that "
                           "command to finish and read its output; do not start a stage here while it runs, or both "
                           "would fetch and place the same calls.")
            lock.unlink(missing_ok=True)  # its owner is gone
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(body)
        LOCKED = lock
        return
    raise Busy(f"could not take {lock}; run this stage again")


def release_lock() -> None:
    global LOCKED
    if LOCKED is not None:
        try:
            if json.loads(LOCKED.read_text(encoding="utf-8")).get("pid") == os.getpid():
                LOCKED.unlink()
        except (OSError, ValueError, AttributeError):
            pass
        LOCKED = None


def request_key(args: Any, extra: str = "") -> str:
    """What a first stage was asked, as a digest: its arguments (never --placed) and `extra` (a question
    read from standard input). The same key means the same request, run again."""
    fields = {key: value for key, value in sorted(vars(args).items())
              if not key.startswith("_") and key != "placed"
              and isinstance(value, (str, int, float, bool, type(None), list, tuple))}
    return hashlib.sha256(json.dumps([fields, extra], sort_keys=True, default=str).encode()).hexdigest()[:16]


def claim(directory: Path, stage: str, key: str | None = None) -> None:
    os.chdir(directory)
    acquire_lock(stage)
    current = state()
    updates: dict[str, Any] = {}
    if not current.get("skill"):
        updates["skill"] = skill_name()
    if key is not None and not current.get("first_stage"):
        updates["first_stage"] = {"stage": stage, "key": key, "done": False}
    if updates:
        save_state(**updates)


def first_stage_done(stage: str) -> None:
    """Record that this run's first stage finished: running it again is then a new request."""
    first = dict(state().get("first_stage") or {})
    if first.get("stage") == stage and not first.get("done"):
        save_state(first_stage={**first, "done": True})


def unfinished(here: Path, stage: str, key: str | None) -> bool:
    """This run directory holds this same request's first stage, stopped before it finished."""
    first = state().get("first_stage") or {}
    return key is not None and first.get("stage") == stage and first.get("key") == key and not first.get("done")


def reserve_run_directory(placed=False, stage="start", reuse=False, args=None, extra=""):
    from static_host import run_dir
    run = run_dir()
    key = request_key(args, extra) if args is not None else None
    os.chdir(run)
    previous = state().get("first_stage") or {}
    if previous and previous.get("key") != key and not reuse:
        raise StageError("this run holds a different request. A resume repeats the first command exactly, "
                         "including the question, and adds --placed; a different request needs a new run from "
                         "static_host.py init")
    claim(run, stage, key)


def strays(here: Path, names: tuple[str, ...]) -> list[Path]:
    """Return run artifacts written into the signed skill directory by mistake."""
    if not in_skill_directory(here):
        return []
    return [here / name for name in names if (here / name).is_file()]


def move_out(found: list[Path], run: Path | None) -> str:
    if not found:
        return ""
    target = f"'{run}/'" if run else "'<the run directory>/'"
    return (f" {', '.join(path.name for path in found)} {'was' if len(found) == 1 else 'were'} written into this "
            f"skill's own directory, which fails its signature check on the next run: move "
            f"{'it' if len(found) == 1 else 'them'} out first with mv {' '.join(repr(str(p)) for p in found)} {target}")


def guard_run_directory(stage: str, model_files: tuple[str, ...] = ()) -> None:
    """A later stage runs only from inside the run directory, and writes nothing anywhere else."""
    here = Path.cwd()
    if in_skill_directory(here):
        note = move_out(strays(here, model_files), latest_run_directory())
        raise go_to_run_directory(f"this is the skill's own directory ({here}), not the run directory.{note}", stage)
    run = run_dir_of(here / STATE)
    if run is None or run != here.resolve():
        raise go_to_run_directory(f"{here} is not the run directory", stage)
    acquire_lock(stage)


def run_stage(stages: dict[str, Callable[[argparse.Namespace], None]], args: argparse.Namespace,
              first: tuple[str, ...] = ("start",), model_files: tuple[str, ...] = ()) -> int:
    """Run one stage; report failures with the recovery they allow in this same run.

    `first` names the stages that reserve the run directory themselves; every other stage must run
    from inside it. `model_files` are the artifacts the model writes with a heredoc before a stage.
    """
    global MODEL_FILES
    MODEL_FILES = model_files
    turn_token()  # read once, as the stage starts
    try:
        if args.stage not in first:
            guard_run_directory(args.stage, model_files)
        stages[args.stage](args)
        if args.stage in first and LOCKED is not None:
            first_stage_done(args.stage)
    except Busy as busy:
        say(f"drive.py {args.stage} did not start: {busy}")
        return 3
    except (StageError, OSError, ValueError, KeyError, TypeError) as error:
        # json.JSONDecodeError is a ValueError. Anything here is reported the same way, never as a
        # traceback. Recovery advice never permits a replacement run.
        detail = str(error) if isinstance(error, StageError) else f"{type(error).__name__}: {error}"
        say(f"drive.py {args.stage} stopped: {detail}")
        say("Preserve this run's artifacts. If the message above names a correction, make it and run the "
            "same command again in this run; fetched rounds remain available. "
            "For a handed-over round whose calls you made and staged, repeat the first command exactly "
            "and add --placed. Otherwise this run has ended: report the failure.")
        return 2
    finally:
        release_lock()
    return 0
