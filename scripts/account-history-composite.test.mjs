// review-creator-account 与 diagnose-reach-drop 的取数是一次后台组合调用 collect_account_history（KS9）：
// 驱动只交出一行 call:（入参钉住各自的样本规则），模型存下结果、static_host.py 拆开落盘，--placed 续跑时
// 驱动按调用顺序逐字节放置 snapshot/envelope-NN.json，再走原来的快照、分析与打印。这里不联网：
// 组合结果按后台对这组入参会给出的形状手写（calls[] 每个逻辑调用一项、summary.accounts[]），走 stage 回退落盘。
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const scripts = (skill) => path.join(root, "skills", skill, "scripts");
const NOW = Math.floor(Date.now() / 1000);
const DAY = 86400;

function python(skill, script, args, { cwd, run, input = "" } = {}) {
  const result = spawnSync("python3", [path.join(scripts(skill), script), ...args], {
    cwd, input, encoding: "utf8",
    env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1", ...(run ? { KABO_STATIC_RUN_DIR: run } : {}) },
  });
  return { code: result.status, out: result.stdout + result.stderr };
}

async function newRun(t, skill) {
  const workspace = await mkdtemp(path.join(os.tmpdir(), `kabo-${skill}-composite-`));
  t.after(() => rm(workspace, { recursive: true, force: true }));
  const { code, out } = python(skill, "static_host.py", ["init", "--workspace", workspace]);
  assert.equal(code, 0, out);
  return out.trim();
}

const calls = (out) => out.split("\n").filter((line) => line.startsWith("call: ")).map((line) => JSON.parse(line.slice(6)));
const resumeOf = (out) => { const line = out.split("\n").find((l) => l.startsWith("Then run this one command")); return line.slice(line.indexOf("cd '")); };

function envelope(connector_id, operation, data, extra = {}) {
  return {
    schema_version: "1.0", connector_id, operation, provider: "fixture-provider", status: "completed",
    summary: "ok", limitations: [], credential_values_recorded: false, request_id: `req-${operation}-${Math.random()}`,
    evidence_ref: `ev-${operation}`, raw_sha256: "a".repeat(64), retrieved_at: new Date(NOW * 1000).toISOString(), data, ...extra,
  };
}
const failed = (connector_id, operation, error_code) => ({
  ...envelope(connector_id, operation, undefined), status: "failed", error_code, summary: "failed",
});

const igRow = (code, index, reel) => ({
  code, pk: `${code}-pk`, taken_at: NOW - (index + 1) * 3 * DAY, like_count: 1000 + 37 * index, comment_count: 20 + index,
  ...(reel ? { play_count: 20000 + 900 * index, media_type: 2, product_type: "clips" } : { media_type: 1 }),
  user: { username: "neutral.creator", pk: "u1" }, caption: { text: `Fixture caption ${code}` },
});
const igPage = (prefix, count, reel, cursor) => ({
  rows: Array.from({ length: count }, (_, i) => igRow(`${prefix}${i}`, i + (reel ? 0 : 1), reel)),
  count, more_available: Boolean(cursor), ...(cursor ? { cursor } : {}),
});
const profile = envelope("instagram-discovery", "resolve_profile", {
  profile: { id: "u1", pk: "u1", username: "neutral.creator", full_name: "Neutral Creator", follower_count: 12000, media_count: 9 },
});

function composite(args, list, accountStatus, extra = {}) {
  const status = accountStatus === "completed" ? "completed" : accountStatus === "unavailable" ? "failed" : "partial";
  return {
    schema_version: "kabo-composite-fetch.v1", tool: "collect_account_history", status,
    calls: list.map(([tool, parameters, result]) => ({ tool, parameters, group: "acct-0", attempts: 1, result })),
    summary: { accounts: [{ group: "acct-0", input: args.accounts[0], resolved: { handle: "neutral.creator", id: null },
      status: accountStatus, items_total: 0, pages: 0, ...extra }] },
    coverage: { deadline_reached: false, unfinished: [], provider_calls: list.length, accounts: [] },
    limitations: [],
  };
}

// 模型做的事：调组合工具、把整份结果原样存进交接点名的文件（绝对路径）；之后只跑续跑命令，
// 续跑自己 fetch（这里没有就绪下载，退回 stage）。
async function answer(skill, run, fetched, result) {
  const saved = fetched.split("\n").find((line) => line.startsWith("  save: ")).slice(8);
  assert.ok(path.isAbsolute(saved) && saved.startsWith(run), saved);
  await writeFile(saved, JSON.stringify({ content: [{ type: "text", text: JSON.stringify(result) }], structuredContent: result }));
}

