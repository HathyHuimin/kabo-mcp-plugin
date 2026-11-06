import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const scripts = path.join(root, "skills/remake-viral-video/scripts");

// Long remakes (up to 10 minutes): many sheet pages are opened as an even sample, and a long
// transcript stays in its file instead of riding in the result.
const PY = String.raw`
import importlib.util, json, os, sys
sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("static_host", os.path.join(sys.argv[1], "static_host.py"))
host = importlib.util.module_from_spec(spec); spec.loader.exec_module(host)
few = host.page_result([f"p{i}" for i in range(5)])
many = host.page_result([f"p{i}" for i in range(75)])
print(json.dumps({"few": few, "many_open": many["open"], "many_note": many.get("open_note", ""),
                  "inline_max": host.TRANSCRIPT_INLINE_MAX_CHARS}))
`;

test("remake long video: sheet pages over 16 are opened as an even sample, first and last kept", () => {
  const r = spawnSync("python3", ["-I", "-c", PY, scripts], { encoding: "utf8" });
  assert.equal(r.status, 0, r.stderr);
  const out = JSON.parse(r.stdout);
  assert.deepEqual(out.few.open, out.few.pages);
  assert.equal(out.few.open_note, undefined);
  assert.equal(out.many_open.length, 16);
  assert.equal(out.many_open[0], "p0");
  assert.equal(out.many_open.at(-1), "p74");
  assert.match(out.many_note, /75 pages; open only the 16/);
  assert.equal(out.inline_max, 20000);
});
