// analyze-creator-competitors 的账号取数走一次后台组合调用（collect_account_history，KS9）：
// subject（主页 + 第一页；IG 解析不到时同一次调用带回候选）、select（入选账号主页 + IG 按时间窗规则翻页的 Reels，候补补位在后台）、
// content（只补还欠页的账号，通常没有）。这里用中性的小 fixture 跑驱动：核对交出的调用逐字节、
// 组合结果按记录逐项放到原来的文件名，下游快照构建一路跑到打印综合判断的输入。
// 少一次宿主往返的两个入口也在这里测：首个阶段带 --workspace 自己占运行目录（不用单独 init），
// 续跑（--placed）自己 fetch / stage 交接点名的文件（一条命令代替「每条 stage + 续跑」）；--result 仍可另指文件。
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtemp, readFile, realpath, rm, writeFile, access } from "node:fs/promises";
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

// 一个阶段交出的调用：每条 call: 行与它下面槽位行里的退路文件。
const handed = (out) => {
  const lines = out.split("\n");
  return lines.flatMap((line, i) => line.startsWith("call: ")
    ? [{ call: JSON.parse(line.slice(6)), save: lines[i + 1].split("has no download url: ")[1].slice(0, -1) }] : []);
};

const realpathOf = (dir) => realpath(dir);
const runDirOf = (out) => /^run directory: (.+)$/m.exec(out)?.[1];
// 交接末尾打印的「一条命令续跑」：去掉 cd / 环境变量前缀，只留参数。
const oneCommand = (out) => {
  const line = out.split("\n").find((l) => l.startsWith("Then run this one command"));
  const command = line.slice(line.indexOf("cd '")).replace(/( --id \d+=<id>)+$/, "");
  return [...command.slice(command.indexOf("drive.py'") + "drive.py'".length).matchAll(/'([^']*)'|(\S+)/g)]
    .map((m) => m[1] ?? m[2]);
};

// 模型只把结果原样存进交接点名的文件（绝对路径）；续跑自己取。
async function answer(runDir, item, result) {
  assert.ok(path.isAbsolute(item.save) && item.save.startsWith(runDir), item.save);
  await writeFile(item.save, JSON.stringify({ structuredContent: result }));
}

test("竞品账号取数：subject / select 各交出一次 collect_account_history（IG 的 Reels 历史随 select 一起取，content 不再交接），结果逐项落到原来的文件", async (t) => {
  const workspace = await mkdtemp(path.join(os.tmpdir(), "kabo-acc-history-"));
  t.after(() => rm(workspace, { recursive: true, force: true }));

  // subject：首个命令带 --workspace，自己占运行目录；主页 + 第一页，一次调用。
  let step = run([path.join(scripts, "drive.py"), "subject", "--workspace", workspace, "--handle", "neutral.creator"], workspace);
  assert.equal(step.code, 0, step.out);
  const runDir = runDirOf(step.out);
  assert.ok(runDir && runDir.startsWith(path.join(await realpathOf(workspace), ".kabo-runs")), step.out);
  const drive = (...stage) => run([path.join(scripts, "drive.py"), ...stage], runDir, runDir);
  let calls = handed(step.out);
  assert.deepEqual(calls.map(({ call }) => call), [{ tool: "collect_account_history", arguments: {
    platform: "instagram", accounts: [{ handle: "neutral.creator" }], profile: true,
    history: { max_pages: 1, instagram_formats: { posts: false, reels: true } }, on_identity_miss: "candidates",
    delivery: "download" } }]);
  // 一条命令续跑：存好结果后只跑这一条，它自己 stage（结果里没有就绪下载）再续跑。
  const resume = oneCommand(step.out);
  assert.deepEqual(resume, ["subject", "--workspace", workspace, "--handle", "neutral.creator", "--placed"]);
  assert.match(step.out, /not the reply/);
  await writeFile(calls[0].save, JSON.stringify({ structuredContent: composite([
    call("instagram_resolve_profile", { handle: "neutral.creator", include_post_count: true }, "acct-0", profile("neutral.creator", 1)),
    call("instagram_list_reels", { handle: "neutral.creator", user_id: "id-neutral.creator" }, "acct-0", reels("neutral.creator", 1, [2, 5, 9])),
  ], [summary("acct-0", "neutral.creator", 1)]) }));
  step = drive(...resume);
  assert.equal(step.code, 0, step.out);
  assert.match(step.out, /staged collect_account_history/);
  assert.doesNotMatch(step.out, /^run directory:/m, "the resume runs in the reserved run, never a new one");
  assert.match(step.out, /subject history: completed, 3 rows/);
  assert.equal(handed(step.out).length, 0);
  assert.equal(JSON.parse(await readFile(path.join(runDir, "subject-profile.json"), "utf8")).data.profile.username, "neutral.creator");

  // select（provided：没有候补）：入选账号的主页 + Reels 历史（content 的页数预算与时间窗规则），一次调用。
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
    history: { max_pages: 3, instagram_formats: { posts: false, reels: true }, since, until, min_items_since: 6 },
    delivery: "download" } }]);
  const first = {
    "creator.one": reels("creator.one", 11, [1, 3, 6], { cursor: "next-one" }), // 窗口未覆盖、窗内不足 6 条：还欠一页
    "creator.two": reels("creator.two", 21, [2, 4, 8]),
    "creator.three": reels("creator.three", 31, [3, 7, 12]),
  };
  await answer(runDir, calls[0], composite(accounts.flatMap((handle, i) => [
    call("instagram_resolve_profile", { handle, include_post_count: true }, `acct-${i}`, profile(handle, i + 2)),
    call("instagram_list_reels", { handle, user_id: `id-${handle}` }, `acct-${i}`, first[handle]),
    ...(handle === "creator.one" ? [call("instagram_list_reels", { handle, user_id: `id-${handle}`, max_id: "next-one" },
      `acct-${i}`, reels("creator.one", 12, [10, 40, 45]))] : []),
  ]), accounts.map((handle, i) => summary(`acct-${i}`, handle, handle === "creator.one" ? 2 : 1))));
  step = drive("discover", "--placed");
  assert.equal(step.code, 0, step.out);
  assert.equal(handed(step.out).length, 0);
  for (const handle of accounts) {
    await access(path.join(runDir, `profile-${handle}.json`));
    await access(path.join(runDir, `reels-${handle}-p1.json`));
  }

  // content：select 已按页数预算与时间窗读完历史，没有还欠页的账号，不再交接。
  await writeFile(path.join(runDir, "competitor-eligibility.json"), JSON.stringify({ direct_refs: accounts }));
  step = drive("content");
  assert.equal(step.code, 0, step.out);
  assert.equal(handed(step.out).length, 0);
  assert.match(step.out, /Next judgment: write competitor-research-synthesis\.json/);
  const second = JSON.parse(await readFile(path.join(runDir, "reels-creator.one-p2.json"), "utf8"));
  assert.equal(second.data.rows.length, 3);
  const snapshot = JSON.parse(await readFile(path.join(runDir, "public-content-snapshot.json"), "utf8"));
  assert.ok(snapshot.items.some((item) => item.content_id.includes("creator") && item.content_id.startsWith("C12")),
    "the second page's in-window Reel reaches the snapshot");
});

