"""Bounded subprocesses and immutable source/artifact bookkeeping."""
from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from . import CONTRACT_VERSION, CORE_VERSION


class HookError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def number(value, name: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise HookError("invalid_request", f"{name} must be a number")
    if not math.isfinite(value) or not low <= value <= high:
        raise HookError("invalid_request", f"{name} must be in [{low}, {high}]")
    return float(value)


def finite(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def implementation_digest() -> str:
    # Development versions can remain unchanged while source evolves. A cache must
    # not mistake old code for the current fixed implementation.
    package = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for source in sorted(package.glob("*.py")):
        h.update(source.name.encode() + b"\0" + bytes.fromhex(digest(source)))
    return h.hexdigest()


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"), parse_constant=lambda s: (_ for _ in ()).throw(
        HookError("invalid_json", f"non-finite JSON value: {s}")))


def write_json(path: Path, value):
    # Never overwrite an earlier artifact; consumers choose a fresh run directory.
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
        stream.write("\n")


def envelope(kind: str, **values):
    return {"contract_version": CONTRACT_VERSION, "core_version": CORE_VERSION,
            "kind": kind, "created_at": datetime.now(timezone.utc).isoformat(), **values}


def source_file(path) -> Path:
    source = Path(path).expanduser().resolve(strict=True)
    if not source.is_file():
        raise HookError("invalid_source", "source must be a regular local file")
    with source.open("rb") as stream:
        stream.read(1)
    return source


def fresh_directory(path, source: Path | None = None) -> Path:
    out = Path(path).expanduser().resolve()
    if source and (out == source or source.is_relative_to(out)):
        raise HookError("source_collision", "output directory must not contain the source")
    out.mkdir(parents=True, exist_ok=False)
    return out


class Runner:
    """One request has a total wall-clock budget, including all child commands."""
    ALLOWED = frozenset({"ffmpeg", "ffprobe", "tesseract"})

    def __init__(self, timeout_s: float = 240):
        self.deadline = time.monotonic() + number(timeout_s, "timeout_s", 1, 300)
        self.ledger = []
        self.versions = {}

    def run(self, name: str, args: list[str], *, timeout_s=60):
        if name not in self.ALLOWED:
            raise HookError("invalid_tool", f"unsupported executable: {name}")
        executable = shutil.which(name)
        if not executable:
            raise HookError("missing_dependency", f"{name} is not installed")
        remaining = min(timeout_s, self.deadline - time.monotonic())
        if remaining <= 0:
            raise HookError("timeout", "request budget expired")
        start = time.monotonic()
        if name in ("ffmpeg", "ffprobe") and args not in (["-version"], ["--version"]):
            # A local playlist can otherwise make the decoder fetch remote media.
            # Host filesystem isolation remains responsible for authorized local access.
            args = ["-protocol_whitelist", "file,pipe", *args]
        try:
            result = subprocess.run([executable, *args], capture_output=True, text=True,
                                    timeout=remaining, check=False)
        except subprocess.TimeoutExpired as error:
            self.ledger.append({"tool": name, "status": "timeout"})
            raise HookError("timeout", f"{name} exceeded request budget") from error
        self.ledger.append({"tool": name, "status": "completed" if result.returncode == 0 else "failed",
                            "elapsed_s": round(time.monotonic() - start, 3),
                            "returncode": result.returncode})
        if result.returncode:
            raise HookError("tool_failed", f"{name}: {result.stderr[-1500:]}")
        return result

    def version(self, name: str):
        if name not in self.versions:
            flag = "--version" if name == "tesseract" else "-version"
            r = self.run(name, [flag], timeout_s=10)
            self.versions[name] = (r.stdout or r.stderr).splitlines()[0]
        return self.versions[name]
