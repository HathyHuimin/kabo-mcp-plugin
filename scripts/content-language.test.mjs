import { execFileSync } from "node:child_process";
import test from "node:test";
import { fileURLToPath } from "node:url";

test("三平台语言准入、证据冲突与未知语言的行为回归", () => {
  execFileSync("python3", ["-B", "scripts/content-language-regression.py", "-v"], {
    cwd: fileURLToPath(new URL("..", import.meta.url)), encoding: "utf8", stdio: "pipe",
  });
});