test("竞品主体：IG 账号解析不到时同一次调用带回候选，打印后停下等用户确认，不打印 Request schema", async (t) => {
  const workspace = await mkdtemp(path.join(os.tmpdir(), "kabo-acc-identity-"));
  t.after(() => rm(workspace, { recursive: true, force: true }));
  let step = run([path.join(scripts, "drive.py"), "subject", "--workspace", workspace, "--handle", "neutral.maker"], workspace);
  assert.equal(step.code, 0, step.out);
  const runDir = runDirOf(step.out);
  const [item] = handed(step.out);
  assert.equal(item.call.arguments.on_identity_miss, "candidates");
  const missing = { ...envelope("resolve_profile", null, 7), status: "failed", error_code: "not_found",
    summary: "No public profile.", data: undefined };
  const search = envelope("search_accounts", { rows: [
    { username: "neutral.maker.official", full_name: "Neutral Maker", follower_count: 5000, biography: "Desk tours" },
    { username: "neutralmaker_", full_name: "Neutral M", follower_count: 40 },
  ] }, 8);
  await answer(runDir, item, { ...composite([
    call("instagram_resolve_profile", { handle: "neutral.maker", include_post_count: true }, "acct-0", missing),
    call("instagram_search_accounts", { query: "neutral.maker" }, "acct-0", search),
  ], [{ group: "acct-0", input: { handle: "neutral.maker" }, resolved: { handle: "neutral.maker", id: null },
    status: "needs_identity", items_total: 0, pages: 0,
    candidates: [{ handle: "neutral.maker.official", full_name: "Neutral Maker" }, { handle: "neutralmaker_" }] }]),
    status: "partial", limitations: ["acct-0: the account did not resolve to a public profile; confirm the identity before using another account in its place."] });
  step = run([path.join(scripts, "drive.py"), "subject", "--workspace", workspace, "--handle", "neutral.maker", "--placed"], runDir, runDir);
  assert.equal(step.code, 0, step.out);
  assert.match(step.out, /@neutral\.maker did not resolve to a public Instagram profile/);
  assert.match(step.out, /@neutral\.maker\.official \| Neutral Maker\] followers=5000/);
  assert.match(step.out, /ask the user which public account they mean/);
  assert.doesNotMatch(step.out, /competitor-research-request|caption phrases:/, "no Request is written before the user confirms");
  assert.equal(handed(step.out).length, 0);
});

test("竞品主体：--result 另指一份存好的结果时照样取走（等同 --placed）；文件不存在时点名该文件", async (t) => {
  const workspace = await mkdtemp(path.join(os.tmpdir(), "kabo-acc-result-"));
  t.after(() => rm(workspace, { recursive: true, force: true }));
  let step = run([path.join(scripts, "drive.py"), "subject", "--workspace", workspace, "--handle", "neutral.creator"], workspace);
  assert.equal(step.code, 0, step.out);
  const runDir = runDirOf(step.out);
  const elsewhere = path.join(runDir, "saved-elsewhere.json");
  const subject = ["subject", "--workspace", workspace, "--handle", "neutral.creator"];
  step = run([path.join(scripts, "drive.py"), ...subject, "--result", `collect_account_history=${elsewhere}`], runDir, runDir);
  assert.equal(step.code, 2, step.out);
  assert.ok(step.out.includes(`no saved result at ${elsewhere}`), step.out);
  await writeFile(elsewhere, JSON.stringify({ structuredContent: composite([
    call("instagram_resolve_profile", { handle: "neutral.creator", include_post_count: true }, "acct-0", profile("neutral.creator", 1)),
    call("instagram_list_reels", { handle: "neutral.creator", user_id: "id-neutral.creator" }, "acct-0", reels("neutral.creator", 1, [2, 5, 9])),
  ], [summary("acct-0", "neutral.creator", 1)]) }));
  step = run([path.join(scripts, "drive.py"), ...subject, "--result", `collect_account_history=${elsewhere}`], runDir, runDir);
  assert.equal(step.code, 0, step.out);
  assert.match(step.out, /saved-elsewhere\.json: staged collect_account_history/);
  assert.match(step.out, /subject history: completed, 3 rows/);
});
