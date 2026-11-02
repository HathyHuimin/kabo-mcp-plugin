#!/usr/bin/env python3
"""Prepare deterministic traffic evidence, then compose the report around the model's written analysis."""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
from analyze_traffic import build, load, require
from compose_traffic import FINAL_BEGIN, FINAL_END, compose, verdict
from expand_traffic_brief import expand_case
from render_traffic import render
from author_traffic import resolve_plan, synthesis_hint
from synthesis import fact_refs, read
from correction import Parser, language


PREPARE_ARTIFACTS = (
    "traffic_brief.json",
    "traffic_case.json",
    "reach_drop_assessment.json",
    "TRAFFIC_EVIDENCE.md",
)
FINAL_ARTIFACTS = ("traffic_delivery.json", "TRAFFIC_ANALYSIS.md", "creator-reply.md")
RUN_ARTIFACTS = ("traffic_run.json", "traffic_source_analysis.json", "traffic_synthesis_input.json")
SYNTHESIS_INPUT = "traffic_synthesis_input.json"
ARTIFACT_NAMES = PREPARE_ARTIFACTS + FINAL_ARTIFACTS
EVIDENCE_BEGIN = "KABO_TRAFFIC_EVIDENCE_BEGIN"
EVIDENCE_END = "KABO_TRAFFIC_EVIDENCE_END"
PLAN_FIELDS = ("questions", "coverage", "user_request", "platform", "account", "source", "schema_version")
CHANGED = "; prepared files changed after prepare. Nothing was written; do not edit them (standalone: prepare again)"


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def remove_artifacts(output: Path | None, names: tuple[str, ...] = ARTIFACT_NAMES) -> None:
    if output is None or not output.is_dir():
        return
    for name in names:
        try:
            (output / name).unlink()
        except FileNotFoundError:
            pass


def prepare(payload: dict, analysis: dict | None = None,
            language: str = "zh-CN") -> tuple[dict, dict, dict]:
    """Return the normalized plan, deterministic case and calculated assessment."""
    brief, case = expand_case(resolve_plan(payload, analysis), language)
    try:
        assessment = build(case, analysis)
    except (ValueError, KeyError, TypeError) as error:
        raise ValueError(f"invalid workflow case: {error}") from error
    return brief, case, assessment


def run(brief: dict, synthesis: dict | str, analysis: dict | None = None,
        language: str = "zh-CN") -> tuple[dict, str]:
    """Test helper and library entry point: plan, calculate, then compose around the written analysis."""
    _, _, assessment = prepare(brief, analysis, language)
    analyses, _ = read(synthesis, assessment, analysis, brief.get("user_request"))
    return assessment, compose(assessment, analyses, language)


def finalize(brief: dict, synthesis: dict | str, case: dict, assessment: dict,
             analysis: dict | None = None, language: str = "zh-CN") -> tuple[dict, list[dict], str]:
    """Check the saved evidence is intact, then compose; unusable analysis is left out, never fatal."""
    normalized, expected_case = expand_case(resolve_plan(brief, analysis), language)
    require(normalized == brief, "saved traffic brief is not normalized" + CHANGED)
    require(expected_case == case, "saved traffic case does not match its brief" + CHANGED)
    require(assessment == build(case, analysis),
            "saved traffic assessment is not bound to the provided source inputs" + CHANGED)
    analyses, issues = read(synthesis, assessment, analysis, brief.get("user_request"))
    return analyses, issues, compose(assessment, analyses, language)


def atomic_publish(output: Path, names: tuple[str, ...], writer) -> None:
    output.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix=".traffic-workflow-", dir=output) as directory:
            staging = Path(directory)
            writer(staging)
            remove_artifacts(output, names)
            for name in names:
                os.replace(staging / name, output / name)
    except (OSError, ValueError, KeyError, TypeError):
        remove_artifacts(output, names)
        raise


def publish_preparation(brief: dict, case: dict, assessment: dict,
                        output: Path, language: str, analysis: dict | None = None) -> Path:
    def writer(staging: Path) -> None:
        write_json(staging / "traffic_brief.json", brief)
        write_json(staging / "traffic_case.json", case)
        write_json(staging / "reach_drop_assessment.json", assessment)
        write_json(staging / "traffic_source_analysis.json", analysis)
        write_json(staging / "traffic_run.json", {"language": language, "has_analysis": analysis is not None})
        (staging / "TRAFFIC_EVIDENCE.md").write_text(
            render(assessment, language), encoding="utf-8"
        )

    atomic_publish(output, PREPARE_ARTIFACTS + RUN_ARTIFACTS[:2], writer)
    return output / "TRAFFIC_EVIDENCE.md"


