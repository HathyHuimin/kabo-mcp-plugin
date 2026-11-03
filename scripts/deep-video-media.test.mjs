// 深度复盘看片是一次后台组合调用 collect_video_media（KS9）：watch_media.py 先跑目录摘要、选片与媒体计划，
// 只交出一行 call:；还有 job 在跑时续跑再交同一个调用；全部终态后按组逐字节写 snapshot/media-*.json、
// 绑定 job id、生成 outcomes / evidence / 作者种子，并打印每条已看视频的开头帧读取。这里不联网：
// 组合结果按后台对这组入参会给出的形状手写，走 stage 回退落盘。
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const SKILL = "review-creator-account-deep";
const scripts = path.join(root, "skills", SKILL, "scripts");

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
const saveOf = (out) => out.split("\n").find((line) => line.startsWith("  save: ")).slice(8);
const resumeOf = (out) => { const line = out.split("\n").find((l) => l.startsWith("Then run this one command")); return line.slice(line.indexOf("cd '")); };

const ids = ["DfixtureA01", "DfixtureB02", "DfixtureC03"];
const analysis = {
  schema_version: "account-analysis.v1", analysis_id: "deep-media-fixture", platform: "instagram", snapshot_id: "snap-1",
  source_mode: "public", rule_version: "fixture",
  scope: { account: "neutral.creator", followers: 12000, window: { start: "2026-08-01T00:00:00Z", end: "2026-10-30T00:00:00Z" } },
  rankings: ids.map((content_id, index) => ({
    content_id, format: "reel", title: `Fixture reel ${index + 1}`, url: `https://www.instagram.com/reel/${content_id}/`,
    published_at: `2026-10-0${index + 1}T10:00:00+00:00`, rank_by_views: index + 1,
    metrics: { views: 90000 - index * 30000, likes: 900 - index * 200, comments: 30 - index * 5 }, view_multiple_vs_format_median: 2 - index * 0.5,
  })),
  baselines: {}, coverage: {}, funnel_signals: {}, outliers: [], pillar_performance: [], claim_boundary: [],
};

