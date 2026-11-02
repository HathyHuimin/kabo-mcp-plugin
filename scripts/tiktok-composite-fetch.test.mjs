// TikTok 两个 Skill 的取数改走后台组合工具（docs/skills-spec.md KS9）：
// research-tiktok-trends 的 discover 原先两轮交接（按意图的 sort 0 + D-1 日榜，再 sort 1 + 空榜时的 D-2）、
// rank 原先最多 12 条 list_account_posts；analyze-tiktok-competitors 的 discover 四条搜索、content 五条历史。
// 现在每个阶段交一次组合调用。这里用中性的小夹具按 kabo-composite-fetch.v1 的形状造后台结果，stage 之后
// 续跑驱动，核对：交出的入参合钉住的 inputSchema、按 group 放回原来的槽位与请求来历、下游脚本照常跑完；
// 热门的历史缺账号时照常继续（只剩绝对播放量），竞品缺账号时照旧停下。
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import Ajv from "ajv";

const root = fileURLToPath(new URL("..", import.meta.url));
const scripts = (skill) => path.join(root, "skills", skill, "scripts");
const pinned = new Map(JSON.parse(await readFile(path.join(root, "contracts/directory-mcp/tools-list.json"), "utf8")).tools.map((tool) => [tool.name, tool]));
const validate = (name) => new Ajv({ allErrors: true, strict: false, logger: false, validateFormats: false }).compile(pinned.get(name).inputSchema);
const RTT = "research-tiktok-trends";
const TTC = "analyze-tiktok-competitors";
const DAY = 86_400_000;

function attempt(args, { cwd = root, run, input } = {}) {
  try {
    const stdout = execFileSync("python3", ["-B", "-W", "ignore", ...args], {
      cwd, encoding: "utf8", input: input ?? "", stdio: ["pipe", "pipe", "pipe"],
      env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1", ...(run ? { KABO_STATIC_RUN_DIR: run } : {}) },
    });
    return { exit: 0, stdout, stderr: "" };
  } catch (error) {
    return { exit: error.status, stdout: String(error.stdout), stderr: String(error.stderr) };
  }
}

async function newRun(t, skill) {
  const temp = await mkdtemp(path.join(os.tmpdir(), "kabo-composite-"));
  t.after(() => rm(temp, { recursive: true, force: true }));
  const made = attempt([path.join(scripts(skill), "static_host.py"), "init", "--workspace", temp]);
  assert.equal(made.exit, 0, made.stderr);
  return made.stdout.trim();
}

const drive = (skill, run, args, input) => attempt([path.join(scripts(skill), "drive.py"), ...args], { cwd: run, run, input });
const callsOf = (stdout) => stdout.split("\n").filter((line) => line.startsWith("call: ")).map((line) => JSON.parse(line.slice(6)));

/** 把后台结果按 stage 落盘（下载交付不可用时的回退路径，拆分逻辑与 fetch 相同）。 */
async function stage(skill, run, tool, value) {
  const file = path.join(run, "host-results", `${tool}-${Math.random().toString(16).slice(2)}.json`);
  await writeFile(file, JSON.stringify({ structuredContent: value }));
  const staged = attempt([path.join(scripts(skill), "static_host.py"), "stage", "--tool", tool, "--input", file], { cwd: run, run });
  assert.equal(staged.exit, 0, staged.stderr);
  return JSON.parse(staged.stdout);
}

let sequence = 0;
function envelope(connector, operation, data, { status = "completed", at = new Date() } = {}) {
  sequence += 1;
  const rows = data.items ?? data.rows ?? [];
  return {
    schema_version: "1.0", connector_id: connector, operation, provider: "provider-a", status,
    summary: `Found ${rows.length} public results.`, limitations: [], credential_values_recorded: false,
    request_id: `req-${sequence}`, retrieved_at: at.toISOString(), raw_sha256: `${sequence}`.padStart(64, "0"),
    observation_refs: [], evidence_ref: `ev-${sequence}`, data,
  };
}