def publish_final(analyses: dict, report: str, output: Path) -> Path:
    def writer(staging: Path) -> None:
        write_json(staging / "traffic_delivery.json", analyses)
        (staging / "TRAFFIC_ANALYSIS.md").write_text(report, encoding="utf-8")
        (staging / "creator-reply.md").write_text(report, encoding="utf-8")

    atomic_publish(output, FINAL_ARTIFACTS, writer)
    return output / "TRAFFIC_ANALYSIS.md"


def input_object(path: str) -> dict:
    if path == "-":
        raw = sys.stdin.read()
        require(bool(raw.strip()), "empty stdin: supply the JSON object in this command using a heredoc, "
                "or pass its file path instead of '-'")
    else:
        try:
            raw = Path(path).read_text(encoding="utf-8")
        except OSError:
            raise ValueError(f"plan file {path!r} not found; pass the plan JSON as a heredoc with -") from None
    value = json.loads(strip_fence(raw))
    require(isinstance(value, dict), "input must be a JSON object")
    return value


def strip_fence(raw: str) -> str:
    """A Markdown code fence around JSON is formatting, not content."""
    match = re.fullmatch(r"\s*```[A-Za-z]*\s*\n(.*?)\n\s*```\s*", raw, flags=re.S)
    return match.group(1) if match else raw


def analysis_file(path: Path | None) -> dict | None:
    """The imported AccountAnalysis; its problems are named as file problems, not plan problems."""
    if path is None:
        return None
    try:
        value = load(path)
    except OSError:
        raise ValueError(f"--analysis {path}: file not found; pass the AccountAnalysis file "
                         "(account_analysis.json)") from None
    except ValueError as error:
        raise ValueError(f"--analysis {path}: not JSON ({error}); pass the AccountAnalysis file") from None
    require(isinstance(value, dict) and value.get("schema_version") == "account-analysis.v3",
            f"--analysis {path}: not an AccountAnalysis (expected account-analysis.v3)")
    return value


def plan_issues(payload: dict, analysis: dict | None, language: str, error: Exception) -> list[str]:
    """Name every question the plan cannot build, not only the first, so one revision fixes them all.

    A problem shared by every question (a top-level field, the account) is named once, as the plan's.
    """
    found = []
    extra = sorted(set(payload) - set(PLAN_FIELDS))
    if extra:
        found.append(f"plan: unknown top-level fields {extra}; allowed: questions, coverage, user_request "
                     "(platform, account and source are inherited after a fetch)")
        payload = {key: value for key, value in payload.items() if key in PLAN_FIELDS}
    per_question = []
    questions = payload.get("questions") or []
    for index, question in enumerate(questions):
        try:
            prepare({**payload, "questions": [question]}, analysis, language)
        except (ValueError, KeyError, TypeError) as failure:
            per_question.append((index, str(failure)))
    shared = {message for _, message in per_question if "questions[0]" not in message}
    if len(questions) > 1 and len(per_question) == len(questions) and len(shared) == 1 and \
            all(message in shared for _, message in per_question):
        return found + [f"plan: {shared.pop()}"]
    found += [message.replace("questions[0]", f"questions[{index}]") if "questions[0]" in message
              else f"questions[{index}]: {message}" for index, message in per_question]
    return found or [str(error)]


def prepare_command(args: argparse.Namespace) -> int:
    output = args.output_dir
    try:
        payload = input_object(args.brief)
        payload["schema_version"] = "traffic-workflow-brief.v1"
        analysis = analysis_file(args.analysis)
        remove_artifacts(output, FINAL_ARTIFACTS + RUN_ARTIFACTS)
        if getattr(args, "question_file", None):
            # The saved original request is authoritative; a restated copy in the plan is replaced.
            payload["user_request"] = Path(args.question_file).read_text(encoding="utf-8")
        issues = [] if isinstance(payload.get("user_request"), str) and payload["user_request"].strip() else \
            ["user_request: include the user's original request verbatim"]
        try:
            brief, case, assessment = prepare(payload, analysis, args.language)
        except (ValueError, KeyError, TypeError) as error:
            issues += plan_issues(payload, analysis, args.language, error)
        if issues:
            remove_artifacts(output, PREPARE_ARTIFACTS)
            print(json.dumps({"status": "needs_correction", "issues": issues}, ensure_ascii=False))
            print("Nothing was calculated. Revise the plan for the issues above and submit it again.")
            return 0
        evidence_path = publish_preparation(brief, case, assessment, output, args.language, analysis)
        evidence = evidence_path.read_text(encoding="utf-8")
        print(EVIDENCE_BEGIN)
        print(evidence, end="" if evidence.endswith("\n") else "\n")
        print(EVIDENCE_END)
        zh = args.language == "zh-CN"
        for finding in assessment["questions"]:
            print(f"Conclusion already written by code for {finding['id']}: {verdict(finding, zh)}")
        print(synthesis_hint(brief["questions"]))
        print(json.dumps({"citable_facts": {q["id"]: fact_refs(q) for q in assessment["questions"]}},
                         ensure_ascii=False))
        check_command, report_command = finish_commands(output, args.language)
        print("Next: check the analysis with: " + check_command)
        print("Then write the report with: " + report_command)
        print(json.dumps({"status": "ok", "run_id": output.resolve().name, "run_directory": str(output),
                          "evidence": str(evidence_path)}, ensure_ascii=False), file=sys.stderr)
        return 0
    except ValueError as error:
        remove_artifacts(output, PREPARE_ARTIFACTS + RUN_ARTIFACTS + FINAL_ARTIFACTS)
        print(json.dumps({"status": "needs_correction", "issues": [str(error)]}, ensure_ascii=False))
        print("Nothing was calculated. Revise the plan for the issues above and submit it again.")
        return 0
    except (OSError, KeyError, TypeError) as error:
        remove_artifacts(output, PREPARE_ARTIFACTS + RUN_ARTIFACTS + FINAL_ARTIFACTS)
        print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False))
        return 2


