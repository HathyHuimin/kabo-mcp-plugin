#!/usr/bin/env python3
"""The mechanical half of a skill driver: hand data calls to the model, place, run scripts.

A skill's `drive.py` runs everything between two model judgments — which data call follows, where
each response is placed, which deterministic script runs next — and prints only what the next judgment
reads. This module is the part of that which does not depend on the skill: fetch rounds recorded by what
they asked for, the hand-over of each round to the model as `call:` lines naming public MCP tools
(`--placed` picks the staged responses up), the run directory, the stage lock and the stage state file.
Scripts never fetch by themselves: every round is handed over, and a multi-round fetch is one backend
composite tool call (`composite_call`), whose staged record the stage then reads
(`composite_placement`).

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
from persist_envelope import run_dir_of  # noqa: E402

SCRIPTS = Path(__file__).resolve().parent
SKILL_DIR = SCRIPTS.parent
REFERENCES = SKILL_DIR / "references"

HANDOVER_PREFIX = "call: "
STATE = ".drive.json"
LOCK = ".drive.lock"
# A lock whose owner process is gone is stale at once. A live owner holds it for as long as a stage
# can run; past that, the pid is taken for another process that reused the number. Only a lock whose
# owner cannot be read (written this instant) is judged by its age alone.
UNREADABLE_LOCK_SECONDS = 60.0
LIVE_LOCK_SECONDS = 1800.0


class StageError(Exception):
    """A stage cannot go on; the message says what to fix."""


class RetryRound(StageError):
    """The round's answer is a transient failure: the same command, run again without `--placed`, hands it over anew."""


class Busy(Exception):
    """Another stage holds this run directory."""


def say(text: str = "") -> None:
    print(text, flush=True)


def requests_of(call: dict[str, Any]) -> list[dict[str, Any]]:
    arguments = call.get("arguments") or {}
    if call.get("tool") == "data_connector_batch_run":
        return list(arguments.get("requests") or [])
    if call.get("tool") == "data_connector_run":
        return [arguments]
    return []


def resume_command() -> str:
    """This same stage, with the same arguments, run again with `--placed` from this run directory.

    `--without-include` answered one hand-over and is remembered by the run. `--question-file -` stays:
    the run checks a first stage's arguments, and a `--placed` run keeps the stored ask without
    waiting on standard input.
    """
    stage = [arg for arg in sys.argv[1:] if arg not in ("--placed", "--without-include")]
    return (f"cd '{Path.cwd()}' && KABO_STATIC_RUN_DIR='{Path.cwd()}' python3 '{SCRIPTS / 'drive.py'}' "
            + " ".join(f"'{arg}'" if re.search(r"[^\w./=:-]", arg) else arg for arg in stage) + " --placed")


def hand_over(calls, why=""):
    # 每条调用后面直接打印要存的文件名与 fetch 命令，结尾打印续跑命令：模型照抄即可，不必自己拼路径
    # （实测里模型猜过不存在的参数、写错过运行目录，最后绕开脚本自己复制、下载，跳过了整条存证）。
    from static_host import public_calls
    public = [mapped for call in calls for mapped in public_calls(call)]
    results = Path("host-results")
    first = len(list(results.glob("*.json"))) + 1 if results.is_dir() else 1
    say(f"Hand-over: make these {len(public)} call(s) now, each as its own individual call (never combined into "
        "one batch call), all in one response, with the arguments verbatim. Each result holds a download link: never "
        "download it yourself with a shell command or a browser. In the order below, save each result itself, link included and "
        "unchanged, to the file named under it (the host's result export, or the result text as returned written "
        "with a single-quoted heredoc) and run the command under it at once: fetch downloads and checks the data; "
        "links expire in 10 minutes. When fetch reports no ready download, run the same command with stage in "
        "place of fetch.")
    for number, mapped in enumerate(public, first):
        saved = f"host-results/{number:02d}-{mapped['tool']}.json"
        say(HANDOVER_PREFIX + json.dumps(mapped, ensure_ascii=False))
        say(f"  save: {saved}")
        say(f"  then: cd '{Path.cwd()}' && KABO_STATIC_RUN_DIR='{Path.cwd()}' python3 "
            f"'{SCRIPTS / 'static_host.py'}' fetch --tool {mapped['tool']} --input '{saved}'")
    say("When every result above is fetched or staged, resume: " + resume_command())
    raise SystemExit(0)


