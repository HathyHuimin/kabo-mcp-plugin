import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";

// remake-viral-video 的 start-call 与服务端 remakeVideoLink（kabo#1836）认同一批链接。
const script = path.resolve("skills/remake-viral-video/scripts/static_host.py");

function startCall(reference, extra = ["--instagram-handle", "niallgardens"]) {
  const refArgs = reference === null ? [] : ["--reference-url", reference];
  const workspace = mkdtempSync(path.join(tmpdir(), "remake-start-"));
  const run = execFileSync("python3", [script, "init", "--workspace", workspace], { encoding: "utf8" }).trim();
  return spawnSync("python3", [script, "start-call", ...refArgs, ...extra], {
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

test("稿件模式：--script-file 原样带上口播稿，与 --reference-url 恰好给一项，不收 --language", () => {
  const dir = mkdtempSync(path.join(tmpdir(), "remake-script-"));
  const file = path.join(dir, "approved.txt");
  const text = "Plant tomatoes deep.  Water at the base — not the leaves.";
  writeFileSync(file, `${text}\n`);
  const ok = startCall(null, ["--script-file", file, "--instagram-handle", "niallgardens"]);
  assert.equal(ok.status, 0, ok.stderr);
  const args = JSON.parse(ok.stdout).arguments;
  assert.equal(args.script, text);
  assert.equal(args.reference_url, undefined);
  for (const extra of [
    ["--script-file", file, "--reference-url", "https://www.youtube.com/shorts/8u7NbUT0XW4", "--instagram-handle", "a"],
    ["--script-file", file, "--instagram-handle", "a", "--language", "en"],
  ]) {
    const refused = startCall(null, extra);
    assert.notEqual(refused.status, 0, extra.join(" "));
  }
  writeFileSync(file, "   \n");
  assert.notEqual(startCall(null, ["--script-file", file, "--instagram-handle", "a"]).status, 0);
});

test("照片当长相素材：--creator-photo 进 creator.photo_urls；只有照片时先要录音，没有才给预设音色", () => {
  const ref = ["--reference-url", "https://www.youtube.com/shorts/8u7NbUT0XW4"];
  const photo = "https://example.com/me.jpg";
  const preset = "536d3a5e000945adb7038665781a4aca";
  const ok = startCall(null, [...ref, "--creator-photo", photo, "--voice-preset", preset, "--creator-name", "me"]);
  assert.equal(ok.status, 0, ok.stderr);
  const args = JSON.parse(ok.stdout).arguments;
  assert.deepEqual(args.creator, { photo_urls: [photo] });
  assert.equal(args.voice_preset, preset);
  const mixed = startCall(null, [...ref, "--creator-photo", photo, "--creator-video", "https://www.instagram.com/reel/AbC123/", "--creator-name", "me"]);
  assert.equal(mixed.status, 0, mixed.stderr);
  assert.deepEqual(Object.keys(JSON.parse(mixed.stdout).arguments.creator).sort(), ["photo_urls", "video_urls"]);
  for (const [extra, pattern] of [
    [[...ref, "--creator-photo", photo, "--creator-name", "me"], /video of them speaking first/],
    [[...ref, "--creator-photo", photo, "--voice-preset", "nope", "--creator-name", "me"], /--voice-preset must be one of/],
    [[...ref, "--creator-photo", photo, "--creator-video", "https://www.instagram.com/reel/AbC123/", "--voice-preset", preset, "--creator-name", "me"], /only for photos/],
    [[...ref, "--creator-photo", "file:///etc/passwd", "--voice-preset", preset, "--creator-name", "me"], /public http\(s\) link/],
    [[...ref, "--creator-photo", photo, "--voice-preset", preset], /--creator-name is required/],
  ]) {
    const refused = startCall(null, extra);
    assert.notEqual(refused.status, 0, extra.join(" "));
    assert.match(refused.stderr, pattern);
  }
});
