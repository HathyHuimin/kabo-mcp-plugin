// optimize-creator-brand 与 price-creator-brand-deals 没有驱动：模型直接调一次组合取数工具（KS9），
// static_host.py 按编号取回并拆开落盘，place_composite.py 按调用顺序逐字节放进 snapshot/。两份
// place_composite.py 逐字相同，两份都在这里跑一遍；再接上各自的下游（价格的账号事实、品牌包的投影、
// 视觉规范按 source_id 放页面），确认放好的文件就是下游要的输入。不联网：走 stage 回退落盘。
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
const SKILLS = ["price-creator-brand-deals", "optimize-creator-brand"];

function python(skill, script, args, { cwd, run } = {}) {
  const result = spawnSync("python3", [path.join(scripts(skill), script), ...args], {
    cwd, encoding: "utf8",
    env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1", ...(run ? { KABO_STATIC_RUN_DIR: run } : {}) },
  });
  return { code: result.status, out: result.stdout + result.stderr };
}

async function newRun(t, skill) {
  const workspace = await mkdtemp(path.join(os.tmpdir(), `kabo-${skill}-place-`));
  t.after(() => rm(workspace, { recursive: true, force: true }));
  const { code, out } = python(skill, "static_host.py", ["init", "--workspace", workspace]);
  assert.equal(code, 0, out);
  return out.trim();
}

function envelope(connector_id, operation, data, extra = {}) {
  return {
    schema_version: "1.0", connector_id, operation, provider: "fixture-provider", status: "completed",
    summary: "ok", limitations: [], credential_values_recorded: false, request_id: `req-${operation}-${Math.random()}`,
    evidence_ref: `ev-${operation}`, raw_sha256: "a".repeat(64), retrieved_at: new Date(NOW * 1000).toISOString(), data, ...extra,
  };
}

const igRow = (code, index, reel) => ({
  code, pk: `${code}-pk`, taken_at: NOW - (index + 3) * DAY, like_count: 1000 + 37 * index, comment_count: 20 + index,
  ...(reel ? { play_count: 20000 + 900 * index, media_type: 2, product_type: "clips" } : { media_type: 1 }),
  user: { username: "neutral.creator", pk: "u1" }, caption: { text: `Fixture caption ${code}` },
});
const igPage = (prefix, count, reel) => ({ rows: Array.from({ length: count }, (_, i) => igRow(`${prefix}${i}`, i, reel)), count });

function accountComposite(extraCalls = []) {
  const list = [
    ["instagram_resolve_profile", { handle: "neutral.creator" }, envelope("instagram-discovery", "resolve_profile", {
      profile: { id: "u1", pk: "u1", username: "neutral.creator", full_name: "Neutral Creator", follower_count: 12000, media_count: 40 },
    })],
    ["instagram_list_posts", { handle: "neutral.creator" }, envelope("instagram-discovery", "list_posts", igPage("P", 8, false))],
    ["instagram_list_reels", { handle: "neutral.creator" }, envelope("instagram-discovery", "list_reels", igPage("R", 8, true))],
    ...extraCalls,
  ];
  return {
    schema_version: "kabo-composite-fetch.v1", tool: "collect_account_history", status: "completed",
    calls: list.map(([tool, parameters, result]) => ({ tool, parameters, group: "acct-0", attempts: 1, result })),
    summary: { accounts: [{ group: "acct-0", input: { handle: "neutral.creator" }, resolved: { handle: "neutral.creator", id: "u1" },
      status: "completed", items_total: 16, pages: 2 }] },
    coverage: { deadline_reached: false, unfinished: [], provider_calls: list.length, accounts: [] },
    limitations: [],
  };
}

async function stage(skill, run, tool, result) {
  const file = path.join(run, "host-results", `01-${tool}.json`);
  await writeFile(file, JSON.stringify({ content: [{ type: "text", text: JSON.stringify(result) }], structuredContent: result }));
  const staged = python(skill, "static_host.py", ["stage", "--tool", tool, "--input", file], { cwd: run, run });
  assert.equal(staged.code, 0, staged.out);
}

test("两份 place_composite.py 逐字相同", async () => {
  const [a, b] = await Promise.all(SKILLS.map((skill) => readFile(path.join(scripts(skill), "place_composite.py"))));
  assert.ok(a.equals(b));
});

