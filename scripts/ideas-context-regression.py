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
import report_language as presentation

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

    def run_builder(self, source):
        """Exercise direct rebuilds against an existing run's output directory."""
        result = subprocess.run([sys.executable, "-B", str(SKILL / "scripts/build_idea_request.py"),
            "--intent", str(self.root / "intent.json"), "--output", str(self.root / "request.json"),
            "--conversation-context", str(source)], capture_output=True, text=True)
        self.assertTrue(result.stdout, result.stderr)
        return result.returncode, json.loads(result.stdout)

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
        self.assertEqual(self.intent, {field: request[field] for field in self.intent})
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

    def test_invalid_direct_rebuild_preserves_existing_request_and_context(self):
        """Reject invalid replacement sources before overwriting retained evidence."""
        self.prep()
        retained = self.root / self.request()["conversation_context"]["path"]
        old_context = retained.read_bytes()
        old_request = (self.root / "request.json").read_bytes()
        bad_quote = copy.deepcopy(self.source)
        bad_quote["reference"]["quote"] = "not in source"
        bad_sources = [json.dumps(bad_quote), json.dumps({"messages": []}), "{broken json"]
        for index, text in enumerate(bad_sources):
            with self.subTest(index=index):
                source = self.root / f"bad-replacement-{index}.json"
                source.write_text(text)
                code, result = self.run_builder(source)
                self.assertEqual((1, "failed"), (code, result["status"]))
                self.assertEqual(old_context, retained.read_bytes())
                self.assertEqual(old_request, (self.root / "request.json").read_bytes())
                self.assertEqual([], validator.validate_request(self.request(), self.root / "request.json"))

    def test_valid_direct_rebuild_binds_the_replacement_bytes(self):
        """Allow a valid replacement and bind the rebuilt request to its exact bytes."""
        self.prep()
        replacement = copy.deepcopy(self.source)
        replacement["messages"][0]["text"] = "Use only ordinary survival-world materials."
        source = self.write("valid-replacement.json", replacement)
        code, result = self.run_builder(source)
        self.assertEqual((0, "ok"), (code, result["status"]))
        context = self.request()["conversation_context"]
        self.assertEqual(replacement["messages"], context["messages"])
        self.assertEqual(source.read_bytes(), (self.root / context["path"]).read_bytes())
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), context["sha256"])
        self.assertEqual([], validator.validate_request(self.request(), self.root / "request.json"))

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

    def test_legacy_chinese_schema_and_translation_errors_are_reported_together(self):
        """Keep translation diagnostics available for supported legacy Chinese reports."""
        self.intent["report_language"] = "zh"
        self.intent["language_targets"] = ["zh"]
        self.prep()
        output = self.output()
        del output["report_language"]
        output["overview"].update(headline="下一组选题", missing_context_effect="没有新的研究资料")
        for idea in output["ideas"]:
            idea["why_for_creator"] = "符合已声明的创作者背景"
        output["ideas"][0]["unexpected"] = True
        output["evidence_gap_translations"] = [{"source_text": "not an original source", "report_text": "错误来源"}]
        errors = validator.validate_idea_set(output, self.request(), self.root / "request.json")
        self.assertTrue(any("unexpected" in error for error in errors), errors)
        self.assertTrue(any("Presentation translation must name one unique original gap" in error for error in errors), errors)
        self.assertFalse(any("Missing report_language" in error for error in errors), errors)

    def test_unrecognized_legacy_prose_keeps_its_language_error(self):
        """Reject missing language metadata without hiding an independent schema error."""
        self.prep()
        output = self.output()
        del output["report_language"]
        output["ideas"][0]["unexpected"] = True
        errors = validator.validate_idea_set(output, self.request(), self.root / "request.json")
        self.assertTrue(any("unexpected" in error for error in errors), errors)
        self.assertTrue(any("Missing report_language" in error for error in errors), errors)

    def test_malformed_overview_does_not_crash_legacy_locale_detection(self):
        """Keep non-object prose containers away from legacy locale dereferences."""
        self.prep()
        for overview in (None, "malformed", ["headline"], True):
            with self.subTest(overview=overview):
                output = self.output()
                del output["report_language"]
                output["overview"] = overview
                errors = validator.validate_idea_set(output, self.request(), self.root / "request.json")
                self.assertTrue(any("overview" in error and "schema" in error for error in errors), errors)
                self.assertFalse(any("has no attribute" in error or "not iterable" in error for error in errors), errors)

    def test_invalid_brief_does_not_hide_independent_fallback_error(self):
        """简报格式错误不能遮住与简报无关的生成依据错误。"""
        self.intent["user_brief"] = []
        self.intent["generation_basis"]["allow_brief_fallback"] = True
        code, prep = self.prep()
        self.assertEqual((0, "needs_correction"), (code, prep["status"]))
        messages = "\n".join(issue["message"] for issue in prep["issues"])
        self.assertIn("user_brief", messages)
        self.assertIn("requires an explicit generation_basis.direct_topic", messages)

    def test_scalar_schema_errors_do_not_hide_missing_translations(self):
        """非法枚举仍可安全检查展示依赖，完整 schema 继续严格拒绝。"""
        self.prep()
        for field, value in (("status", "ready"), ("task_intent", "shortlist"),
                             ("source_mode", "unknown")):
            with self.subTest(field=field):
                output = self.output()
                output[field] = value
                output["evidence_gap_translations"] = []
                errors = "\n".join(validator.validate_idea_set(output, self.request(), self.root / "request.json"))
                self.assertIn("schema", errors)
                self.assertIn("every rendered canonical value", errors)

    def test_multiple_bad_translations_and_missing_values_report_together(self):
        """一次汇总全部映射错误，不用第二轮才揭示下一项。"""
        self.prep()
        output = self.output()
        output["evidence_gap_translations"] = [
            {"source_text": value, "report_text": value} for value in ("made-up-state", "choose", "answer_production_question")]
        errors = "\n".join(validator.validate_idea_set(output, self.request(), self.root / "request.json"))
        for value in ("made-up-state", "choose", "answer_production_question"):
            self.assertIn("source_text " + repr(value), errors)
        self.assertIn("every rendered canonical value", errors)

    def test_unused_schema_display_translations_do_not_block_or_change_the_report(self):
        """合法备用枚举翻译可以保留，但不能渲染成来源、缺口或当前状态。"""
        from render_idea_report import concise
        self.prep()
        output = self.output()
        before = concise(output, self.request())
        missing = presentation.CANONICAL_DISPLAY_VOCABULARY - {
            item["source_text"] for item in output["evidence_gap_translations"]}
        output["evidence_gap_translations"].extend(
            {"source_text": value, "report_text": "备用翻译"} for value in sorted(missing))
        self.assertEqual([], validator.validate_idea_set(output, self.request(), self.root / "request.json"))
        self.assertEqual(before, concise(output, self.request()))
        self.assertNotIn("unknown", presentation.report_value_translations(output))
        output["evidence_gap_translations"].append({"source_text": "https://invented.example/evidence", "report_text": "虚构证据"})
        self.assertTrue(validator.validate_idea_set(output, self.request(), self.root / "request.json"))

    def test_absent_production_constraints_need_no_invented_user_input(self):
        """空制作条件可在同一轮改为 absent，不必虚构限制或向用户索取。"""
        constraints = self.intent["production_constraints"]
        for field in constraints:
            constraints[field] = [] if field == "available_assets" else None
        constraints["status"] = "partial"
        code, result = self.prep()
        self.assertEqual((0, "needs_correction"), (code, result["status"]))
        self.assertTrue(any("set status=absent" in row["message"] for row in result["issues"]))
        constraints["status"] = "absent"
        code, result = self.prep()
        self.assertEqual((0, "ready_for_generation"), (code, result["decision"]["status"]))

    def test_missing_schema_fields_do_not_hide_missing_translations(self):
        """缺失必填字段不能遮住独立展示映射错误。"""
        self.prep()
        for field in ("status", "task_intent", "source_mode", "evidence_summary", "ideas"):
            with self.subTest(field=field):
                output = self.output()
                del output[field]
                output["evidence_gap_translations"] = []
                errors = "\n".join(validator.validate_idea_set(output, self.request(), self.root / "request.json"))
                self.assertIn("schema", errors)
                self.assertIn("every rendered canonical value", errors)

    def test_missing_intent_path_preserves_existing_input_budget(self):
        """调用路径错误既不消耗也不重置已有修订预算。"""
        missing = self.root / "missing-intent.json"
        code, result = self.run_stage("prep", "--intent", missing)
        self.assertEqual((1, "invocation_error", 0), (code, result["status"], result["input_attempts"]))
        self.assertFalse((self.root / "stage_timing.json").exists())
        self.source["reference"]["quote"] = "not in source"
        self.assertEqual("needs_correction", self.prep()[1]["status"])
        before = (self.root / "stage_timing.json").read_bytes()
        code, result = self.run_stage("prep", "--intent", missing)
        self.assertEqual((1, "invocation_error", 1), (code, result["status"], result["input_attempts"]))
        self.assertEqual(before, (self.root / "stage_timing.json").read_bytes())

    def test_conditional_estimate_keeps_assumptions_without_required_assets(self):
        """没有额外资源依赖时，条件估时的前提仍须在两种报告中可见。"""
        self.prep()
        output = self.output()
        assumption = "Estimate assumes one uninterrupted recording session."
        feasibility = output["ideas"][0]["production_feasibility"]
        feasibility.update(status="requires_user_confirmation", required_assets=[],
                           assets_used=[], assumptions=[assumption])
        output["gates"]["production_missing"] = [assumption]
        output["interaction"]["next_actions"].append("answer_production_question")
        self.write("idea_set.json", output)
        code, result = self.run_stage("core")
        self.assertEqual((0, "ok"), (code, result["status"]), result)
        from render_idea_report import concise, full, validate_concise
        for report in (self.root / "RESULT.md", self.root / "IDEA_REPORT_FULL.md"):
            self.assertIn(assumption, report.read_text())
        for request in (None, self.request()):
            for render in (concise, full):
                report = render(output, request)
                self.assertIn("20–30", report)
                self.assertIn("Before production", report)
                self.assertIn(assumption, report)
        markdown = concise(output, self.request())
        with self.assertRaises(ValueError):
            validate_concise(markdown.replace(assumption, ""), output, self.request())

    def test_pending_resources_are_visible_without_becoming_available(self):
        """候选可声明未来资源，已拥有与产能约束保持严格。"""
        self.prep()
        output = self.output()
        feasibility = output["ideas"][0]["production_feasibility"]
        feasibility.update(status="requires_user_confirmation", required_assets=["A consenting guest"],
                           assumptions=["Confirm a guest before recording."])
        output["gates"]["production_missing"] = ["Confirm a guest before recording."]
        output["interaction"]["next_actions"].append("answer_production_question")
        self.write("idea_set.json", output)
        code, result = self.run_stage("core")
        self.assertEqual((0, "ok"), (code, result["status"]), result)
        self.assertIn("To confirm: A consenting guest", (self.root / "RESULT.md").read_text())
        from render_idea_report import concise, full
        output["report_language"] = "fr"
        for report in (concise(output, self.request()), full(output, self.request())):
            self.assertIn("Avant la réalisation", report)
            self.assertIn("À confirmer: A consenting guest", report)
            self.assertNotIn("Before production", report)
        output["report_language"] = self.request()["report_language"]
        feasibility["assets_used"] = ["A consenting guest"]
        errors = validator.validate_idea_set(output, self.request(), self.root / "request.json")
        self.assertTrue(any("assets_used must be a subset" in error for error in errors), errors)


if __name__ == "__main__":
    unittest.main()
