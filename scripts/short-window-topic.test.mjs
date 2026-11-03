import { execFileSync } from "node:child_process";
import test from "node:test";
import { fileURLToPath } from "node:url";

test("短窗领域门、精确日期与完整报告的行为回归", () => {
  execFileSync("python3", ["-B", "scripts/short-window-topic-regression.py", "-v"], {
    cwd: fileURLToPath(new URL("..", import.meta.url)), encoding: "utf8", stdio: "pipe",
  });
});
