import { execFileSync } from "node:child_process";
import test from "node:test";
import { fileURLToPath } from "node:url";

test("选题聊天原文交接、独立错误汇总与两次修订预算", () => {
  execFileSync("python3", ["-B", "scripts/ideas-context-regression.py", "-v"], {
    cwd: fileURLToPath(new URL("..", import.meta.url)), encoding: "utf8", stdio: "pipe",
  });
});
