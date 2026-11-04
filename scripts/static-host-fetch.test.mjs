import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdir, mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const sha256 = (bytes) => createHash("sha256").update(bytes).digest("hex");

// 在同一进程里替换 urllib 的 opener：真实的 NoRedirect、状态码处理与流式校验都照常运行，
// 只有 HTTPS 收发被假处理器接管。命令行入口没有任何开关能做到这一点，测试也就不联网。
const HARNESS = String.raw`
import base64, contextlib, http.client, io, json, os, runpy, sys, urllib.request, urllib.response

script, scenarios_file = sys.argv[1], sys.argv[2]
scenarios = json.load(open(scenarios_file))
requests = []
responses = {}

class FakeHTTPS(urllib.request.HTTPSHandler):
    def https_open(self, req):
        requests.append(req.get_method())
        code, body, headers = responses[req.full_url]
        message = http.client.parse_headers(io.BytesIO(("".join(f"{k}: {v}\r\n" for k, v in headers.items()) + "\r\n").encode()))
        response = urllib.response.addinfourl(io.BytesIO(base64.b64decode(body)), message, req.full_url, code)
        response.msg = "fake"
        return response

original = urllib.request.build_opener
urllib.request.build_opener = lambda *handlers: original(*handlers, FakeHTTPS())

def run(argv, stdin=b""):
    sys.argv = [script, *argv]
    out, err = io.StringIO(), io.StringIO()
    code = 0
    sys.stdin = io.TextIOWrapper(io.BytesIO(stdin))
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            runpy.run_path(script, run_name="__main__")
        except SystemExit as exit_:
            code = exit_.code or 0
    return code, out.getvalue(), err.getvalue()

report = {}
for scenario in scenarios:
    workspace = scenario["workspace"]
    os.environ.pop("KABO_STATIC_RUN_DIR", None)
    _, run_dir, _ = run(["init", "--workspace", workspace])
    run_dir = run_dir.strip()
    os.environ["KABO_STATIC_RUN_DIR"] = run_dir
    # before：同一运行目录里先跑的命令（重复落盘等场景），它们的输出不进报告。
    for index, step in enumerate(scenario.get("before", [])):
        prior = os.path.join(run_dir, f"before-{index}.json")
        with open(prior, "w") as stream:
            json.dump(step["result"], stream)
        run([step["command"], "--tool", step["tool"], "--input", prior])
    responses.clear()
    responses.update(scenario.get("responses", {}))
    requests.clear()
    source = os.path.join(run_dir, "result.json")
    with open(source, "w") as stream:
        json.dump(scenario["result"], stream)
    argv = ([scenario["command"], "--tool", scenario["tool"], "--id", scenario["id"]] if "id" in scenario else
            [scenario["command"], "--tool", scenario["tool"], "--input", source])
    code, out, err = run(argv)
    evidence = os.path.join(run_dir, "snapshot/.host-evidence")
    files = {name: base64.b64encode(open(os.path.join(evidence, name), "rb").read()).decode()
             for name in sorted(os.listdir(evidence))}
    report[scenario["name"]] = {"exit": code, "stdout": out, "stderr": err, "files": files, "requests": list(requests), "run_dir": run_dir}
print(json.dumps(report))
`;

const skills = (await readdir(path.join(root, "skills"))).sort();
const fetchSkills = [];
for (const id of skills) {
  const text = await readFile(path.join(root, "skills", id, "scripts/static_host.py"), "utf8");
  if (text.includes("def stage(")) fetchSkills.push(id);
}

async function harness(t, skill, scenarios) {
  const temp = await mkdtemp(path.join(os.tmpdir(), "kabo-fetch-"));
  t.after(() => rm(temp, { recursive: true, force: true }));
  for (const scenario of scenarios) scenario.workspace = temp;
  const file = path.join(temp, "scenarios.json");
  await writeFile(file, JSON.stringify(scenarios));
  const output = execFileSync("python3", ["-I", "-B", "-c", HARNESS, path.join(root, "skills", skill, "scripts/static_host.py"), file], {
    encoding: "utf8",
    maxBuffer: 64 * 1024 * 1024,
  });
  return JSON.parse(output);
}

const SIGNED = "https://storage.googleapis.com/kabo-media/_temporary/research-export/v1/0f3c.json?X-Goog-Credential=CRED&X-Goog-Signature=SIGNATURE";
const secrets = ["storage.googleapis.com", "X-Goog", "SIGNATURE", "CRED", "_temporary", "creator-media/"];

function noSecrets(result) {
  for (const stream of [result.stdout, result.stderr])
    for (const secret of secrets) assert.equal(stream.includes(secret), false, `输出泄露了 ${secret}：${stream}`);
}

const envelope = {
  connector_id: "youtube-public",
  operation: "get_video",
  status: "completed",
  summary: "Found 1 video",
  limitations: [],
  evidence_ref: "ev-1",
  data: { rows: [{ id: "abc", title: "Ünïcode title" }] },
};
const { data: _data, ...inlineEnvelope } = envelope;
// 服务端的下载字节是 JSON.stringify(envelope)：紧凑、非 ASCII 原样 UTF-8。
const envelopeBytes = Buffer.from(JSON.stringify(envelope));

function downloadResult({ url = SIGNED, bytes = envelopeBytes.length, digest = sha256(envelopeBytes), result = inlineEnvelope } = {}) {
  const structured = {
    download: { status: "ready", kind: "envelope", url, expires_at: "2099-01-01T00:10:00Z", sha256: digest, bytes, content_type: "application/json" },
    result,
  };
  return { content: [{ type: "text", text: JSON.stringify(structured) }], structuredContent: structured };
}

const ok = (body = envelopeBytes) => [200, body.toString("base64"), { "Content-Type": "application/json" }];
// GCS 按签名里的 response-content-type 回类型头，产物下载的假响应照此给出。
const served = (body, type) => [200, body.toString("base64"), { "Content-Type": type }];

test("fetch：envelope 下载核对大小与 sha256 后走 stage 的同一套校验落盘；stage 拒绝只有链接的结果", async (t) => {
  const report = await harness(t, "analyze-content-video", [
    { name: "valid", command: "fetch", tool: "youtube_get_video", result: downloadResult(), responses: { [SIGNED]: ok() } },
    { name: "stageRefuses", command: "stage", tool: "youtube_get_video", result: downloadResult() },
    { name: "inlineStage", command: "stage", tool: "youtube_get_video", result: { structuredContent: { result: envelope } } },
    { name: "mismatch", command: "fetch", tool: "youtube_get_video", result: downloadResult({ result: { ...inlineEnvelope, evidence_ref: "ev-other" } }), responses: { [SIGNED]: ok() } },
  ]);
  const valid = report.valid;
  assert.equal(valid.exit, 0, valid.stderr);
  noSecrets(valid);
  assert.deepEqual(valid.requests, ["GET"]);
  const printed = JSON.parse(valid.stdout);
  assert.equal(printed.staged, 1);
  assert.deepEqual(printed.fetched, [{ kind: "envelope", sha256: sha256(envelopeBytes), bytes: envelopeBytes.length, status: "fetched" }]);
  const names = Object.keys(valid.files);
  assert.equal(names.length, 2);
  const body = Buffer.from(valid.files[names.find((name) => name.endsWith(".json"))], "base64");
  const meta = JSON.parse(Buffer.from(valid.files[names.find((name) => name.endsWith(".meta"))], "base64"));
  assert.deepEqual(JSON.parse(body), envelope);
  assert.equal(meta.sha256, sha256(body));
  assert.equal(meta.connector_id, "youtube-public");
  assert.equal(meta.operation, "get_video");
  assert.equal(meta.delivery, "download");
  assert.equal(JSON.stringify(meta).includes("storage.googleapis.com"), false);

  assert.equal(report.stageRefuses.exit, 1);
  assert.match(report.stageRefuses.stderr, /run static_host\.py fetch/);
  assert.deepEqual(report.stageRefuses.files, {});
  assert.equal(report.inlineStage.exit, 0, report.inlineStage.stderr);
  assert.equal(Object.keys(report.inlineStage.files).length, 2);
  assert.equal(report.mismatch.exit, 1);
  assert.match(report.mismatch.stderr, /does not match this tool result/);
  assert.deepEqual(report.mismatch.files, {});
});

