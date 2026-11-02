// 从 kabo 与 creator-skills 搬来的机械检查（对应它们 .coderabbit.yaml 与 tests/ 里的同名规则），本仓按公开仓库从严：
// 1. 供应商名：本仓整库公开（任何人都能读 raw 文件），所以扫全仓，不只扫打包内容；
// 2. 宿主专属机制：Skill 正文不写某个宿主的命令、包装器占位符或按宿主分支；
// 3. case 特化：Skill 里不出现基准用例编号、场景编号或本地 run id；
// 4. 溯源文档：Skill 包里不放「怎么从私仓蒸馏来」的源码地图；
// 5. 字节码：不提交 __pycache__ / *.pyc；
// 6. 来历标记：Skill 的 Markdown 不写版本代号（V3、V4）和日期（测量、观察日期），结论和数字照留；
// 7. 前台执行说明（KS2.4）：带脚本的 Skill 在正文开头写明在前台子 agent 中执行、不放后台。
// 需要判断的部分（品牌 / 国家关键词闸门、措辞）留给 CodeRabbit。
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const tracked = (...paths) =>
  execFileSync("git", ["ls-files", "-z", "--", ...paths], { cwd: root, encoding: "utf8" }).split("\0").filter(Boolean);
const text = (file) => readFileSync(path.join(root, file), "utf8");
const isText = (file) => !/\.(png|jpe?g|gif|webp|ico|zip|mp4|mov|pdf|woff2?)$/i.test(file);

// 供应商身份词，与 kabo、creator-skills 的 CodeRabbit「No vendor in …」规则同一份名单，另加本仓已知会出现的取数供应商。
// 本文件与 .coderabbit.yaml 要列出这些词作为规则本身，所以跳过这两个文件。
const VENDORS = /tikhu|scrape ?creators|tubelab|vidiq|\bgemini\b|printingpress|yt-dlp|youtube-pp|youtube data api|ensemble ?data|hiker ?api|creator ?db|\bapify\b|firecrawl/i;

export function vendorHits(files = tracked(".").filter(isText)) {
  const self = path.relative(root, fileURLToPath(import.meta.url));
  const hits = [];
  for (const file of files) {
    if (file === self || file === ".coderabbit.yaml") continue;
    text(file).split("\n").forEach((line, index) => {
      if (VENDORS.test(line)) hits.push(`${file}:${index + 1}: ${line.trim().slice(0, 100)}`);
    });
  }
  return hits;
}

