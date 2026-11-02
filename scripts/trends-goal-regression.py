"""Regression coverage for request goals, cold starts, and allocated report references."""
from __future__ import annotations

import copy
import importlib.util
import json
import os
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
RUNTIME = Path(__file__).resolve().parents[1] / 'skills'
PROFILE_FIXTURES = Path(__file__).resolve().parent / 'fixtures/creator-profile'


def module(platform, script):
    path = RUNTIME / f"research-{platform}-trends/scripts/{script}.py"
    spec = importlib.util.spec_from_file_location(f"regression_{platform}_{script}", path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def inputs(platform):
    fmt = {"youtube": "youtube_video", "instagram": "instagram_reel", "tiktok": "tiktok_video"}[platform]
    request = {"schema_version": "trend-research-request.v3", "request_id": "goal-regression",
               "mode": "current_popular", "niche": "reviews and first looks of cozy and indie games",
               "platforms": [platform], "window": {"since": "2026-09-01T00:00:00Z", "until": "2026-10-20T00:00:00Z"},
               "formats": [fmt], "locale": {"region": None, "language": None},
               "creator_profile": {"handle": "owner", "signature_content": [
                   {"id": "career", "name": "Gaming blogging career advice", "topic": "blogging income",
                    "approach": "career advice", "weight": 1, "seed_terms": ["job", "could", "money", "over", "want"],
                    "seed_hashtags": [], "example_content_ids": []}]}}
    snapshot = {"schema_version": "public-content-snapshot.v1", "snapshot_id": "snap-goal",
                "observed_at": "2026-10-20T00:00:00Z", "platform": platform,
                "scope": {"window": {"start": "2026-09-01T00:00:00Z", "end": "2026-10-20T00:00:00Z"}},
                "source": {"coverage_complete": True, "source_mode": "fixture"},
                "creators": [{"creator_id": "a", "handle": "creator_a"}, {"creator_id": "b", "handle": "creator_b"}],
                "items": [{"content_id": cid, "creator_id": author,
                           "url": f"https://example.invalid/{platform}/{cid}", "title": f"Cozy game review {cid}",
                           "published_at": "2026-10-01T00:00:00Z", "format": fmt,
                           "metrics": {"views": {"status": "available", "value": views}}}
                          for cid, author, views in (("one", "a", 500), ("two", "b", 300), ("unseated", "b", 900))]}
    gate = {"schema_version": "trend-relevance-gate.v1", "items": [
        {"content_key": f"{platform}:{cid}", "relevance": "direct"} for cid in ("one", "two", "unseated")]}
    allocation = {"schema": "signature-content-allocation.v1", "rule_version": "signature-allocation.v1",
                  "slots": [{"content_key": f"{platform}:one", "signature_content_id": "career", "rank": 1},
                            {"content_key": f"{platform}:two", "signature_content_id": None, "rank": 2}],
                  "per_content": {"career": {"quota": 2, "filled": 1}}, "shortfall": 0}
    synthesis = {"schema_version": "trend-research-synthesis.v3.3", "request_id": request["request_id"],
                 "report_id": "report-goal", "status": "partial", "overview": {
                     "title": "Cozy game reviews", "summary": "Compare the reviewed games.", "key_findings": [],
                     "next_action": "Try a first-look review."}, "featured_content_refs": ["one", "two"],
                 "patterns": [], "emerging_creators": [], "data_gaps": []}
    return request, snapshot, gate, allocation, synthesis


class GoalAndColdStartTest(unittest.TestCase):
    def test_current_goal_queries_do_not_need_historical_seeds(self):
        for platform in ("instagram", "tiktok", "youtube"):
            with self.subTest(platform=platform):
                req, *_ = inputs(platform)
                script = "validate_tiktok_query_plan" if platform == "tiktok" else "validate_query_plan"
                validator = module(platform, script)
                queries = ["cozy game reviews", "indie game first impressions"]
                plan = {"schema_version": "tiktok-trend-query-plan.v1" if platform == "tiktok" else "trend-query-plan.v1",
                        "request_id": req["request_id"], "intents": [
                            {"intent_id": str(n), "search_query": q} for n, q in enumerate(queries)]}
                validator.validate(plan)
                validator.validate_against_request(plan, req)
                req["creator_profile"]["excluded_topics"] = ["cozy game"]
                with self.assertRaisesRegex(ValueError, "excluded"):
                    validator.validate_against_request(plan, req)

    def test_topic_eligibility_is_independent_of_historical_class_match(self):
        for platform in ("instagram", "tiktok", "youtube"):
            with self.subTest(platform=platform):
                gate = {"schema_version": "trend-relevance-gate.v3", "platform": platform, "items": [
                    {"content_key": "on-topic", "signature_content_id": None,
                     "semantic_scores": {"topic_relevance": 95, "locale_fit": 100, "creator_transferability": 70}},
                    {"content_key": "off-topic", "signature_content_id": "career",
                     "semantic_scores": {"topic_relevance": 10, "locale_fit": 100, "creator_transferability": 100}}]}
                self.assertEqual({"on-topic": "direct", "off-topic": "reject"},
                                 module(platform, "trend_gate_contract").classifications(gate))
                output = {"request_id": "goal", "platform": platform,
                          "subject": {"niche": "cozy game reviews"}, "items": []}
                header = json.loads(next(module(platform, "project_relevance_gate_input").table_parts(output))[1])
                self.assertIn("subject.niche", header["topic_relevance_rule"])
                self.assertEqual("cozy game reviews", header["subject"]["niche"])

    def test_missing_classes_keep_reliable_fields_without_inventing_a_topic(self):
        for platform in ("instagram", "tiktok", "youtube"):
            with self.subTest(platform=platform):
                profile = json.loads((PROFILE_FIXTURES / f"{platform}.json").read_text())
                profile.pop("effectiveContentProfile", None)
                profile["contentProfile"].update(signatureContent=[], signatureContentStatus="no_qualifying_group")
                profile["contentProfile"]["language"] = {"primary": "pt", "confidence": "high", "accepted": ["pt"]}
                req = inputs(platform)[0]
                req["creator_profile"] = {"handle": profile["account"]["handle"]}
                intake = module(platform, "attach_creator_profile")
                attached, receipt = intake.attach(req, profile, platform)
                self.assertTrue(receipt["attached"])
                self.assertEqual(req["niche"], attached["niche"])
                self.assertEqual("pt", attached["creator_profile"]["content_language"])
                self.assertEqual(profile["account"]["audienceCount"], attached["creator_profile"]["audience_count"])
                self.assertNotIn("signature_content", attached["creator_profile"])
                profile["contentProfile"]["language"]["confidence"] = "low"
                self.assertNotIn("content_language", intake.attach(req, profile, platform)[0]["creator_profile"])
                profile["contentProfile"] = None
                self.assertFalse(intake.attach(req, profile, platform)[1]["attached"])

    def test_without_a_profile_candidate_policy_is_unchanged(self):
        for platform in ("instagram", "tiktok", "youtube"):
            with self.subTest(platform=platform):
                req = {"creator_profile": {"handle": "owner"}}
                own = {"content_key": "own", "author": {"handle": "owner"}, "channel": {"id": "owner"}}
                candidates = {"candidate_set_id": "set", "items": [own, {"content_key": "ad", "is_ad": True},
                                                                          {"content_key": "unknown"}]}
                result = module(platform, "filter_candidate_eligibility").filter_set(req, candidates, platform)
                self.assertEqual(candidates["items"], result["items"])
                self.assertFalse(result["eligibility"]["applied"])
                self.assertEqual([], result["eligibility"]["backfilled"])
                self.assertEqual("set", result["candidate_set_id"])


class ReportSelectionTest(unittest.TestCase):
    def test_synthesis_output_preserves_full_analysis_and_hides_unselected_evidence(self):
        for platform in ("instagram", "tiktok", "youtube"):
            req, snapshot, gate, allocation, _ = inputs(platform)
            empty = {**allocation, "slots": [], "per_content": {"career": {"quota": 2, "filled": 0}}}
            for seats in (None, allocation, empty):
                with self.subTest(platform=platform, allocated=seats), tempfile.TemporaryDirectory() as tmp:
                    run = Path(tmp)
                    for name, value in (("snapshot", snapshot), ("request", req), ("gate", gate), ("allocation", seats)):
                        (run / f"{name}.json").write_text(json.dumps(value))
                    command = [sys.executable, "-B", str(RUNTIME / f"research-{platform}-trends/scripts/analyze_public_snapshot.py"),
                               "snapshot.json", "--mode", "trend", "--request", "request.json",
                               "--relevance-gate", "gate.json", "--output", "analysis.json", "--print-synthesis"]
                    if seats is not None:
                        command.extend(["--allocation", "allocation.json"])
                    result = subprocess.run(command, cwd=run, text=True, capture_output=True)
                    self.assertEqual(0, result.returncode, result.stdout + result.stderr)
                    saved = json.loads((run / "analysis.json").read_text())
                    shown = json.loads(result.stdout)
                    self.assertEqual(3, len(saved["ranked_content"]))
                    self.assertEqual(saved["data_quality"], shown["data_quality"])
                    if seats is None:
                        self.assertEqual(saved, shown, "runs without allocation keep their existing output")
                        continue
                    self.assertNotIn("unseated", json.dumps(shown), "unselected IDs and evidence aliases must not leak")
                    expected = {"one", "two"} if seats["slots"] else set()
                    self.assertEqual(expected, {row["content_id"] for row in shown["ranked_content"]})
                    self.assertEqual(expected, {row["content_id"] for row in shown["evidence"]})
                    self.assertTrue({row["content_id"] for row in shown["account_outliers"]} <= expected)
                    self.assertEqual([row for row in saved["ranked_content"] if row["content_id"] in expected],
                                     shown["ranked_content"], "selection must not change ranks or metrics")

    def test_null_class_is_selected_but_unseated_rows_cannot_escape_via_any_section(self):
        for platform in ("instagram", "tiktok", "youtube"):
            with self.subTest(platform=platform):
                req, snapshot, gate, allocation, synthesis = inputs(platform)
                analyzer = module(platform, "analyze_public_snapshot")
                analysis = analyzer.analyze(snapshot, "trend", None, request=req, relevance_gate=gate, allocation=allocation)
                self.assertEqual({"one", "two"}, set(analysis["selected_content_ids"]))
                self.assertEqual(3, len(analysis["ranked_content"]))
                self.assertIsNone(next(r for r in analysis["ranked_content"] if r["content_id"] == "two")["signature_content_id"])
                composer = module(platform, "compose_report")
                validator = module(platform, "validate_output")
                report = composer.compose(req, snapshot, analysis, synthesis)
                self.assertEqual([], validator.validate(report, req, analysis))
                for section in ("featured_content_refs", "patterns", "emerging_creators"):
                    bad = copy.deepcopy(synthesis)
                    if section == "featured_content_refs":
                        bad[section].append("unseated")
                    else:
                        bad[section] = [{"content_refs": ["unseated"]}]
                    with self.subTest(section=section), self.assertRaisesRegex(ValueError, "outside the allocation"):
                        composer.compose(req, snapshot, analysis, bad)
                bad_report = copy.deepcopy(report)
                injected = {**report["top_content"][-1], "rank": 3, "content_id": "unseated"}
                bad_report["top_content"].append(injected)
                self.assertTrue(any("outside the allocation" in e for e in validator.validate(bad_report, req, analysis)))
                legacy = {k: v for k, v in analysis.items() if k != "selected_content_ids"}
                with self.assertRaisesRegex(ValueError, "regenerate analysis"):
                    composer.compose(req, snapshot, legacy, synthesis)

    def test_empty_allocation_is_not_replaced_with_the_ranked_pool(self):
        for platform in ("instagram", "tiktok", "youtube"):
            req, snapshot, gate, allocation, synthesis = inputs(platform)
            allocation.update(slots=[], per_content={"career": {"quota": 2, "filled": 0}}, shortfall=2)
            analyzer = module(platform, "analyze_public_snapshot")
            analysis = analyzer.analyze(snapshot, "trend", None, request=req, relevance_gate=gate, allocation=allocation)
            self.assertEqual([], analysis["selected_content_ids"])
            with self.assertRaisesRegex(ValueError, "outside the allocation"):
                module(platform, "compose_report").compose(req, snapshot, analysis, synthesis)
            empty = {**synthesis, "featured_content_refs": []}
            report = module(platform, "compose_report").compose(req, snapshot, analysis, empty)
            self.assertEqual([], module(platform, "validate_output").validate(report, req, analysis))


class VideoDeliveryFallbackTest(unittest.TestCase):
    def candidates(self, platform):
        urls = {"youtube": "https://www.youtube.com/watch?v=", "instagram": "https://www.instagram.com/reel/",
                "tiktok": "https://www.tiktok.com/@creator/video/"}
        return {"platform": platform, "items": [
            {"content_key": f"{platform}:ref-{n}", "url": urls[platform] + str(10000000000 + n),
             "caption": f"A game to explore {n}", "published_at": None} for n in range(4)]}

    def empty_report_inputs(self, platform):
        req, snapshot, gate, allocation, synthesis = inputs(platform)
        for row in gate["items"]:
            row["relevance"] = "reject"
        allocation.update(slots=[], per_content={"career": {"quota": 2, "filled": 0}}, shortfall=2)
        synthesis.update(featured_content_refs=[], patterns=[], emerging_creators=[])
        analysis = module(platform, "analyze_public_snapshot").analyze(
            snapshot, "trend", None, request=req, relevance_gate=gate, allocation=allocation)
        return req, snapshot, analysis, synthesis

    def test_every_score_below_floor_still_delivers_source_links_without_changing_the_scores(self):
        for platform in ("instagram", "tiktok", "youtube"):
            with self.subTest(platform=platform):
                req, snapshot, analysis, synthesis = self.empty_report_inputs(platform)
                candidates = self.candidates(platform)
                gate = {"items": [{"content_key": row["content_key"],
                                   "semantic_scores": {"topic_relevance": n, "locale_fit": 0}}
                                  for n, row in enumerate(candidates["items"])]}
                original_gate = copy.deepcopy(gate)
                report = module(platform, "compose_report").compose(
                    req, snapshot, analysis, synthesis, reference_candidates=[candidates], reference_gates=[gate])
                self.assertEqual("partial", report["status"])
                self.assertEqual([], report["top_content"])
                self.assertEqual([row["url"] for row in reversed(candidates["items"][1:])],
                                 [row["url"] for row in report["reference_videos"]])
                self.assertEqual(original_gate, gate)
                self.assertEqual([], module(platform, "validate_output").validate(report, req, analysis, [candidates], [gate]))
                rendered = module(platform, "render_creator_report").render(report)
                for row in report["reference_videos"]:
                    self.assertIn(row["url"], rendered)
                    self.assertIsNone(row["published_at"], "a missing date is never fabricated")
                    self.assertNotIn("metrics", row, "retrieval references do not gain evidence-rank metrics")
                self.assertIn("did not qualify for a strong recommendation", rendered)

    def test_an_empty_synthesis_keeps_existing_selected_videos(self):
        for platform in ("instagram", "tiktok", "youtube"):
            with self.subTest(platform=platform):
                req, snapshot, gate, allocation, synthesis = inputs(platform)
                analysis = module(platform, "analyze_public_snapshot").analyze(
                    snapshot, "trend", None, request=req, relevance_gate=gate, allocation=allocation)
                synthesis["featured_content_refs"] = []
                report = module(platform, "compose_report").compose(
                    req, snapshot, analysis, synthesis, reference_candidates=[self.candidates(platform)])
                self.assertEqual({"one", "two"}, {row["content_id"] for row in report["top_content"]})
                self.assertNotIn("reference_videos", report)
                self.assertEqual([], module(platform, "validate_output").validate(report, req, analysis, [self.candidates(platform)]))
                rendered = module(platform, "render_creator_report").render(report)
                for item in report["top_content"]:
                    self.assertIn(item["url"], rendered)

    def test_driver_correction_keeps_candidates_and_then_delivers_references(self):
        for platform in ("instagram", "tiktok", "youtube"):
            with self.subTest(platform=platform), tempfile.TemporaryDirectory() as tmp:
                scripts = RUNTIME / f"research-{platform}-trends/scripts"
                made = subprocess.run([sys.executable, "-B", str(scripts / "static_host.py"),
                                       "init", "--workspace", tmp], text=True, capture_output=True)
                self.assertEqual(0, made.returncode, made.stdout + made.stderr)
                run = Path(made.stdout.strip())
                env = {**os.environ, "KABO_STATIC_RUN_DIR": str(run), "PYTHONDONTWRITEBYTECODE": "1"}

                def stage(name):
                    result = subprocess.run([sys.executable, "-B", str(scripts / "drive.py"), name],
                                            cwd=run, env=env, text=True, capture_output=True)
                    self.assertEqual(0, result.returncode, result.stdout + result.stderr)
                    self.assertNotIn("call: ", result.stdout, "report correction must not fetch data")
                    return result.stdout

                stage("start")
                req, snapshot, analysis, synthesis = self.empty_report_inputs(platform)
                candidates = self.candidates(platform)
                candidate_name = {"instagram": "trend-candidate-set.json", "tiktok": "tiktok-trend-candidate-set.json",
                                  "youtube": "youtube-trend-candidate-set.json"}[platform]
                for name, value in (("trend-research-request.json", req), ("public-content-snapshot.json", snapshot),
                                    ("public-content-analysis.json", analysis), (candidate_name, candidates)):
                    (run / name).write_text(json.dumps(value))
                source_bytes = (run / candidate_name).read_bytes()
                authored = run / "trend-research-synthesis.json"
                authored.write_text(json.dumps({**synthesis, "featured_content_refs": ["unseated"]}))
                rejected_bytes = authored.read_bytes()
                rejected = stage("report")
                self.assertIn('"status": "needs_correction"', rejected)
                self.assertEqual(rejected_bytes, authored.read_bytes())
                self.assertFalse((run / "trend-research-report.json").exists())

                authored.write_text(json.dumps(synthesis))
                delivered = stage("report")
                self.assertNotIn('"status": "needs_correction"', delivered)
                report = json.loads((run / "trend-research-report.json").read_text())
                self.assertEqual("partial", report["status"])
                self.assertEqual(3, len(report["reference_videos"]))
                markdown = (run / "creator-report.md").read_text()
                for item in report["reference_videos"]:
                    self.assertIn(item["url"], markdown)
                self.assertEqual(source_bytes, (run / candidate_name).read_bytes())

    def test_reference_links_are_bound_to_candidates_and_cannot_claim_complete(self):
        for platform in ("instagram", "tiktok", "youtube"):
            req, snapshot, analysis, synthesis = self.empty_report_inputs(platform)
            candidates = self.candidates(platform)
            report = module(platform, "compose_report").compose(
                req, snapshot, analysis, synthesis, reference_candidates=[candidates])
            validator = module(platform, "validate_output")
            forged = copy.deepcopy(report)
            forged["reference_videos"][0]["url"] += "-invented"
            self.assertTrue(any("source-backed" in error for error in validator.validate(forged, req, analysis, [candidates])))
            self.assertTrue(any("partial" in error for error in validator.validate({**report, "status": "complete"}, req, analysis, [candidates])))
            self.assertTrue(any("original request" in error for error in validator.validate(report, req, analysis)))

    def test_reference_fallback_does_not_invent_results_or_reinsert_explicit_exclusions(self):
        for platform in ("instagram", "tiktok", "youtube"):
            req, snapshot, analysis, synthesis = self.empty_report_inputs(platform)
            candidates = self.candidates(platform)
            req["creator_profile"]["excluded_topics"] = ["game to explore"]
            fallback = module(platform, "reference_fallback")
            self.assertEqual([], fallback.select(req, [candidates], []))
            req["creator_profile"]["excluded_topics"] = []
            self.assertEqual([], fallback.select(req, [{"items": []}], []))
            candidates["items"] = [candidates["items"][0]] * 3
            self.assertEqual(1, len(fallback.select(req, [candidates, candidates], [])))
            report = module(platform, "compose_report").compose(
                req, snapshot, analysis, synthesis, reference_candidates=[{"items": []}])
            self.assertNotIn("reference_videos", report)
            self.assertEqual([], module(platform, "validate_output").validate(report, req, analysis, [{"items": []}]))


if __name__ == "__main__":
    unittest.main()
