"""Exercise topic eligibility, verified weekly collection and report delivery offline."""
from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
REQUEST = {"ask": "Find video editing examples from the past week",
           "niche": "video editing", "content_use": "learn editing techniques",
           "language_scope": {"mode": "unrestricted", "accepted_languages": [], "basis": "No account or content language was requested."}}


def module(platform, name):
    path = ROOT / f"skills/research-{platform}-trends/scripts/{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


def setup(platform):
    gate = module(platform, "short_window_topic_gate")
    builder = module(platform, "build_short_window_layers")
    renderer = module(platform, "render_short_window_report")
    return gate, builder, renderer


def item(identifier, *, views=100, date="2026-11-02T00:00:00Z", **extra):
    return {"content_id": identifier, "url": f"https://example.invalid/{identifier}",
            "title": "Video editing", "caption": "An editing example", "description": "Complete explanation",
            "author": {"handle": "editor"}, "published_at": date, "views": views, **extra}


def payload(platform, items):
    return {"platform": platform, "window": {"since": "2026-10-27T00:00:00Z", "until": "2026-11-03T00:00:00Z"},
            "runs": {"search": {"status": "completed", "items": items}}}


def decisions(projection, labels=None):
    labels = labels or {}
    return [{"content_id": row["content_id"], "decision": labels.get(row["content_id"], "on_niche"),
             "reason": f"Fixture judgment for {row['content_id']}",
             "language": {"decision": "not_applicable", "languages": [], "evidence": [],
                          "reason": "This fixture explicitly has no language restriction."}} for row in projection["items"]]


def weekly(items):
    source = payload("instagram", [])
    source["source_policy"] = "instagram-week-search.v1"
    source["runs"] = {"week": {"schema_version": "trend-candidate-set.v1", "status": "partial",
                               "window": "last-week", "items": items,
                               "limitations": ["Search coverage is partial."]}}
    return source


def verified(identifier, **extra):
    raw = item(identifier, **extra)
    raw.pop("views")
    raw.update(metrics={"play_count": 0}, availability={"detail_status": "completed"},
               sources=[{"route": "search"}])
    return raw