for (const skill of SKILLS) {
  test(`${skill}：账号组合结果按调用顺序逐字节放进 snapshot/，失败项跳过并报出，同一份不放第二次`, async (t) => {
    const run = await newRun(t, skill);
    const failedCall = ["instagram_list_reels", { handle: "neutral.creator", cursor: "c2" },
      { ...envelope("instagram-discovery", "list_reels", undefined), status: "failed", error_code: "provider_error", summary: "failed" }];
    await stage(skill, run, "collect_account_history", accountComposite([failedCall]));
    const placed = python(skill, "place_composite.py", ["accounts"], { cwd: run, run });
    assert.equal(placed.code, 0, placed.out);
    const report = JSON.parse(placed.out);
    assert.deepEqual(report.placed.map((entry) => [entry.tool, entry.file]), [
      ["instagram_resolve_profile", "snapshot/envelope-01.json"],
      ["instagram_list_posts", "snapshot/envelope-02.json"],
      ["instagram_list_reels", "snapshot/envelope-03.json"],
    ]);
    assert.deepEqual(report.skipped.map((entry) => [entry.tool, entry.status]), [["instagram_list_reels", "failed"]]);
    assert.equal(report.summary.accounts[0].status, "completed");
    const placedProfile = JSON.parse(await readFile(path.join(run, "snapshot/envelope-01.json"), "utf8"));
    assert.equal(placedProfile.data.profile.follower_count, 12000);
    const again = python(skill, "place_composite.py", ["accounts"], { cwd: run, run });
    assert.equal(again.code, 1);
    assert.match(again.out, /no fetched collect_account_history result is waiting/);
  });
}

test("price-creator-brand-deals：放好的文件直接喂给互动率与账号事实", async (t) => {
  const skill = "price-creator-brand-deals";
  const run = await newRun(t, skill);
  await stage(skill, run, "collect_account_history", accountComposite());
  assert.equal(python(skill, "place_composite.py", ["accounts"], { cwd: run, run }).code, 0);
  const rate = python(skill, "calculate_engagement_rate.py", ["--profile-envelope", "snapshot/envelope-01.json",
    "--content-envelope", "snapshot/envelope-02.json", "--content-envelope", "snapshot/envelope-03.json",
    "--output", "analysis/engagement_rate.json"], { cwd: run, run });
  assert.equal(rate.code, 0, rate.out);
  const facts = python(skill, "derive_account_facts.py", ["--platform", "instagram", "--envelope", "snapshot/envelope-01.json",
    "--envelope", "snapshot/envelope-02.json", "--envelope", "snapshot/envelope-03.json",
    "--engagement-rate", "analysis/engagement_rate.json", "--output", "analysis/account-facts.json"], { cwd: run, run });
  assert.equal(facts.code, 0, facts.out);
  const derived = JSON.parse(await readFile(path.join(run, "analysis/account-facts.json"), "utf8"));
  assert.equal(JSON.stringify(derived).includes("12000"), true, JSON.stringify(derived));
});

test("optimize-creator-brand：品牌包从放好的文件投影账号事实", async (t) => {
  const skill = "optimize-creator-brand";
  const run = await newRun(t, skill);
  await stage(skill, run, "collect_account_history", accountComposite());
  const placed = python(skill, "place_composite.py", ["accounts", "--dir", "brand_kit/snapshot"], { cwd: run, run });
  assert.equal(placed.code, 0, placed.out);
  assert.deepEqual((await readdir(path.join(run, "brand_kit/snapshot"))).sort(),
    ["envelope-01.json", "envelope-02.json", "envelope-03.json"]);
  const projected = python(skill, "project_brand_kit_facts.py", ["--platform", "instagram", "--handle", "neutral.creator",
    "--envelope", "snapshot/envelope-01.json", "--envelope", "snapshot/envelope-02.json", "--envelope", "snapshot/envelope-03.json",
    "--output", "facts.json"], { cwd: path.join(run, "brand_kit"), run });
  assert.equal(projected.code, 0, projected.out);
  const facts = JSON.parse(await readFile(path.join(run, "brand_kit/facts.json"), "utf8"));
  assert.ok(JSON.stringify(facts).includes("resolved_public"), JSON.stringify(facts));
});

test("optimize-creator-brand：官方页组合结果按 source_id 放成 <source_id>.envelope.json", async (t) => {
  const skill = "optimize-creator-brand";
  const run = await newRun(t, skill);
  const pages = [
    ["instagram.photo_resolution", "https://help.instagram.com/1631821640426723"],
    ["tiktok.profile_setup", "https://support.tiktok.com/en/getting-started/setting-up-your-profile"],
  ];
  const result = {
    schema_version: "kabo-composite-fetch.v1", tool: "render_official_pages", status: "completed",
    calls: pages.map(([source_id, url], index) => ({
      tool: "official_page_render_page", parameters: { url }, group: `page-${index}`, attempts: 1,
      result: envelope("official-page-render", "render_page", { url, fetched_url: url, text: `Page ${source_id} text 1080 x 1080 pixels.` }),
    })),
    summary: { pages: pages.map(([source_id, url], index) => ({ group: `page-${index}`, url, source_id, status: "completed", final_url: url })) },
    coverage: { deadline_reached: false, unfinished: [] }, limitations: [],
  };
  await stage(skill, run, "render_official_pages", result);
  const placed = python(skill, "place_composite.py", ["pages", "--dir", "visual_identity_spec/snapshot"], { cwd: run, run });
  assert.equal(placed.code, 0, placed.out);
  assert.deepEqual((await readdir(path.join(run, "visual_identity_spec/snapshot"))).sort(),
    ["instagram.photo_resolution.envelope.json", "tiktok.profile_setup.envelope.json"]);
  const outside = python(skill, "place_composite.py", ["pages", "--dir", "../elsewhere"], { cwd: run, run });
  assert.equal(outside.code, 1);
});
