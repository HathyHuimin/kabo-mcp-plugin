// research-youtube-trends 的短窗口取数是一次后台组合调用 collect_youtube_recent_videos（docs/skills-spec.md KS9）：
// 搜索 → 去重 → get_video 每 50 个一块都在后台做完。static_host.py stage 拆开后，
// complete_youtube_short_window.py place 把搜索写成 envelope-NN.json、详情写成 envelope-detail-NN.json
// 并合并；下游的 build / render / verify 不变。任何一次搜索或详情块失败、缺失都整段拒绝。
// 夹具全是合成的中性行（不含真实 creator 数据）。
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const scripts = path.join(root, "skills", "research-youtube-trends", "scripts");
const SINCE = "2026-10-26T00:00:00Z";
const UNTIL = "2026-11-02T00:00:00Z";
const HEAT = "2026-10-03T00:00:00Z";

function python(script, args, { cwd, run } = {}) {
  const env = { ...process.env, PYTHONDONTWRITEBYTECODE: "1" };
  delete env.KABO_STATIC_RUN_DIR;
  if (run) env.KABO_STATIC_RUN_DIR = run;
  const result = spawnSync("python3", ["-B", path.join(scripts, script), ...args], { cwd, env, encoding: "utf8" });
  return { code: result.status, out: result.stdout + result.stderr };
}

const id = (lane, n) => `${lane}${String(n).padStart(9, "0")}`.slice(0, 11);
const row = (videoId, day, views) => ({
  id: videoId, kind: views === undefined ? "youtube#searchResult" : "youtube#video", title: `Neutral clip ${videoId}`,
  description: "Plain description.", published_at: `2026-${day}T12:00:00Z`, channel_id: `UC${videoId}`, channel_title: `Channel ${videoId.slice(-3)}`,
  source_url: `https://www.youtube.com/watch?v=${videoId}`, duration_seconds: views === undefined ? null : 30, duration_text: null,
  metrics: { views: views === undefined ? null : String(views), likes: null, comments: null, subscribers: null, videos: null },
});
const envelope = (operation, rows, extra = {}) => ({
  schema_version: "1.0", connector_id: "youtube-public", operation, provider: "Neutral video provider", status: "completed",
  summary: `YouTube ${operation} returned ${rows.length} rows.`, limitations: [], credential_values_recorded: false,
  data: { rows, count: rows.length, retrieved_at: "2026-11-02T00:00:01.000Z" }, request_id: `req-${operation}-${rows.length}-${rows[0]?.id ?? "none"}`,
  retrieved_at: "2026-11-02T00:00:01.000Z", evidence_ref: `ev-${operation}-${rows[0]?.id ?? "none"}`, ...extra,
});

// 两个意图 × 两条通道：窗口通道各 20 行（窗口内），热度通道各 25 行（近 30 天，与窗口通道有重叠），共 60 个去重 id → 两块详情。
function collection({ failSearch, wrongChunk, dropChunk, audioLanguage } = {}) {
  const lanes = [
    { q: "desk setup", window: true, ids: Array.from({ length: 20 }, (_, n) => id("w0", n)) },
    { q: "home office", window: true, ids: Array.from({ length: 20 }, (_, n) => id("w1", n)) },
    { q: "desk setup", window: false, ids: [...Array.from({ length: 15 }, (_, n) => id("h0", n)), ...Array.from({ length: 10 }, (_, n) => id("w0", n))] },
    { q: "home office", window: false, ids: Array.from({ length: 5 }, (_, n) => id("h1", n)) },
  ];
  const day = (videoId) => (videoId.startsWith("w") ? "10-30" : "10-10");
  const calls = lanes.map((lane, index) => ({
    tool: "youtube_search_videos", group: `query-${index}`, attempts: 1,
    parameters: { query: lane.q, limit: 50, published_after: lane.window ? SINCE : HEAT, published_before: UNTIL, ...(lane.window ? {} : { order: "viewCount" }) },
    result: failSearch === index
      ? { schema_version: "1.0", connector_id: "youtube-public", operation: "search_videos", provider: "Neutral video provider", status: "failed",
          error_code: "quota_exceeded", summary: "Quota exceeded.", limitations: ["Daily quota exceeded."], credential_values_recorded: false }
      : envelope("search_videos", lane.ids.map((videoId) => row(videoId, day(videoId)))),
  }));
  const ids = [...new Set(lanes.filter((_, index) => index !== failSearch).flatMap((lane) => lane.ids))];
  const chunks = [ids.slice(0, 50), ids.slice(50)];
  chunks.forEach((chunk, index) => {
    if (dropChunk === index) return;
    const asked = wrongChunk === index ? [...chunk].reverse() : chunk;
    calls.push({ tool: "youtube_get_video", group: `detail-${index}`, attempts: 1,
      parameters: { video_ids: asked, ...(audioLanguage ? { include: ["content_signals"] } : {}) },
      result: envelope("get_video", chunk.map((videoId, n) => ({
        ...row(videoId, day(videoId), 1000 * (ids.length - ids.indexOf(videoId)) + n),
        ...(audioLanguage ? { content_signals: { default_audio_language: audioLanguage, default_language: "en" } } : {}),
      }))) });
  });
  return {
    schema_version: "kabo-composite-fetch.v1", tool: "collect_youtube_recent_videos", status: failSearch === undefined && dropChunk === undefined ? "completed" : "partial", calls,
    summary: { queries: lanes.map((lane, index) => ({ group: `query-${index}`, q: lane.q, status: failSearch === index ? "failed" : "completed", rows: lane.ids.length })),
      video_ids: ids, detail_chunks: chunks.length, details_returned: ids.length },
    coverage: { deadline_reached: dropChunk !== undefined, unfinished: dropChunk === undefined ? [] : [`detail-${dropChunk}`], provider_calls: calls.length },
    limitations: failSearch === undefined ? [] : [`query-${failSearch}: search failed (quota_exceeded).`],
  };
}