const AUTHORS = ["neutral.maker.a", "neutral.maker.b", "neutral.maker.c"];
/** 每个作者两条近期视频；各条搜索返回同一批（去重后共六条候选）。 */
function searchItems(keyword) {
  return AUTHORS.flatMap((handle, a) => [0, 1].map((n) => {
    const id = `74000000000000${a}${n}`;
    return {
      content_id: id, url: `https://www.tiktok.com/@${handle}/video/${id}`, creator_id: `90${a}`, creator_handle: handle,
      creator_name: handle, creator_url: `https://www.tiktok.com/@${handle}`, creator_followers: 1000 * (a + 1),
      title: `${keyword} idea ${a}-${n} #craft`, published_at: new Date(Date.now() - (3 + a + n) * DAY).toISOString(),
      views: 50_000 * (a + 1) + n, likes: 900, comments: 20, shares: 5, period_views: null, native_category_ids: [], sound: null,
    };
  }));
}

function historyRows(handle, count) {
  return Array.from({ length: count }, (_, i) => ({
    content_id: `75${AUTHORS.indexOf(handle)}${String(i).padStart(4, "0")}`, creator_id: `90${AUTHORS.indexOf(handle)}`, username: handle,
    title: `${handle} post ${i}`, published_at: new Date(Date.now() - (2 + i) * DAY).toISOString(),
    url: `https://www.tiktok.com/@${handle}/video/75${AUTHORS.indexOf(handle)}${String(i).padStart(4, "0")}`,
    format: "tiktok_video", metrics: { views: 1000 + 37 * i, likes: 40, comments: 2 },
  }));
}

/** 按后台 collectTiktokVideoCandidates 的分组与入参，替交出的调用造一份组合结果；日榜 D-1 空、D-2 有数。 */
function candidatesResult(args) {
  const calls = [];
  const searches = [];
  args.intents.forEach((intent, i) => args.sorts.forEach((sort) => {
    const parameters = { keyword: intent.keyword, sort_type: sort, count: args.count,
      ...(args.region ? { region: args.region } : {}), ...(intent.publish_time !== undefined ? { publish_time: intent.publish_time } : {}),
      ...(args.include ? { include: args.include } : {}) };
    const result = envelope("tiktok-discovery", "search_videos", { count: args.count, items: searchItems(intent.keyword), query: intent.keyword, offset: 0, has_more: false });
    calls.push({ tool: "tiktok_discovery_search_videos", parameters, group: `intent-${i}-sort-${sort}`, attempts: 1, result });
    searches.push({ group: `intent-${i}-sort-${sort}`, keyword: intent.keyword, sort_type: sort, status: "completed", rows: result.data.items.length });
  }));
  const board = [];
  if (args.top_contents) {
    let date = args.top_contents.date;
    for (let day = 0; day <= args.top_contents.fallback_days; day += 1) {
      const stamp = Math.floor(Date.parse(`${date}T00:00:00Z`) / 1000);
      const items = day === 0 ? [] : searchItems("board").slice(0, 1);
      const result = envelope("tiktok-discovery", "list_top_contents", { period_end_timestamp: stamp, ...(args.region ? { country_code: args.region } : {}), items },
        { at: new Date(Date.now() + day * 1000) });
      calls.push({ tool: "tiktok_discovery_list_top_contents", group: `top-${date}`, attempts: 1, result,
        parameters: { period_end_timestamp: stamp, period_dimension: 3, ...(args.region ? { country_code: args.region } : {}), limit: 100 } });
      board.push({ group: `top-${date}`, date, status: "completed", items: items.length });
      if (items.length) break;
      date = new Date(Date.parse(`${date}T00:00:00Z`) - DAY).toISOString().slice(0, 10);
    }
  }
  return { schema_version: "kabo-composite-fetch.v1", tool: "collect_tiktok_video_candidates", status: "completed", calls,
    summary: { searches, ...(args.top_contents ? { top_contents: board } : {}) },
    coverage: { deadline_reached: false, unfinished: [], provider_calls: calls.length }, limitations: [] };
}

