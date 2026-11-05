import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import test from "node:test";

test("续写检查拒绝重放，但允许短回顾与新的推进；不作语义判定", () => {
  const output = execFileSync("python3", ["-B", "-c", `
import importlib.util
from pathlib import Path
spec = importlib.util.spec_from_file_location("overlap", Path("skills/create-script-and-packaging/scripts/check_continuation_overlap.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
source = """The courier reached the island before sunset and carried the sealed box up the narrow path.
At the gate the keeper asked for a name and refused to open the door without a written invitation.
The courier unfolded the letter but the rain had washed every line from the paper except its signature.
They waited beneath the arch until the keeper finally recognized the unusual mark and lowered the chain.
Inside the courtyard the courier heard a bell ringing from an empty tower while all the windows remained dark.
The box began to move on its own and a small silver key fell onto the stones beside the courier's feet."""
assert m.check_overlap(source, source + " The next morning a visitor arrived.")["status"] == "repeated_source"
assert m.check_overlap(source, source + " " + " ".join(f"freshword{n}" for n in range(700)))["status"] == "repeated_source"
fresh = "Holding the silver key, the courier unlocked a hatch beneath the stairs. A map inside showed a different island, and the keeper denied ever having seen it."
assert m.check_overlap(source, fresh)["status"] == "no_large_literal_replay"
recap = source.splitlines()[-1] + " " + fresh
assert m.check_overlap(source, recap)["status"] == "no_large_literal_replay"
assert m.check_overlap(source, " ")["status"] == "empty"
assert m.check_overlap("", fresh)["status"] == "empty"
nonlatin = " ".join("یہ پچھلے منظر کا واقعہ تھا اور اس کے بعد دروازہ بند ہوا" for _ in range(12))
assert m.check_overlap(nonlatin, nonlatin)["status"] == "repeated_source"
try:
    m.check_overlap("a " * 8001, fresh)
except ValueError:
    pass
else:
    raise AssertionError("Unbounded repetitive input accepted")
print("8 continuation overlap checks passed")
`], { encoding: "utf8" });
  assert.match(output, /8 continuation overlap checks passed/);
});