async function staged(t, value) {
  const workspace = await mkdtemp(path.join(os.tmpdir(), "kabo-ytsw-"));
  t.after(() => rm(workspace, { recursive: true, force: true }));
  const run = python("static_host.py", ["init", "--workspace", workspace]).out.trim();
  const file = path.join(run, "host-results", "01-collect_youtube_recent_videos.json");
  await writeFile(file, JSON.stringify({ content: [{ type: "text", text: JSON.stringify(value) }], structuredContent: value }));
  const stage = python("static_host.py", ["stage", "--tool", "collect_youtube_recent_videos", "--input", file], { cwd: run, run });
  assert.equal(stage.code, 0, stage.out);
  return run;
}

const place = (run) => python("complete_youtube_short_window.py", ["place", "--since", SINCE, "--until", UNTIL, "--output", "short-window-input.json"], { cwd: run, run });

test("组合取数的音频语言声明穿过 place 和详情合并，英语标题不能掩盖冲突", async (t) => {
  const run = await staged(t, collection({ audioLanguage: "hi" }));
  const placed = place(run);
  assert.equal(placed.code, 0, placed.out);
  await writeFile(path.join(run, "request.json"), JSON.stringify({
    ask: "English desk setup examples", niche: "desk setup", content_use: "workspace inspiration",
    language_scope: { mode: "restricted", accepted_languages: ["en"], basis: "The user requested English content." },
  }));
  const project = python("short_window_topic_gate.py", ["short-window-input.json", "--request", "request.json", "--output", "topic.json"], { cwd: run, run });
  assert.equal(project.code, 0, project.out);
  const projected = JSON.parse(await readFile(path.join(run, "topic.json"), "utf8"));
  assert.equal(projected.items.length, 60);
  assert.ok(projected.items.every((item) => item.language_evidence.some((e) => e.source === "audio_language" && e.text === "hi")));
  await writeFile(path.join(run, "decisions.json"), JSON.stringify(projected.items.map((item) => ({
    content_id: item.content_id, decision: "on_niche", reason: "Synthetic desk example.",
    language: { decision: "mismatch", languages: ["hi"], evidence: [{ source: "audio_language", quote: "hi" }],
      reason: "Declared Hindi audio does not match the requested English." },
  }))));
  const build = python("build_short_window_layers.py", ["short-window-input.json", "--topic-gate-input", "topic.json", "--topic-decisions", "decisions.json", "--output", "layers.json"], { cwd: run, run });
  assert.equal(build.code, 0, build.out);
  const layers = JSON.parse(await readFile(path.join(run, "layers.json"), "utf8"));
  assert.deepEqual(layers.heat, []);
  assert.deepEqual(layers.in_window, []);
});