/** collectAccountHistory（TikTok）：每个账号一次 list_account_posts；`skip` 里的账号模拟到点没发出（无调用）。 */
function historyResult(args, skip = []) {
  const calls = [];
  const accounts = [];
  const limitations = [];
  args.accounts.forEach((ref, i) => {
    const group = `acct-${i}`;
    if (skip.includes(ref.handle)) {
      accounts.push({ group, input: ref, resolved: { handle: ref.handle, id: null }, status: "partial", items_total: 0, pages: 0 });
      limitations.push(`${group}: not started before the time budget ran out.`);
      return;
    }
    const rows = historyRows(ref.handle, 12);
    const result = envelope("tiktok-public", "list_account_posts", { handle: ref.handle, follower_count: 1000, rows, more_available: true });
    calls.push({ tool: "tiktok_list_account_posts", parameters: { handle: `@${ref.handle}`, limit: Math.max(5, args.history.max_items) }, group, attempts: 1, result });
    accounts.push({ group, input: ref, resolved: { handle: ref.handle, id: null }, status: "completed", items_total: rows.length, pages: 1 });
  });
  return { schema_version: "kabo-composite-fetch.v1", tool: "collect_account_history", status: skip.length ? "partial" : "completed", calls,
    summary: { accounts }, coverage: { deadline_reached: skip.length > 0, unfinished: [], provider_calls: calls.length }, limitations };
}

test("research-tiktok-trends：discover 与 rank 各交一次组合调用，按 group 放回原槽位，缺历史的账号照常继续", async (t) => {
  const run = await newRun(t, RTT);
  assert.equal(drive(RTT, run, ["start"]).exit, 0);
  await writeFile(path.join(run, "trend-research-request.json"), JSON.stringify({ mode: "current_popular", niche: "bead craft ideas", platforms: ["tiktok"], locale: { region: "US", language: null, named_in_ask: "in the US" }, formats: ["tiktok_video"] }));
  await writeFile(path.join(run, "tiktok-trend-query-plan.json"), JSON.stringify({ intents: [{ search_query: "bead craft ideas" }, { search_query: "perler bead patterns" }] }));
  const discover = ["discover", "--policy", "recent_90d", "--question-file", "-"];
  const handed = drive(RTT, run, discover, "What bead craft videos are trending in the US?");
  assert.equal(handed.exit, 0, handed.stdout + handed.stderr);
  const [call, ...rest] = callsOf(handed.stdout);
  assert.equal(rest.length, 0, "discover hands over exactly one call");
  assert.equal(call.tool, "collect_tiktok_video_candidates");
  assert.ok(validate(call.tool)(call.arguments), JSON.stringify(validate(call.tool).errors));
  const d1 = new Date(Date.now() - DAY).toISOString().slice(0, 10);
  assert.deepEqual(call.arguments, {
    intents: [{ keyword: "bead craft ideas", publish_time: 90 }, { keyword: "perler bead patterns", publish_time: 90 }],
    sorts: [0, 1], count: 30, region: "US", top_contents: { date: d1, fallback_days: 1 }, delivery: "download",
  });
  await stage(RTT, run, call.tool, candidatesResult(call.arguments));

  const placed = drive(RTT, run, [...discover, "--placed"]);
  assert.equal(placed.exit, 0, placed.stdout + placed.stderr);
  assert.match(placed.stdout, /the D-1 board came back empty; the D-2 board was asked once/);
  const envelopes = JSON.parse(await readFile(path.join(run, "discovery-envelopes.json"), "utf8")).runs;
  assert.deepEqual(Object.keys(envelopes), ["intent-1-relevance", "intent-1-most-liked", "intent-2-relevance", "intent-2-most-liked", "ranked-board-d1", "ranked-board-d2"]);
  const provenance = JSON.parse(await readFile(path.join(run, "discovery-requests.json"), "utf8")).runs;
  assert.deepEqual(provenance["intent-2-most-liked"].params, { keyword: "perler bead patterns", sort_type: 1, count: 30, region: "US", publish_time: 90 });
  assert.equal(provenance["intent-2-most-liked"].request_id, envelopes["intent-2-most-liked"].request_id);
  assert.deepEqual(provenance["ranked-board-d1"].params, { period_end_timestamp: Date.parse(`${d1}T00:00:00Z`) / 1000, period_dimension: 3, country_code: "US", limit: 100 });
  assert.match(placed.stdout, /"gate_scoring"/);
  const items = JSON.parse(await readFile(path.join(run, "trend-relevance-gate-input.json"), "utf8")).items.length;
  assert.equal(items, 6);

  await writeFile(path.join(run, "trend-relevance-gate.tsv"), Array.from({ length: items }, (_, i) => `${i + 1} 90 90`).join("\n") + "\n");
  const ranked = drive(RTT, run, ["rank"]);
  assert.equal(ranked.exit, 0, ranked.stdout + ranked.stderr);
  const [history, ...more] = callsOf(ranked.stdout);
  assert.equal(more.length, 0, "rank hands over exactly one call");
  assert.equal(history.tool, "collect_account_history");
  assert.ok(validate(history.tool)(history.arguments), JSON.stringify(validate(history.tool).errors));
  assert.deepEqual(history.arguments.history, { max_items: 30 });
  assert.equal(history.arguments.platform, "tiktok");
  assert.equal(history.arguments.on_identity_miss, "gap");
  const handles = history.arguments.accounts.map((ref) => ref.handle);
  assert.deepEqual([...handles].sort(), [...AUTHORS].sort());
  // 排第二的账号到点没发出：rank 照常继续，那个账号只剩绝对播放量。
  await stage(RTT, run, history.tool, historyResult(history.arguments, [handles[1]]));
  const enriched = drive(RTT, run, ["rank", "--placed"]);
  assert.equal(enriched.exit, 0, enriched.stdout + enriched.stderr);
  assert.match(enriched.stdout, new RegExp(`no account history came back for: ${handles[1].replace(/\./g, "\\.")}`));
  const histories = JSON.parse(await readFile(path.join(run, "account-history-envelopes.json"), "utf8")).runs;
  assert.deepEqual(Object.keys(histories), [handles[0], handles[2]]);
  assert.equal(histories[handles[0]].data.handle, handles[0]);
  assert.ok(JSON.parse(await readFile(path.join(run, "public-content-analysis.json"), "utf8")));
  assert.match(enriched.stdout, /trend-research-synthesis/);
});

