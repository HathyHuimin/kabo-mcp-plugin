// verify-platform-rule 的取页走一次后台组合调用 render_official_pages（docs/skills-spec.md KS9）：
// fetch 只交出一条 call:，组合结果经 static_host.py stage 拆开后，--placed 按 page-<i> 组与请求 URL
// 把每页绑回计划里的那一页；失败、未渲染、跳出注册主机的页面都记为 unreachable，报告照常校验通过。
// 夹具是中性的合成页面正文，只用登记表里已有的官方页面 URL。
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const scripts = path.join(root, "skills", "verify-platform-rule", "scripts");
const SOURCES = ["instagram.originality", "instagram.reels_feature"];
const FETCH = ["fetch", "--platform", "instagram", "--sources", ...SOURCES, "--language", "en"];

function python(script, args, { cwd, run, input } = {}) {
  const env = { ...process.env, PYTHONDONTWRITEBYTECODE: "1" };
  delete env.KABO_STATIC_RUN_DIR;
  if (run) env.KABO_STATIC_RUN_DIR = run;
  const result = spawnSync("python3", ["-B", path.join(scripts, script), ...args], { cwd, env, input, encoding: "utf8" });
  return { code: result.status, out: result.stdout + result.stderr };
}

const page = (url, text, extra = {}) => ({
  schema_version: "1.0", connector_id: "official-page-render", operation: "render_page", provider: "Official page render",
  status: "completed", summary: `Read ${text.length} characters.`, limitations: [], credential_values_recorded: false,
  data: { url, fetched_url: url, title: "Neutral page", text, retrieval_method: "plain_fetch", retrieved_at: "2026-11-02T08:00:00.000Z", ...extra },
  request_id: `req-${url.length}-${text.length}`, retrieved_at: "2026-11-02T08:00:00.000Z", observation_refs: [], evidence_ref: `ev-${url.length}`,
});
const failed = { schema_version: "1.0", connector_id: "official-page-render", operation: "render_page", provider: "Official page render",
  status: "failed", error_code: "service_unavailable", summary: "Page unavailable.", limitations: ["The page could not be read."], credential_values_recorded: false };

function composite(pages, { skip = [], overrides = {} } = {}) {
  const calls = [];
  pages.forEach(({ url }, index) => {
    if (skip.includes(index)) return;
    const result = overrides[index] ?? page(url, `Rule passage ${index} for this page. A second sentence follows.\n`);
    calls.push({ tool: "official_page_render_page", parameters: { url: result.requested ?? url }, group: `page-${index}`, attempts: 1,
      result: Object.fromEntries(Object.entries(result).filter(([key]) => key !== "requested")) });
  });
  return {
    schema_version: "kabo-composite-fetch.v1", tool: "render_official_pages", status: skip.length ? "partial" : "completed", calls,
    summary: { pages: pages.map(({ url, source_id }, index) => ({ group: `page-${index}`, url, ...(source_id ? { source_id } : {}),
      status: skip.includes(index) ? "not_run" : (overrides[index]?.status ?? "completed"), final_url: null })) },
    coverage: { deadline_reached: skip.length > 0, unfinished: skip.map((index) => `page-${index}`), provider_calls: calls.length },
    limitations: skip.map((index) => `page-${index}: official_page_render_page became a deferred job; its result is not included.`),
  };
}

async function handedOver(t) {
  const workspace = await mkdtemp(path.join(os.tmpdir(), "kabo-vpr-"));
  t.after(() => rm(workspace, { recursive: true, force: true }));
  const run = python("static_host.py", ["init", "--workspace", workspace]).out.trim();
  const first = python("drive.py", FETCH, { cwd: run, run });
  assert.equal(first.code, 0, first.out);
  const calls = first.out.split("\n").filter((line) => line.startsWith("call: ")).map((line) => JSON.parse(line.slice(6)));
  return { run, first, calls };
}

// 模型只把结果原样存进交接点名的文件、跑续跑命令；续跑自己 fetch（没有就绪下载时 stage）。
async function placeComposite(run, value) {
  const file = path.join(run, "host-results", "01-render_official_pages.json");
  await writeFile(file, JSON.stringify({ content: [{ type: "text", text: JSON.stringify(value) }], structuredContent: value }));
  return python("drive.py", [...FETCH, "--placed"], { cwd: run, run });
}

