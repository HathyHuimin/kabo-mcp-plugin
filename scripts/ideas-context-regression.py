#!/usr/bin/env python3
"""Check input provenance and diagnostics, not model style or creative quality."""
import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills/develop-content-ideas"
sys.dont_write_bytecode = True
sys.path.insert(0, str(SKILL / "scripts"))
import validate_ideas as validator

FIXTURE = json.loads((ROOT / "scripts/fixtures/ideas-continuation.json").read_text())


class ConversationContract(unittest.TestCase):
    def setUp(self):
        """Prepare one isolated follow-up and the exact earlier visible messages."""
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.intent = copy.deepcopy(FIXTURE["intent"])
        self.source = {
            "schema_version": "idea-conversation.v1",
            "messages": [
                {"role": "user", "text": "Keep each challenge possible in an ordinary survival world."},
                {"role": "assistant", "text": "1. One tool unlocks the next biome.\n2. A timed race unlocks equipment.\nThese are proposals, not tested performance claims."},
            ],
            "reference": {"message_index": 1, "quote": "1. One tool unlocks the next biome."},
        }
        self.source_path = self.root / "visible-messages.json"

    def write(self, name, data):
        """Persist a fixture with stable UTF-8 text and return its run-local path."""
        path = self.root / name
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        return path

    def run_stage(self, stage, *flags):
        """Exercise the real prep or core CLI and parse its structured result."""
        result = subprocess.run([sys.executable, "-B", str(SKILL / "scripts/run_ideas_stages.py"),
            stage, "--run-dir", str(self.root), *map(str, flags)], capture_output=True, text=True)
        self.assertTrue(result.stdout, result.stderr)
        return result.returncode, json.loads(result.stdout)

    def prep(self, with_source=True, *flags):
        """Supply the authored intent and optional original conversation artifact."""
        intent = self.write("intent.json", self.intent)
        source_flags = []
        if with_source:
            self.write(self.source_path.name, self.source)
            source_flags = ["--conversation-context", self.source_path]
        return self.run_stage("prep", "--intent", intent, *source_flags, *flags)

    def request(self):
        """Read the request that the builder actually retained for core."""
        return json.loads((self.root / "request.json").read_text())

    def output(self):
        """Build three structurally valid proposals without grading creative quality."""
        output = copy.deepcopy(FIXTURE["idea_set"])
        output["ideas"] = []
        for rank in range(1, 4):
            idea = copy.deepcopy(FIXTURE["idea"])
            idea.update(idea_id=f"idea-{rank:03d}", rank=rank,
                        working_title=f"A workflow boundary worth showing ({rank})")
            output["ideas"].append(idea)
        return output

    def test_exact_chat_and_current_request_are_separate_through_core(self):
        """Keep the current query and historical bytes separate through report delivery."""
        code, prep = self.prep()
        self.assertEqual((0, "ok", "ready_for_generation"),
                         (code, prep["status"], prep["decision"]["status"]))
        request = self.request()
        self.assertEqual(self.intent["user_brief"], request["user_brief"])
        self.assertEqual(self.intent["exclusions"], request["exclusions"])
        self.assertIsNone(request["generation_basis"]["direct_topic"])
        self.assertEqual(3, request["idea_count"])
        context = request["conversation_context"]
        self.assertEqual(self.source["messages"], context["messages"])
        self.assertEqual(self.source["reference"], context["reference"])
        self.assertEqual(hashlib.sha256(self.source_path.read_bytes()).hexdigest(), context["sha256"])
        self.assertEqual(self.source_path.read_bytes(), (self.root / context["path"]).read_bytes())
        self.source_path.unlink()
        self.write("idea_set.json", self.output())
        code, core = self.run_stage("core")
        self.assertEqual((0, "ok", 1), (code, core["status"], core["generation_attempts"]))
        self.assertTrue((self.root / "RESULT.md").stat().st_size)

    def test_missing_chat_needs_context_and_not_a_repeated_user_request(self):
        """Preserve the missing-context decision without inventing a source."""
        del self.intent["generation_basis"]["continuation_quote"]
        code, prep = self.prep(False)
        self.assertEqual((0, "needs_selection_context"), (code, prep["decision"]["status"]))

    def test_chat_cannot_replace_required_research(self):
        """Keep account research requirements blocking despite available chat context."""
        self.intent["generation_basis"]["required_sources"] = ["account"]
        code, prep = self.prep()
        self.assertEqual((0, "blocked_missing_required_source"), (code, prep["decision"]["status"]))
        self.assertEqual(["account"], prep["decision"]["missing_required_sources"])
        self.assertEqual([], prep["decision"]["available_sources"])
        self.assertFalse(prep["decision"]["brief_fallback_used"])

    def test_chat_cannot_be_cited_as_research(self):
        """Reject a chat reference presented as verified research evidence."""
        self.prep()
        request = self.request()
        self.assertEqual("brief_only", request["source_mode"])
        self.assertEqual([], request["upstream_sources"])
        output = self.output()
        output["ideas"][0].update(evidence_basis="source_backed", evidence_refs=["conversation_context"])
        errors = validator.validate_idea_set(output, request, self.root / "request.json")
        self.assertTrue(any("evidence_refs" in error for error in errors), errors)

    def test_wrong_index_reports_exact_match_without_silently_rebinding(self):
        """Suggest the exact source index but require an explicit caller correction."""
        self.source["reference"]["message_index"] = 0
        code, prep = self.prep()
        self.assertEqual((0, "needs_correction"), (code, prep["status"]))
        messages = "\n".join(issue["message"] for issue in prep["issues"])
        self.assertIn("only at zero-based message_index=1", messages)
        self.assertEqual(0, json.loads(self.source_path.read_text())["reference"]["message_index"])
        self.assertFalse((self.root / "request.json").exists())
        self.source["reference"]["message_index"] = 1
        self.assertEqual("ok", self.prep()[1]["status"])

    def test_empty_forged_or_out_of_range_reference_is_rejected(self):
        """Reject malformed text, reference indices and forged binding metadata."""
        mutations = [
            lambda s: s.update(messages=[]),
            lambda s: s["messages"][0].update(role="system"),
            lambda s: s["messages"][0].update(text="  "),
            lambda s: s["reference"].update(message_index=-1),
            lambda s: s["reference"].update(message_index=2),
            lambda s: s["reference"].update(message_index=True),
            lambda s: s["reference"].update(quote="paraphrased idea"),
            lambda s: s.update(sha256="forged-digest"),
            lambda s: s.update(path="forged-path"),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                source = copy.deepcopy(self.source)
                mutate(source)
                path = self.write(f"invalid-{index}.json", source)
                with self.assertRaises(ValueError):
                    validator.bound_conversation_context(path, self.root)

    def test_source_bytes_cannot_change_after_prep(self):
        """Stop core when the retained conversation bytes no longer match their digest."""
        self.prep()
        self.write("idea_set.json", self.output())
        source = self.root / self.request()["conversation_context"]["path"]
        source.write_bytes(source.read_bytes() + b"\n")
        code, core = self.run_stage("core")
        self.assertEqual((1, "failed"), (code, core["status"]))
        self.assertIn("checksum", core["error"])
        self.assertFalse((self.root / "RESULT.md").exists())

    def test_one_correction_only_and_no_reset_after_exhaustion(self):
        """Keep an exhausted preparation budget final after a source is corrected."""
        self.source["reference"]["quote"] = "not in source"
        self.assertEqual("needs_correction", self.prep()[1]["status"])
        code, prep = self.prep()
        self.assertEqual((3, "not_met"), (code, prep["status"]))
        before = (self.root / "stage_timing.json").read_bytes()
        self.source["reference"]["quote"] = "1. One tool unlocks the next biome."
        self.assertEqual(3, self.prep()[0])
        self.assertEqual(before, (self.root / "stage_timing.json").read_bytes())

    def test_no_history_topic_is_smuggled_into_current_direct_topic(self):
        """Reject historical topics represented as literal words in the current query."""
        self.intent["generation_basis"]["direct_topic"] = "ordinary survival world"
        code, prep = self.prep()
        self.assertEqual((0, "needs_correction"), (code, prep["status"]))
        self.assertTrue(any(issue["code"] == "invalid_direct_topic" for issue in prep["issues"]))

    def test_current_continuation_quote_must_be_literal(self):
        """Reject a continuation trigger that the current user request never contains."""
        self.intent["generation_basis"]["continuation_quote"] = "not in current request"
        self.assertEqual("needs_correction", self.prep()[1]["status"])

    def test_current_explicit_topic_still_works_without_history(self):
        """Preserve the direct-topic route for a self-contained current request."""
        self.intent["user_brief"]["text"] = "Give me three workflow ideas."
        self.intent["generation_basis"].pop("continuation_quote")
        self.intent["generation_basis"]["direct_topic"] = "workflow"
        code, prep = self.prep(False)
        self.assertEqual((0, "ready_for_generation"), (code, prep["decision"]["status"]))
        self.assertNotIn("conversation_context", self.request())

    def test_existing_native_ideas_route_remains_supported(self):
        """Retain compatibility with an original native IdeaSet as creative history."""
        previous = self.output()
        previous["request_id"] = "earlier-request"
        path = self.write("earlier-ideas.json", previous)
        code, prep = self.prep(False, "--previous-ideas", path)
        self.assertEqual((0, "ready_for_generation"), (code, prep["decision"]["status"]))
        self.assertEqual("earlier-request", self.request()["continuation_context"]["request_id"])
        self.assertNotIn("conversation_context", self.request())

    def test_missing_chat_file_can_be_restored_in_one_correction(self):
        """Allow the caller to restore the missing source within the existing budget."""
        code, prep = self.prep(False, "--conversation-context", self.source_path)
        self.assertEqual((0, "needs_correction"), (code, prep["status"]))
        self.assertTrue(any(issue["code"] == "missing_conversation_context" for issue in prep["issues"]))
        self.assertEqual("ok", self.prep()[1]["status"])

    def test_both_source_routes_are_rejected(self):
        """Reject simultaneous native IdeaSet and plain-chat continuation sources."""
        previous = self.write("previous.json", {})
        code, prep = self.prep(True, "--previous-ideas", previous)
        self.assertEqual((0, "needs_correction"), (code, prep["status"]))
        self.assertTrue(any("choose either" in issue["message"] for issue in prep["issues"]))

    def test_missing_file_and_independent_reference_error_are_reported_together(self):
        """Report unavailable upstream evidence and an invalid chat quote in one response."""
        self.source["reference"]["quote"] = "not in source"
        code, prep = self.prep(True, "--account-review", self.root / "missing-account.json")
        self.assertEqual((0, "needs_correction"), (code, prep["status"]))
        self.assertTrue(any(issue["code"] == "missing_artifact" for issue in prep["issues"]))
        self.assertTrue(any("reference.quote" in issue["message"] for issue in prep["issues"]))

    def test_schema_errors_do_not_hide_current_topic_and_derived_field_errors(self):
        """Keep independent topic and source-provenance errors visible beside schema errors."""
        self.intent["idea_count"] = "three"
        self.intent["generation_basis"]["direct_topic"] = "ordinary survival world"
        self.intent["conversation_context"] = self.source
        code, prep = self.prep()
        self.assertEqual((0, "needs_correction"), (code, prep["status"]))
        messages = "\n".join(issue["message"] for issue in prep["issues"])
        for expected in ("idea_count", "literal user-supplied phrase", "request.conversation_context"):
            self.assertIn(expected, messages)

    def test_schema_and_missing_presentation_translation_are_reported_together(self):
        """Report missing display translations in the same pass as schema failures."""
        self.prep()
        output = self.output()
        output["ideas"][0]["unexpected"] = True
        output["evidence_gap_translations"] = []
        errors = validator.validate_idea_set(output, self.request(), self.root / "request.json")
        self.assertTrue(any("unexpected" in error for error in errors), errors)
        self.assertTrue(any("Explicit report languages require evidence_gap_translations" in error for error in errors), errors)

    def test_core_reports_both_errors_then_uses_only_one_revision(self):
        """Deliver after one revision fixes the schema and translation failures together."""
        self.prep()
        output = self.output()
        output["ideas"][0]["unexpected"] = True
        output["evidence_gap_translations"] = []
        self.write("idea_set.json", output)
        code, first = self.run_stage("core")
        self.assertEqual((0, "needs_revision", 1), (code, first["status"], first["revision_remaining"]))
        messages = "\n".join(first["errors"])
        self.assertIn("unexpected", messages)
        self.assertIn("evidence_gap_translations", messages)
        self.assertFalse((self.root / "RESULT.md").exists())
        self.write("idea_set.json", self.output())
        code, revised = self.run_stage("core")
        self.assertEqual((0, "ok", 2), (code, revised["status"], revised["generation_attempts"]))

    def test_unsafe_schema_shapes_do_not_become_presentation_exceptions(self):
        """Return structural diagnostics without incidental type or attribute exceptions."""
        self.prep()
        for broken in (None, [], [None], [{"production_feasibility": "wrong type"}]):
            with self.subTest(ideas=broken):
                output = self.output()
                output["ideas"] = broken
                errors = validator.validate_idea_set(output, self.request(), self.root / "request.json")
                self.assertTrue(any("schema" in error for error in errors), errors)
                self.assertFalse(any("has no attribute" in error or "not iterable" in error for error in errors), errors)


if __name__ == "__main__":
    unittest.main()
