"""Run-local, source-bound display translations; facts never belong to the translator."""
from __future__ import annotations

import hashlib
import json
import re
import string
from pathlib import Path
from typing import Any

VERSION = "tiktok-competitor-presentation.v1"
# Protect literal facts in script-owned text, not arbitrary language meaning. Quote titles use
# the same protection, so e.g. dimensions cannot disappear during translation.
LITERAL = re.compile(r"https?://[^\s<>\]\)]+|@[\w.]+|\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)?|\d+(?:[,.]\d+)*(?:%|×|x)?|[{}]", re.UNICODE)
TOKEN = re.compile(r"\{([a-z][a-z0-9_]*)\}")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]").replace("*", "\\*").replace("_", "\\_").replace("`", "\\`").replace("<", "&lt;").replace(">", "&gt;")


class Display:
    def __init__(self, report_bytes: bytes, language: str, supplied: dict[str, Any] | None = None):
        if not isinstance(language, str) or not language.strip():
            raise ValueError("presentation language must be explicit")
        self.report = json.loads(report_bytes)
        self.header = {"schema_version": VERSION, "language": language,
                       "report_sha256": digest(report_bytes)}
        self.slots: list[dict[str, Any]] = []
        self.used: dict[str, str] = {}
        # Slots whose translation was rejected and shown in the source text instead, with why.
        self.kept_source: dict[str, str] = {}
        self.supplied = supplied
        if supplied is not None:
            # Like a compact slot, an omitted header field is restored from the bound report; one
            # that is present must still match it exactly.
            if not isinstance(supplied, dict) or "slots" not in supplied or not set(supplied) <= {*self.header, "slots"}:
                raise ValueError("presentation must hold slots and only the bound header fields")
            changed = sorted(k for k, v in self.header.items() if k in supplied and supplied[k] != v)
            if changed:
                raise ValueError("presentation report/language/header binding changed: " + ", ".join(changed))
            supplied = {**self.header, **supplied}
            self.supplied = supplied
            if not isinstance(supplied["slots"], list):
                raise ValueError("presentation slots must be a list")
            # Slots are matched by id and only their text is used; every other field is recomputed
            # from the bound report, so a mis-copied hash or a reordered list cannot change output.
            self.by_id: dict[str, dict[str, Any]] = {}
            for item in supplied["slots"]:
                if not isinstance(item, dict) or not isinstance(item.get("id"), str) or "text" not in item:
                    raise ValueError("presentation slot must hold an id and a text")
                if item["id"] in self.by_id:
                    raise ValueError(f"duplicate presentation slot {item['id']}")
                self.by_id[item["id"]] = item

    def assert_report(self, report: dict[str, Any]) -> None:
        if report != self.report:
            raise ValueError("presentation report object differs from its bound bytes")

    def text(self, slot_id: str, kind: str, source: str, source_ref: str,
             insertions: dict[str, str] | None = None, source_identity: str | None = None) -> str:
        facts = dict(insertions or {})
        # Named insertions were already mechanically cut from this source (e.g. a quoted title).
        template = source
        for name, value in facts.items():
            if template.count(value) != 1:
                raise ValueError(f"{slot_id}: ambiguous source insertion {name}")
            template = template.replace(value, "{" + name + "}", 1)
        # Do not rescan inserted placeholder identifiers (which can contain digits).
        pieces = []
        cursor = 0
        for match in TOKEN.finditer(template):
            if match[1] not in facts:
                continue
            pieces.append(self._protect(template[cursor:match.start()], facts))
            pieces.append(match[0]); cursor = match.end()
        pieces.append(self._protect(template[cursor:], facts))
        template = "".join(pieces)
        slot = {"id": slot_id, "kind": kind, "source_ref": source_ref, "source_text": source,
                "source_sha256": digest(source.encode()),
                "source_identity_sha256": digest((source_identity if source_identity is not None else source).encode()),
                "source_template": template, "facts": facts, "text": ""}
        if slot_id in self.used:
            if self.used[slot_id] != json.dumps(slot, sort_keys=True, ensure_ascii=False):
                raise ValueError(f"conflicting presentation slot {slot_id}")
        else:
            self.used[slot_id] = json.dumps(slot, sort_keys=True, ensure_ascii=False)
            self.slots.append(slot)
        if self.supplied is None:
            return source
        if slot_id not in self.by_id:
            raise ValueError(f"missing presentation slot {slot_id}")
        text = self.by_id[slot_id]["text"]
        problem = self._rejected(text, facts)
        if problem:
            # A rejected translation is shown in its source text, which carries every fact as
            # given; the report is still delivered instead of failing on one title (plugins,
            # 2026-10-28: "Oct 15" translated as "10 月 {fact_0} 日" added the literal 10).
            self.kept_source[slot_id] = problem
            return source
        if text == template:
            return source
        return "".join(escape(literal) + (facts[field] if field is not None else "")
                       for literal, field, _, _ in string.Formatter().parse(text))

    @staticmethod
    def _rejected(text: Any, facts: dict[str, str]) -> str | None:
        if not isinstance(text, str) or not text.strip() or "\n" in text or "\r" in text:
            return "presentation text must be one nonempty line"
        # A translator may move typed facts for grammar, but must use every one exactly once.
        try:
            fields = [field for _, field, spec, conversion in string.Formatter().parse(text)
                      if field is not None]
            valid_fields = sorted(fields) == sorted(facts) and all(
                not spec and conversion is None for _, field, spec, conversion in string.Formatter().parse(text) if field is not None)
        except ValueError:
            valid_fields = False
        if not valid_fields:
            return "presentation placeholders changed"
        # All literal facts already have typed slots; adding new literal numbers/URLs/handles
        # outside them would turn a presentation into a new factual claim.
        if LITERAL.search(TOKEN.sub("", text)):
            return "presentation added literal fact"
        return None

    def identity(self, slot_id: str) -> bool:
        if self.supplied is None or slot_id in self.kept_source:
            return True
        bound = next(item for item in self.slots if item["id"] == slot_id)
        return self.by_id[slot_id]["text"] == bound["source_template"]

    @staticmethod
    def _protect(text: str, facts: dict[str, str]) -> str:
        def protect(match: re.Match[str]) -> str:
            name = f"fact_{len(facts)}"
            facts[name] = match[0]
            return "{" + name + "}"
        return LITERAL.sub(protect, text)

    def contract(self) -> dict[str, Any]:
        if self.supplied is not None and set(self.by_id) != {slot["id"] for slot in self.slots}:
            unknown = sorted(set(self.by_id) - {slot["id"] for slot in self.slots})
            raise ValueError("unknown presentation slots: " + ", ".join(unknown))
        return {**self.header, "slots": self.slots}


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("presentation must be an object")
    return value
