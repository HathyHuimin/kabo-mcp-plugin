// 瘦身后各 Skill 共用的两段内容按副本分发（KS4.2）：references/data-calls.md 整份，以及
// connector_driver.py 里交接与续跑的那一段（每条调用的存放文件与一条续跑命令，续跑自己 fetch / stage 点名的文件）。
// 副本必须逐字相同：改一份就同步改所有副本，否则同一个交接在不同 Skill 里说法不一。
import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const skills = readdirSync(path.join(root, "skills"), { withFileTypes: true })
  .filter((entry) => entry.isDirectory())
  .map((entry) => entry.name);
const copies = (relative) => skills
  .map((skill) => ({ skill, file: path.join(root, "skills", skill, relative) }))
  .filter(({ file }) => existsSync(file))
  .map(({ skill, file }) => ({ skill, text: readFileSync(file, "utf8") }));

test("references/data-calls.md 在各 Skill 中逐字相同", () => {
  const found = copies("references/data-calls.md");
  assert.ok(found.length >= 2, "至少两份 data-calls.md");
  for (const { skill, text } of found) assert.equal(text, found[0].text, `${skill} 与 ${found[0].skill} 不同`);
});

test("connector_driver.py 的交接段（resume_command 与 hand_over）在各 Skill 中逐字相同", () => {
  const block = (text) => {
    const start = text.indexOf("def resume_command()");
    const end = text.indexOf("def fetch_or_hand_over(");
    return start >= 0 && end > start ? text.slice(start, end) : null;
  };
  const found = copies("scripts/connector_driver.py").map(({ skill, text }) => ({ skill, block: block(text) }));
  const withBlock = found.filter(({ block }) => block !== null);
  assert.ok(withBlock.length >= 2, "至少两份带交接段的 connector_driver.py");
  for (const { skill, block } of withBlock) assert.equal(block, withBlock[0].block, `${skill} 与 ${withBlock[0].skill} 不同`);
});
