import { execFileSync } from "node:child_process";
import test from "node:test";
import { fileURLToPath } from "node:url";

test("本次目标、冷启动和分配名额的端到端回归", () => {
  execFileSync("python3", ["-B", "scripts/trends-goal-regression.py", "-v"], {
    cwd: fileURLToPath(new URL("..", import.meta.url)), encoding: "utf8", stdio: "pipe",
  });
});
