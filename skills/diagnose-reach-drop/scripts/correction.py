"""Input mistakes caught before anything runs: report them as needs_correction (exit 0), not as failures.

A host pipeline closes the run on any non-zero exit. A wrong flag, a near-miss value or a malformed plan
has fetched and written nothing, so it is reported for the same step to be corrected and rerun in the
same run. Failures after work started still exit non-zero.
"""
from __future__ import annotations

import argparse
import json
import sys

NOTHING_RAN = "Nothing was run or written; correct the command and run the same step again in this run."
TEXT_FLAGS = {
    "--question": "--question-file - with the request as a heredoc",
    "--brief": "--brief-file - with the plan JSON as a heredoc",
    "--synthesis": "--synthesis-file - with the analysis JSON as a heredoc",
    "--analysis-file": "--analysis <AccountAnalysis file>",
}


class Correction(Exception):
    """Every issue is one sentence the model can act on."""

    def __init__(self, *issues: str, next_step: str = NOTHING_RAN):
        super().__init__(issues[0] if issues else "")
        self.issues = list(issues)
        self.next_step = next_step

    def emit(self) -> int:
        print(json.dumps({"status": "needs_correction", "issues": self.issues}, ensure_ascii=False))
        print(self.next_step)
        sys.stdout.flush()
        return 0


class Parser(argparse.ArgumentParser):
    """Exact flags only (no prefix guessing); a usage error is a correction, not a failed run."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("allow_abbrev", False)
        super().__init__(*args, **kwargs)

    def error(self, message: str):  # type: ignore[override]
        issues = [f"{self.prog}: {message}"]
        for flag, use in TEXT_FLAGS.items():
            if f"{flag} " in message + " " or message.rstrip().endswith(flag):
                issues.append(f"{flag} is not a flag; use {use}")
        raise SystemExit(Correction(*issues, next_step=NOTHING_RAN + "\n" + self.stage_usage()).emit())

    def stage_usage(self) -> str:
        """The usage of the subcommand being run, which names its own flags."""
        for action in self._actions:
            if isinstance(action, argparse._SubParsersAction):
                stage = next((value for value in sys.argv[1:] if value in action.choices), None)
                if stage:
                    return action.choices[stage].format_usage().strip()
        return self.format_usage().strip()


def language(value: str) -> str:
    """The two delivery languages, from the spellings a model plausibly writes."""
    key = value.strip().casefold().replace("_", "-")
    if key in {"zh", "cn", "chinese", "中文", "zh-cn"} or key.startswith(("zh-", "zh-hans")):
        return "zh-CN"
    if key in {"en", "english", "英文"} or key.startswith("en-"):
        return "en"
    raise argparse.ArgumentTypeError(f"use en or zh-CN, not {value!r}")


def choice(aliases: dict[str, str], allowed: tuple[str, ...]):
    """A case-insensitive choice that also accepts the listed aliases."""
    def convert(value: str) -> str:
        key = value.strip().casefold()
        key = aliases.get(key, key)
        if key not in allowed:
            raise argparse.ArgumentTypeError(f"use one of {', '.join(allowed)}, not {value!r}")
        return key
    return convert


PLATFORM = choice({"ig": "instagram", "insta": "instagram", "yt": "youtube", "tt": "tiktok", "tik tok": "tiktok"},
                  ("instagram", "youtube", "tiktok"))
CONTENT_KIND = choice({"reel": "reels", "post": "feed", "posts": "feed", "all": "auto"}, ("auto", "feed", "reels"))


def read_file(flag: str, path: str) -> str:
    """Read a file argument; a value that is not a file is a correction, since nothing ran yet."""
    from pathlib import Path
    try:
        return Path(path).read_text(encoding="utf-8")
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
        shown = path if len(path) <= 60 else path[:57] + "..."
        hint = "JSON" if path.lstrip().startswith(("{", "[")) else "text"
        raise Correction(f"{flag} takes a file path or '-' for stdin; {shown!r} is not a file. "
                         f"Pass the {hint} as a heredoc with {flag} -.") from None