const uuid = (n) => `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
function mediaEnvelope(request, jobId) {
  const base = {
    schema_version: "1.0", connector_id: "public-video-media", provider: "fixture", status: "completed", summary: "ok",
    limitations: [], credential_values_recorded: false, job_id: jobId, evidence_ref: `ev-${jobId}`, raw_sha256: "a".repeat(64),
    request_id: `req-${jobId}`, retrieved_at: "2026-11-03T00:00:00Z", observation_refs: [],
  };
  const url = request.parameters.url;
  if (request.operation === "collect_public_metadata")
    return { ...base, operation: "collect_public_metadata", data: { video: { id: request.content_id, title: `Fixture reel`, webpage_url: url, duration: 30 }, artifacts: [] } };
  if (request.operation === "fetch_transcript")
    return { ...base, operation: "fetch_transcript", data: { cues: [{ text: "Here is the one thing you need.", start_ms: 0, end_ms: null }, { text: "Watch this.", start_ms: 4000, end_ms: null }], origin: "speech_recognition", cue_count: 2,
      artifacts: [{ kind: "transcript", bytes: 10, sha256: "b".repeat(64), object_ref: `creator-media/${jobId}/epoch-1/transcript.json` }] } };
  const frames = [0, 0.5, 1, 1.5, 2, 2.5, 3, 10].map((t, i) => ({ bytes: 100, sha256: "c".repeat(64), frame_id: `frame-${String(i + 1).padStart(4, "0")}`, t_seconds: t,
    object_ref: `creator-media/${jobId}/epoch-1/frame-${String(i + 1).padStart(4, "0")}.jpg` }));
  return { ...base, operation: "extract_keyframes_hosted_media", data: { frames, artifacts: frames.map((f) => ({ kind: "keyframes", bytes: 100, sha256: f.sha256, object_ref: f.object_ref })),
    selection: "duration_scaled_v1", duration_s: 30, frame_count: frames.length } };
}
function composite(args, pendingGroups = new Set()) {
  const jobs = [], list = [], unfinished = [];
  args.requests.forEach((request, index) => {
    const group = `${request.content_id}/${request.operation}`;
    const jobId = uuid(index + 1);
    const pending = pendingGroups.has(group);
    jobs.push({ group, content_id: request.content_id, operation: request.operation, attempt: 1, job_id: jobId, status: pending ? "running" : "completed" });
    if (pending) { unfinished.push(group); return; }
    const result = mediaEnvelope(request, jobId);
    list.push({ tool: result.operation === "extract_keyframes_hosted_media" ? "public_video_extract_keyframes_hosted_media" : `public_video_${request.operation}`,
      parameters: request.parameters, group, attempts: 1, result });
  });
  return { schema_version: "kabo-composite-fetch.v1", tool: "collect_video_media", status: unfinished.length ? "partial" : "completed", calls: list,
    summary: { requests: jobs.length, pending: unfinished.length, jobs },
    coverage: { deadline_reached: unfinished.length > 0, unfinished, provider_calls: 0, pending: unfinished.length }, limitations: [] };
}
const save = (file, result) => writeFile(file, JSON.stringify({ content: [{ type: "text", text: JSON.stringify(result) }], structuredContent: result }));

test("看片一次组合调用：交接一个 call，续跑等到终态，按组写入快照并打印开头帧读取与作者包", async (t) => {
  const workspace = await mkdtemp(path.join(os.tmpdir(), "kabo-deep-media-"));
  t.after(() => rm(workspace, { recursive: true, force: true }));
  const init = python("static_host.py", ["init", "--workspace", workspace]);
  assert.equal(init.code, 0, init.out);
  const run = init.out.trim();
  await writeFile(path.join(run, "account_analysis.json"), JSON.stringify(analysis));

  const first = python("watch_media.py", ["account_analysis.json"], { cwd: run, run });
  assert.equal(first.code, 0, first.out);
  const handed = calls(first.out);
  assert.equal(handed.length, 1);
  assert.equal(handed[0].tool, "collect_video_media");
  const args = handed[0].arguments;
  assert.equal(args.delivery, "download");
  assert.match(args.run_key, /^deep:[0-9a-f]{32}$/);
  assert.equal(args.requests.length, 9);
  assert.deepEqual([...new Set(args.requests.map((r) => r.operation))], ["collect_public_metadata", "fetch_transcript", "extract_keyframes"]);

  // 第一次结果还有 job 在跑：续跑再交同一个调用（同一 run_key、同一组请求）。
  await save(saveOf(first.out), composite(args, new Set([`${ids[0]}/extract_keyframes`])));
  const second = shell(resumeOf(first.out));
  assert.equal(second.code, 0, second.out);
  assert.match(second.out, /1 media job\(s\) are still running/);
  assert.deepEqual(calls(second.out)[0].arguments, args);
  assert.notEqual(saveOf(second.out), saveOf(first.out));

  const final = composite(args);
  await save(saveOf(second.out), final);
  const third = shell(resumeOf(second.out));
  assert.equal(third.code, 0, third.out);
  assert.match(third.out, /"status": "watched", "pending_jobs": 0/);
  for (const call of final.calls) {
    const [contentId, operation] = call.group.split("/");
    const placed = JSON.parse(await readFile(path.join(run, `snapshot/media-${contentId}-${operation}.attempt-1.json`), "utf8"));
    assert.deepEqual(placed, call.result);
  }
  const outcomes = JSON.parse(await readFile(path.join(run, "watch_outcomes.json"), "utf8"));
  assert.ok(JSON.stringify(outcomes).includes(ids[0]));
  const frameReads = reads(third.out);
  assert.equal(frameReads.length, 3);
  for (const read of frameReads) {
    assert.equal(read.tool, "read_research_data_artifacts");
    assert.equal(read.arguments.object_refs.length, 5, "开头 3 秒内 7 帧，取 5 张、首尾必含");
    assert.match(read.arguments.object_refs[0], /frame-0001\.jpg$/);
    assert.match(read.arguments.object_refs.at(-1), /frame-0007\.jpg$/);
  }
  assert.match(third.out, /Authoring packet/);
  await readFile(path.join(run, "account_deep_authoring.json"), "utf8");
});