test("fetch 交出一条 render_official_pages：全部计划页面、带 source_id 与下载交付", async (t) => {
  const { run, first, calls } = await handedOver(t);
  assert.deepEqual(calls.map((call) => call.tool), ["render_official_pages"]);
  const { pages, delivery } = calls[0].arguments;
  assert.equal(delivery, "download");
  assert.deepEqual(pages.map((p) => p.url), [
    "https://creators.instagram.com/original-content-guidelines",
    "https://creators.instagram.com/blog/rewarding-original-creators-on-instagram",
    "https://about.instagram.com/features/reels",
    "https://creators.instagram.com/blog/tips-for-improving-your-reach",
  ]);
  // 只有登记页本身带 source_id；随源一起读的 watch 页没有。
  assert.deepEqual(pages.map((p) => p.source_id), ["instagram.originality", undefined, "instagram.reels_feature", undefined]);
  // 交接只点名存放文件（绝对路径）与一条续跑命令，不再让模型自己跑 fetch / stage。
  assert.ok(first.out.includes(`  result 01  (fallback file, only if this result has no download url: ${path.join(run, "host-results", "01-render_official_pages.json")})`), first.out);
  assert.doesNotMatch(first.out, /static_host\.py' (?:fetch|stage)/);
  assert.match(first.out, /^Then run this one command right away.*drive\.py' fetch .*--placed --id 01=<id>$/m);
  assert.match(first.out, /never call get_research_data_download/i);
});

test("组合结果按 page-<i> 绑回：读到的页面可引用，失败 / 未渲染 / 跳出主机的记为 unreachable，报告通过校验", async (t) => {
  const { run, calls } = await handedOver(t);
  const pages = calls[0].arguments.pages;
  const value = composite(pages, {
    skip: [2],
    overrides: {
      1: failed,
      3: page(pages[3].url, "Moved elsewhere.\n", { fetched_url: "https://elsewhere.example.com/moved" }),
    },
  });
  const placed = await placeComposite(run, value);
  assert.equal(placed.code, 0, placed.out);
  const input = JSON.parse(await readFile(path.join(run, "analysis", "rule-input.json"), "utf8"));
  assert.deepEqual(input.pages.map((p) => p.status), ["read", "unreachable", "unreachable", "unreachable"]);
  assert.equal(input.pages[2].reason, "The data service did not render this page in this run.");
  assert.match(input.pages[3].reason, /redirected outside its registered host/);
  assert.deepEqual(input.pages[0].passages, ["Rule passage 0 for this page.", " A second sentence follows.\n"]);
  const judgments = {
    claims: [
      { statement: "The page states the rule.", verdict: "confirmed", evidence: [{ ref: "instagram.originality:P1" }] },
      { statement: "The feature page was not checked.", verdict: "not_checked", inference: "Unread.", unreached_source: "instagram.reels_feature" },
    ],
  };
  const report = python("drive.py", ["report", "--judgments", "-"], { cwd: run, run, input: JSON.stringify(judgments) });
  assert.equal(report.code, 0, report.out);
  assert.match(report.out, /^creator_report: /m);
  const check = JSON.parse(await readFile(path.join(run, "analysis", "rule-check.json"), "utf8"));
  assert.equal(check.claims[0].evidence[0].quote, "Rule passage 0 for this page.");
  assert.deepEqual(check.unreachable_sources.map((s) => s.source_id ?? s.url).sort(),
    ["https://creators.instagram.com/blog/rewarding-original-creators-on-instagram",
      "https://creators.instagram.com/blog/tips-for-improving-your-reach", "instagram.reels_feature"].sort());
});

test("组内回答的不是计划里那一页时整轮拒绝，不改标签", async (t) => {
  const { run, calls } = await handedOver(t);
  const pages = calls[0].arguments.pages;
  const value = composite(pages, { overrides: { 0: { ...page(pages[1].url, "Wrong page.\n"), requested: pages[1].url } } });
  const placed = await placeComposite(run, value);
  assert.notEqual(placed.code, 0);
  assert.match(placed.out, /page-0 answers .* not the planned page .*no page was relabelled/);
});

test("续跑时交接点名的文件不存在：一句话点名文件与该存什么，什么都不放", async (t) => {
  const { run } = await handedOver(t);
  const placed = python("drive.py", [...FETCH, "--placed"], { cwd: run, run });
  assert.equal(placed.code, 2, placed.out);
  const file = path.join(run, "host-results", "01-render_official_pages.json");
  assert.ok(placed.out.includes("no result for slot 01 (render_official_pages). Run the resume command again with --id 01=<id>"), placed.out);
  assert.equal(existsSync(path.join(run, "analysis", "rule-input.json")), false);
});
