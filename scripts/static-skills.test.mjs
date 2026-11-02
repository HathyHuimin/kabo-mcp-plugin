import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtemp, writeFile, readFile, rm, mkdir } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { packageFiles, checkPackage } from "./package-check.mjs";
import { deterministicZip, validatePackage } from "./openai-plugin-package.mjs";

const ids = ["analyze-content-video", "review-creator-account", "review-creator-account-deep", "create-script-and-packaging", "develop-content-ideas", "research-instagram-trends", "research-tiktok-trends", "research-youtube-trends", "analyze-creator-competitors", "analyze-tiktok-competitors", "remake-viral-video", "diagnose-reach-drop", "recommend-publish-timing", "plan-platform-monetization", "verify-platform-rule"];

async function installed(t) {
  const temp = await mkdtemp(path.join(os.tmpdir(), "kabo-static-installed-"));
  t.after(() => rm(temp, { recursive: true, force: true }));
  const files = await packageFiles();
  validatePackage(files);
  const archive = path.join(temp, "kabo.zip");
  await writeFile(archive, deterministicZip(files));
  const root = path.join(temp, "installed");
  execFileSync("unzip", ["-q", archive, "-d", root]);
  return { temp, root, files };
}

test("十五个固定 Skills 完整解包；根目录、宿主身份与唯一 MCP 一致", async t => {
  const { root, files } = await installed(t);
  const result = await checkPackage(root);
  assert.equal(result.version, JSON.parse(await readFile(new URL("../plugin.json", import.meta.url))).version);
  assert.equal(result.submissionReady, false);
  const runtime = JSON.parse(await readFile(path.join(root, "runtime-contract.json")));
  assert.deepEqual(runtime.skills, ids);
  // 目录面已收敛到插件在用的工具：必需与可选两组恰好覆盖本仓钉住的 tools/list（契约不进包），不多不少。
  const pinnedNames = JSON.parse(await readFile(new URL("../contracts/directory-mcp/tools-list.json", import.meta.url))).tools.map(tool => tool.name).sort();
  assert.equal(pinnedNames.length, 64);
  assert.deepEqual([...runtime.requiredTools, ...runtime.optionalTools].sort(), pinnedNames);
  assert.ok(runtime.optionalTools.includes("public_video_measure_media_signals"));
  assert.equal(files.some(file => /^(plugin|bin|hooks)\//.test(file.name)), false);
  for (const id of ids) assert.ok((await readFile(path.join(root, "skills", id, "SKILL.md"))).length);
  assert.deepEqual(deterministicZip(files), deterministicZip([...files].reverse()));
  await mkdir(path.join(root, "hooks"));
  await writeFile(path.join(root, "hooks/hooks.json"), "{}");
  await assert.rejects(checkPackage(root), /过滤器/);
});

// 两个宿主各读自己的市场清单，都只列仓库根这一个 kabo；Codex 市场名已被用户本机安装引用，改名会让已装条目找不到来源。
test("Claude Code 与 Codex 市场清单都指向仓库根的同一个 kabo，且不进安装包", async () => {
  const json = async name => JSON.parse(await readFile(new URL(`../${name}`, import.meta.url)));
  const manifest = await json("plugin.json");
  const claude = await json(".claude-plugin/marketplace.json");
  const codex = await json(".agents/plugins/marketplace.json");
  assert.equal(claude.name, "kabo-mcp-plugin");
  assert.equal(codex.name, "kabo-plugin-openai");
  assert.deepEqual(claude.plugins.map(entry => [entry.name, entry.source]), [[manifest.name, "./"]]);
  assert.deepEqual(codex.plugins.map(entry => [entry.name, entry.source]), [[manifest.name, { source: "local", path: "./" }]]);
  // 版本只认 plugin.json；条目再写一份会与之漂移（Claude Code 以 plugin.json 为准并告警）。
  assert.equal("version" in claude.plugins[0], false);
  assert.equal((await packageFiles()).some(file => file.name.endsWith("marketplace.json")), false);
});

// 现行品牌只有 K Fold 一张图标；旧三角形标志已退出，两个宿主的清单都指向同一文件。
test("插件图标只有 composer-icon.png，两份清单都指向它", async () => {
  const json = async name => JSON.parse(await readFile(new URL(`../${name}`, import.meta.url)));
  const ui = (await json("plugin.json")).extensions["com.openai"].interface;
  assert.equal(ui.logo, "./assets/composer-icon.png");
  assert.equal(ui.composerIcon, "./assets/composer-icon.png");
  assert.equal((await json(".claude-plugin/plugin.json")).icon, "./assets/composer-icon.png");
  assert.deepEqual((await packageFiles()).filter(file => file.name.startsWith("assets/")).map(file => file.name), ["assets/composer-icon.png"]);
  const original = await packageFiles();
  const broken = original.map(file => file.name === ".claude-plugin/plugin.json"
    ? { ...file, bytes: Buffer.from(JSON.stringify({ ...JSON.parse(file.bytes), icon: "./assets/logo.png" })) } : file);
  assert.throws(() => validatePackage(broken), /图标/);
});

test("MIT 许可随包分发，两份清单声明一致", async () => {
  const original = await packageFiles();
  assert.ok(original.some(file => file.name === "LICENSE"));
  const mismatched = original.map(file => file.name === ".claude-plugin/plugin.json"
    ? { ...file, bytes: Buffer.from(JSON.stringify({ ...JSON.parse(file.bytes), license: "Apache-2.0" })) } : file);
  assert.throws(() => validatePackage(mismatched), /同一插件身份/);
});

test("每个 Skill 自带与插件清单一致的 license，供 skills CLI 单独安装", async () => {
  const original = await packageFiles();
  const filename = "skills/develop-content-ideas/SKILL.md";
  for (const to of ["license: Apache-2.0\n", ""]) {
    const changed = original.map(file => file.name === filename ? { ...file, bytes: Buffer.from(String(file.bytes).replace("license: MIT\n", to)) } : file);
    assert.throws(() => validatePackage(changed), /license/);
  }
});

test("固定 Skills 可在本仓直接修改；缺正文、未记录目录和错误工具绑定仍被拒绝", async () => {
  const original = await packageFiles();
  const filename = "skills/review-creator-account/scripts/drive.py";
  const changed = original.map(file => file.name === filename ? { ...file, bytes: Buffer.from("print('changed')") } : file);
  validatePackage(changed);
  validatePackage(original.filter(file => file.name !== "skills/review-creator-account/manifest.json"));
  assert.throws(() => validatePackage(original.filter(file => file.name !== "skills/review-creator-account/SKILL.md")), /入口缺失|正文/);
  assert.throws(() => validatePackage([...original, { name: "skills/extra-skill/SKILL.md", bytes: Buffer.from("---\nname: extra-skill\ndescription: x\n---\nbody\n"), mode: 0o100644 }]), /未记录/);
  // Skill 已按规范删除绑定表；包内再出现与公开契约不一致的绑定表仍须拒绝。
  const bindings = "skills/review-creator-account/references/mcp-bindings.json";
  assert.throws(() => validatePackage([...original.filter(file => file.name !== bindings), { name: bindings, bytes: Buffer.from("{}"), mode: 0o100644 }]), /公开工具契约/);
  for (const edit of [
    data => { data.execution = "agent-service"; },
    data => { data.requiredTools.push("not_a_pinned_tool"); },
    data => { data.connectorTools["youtube-public/get_channel"] = "youtube_get_video"; },
    data => { data.skills.pop(); },
  ]) {
    const changed = original.map(file => {
      if (file.name !== "runtime-contract.json") return file;
      const value = JSON.parse(file.bytes); edit(value);
      return { ...file, bytes: Buffer.from(JSON.stringify(value)) };
    });
    assert.throws(() => validatePackage(changed));
  }
});

test("实际 ZIP 中的驱动不下载程序，显式交接公开工具；deep 不再查找其他 Skill", async t => {
  const { root, temp } = await installed(t);
  const scripts = path.join(root, "skills/review-creator-account/scripts");
  const run = execFileSync("python3", [path.join(scripts, "static_host.py"), "init", "--workspace", temp], { encoding: "utf8" }).trim();
  const output = execFileSync("python3", [path.join(scripts, "drive.py"), "fetch", "--platform", "youtube", "--handle", "@fixture", "--question-file", "-"], {
    encoding: "utf8", cwd: run, env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1", KABO_STATIC_RUN_DIR: run }, input: "Review public YouTube account @fixture.",
  });
  const calls = output.split("\n").filter(line => line.startsWith("call: ")).map(line => JSON.parse(line.slice(6)));
  assert.ok(calls.length > 0);
  // 交接的数据调用带 delivery:"download"：完整数据由 static_host.py fetch 下载校验。
  assert.ok(calls.every(call => call.tool.startsWith("youtube_") && Object.keys(call.arguments).join() === "parameters,delivery" && call.arguments.delivery === "download"));
  // deep 原先在宿主 skill 缓存里找视频 skill（KS4.2 / KS7.1），该脚本已删除。
  await assert.rejects(readFile(path.join(root, "skills/review-creator-account-deep/scripts/resolve_video_skill.py")));
});

// 计划帧数是请求量：provider 少返回一帧（120 请求、119 记录）时照实写进 data_note，多于计划才拒绝。
test("ACV data_note 接受少于计划的帧记录并写明请求量，多于计划仍拒绝", () => {
  const probe = `
import json, sys
sys.path.insert(0, sys.argv[1])
from creator_measurement_basis import canonical_note
def evidence(count):
    return {"content_ref": {"platform": "youtube"}, "transcript": {"status": "not_attempted"},
            "owner_metrics": {"status": "unavailable"},
            "frames": {"status": "available", "origin": "extracted_frames", "sampling_plan": {"frames": 120},
                       "items": [{"frame_id": f"f{i}", "t_seconds": i} for i in range(count)]}}
out = {}
for count in (119, 120, 121):
    try:
        out[count] = canonical_note(evidence(count), {}, {})
    except ValueError as error:
        out[count] = "ERROR " + str(error)
print(json.dumps(out))
`;
  const scripts = path.join(import.meta.dirname, "..", "skills", "analyze-content-video", "scripts");
  const out = JSON.parse(execFileSync("python3", ["-I", "-B", "-c", probe, scripts], { encoding: "utf8" }));
  assert.match(out[119], /^YouTube video; 119 extracted frames of 120 requested;/);
  assert.match(out[120], /^YouTube video; 120 extracted frames;/);
  assert.match(out[121], /^ERROR cannot derive data_note: sampling-plan frame count disagrees/);
});

// 证据原文自带量级词（转录 "27 B" / "27 billion"、caption "27B"）时，报告照写不能判成编造；
// 证据只有不相关的裸数字 27、"5 billionaires" 或 ID 片段（UUID 的 "-5b2e"）时，报告的带量级数字仍要拦。2026-10-28 Claude Code 补测 P2 实测误报。
test("ACV 数字核对接受证据里自带量级词的数字，裸数字仍不能支撑带量级的写法", () => {
  const probe = `
import json, sys
sys.path.insert(0, sys.argv[1])
from validate_creator_shape import figure_errors, source_values
evidence = {"transcript": {"cues": [{"start_s": 0.0, "text": "Qwen 3.8 27 B is a 27 billion parameter variant"}]},
            "caption": "Models like Qwen 3.8 27B run on consumer computers", "views": 1600000}
view = {"cards": [{"why_for_you": "The 27B label and the spoken 27 billion both land in the first second; 1.6M views."}]}
print(json.dumps({
    "grounded": figure_errors(view, source_values(evidence)),
    "bare": figure_errors({"cards": [{"why_for_you": "a 27B model"}]}, source_values({"steps": "27 steps"})),
    "other": figure_errors({"cards": [{"why_for_you": "a 28B model"}]}, source_values(evidence)),
    "line_break": figure_errors({"cards": [{"why_for_you": "a 27 billion model"}]}, source_values({"t": "a 27\\nbillion model"})),
    "word_tail": figure_errors({"cards": [{"why_for_you": "5B people"}]}, source_values({"t": "5 billionaires agree"})),
    "leading_dot": figure_errors({"cards": [{"why_for_you": "0.5B users"}]}, source_values({"t": "about .5 billion users"})),
    "metres": figure_errors({"cards": [{"why_for_you": "5M views"}]}, source_values({"t": "he is 5 m tall"})),
    "wan_yi": figure_errors({"cards": [{"why_for_you": "5万"}]}, source_values({"t": "市值5万亿"})),
    "wan_yi_full": figure_errors({"cards": [{"why_for_you": "5万亿"}]}, source_values({"t": "市值5万亿"})),
    "report_dot": figure_errors({"cards": [{"why_for_you": ".5B users"}]}, source_values({"t": "5 billion users"})),
    "dotted_ref": figure_errors({"cards": [{"why_for_you": "see obs.14, v1.5 and version..5"}]}, source_values({"observation_index": 14, "version": 1.5, "patch": 5})),
    "bare_dot": figure_errors({"cards": [{"why_for_you": "a .5 share"}]}, source_values({"t": "a .5 share"})),
    "identifier": figure_errors({"cards": [{"why_for_you": "5B people"}]}, source_values({"id": "7a9b2edb-d958-495d-5b2e-281111e5db11", "sha": "c5b3ff"})),
}))
`;
  const scripts = path.join(import.meta.dirname, "..", "skills", "analyze-content-video", "scripts");
  const out = JSON.parse(execFileSync("python3", ["-I", "-B", "-c", probe, scripts], { encoding: "utf8" }));
  assert.deepEqual(out.grounded, []);
  assert.equal(out.bare.length, 1);
  assert.match(out.bare[0], /states 27B, which appears nowhere/);
  assert.equal(out.other.length, 1);
  assert.match(out.other[0], /states 28B, which appears nowhere/);
  assert.deepEqual(out.line_break, []);
  assert.equal(out.word_tail.length, 1);
  assert.equal(out.identifier.length, 1);
  assert.deepEqual(out.leading_dot, []);
  assert.equal(out.metres.length, 1);
  assert.equal(out.wan_yi.length, 1);
  assert.deepEqual(out.wan_yi_full, []);
  assert.equal(out.report_dot.length, 1);
  assert.deepEqual(out.bare_dot, []);
  assert.deepEqual(out.dotted_ref, []);
});