def finish_commands(output: Path, language: str) -> tuple[str, str]:
    """The check and report commands for this run, runnable from any directory."""
    driver = Path(__file__).with_name("drive.py")
    here = "cd " + shlex.quote(str(output.resolve())) + " && python3 " + shlex.quote(str(driver))
    return (here + " check --synthesis-file - (analysis JSON on stdin)", here + " report")


def finalize_command(args: argparse.Namespace) -> int:
    output = None
    try:
        output = args.output_dir or Path(".")
        args.brief = args.brief or output / "traffic_brief.json"
        args.case = args.case or output / "traffic_case.json"
        args.assessment = args.assessment or output / "reach_drop_assessment.json"
        args.language = args.language or "zh-CN"
        saved = output / SYNTHESIS_INPUT
        if args.synthesis is not None:
            raw = sys.stdin.read() if args.synthesis == "-" else Path(args.synthesis).read_text(encoding="utf-8")
            saved.write_text(strip_fence(raw), encoding="utf-8")
        raw = saved.read_text(encoding="utf-8") if saved.is_file() else "{}"
        brief, case, assessment = load(args.brief), load(args.case), load(args.assessment)
        analysis = load(args.analysis) if args.analysis else None
        analyses, issues, report = finalize(brief, raw, case, assessment, analysis, args.language)
        if args.check:
            print(json.dumps({"status": "issues" if issues else "ok", "issues": issues}, ensure_ascii=False))
            unusable = any(issue.get("path") in {"synthesis", "questions"} for issue in issues)
            print("None of the analysis is usable: fix it and check again before writing the report." if unusable
                  else "Revise only what the issues name and check again, or write the report now: unusable parts "
                  "are left out with a note." if issues else "The analysis reads cleanly.")
            print("Write the report with: " + finish_commands(output, args.language)[1])
            return 0
        report_path = publish_final(analyses, report, output)
        # One report per run: the host names creator-reply.md, so this status line does not use the
        # creator_report key that hosts read as a separate report to merge.
        print(json.dumps({"status": "ok", "report_file": str(report_path.with_name("creator-reply.md"))},
                         ensure_ascii=False), file=sys.stderr)
        print(FINAL_BEGIN)
        print(report, end="")
        print(FINAL_END)
        print("Deliver this report once, entirely in the creator's language: translate every part not in it, "
              "including the evidence labels and source notes, and present the translated text, not a file path; "
              "keep every conclusion, number, limitation and next step.")
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        remove_artifacts(output, FINAL_ARTIFACTS)
        print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False))
        return 2


def main() -> int:
    parser = Parser(description=__doc__)
    sub = parser.add_subparsers(dest="stage", required=True)

    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("brief", nargs="?", default="-", help="traffic-workflow-brief JSON, or - for stdin")
    prepare_parser.add_argument("--output-dir", type=Path, required=True)
    prepare_parser.add_argument("--analysis", type=Path)
    prepare_parser.add_argument("--question-file", help="Original user request, used to bind goals and delivery intent")
    prepare_parser.add_argument("--language", type=language, metavar="{en,zh-CN}", default="zh-CN")
    prepare_parser.set_defaults(command=prepare_command)

    final_parser = sub.add_parser("finalize")
    final_parser.add_argument("synthesis", nargs="?",
                              help="the written analysis JSON, or - for stdin; omitted: the last one checked")
    final_parser.add_argument("--output-dir", type=Path)
    final_parser.add_argument("--brief", type=Path)
    final_parser.add_argument("--case", type=Path)
    final_parser.add_argument("--assessment", type=Path)
    final_parser.add_argument("--analysis", type=Path)
    final_parser.add_argument("--language", type=language, metavar="{en,zh-CN}")
    final_parser.add_argument("--check", action="store_true", help="List issues without writing the report")
    final_parser.set_defaults(command=finalize_command)

    args = parser.parse_args()
    return args.command(args)


if __name__ == "__main__":
    raise SystemExit(main())
