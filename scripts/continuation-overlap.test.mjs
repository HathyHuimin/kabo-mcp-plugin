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

test("无空格文字的复制不会被当成几个词，Unicode 等价拼写也能匹配", () => {
  const output = execFileSync("python3", ["-B", "-c", `
import importlib.util
import unicodedata
from pathlib import Path
spec = importlib.util.spec_from_file_location("overlap", Path("skills/create-script-and-packaging/scripts/check_continuation_overlap.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
source = "清晨信使穿过森林来到海边的小村庄。他把密封的信交给守门人，却发现信封里面只有一张画着灯塔的地图。守门人带他走上旧楼梯，在最高一层打开木箱，取出一把生锈的钥匙。窗外突然响起钟声，两个人看到远处的船正在缓缓离开码头。"
assert m.check_overlap(source, source + "第二天他们继续寻找失踪的船长。")["status"] == "repeated_source"
assert m.check_overlap(source, source.replace("。", "，"))["status"] == "repeated_source"
fresh = "她从山顶下到山谷，发现雪地里有一串新的脚印，于是循着足迹走进废弃的矿洞。"
assert m.check_overlap(source, fresh)["status"] == "no_large_literal_replay"
assert m.check_overlap(source, source.split("。")[0] + "。" + fresh)["status"] == "no_large_literal_replay"
japanese = "旅人は夜明けに駅を出て古い橋を渡りました。対岸の店で地図を受け取り、森の奥にある小屋へ向かいました。扉を開けると部屋の中央には青い箱が置かれていました。箱の中には誰も知らない島の写真と船の切符が入っていました。"
assert m.check_overlap(japanese, japanese)["status"] == "repeated_source"
accented = " ".join(f"café{n} résumé{n}" for n in range(40))
assert m.check_overlap(accented, unicodedata.normalize("NFD", accented))["status"] == "repeated_source"
try:
    m.check_overlap("山" * 8001, fresh)
except ValueError:
    pass
else:
    raise AssertionError("Unbounded unspaced input accepted")
print("7 Unicode continuation checks passed")
`], { encoding: "utf8" });
  assert.match(output, /7 Unicode continuation checks passed/);
});
