// 视频分析的媒体证据是一次后台组合调用 collect_video_media（KS9）：collect_media.py 只交出一行 call:；还有 job
// 在跑时续跑再交同一个调用；全部终态后逐项 persist 成 snapshot/envelope-NN.json，打印证据构建的 --envelope 参数和
// 一次读帧。这里不联网：组合结果按后台对这组入参会给出的形状手写，走「没有下载链接」的退路落盘。
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const scripts = path.join(root, "skills", "analyze-content-video", "scripts");

function python(script, args, { cwd, run } = {}) {
  const result = spawnSync("python3", [path.join(scripts, script), ...args], {
    cwd, encoding: "utf8", env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1", ...(run ? { KABO_STATIC_RUN_DIR: run } : {}) },
  });
  return { code: result.status, out: result.stdout + result.stderr };
}
function shell(command) {
  const result = spawnSync("bash", ["-c", command], { encoding: "utf8", env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" } });
  return { code: result.status, out: result.stdout + result.stderr };
}
const calls = (out) => out.split("\n").filter((line) => line.startsWith("call: ")).map((line) => JSON.parse(line.slice(6)));
const reads = (out) => out.split("\n").filter((line) => line.startsWith("read: ")).map((line) => JSON.parse(line.slice(6)));
const saveOf = (out) => out.split("\n").find((line) => line.startsWith("  result ")).split("has no download url: ")[1].slice(0, -1);
const resumeOf = (out) => { const line = out.split("\n").find((l) => l.startsWith("Then run this one command")); return line.slice(line.indexOf("cd '")).replace(/ --id <id>$/, ""); };

const URL_ = "https://www.youtube.com/watch?v=fixture0001";
const uuid = (n) => `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
function mediaEnvelope(request, jobId) {
  const base = {
    schema_version: "1.0", connector_id: "public-video-media", provider: "fixture", status: "completed", summary: "ok",
    limitations: [], credential_values_recorded: false, job_id: jobId, evidence_ref: `ev-${jobId}`, raw_sha256: jobId.replace(/-/g, "").padEnd(64, "a"),
    request_id: `req-${jobId}`, retrieved_at: "2026-11-06T00:00:00Z", observation_refs: [],
  };
  if (request.operation === "fetch_transcript")
    return { ...base, operation: "fetch_transcript", data: { cues: [{ text: "Here is the one thing you need.", start_ms: 0, end_ms: null }], origin: "speech_recognition", cue_count: 1,
      artifacts: [{ kind: "transcript", bytes: 10, sha256: "b".repeat(64), object_ref: `creator-media/${jobId}/epoch-1/transcript.json` }] } };
  if (request.operation === "measure_media_signals")
    return { ...base, operation: "measure_media_signals_hosted_stream", data: { shots: [], artifacts: [] } };
  const frames = [0, 0.5, 1].map((t, i) => ({ bytes: 100, sha256: "c".repeat(64), frame_id: `frame-${String(i + 1).padStart(4, "0")}`, t_seconds: t,
    object_ref: `creator-media/${jobId}/epoch-1/frame-${String(i + 1).padStart(4, "0")}.jpg` }));
  return { ...base, operation: "extract_keyframes_hosted_media", data: { frames, artifacts: frames.map((f) => ({ kind: "keyframes", bytes: 100, sha256: f.sha256, object_ref: f.object_ref })),
    selection: "duration_scaled_v1", duration_s: 30, frame_count: frames.length } };
}
const TOOLS = { fetch_transcript: "public_video_fetch_transcript", extract_keyframes: "public_video_extract_keyframes_hosted_media",
  measure_media_signals: "public_video_measure_media_signals" };
function composite(args, pendingGroups = new Set()) {
  const jobs = [], list = [], unfinished = [];
  args.requests.forEach((request, index) => {
    const group = `${request.content_id}/${request.operation}`;
    const jobId = uuid(index + 1);
    const pending = pendingGroups.has(group);
    jobs.push({ group, content_id: request.content_id, operation: request.operation, attempt: 1, job_id: jobId, status: pending ? "running" : "completed" });
    if (pending) { unfinished.push(group); return; }
    list.push({ tool: TOOLS[request.operation], parameters: request.parameters, group, attempts: 1, result: mediaEnvelope(request, jobId) });
  });
  return { schema_version: "kabo-composite-fetch.v1", tool: "collect_video_media", status: unfinished.length ? "partial" : "completed", calls: list,
    summary: { requests: jobs.length, pending: unfinished.length, jobs },
    coverage: { deadline_reached: unfinished.length > 0, unfinished, provider_calls: 0, pending: unfinished.length }, limitations: [] };
}
const save = (file, result) => writeFile(file, JSON.stringify({ content: [{ type: "text", text: JSON.stringify(result) }], structuredContent: result }));

test("视频分析媒体一次组合调用：交接一个 call，续跑等到终态，逐项落成 envelope-NN 并打印读帧", async (t) => {
  const workspace = await mkdtemp(path.join(os.tmpdir(), "kabo-acv-media-"));
  if (!process.env.KEEP) t.after(() => rm(workspace, { recursive: true, force: true }));
  const init = python("static_host.py", ["init", "--workspace", workspace]);
  assert.equal(init.code, 0, init.out);
  const run = init.out.trim();
  if (process.env.KEEP) console.log("RUN=" + run);

  const first = python("collect_media.py", ["--url", URL_, "--operations", "transcript,keyframes,measure"], { cwd: run, run });
  assert.equal(first.code, 0, first.out);
  const handed = calls(first.out);
  assert.equal(handed.length, 1);
  assert.equal(handed[0].tool, "collect_video_media");
  const args = handed[0].arguments;
  assert.equal(args.delivery, "download");
  assert.match(args.run_key, /^acv:[0-9a-f]{32}$/);
  assert.deepEqual(args.requests.map((r) => r.operation), ["fetch_transcript", "extract_keyframes", "measure_media_signals"]);
  const keyframes = args.requests.find((r) => r.operation === "extract_keyframes");
  assert.deepEqual(keyframes.parameters, { url: URL_, sampling: "duration_scaled", max_frames: 60 });

  // 还有 job 在跑：续跑再交同一个调用。
  await save(saveOf(first.out), composite(args, new Set(["video/extract_keyframes"])));
  const second = shell(resumeOf(first.out));
  assert.equal(second.code, 0, second.out);
  assert.match(second.out, /1 media job\(s\) are still running/);
  assert.deepEqual(calls(second.out)[0].arguments, args);

  const final = composite(args);
  await save(saveOf(second.out), final);
  const third = shell(resumeOf(second.out));
  assert.equal(third.code, 0, third.out);
  assert.match(third.out, /"status": "collected"/);
  for (const [index, call] of final.calls.entries()) {
    const placed = JSON.parse(await readFile(path.join(run, `snapshot/envelope-${String(index + 1).padStart(2, "0")}.json`), "utf8"));
    assert.equal(placed.job_id, call.result.job_id);
    assert.equal(placed.operation, call.result.operation);
  }
  assert.match(third.out, /--envelope snapshot\/envelope-01\.json --envelope snapshot\/envelope-02\.json --envelope snapshot\/envelope-03\.json/);
  const frameReads = reads(third.out);
  assert.equal(frameReads.length, 1);
  assert.equal(frameReads[0].arguments.job_id, uuid(2));
  assert.equal(frameReads[0].arguments.object_refs.length, 3);
});

test("collect_media.py：未知操作与超出上限的帧数直接拒绝，不交出调用", () => {
  const bad = python("collect_media.py", ["--url", URL_, "--operations", "frames"], { cwd: os.tmpdir() });
  assert.equal(bad.code, 1);
  assert.match(bad.out, /--operations takes/);
  assert.equal(calls(bad.out).length, 0);
});

test("collect_media.py：续跑时既没有结果编号、也没有暂存记录，报错说出先 stage 再续跑", async (t) => {
  const workspace = await mkdtemp(path.join(os.tmpdir(), "kabo-acv-media-"));
  t.after(() => rm(workspace, { recursive: true, force: true }));
  const run = python("static_host.py", ["init", "--workspace", workspace]).out.trim();
  const first = python("collect_media.py", ["--url", URL_, "--operations", "transcript,keyframes,measure"], { cwd: run, run });
  assert.equal(first.code, 0, first.out);
  const resumed = shell(resumeOf(first.out));
  assert.equal(resumed.code, 1, resumed.out);
  assert.match(resumed.out, /no result for this hand-over/);
  assert.match(resumed.out, /static_host\.py stage --tool collect_video_media/);
});
