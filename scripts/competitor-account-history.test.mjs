// analyze-creator-competitors 的账号取数走一次后台组合调用（collect_account_history，KS9）：
// subject（主页 + 第一页）、select（入选账号主页 + 第一页，候补补位在后台）、content（Reels 按时间窗规则翻页）
// 各交接一次。这里用中性的小 fixture 跑驱动：核对交出的调用逐字节、组合结果按记录逐项放到原来的文件名，
// 下游快照构建一路跑到打印综合判断的输入。
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile, access } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const scripts = path.join(root, "skills/analyze-creator-competitors/scripts");
const DAY = 86400;
const now = Math.floor(Date.now() / 1000);
const iso = (seconds) => new Date(seconds * 1000).toISOString().replace(/\.\d{3}Z$/, "Z");
const since = iso(now - 30 * DAY);
const until = iso(now);

const envelope = (operation, data, n) => ({
  schema_version: "1.0", connector_id: "instagram-discovery", operation, provider: "Instagram discovery",
  status: "completed", summary: `${operation} returned`, limitations: [], credential_values_recorded: false,
  evidence_ref: `ev-${operation}-${n}`, request_id: `req-${operation}-${n}`, retrieved_at: until,
  raw_sha256: String(n % 10).repeat(64), data,
});
const profile = (handle, n) => envelope("resolve_profile", {
  profile: { id: `id-${handle}`, pk: `id-${handle}`, username: handle, full_name: `Creator ${n}`,
    biography: "Short desk setup tours and tips", follower_count: 1000 * n, post_count: 40, category_name: "Creator" },
}, n);
const reels = (handle, n, ages, { cursor } = {}) => envelope("list_reels", {
  rows: ages.map((age, i) => ({ media: {
    id: `${handle}-${n}-${i}`, pk: `${handle}-${n}-${i}`, code: `C${n}x${i}${handle.replace(/\W/g, "")}`,
    taken_at: now - age * DAY, product_type: "clips", media_type: 2, video_duration: 20,
    user: { username: handle, pk: `id-${handle}` }, caption: { text: `Desk setup tour ${i}: cable trick ${n}` },
    play_count: 1000 * (i + 1), like_count: 50 * (i + 1), comment_count: 3 + i,
  } })),
  more_available: Boolean(cursor), ...(cursor ? { cursor } : {}),
}, n);
const call = (tool, parameters, group, result) => ({ tool, parameters, group, attempts: 1, result });
const composite = (calls, accounts) => ({
  schema_version: "kabo-composite-fetch.v1", tool: "collect_account_history", status: "completed", calls,
  summary: { accounts }, coverage: { deadline_reached: false, unfinished: [], provider_calls: calls.length }, limitations: [],
});
const summary = (group, handle, pages) => ({ group, input: { handle }, resolved: { handle, id: `id-${handle}` }, status: "completed", items_total: 3, pages });

function run(args, cwd, runDir) {
  const env = { ...process.env, PYTHONDONTWRITEBYTECODE: "1" };
  if (runDir) env.KABO_STATIC_RUN_DIR = runDir;
  else delete env.KABO_STATIC_RUN_DIR;
  const result = spawnSync("python3", ["-B", ...args], { cwd, env, encoding: "utf8" });
  return { code: result.status, out: (result.stdout ?? "") + (result.stderr ?? "") };
}

// 一个阶段交出的调用：每条 call: 行与它下面的 save: 文件。
const handed = (out) => {
  const lines = out.split("\n");
  return lines.flatMap((line, i) => line.startsWith("call: ")
    ? [{ call: JSON.parse(line.slice(6)), save: lines[i + 1].trim().replace(/^save: /, "") }] : []);
};

async function answer(runDir, item, result) {
  const file = path.join(runDir, item.save);
  await writeFile(file, JSON.stringify({ structuredContent: result }));
  const staged = run([path.join(scripts, "static_host.py"), "stage", "--tool", item.call.tool, "--input", file], runDir, runDir);
  assert.equal(staged.code, 0, staged.out);
}