test("research-tiktok-trends：整份历史调用被拒（没有结果可落）时 rank --placed 照常继续", async (t) => {
  const run = await newRun(t, RTT);
  drive(RTT, run, ["start"]);
  await writeFile(path.join(run, "trend-research-request.json"), JSON.stringify({ mode: "current_popular", niche: "bead craft ideas", platforms: ["tiktok"], locale: { region: null, language: null }, formats: ["tiktok_video"] }));
  await writeFile(path.join(run, "tiktok-trend-query-plan.json"), JSON.stringify({ intents: [{ search_query: "bead craft ideas" }] }));
  const [call] = callsOf(drive(RTT, run, ["discover", "--policy", "recent_90d"]).stdout);
  assert.equal(call.arguments.region, undefined, "no region is sent when none is known");
  await stage(RTT, run, call.tool, candidatesResult(call.arguments));
  const placed = drive(RTT, run, ["discover", "--policy", "recent_90d", "--placed"]);
  assert.equal(placed.exit, 0, placed.stdout + placed.stderr);
  const items = JSON.parse(await readFile(path.join(run, "trend-relevance-gate-input.json"), "utf8")).items.length;
  await writeFile(path.join(run, "trend-relevance-gate.tsv"), Array.from({ length: items }, (_, i) => `${i + 1} 80 80`).join("\n") + "\n");
  assert.equal(callsOf(drive(RTT, run, ["rank"]).stdout).length, 1);
  const thin = drive(RTT, run, ["rank", "--placed"]);
  assert.equal(thin.exit, 0, thin.stdout + thin.stderr);
  assert.match(thin.stdout, /no account history came back for: .*those accounts keep absolute plays only/);
  assert.deepEqual(JSON.parse(await readFile(path.join(run, "account-history-envelopes.json"), "utf8")), { runs: {} });
});