class TopicGateTests(unittest.TestCase):
    def test_full_text_and_duplicate_identity_are_reviewed_before_popularity(self):
        for platform in ("instagram", "tiktok", "youtube"):
            with self.subTest(platform=platform):
                gate, builder, _ = setup(platform)
                rows = [item("relevant", views=3, caption="prefix " + "x" * 1000 + " decisive ending"),
                        item("relevant", description="Additional source description"),
                        item("off", views=999999), item("adjacent", views=9999), item("unknown", views=500)]
                source = payload(platform, rows)
                projected = gate.project(source, REQUEST)
                relevant = next(row for row in projected["items"] if row["content_id"] == "relevant")
                self.assertIn("decisive ending", relevant["texts"][0])
                self.assertIn("Additional source description", relevant["texts"][1])
                self.assertEqual(set(relevant), {"content_id", "author", "texts", "language_evidence"})
                self.assertEqual(len(projected["items"]), 4)
                judged = decisions(projected, {"off": "off_niche", "adjacent": "adjacent", "unknown": "uncertain"})
                result = builder.build(source, projected, judged)
                self.assertEqual([row["content_id"] for row in result["heat"]], ["relevant"])
                self.assertEqual(result["heat"][0]["views"], 3)
                self.assertEqual(result["in_window"], result["heat"])
                self.assertEqual(result["topic_gate"]["decisions"], judged)
                self.assertEqual(result["topic_gate"]["counts"], {label: 1 for label in gate.DECISIONS})

    def test_missing_duplicate_unknown_and_modified_evidence_fail(self):
        gate, builder, _ = setup("instagram")
        source = payload("instagram", [item("a"), item("b")])
        projected = gate.project(source, REQUEST)
        valid = decisions(projected)
        for invalid in (valid[:1], valid + [valid[0]], [*valid[:1], {**valid[1], "content_id": "foreign"}],
                        [{**valid[0], "reason": ""}, valid[1]], [{**valid[0], "decision": "yes"}, valid[1]]):
            with self.subTest(decisions=invalid), self.assertRaises(ValueError):
                builder.build(source, projected, invalid)
        changed = copy.deepcopy(source)
        changed["runs"]["search"]["items"][0]["caption"] = "different topic"
        with self.assertRaisesRegex(ValueError, "source changed"):
            builder.build(changed, projected, valid)
        changed_projection = copy.deepcopy(projected)
        changed_projection["items"][0]["texts"] = ["invented evidence"]
        with self.assertRaisesRegex(ValueError, "complete source text"):
            builder.build(source, changed_projection, valid)

    def test_empty_pool_or_all_rejections_are_valid_and_never_filled(self):
        for platform in ("instagram", "tiktok", "youtube"):
            gate, builder, _ = setup(platform)
            for rows in ([], [item("off", views=999999)]):
                source = payload(platform, rows)
                projected = gate.project(source, REQUEST)
                result = builder.build(source, projected, decisions(projected, {"off": "off_niche"}))
                self.assertEqual(result["heat"], [])
                self.assertEqual(result["in_window"], [])

    def test_provider_date_filters_are_rechecked_for_thirty_day_heat(self):
        for platform in ("tiktok", "youtube"):
            gate, builder, _ = setup(platform)
            source = payload(platform, [item("edge", views=0, date="2026-10-04T00:00:00Z"),
                                        item("old", date="2026-10-03T23:59:59Z"),
                                        item("now", date="2026-11-03T00:00:00Z"),
                                        item("future", date="2026-11-03T00:00:01Z")])
            projected = gate.project(source, REQUEST)
            result = builder.build(source, projected, decisions(projected))
            self.assertEqual({row["content_id"] for row in result["heat"]}, {"edge", "now"})
            self.assertEqual(result["heat"][-1]["views"], 0)
            self.assertEqual([row["content_id"] for row in result["in_window"]], ["now"])

    def test_weekly_search_requires_details_plays_and_original_window(self):
        gate, builder, renderer = setup("instagram")
        rows = [verified("eligible"), verified("omitted"), verified("no-plays"),
                verified("undated", date=None), verified("old", date="2026-01-01T00:00:00Z")]
        rows[1]["availability"]["detail_status"] = "not_attempted_budget"
        rows[2]["metrics"] = {}
        source = weekly(rows)
        projected = gate.project(source, REQUEST)
        result = builder.build(source, projected, decisions(projected))
        self.assertEqual([row["content_id"] for row in result["heat"]], ["eligible"])
        self.assertEqual(result["in_window"], result["heat"])
        self.assertEqual(result["coverage"], {"heat_count": 1, "in_window_count": 1,
                                            "undated_count": 1, "unverified_count": 2, "outside_window_count": 1})
        report = renderer.render(result)
        self.assertIn("2 on-topic candidates lack", report)
        self.assertIn("Search coverage is partial.", report)
        self.assertIn("1 on-topic candidates fall outside", report)
        with self.assertRaisesRegex(ValueError, "requires topic"):
            builder.build(source)
        for change in ("route", "schema", "window", "failed"):
            bad = copy.deepcopy(source)
            collector = bad["runs"]["week"]
            if change == "route": collector["items"][0]["sources"][0]["route"] = "popular"
            if change == "schema": collector["schema_version"] = "wrong"
            if change == "window": collector["window"] = "last-month"
            if change == "failed": collector["status"] = "failed"
            with self.subTest(change=change), self.assertRaises(ValueError):
                builder.build(bad, projected, decisions(projected))

    def test_unicode_pages_and_cli_report_contract_round_trip(self):
        gate, _, renderer = setup("instagram")
        source = weekly([verified("eligible", caption="长文🙂\n" * 6000)])
        projected = gate.project(source, REQUEST)
        self.assertGreater(len(gate.pages(projected)), 1)
        self.assertEqual(json.loads("".join(gate.pages(projected))), projected["items"])
        for page in gate.pages(projected):
            self.assertLessEqual(len(json.dumps(page, ensure_ascii=False)[1:-1].encode()), gate.PAGE_BYTES)
        scripts = ROOT / "skills/research-instagram-trends/scripts"
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            for name, value in (("input.json", source), ("request.json", REQUEST)):
                (work / name).write_text(json.dumps(value))
            def run(name, *args):
                result = subprocess.run([sys.executable, "-B", str(scripts / name), *args], cwd=work,
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                return json.loads(result.stdout)
            response = run("short_window_topic_gate.py", "input.json", "--request", "request.json", "--output", "topic.json")
            chunks = [response["text_chunk"]]
            while response["next"]:
                response = run("short_window_topic_gate.py", *response["next"]["arguments"])
                chunks.append(response["text_chunk"])
            self.assertEqual(json.loads("".join(chunks)), projected["items"])
            (work / "decisions.json").write_text(json.dumps(decisions(projected)))
            run("build_short_window_layers.py", "input.json", "--topic-gate-input", "topic.json",
                "--topic-decisions", "decisions.json", "--output", "layers.json")
            layers = json.loads((work / "layers.json").read_text())
            contract = renderer.presentation_contract(layers, "English", renderer.digest((work / "layers.json").read_bytes()))
            for entry in contract["labels"].values(): entry["text"] = entry["source"]
            for entry in contract["rows"]: entry["text"] = entry["source_excerpt"]
            (work / "presentation.json").write_text(json.dumps(contract))
            args = ("layers.json", "report.md", "--language", "English", "--presentation", "presentation.json")
            run("render_short_window_report.py", *args)
            run("render_short_window_report.py", *args, "--verify-report")
            self.assertIn("model judgment", (work / "report.md").read_text())
            self.assertIn("Search coverage is partial.", (work / "report.md").read_text())


if __name__ == "__main__":
    unittest.main()