test("fetch：sha256 不符、大小不符、非白名单主机、重定向、过期一律失败且不落盘、不复述链接", async (t) => {
  const report = await harness(t, "analyze-content-video", [
    { name: "sha", command: "fetch", tool: "youtube_get_video", result: downloadResult({ digest: sha256(Buffer.from("other")) }), responses: { [SIGNED]: ok() } },
    { name: "short", command: "fetch", tool: "youtube_get_video", result: downloadResult({ bytes: envelopeBytes.length + 5 }), responses: { [SIGNED]: ok() } },
    { name: "long", command: "fetch", tool: "youtube_get_video", result: downloadResult({ bytes: envelopeBytes.length - 1 }), responses: { [SIGNED]: ok() } },
    ...[
      "https://evil.example.com/kabo.json",
      "https://storage.googleapis.com.evil.example.com/kabo.json",
      "http://storage.googleapis.com/kabo.json",
      "https://storage.googleapis.com:8443/kabo.json",
      "https://user@storage.googleapis.com/kabo.json",
      "https://storage.googleapis.com/kabo.json\r\nX: y",
    ].map((url, index) => ({ name: `host${index}`, command: "fetch", tool: "youtube_get_video", result: downloadResult({ url }), responses: { [url]: ok() } })),
    { name: "redirect", command: "fetch", tool: "youtube_get_video", result: downloadResult(), responses: { [SIGNED]: [302, "", { Location: "https://evil.example.com/stolen" }], "https://evil.example.com/stolen": ok() } },
    { name: "expiredData", command: "fetch", tool: "youtube_get_video", result: downloadResult(), responses: { [SIGNED]: [403, "", {}] } },
    {
      name: "expiredJob",
      command: "fetch",
      tool: "get_research_data_job",
      result: {
        structuredContent: {
          download: downloadResult().structuredContent.download,
          job_id: "job-1",
          status: "completed",
          envelope: { ...inlineEnvelope, connector_id: "public-video-media", operation: "fetch_transcript" },
        },
      },
      responses: { [SIGNED]: [400, "", {}] },
    },
  ]);
  for (const [name, result] of Object.entries(report)) {
    assert.equal(result.exit, 1, `${name} 应失败：${result.stdout}`);
    assert.deepEqual(result.files, {}, `${name} 不得落盘`);
    noSecrets(result);
  }
  assert.match(report.sha.stderr, /sha256 does not match/);
  assert.match(report.short.stderr, /size does not match/);
  assert.match(report.long.stderr, /size does not match/);
  for (let index = 0; index < 6; index++) {
    assert.match(report[`host${index}`].stderr, /not a Kabo signed storage link/);
    assert.deepEqual(report[`host${index}`].requests, [], "非白名单链接不得发出请求");
  }
  assert.match(report.redirect.stderr, /redirected \(HTTP 302\)/);
  assert.deepEqual(report.redirect.requests, ["GET"], "重定向不得被跟随");
  // 同步数据结果的链接过期后按 evidence_ref 重签，不重跑取数。
  assert.match(report.expiredData.stderr, /expired.*get_research_data_download with \{"evidence_ref": "ev-1"\}.*no allowance cost/);
  assert.match(report.expiredJob.stderr, /expired.*get_research_data_job again with delivery "download" \(it only reads/);
});

test("fetch：没有就绪下载时提示回退宿主导出 + stage；排队任务提示轮询", async (t) => {
  const report = await harness(t, "analyze-content-video", [
    {
      name: "unavailable",
      command: "fetch",
      tool: "youtube_get_video",
      result: { structuredContent: { download: { status: "unavailable", reason: "too_large" }, result: envelope } },
    },
    { name: "oldServer", command: "fetch", tool: "youtube_get_video", result: { structuredContent: { result: envelope } } },
    { name: "queued", command: "fetch", tool: "get_research_data_job", result: { structuredContent: { job_id: "job-1", status: "running", envelope: null } } },
    { name: "deferred", command: "fetch", tool: "public_video_fetch_transcript", result: { structuredContent: { result: { job_id: "job-1", status: "queued" } } } },
  ]);
  for (const name of ["unavailable", "oldServer"]) {
    const result = report[name];
    assert.equal(result.exit, 0, result.stderr);
    const printed = JSON.parse(result.stdout);
    assert.equal(printed.fetched, 0);
    assert.match(printed.next, /export the complete tool result through the host and run static_host\.py stage --tool youtube_get_video/);
    assert.deepEqual(result.files, {});
    assert.deepEqual(result.requests, []);
  }
  assert.deepEqual(JSON.parse(report.unavailable.stdout).download, ["too_large"]);
  assert.equal(JSON.parse(report.oldServer.stdout).download, "absent");
  for (const name of ["queued", "deferred"]) {
    assert.equal(report[name].exit, 1);
    assert.match(report[name].stderr, /Poll the data job/);
  }
});

test("fetch：产物（文本与帧都默认走下载）逐项下载或按 _meta 配对回退正文，旁注带 job_id/object_ref/sha256，不含链接", async (t) => {
  const jpeg = Buffer.from([0xff, 0xd8, 0xff, 0xe0, 1, 2, 3, 4]);
  const vtt = Buffer.from("WEBVTT\n\n00:00.000 --> 00:01.000\nhello\n");
  const png = Buffer.from([0x89, 0x50, 0x4e, 0x47, 9, 9]);
  const urls = ["https://storage.googleapis.com/kabo-media/creator-media/job-1/epoch-1/frame-0.jpg?X-Goog-Signature=SIGNATURE", "https://storage.googleapis.com/kabo-media/creator-media/job-1/epoch-1/transcript.vtt?X-Goog-Signature=SIGNATURE"];
  const ready = (url, bytes, type) => ({ status: "ready", kind: "artifact", url, expires_at: "2099-01-01T00:10:00Z", sha256: sha256(bytes), bytes: bytes.length, content_type: type });
  const record = (ref, kind, bytes, type, download) => ({ object_ref: ref, kind, bytes: bytes.length, sha256: sha256(bytes), content_type: type, download });
  const artifacts = [
    record("creator-media/job-1/epoch-1/frame-0.jpg", "frame", jpeg, "image/jpeg", ready(urls[0], jpeg, "image/jpeg")),
    record("creator-media/job-1/epoch-1/transcript.vtt", "transcript", vtt, "text/vtt", ready(urls[1], vtt, "text/vtt")),
    record("creator-media/job-1/epoch-1/frame-1.png", "frame", png, "image/png", { status: "unavailable", reason: "service_unavailable" }),
    { object_ref: "creator-media/job-1/epoch-1/gone.jpg", kind: "frame", bytes: 3, sha256: sha256(Buffer.from("abc")), content_type: "application/octet-stream", download: { status: "unavailable", reason: "not_found" } },
  ];
  const structured = { job_id: "job-1", artifacts };
  const result = {
    structuredContent: structured,
    content: [
      { type: "text", text: JSON.stringify(structured) },
      { type: "image", data: png.toString("base64"), mimeType: "image/png", _meta: { "kabo.sh/object_ref": "creator-media/job-1/epoch-1/frame-1.png" } },
    ],
  };
  const tampered = structuredClone(result);
  tampered.structuredContent.artifacts[1].download.sha256 = sha256(Buffer.from("x"));
  tampered.structuredContent.artifacts[1].sha256 = sha256(Buffer.from("x"));
  const responses = { [urls[0]]: served(jpeg, "image/jpeg"), [urls[1]]: served(vtt, "text/vtt") };
  const report = await harness(t, "analyze-content-video", [
    { name: "artifacts", command: "fetch", tool: "read_research_data_artifacts", result, responses },
    { name: "tampered", command: "fetch", tool: "read_research_data_artifacts", result: tampered, responses },
  ]);
  const run = report.artifacts;
  assert.equal(run.exit, 0, run.stderr);
  noSecrets(run);
  const printed = JSON.parse(run.stdout);
  assert.equal(printed.staged, 3);
  assert.deepEqual(printed.fetched.map((item) => [item.index, item.status]), [[0, "fetched"], [1, "fetched"], [2, "inline"]]);
  assert.deepEqual(printed.missing, [{ index: 3, status: "unavailable", reason: "not_found" }]);
  const names = Object.keys(run.files);
  const bodyOf = (suffix) => Buffer.from(run.files[names.find((name) => name.endsWith(suffix))], "base64");
  const metaOf = (index) => JSON.parse(Buffer.from(run.files[names.find((name) => name.endsWith(`-${index}.meta`))], "base64"));
  assert.deepEqual(bodyOf("-0.jpg"), jpeg);
  assert.deepEqual(bodyOf("-1.txt"), vtt);
  assert.deepEqual(bodyOf("-2.png"), png);
  for (const [index, bytes] of [[0, jpeg], [1, vtt], [2, png]]) {
    const meta = metaOf(index);
    assert.equal(meta.job_id, "job-1");
    assert.equal(meta.object_ref, artifacts[index].object_ref);
    assert.equal(meta.sha256, sha256(bytes));
    assert.equal(meta.tool, "read_research_data_artifacts");
    assert.equal("download" in meta, false);
    assert.equal(meta.delivery, index < 2 ? "download" : undefined);
  }
  assert.equal(names.length, 6);
  // 任何一项对不上都不写入其他项。
  assert.equal(report.tampered.exit, 1);
  assert.match(report.tampered.stderr, /sha256 does not match/);
  assert.deepEqual(report.tampered.files, {});
});

// 3.1.1：帧默认走下载（Claude Code 2.1.308 把内联图片另存进自己的会话目录，导出的工具结果里没有它，stage 连续失败）。
// 帧经 fetch 落成与 stage 相同的 <id>-<i>.<ext> + .meta，后续 build_content_evidence.py 按 sha256 在 snapshot/ 下找到它们。
test("fetch：帧图片下载落成 stage 同款文件并被证据构建读到；sha256、超限、重定向、主机、类型、单项不可用与断网各自正确处理", async (t) => {
  const jpeg = Buffer.from([0xff, 0xd8, 0xff, 0xe0, 5, 6, 7, 8]);
  const png = Buffer.from([0x89, 0x50, 0x4e, 0x47, 1, 1]);
  const webp = Buffer.from("RIFF\x00\x00\x00\x00WEBPVP8 ", "latin1");
  const base = "https://storage.googleapis.com/kabo-media/creator-media/job-9/epoch-1/";
  const url = (name) => `${base}${name}?X-Goog-Signature=SIGNATURE`;
  const frame = (name, bytes, type, download = {}) => ({
    object_ref: `creator-media/job-9/epoch-1/${name}`, kind: "frame", bytes: bytes.length, sha256: sha256(bytes), content_type: type,
    download: { status: "ready", kind: "artifact", url: url(name), expires_at: "2099-01-01T00:10:00Z", sha256: sha256(bytes), bytes: bytes.length, content_type: type, ...download },
  });
  const result = (artifacts, content = []) => {
    const structured = { job_id: "job-9", artifacts };
    return { structuredContent: structured, content: [{ type: "text", text: JSON.stringify(structured) }, ...content] };
  };
  const good = [frame("f0.jpg", jpeg, "image/jpeg"), frame("f1.png", png, "image/png"), frame("f2.webp", webp, "image/webp")];
  const goodResponses = { [url("f0.jpg")]: served(jpeg, "image/jpeg"), [url("f1.png")]: served(png, "image/png"), [url("f2.webp")]: served(webp, "image/webp") };
  const one = (name, override = {}, response) => {
    const item = frame(name, jpeg, "image/jpeg", override);
    return { result: result([item]), responses: { [item.download.url]: response ?? served(jpeg, "image/jpeg") } };
  };
  const evil = "https://storage.googleapis.com.evil.example.com/f.jpg";
  const tooBig = 32 * 1024 * 1024 + 1;
  const scenarios = {
    frames: { result: result(good), responses: goodResponses },
    sha: one("sha.jpg", { sha256: sha256(Buffer.from("other")) }),
    shaBoth: (() => {
      const item = frame("shaboth.jpg", jpeg, "image/jpeg");
      item.sha256 = item.download.sha256 = sha256(Buffer.from("other"));
      return { result: result([item]), responses: { [item.download.url]: served(jpeg, "image/jpeg") } };
    })(),
    oversize: (() => {
      const item = frame("big.jpg", jpeg, "image/jpeg");
      item.bytes = item.download.bytes = tooBig;
      return { result: result([item]), responses: { [item.download.url]: served(jpeg, "image/jpeg") } };
    })(),
    longer: (() => {
      const item = frame("long.jpg", jpeg, "image/jpeg");
      return { result: result([item]), responses: { [item.download.url]: served(Buffer.concat([jpeg, Buffer.from("extra")]), "image/jpeg") } };
    })(),
    redirect: one("redir.jpg", {}, [302, "", { Location: "https://evil.example.com/stolen.jpg" }]),
    host: (() => {
      const item = frame("host.jpg", jpeg, "image/jpeg", { url: evil });
      return { result: result([item]), responses: { [evil]: served(jpeg, "image/jpeg") } };
    })(),
    svg: (() => {
      const item = frame("x.svg", jpeg, "image/svg+xml");
      return { result: result([item]), responses: { [item.download.url]: served(jpeg, "image/svg+xml") } };
    })(),
    servedType: one("type.jpg", {}, served(jpeg, "text/html")),
    recordType: (() => {
      const item = frame("record.jpg", jpeg, "image/jpeg");
      item.content_type = "image/png";
      return { result: result([item]), responses: { [item.download.url]: served(jpeg, "image/jpeg") } };
    })(),
    partial: (() => {
      const lost = { ...frame("f1.png", png, "image/png"), download: { status: "unavailable", reason: "service_unavailable" } };
      return { result: result([good[0], lost]), responses: { [url("f0.jpg")]: served(jpeg, "image/jpeg") } };
    })(),
    offline: { result: result([good[0]]), responses: {} },
  };
  const report = await harness(t, "analyze-content-video", Object.entries(scenarios).map(([name, scenario]) => ({ name, command: "fetch", tool: "read_research_data_artifacts", ...scenario })));

  const frames = report.frames;
  assert.equal(frames.exit, 0, frames.stderr);
  noSecrets(frames);
  const printed = JSON.parse(frames.stdout);
  assert.equal(printed.staged, 3);
  assert.deepEqual(frames.requests, ["GET", "GET", "GET"]);
  assert.match(printed.view, /open each image's path with the host's own file or image viewer/);
  const names = Object.keys(frames.files);
  assert.equal(names.length, 6);
  for (const [index, [bytes, extension, type]] of [[jpeg, ".jpg", "image/jpeg"], [png, ".png", "image/png"], [webp, ".webp", "image/webp"]].entries()) {
    const item = printed.fetched[index];
    assert.equal(item.status, "fetched");
    assert.ok(item.file.endsWith(`-${index}${extension}`));
    assert.equal(item.path, path.join(frames.run_dir, "snapshot/.host-evidence", item.file));
    assert.deepEqual(Buffer.from(frames.files[item.file], "base64"), bytes);
    const meta = JSON.parse(Buffer.from(frames.files[item.file.replace(extension, ".meta")], "base64"));
    assert.equal(meta.object_ref, good[index].object_ref);
    assert.equal(meta.content_type, type);
    assert.equal(meta.sha256, sha256(bytes));
    assert.equal(meta.job_id, "job-9");
    assert.equal(meta.delivery, "download");
    assert.equal(JSON.stringify(meta).includes("storage.googleapis.com"), false);
  }
  // 后续脚本不用改：build_content_evidence.py 按 sha256 在 snapshot/ 下找帧，按 frame_id 落成规范别名。
  const probe = String.raw`
import json, runpy, sys
from pathlib import Path
scripts, run = sys.argv[1], Path(sys.argv[2])
sys.path.insert(0, scripts)
module = runpy.run_path(scripts + "/build_content_evidence.py")
items = json.loads(sys.argv[3])
module["materialise_frame_aliases"]({"items": items}, run / "snapshot", run / "analysis")
print(json.dumps(sorted(path.name for path in (run / "analysis").iterdir() if path.name.startswith("frame-"))))
`;
  const items = [jpeg, png, webp].map((bytes, index) => ({ frame_id: `job-9#${index}`, sha256: sha256(bytes) }));
  const aliases = JSON.parse(execFileSync("python3", ["-I", "-B", "-c", probe, path.join(root, "skills/analyze-content-video/scripts"), frames.run_dir, JSON.stringify(items)], { encoding: "utf8" }));
  assert.deepEqual(aliases, items.map((item, index) => `frame-${sha256(Buffer.from(item.frame_id))}${[".jpg", ".png", ".webp"][index]}`).sort());

  // 任何一项校验不过都整次失败、不落盘、不复述链接。
  const failures = {
    sha: /Download does not match its artifact record/,
    shaBoth: /sha256 does not match the declared value/,
    oversize: /over the evidence limit/,
    longer: /size does not match the declared bytes/,
    redirect: /redirected \(HTTP 302\)/,
    host: /not a Kabo signed storage link/,
    svg: /Unsupported artifact content type/,
    servedType: /content type does not match the declared content_type/,
    recordType: /Download does not match its artifact record/,
  };
  for (const [name, pattern] of Object.entries(failures)) {
    const failed = report[name];
    assert.equal(failed.exit, 1, `${name} 应失败：${failed.stdout}`);
    assert.match(failed.stderr, pattern, name);
    assert.deepEqual(failed.files, {}, `${name} 不得落盘`);
    noSecrets(failed);
  }
  for (const name of ["sha", "oversize", "host", "svg", "recordType"]) assert.deepEqual(report[name].requests, [], `${name} 不得发出请求`);
  assert.deepEqual(report.redirect.requests, ["GET"], "重定向不得被跟随");

  // 单项不可用：其余照常落盘，该项指向内联重读 + stage，且明说不要从宿主自己的目录拼结果。
  const partial = report.partial;
  assert.equal(partial.exit, 0, partial.stderr);
  const partialOut = JSON.parse(partial.stdout);
  assert.equal(partialOut.staged, 1);
  assert.deepEqual(partialOut.missing, [{ index: 1, status: "unavailable", reason: "service_unavailable" }]);
  assert.match(partialOut.next, /read_research_data_artifacts again for those object_refs without delivery/);
  assert.match(partialOut.next, /never rebuild it from the host's own session or tool-result folders/);
  assert.equal(Object.keys(partial.files).length, 2);

  // 脚本不能联网：产物只能内联重读；不能指回 stage（stage 对下载模式的结果会指回 fetch）。
  const offline = report.offline;
  assert.equal(offline.exit, 1);
  assert.match(offline.stderr, /Download failed \(\w+(?:: [^)]*)?\); if this host blocks network access for scripts, call read_research_data_artifacts again for those object_refs without delivery/);
  assert.deepEqual(offline.files, {});
  noSecrets(offline);
});

test("fetch 与 stage 不互相指回：产物全部不可用时指向缺口记录与内联重读，stage 接受内联重读结果", async (t) => {
  const png = Buffer.from([0x89, 0x50, 0x4e, 0x47, 7, 7]);
  const gone = (ref, reason) => ({ object_ref: ref, kind: "frame", bytes: 3, sha256: sha256(Buffer.from("abc")), content_type: "application/octet-stream", download: { status: "unavailable", reason } });
  const allMissing = { job_id: "job-1", artifacts: [gone("creator-media/job-1/epoch-1/a.jpg", "not_found"), gone("creator-media/job-1/epoch-1/b.png", "service_unavailable")] };
  const downloadModeResult = { structuredContent: allMissing, content: [{ type: "text", text: JSON.stringify(allMissing) }] };
  const reread = { job_id: "job-1", artifacts: [{ object_ref: "creator-media/job-1/epoch-1/b.png", kind: "frame", bytes: png.length, sha256: sha256(png), content_type: "image/png" }] };
  const report = await harness(t, "analyze-content-video", [
    { name: "fetchAllMissing", command: "fetch", tool: "read_research_data_artifacts", result: downloadModeResult },
    { name: "stageAllMissing", command: "stage", tool: "read_research_data_artifacts", result: downloadModeResult },
    { name: "stageReread", command: "stage", tool: "read_research_data_artifacts", result: { structuredContent: reread, content: [{ type: "image", data: png.toString("base64"), mimeType: "image/png" }] } },
  ]);
  const fetched = report.fetchAllMissing;
  assert.equal(fetched.exit, 0, fetched.stderr);
  noSecrets(fetched);
  const printed = JSON.parse(fetched.stdout);
  assert.equal(printed.fetched, 0);
  assert.deepEqual(printed.missing.map((item) => item.reason), ["not_found", "service_unavailable"]);
  assert.match(printed.next, /not_found are missing evidence: record them as gaps/);
  assert.match(printed.next, /without delivery/);
  assert.deepEqual(fetched.files, {});
  // stage 不再要求改跑 fetch，而是给出同一条出路。
  assert.equal(report.stageAllMissing.exit, 1);
  assert.doesNotMatch(report.stageAllMissing.stderr, /run static_host\.py fetch/);
  assert.match(report.stageAllMissing.stderr, /without delivery/);
  const staged = report.stageReread;
  assert.equal(staged.exit, 0, staged.stderr);
  assert.deepEqual(Buffer.from(staged.files[Object.keys(staged.files).find((name) => name.endsWith("-0.png"))], "base64"), png);
});

test("九份 static_host.py 的共用部分逐字相同；交给模型的调用带 delivery，且钉住的 tools/list 声明了它", async (t) => {
  const tools = new Map(JSON.parse(await readFile(path.join(root, "contracts/directory-mcp/tools-list.json"), "utf8")).tools.map((tool) => [tool.name, tool]));
  const shared = new Set();
  for (const id of fetchSkills) {
    const text = await readFile(path.join(root, "skills", id, "scripts/static_host.py"), "utf8");
    shared.add(text.slice(text.indexOf("def evidence_id():")));
  }
  assert.equal(fetchSkills.length, 13);
  assert.equal(shared.size, 1, "evidence_id 之后的共用函数在各副本间不一致");
  const probe = String.raw`
import json, runpy, sys
module = runpy.run_path(sys.argv[1])
tools = module["CONNECTOR_TOOLS"]
calls = []
if "public_calls" in module:
    for key, name in tools.items():
        connector, operation = key.split("/")
        calls += module["public_calls"]({"tool": "data_connector_batch_run", "arguments": {"requests": [{"connector_id": connector, "operation": operation, "params": {"x": 1, "url": "https://www.instagram.com/reel/DeZrE2phzDx/"}}]}})
    for name in sorted(module["JOB_TOOLS"]):
        try:
            calls += module["public_calls"]({"tool": name, "arguments": {"job_id": "job-1"}})
        except ValueError:
            pass
    # 聚合工具同样带 delivery 交出，入参原样在顶层。
    for name in sorted(module.get("AGGREGATE_SCHEMAS", {})):
        calls += module["public_calls"]({"tool": name, "arguments": {"window": "x"}})
print(json.dumps({"declared": sorted(set(tools.values()) | module["JOB_TOOLS"]), "calls": calls}))
`;
  let handed = 0;
  for (const id of fetchSkills) {
    const output = JSON.parse(execFileSync("python3", ["-I", "-B", "-c", probe, path.join(root, "skills", id, "scripts/static_host.py")], { encoding: "utf8" }));
    for (const name of output.declared)
      assert.ok(tools.get(name)?.inputSchema?.properties?.delivery, `${id}：${name} 在钉住的 tools/list 里没有 delivery 入参`);
    for (const call of output.calls) {
      // 产物读取（含帧）同样带 delivery：内联图片在 Claude Code 里进不了可导出的工具结果，帧改由 fetch 落盘。
      assert.equal(call.arguments.delivery, "download", `${id}：${call.tool} 交接时缺 delivery`);
      assert.ok(tools.get(call.tool).inputSchema.properties.delivery.enum.includes("download"));
      // 交接的入参必须是钉住的 inputSchema 声明过的字段：lookup 在顶层收 post，单操作工具套 parameters。
      for (const field of Object.keys(call.arguments)) {
        if (field === "window" && call.tool.match(/^(collect|enrich)_/)) continue; // 探针的占位入参
        assert.ok(tools.get(call.tool).inputSchema.properties[field], `${id}：${call.tool} 的入参没有 ${field}`);
      }
      handed++;
    }
  }
  assert.ok(handed > 20);
});

// 聚合工具的 delivery 只多给一份供脚本下载的完整副本，items 照旧内联。
// 下载字节是去掉 download 的整份出参，fetch 按 schema_version 判形状；stage 也接受，落盘前去掉 download。
const candidateSet = {
  candidate_set_id: "cs-1",
  schema_version: "trend-candidate-set.v1",
  status: "completed",
  platform: "instagram",
  window: { since: "2026-09-22T00:00:00Z", until: "2026-10-22T00:00:00Z" },
  items: [{ content_key: "instagram:DeZrE2phzDx", caption: "Ünïcode caption", semantic_scores: { topic: 80 } }],
  coverage: { routes: 2 },
  stop_reason: "budget",
  limitations: [],
};
const candidateBytes = Buffer.from(JSON.stringify(candidateSet));

function aggregateResult({ url = SIGNED, bytes = candidateBytes.length, digest = sha256(candidateBytes), expires = "2099-01-01T00:10:00Z", inline = candidateSet, download } = {}) {
  const structured = {
    download: download ?? { status: "ready", kind: "envelope", url, expires_at: expires, sha256: digest, bytes, content_type: "application/json" },
    ...inline,
  };
  return { content: [{ type: "text", text: JSON.stringify(structured) }], structuredContent: structured };
}

test("聚合工具 fetch：副本核对后按 schema_version 落盘；sha256 不符、超限、重定向、非白名单主机、过期一律不落盘；unavailable 与就绪结果都能 stage 内联", async (t) => {
  const tool = "collect_instagram_trend_candidates";
  // 另一个聚合工具的出参（补全结果）交在采集工具名下：身份字段自洽，只有 schema_version 对不上。
  const otherSet = { ...candidateSet, schema_version: "trend-enrichment.v1" };
  const otherBytes = Buffer.from(JSON.stringify(otherSet));
  const report = await harness(t, "research-instagram-trends", [
    { name: "valid", command: "fetch", tool, result: aggregateResult(), responses: { [SIGNED]: ok(candidateBytes) } },
    { name: "sha", command: "fetch", tool, result: aggregateResult({ digest: sha256(Buffer.from("other")) }), responses: { [SIGNED]: ok(candidateBytes) } },
    { name: "overLimit", command: "fetch", tool, result: aggregateResult({ bytes: 32 * 1024 * 1024 + 1 }), responses: { [SIGNED]: ok(candidateBytes) } },
    { name: "short", command: "fetch", tool, result: aggregateResult({ bytes: candidateBytes.length + 9 }), responses: { [SIGNED]: ok(candidateBytes) } },
    { name: "redirect", command: "fetch", tool, result: aggregateResult(), responses: { [SIGNED]: [307, "", { Location: "https://evil.example.com/x" }], "https://evil.example.com/x": ok(candidateBytes) } },
    { name: "host", command: "fetch", tool, result: aggregateResult({ url: "https://storage.googleapis.com.evil.example.com/x.json" }), responses: { "https://storage.googleapis.com.evil.example.com/x.json": ok(candidateBytes) } },
    { name: "expired", command: "fetch", tool, result: aggregateResult({ expires: "2020-01-01T00:00:00Z" }), responses: { [SIGNED]: ok(candidateBytes) } },
    { name: "gcsExpired", command: "fetch", tool, result: aggregateResult(), responses: { [SIGNED]: [403, "", {}] } },
    { name: "otherSchema", command: "fetch", tool, result: aggregateResult({ inline: otherSet, digest: sha256(otherBytes), bytes: otherBytes.length }), responses: { [SIGNED]: ok(otherBytes) } },
    { name: "mismatch", command: "fetch", tool, result: aggregateResult({ inline: { ...candidateSet, status: "partial" } }), responses: { [SIGNED]: ok(candidateBytes) } },
    { name: "unavailable", command: "fetch", tool, result: aggregateResult({ download: { status: "unavailable", reason: "too_large" } }) },
    { name: "stageUnavailable", command: "stage", tool, result: aggregateResult({ download: { status: "unavailable", reason: "too_large" } }) },
    { name: "stageReady", command: "stage", tool, result: aggregateResult() },
  ]);
  const valid = report.valid;
  assert.equal(valid.exit, 0, valid.stderr);
  noSecrets(valid);
  assert.deepEqual(valid.requests, ["GET"]);
  const bodyOf = (result) => JSON.parse(Buffer.from(result.files[Object.keys(result.files).find((name) => name.endsWith(".json"))], "base64"));
  const metaOf = (result) => JSON.parse(Buffer.from(result.files[Object.keys(result.files).find((name) => name.endsWith(".meta"))], "base64"));
  assert.deepEqual(bodyOf(valid), candidateSet);
  assert.equal(metaOf(valid).schema_version, "trend-candidate-set.v1");
  assert.equal(metaOf(valid).delivery, "download");
  assert.equal("connector_id" in metaOf(valid), false);

  for (const name of ["sha", "overLimit", "short", "redirect", "host", "expired", "gcsExpired", "otherSchema", "mismatch"]) {
    assert.equal(report[name].exit, 1, `${name} 应失败：${report[name].stdout}`);
    assert.deepEqual(report[name].files, {}, `${name} 不得落盘`);
    noSecrets(report[name]);
  }
  assert.match(report.sha.stderr, /sha256 does not match/);
  assert.match(report.overLimit.stderr, /over the evidence limit/);
  assert.deepEqual(report.overLimit.requests, []);
  assert.match(report.short.stderr, /size does not match/);
  assert.match(report.redirect.stderr, /redirected \(HTTP 307\)/);
  assert.deepEqual(report.redirect.requests, ["GET"], "重定向不得被跟随");
  assert.match(report.host.stderr, /not a Kabo signed storage link/);
  assert.deepEqual(report.host.requests, []);
  // 过期判定两条：expires_at 已过就不发请求；GCS 回 403。聚合工具的内联结果本来完整，指向导出 + stage，不重调。
  assert.match(report.expired.stderr, /expires_at has passed.*inline result is complete.*stage --tool collect_instagram_trend_candidates/);
  assert.deepEqual(report.expired.requests, []);
  assert.match(report.gcsExpired.stderr, /expired or was refused \(HTTP 403\).*calling the tool again would re-run it/);
  assert.match(report.otherSchema.stderr, /declared schema/);
  assert.match(report.mismatch.stderr, /does not match this tool result/);

  assert.equal(report.unavailable.exit, 0, report.unavailable.stderr);
  assert.match(JSON.parse(report.unavailable.stdout).next, /run static_host\.py stage --tool collect_instagram_trend_candidates/);
  // 不可用与就绪两种结果的内联 items 都完整：stage 落下的就是去掉 download 的整份出参，不含签名链接。
  for (const name of ["stageUnavailable", "stageReady"]) {
    const staged = report[name];
    assert.equal(staged.exit, 0, staged.stderr);
    assert.deepEqual(bodyOf(staged), candidateSet, name);
    assert.equal(Object.values(staged.files).some((body) => Buffer.from(body, "base64").includes("storage.googleapis.com")), false);
  }
});

test("链接过期：expires_at 已过不发请求；同步数据按 evidence_ref 指向重签，任务再读，lookup 再调原工具", async (t) => {
  const channel = { ...inlineEnvelope, connector_id: "youtube-public", operation: "get_channel", evidence_ref: "ev-9" };
  const past = "2020-01-01T00:00:00Z";
  const withExpiry = (value, expires) => {
    value.structuredContent.download.expires_at = expires;
    value.content = [{ type: "text", text: JSON.stringify(value.structuredContent) }];
    return value;
  };
  const report = await harness(t, "analyze-creator-competitors", [
    { name: "sync", command: "fetch", tool: "youtube_get_channel", result: withExpiry(downloadResult({ result: channel }), past), responses: { [SIGNED]: ok() } },
    { name: "syncNoRef", command: "fetch", tool: "youtube_get_channel", result: downloadResult({ result: { ...channel, evidence_ref: undefined } }), responses: { [SIGNED]: [400, "", {}] } },
    // 重签拿回的链接也会过期：再重签一次；退路指回信封作答路线所属的原数据工具，不是重签工具本身。
    { name: "resign", command: "fetch", tool: "get_research_data_download", result: withExpiry(downloadResult({ result: channel }), past), responses: { [SIGNED]: ok() } },
    { name: "resignNoRef", command: "fetch", tool: "get_research_data_download", result: downloadResult({ result: { ...channel, evidence_ref: undefined } }), responses: { [SIGNED]: [400, "", {}] } },
    { name: "resignProjection", command: "fetch", tool: "get_research_data_download", result: downloadResult({ result: { ...channel, connector_id: "instagram-detail-proxy", operation: "get_post" } }), responses: { [SIGNED]: [403, "", {}] } },
    {
      name: "lookup",
      command: "fetch",
      tool: "lookup_instagram_post",
      result: downloadResult({ result: { ...inlineEnvelope, connector_id: "instagram-discovery", operation: "get_post", evidence_ref: "ev-lookup" } }),
      responses: { [SIGNED]: [403, "", {}] },
    },
    {
      name: "job",
      command: "fetch",
      tool: "get_research_data_job",
      result: { structuredContent: { download: { ...downloadResult().structuredContent.download, expires_at: past }, job_id: "job-1", status: "completed", envelope: { ...inlineEnvelope, connector_id: "youtube-public", operation: "get_channel" } } },
      responses: { [SIGNED]: ok() },
    },
  ]);
  for (const [name, result] of Object.entries(report)) {
    assert.equal(result.exit, 1, `${name} 应失败：${result.stdout}`);
    assert.deepEqual(result.files, {});
    noSecrets(result);
  }
  assert.match(report.sync.stderr, /expires_at has passed.*get_research_data_download with \{"evidence_ref": "ev-9"\}.*new sha256 and bytes/);
  assert.deepEqual(report.sync.requests, [], "已过期的链接不发请求");
  // 重签本身失败（not_found / service_unavailable）时，退回再调原工具（会重跑），已收费的结果不就此丢掉。
  assert.match(report.sync.stderr, /If get_research_data_download itself fails, call youtube_get_channel again with the same parameters and delivery "download" \(this re-runs it\)/);
  assert.match(report.syncNoRef.stderr, /HTTP 400.*youtube_get_channel again with the same parameters/);
  // 重签工具的入参只有 evidence_ref：退路必须是原数据工具，绝不能是「带 delivery 再调 get_research_data_download」。
  assert.match(report.resign.stderr, /expires_at has passed.*get_research_data_download with \{"evidence_ref": "ev-9"\}.*If get_research_data_download itself fails, call youtube_get_channel again with the same parameters and delivery "download" \(this re-runs it\)/);
  assert.deepEqual(report.resign.requests, []);
  assert.match(report.resignNoRef.stderr, /HTTP 400.*Call youtube_get_channel again with the same parameters/);
  assert.match(report.resignProjection.stderr, /HTTP 403.*Call lookup_instagram_post again with the same parameters/);
  for (const name of ["resign", "resignNoRef", "resignProjection"]) {
    assert.doesNotMatch(report[name].stderr, /call get_research_data_download again/i, name);
  }
  // lookup 的重签结果是作答路线的原始信封、不是投影：只能再调 lookup。
  assert.match(report.lookup.stderr, /lookup_instagram_post again with the same parameters/);
  assert.doesNotMatch(report.lookup.stderr, /get_research_data_download/);
  assert.match(report.job.stderr, /expires_at has passed.*get_research_data_job again with delivery "download" \(it only reads/);
  assert.deepEqual(report.job.requests, []);
});

test("重签工具 get_research_data_download：下载核对后按原操作落盘；签不出时 stage 内联信封；未声明的操作与投影路线被拒", async (t) => {
  const channel = { ...envelope, schema_version: "1.0", connector_id: "youtube-public", operation: "get_channel", evidence_ref: "ev-9" };
  const channelBytes = Buffer.from(JSON.stringify(channel));
  const { data: _channelData, ...inlineChannel } = channel;
  const tool = "get_research_data_download";
  const report = await harness(t, "analyze-creator-competitors", [
    { name: "valid", command: "fetch", tool, result: downloadResult({ result: inlineChannel, digest: sha256(channelBytes), bytes: channelBytes.length }), responses: { [SIGNED]: ok(channelBytes) } },
    { name: "sha", command: "fetch", tool, result: downloadResult({ result: inlineChannel, bytes: channelBytes.length }), responses: { [SIGNED]: ok(channelBytes) } },
    { name: "unavailable", command: "stage", tool, result: { structuredContent: { download: { status: "unavailable", reason: "service_unavailable" }, result: channel } } },
    { name: "undeclared", command: "stage", tool, result: { structuredContent: { download: { status: "unavailable", reason: "too_large" }, result: { ...channel, connector_id: "tiktok-discovery", operation: "list_comments" } } } },
    { name: "projection", command: "stage", tool, result: { structuredContent: { download: { status: "unavailable", reason: "too_large" }, result: { ...channel, connector_id: "instagram-detail-proxy", operation: "get_post" } } } },
    { name: "expired", command: "fetch", tool, result: downloadResult({ result: inlineChannel }), responses: { [SIGNED]: [403, "", {}] } },
  ]);
  const valid = report.valid;
  assert.equal(valid.exit, 0, valid.stderr);
  noSecrets(valid);
  const meta = JSON.parse(Buffer.from(valid.files[Object.keys(valid.files).find((name) => name.endsWith(".meta"))], "base64"));
  assert.deepEqual([meta.tool, meta.connector_id, meta.operation, meta.delivery], [tool, "youtube-public", "get_channel", "download"]);
  assert.equal(report.sha.exit, 1);
  assert.match(report.sha.stderr, /sha256 does not match/);
  assert.deepEqual(report.sha.files, {});
  assert.equal(report.unavailable.exit, 0, report.unavailable.stderr);
  assert.equal(Object.keys(report.unavailable.files).length, 2);
  assert.equal(report.undeclared.exit, 1);
  assert.match(report.undeclared.stderr, /not from one of this Skill's data tools/);
  assert.equal(report.projection.exit, 1);
  assert.match(report.projection.stderr, /raw envelope, not the projection; call lookup_instagram_post again/);
  // 重签链接再过期：再重签一次，仍按新的 sha256 / bytes 校验。
  assert.match(report.expired.stderr, /get_research_data_download with \{"evidence_ref": "ev-9"\}/);
});

// 2026-10-22 Codex 0.163 实测：模型把 youtube_get_video 的完整 MCP 结果存进 snapshot/，fetch 正常暂存，
// persist_envelope.py 却报 "no complete host record"——统计「已放置」时把原始结果里的 envelope
// 当成已放置，扣掉了唯一的暂存候选。下面在真实运行目录里逐份副本验证：原始结果放在哪里都不算放置，
// 真正放置过的仍然去重。暂存文件按 static_host.py store 的格式直接写入，不经网络。
// 九份副本按内容分组，每种内容测一份：逐字相同的副本行为相同，哪份改出了差异就自成一组被测到。
const persistCopies = new Map();
for (const id of skills) {
  const text = await readFile(path.join(root, "skills", id, "scripts/persist_envelope.py"), "utf8").catch(() => null);
  if (text !== null) persistCopies.set(id, text);
}
const persistVariants = [...new Map([...persistCopies].map(([id, text]) => [sha256(text), { id, drained: text.includes("def drained_candidates(") }])).values()];

async function persistRun(t, skill) {
  const temp = await mkdtemp(path.join(os.tmpdir(), "kabo-persist-"));
  t.after(() => rm(temp, { recursive: true, force: true }));
  const scripts = path.join(root, "skills", skill, "scripts");
  const env = { ...process.env, PYTHONDONTWRITEBYTECODE: "1" };
  delete env.KABO_STATIC_RUN_DIR;
  const run = execFileSync("python3", ["-B", path.join(scripts, "static_host.py"), "init", "--workspace", temp], { encoding: "utf8", env }).trim();
  let serial = 0;
  return {
    run,
    async stage(body, meta) {
      const bytes = Buffer.from(JSON.stringify(body));
      const name = path.join(run, "snapshot/.host-evidence", `${Date.now()}${String(++serial).padStart(4, "0")}`);
      await writeFile(`${name}.json`, bytes);
      await writeFile(`${name}.meta`, JSON.stringify({ sha256: sha256(bytes), tool: "test", staged_at: new Date(Date.now() + serial).toISOString(), ...meta }));
      return bytes;
    },
    async save(relative, value) {
      await mkdir(path.dirname(path.join(run, relative)), { recursive: true });
      await writeFile(path.join(run, relative), typeof value === "string" ? value : JSON.stringify(value));
    },
    persist(...args) {
      const result = spawnSync("python3", ["-B", path.join(scripts, "persist_envelope.py"), ...args], {
        cwd: run, encoding: "utf8", env: { ...env, KABO_STATIC_RUN_DIR: run },
      });
      return { exit: result.status, out: result.stdout + result.stderr };
    },
    read: (relative) => readFile(path.join(run, relative)),
  };
}

const fullVideo = (n) => ({
  ...envelope, schema_version: "1.0", raw_sha256: sha256(Buffer.from(`raw-${n}`)), request_id: `req-${n}`, evidence_ref: `ev-${n}`,
  data: { rows: [{ id: `video-${n}`, title: "Ünïcode title" }] },
});
const withoutData = ({ data: _unused, ...rest }) => rest;
const mcp = (structured, extra = {}) => ({ content: [{ type: "text", text: JSON.stringify(structured) }], structuredContent: structured, ...extra });
const VIDEO = ["--connector", "youtube-public", "--operation", "get_video"];
const VIDEO_META = { connector_id: "youtube-public", operation: "get_video", delivery: "download" };

test("persist：宿主导出的原始工具结果放在 snapshot/、host-results/ 或运行目录根都不算已放置；放置过的照旧去重", async (t) => {
  assert.equal(persistCopies.size, 13);
  assert.equal(persistVariants.length, 3);
  const raw = (full) => downloadResult({ result: withoutData(full) });
  const placements = [
    ["snapshot/tool-metadata-initial.json", raw],
    ["host-results/01-youtube_get_video.json", raw],
    ["tool-metadata-initial.json", raw],
    // 去掉 MCP 外壳的 structuredContent 本体：带下载描述的，以及只有内联 result 的。
    ["snapshot/structured.json", (full) => raw(full).structuredContent],
    ["snapshot/inline.json", (full) => ({ result: full })],
    // 纯 content 块的 MCP 结果。
    ["snapshot/content-only.json", (full) => ({ content: [{ type: "text", text: JSON.stringify(full) }] })],
  ];
  for (const { id } of persistVariants) {
    for (const [where, shape] of placements) {
      const run = await persistRun(t, id);
      const full = fullVideo(1);
      const staged = await run.stage(full, VIDEO_META);
      await run.save(where, shape(full));
      const first = run.persist("--dest", "snapshot/envelope-01.json", ...VIDEO, "--any-status");
      assert.equal(first.exit, 0, `${id} ${where}: ${first.out}`);
      assert.deepEqual(await run.read("snapshot/envelope-01.json"), staged, `${id} ${where}`);
      // 同一个槽位重跑是幂等的；换一个槽位就没有剩下的响应了。
      assert.equal(run.persist("--dest", "snapshot/envelope-01.json", ...VIDEO, "--any-status").exit, 0, `${id} ${where}`);
      const second = run.persist("--dest", "snapshot/envelope-02.json", ...VIDEO, "--any-status");
      assert.equal(second.exit, 1, `${id} ${where}: ${second.out}`);
      assert.match(second.out, /no complete host record/);
    }
  }
});

test("persist：bundle 与 --schema 的放置照旧计入；失败 envelope 不带 --any-status 被拒，带上则照常放置", async (t) => {
  for (const { id } of persistVariants) {
    // bundle：两次响应依次填两个位置，原始结果都在 snapshot/；第三个位置没有响应可取。
    let run = await persistRun(t, id);
    for (const n of [1, 2]) {
      await run.stage(fullVideo(n), VIDEO_META);
      await run.save(`snapshot/tool-${n}.json`, downloadResult({ result: withoutData(fullVideo(n)) }));
    }
    for (const [slot, n] of [["a", 1], ["b", 2], ["a", 1]]) {
      const placed = run.persist("--into", "bundle.json", "--at", "runs", "--at", slot, ...VIDEO);
      assert.equal(placed.exit, 0, `${id} ${slot}: ${placed.out}`);
      assert.equal(JSON.parse(placed.out).request_id, `req-${n}`, id);
    }
    assert.match(run.persist("--into", "bundle.json", "--at", "runs", "--at", "c", ...VIDEO).out, /no complete host record/, id);

    // 类型化结果：原始出参（带 download 描述、没有 MCP 外壳）放在运行目录根。
    run = await persistRun(t, id);
    const staged = await run.stage(candidateSet, { schema_version: candidateSet.schema_version, delivery: "download" });
    await run.save("collect.json", aggregateResult().structuredContent);
    const typed = run.persist("--dest", "snapshot/candidates.json", "--schema", candidateSet.schema_version);
    assert.equal(typed.exit, 0, `${id}: ${typed.out}`);
    assert.deepEqual(await run.read("snapshot/candidates.json"), staged);
    assert.match(run.persist("--dest", "snapshot/candidates-2.json", "--schema", candidateSet.schema_version).out, /no complete host record/, id);

    // 失败 envelope：MCP 错误结果存在 snapshot/。
    run = await persistRun(t, id);
    const failed = { ...withoutData(fullVideo(3)), status: "failed", error_code: "invalid_input", summary: "Invalid video id" };
    const failedBytes = await run.stage(failed, VIDEO_META);
    await run.save("snapshot/tool-error.json", mcp(failed, { isError: true }));
    const refused = run.persist("--dest", "snapshot/envelope-01.json", ...VIDEO);
    assert.equal(refused.exit, 1, id);
    assert.match(refused.out, /is not a completed response/);
    await assert.rejects(run.read("snapshot/envelope-01.json"));
    const accepted = run.persist("--dest", "snapshot/envelope-01.json", ...VIDEO, "--any-status");
    assert.equal(accepted.exit, 0, `${id}: ${accepted.out}`);
    assert.deepEqual(await run.read("snapshot/envelope-01.json"), failedBytes);
    assert.match(run.persist("--dest", "snapshot/envelope-02.json", ...VIDEO, "--any-status").out, /no complete host record/, id);
  }
});

test("persist --drained：带 MCP 外壳的来源记录原样放置后仍算已放置，不会被放第二次", async (t) => {
  const drainedSkills = persistVariants.filter((skill) => skill.drained);
  assert.equal(drainedSkills.length, 2);
  for (const { id } of drainedSkills) {
    const run = await persistRun(t, id);
    const source = JSON.stringify(mcp(fullVideo(4)));
    await run.save("snapshot/.drained/envelope-01.json", source);
    const first = run.persist("--dest", "snapshot/envelope-01.json", ...VIDEO, "--drained", "snapshot/.drained");
    assert.equal(first.exit, 0, `${id}: ${first.out}`);
    assert.equal((await run.read("snapshot/envelope-01.json")).toString(), source);
    const second = run.persist("--dest", "snapshot/envelope-02.json", ...VIDEO, "--drained", "snapshot/.drained");
    assert.equal(second.exit, 1, `${id}: ${second.out}`);
    assert.match(second.out, /no complete host record/);
  }
});

test("stage：状态为完成却没有 data 的结果（下载结果丢了 download 只剩摘要）被拒收，指回 fetch", async (t) => {
  let checked = 0;
  for (const id of await readdir(path.join(root, "skills"))) {
    const script = path.join(root, "skills", id, "scripts", "static_host.py");
    const text = await readFile(script, "utf8").catch(() => "");
    if (!text.includes("completed status but no data")) continue;
    // 取这份副本声明的第一个普通数据工具（投影查找与聚合工具另有规则）。
    const declared = text.slice(text.indexOf("CONNECTOR_TOOLS = {"), text.indexOf("\n}\n", text.indexOf("CONNECTOR_TOOLS = {")));
    const entry = [...declared.matchAll(/"([a-z-]+)\/([a-z_]+)": "([a-z_]+)"/g)]
      .find(([, connector, , tool]) => !tool.startsWith("lookup_") && !connector.startsWith("creator-trending"));
    assert.ok(entry, id);
    const [, connector, operation, tool] = entry;
    const temp = await mkdtemp(path.join(os.tmpdir(), "kabo-stage-summary-"));
    t.after(() => rm(temp, { recursive: true, force: true }));
    const env = { ...process.env, PYTHONDONTWRITEBYTECODE: "1" };
    delete env.KABO_STATIC_RUN_DIR;
    const run = execFileSync("python3", ["-B", script, "init", "--workspace", temp], { encoding: "utf8", env }).trim();
    const input = path.join(run, "host-result.json");
    await writeFile(input, JSON.stringify({ result: { ...withoutData(fullVideo(1)), connector_id: connector, operation } }));
    const result = spawnSync("python3", ["-B", script, "stage", "--tool", tool, "--input", input],
      { cwd: run, encoding: "utf8", env: { ...env, KABO_STATIC_RUN_DIR: run } });
    assert.notEqual(result.status, 0, id);
    assert.match(result.stdout + result.stderr, /completed status but no data/, `${id}: ${result.stdout}${result.stderr}`);
    checked += 1;
  }
  assert.ok(checked >= 9, String(checked));
});

// 组合取数工具（kabo-composite-fetch.v1，KS8）：calls[i].result 与单个数据工具的结果信封完全相同。
// stage / fetch 把 calls[] 拆开，每项落盘与对同一信封单独 stage 逐字节相同（旁注只多 composite_* 与 delivery），
// 另写一份整体记录；任何一项不过整份不落盘。
const COMPOSITE = "kabo-composite-fetch.v1";
const env = (connector_id, operation, n, data, extra = {}) => ({
  connector_id, operation, status: "completed", summary: `Found ${n} rows`, limitations: [],
  evidence_ref: `ev-${operation}-${n}`, raw_sha256: "a".repeat(64), request_id: `req-${operation}-${n}`, data, ...extra,
});
const rows = (prefix, n) => ({ rows: Array.from({ length: n }, (_, i) => ({ id: `${prefix}-${i}`, caption: `Ünïcode ${prefix} ${i}` })) });
const compositeFixtures = [
  {
    skill: "review-creator-account",
    tool: "collect_account_history",
    status: "completed",
    calls: [
      { tool: "instagram_resolve_profile", group: "acct-0", parameters: { username: "neutral.creator" },
        result: env("instagram-discovery", "resolve_profile", 1, { rows: [{ handle: "neutral.creator", id: "u1", followers: 1200 }] }) },
      { tool: "instagram_list_posts", group: "acct-0", parameters: { username: "neutral.creator", count: 12 },
        result: env("instagram-discovery", "list_posts", 2, rows("post", 2)) },
      { tool: "instagram_list_reels", group: "acct-0", parameters: { username: "neutral.creator", count: 12, cursor: "c-2" },
        result: env("instagram-discovery", "list_reels", 3, rows("reel", 3)) },
    ],
    summary: { accounts: [{ group: "acct-0", input: { handle: "neutral.creator" }, resolved: { handle: "neutral.creator", id: "u1" }, status: "completed", items_total: 5, pages: 2 }] },
    coverage: { accounts_requested: 1, accounts_completed: 1 },
    limitations: [],
  },
  {
    skill: "research-tiktok-trends",
    tool: "collect_tiktok_video_candidates",
    status: "partial",
    calls: [
      { tool: "tiktok_discovery_search_videos", group: "intent-0-sort-0", parameters: { keyword: "desk setup", sort_type: 0, publish_time: 7, count: 30 },
        result: env("tiktok-discovery", "search_videos", 2, rows("tt0", 2)) },
      { tool: "tiktok_discovery_search_videos", group: "intent-0-sort-1", parameters: { keyword: "desk setup", sort_type: 1, publish_time: 7, count: 30 },
        result: env("tiktok-discovery", "search_videos", 1, rows("tt1", 1)) },
      { tool: "tiktok_discovery_list_top_contents", group: "top-2026-11-01", parameters: { date: "2026-10-31", region: "US" },
        result: env("tiktok-discovery", "list_top_contents", 1, rows("top", 1)) },
    ],
    summary: { intents: [{ group: "intent-0", keyword: "desk setup", rows: 3 }] },
    coverage: { top_contents: { requested: "2026-11-01", used: "2026-10-31" } },
    limitations: ["Top contents were empty for 2026-11-01; used 2026-10-31."],
  },
  {
    skill: "research-youtube-trends",
    tool: "collect_youtube_recent_videos",
    status: "completed",
    calls: [
      { tool: "youtube_search_videos", group: "query-0", parameters: { q: "budget travel", order: "viewCount", max_results: 50 },
        result: env("youtube-public", "search_videos", 2, rows("yt0", 2)) },
      { tool: "youtube_search_videos", group: "query-1", parameters: { q: "budget travel tips", order: "date", max_results: 50 },
        result: env("youtube-public", "search_videos", 2, rows("yt1", 2)) },
      { tool: "youtube_get_video", group: "details-0", parameters: { video_ids: ["yt0-0", "yt0-1", "yt1-0", "yt1-1"] },
        result: env("youtube-public", "get_video", 4, rows("detail", 4)) },
    ],
    summary: { unique_videos: 4 },
    coverage: { queries: 2, detail_chunks: 1 },
    limitations: [],
  },
  {
    skill: "verify-platform-rule",
    tool: "render_official_pages",
    status: "partial",
    calls: [
      { tool: "official_page_render_page", group: "page-0", parameters: { url: "https://help.example.com/rules", source_id: "src-0" },
        result: env("official-page-render", "render_page", 1, { rows: [{ url: "https://help.example.com/rules", text: "Rule text" }] }) },
      { tool: "official_page_render_page", group: "page-1", parameters: { url: "https://help.example.com/gone", source_id: "src-1" },
        result: { connector_id: "official-page-render", operation: "render_page", status: "failed", summary: "Page unavailable", limitations: ["The page returned 404."], evidence_ref: "ev-gone" } },
    ],
    summary: { pages: [{ group: "page-0", url: "https://help.example.com/rules", source_id: "src-0", status: "completed" }, { group: "page-1", url: "https://help.example.com/gone", source_id: "src-1", status: "unavailable" }] },
    coverage: { pages_requested: 2, pages_rendered: 1 },
    limitations: ["page-1 could not be rendered."],
  },
];
const compositeOf = ({ tool, status, calls, summary, coverage, limitations }) => ({ schema_version: COMPOSITE, tool, status, calls, summary, coverage, limitations });
const decoded = (files, name) => Buffer.from(files[name], "base64");
const COMPOSITE_MARKS = ["composite_tool", "composite_id", "composite_index", "composite_group", "composite_parameters", "delivery", "staged_at"];
const withoutMarks = (meta) => Object.fromEntries(Object.entries(meta).filter(([key]) => !COMPOSITE_MARKS.includes(key)));

// 读出组合落盘：整体记录（旁注 schema_version 为记录 schema）与逐项调用的正文和旁注。
function splitFiles(files) {
  const metas = Object.keys(files).filter((name) => name.endsWith(".meta")).map((name) => ({ name, meta: JSON.parse(decoded(files, name)) }));
  const record = metas.find(({ meta }) => meta.schema_version === "kabo-composite-fetch.record.v1");
  assert.ok(record, "缺整体记录");
  const recordBody = JSON.parse(decoded(files, record.name.replace(/\.meta$/, ".json")));
  const calls = recordBody.calls.map((entry) => ({
    entry,
    body: decoded(files, `${entry.evidence_id}.json`),
    meta: JSON.parse(decoded(files, `${entry.evidence_id}.meta`)),
  }));
  return { record, recordBody, calls };
}

function checkSplit(fixture, result, singles, { delivery } = {}) {
  assert.equal(result.exit, 0, result.stderr);
  noSecrets(result);
  const { record, recordBody, calls } = splitFiles(result.files);
  assert.equal(Object.keys(result.files).length, 2 * fixture.calls.length + 2);
  assert.equal(record.meta.tool, fixture.tool);
  assert.equal(record.meta.composite_calls, fixture.calls.length);
  assert.equal(record.meta.delivery, delivery);
  assert.deepEqual(
    { ...recordBody, calls: recordBody.calls.map(({ evidence_id: _id, ...rest }) => rest) },
    {
      schema_version: "kabo-composite-fetch.record.v1", tool: fixture.tool, status: fixture.status,
      summary: fixture.summary, coverage: fixture.coverage, limitations: fixture.limitations,
      calls: fixture.calls.map((call, index) => ({ index, tool: call.tool, group: call.group, status: call.result.status })),
    },
  );
  const recordId = record.name.replace(/\.meta$/, "");
  calls.forEach(({ entry, body, meta }, index) => {
    const call = fixture.calls[index];
    // 与对同一信封单独 stage 逐字节相同；旁注只多组合标记。
    const single = singles[index];
    const singleBody = Object.entries(single.files).find(([name]) => name.endsWith(".json"));
    const singleMeta = JSON.parse(decoded(single.files, singleBody[0].replace(/\.json$/, ".meta")));
    assert.deepEqual(body, decoded(single.files, singleBody[0]), `${fixture.tool} calls[${index}] 正文与单独 stage 不同`);
    assert.deepEqual(withoutMarks(meta), withoutMarks(singleMeta));
    assert.equal(meta.tool, call.tool);
    assert.equal(meta.composite_tool, fixture.tool);
    assert.equal(meta.composite_id, recordId);
    assert.equal(meta.composite_index, index);
    assert.equal(meta.composite_group, call.group);
    assert.deepEqual(meta.composite_parameters, call.parameters);
    assert.equal(meta.delivery, delivery);
    assert.equal(entry.tool, call.tool);
  });
  // 输出整体是合法 JSON，逐项调用各占一行。
  const printed = JSON.parse(result.stdout);
  assert.equal(printed.composite, fixture.tool);
  assert.equal(printed.staged, fixture.calls.length);
  assert.equal(printed.record, path.join(result.run_dir, "snapshot/.host-evidence", `${recordId}.json`));
  assert.deepEqual(printed.calls.map((entry) => entry.evidence_id), calls.map(({ entry }) => entry.evidence_id));
  const lines = result.stdout.trim().split("\n");
  assert.equal(lines.length, fixture.calls.length + 2);
  fixture.calls.forEach((call, index) => assert.match(lines[index + 1], new RegExp(`^\\{"index": ${index}, "tool": "${call.tool}"`)));
}

for (const fixture of compositeFixtures) {
  test(`组合结果 ${fixture.tool}：stage 与下载 fetch 都逐项拆开落盘，与单工具 stage 逐字节相同；坏一项整份不落盘`, async (t) => {
    const composite = compositeOf(fixture);
    const bytes = Buffer.from(JSON.stringify(composite));
    const { calls: _calls, ...inline } = composite;
    const ready = { status: "ready", kind: "envelope", url: SIGNED, expires_at: "2099-01-01T00:10:00Z", sha256: sha256(bytes), bytes: bytes.length, content_type: "application/json" };
    const downloaded = { ...inline, download: ready };
    const broken = structuredClone(composite);
    broken.calls[1].result.operation = "some_other_operation";
    const brokenBytes = Buffer.from(JSON.stringify(broken));
    const report = await harness(t, fixture.skill, [
      { name: "stage", command: "stage", tool: fixture.tool, result: { structuredContent: composite } },
      { name: "stageText", command: "stage", tool: fixture.tool, result: { content: [{ type: "text", text: JSON.stringify(composite) }] } },
      ...fixture.calls.map((call, index) => ({ name: `single${index}`, command: "stage", tool: call.tool, result: { structuredContent: { result: call.result } } })),
      { name: "fetch", command: "fetch", tool: fixture.tool, result: { structuredContent: downloaded, content: [{ type: "text", text: JSON.stringify(downloaded) }] }, responses: { [SIGNED]: ok(bytes) } },
      { name: "stageDownloadOnly", command: "stage", tool: fixture.tool, result: { structuredContent: downloaded } },
      { name: "stageBroken", command: "stage", tool: fixture.tool, result: { structuredContent: broken } },
      { name: "fetchBroken", command: "fetch", tool: fixture.tool,
        result: { structuredContent: { ...inline, download: { ...ready, sha256: sha256(brokenBytes), bytes: brokenBytes.length } } }, responses: { [SIGNED]: ok(brokenBytes) } },
      { name: "fetchMismatch", command: "fetch", tool: fixture.tool,
        result: { structuredContent: { ...downloaded, status: fixture.status === "partial" ? "completed" : "partial" } }, responses: { [SIGNED]: ok(bytes) } },
      { name: "fetchUnavailable", command: "fetch", tool: fixture.tool, result: { structuredContent: { ...inline, download: { status: "unavailable", reason: "too_large" } } } },
    ]);
    const singles = fixture.calls.map((_, index) => report[`single${index}`]);
    for (const single of singles) assert.equal(single.exit, 0, single.stderr);
    checkSplit(fixture, report.stage, singles);
    checkSplit(fixture, report.stageText, singles);
    checkSplit(fixture, report.fetch, singles, { delivery: "download" });
    assert.deepEqual(report.fetch.requests, ["GET"]);
    assert.deepEqual(JSON.parse(report.fetch.stdout).fetched, [{ kind: "envelope", sha256: sha256(bytes), bytes: bytes.length, status: "fetched" }]);

    assert.equal(report.stageDownloadOnly.exit, 1);
    assert.match(report.stageDownloadOnly.stderr, new RegExp(`run static_host\\.py fetch --tool ${fixture.tool}`));
    for (const name of ["stageDownloadOnly", "stageBroken", "fetchBroken", "fetchMismatch"]) {
      assert.equal(report[name].exit, 1, `${name} 应失败`);
      assert.deepEqual(report[name].files, {}, `${name} 不得落盘`);
      noSecrets(report[name]);
    }
    for (const name of ["stageBroken", "fetchBroken"]) assert.match(report[name].stderr, /calls\[1\] .*does not match its declared operation; nothing was written/);
    assert.match(report.fetchMismatch.stderr, /does not match this tool result/);
    assert.equal(report.fetchUnavailable.exit, 0, report.fetchUnavailable.stderr);
    assert.match(JSON.parse(report.fetchUnavailable.stdout).next, new RegExp(`stage --tool ${fixture.tool}`));
    assert.deepEqual(report.fetchUnavailable.files, {});
  });
}

test("组合结果：未知组合工具名、借单工具名 stage 组合结果、本 Skill 未声明的调用、嵌套组合与错误结果一律拒收，不落盘", async (t) => {
  const fixture = compositeFixtures[0];
  const composite = compositeOf(fixture);
  const undeclared = structuredClone(composite);
  undeclared.calls[0] = { tool: "youtube_search_videos", group: "acct-0", parameters: {}, result: env("youtube-public", "search_videos", 1, rows("x", 1)) };
  const nested = structuredClone(composite);
  nested.calls[2] = { tool: "collect_account_history", group: "acct-0", parameters: {}, result: composite };
  const report = await harness(t, fixture.skill, [
    { name: "unknown", command: "stage", tool: "collect_everything", result: { structuredContent: { ...composite, tool: "collect_everything" } } },
    { name: "unknownFetch", command: "fetch", tool: "collect_everything", result: { structuredContent: { ...composite, tool: "collect_everything" } } },
    { name: "otherName", command: "stage", tool: "instagram_list_posts", result: { structuredContent: composite } },
    { name: "wrongTool", command: "stage", tool: "render_official_pages", result: { structuredContent: composite } },
    { name: "undeclared", command: "stage", tool: fixture.tool, result: { structuredContent: undeclared } },
    { name: "nested", command: "stage", tool: fixture.tool, result: { structuredContent: nested } },
    { name: "noCalls", command: "stage", tool: fixture.tool, result: { structuredContent: { ...composite, calls: undefined } } },
    { name: "error", command: "stage", tool: fixture.tool, result: { isError: true, content: [{ type: "text", text: "budget exceeded" }] } },
  ]);
  for (const [name, result] of Object.entries(report)) {
    assert.equal(result.exit, 1, `${name} 应失败：${result.stdout}`);
    assert.deepEqual(result.files, {}, `${name} 不得落盘`);
  }
  assert.match(report.unknown.stderr, /Not a declared evidence tool/);
  assert.match(report.unknownFetch.stderr, /Not a declared evidence tool/);
  assert.match(report.otherName.stderr, /does not match its declared operation|composite tool name/);
  assert.match(report.wrongTool.stderr, /does not match its declared operation/);
  assert.match(report.undeclared.stderr, /calls\[0\] \(youtube_search_videos\): Not a declared evidence tool/);
  assert.match(report.nested.stderr, /calls\[2\] \(collect_account_history\)/);
  assert.match(report.noCalls.stderr, /must carry its calls/);
  assert.match(report.error.stderr, /collect_account_history failed.*budget exceeded/);
});

test("组合结果：同一份再 stage 不重复落盘，记录指回原证据；composite_records 读出摘要与逐项证据路径", async (t) => {
  const fixture = compositeFixtures[1];
  const composite = { structuredContent: compositeOf(fixture) };
  const report = await harness(t, fixture.skill, [
    { name: "again", command: "stage", tool: fixture.tool, result: composite, before: [{ command: "stage", tool: fixture.tool, result: composite }] },
  ]);
  const again = report.again;
  assert.equal(again.exit, 0, again.stderr);
  const printed = JSON.parse(again.stdout);
  assert.equal(printed.staged, 0);
  assert.equal(printed.duplicate, fixture.calls.length);
  assert.equal(Object.keys(again.files).length, 2 * fixture.calls.length + 2);
  const { record, calls } = splitFiles(again.files);
  assert.equal(printed.record, path.join(again.run_dir, "snapshot/.host-evidence", record.name.replace(/\.meta$/, ".json")));
  assert.deepEqual(printed.calls.map((entry) => entry.evidence_id), calls.map(({ entry }) => entry.evidence_id));

  const script = path.join(root, "skills", fixture.skill, "scripts/static_host.py");
  const read = execFileSync("python3", ["-I", "-B", "-c", String.raw`
import json, runpy, sys
host = runpy.run_path(sys.argv[1])
print(json.dumps(host["composite_records"]("collect_tiktok_video_candidates")))
print(json.dumps(host["composite_records"]("render_official_pages")))
`, script], { encoding: "utf8", env: { ...process.env, KABO_STATIC_RUN_DIR: again.run_dir } }).trim().split("\n");
  const records = JSON.parse(read[0]);
  assert.equal(records.length, 1);
  assert.equal(records[0].status, "partial");
  assert.deepEqual(records[0].coverage, fixture.coverage);
  assert.deepEqual(records[0].limitations, fixture.limitations);
  for (const [index, call] of records[0].calls.entries()) {
    assert.equal(call.group, fixture.calls[index].group);
    assert.deepEqual(JSON.parse(await readFile(call.path, "utf8")), fixture.calls[index].result);
  }
  assert.deepEqual(JSON.parse(read[1]), []);
});

test("组合结果的读取与拆分段在各 Skill 的 static_host.py 中逐字相同", async () => {
  const blocks = [];
  for (const id of fetchSkills) {
    const text = await readFile(path.join(root, "skills", id, "scripts/static_host.py"), "utf8");
    const start = text.indexOf("# 组合取数工具（kabo-composite-fetch.v1");
    const end = text.indexOf("# 延迟媒体工具的幂等键");
    assert.ok(start > 0 && end > start, id);
    blocks.push({ id, block: text.slice(start, end) });
    assert.match(text, /def stage\(tool, raw\):\n    if tool in COMPOSITE_TOOLS:\n        return stage_composite\(tool, raw\)\n/, id);
    assert.match(text, /    if tool in COMPOSITE_TOOLS:\n        return fetch_composite\(tool, raw\)\n/, id);
  }
  for (const { id, block } of blocks) assert.equal(block, blocks[0].block, `${id} 与 ${blocks[0].id} 不同`);
});

test("persist：媒体任务按请求名或托管变体名都能放置（宿主记哪一个都行），其他操作不借用", async (t) => {
  const variants = [...persistCopies].filter(([, text]) => text.includes("MEDIA_VARIANTS"));
  assert.ok(variants.length >= 1);
  for (const [id] of variants) {
    for (const [recorded, named] of [["extract_keyframes_hosted_stream", "extract_keyframes"], ["extract_keyframes", "extract_keyframes_hosted_media"],
      ["measure_media_signals", "measure_media_signals_hosted_stream"]]) {
      const run = await persistRun(t, id);
      const job = { ...envelope, schema_version: "1.0", connector_id: "public-video-media", operation: recorded, raw_sha256: sha256(Buffer.from(recorded)),
        request_id: `req-${recorded}`, evidence_ref: `ev-${recorded}`, data: { frames: [] } };
      const staged = await run.stage(job, { connector_id: "public-video-media", operation: recorded });
      const media = ["--connector", "public-video-media", "--any-status"];
      assert.match(run.persist("--dest", "snapshot/other.json", ...media, "--operation", "fetch_transcript").out, /no complete host record/, `${id} ${recorded}`);
      const placed = run.persist("--dest", "snapshot/media.json", ...media, "--operation", named);
      assert.equal(placed.exit, 0, `${id} ${recorded} as ${named}: ${placed.out}`);
      assert.deepEqual(await run.read("snapshot/media.json"), staged);
    }
  }
});

// 按结果编号取（kabo ADR-0084「2026-11-04」）：交接时模型只交编号（或含编号的下载链接），脚本向 Kabo 取同一份
// 字节、按响应头的长度与 sha256 校验后照 fetch 落盘。每个带 fetch 的 static_host 副本都走一遍。
const RESULT_ID = "AbCdEfGhIjKlMnOpQrSt_-";
const BY_ID = `https://kabo.sh/v1/research-downloads/${RESULT_ID}`;
const byId = (body = envelopeBytes, { digest = sha256(body), length = body.length, code = 200 } = {}) =>
  [code, body.toString("base64"), { "Content-Type": "application/json", "Content-Length": String(length), "X-Content-SHA256": digest }];

for (const skill of fetchSkills) {
  test(`${skill} fetch --id：按编号取、核对长度与 sha256 后落盘；链接形式也认；不符、过期、错工具一律不落盘`, async (t) => {
    const tool = "get_research_data_job";
    const report = await harness(t, skill, [
      { name: "valid", command: "fetch", tool, id: RESULT_ID, result: {}, responses: { [BY_ID]: byId() } },
      { name: "url", command: "fetch", tool, id: SIGNED.replace("0f3c", RESULT_ID), result: {}, responses: { [BY_ID]: byId() } },
      { name: "digest", command: "fetch", tool, id: RESULT_ID, result: {}, responses: { [BY_ID]: byId(envelopeBytes, { digest: "0".repeat(64) }) } },
      { name: "length", command: "fetch", tool, id: RESULT_ID, result: {}, responses: { [BY_ID]: byId(envelopeBytes, { length: envelopeBytes.length + 1 }) } },
      { name: "expired", command: "fetch", tool, id: RESULT_ID, result: {}, responses: { [BY_ID]: [404, "", {}] } },
      { name: "malformed", command: "fetch", tool, id: "short", result: {}, responses: {} },
    ]);
    // 读回任务结果只认本 Skill 声明的数据操作；不认的 Skill 至少要以 Job 字样拒收，不落盘。
    const valid = report.valid;
    if (valid.exit === 0) {
      assert.deepEqual(valid.requests, ["GET"]);
      assert.equal(JSON.parse(valid.stdout).staged, 1, valid.stdout);
      assert.equal(report.url.exit, 0, report.url.stderr);
    } else {
      assert.match(valid.stderr, /Job returned an undeclared data operation; check that the id belongs/);
    }
    for (const name of ["digest", "length"]) {
      assert.equal(report[name].exit, 1, name);
      assert.match(report[name].stderr, /does not match its declared size and sha256; nothing was written/);
      assert.deepEqual(Object.keys(report[name].files), [], name);
    }
    assert.match(report.expired.stderr, /no result under this id: it expired \(ids last 10 minutes\) or was mistyped/);
    assert.deepEqual(Object.keys(report.expired.files), []);
    assert.match(report.malformed.stderr, /A result id is the 22 letters/);
    assert.deepEqual(report.malformed.requests, []);
    for (const result of Object.values(report)) assert.equal(result.stderr.includes(RESULT_ID), false, result.stderr);
  });
}
