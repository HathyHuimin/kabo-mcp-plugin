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
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
import static_host  # noqa: E402

TOOL = "collect_video_media"
# 看片的组合工具只有本 Skill 用：在这里登记给 static_host 的组合拆分，各 Skill 共用的 static_host.py 保持逐字相同。
static_host.COMPOSITE_TOOLS.add(TOOL)
STATE = Path("watch-media.json")
PLAN = Path("media_job_plan.json")
# 一次调用最多等 45 秒（Codex 工具超时 60 秒以内）；媒体一轮约三分钟，6 份结果够等满。
MAX_ROUNDS = 6
# 作者包默认读每条视频开头 3 秒以内的帧（read_deep_synthesis_packet.py 同一口径），先看这些；每条最多看 5 张、
# 首尾必含、其余均匀取，10 条视频 50 张，留在 Web 每回合 60 张图的上限以内。
OPENING_SECONDS = 3
FRAMES_PER_VIDEO = 5
SAVE_RULE = ("Save the result as is, download link included, to the absolute path under it; write the file with "
             "your file-write tool or a single-quoted heredoc, never by passing the result as a command-line "
             "argument. Never call get_research_data_download and never download anything yourself.")


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
            + " ".join(quote(part) for part in argv))


def hand_over_media(plan: dict[str, Any], args: argparse.Namespace, note: str = "") -> None:
    current = state()
    results = Path.cwd() / "host-results"
    first = len(list(results.glob("*.json"))) + 1 if results.is_dir() else 1
    file = results / f"{first:02d}-{TOOL}.json"
    current.setdefault("awaiting", []).append(str(file))
    save_state(current)
    videos = len(plan.get("jobs") or [])
    say((note + " " if note else "") +
        f"Hand-over: make this one call now with the arguments verbatim; the backend queues the metadata, transcript "
        f"and keyframe jobs of all {videos} selected videos and waits for them. If its result still reports running "
        f"jobs (coverage.pending above 0), make the same call again right away with the same arguments (nothing is "
        f"queued or charged twice) until pending is 0 or you have made it {MAX_ROUNDS} times, saving each result over "
        f"the same file. " + SAVE_RULE)
    say("call: " + json.dumps({"tool": TOOL, "arguments": call_arguments(plan)}, ensure_ascii=False))
    say(f"  save: {file}")
    say("Then run this one command right away (it fetches and checks the saved result; links expire in 10 "
        "minutes): " + resume_command(args))


def take_saved() -> None:
    current = state()
    taken = current.setdefault("taken", {})
    for name in current.get("awaiting") or []:
        path = Path(name)
        if not path.is_file():
            raise ValueError(f"{path} is missing: save the complete {TOOL} result there, then run the same "
                             "resume command again")
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
        if job.get("status") in ("queued", "running"):
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
        take_saved()
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
