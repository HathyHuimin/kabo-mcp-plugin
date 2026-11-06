// 本地文件抽帧/测量的时限回归：测量两遍各解码一路、命令到时限自己停、帧先落盘、--signals-only 单独测量。
// 没装 ffmpeg 的机器上端到端用例自动跳过，mock 用例照跑。
import { execFileSync } from "node:child_process";
import test from "node:test";
import { fileURLToPath } from "node:url";

test("analyze-content-video 本地测量不再拖死抽帧", () => {
  execFileSync("python3", ["-B", "scripts/acv-local-measure-regression.py", "-v"], {
    cwd: fileURLToPath(new URL("..", import.meta.url)), encoding: "utf8", stdio: "pipe",
  });
});