def fetch_or_hand_over(calls: list[dict[str, Any]], placed: bool,
                       hand_over_calls: Callable[[], None] | None = None, optional: bool = False) -> None:
    """Print the calls for the model and stop the stage; with `placed`, the calls were made: go on.

    `hand_over_calls` prints the hand-over its own way instead (it ends the stage itself). `optional`
    is kept for callers that place a round without some of its answers; the hand-over is the same.
    """
    if placed:
        return
    if hand_over_calls is not None:
        hand_over_calls()
    hand_over(calls)


def round_key(calls: list[dict[str, Any]]) -> str:
    return hashlib.sha256(json.dumps(calls, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def typed_records_since(roots: list[Path], since: datetime) -> int:
    """Typed tool results the host staged since then: a collector's set, or a composite's record.

    A composite record's sidecar has `schema_version` and no `connector_id`, so it counts here once
    per composite call; the per-call envelopes it was split into carry `connector_id` and do not.
    """
    count = 0
    for root in roots:
        try:
            metas = sorted(root.rglob("*.meta")) if root.is_dir() else []
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


def fetch_round(calls: list[dict[str, Any]], args: argparse.Namespace, place_responses,
                hand_over_calls: Callable[[], None] | None = None, optional: bool = False) -> bool:
    """One fetch round, done at most once per run: hand it over, then (with `--placed`) place the responses.

    Each round is recorded by what it asked for, so running a stage again — with `--placed` after a
    hand-over, or after fixing an artifact the stage rejected — skips the rounds already placed, and
    `--placed` applies to the first round still open. Returns False when it had been done.

    A round handed over is recorded too. Run again without `--placed`, it is placed all the same when
    the host recorded a response for each of its calls since the hand-over, and handed over again only
    when it did not. A placement that raises `RetryRound` (a transient failure, no answer about the
    subject) forgets the hand-over, so the same command run again without `--placed` hands it over anew.
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
        fetch_or_hand_over(calls, args.placed, hand_over_calls=hand_over_calls, optional=optional)
    except SystemExit as stop:
        if stop.code in (0, None):
            save_state(handed={**handed, key: now_iso()})
        raise
    args.placed = False
    try:
        place_responses()
    except RetryRound:
        handed.pop(key, None)
        save_state(handed=handed)
        raise
    handed.pop(key, None)
    save_state(rounds=[*done, key], handed=handed)
    return True


def composite_call(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """One backend composite fetch (`kabo-composite-fetch.v1`): a whole multi-round fetch as one call."""
    return {"tool": tool, "arguments": arguments}


def composite_placement(call: dict[str, Any], place: Callable[[dict[str, Any]], None],
                        matches: Callable[[dict[str, Any]], bool] | None = None) -> Callable[[], None]:
    """The `place_responses` of a `fetch_round([call], …)` whose one call is a composite: give `place`
    this run's staged record of it.

    `static_host.py fetch` / `stage` split the composite into one staged envelope per call and a record
    (`composite_records`): status, summary, coverage, limitations and the calls in order, each with the
    absolute `path` of its envelope. The newest record of the tool this run has not placed yet, and that
    `matches` accepts, is this round's; each record is placed at most once.
    """
    tool = str(call["tool"])
    key = round_key([call])

    def placed() -> None:
        from static_host import composite_records
        used = dict(state().get("composites") or {})
        taken = set(used.values())
        records = [record for record in composite_records(tool)
                   if record["id"] not in taken and (matches is None or matches(record))]
        if not records:
            raise StageError(f"no staged {tool} result for this request was found in this run. Make the {tool} "
                             "call the hand-over printed, save its complete result to the file named under it, run "
                             "the fetch command printed there, then repeat this command with --placed")
        record = records[-1]
        save_state(composites={**used, key: record["id"]})
        place(record)

    return placed


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
