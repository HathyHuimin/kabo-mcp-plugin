#!/usr/bin/env python3
"""机械整理用户可见聊天和当前请求；不选择指代、不生成内容、不替代 prep。"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from uuid import uuid4

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate_ideas import (REQUEST_SCHEMA, has_text, load,
                            validate_authored_intent, validate_schema_value)


def read_text(path: Path) -> str:
    """保留原文换行和 Unicode，不通过文本模式归一化 CRLF。"""
    return path.read_bytes().decode("utf-8")


def conversation_source(args: argparse.Namespace) -> dict:
    """只统一导出字段名，保持全文、角色和消息索引不变。"""
    exported = json.loads(read_text(args.messages))
    messages = exported.get("messages") if isinstance(exported, dict) else exported
    if not isinstance(messages, list) or not messages:
        raise ValueError("--messages must contain a nonempty array or an object with a messages array")
    normalized = []
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            raise ValueError(f"messages[{index}] must be an object")
        text = message.get("text", message.get("content"))
        if not has_text(text):
            raise ValueError(f"messages[{index}] must have nonempty plain-text text or content")
        if "text" in message and "content" in message and message["content"] != text:
            raise ValueError(f"messages[{index}] has conflicting text and content")
        normalized.append({"role": message.get("role"), "text": text})
    quote = read_text(args.reference_quote_file) if args.reference_quote_file else args.reference_quote
    source = {"schema_version": "idea-conversation.v1", "messages": normalized,
              "reference": {"message_index": args.reference_index, "quote": quote}}
    schema = load(REQUEST_SCHEMA)
    errors = validate_schema_value(source, schema["$defs"]["conversationSource"], schema, "$")
    if errors:
        raise ValueError("; ".join(errors))
    if args.reference_index >= len(normalized):
        raise ValueError("--reference-index is outside the messages array")
    if not has_text(quote) or quote not in normalized[args.reference_index]["text"]:
        raise ValueError("--reference-quote must be an exact nonempty span of the selected message")
    return source


def authored_intent(args: argparse.Namespace) -> dict:
    """未知信息保持缺失；用户明确给出的限制须随后补入对应字段。"""
    intent = {
        "schema_version": "idea-request.v3-candidate2",
        "request_id": f"conversation-{uuid4().hex}",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "task_intent": "brief_only", "idea_count": args.idea_count,
        "hook_option_count": args.hook_count,
        "goal": {"status": "missing", "value": None},
        "audience": {"status": "missing", "value": None},
        "desired_audience_action": {"status": "missing", "value": None},
        "platform_targets": args.platform, "language_targets": [args.language],
        "report_language": args.language,
        "user_brief": {"status": "provided", "text": read_text(args.brief_file)},
        "generation_basis": {"direct_topic": None, "required_sources": [],
                             "allow_brief_fallback": False,
                             "continuation_quote": args.continuation_quote},
        "production_constraints": {
            "status": "absent", "total_time_minutes": None, "time_per_item": None,
            "budget": None, "available_assets": [], "filming_mode": None,
            "editing_capacity": None, "publishing_cadence": None},
        "exclusions": {"topics": [], "claims": [], "formats": [], "assets": []},
    }
    errors = validate_authored_intent(intent, has_conversation_context=True)
    if errors:
        raise ValueError("; ".join(errors))
    return intent


def main() -> int:
    """先完整校验再独占创建文件；已有任一输出时拒绝覆盖。"""
    parser = argparse.ArgumentParser(description=(
        "Prepare exact visible-chat inputs for a continuation with no new explicit topic "
        "or external-evidence request. Fill explicitly supplied constraints afterward; "
        "the standard prep and core stages are still required."))
    parser.add_argument("--messages", type=Path, required=True)
    parser.add_argument("--reference-index", type=int, required=True, help="Zero-based message index")
    quote = parser.add_mutually_exclusive_group(required=True)
    quote.add_argument("--reference-quote")
    quote.add_argument("--reference-quote-file", type=Path)
    parser.add_argument("--brief-file", type=Path, required=True)
    parser.add_argument("--idea-count", type=int, choices=range(1, 11), required=True)
    parser.add_argument("--hook-count", type=int, choices=(1, 2), default=1)
    parser.add_argument("--platform", action="append", required=True)
    parser.add_argument("--language", required=True)
    parser.add_argument("--continuation-quote", required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    created = []
    try:
        run_dir = args.run_dir.resolve()
        source_path, intent_path = run_dir / "conversation-source.json", run_dir / "intent.json"
        if any(path.exists() or path.is_symlink() for path in (source_path, intent_path)):
            raise ValueError("Output already exists; use a new run directory or inspect the existing inputs")
        source, intent = conversation_source(args), authored_intent(args)
        run_dir.mkdir(parents=True, exist_ok=True)
        for path, data in ((source_path, source), (intent_path, intent)):
            with path.open("x", encoding="utf-8", newline="\n") as handle:
                created.append(path)
                json.dump(data, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
    except (OSError, ValueError) as error:
        for path in created:
            path.unlink(missing_ok=True)
        print(json.dumps({"status": "invalid_input", "errors": [str(error)]}, ensure_ascii=False))
        return 1
    print(json.dumps({"status": "ok", "conversation_source": str(source_path),
                      "intent": str(intent_path), "message_count": len(source["messages"]),
                      "reference_index": args.reference_index}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