test("竞品账号取数：subject / select / content 各交出一次 collect_account_history，结果逐项落到原来的文件", async (t) => {
  const workspace = await mkdtemp(path.join(os.tmpdir(), "kabo-acc-history-"));
  t.after(() => rm(workspace, { recursive: true, force: true }));
  const init = run([path.join(scripts, "static_host.py"), "init", "--workspace", workspace], workspace);
  assert.equal(init.code, 0, init.out);
  const runDir = init.out.trim().split("\n").at(-1);
  const drive = (...stage) => run([path.join(scripts, "drive.py"), ...stage], runDir, runDir);

  // subject：主页 + 第一页，一次调用。
  let step = drive("subject", "--handle", "neutral.creator");
  assert.equal(step.code, 0, step.out);
  let calls = handed(step.out);
  assert.deepEqual(calls.map(({ call }) => call), [{ tool: "collect_account_history", arguments: {
    platform: "instagram", accounts: [{ handle: "neutral.creator" }], profile: true,
    history: { max_pages: 1, instagram_formats: { posts: false, reels: true } }, delivery: "download" } }]);
  await answer(runDir, calls[0], composite([
    call("instagram_resolve_profile", { handle: "neutral.creator", include_post_count: true }, "acct-0", profile("neutral.creator", 1)),
    call("instagram_list_reels", { handle: "neutral.creator", user_id: "id-neutral.creator" }, "acct-0", reels("neutral.creator", 1, [2, 5, 9])),
  ], [summary("acct-0", "neutral.creator", 1)]));
  step = drive("subject", "--handle", "neutral.creator", "--placed");
  assert.equal(step.code, 0, step.out);
  assert.match(step.out, /subject history: completed, 3 rows/);
  assert.equal(handed(step.out).length, 0);
  assert.equal(JSON.parse(await readFile(path.join(runDir, "subject-profile.json"), "utf8")).data.profile.username, "neutral.creator");

  // select（provided：没有候补）：入选账号的主页 + 第一页，一次调用。
  await writeFile(path.join(runDir, "competitor-research-request.json"), JSON.stringify({
    subject: { name: "Neutral Creator", description: "Short desk setup tours", kind: "creator", account_ref: "neutral.creator" },
    platforms: ["instagram"], formats: ["reel"], discovery_mode: "provided",
    competitor_refs: ["@creator.one", "@creator.two", "@creator.three"],
    locale: { region: null, language: "en" }, window: { since, until },
  }));
  step = drive("discover");
  assert.equal(step.code, 0, step.out);
  calls = handed(step.out);
  const accounts = ["creator.one", "creator.two", "creator.three"];
  assert.deepEqual(calls.map(({ call }) => call), [{ tool: "collect_account_history", arguments: {
    platform: "instagram", accounts: accounts.map((handle) => ({ handle })), profile: true,
    history: { max_pages: 1, instagram_formats: { posts: false, reels: true } }, delivery: "download" } }]);
  const first = {
    "creator.one": reels("creator.one", 11, [1, 3, 6], { cursor: "next-one" }), // 窗口未覆盖、窗内不足 6 条：还欠一页
    "creator.two": reels("creator.two", 21, [2, 4, 8]),
    "creator.three": reels("creator.three", 31, [3, 7, 12]),
  };
  await answer(runDir, calls[0], composite(accounts.flatMap((handle, i) => [
    call("instagram_resolve_profile", { handle, include_post_count: true }, `acct-${i}`, profile(handle, i + 2)),
    call("instagram_list_reels", { handle, user_id: `id-${handle}` }, `acct-${i}`, first[handle]),
  ]), accounts.map((handle, i) => summary(`acct-${i}`, handle, 1))));
  step = drive("discover", "--placed");
  assert.equal(step.code, 0, step.out);
  assert.equal(handed(step.out).length, 0);
  for (const handle of accounts) {
    await access(path.join(runDir, `profile-${handle}.json`));
    await access(path.join(runDir, `reels-${handle}-p1.json`));
  }

  // content：只有还欠页的账号进组合调用，按 next_reel_page 的时间窗规则翻页、不取主页。
  await writeFile(path.join(runDir, "competitor-eligibility.json"), JSON.stringify({ direct_refs: accounts }));
  step = drive("content");
  assert.equal(step.code, 0, step.out);
  calls = handed(step.out);
  assert.deepEqual(calls.map(({ call }) => call), [{ tool: "collect_account_history", arguments: {
    platform: "instagram", accounts: [{ handle: "creator.one" }], profile: false,
    history: { max_pages: 3, instagram_formats: { posts: false, reels: true }, since, until, min_items_since: 6 },
    delivery: "download" } }]);
  await answer(runDir, calls[0], composite([
    call("instagram_list_reels", { handle: "creator.one" }, "acct-0", first["creator.one"]),
    call("instagram_list_reels", { handle: "creator.one", max_id: "next-one" }, "acct-0", reels("creator.one", 12, [10, 40, 45])),
  ], [summary("acct-0", "creator.one", 2)]));
  step = drive("content", "--placed");
  assert.equal(step.code, 0, step.out);
  assert.equal(handed(step.out).length, 0);
  assert.match(step.out, /Next judgment: write competitor-research-synthesis\.json/);
  const second = JSON.parse(await readFile(path.join(runDir, "reels-creator.one-p2.json"), "utf8"));
  assert.equal(second.data.rows.length, 3);
  const snapshot = JSON.parse(await readFile(path.join(runDir, "public-content-snapshot.json"), "utf8"));
  assert.ok(snapshot.items.some((item) => item.content_id.includes("creator") && item.content_id.startsWith("C12")),
    "the second page's in-window Reel reaches the snapshot");
});