function resume(command, input) {
  const result = spawnSync("bash", ["-c", command], { input, encoding: "utf8", env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" } });
  return { code: result.status, out: result.stdout + result.stderr };
}

const placedOperations = async (run) => JSON.parse(await readFile(path.join(run, ".drive.json"), "utf8")).envelopes?.map((entry) => entry.operation) ?? [];

const REVIEW = "review-creator-account";
const DIAGNOSE = "diagnose-reach-drop";
const reviewFetch = (run, platform, handle, question) =>
  python(REVIEW, "drive.py", ["fetch", "--platform", platform, "--handle", handle, "--question-file", "-"], { cwd: run, run, input: question });

test("账号复盘 Instagram：一次组合调用交出 100 条合计上限与少者先翻；续跑按调用顺序逐字节放置并打印综合输入", async (t) => {
  const run = await newRun(t, REVIEW);
  const question = "How is @neutral.creator doing on Instagram lately?";
  const first = reviewFetch(run, "instagram", "neutral.creator", question);
  assert.equal(first.code, 0, first.out);
  const handed = calls(first.out);
  assert.deepEqual(handed, [{ tool: "collect_account_history", arguments: {
    platform: "instagram", accounts: [{ handle: "neutral.creator" }], profile: true,
    history: { max_items: 100, max_pages: 40, instagram_formats: { posts: true, reels: true, balance: "fewer_rows_first" } },
    on_identity_miss: "candidates", delivery: "download" } }]);
  const args = handed[0].arguments;
  const result = composite(args, [
    ["instagram_resolve_profile", { handle: "neutral.creator", include_post_count: true }, profile],
    ["instagram_list_posts", { handle: "neutral.creator" }, envelope("instagram-discovery", "list_posts", igPage("FP", 3, false, "c-feed-1"))],
    ["instagram_list_reels", { handle: "neutral.creator", user_id: "u1" }, envelope("instagram-discovery", "list_reels", igPage("RL", 4, true, null))],
    ["instagram_list_posts", { handle: "neutral.creator", max_id: "c-feed-1" }, envelope("instagram-discovery", "list_posts", igPage("FQ", 2, false, null))],
  ], "completed");
  await answer(REVIEW, run, first.out, result);
  const second = resume(resumeOf(first.out), question);
  assert.equal(second.code, 0, second.out);
  assert.match(second.out, /01-collect_account_history\.json: staged collect_account_history/);
  assert.match(second.out, /list_posts 2 page\(s\)\/5 rows, list_reels 1 page\(s\)\/4 rows; snapshot flags: --complete-operation list_posts --complete-operation list_reels/);
  assert.match(second.out, /account-review-synthesis-input\.v1/);
  assert.deepEqual(await placedOperations(run), ["resolve_profile", "list_posts", "list_reels", "list_posts"]);
  for (const [index, call] of result.calls.entries()) {
    const placed = JSON.parse(await readFile(path.join(run, `snapshot/envelope-0${index + 1}.json`), "utf8"));
    assert.deepEqual(placed, call.result);
  }
  // 成长估算这一步照旧可跑（只列可选口径，不写文件）。
  const growth = python(REVIEW, "drive.py", ["growth", "--language", "en"], { cwd: run, run });
  assert.equal(growth.code, 0, growth.out);
  assert.match(growth.out, /Growth evidence/);
  // 同一命令带 --placed 再跑：这一轮已放置，不再交接、也不重复放置。
  const again = resume(resumeOf(first.out), question);
  assert.equal(again.code, 0, again.out);
  assert.equal(calls(again.out).length, 0);
  assert.equal((await placedOperations(run)).length, 4);
});

test("账号复盘：Instagram 身份解析不到时列出候选并停下；YouTube handle 被拒提示 --channel-id；TikTok 不存在如实说", async (t) => {
  const igRun = await newRun(t, REVIEW);
  const igFirst = reviewFetch(igRun, "instagram", "neutral.creatr", "Review @neutral.creatr on Instagram.");
  const igArgs = calls(igFirst.out)[0].arguments;
  const search = envelope("instagram-discovery", "search_accounts", { rows: [
    { username: "neutral.creator", full_name: "Neutral Creator", is_verified: true }, { username: "neutral.other" }] });
  await answer(REVIEW, igRun, igFirst.out, composite(igArgs, [
    ["instagram_resolve_profile", { handle: "neutral.creatr", include_post_count: true }, failed("instagram-discovery", "resolve_profile", "not_found")],
    ["instagram_search_accounts", { query: "neutral.creatr" }, search],
  ], "needs_identity", { candidates: [{ handle: "neutral.creator" }, { handle: "neutral.other" }] }));
  const igSecond = resume(resumeOf(igFirst.out), "Review @neutral.creatr on Instagram.");
  assert.equal(igSecond.code, 0, igSecond.out);
  assert.match(igSecond.out, /did not resolve to a public Instagram profile\. Account search for 'neutral\.creatr' returned 2 account\(s\)/);
  assert.match(igSecond.out, /- @neutral\.creator \| Neutral Creator verified/);
  assert.deepEqual(await placedOperations(igRun), []);

  const ytRun = await newRun(t, REVIEW);
  const ytFirst = reviewFetch(ytRun, "youtube", "@neutral creator", "Review this YouTube channel.");
  const ytArgs = calls(ytFirst.out)[0].arguments;
  assert.deepEqual(ytArgs.history, { max_items: 100, max_pages: 2, page_size: 50 });
  assert.equal(ytArgs.on_identity_miss, "gap");
  await answer(REVIEW, ytRun, ytFirst.out, composite(ytArgs, [
    ["youtube_get_channel", { handle: "@neutral creator" }, failed("youtube-public", "get_channel", "validation_error")],
  ], "needs_identity"));
  const ytSecond = resume(resumeOf(ytFirst.out), "Review this YouTube channel.");
  assert.equal(ytSecond.code, 0, ytSecond.out);
  assert.match(ytSecond.out, /YouTube refused the handle .* --channel-id <UC… id>/);

  const ttRun = await newRun(t, REVIEW);
  const ttFirst = reviewFetch(ttRun, "tiktok", "neutral.creator", "Review @neutral.creator on TikTok.");
  const ttArgs = calls(ttFirst.out)[0].arguments;
  assert.deepEqual(ttArgs.history, { max_items: 100 });
  await answer(REVIEW, ttRun, ttFirst.out, composite(ttArgs, [
    ["tiktok_list_account_posts", { handle: "@neutral.creator", limit: 100 }, failed("tiktok-public", "list_account_posts", "not_found")],
  ], "needs_identity"));
  const ttSecond = resume(resumeOf(ttFirst.out), "Review @neutral.creator on TikTok.");
  assert.equal(ttSecond.code, 0, ttSecond.out);
  assert.match(ttSecond.out, /@neutral\.creator is not a public TikTok account/);
});

test("账号复盘：资料页临时失败不当作账号不存在；不带 --placed 重跑同一命令会重新交接", async (t) => {
  const run = await newRun(t, REVIEW);
  const question = "How is @neutral.creator doing on Instagram?";
  const first = reviewFetch(run, "instagram", "neutral.creator", question);
  await answer(REVIEW, run, first.out, composite(calls(first.out)[0].arguments, [
    ["instagram_resolve_profile", { handle: "neutral.creator", include_post_count: true }, failed("instagram-discovery", "resolve_profile", "service_unavailable")],
  ], "unavailable"));
  const second = resume(resumeOf(first.out), question);
  assert.equal(second.code, 2, second.out);
  assert.match(second.out, /says nothing about whether the account exists.*Run this same command again once, without --placed/s);
  const retry = reviewFetch(run, "instagram", "neutral.creator", question);
  assert.equal(retry.code, 0, retry.out);
  assert.equal(calls(retry.out).length, 1);
  const quota = await newRun(t, REVIEW);
  const quotaFirst = reviewFetch(quota, "instagram", "neutral.creator", question);
  await answer(REVIEW, quota, quotaFirst.out, composite(calls(quotaFirst.out)[0].arguments, [
    ["instagram_resolve_profile", { handle: "neutral.creator", include_post_count: true }, failed("instagram-discovery", "resolve_profile", "quota_exceeded")],
  ], "unavailable"));
  const stopped = resume(resumeOf(quotaFirst.out), question);
  assert.equal(stopped.code, 2);
  assert.match(stopped.out, /capability is missing.*do not run this again/s);
});

test("流量诊断：Instagram 每种格式一页（max_pages 2）、YouTube 一页 30、TikTok limit 30；失败按错误码给指引", async (t) => {
  const question = "My reach dropped on Instagram lately, why?";
  const run = await newRun(t, DIAGNOSE);
  const fetch = (r, platform, handle, q) => python(DIAGNOSE, "drive.py", ["fetch", "--platform", platform, "--handle", handle, "--language", "en", "--question-file", "-"], { cwd: r, run: r, input: q });
  const first = fetch(run, "instagram", "neutral.creator", question);
  assert.equal(first.code, 0, first.out);
  assert.deepEqual(calls(first.out), [{ tool: "collect_account_history", arguments: {
    platform: "instagram", accounts: [{ handle: "neutral.creator" }], profile: true,
    history: { max_items: 30, max_pages: 2, instagram_formats: { posts: true, reels: true, balance: "fewer_rows_first" } },
    on_identity_miss: "gap", delivery: "download" } }]);
  await answer(DIAGNOSE, run, first.out, composite(calls(first.out)[0].arguments, [
    ["instagram_resolve_profile", { handle: "neutral.creator", include_post_count: true }, profile],
    ["instagram_list_posts", { handle: "neutral.creator" }, envelope("instagram-discovery", "list_posts", igPage("FP", 6, false, "c-1"))],
    ["instagram_list_reels", { handle: "neutral.creator", user_id: "u1" }, envelope("instagram-discovery", "list_reels", igPage("RL", 8, true, "c-2"))],
  ], "partial"));
  const second = resume(resumeOf(first.out), question);
  assert.equal(second.code, 0, second.out);
  assert.match(second.out, /public evidence bounded to 30 items \(15 per format in auto mode\)/);
  assert.deepEqual(await placedOperations(run), ["resolve_profile", "list_posts", "list_reels"]);

  const yt = await newRun(t, DIAGNOSE);
  const ytFirst = fetch(yt, "youtube", "@neutral.creator", "My YouTube views dropped.");
  assert.deepEqual(calls(ytFirst.out)[0].arguments.history, { max_items: 30, max_pages: 1, page_size: 30 });

  const tt = await newRun(t, DIAGNOSE);
  const ttFirst = fetch(tt, "tiktok", "neutral.creator", "My TikTok views dropped.");
  const ttArgs = calls(ttFirst.out)[0].arguments;
  assert.deepEqual(ttArgs.history, { max_items: 30 });
  await answer(DIAGNOSE, tt, ttFirst.out, composite(ttArgs, [
    ["tiktok_list_account_posts", { handle: "@neutral.creator", limit: 30 }, failed("tiktok-public", "list_account_posts", "not_found")],
  ], "needs_identity"));
  const ttSecond = resume(resumeOf(ttFirst.out), "My TikTok views dropped.");
  assert.equal(ttSecond.code, 2, ttSecond.out);
  assert.match(ttSecond.out, /list_account_posts failed for @neutral\.creator .*confirm the exact handle with the user/s);
  assert.deepEqual((await readdir(path.join(tt, "rejected"))).length, 1);
});

test("流量诊断：续跑时交接点名的文件还没存，停下并点名该文件与该存的结果，不当作取数为空", async (t) => {
  const run = await newRun(t, DIAGNOSE);
  const question = "My reach dropped on Instagram lately, why?";
  const first = python(DIAGNOSE, "drive.py", ["fetch", "--platform", "instagram", "--handle", "neutral.creator", "--language", "en",
    "--question-file", "-"], { cwd: run, run, input: question });
  assert.equal(first.code, 0, first.out);
  const saved = first.out.split("\n").find((line) => line.startsWith("  save: ")).slice(8);
  const second = resume(resumeOf(first.out), question);
  assert.equal(second.code, 2, second.out);
  assert.ok(second.out.includes(`no saved result at ${saved} (save the complete collect_account_history result there)`), second.out);
  assert.deepEqual(await placedOperations(run), []);
  // 存好之后同一条续跑命令即可放置；再跑一次不重复取。
  await answer(DIAGNOSE, run, first.out, composite(calls(first.out)[0].arguments, [
    ["instagram_resolve_profile", { handle: "neutral.creator", include_post_count: true }, profile],
    ["instagram_list_posts", { handle: "neutral.creator" }, envelope("instagram-discovery", "list_posts", igPage("FP", 6, false, null))],
    ["instagram_list_reels", { handle: "neutral.creator", user_id: "u1" }, envelope("instagram-discovery", "list_reels", igPage("RL", 8, true, null))],
  ], "completed"));
  const third = resume(resumeOf(first.out), question);
  assert.equal(third.code, 0, third.out);
  assert.deepEqual(await placedOperations(run), ["resolve_profile", "list_posts", "list_reels"]);
});