test("一次 collect_youtube_recent_videos 拆成逐次搜索与详情 envelope，合并后走完 build → render → verify", async (t) => {
  const run = await staged(t, collection());
  const placed = place(run);
  assert.equal(placed.code, 0, placed.out);
  const result = JSON.parse(placed.out);
  assert.deepEqual(result.searches, ["envelope-01.json", "envelope-02.json", "envelope-03.json", "envelope-04.json"]);
  assert.deepEqual(result.details, ["envelope-detail-01.json", "envelope-detail-02.json"]);
  assert.deepEqual(result.merge, { status: "ok", search_ids: 60, detail_rows: 60, merged: 60, views_filled: 60 });
  const chunks = JSON.parse(await readFile(path.join(run, "video-id-chunks.json"), "utf8"));
  assert.deepEqual(chunks.chunks.map((chunk) => chunk.length), [50, 10]);
  // 每个 envelope 与 stage 落下的那份逐字节相同。
  const value = collection();
  const first = JSON.parse(await readFile(path.join(run, "envelope-01.json"), "utf8"));
  assert.deepEqual(first, value.calls[0].result);

  await writeFile(path.join(run, "request.json"), JSON.stringify({
    ask: "Find desk setup examples this week", niche: "desk setup", content_use: "workspace inspiration",
    language_scope: { mode: "unrestricted", accepted_languages: [], basis: "This synthetic scan names no account or content language." },
  }));
  const project = python("short_window_topic_gate.py", ["short-window-input.json", "--request", "request.json", "--output", "topic.json"], { cwd: run, run });
  assert.equal(project.code, 0, project.out);
  const projected = JSON.parse(await readFile(path.join(run, "topic.json"), "utf8"));
  await writeFile(path.join(run, "decisions.json"), JSON.stringify(projected.items.map((item) => ({
    content_id: item.content_id, decision: "on_niche", reason: "Synthetic desk example.",
    language: { decision: "not_applicable", languages: [], evidence: [], reason: "No content-language restriction." },
  }))));
  const build = python("build_short_window_layers.py", ["short-window-input.json", "--topic-gate-input", "topic.json", "--topic-decisions", "decisions.json", "--output", "short-window-layers.json"], { cwd: run, run });
  assert.equal(build.code, 0, build.out);
  assert.deepEqual(JSON.parse(build.out).in_window > 0, true);
  const contract = python("render_short_window_report.py", ["short-window-layers.json", "--language", "en", "--print-presentation-contract", "--text-only",
    "--contract-output", "short-window-presentation.json"], { cwd: run, run });
  assert.equal(contract.code, 0, contract.out);
  const sources = JSON.parse(contract.out).authoring_sources;
  const presentation = JSON.parse(await readFile(path.join(run, "short-window-presentation.json"), "utf8"));
  for (const [key, label] of Object.entries(presentation.labels)) label.text = sources.labels[key];
  const excerpts = Object.fromEntries(sources.rows.map((r) => [r.slot, r.source_excerpt]));
  for (const slot of presentation.rows) slot.text = excerpts[slot.slot];
  await writeFile(path.join(run, "short-window-presentation.json"), JSON.stringify(presentation));
  const args = ["short-window-layers.json", "creator-report.md", "--language", "en", "--presentation", "short-window-presentation.json"];
  const rendered = python("render_short_window_report.py", [...args, "--allow-authoring-correction"], { cwd: run, run });
  assert.equal(JSON.parse(rendered.out).status, "ok", rendered.out);
  const verified = python("render_short_window_report.py", [...args, "--verify-report"], { cwd: run, run });
  assert.equal(JSON.parse(verified.out).status, "ok", verified.out);
  assert.match(await readFile(path.join(run, "creator-report.md"), "utf8"), /plays/);
});

for (const [name, options, message] of [
  ["一次搜索失败", { failSearch: 1 }, /query-1 search status is failed.*cannot be completed/],
  ["一块详情没回来", { dropChunk: 1 }, /1 of 2 get_video chunks came back.*cannot be completed/],
  ["详情块问的不是这些搜索的 id", { wrongChunk: 0 }, /detail-0 asked for other video ids than chunk 1/],
]) {
  test(`place 拒绝不完整的组合结果：${name}`, async (t) => {
    const run = await staged(t, collection(options));
    const placed = place(run);
    assert.equal(placed.code, 2, placed.out);
    assert.match(placed.out, message);
  });
}

// place 自己取走存好的结果（没有就绪下载时 stage）：模型只存文件、跑 place，不再单独跑 static_host.py。
test("place 自己 stage 存在 host-results/ 的整份组合结果；没存时点名该存的文件", async (t) => {
  const workspace = await mkdtemp(path.join(os.tmpdir(), "kabo-ytsw-save-"));
  t.after(() => rm(workspace, { recursive: true, force: true }));
  const run = python("static_host.py", ["init", "--workspace", workspace]).out.trim();
  const file = path.join(run, "host-results", "01-collect_youtube_recent_videos.json");
  const missing = place(run);
  assert.equal(missing.code, 2, missing.out);
  assert.ok(missing.out.includes(`run place again with --id <id>`) && missing.out.includes(`save it whole to ${file} instead`),
    missing.out);
  const value = collection();
  await writeFile(file, JSON.stringify({ content: [{ type: "text", text: JSON.stringify(value) }], structuredContent: value }));
  const placed = place(run);
  assert.equal(placed.code, 0, placed.out);
  assert.deepEqual(JSON.parse(placed.out).merge, { status: "ok", search_ids: 60, detail_rows: 60, merged: 60, views_filled: 60 });
  const again = place(run);
  assert.equal(again.code, 0, again.out);
});
