// review-creator-account-deep 一个请求只有一个 run（1.0.14 生产回归暴露）：模型先跑 static_host.py init、
// 按那个 run 记了一次 start_skill_run，又跑 fetch_catalog.py --workspace 另预留一个 run，同一请求被记两次用量。
// 现在 --workspace 先认领本 workspace 里刚预留、仍是空的本 Skill run；已经开始的 run 不认领。
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtemp, readdir, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const scripts = path.join(root, "skills", "review-creator-account-deep", "scripts");
const env = { ...process.env, PYTHONDONTWRITEBYTECODE: "1" };
delete env.KABO_STATIC_RUN_DIR;

const python = (script, args, cwd) => {
  const result = spawnSync("python3", [path.join(scripts, script), ...args], { cwd, encoding: "utf8", env });
  return { code: result.status, out: result.stdout + result.stderr };
};
const catalog = (workspace) =>
  python("fetch_catalog.py", ["--workspace", workspace, "--platform", "youtube", "--account", "@neutral", "--output-language", "en"], workspace);

async function workspace(t) {
  const dir = await mkdtemp(path.join(os.tmpdir(), "kabo-deep-run-"));
  t.after(() => rm(dir, { recursive: true, force: true }));
  return dir;
}

test("init 之后再跑 --workspace，认领刚预留的空 run，不另建", async (t) => {
  const dir = await workspace(t);
  const init = python("static_host.py", ["init", "--workspace", dir], dir);
  assert.equal(init.code, 0, init.out);
  const reserved = init.out.trim();
  const { code, out } = catalog(dir);
  assert.equal(code, 0, out);
  assert.equal(JSON.parse(out.slice(out.indexOf("{"))).run_directory, reserved);
  assert.equal((await readdir(path.join(dir, ".kabo-runs"))).length, 1);
});

test("已经开始的 run 不认领：另建一个", async (t) => {
  const dir = await workspace(t);
  const init = python("static_host.py", ["init", "--workspace", dir], dir);
  const started = init.out.trim();
  await writeFile(path.join(started, "account_analysis.json"), "{}");
  const { code, out } = catalog(dir);
  assert.equal(code, 0, out);
  assert.notEqual(JSON.parse(out.slice(out.indexOf("{"))).run_directory, started);
  const runs = await readdir(path.join(dir, ".kabo-runs"));
  assert.equal(runs.length, 2);
});