const HOST_SPECIFIC = [
  ["host helper", /\bkabo-run-(?:dir|pipeline)\b/],
  ["wrapper report flag", /--report-file\b/],
  ["wrapper stage model", /\bPRE\/FETCH\/POST\b/],
  ["Web script tool", /\brun_bundle_(?:script|stage)\b|\bemit_block\b|--export-deliverable\b/],
  ["generic data-call tool", /\bdata_connector_(?:run|batch_run|catalog)\b|\bkabo_connector_run\b/],
  ["registry lookup", /\bregistry_skill_[a-z_]+\b/],
  ["restricted runner", /\bskill-runner\b/],
  ["host-named branch", /\b(?:Native desktop|Plugin host|Plugin pipeline host|Pipeline host|Legacy direct host|Web script-tool host)\b|\bOn (?:Web|Desktop|Codex|Claude)\b/i],
  ["host install path", /<plugin-root>|\$\{?CLAUDE_PLUGIN_ROOT\b|\bCODEX_THREAD_ID\b/],
  ["wrapper placeholder", /\{(?:skill|run|snapshot|analysis|report|owner|envelopes|language)\}|\{param\.[A-Za-z_]+\}/],
];

const CASE_SPECIFIC = [
  ["scene or case number", /\(0\d{2}[a-z]?(?:\s*\/\s*0\d{2}[a-z]?)*\)/],
  ["benchmark case id", /\b(?:COMP|TREND|KAB|REACH|ACCT|IDEA)-(?:[A-Z0-9]+-)?\d{2,}\b/],
  ["run id", /\b20\d{6}T\d{6}Z-[0-9a-f]{6,}\b/],
];

const PROVENANCE = [
  ["private-repo coordinate", /kabo-sh\/kabo@[0-9a-f]{7,}/],
  ["private implementation path", /apps\/web\/src\/app\/tools\//],
  ["provenance document", /(?:^|\/)source-composition\.md$/],
];

export function patternHits(files, rules) {
  const hits = [];
  for (const file of files) {
    for (const [label, pattern] of rules) if (pattern.test(file)) hits.push(`${file}: ${label}`);
    text(file).split("\n").forEach((line, index) => {
      for (const [label, pattern] of rules)
        if (pattern.test(line)) hits.push(`${file}:${index + 1}: ${label}: ${line.trim().slice(0, 100)}`);
    });
  }
  return hits;
}

// 日期与版本代号写的是「什么时候、哪一版发现的」，属于 PR 描述；Skill 里只留规则本身。
// schema_version 这类机器值在 JSON 里，不在 Markdown 正文里，所以只扫 .md。
const DATED = [
  ["version label", /\bV\d+\b/],
  ["date stamp", /\b20\d{2}-\d{2}-\d{2}\b/],
];

// 正文开头的这段长度：现有说明都在正文前 1,600 字符内，开头一段之后再写就起不到作用。
const FOREGROUND_WINDOW = 1600;

export function missingForegroundNote(skills = tracked("skills").filter((file) => /^skills\/[^/]+\/SKILL\.md$/.test(file))) {
  const withScripts = new Set(tracked("skills").filter((file) => /^skills\/[^/]+\/scripts\//.test(file)).map((file) => file.split("/")[1]));
  return skills.filter((file) => {
    if (!withScripts.has(file.split("/")[1])) return false;
    const body = text(file).replace(/^---\n[\s\S]*?\n---\n/, "");
    return !/foreground sub-agent/i.test(body.slice(0, FOREGROUND_WINDOW).replace(/\s+/g, " "));
  });
}

const skillProse = () => tracked("skills").filter((file) => /\.md$/.test(file));
const skillFiles = () => tracked("skills").filter((file) => /\.(md|py|mjs|js|json|ya?ml)$/.test(file));

test("全仓不出现数据供应商名（公开仓库，测试夹具与文档同样算）", () => {
  assert.deepEqual(vendorHits(), []);
});

test("Skill 正文不写宿主专属机制", () => {
  assert.deepEqual(patternHits(skillProse(), HOST_SPECIFIC), []);
});

test("Skill 里不出现基准用例编号、场景编号或本地 run id", () => {
  assert.deepEqual(patternHits(skillFiles(), CASE_SPECIFIC), []);
});

test("Skill 包里没有私仓坐标、实现路径地图或溯源文档", () => {
  assert.deepEqual(patternHits(tracked("skills"), PROVENANCE), []);
});

test("Skill 的 Markdown 不写版本代号和日期", () => {
  assert.deepEqual(patternHits(skillProse(), DATED), []);
});

test("带脚本的 Skill 在正文开头写明前台子 agent 执行", () => {
  assert.deepEqual(missingForegroundNote(), []);
});

test("不提交 Python 字节码", () => {
  assert.deepEqual(tracked(".").filter((file) => /(?:^|\/)__pycache__\/|\.pyc$/.test(file)), []);
});

test("检查本身能抓到违例", () => {
  assert.ok(VENDORS.test("provider: tikhub"));
  assert.ok(HOST_SPECIFIC.some(([, pattern]) => pattern.test("call data_connector_run with")));
  assert.ok(CASE_SPECIFIC.some(([, pattern]) => pattern.test("run 20260827T152038Z-5e90c56b")));
  assert.ok(PROVENANCE.some(([, pattern]) => pattern.test("see kabo-sh/kabo@1a2b3c4d")));
  assert.ok(DATED.some(([, pattern]) => pattern.test("# Create Script and Packaging V4")));
  assert.ok(DATED.some(([, pattern]) => pattern.test("measured 2026-09-22 on a ten-video set")));
  assert.ok(!DATED.some(([, pattern]) => pattern.test("`schema_version` is `idea-set.v3`")));
});
