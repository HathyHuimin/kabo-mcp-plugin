import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtemp, rm, symlink } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

test("从目录别名启动校验 CLI 仍执行检查，不能静默成功", async t => {
  const temporary = await mkdtemp(path.join(os.tmpdir(), "kabo-cli-alias-"));
  t.after(() => rm(temporary, { recursive: true, force: true }));
  const alias = path.join(temporary, "plugin");
  await symlink(fileURLToPath(new URL("..", import.meta.url)), alias, "dir");
  const run = (script, ...args) => execFileSync(process.execPath,
    [path.join(alias, "scripts", script), ...args], { encoding: "utf8" });
  assert.match(run("package-check.mjs"), /submissionReady=false/);
  const findings = JSON.parse(run("skills-lint.mjs", "--json"));
  assert.equal(findings.length, 15);
});
