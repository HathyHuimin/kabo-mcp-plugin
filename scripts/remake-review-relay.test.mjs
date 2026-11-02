import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import path from "node:path";
import test from "node:test";

// fetch 给出的终审转述只看 review.qc.failed；交付说明里的自检与 qc 行不进 notes_for_user。
const scripts = path.resolve("skills/remake-viral-video/scripts");
const notes = [
  "# Delivery notes", "", "## Rewritten lines", "", "- [cta] \"Follow me\" (replaces \"RSVP\") -- creator's sign-off.", "",
  "## Self-check (cr qc)", "", "- status: **measured**; failed []; missing []", "- face likeness median 0.87", "",
  "## Notes from the run", "", "- one-pass: framing group 0: the closest was used as is",
  "- one-pass (not checked or not fixed): S9_deliver: delivered with qc measured, failed ['framing']",
].join("\n");

function relay(qc, deliveryNotes = notes) {
  const code = `import importlib.util,json,sys
sys.dont_write_bytecode=True
spec=importlib.util.spec_from_file_location("h",sys.argv[1]);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
print(json.dumps(m.review_relay(json.loads(sys.argv[2]))))`;
  const structured = { status: "delivered", review: { qc, delivery_notes: deliveryNotes } };
  return JSON.parse(execFileSync("python3", ["-c", code, path.join(scripts, "static_host.py"), JSON.stringify(structured)], { encoding: "utf8" }));
}

test("failed 不空时不论 status 都逐项说，measured 也一样", () => {
  assert.match(relay({ status: "measured", failed: ["framing"] }).review_message, /framed differently/);
  const two = relay({ status: "failed", failed: ["caption_overlap", "loudness"] }).review_message;
  assert.match(two, /captions overlap/);
  assert.match(two, /volume is off/);
  assert.match(relay({ status: "failed", failed: ["new_item"] }).review_message, /new_item/);
});

test("failed 为空：measured 与 null 不说终审，incomplete 用固定句子", () => {
  assert.equal(relay({ status: "measured", failed: [] }).review_message, null);
  assert.equal(relay(null).review_message, null);
  assert.equal(relay({ status: "incomplete", failed: [] }).review_message, "Some of Kabo's final checks could not be measured.");
});

test("notes_for_user 只留改了什么，去掉自检与 qc 行", () => {
  const { notes_for_user } = relay({ status: "measured", failed: [] });
  assert.equal(notes_for_user.length, 2);
  assert.match(notes_for_user[0], /Follow me/);
  assert.ok(notes_for_user.every(line => !/qc|likeness|failed \[/.test(line)));
  assert.deepEqual(relay(null, null).notes_for_user, []);
});

test("render-args 的可选档优先取 talking_model_choices（all_models），旧服务端退回 talking_models", () => {
  const code = `import importlib.util,json,sys,tempfile,os
sys.dont_write_bytecode=True
spec=importlib.util.spec_from_file_location("c",sys.argv[1]);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
out=[]
for doc in json.loads(sys.argv[2]):
    f=tempfile.NamedTemporaryFile("w",suffix=".json",delete=False);json.dump(doc,f);f.close()
    out.append(m.offered_models(f.name)[0]);os.unlink(f.name)
print(json.dumps(out))`;
  const wan = "alibaba/wan-3.0/reference-to-video";
  const docs = [
    { job_id: "j", status: "waiting_script", talking_models: [{ id: "vivix/vivix-a1" }], talking_model_choices: [{ id: wan }, { id: "vivix/vivix-a1" }] },
    { job_id: "j", status: "waiting_script", talking_models: [{ id: "vivix/vivix-a1" }] },
  ];
  const out = JSON.parse(execFileSync("python3", ["-c", code, path.join(scripts, "check_documents.py"), JSON.stringify(docs)], { encoding: "utf8" }));
  assert.deepEqual(out, [[wan, "vivix/vivix-a1"], ["vivix/vivix-a1"]]);
});
