"""语言作为独立准入条件；高播放、英语标题和报告翻译不能替它放行。"""
from __future__ import annotations

import copy
import importlib.util
import sys
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("topic_tests", Path(__file__).with_name("short-window-topic-regression.py"))
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)


def request(languages=None, mode="restricted"):
    return {**base.REQUEST, "language_scope": {
        "mode": mode, "accepted_languages": languages if languages is not None else ["en"],
        "basis": "The matching creator's supplied account context names these accepted content languages.",
    }}


def judged(projected, overrides=None):
    rows = base.decisions(projected)
    for row in rows:
        row["language"] = {"decision": "match", "languages": ["en"],
                           "evidence": [{"source": "description", "quote": "Complete explanation"}],
                           "reason": "The complete instructional description is English; speech was not inspected."}
        row["language"].update((overrides or {}).get(row["content_id"], {}))
    return rows


class LanguageGateTests(unittest.TestCase):
    def test_no_platform_can_build_without_language_review(self):
        for platform in ("instagram", "tiktok", "youtube"):
            _, builder, _ = base.setup(platform)
            with self.subTest(platform=platform), self.assertRaisesRegex(ValueError, "requires topic"):
                builder.build(base.payload(platform, [base.item("a")]))

    def test_tiktok_provider_caption_in_title_is_preserved_without_promoting_youtube_titles(self):
        for platform, source_field in (("tiktok", "tiktok_caption"), ("youtube", "title")):
            gate, builder, _ = base.setup(platform)
            source = base.payload(platform, [base.item("a", title="I explain how to edit this video step by step.",
                                                        caption="", description="")])
            projected = gate.project(source, request())
            self.assertEqual(projected["items"][0]["language_evidence"][0]["source"], source_field)
            decisions = judged(projected, {"a": {"evidence": [{"source": source_field,
                "quote": "I explain how to edit this video step by step."}]}})
            if platform == "tiktok":
                self.assertEqual(len(builder.build(source, projected, decisions)["heat"]), 1)
            else:
                with self.assertRaisesRegex(ValueError, "title alone"):
                    builder.build(source, projected, decisions)

    def test_all_platforms_exclude_mismatch_and_unknown_before_ranking(self):
        for platform in ("instagram", "tiktok", "youtube"):
            with self.subTest(platform=platform):
                gate, builder, renderer = base.setup(platform)
                source = base.payload(platform, [base.item("english", views=1),
                    base.item("hindi", views=9000000, description="Dosto, video kaise banaye"),
                    base.item("unknown", views=8000000, description="", caption="")])
                projected = gate.project(source, request())
                decisions = judged(projected, {
                    "hindi": {"decision": "mismatch", "languages": ["hi"],
                              "evidence": [{"source": "description", "quote": "Dosto, video kaise banaye"}]},
                    "unknown": {"decision": "unknown", "languages": [], "evidence": []},
                })
                result = builder.build(source, projected, decisions)
                self.assertEqual([row["content_id"] for row in result["heat"]], ["english"])
                self.assertEqual(result["heat"], result["in_window"])
                self.assertEqual(result["topic_gate"]["language_gate"]["on_topic_counts"],
                                 {"match": 1, "mismatch": 1, "unknown": 1, "not_applicable": 0})
                report = renderer.render(result)
                self.assertIn("1 mismatches and 1 unknowns", report)
                self.assertIn("not verified spoken language", report)

    def test_english_title_cannot_override_declared_hindi_audio(self):
        gate, builder, _ = base.setup("youtube")
        source = base.payload("youtube", [base.item("wrong", content_signals={"default_audio_language": "hi"})])
        projected = gate.project(source, request())
        with self.assertRaisesRegex(ValueError, "declared audio"):
            builder.build(source, projected, judged(projected))
        decisions = judged(projected, {"wrong": {"decision": "mismatch", "languages": ["hi"],
            "evidence": [{"source": "audio_language", "quote": "hi"}]}})
        self.assertEqual(builder.build(source, projected, decisions)["heat"], [])

    def test_title_only_unknown_is_not_silently_english(self):
        gate, builder, _ = base.setup("instagram")
        source = base.payload("instagram", [base.item("title", caption="", description="")])
        projected = gate.project(source, request())
        decisions = judged(projected, {"title": {"evidence": [{"source": "title", "quote": "Video editing"}]}})
        with self.assertRaisesRegex(ValueError, "title alone"):
            builder.build(source, projected, decisions)

    def test_original_quotes_are_required_not_report_translations_or_another_item(self):
        gate, builder, _ = base.setup("tiktok")
        source = base.payload("tiktok", [base.item("a"), base.item("b", description="Different English description")])
        projected = gate.project(source, request())
        for quote in ("完整说明", "Different English description"):
            decisions = judged(projected)
            decisions[0]["language"]["evidence"][0]["quote"] = quote
            with self.subTest(quote=quote), self.assertRaisesRegex(ValueError, "original source"):
                builder.build(source, projected, decisions)

    def test_explicit_multilingual_scope_and_regional_variants(self):
        gate, builder, _ = base.setup("youtube")
        source = base.payload("youtube", [base.item("bilingual", content_signals={"default_audio_language": "es-MX"})])
        projected = gate.project(source, request(["en", "es"]))
        decisions = judged(projected, {"bilingual": {"languages": ["es-MX"],
            "evidence": [{"source": "audio_language", "quote": "es-MX"}]}})
        self.assertEqual(len(builder.build(source, projected, decisions)["heat"]), 1)
        projected = gate.project(source, request(["en"]))
        with self.assertRaisesRegex(ValueError, "accepted language"):
            builder.build(source, projected, decisions)

    def test_unknown_scope_stops_ranking_and_unrestricted_scope_is_explicit(self):
        for platform in ("instagram", "tiktok", "youtube"):
            gate, builder, renderer = base.setup(platform)
            source = base.payload(platform, [base.item("a")])
            for mode, count in (("unknown", 0), ("unrestricted", 1)):
                projected = gate.project(source, request([], mode))
                result = builder.build(source, projected, base.decisions(projected))
                self.assertEqual(len(result["heat"]), count)
                self.assertIn("Content language" if count else "could not be established", renderer.render(result))
            with self.assertRaisesRegex(ValueError, "language_scope"):
                gate.project(source, {k: v for k, v in base.REQUEST.items() if k != "language_scope"})

    def test_no_speech_is_not_inferred_from_missing_captions(self):
        gate, builder, _ = base.setup("youtube")
        source = base.payload("youtube", [base.item("a", content_signals={"default_audio_language": "zxx"})])
        projected = gate.project(source, request())
        decisions = judged(projected, {"a": {"decision": "not_applicable", "languages": [],
            "evidence": [{"source": "audio_language", "quote": "zxx"}]}})
        self.assertEqual(len(builder.build(source, projected, decisions)["heat"]), 1)
        decisions[0]["language"]["languages"] = ["hi"]
        with self.assertRaisesRegex(ValueError, "detected linguistic"):
            builder.build(source, projected, decisions)
        source["runs"]["search"]["items"][0].pop("content_signals")
        projected = gate.project(source, request())
        with self.assertRaises(ValueError):
            builder.build(source, projected, base.decisions(projected))

    def test_language_or_source_cannot_be_omitted_or_changed(self):
        gate, builder, _ = base.setup("youtube")
        source = base.payload("youtube", [base.item("a", content_signals={"default_audio_language": "en"})])
        projected = gate.project(source, request())
        decisions = judged(projected)
        bad = copy.deepcopy(decisions)
        bad[0].pop("language")
        with self.assertRaisesRegex(ValueError, "language judgment"):
            builder.build(source, projected, bad)
        bad = copy.deepcopy(projected)
        bad["schema_version"] = "short-window-topic-input.v1"
        with self.assertRaisesRegex(ValueError, "new builds require"):
            builder.build(source, bad, decisions)
        source["runs"]["search"]["items"][0]["content_signals"]["default_audio_language"] = "hi"
        with self.assertRaisesRegex(ValueError, "source changed"):
            builder.build(source, projected, decisions)


if __name__ == "__main__":
    unittest.main()
