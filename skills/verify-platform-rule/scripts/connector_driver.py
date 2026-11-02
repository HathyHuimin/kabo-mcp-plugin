#!/usr/bin/env python3
"""The mechanical half of a skill driver: hand calls over, place responses, run scripts.

A skill's `drive.py` runs everything between two model judgments — which connector call follows, where
each response is placed, which deterministic script runs next — and prints only what the next judgment
reads. This module is the part of that which does not depend on the skill: fetch rounds recorded by
what they asked for, the hand-over of each round to the model as named public MCP tool calls
(`--placed` picks the staged responses up), one more hand-over of a round the data service briefly
failed, the run directory, the stage lock and the stage state file.

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

BATCH_LIMIT = 4
# A rate-limited request is retried once, after at most this long.
RETRY_WAIT_MAX = 5.0
HANDOVER_PREFIX = "call: "
STATE = ".drive.json"
LOCK = ".drive.lock"
# A lock whose owner process is gone is stale at once. A live owner holds it for as long as a stage
# can run; past that, the pid is taken for another process
# that reused the number. Only a lock whose owner cannot be read (written this instant) is judged by
# its age alone.
UNREADABLE_LOCK_SECONDS = 60.0
LIVE_LOCK_SECONDS = 1800.0
# Where a stage copies a record it will never place: a rate-limited response its retry replaced.
# Inside the run directory, so the placement scripts count it as taken.
SUPERSEDED = ".superseded"

# Pairs each request of the last fetch with the file holding its response. The stages never fetch
# themselves (the model makes every call), so there is no such pairing and this stays None: every
# response is placed by content, from this run's staged copies, in call order.
LAST_RECORDS: list[tuple[dict[str, Any], Path | None]] | None = None


class StageError(Exception):
    """A stage cannot go on; the message says what to fix."""


class Busy(Exception):
    """Another stage holds this run directory."""


def say(text: str = "") -> None:
    print(text, flush=True)


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


def requests_of(call: dict[str, Any]) -> list[dict[str, Any]]:
    arguments = call.get("arguments") or {}
    if call.get("tool") == "data_connector_batch_run":
        return list(arguments.get("requests") or [])
    if call.get("tool") == "data_connector_run":
        return [arguments]
    return []


# Failures worth one retry before a stage reads them as an answer, with the default wait in seconds:
# a rate limit, and a transient upstream outage (HTTP 503 seen on Instagram Feed reads, 2026-10-15,
# that ended two whole account reviews although the same read succeeded minutes later).
RETRIED_ONCE = {"rate_limited": 1.0, "service_unavailable": 3.0}


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


def resume_command() -> str:
    """This same stage, with the same arguments, run again with `--placed` from this run directory.

    `--without-include` answered one hand-over and is remembered by the run. `--question-file -` stays:
    the run checks a first stage's arguments, and a `--placed` run keeps the stored ask without
    waiting on standard input.
    """
    stage = [arg for arg in sys.argv[1:] if arg not in ("--placed", "--without-include")]
    return (f"cd '{Path.cwd()}' && KABO_STATIC_RUN_DIR='{Path.cwd()}' python3 '{SCRIPTS / 'drive.py'}' "
            + " ".join(f"'{arg}'" if re.search(r"[^\w./=:-]", arg) else arg for arg in stage) + " --placed")


# 交接点名的结果文件（绝对路径，按调用顺序）与续跑已取走的文件（路径 -> 内容 sha256），记在本次运行的状态里。
AWAITING = "awaiting_results"
TAKEN = "taken_results"
# 每轮交接都打印，所以要短。实测里模型把组合结果里的 calls[] 逐个拿去 get_research_data_download、
# 用命令行参数传 base64 写文件（参数过长）、在错的目录里跑 stage——这里各说一次。
SAVE_RULE = ("Save each result as is, download link included, to the absolute path under it; write the file with "
             "your file-write tool or a single-quoted heredoc, never by passing the result as a command-line argument. "
             "A result that lists `calls` is saved whole, as one file: never call get_research_data_download and "
             "never download anything yourself.")


def await_results(tools: list[str]) -> list[Path]:
    """Name one file under this run's host-results/ per handed-over call, in call order, and remember them.

    The paths are absolute, so a save from another working directory still lands in this run; the
    resume takes exactly these files (`take_handed_results`).
    """
    results = Path.cwd() / "host-results"
    first = len(list(results.glob("*.json"))) + 1 if results.is_dir() else 1
    files = [results / f"{number:02d}-{tool}.json" for number, tool in enumerate(tools, first)]
    save_state(**{AWAITING: [{"tool": tool, "file": str(file)} for tool, file in zip(tools, files)]})
    return files


def say_resume() -> None:
    say("Then run this one command right away (it fetches and checks every saved result; links expire in 10 "
        "minutes): " + resume_command())


def hand_over(calls, why=""):
    # 每条调用下面打印要存的绝对路径，结尾只打印一条续跑命令：续跑自己 fetch（没有就绪下载时 stage）
    # 每个存好的结果，模型不再单独跑 static_host.py。
    from static_host import public_calls
    public = [mapped for call in calls for mapped in public_calls(call)]
    files = await_results([mapped["tool"] for mapped in public])
    say(f"Hand-over: make these {len(public)} call(s) now, each as its own call (never combined into one batch "
        "call), all in one response, with the arguments verbatim. " + SAVE_RULE)
    for mapped, file in zip(public, files):
        say(HANDOVER_PREFIX + json.dumps(mapped, ensure_ascii=False))
        say(f"  save: {file}")
    say_resume()
    raise SystemExit(0)


def take_saved_results(pairs: list[tuple[str, Path]]) -> None:
    """Fetch each saved result (stage it when it holds no ready download), in order, each file once.

    The same `static_host` fetch and stage the model used to run by hand. Every file is read before
    any is taken; a file already taken with the same bytes is skipped, so a resume run again does not
    download an expired link a second time.
    """
    import static_host

    read = []
    for tool, path in pairs:
        try:
            with path.open("rb") as stream:
                read.append((tool, path, stream.read(static_host.MAX_BYTES + 1)))
        except OSError as error:
            raise StageError(f"cannot read {path} ({error.strerror or error}); save the complete {tool} result "
                             "there, then run the same resume command again") from None
    taken = dict(state().get(TAKEN) or {})
    for tool, path, raw in read:
        digest = hashlib.sha256(raw).hexdigest()
        if taken.get(str(path)) == digest:
            continue
        try:
            result, how = static_host.fetch(tool, raw), "fetched"
            if result.get("fetched") == 0:
                result, how = static_host.stage(tool, raw), "staged"
        except (ValueError, OSError, KeyError, TypeError) as error:
            raise StageError(f"{path} does not hold a usable {tool} result ({error}). Save the complete {tool} "
                             "result there as returned (a result that lists `calls` whole, download link included), "
                             "then run the same resume command again") from None
        taken[str(path)] = digest
        save_state(**{TAKEN: taken})
        held = result.get("duplicate")
        say(f"[drive] {path.name}: {how} {tool}"
            + (" (already held)" if held is True or (held and not result.get("staged")) else ""))


def take_handed_results(calls: list[dict[str, Any]], handed_at: str, placed: bool, optional: bool = False) -> None:
    """Before a handed-over round is placed, take the results its hand-over named.

    Each named file that exists is fetched or staged (`take_saved_results`). Nothing is taken when the
    host already recorded a response for every call of the round some other way (a result fetched by
    hand, a link renewed after it expired). With `--placed`, a named file that does not exist stops the
    stage with its path, unless the round is `optional` (its placement goes on without the answers
    that did not come back, and names them).
    """
    awaited = [(str(item["tool"]), Path(str(item["file"]))) for item in state().get(AWAITING) or []
               if isinstance(item, dict) and item.get("tool") and item.get("file")]
    if not awaited or made_since(calls, handed_at):
        return
    missing = [(tool, path) for tool, path in awaited if not path.is_file()]
    if missing and placed and optional:
        say("[drive] nothing saved at " + ", ".join(str(path) for _, path in missing) + "; placing what came back")
    elif missing and placed:
        raise StageError("no saved result at " + "; ".join(f"{path} (save the complete {tool} result there)"
                                                           for tool, path in missing)
                         + ". Save each result as is, download link included, then run the same resume command again")
    take_saved_results([pair for pair in awaited if pair not in missing])


def fetch_or_hand_over(calls: list[dict[str, Any]], placed: bool,
                       hand_over_calls: Callable[[], None] | None = None, optional: bool = False) -> None:
    """Print the calls for the model and stop the stage; with `placed`, go on to placement.

    `hand_over_calls` prints the hand-over its own way instead (it ends the stage itself).

    `optional` says this round's subjects can go without an answer: its caller places what came back
    and names the subjects that got nothing. The stages never fetch themselves, so it changes nothing
    here; it is kept so every caller states which rounds may come back thin.

    If local persistence fails after the calls were made, never switch Connector operations or
    refetch the account: the stage is run again with `--placed`, which places what was staged.
    """
    if placed:
        return
    if hand_over_calls is not None:
        hand_over_calls()
    hand_over(calls)


def place_record(operation: str, dest: str | Path, by_content: Callable[[], Any], nth: int = 0,
                 any_status: bool = True) -> None:
    """Place the response to this round's request of `operation` at `dest`, by content.

    `by_content` places it from this run's staged copies (`persist_envelope.py --dest`). A
    rate-limited response the model retried is followed by its retry: set the limited one aside as
    superseded and take the next. `nth` and `any_status` are the caller's; `by_content` already
    carries both.
    """
    by_content()
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
    if key in handed:
        # 交接点名的结果文件由续跑自己 fetch / stage，模型只存文件、跑一条续跑命令。
        take_handed_results(calls, str(handed[key]), args.placed, optional)
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
