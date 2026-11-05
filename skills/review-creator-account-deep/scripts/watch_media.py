#!/usr/bin/env python3
"""Watch the selected videos with one backend call and get every watch file ready for authoring.

Workflow steps 2–4 in one command. Run it from the run directory after step 1 settles on
`<analysis>`:

- First run: writes `catalog_stability.json`, `catalog_plus.md`, `watch_set.json` and
  `media_job_plan.json` (the same scripts the steps name), then prints one `collect_video_media`
  call with the file to save its result in and the resume command. The backend queues every
  planned metadata, transcript and keyframe job itself and waits for them.
- `--placed`: fetches the saved result. While the backend still reports running jobs it prints the
  same call again (the backend only keeps waiting: nothing is queued or charged twice). Once every
  job is terminal (or after `MAX_ROUNDS` saved results) it writes each terminal envelope as
  `snapshot/media-<content_id>-<operation>.attempt-<n>.json`, runs `record_watch_outcomes.py`,
  `build_watch_evidence.py`, `build_deep_synthesis_input.py` and the authoring seed, and prints the
  frame reads to view and the authoring packet.

Prints `status: stopped` when no video can be watched.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
import static_host  # noqa: E402

TOOL = "collect_video_media"
STATE = Path("watch-media.json")
PLAN = Path("media_job_plan.json")
# 一次调用最多等 45 秒（Codex 工具超时 60 秒以内）；媒体一轮约三分钟，6 份结果够等满。
MAX_ROUNDS = 6
# 作者包默认读每条视频开头 3 秒以内的帧（read_deep_synthesis_packet.py 同一口径），先看这些；每条最多看 5 张、
# 首尾必含、其余均匀取，10 条视频 50 张，留在 Web 每回合 60 张图的上限以内。
OPENING_SECONDS = 3
FRAMES_PER_VIDEO = 5
# 与 connector_driver.py 的交接同一个办法（kabo ADR-0084「2026-11-04」）：模型只交结果编号，续跑按编号自己取；
# 存文件只是没有下载链接时的退路。
SAVE_RULE = ("Do not save the result. Its download url holds its result id: the 22 characters between "
             "`research-export/v1/` and `.json`. Put the id of your last call's result into the resume command "
             "below as `--id <id>`. Only if that result has no download url, save it whole, as returned, to the "
             "fallback file named under the call (with the host's file-write tool, never as a command-line "
             "argument) and leave `--id` out. Never download anything yourself; call get_research_data_download "
             "only when the resume says the id expired.")


def say(text: str) -> None:
    print(text, flush=True)


def run(*argv: str) -> str:
    done = subprocess.run([sys.executable, *argv], capture_output=True, text=True)
    if done.returncode != 0:
        raise RuntimeError((done.stdout + done.stderr).strip()[-1500:] or f"{argv[0]} failed")
    return done.stdout


def script(name: str) -> str:
    return str(SCRIPTS / name)


def state() -> dict[str, Any]:
    return json.loads(STATE.read_text()) if STATE.is_file() else {}


def save_state(value: dict[str, Any]) -> None:
    STATE.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def run_key() -> str:
    # 同一个 run 恒得同一把键：重复调用只是接着等；别的 run 的键不同，不会回放别人的 job。
    return "deep:" + hashlib.sha256(str(static_host.run_dir().resolve()).encode()).hexdigest()[:32]


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    analysis = str(args.analysis)
    if not Path("catalog_stability.json").is_file():
        run(script("build_catalog_stability.py"), analysis, "--output", "catalog_stability.json")
    if not Path("catalog_plus.md").is_file():
        run(script("compose_catalog_plus.py"), analysis, "--stability", "catalog_stability.json",
            "--markdown", "catalog_plus.md")
    if not Path("watch_set.json").is_file():
        named = [part for content_id in args.named_content_id for part in ("--named-content-id", content_id)]
        run(script("select_watch_set.py"), analysis, "--output", "watch_set.json", "--cap", str(args.cap), *named)
    if not PLAN.is_file():
        run(script("plan_media_jobs.py"), "watch_set.json", "--output", str(PLAN))
    return json.loads(PLAN.read_text())


def call_arguments(plan: dict[str, Any]) -> dict[str, Any]:
    requests = [{"content_id": request["content_id"], "operation": request["operation"],
                 "parameters": request["params"]}
                for batch in plan.get("batches") or [] for request in batch.get("requests") or []]
    return {"requests": requests, "run_key": run_key(), "delivery": "download"}


def quote(text: str) -> str:
    """SKILL.md 的命令形式：每个路径都用单引号包起来。"""
    return "'" + str(text).replace("'", "'\\''") + "'"


def resume_command(args: argparse.Namespace) -> str:
    run_directory = quote(str(static_host.run_dir()))
    argv = [script("watch_media.py"), str(args.analysis), "--cap", str(args.cap),
            "--output-language", args.output_language,
            *[part for content_id in args.named_content_id for part in ("--named-content-id", content_id)], "--placed"]
    return (f"cd {run_directory} && KABO_STATIC_RUN_DIR={run_directory} python3 "
            + " ".join(quote(part) for part in argv) + " --id <id>")


def hand_over_media(plan: dict[str, Any], args: argparse.Namespace, note: str = "") -> None:
    current = state()
    results = Path.cwd() / "host-results"
    first = len(list(results.glob("*.json"))) + 1 if results.is_dir() else 1
    file = results / f"{first:02d}-{TOOL}.json"
    current.setdefault("awaiting", []).append(str(file))
    current["handed_at"] = datetime.now(timezone.utc).isoformat()
    save_state(current)
    videos = len(plan.get("jobs") or [])
    say((note + " " if note else "") +
        f"Hand-over: make this one call now with the arguments verbatim; the backend queues the metadata, transcript "
        f"and keyframe jobs of all {videos} selected videos and waits for them. If its result still reports running "
        f"jobs (coverage.pending above 0), make the same call again right away with the same arguments (nothing is "
        f"queued or charged twice) until pending is 0 or you have made it {MAX_ROUNDS} times; only the last result "
        f"counts. " + SAVE_RULE)
    say("call: " + json.dumps({"tool": TOOL, "arguments": call_arguments(plan)}, ensure_ascii=False))
    say(f"  result  (fallback file, only if the result has no download url: {file})")
    say("Then run this one command right away, <id> replaced by the last result's id (ids expire in 10 "
        "minutes): " + resume_command(args))


def staged_since(moment: str | None) -> bool:
    if not moment:
        return False
    return any(str(record.get("staged_at") or "") >= moment for record in static_host.composite_records(TOOL))


def take_saved(result_id: str | None = None) -> None:
    current = state()
    taken = current.setdefault("taken", {})
    awaiting = current.get("awaiting") or []
    if result_id:
        # 编号给的是最近一次交接那一份；之前各次交接的退路文件仍按下面的办法取。
        name = awaiting[-1] if awaiting else ""
        key = "id:" + static_host.result_id_of(result_id)
        if name and taken.get(name) != key:
            try:
                static_host.fetch_id(TOOL, result_id)
            except (ValueError, OSError, KeyError, TypeError) as error:
                raise ValueError(f"--id {result_id} gave no usable {TOOL} result ({error}). Run the same resume "
                                 "command again with the id from the last call's download url") from None
            taken[name] = key
            save_state(current)
        awaiting = awaiting[:-1]
    for name in awaiting:
        path = Path(name)
        if not path.is_file() and staged_since(current.get("handed_at")):
            # 宿主已用自己的方式把结果落进本 run（例如直接 stage），没有另存文件：照用已落的那份。
            continue
        if str(taken.get(name) or "").startswith("id:"):
            continue
        if not path.is_file():
            raise ValueError(f"no result for this hand-over: run the same resume command again with --id <id>, the "
                             f"id from the last {TOOL} call's download url (only for a result with no download url, "
                             f"save it whole at {path} and leave --id out)")
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if taken.get(name) == digest:
            continue
        try:
            result = static_host.fetch(TOOL, raw)
            if result.get("fetched") == 0:
                result = static_host.stage(TOOL, raw)
        except (ValueError, OSError, KeyError, TypeError) as error:
            raise ValueError(f"{path} does not hold a usable {TOOL} result ({error}). Save the complete result there "
                             "as returned, download link included, then run the same resume command again") from None
        taken[name] = digest
        save_state(current)


def place_envelopes(record: dict[str, Any]) -> tuple[list[str], list[str]]:
    jobs = {job["group"]: job for job in (record.get("summary") or {}).get("jobs") or [] if isinstance(job, dict)}
    bindings, pending = [], []
    for call in record.get("calls") or []:
        content_id, operation = str(call["group"]).rsplit("/", 1)
        attempt = int(jobs.get(call["group"], {}).get("attempt") or 1)
        body = Path(call["path"]).read_bytes()
        dest = Path("snapshot") / f"media-{content_id}-{operation}.attempt-{attempt}.json"
        if dest.is_file() and dest.read_bytes() != body:
            raise ValueError(f"{dest} already holds a different result; this run's watch files were changed")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(body)
    for group, job in jobs.items():
        if not job.get("job_id"):
            continue
        content_id, operation = group.rsplit("/", 1)
        flag = f"{content_id}:{operation}={job['job_id']}"
        if job.get("status") in static_host.PENDING:
            pending.append(flag)
        else:
            bindings.append(flag)
    return bindings, pending


def frame_reads(evidence: dict[str, Any], record: dict[str, Any]) -> list[dict[str, Any]]:
    job_of = {job["group"]: job.get("job_id") for job in (record.get("summary") or {}).get("jobs") or []
              if isinstance(job, dict)}
    reads = []
    for video in evidence.get("items") or []:
        if not isinstance(video, dict) or not video.get("watched"):
            continue
        content_id = video.get("content_id")
        frames = ((video.get("frames") or {}).get("items")) or []
        refs = [frame["path"] for frame in frames
                if isinstance(frame, dict) and frame.get("path") and frame.get("t_seconds", OPENING_SECONDS + 1) <= OPENING_SECONDS]
        if len(refs) > FRAMES_PER_VIDEO:
            step = (len(refs) - 1) / (FRAMES_PER_VIDEO - 1)
            refs = [refs[round(index * step)] for index in range(FRAMES_PER_VIDEO)]
        job_id = job_of.get(f"{content_id}/extract_keyframes")
        if refs and job_id:
            reads.append({"content_id": content_id, "job_id": job_id, "object_refs": refs})
    return reads


def finish(args: argparse.Namespace, record: dict[str, Any]) -> None:
    bindings, pending = place_envelopes(record)
    flags = [part for flag in bindings for part in ("--job", flag)]
    flags += [part for flag in pending for part in ("--pending", flag)]
    if pending:
        flags.append("--allow-pending")
    run(script("record_watch_outcomes.py"), str(PLAN), "--envelope-dir", "snapshot", *flags,
        "--output", "watch_outcomes.json")
    run(script("build_watch_evidence.py"), "watch_outcomes.json", "--envelope-dir", "snapshot",
        "--output", "watched_evidence.json")
    run(script("build_deep_synthesis_input.py"), "watched_evidence.json", "--watch-set", "watch_set.json",
        "--account-analysis", str(args.analysis), "--authoring-basis", "--stability", "catalog_stability.json",
        "--output", "deep_synthesis_input.json")
    packet_argv = [script("read_deep_synthesis_packet.py"), "deep_synthesis_input.json", "--schema",
                   "--output-language", args.output_language, "--stability", "catalog_stability.json"]
    if not Path("account_deep_authoring.json").is_file():
        packet_argv += ["--authoring-seed", "account_deep_authoring.json"]
    else:
        packet_argv += ["--authoring"]
    packet = run(*packet_argv)
    evidence = json.loads(Path("watched_evidence.json").read_text())
    reads = frame_reads(evidence, record)
    say(json.dumps({"status": "watched", "pending_jobs": len(pending), "watch_outcomes": "watch_outcomes.json",
                    "watched_evidence": "watched_evidence.json", "authoring_seed": "account_deep_authoring.json"}))
    if reads:
        say("View the frames: read these opening frames now, one read_research_data_artifacts call per video, all in "
            "one response, then display every image before any visual claim (see references/watch-and-persist.md, "
            "View the frames and write the cards):")
        for read in reads:
            say("read: " + json.dumps({"tool": "read_research_data_artifacts",
                                       "arguments": {"job_id": read["job_id"], "object_refs": read["object_refs"],
                                                     "delivery": "download"}}, ensure_ascii=False))
    else:
        say("No frames arrived: keep the missing-visual-evidence limit and use the transcripts only.")
    say("Authoring packet (fill account_deep_authoring.json from it, as references/authoring.md says):")
    say(packet.rstrip())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("analysis", type=Path)
    parser.add_argument("--cap", type=int, default=10)
    parser.add_argument("--named-content-id", action="append", default=[])
    parser.add_argument("--output-language", choices=("en", "zh-CN"), default="en")
    parser.add_argument("--placed", action="store_true")
    parser.add_argument("--id", dest="result_id", help="the result id of the last collect_video_media call")
    args = parser.parse_args(argv)
    try:
        plan = prepare(args)
        if not plan.get("jobs"):
            say(json.dumps({"status": "stopped", "message": "No selected video can be watched: say no video could "
                            "be watched and describe no hooks or cuts.", "reason": plan.get("empty_reason")}))
            return 0
        if not args.placed:
            if state().get("awaiting"):
                say("This watch round was already handed over; make the call printed earlier, or run with --placed.")
            hand_over_media(plan, args)
            return 0
        take_saved(args.result_id)
        records = static_host.composite_records(TOOL)
        if not records:
            raise ValueError(f"no {TOOL} result was saved in this run; make the printed call and save it first")
        latest = records[-1]
        pending = int((latest.get("coverage") or {}).get("pending") or 0)
        current = state()
        current["rounds"] = int(current.get("rounds") or 0) + 1
        save_state(current)
        if pending and current["rounds"] < MAX_ROUNDS:
            hand_over_media(plan, args, note=f"{pending} media job(s) are still running.")
            return 0
        finish(args, latest)
        return 0
    except (ValueError, RuntimeError, OSError, KeyError, json.JSONDecodeError) as error:
        say(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
