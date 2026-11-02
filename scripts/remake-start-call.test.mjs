import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";

// remake-viral-video 的 start-call 与服务端 remakeVideoLink（kabo#1836）认同一批链接。
const script = path.resolve("skills/remake-viral-video/scripts/static_host.py");

function startCall(reference, extra = ["--instagram-handle", "niallgardens"]) {
  const workspace = mkdtempSync(path.join(tmpdir(), "remake-start-"));
  const run = execFileSync("python3", [script, "init", "--workspace", workspace], { encoding: "utf8" }).trim();
  return spawnSync("python3", [script, "start-call", "--reference-url", reference, ...extra], {
    encoding: "utf8", env: { ...process.env, KABO_STATIC_RUN_DIR: run },
  });
}

test("TikTok 长链规范成 www 写法，短链原样带尾斜杠", () => {
  for (const [input, expected] of [
    ["https://m.tiktok.com/@some.creator/video/7694694514350361869?lang=en", "https://www.tiktok.com/@some.creator/video/7694694514350361869"],
    ["https://tiktok.com/@a/video/7694694514350361869", "https://www.tiktok.com/@a/video/7694694514350361869"],
    ["https://vm.tiktok.com/ZMabc123/", "https://vm.tiktok.com/ZMabc123/"],
    ["https://vt.tiktok.com/ZMabc123", "https://vt.tiktok.com/ZMabc123/"],
  ]) {
    const result = startCall(input);
    assert.equal(result.status, 0, result.stderr);
    assert.equal(JSON.parse(result.stdout).arguments.reference_url, expected);
  }
});

test("TikTok 图文、http 与其他平台在付费调用前就被拒", () => {
  for (const input of [
    "https://www.tiktok.com/@a/photo/7694694514350361869",
    "http://www.tiktok.com/@a/video/7694694514350361869",
    "https://www.facebook.com/reel/1234567890",
    "https://vm.tiktok.com/a/b/",
  ]) {
    const result = startCall(input);
    assert.notEqual(result.status, 0, input);
    assert.match(result.stderr, /TikTok/);
  }
});

test("同一条 TikTok 不能既当参考又当创作者素材", () => {
  const result = startCall("https://www.tiktok.com/@a/video/1234567",
    ["--creator-video", "https://m.tiktok.com/@a/video/1234567", "--creator-name", "a"]);
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /same video/);
});