test("analyze-tiktok-competitors：discover 与 content 各交一次组合调用；缺账号历史时照旧停下", async (t) => {
  const run = await newRun(t, TTC);
  assert.equal(drive(TTC, run, ["start"]).exit, 0);
  await writeFile(path.join(run, "competitor-research-request.json"), JSON.stringify({
    subject: { name: "Bead craft studio", description: "TikTok account teaching bead crafts to beginners" }, platforms: ["tiktok"],
    locale: { region: "US", language: "en" }, formats: ["tiktok_video"], discovery_mode: "auto", competitor_refs: [] }));
  await writeFile(path.join(run, "tiktok-query-plan.json"), JSON.stringify({ intents: [{ search_query: "bead craft ideas" }, { search_query: "perler bead patterns" }] }));
  const handed = drive(TTC, run, ["discover", "--region", "US"]);
  assert.equal(handed.exit, 0, handed.stdout + handed.stderr);
  const [call, ...rest] = callsOf(handed.stdout);
  assert.equal(rest.length, 0);
  assert.ok(validate(call.tool)(call.arguments), JSON.stringify(validate(call.tool).errors));
  assert.deepEqual(call.arguments, {
    intents: [{ keyword: "bead craft ideas", publish_time: 30 }, { keyword: "perler bead patterns", publish_time: 30 }],
    sorts: [0, 1], count: 30, region: "US", delivery: "download",
  });
  await stage(TTC, run, call.tool, candidatesResult(call.arguments));
  const placed = drive(TTC, run, ["discover", "--region", "US", "--placed"]);
  assert.equal(placed.exit, 0, placed.stdout + placed.stderr);
  const envelopes = JSON.parse(await readFile(path.join(run, "discovery-envelopes.json"), "utf8")).runs;
  assert.deepEqual(Object.keys(envelopes), ["intent-1-relevance", "intent-1-most-liked", "intent-2-relevance", "intent-2-most-liked"]);
  assert.equal(envelopes["intent-2-relevance"].data.query, "perler bead patterns");
  const accounts = JSON.parse(await readFile(path.join(run, "tiktok-competitor-discovery-input.json"), "utf8")).items.length;
  assert.equal(accounts, AUTHORS.length);

  await writeFile(path.join(run, "tiktok-competitor-discovery-gate.tsv"), Array.from({ length: accounts }, (_, i) => `${i + 1} direct 90 90 90 90`).join("\n") + "\n");
  const content = drive(TTC, run, ["content"]);
  assert.equal(content.exit, 0, content.stdout + content.stderr);
  const [history, ...more] = callsOf(content.stdout);
  assert.equal(more.length, 0);
  assert.equal(history.tool, "collect_account_history");
  assert.ok(validate(history.tool)(history.arguments), JSON.stringify(validate(history.tool).errors));
  assert.deepEqual({ ...history.arguments, accounts: undefined }, { platform: "tiktok", accounts: undefined, history: { max_items: 100 }, profile: false, on_identity_miss: "gap", delivery: "download" });
  assert.deepEqual([...history.arguments.accounts.map((ref) => ref.handle)].sort(), [...AUTHORS].sort());

  // 一个账号没有回应：与原先缺一条历史一样，content 停下，不拿别的账号顶替。
  await stage(TTC, run, history.tool, historyResult(history.arguments, [history.arguments.accounts[0].handle]));
  const stopped = drive(TTC, run, ["content", "--placed"]);
  assert.equal(stopped.exit, 2, stopped.stdout);
  assert.match(stopped.stdout, /no complete host record of the account histories/);

  // 后台再做一次（完整）：content 放置后跑完快照与证据投影。
  await stage(TTC, run, history.tool, historyResult(history.arguments));
  const done = drive(TTC, run, ["content", "--placed"]);
  assert.equal(done.exit, 0, done.stdout + done.stderr);
  const histories = JSON.parse(await readFile(path.join(run, "account-history-envelopes.json"), "utf8")).runs;
  assert.deepEqual(Object.keys(histories), history.arguments.accounts.map((ref) => ref.handle));
  assert.match(done.stdout, /competitor-research-synthesis/);
});
