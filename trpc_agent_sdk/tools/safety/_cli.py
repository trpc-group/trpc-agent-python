# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Command-line interface for offline tool safety scans."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from ._integration import adapt_cli_request
from ._audit import emit_report
from ._audit import JsonlAuditSink
from ._audit import SafetyAuditError
from ._models import SafetyDecision
from ._models import ScriptLanguage
from ._models import ScriptPayload
from ._models import ToolMetadata
from ._sanitizer import SafetySanitizer
from ._scanner import ToolScriptSafetyGuard

EXIT_ALLOW = 0
EXIT_ERROR = 1
EXIT_REVIEW = 2
EXIT_DENY = 3
_CLI_SANITIZER = SafetySanitizer()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Scan Python or Bash before tool execution.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", help="Script file to scan.")
    source.add_argument("--command", help="Inline command or script text.")
    parser.add_argument("--language", choices=["python", "bash"], required=True)
    parser.add_argument("--policy", required=True, help="Safety policy YAML.")
    parser.add_argument("--report", help="Optional report JSON output path.")
    parser.add_argument("--audit", help="Optional audit JSONL output path.")
    parser.add_argument("--tool-name", default="tool_safety_cli")
    parser.add_argument("--tool-description", default="")
    parser.add_argument("--tag", action="append", default=[])
    parser.add_argument("--argv", action="append", default=[])
    parser.add_argument("--env-key", action="append", default=[])
    parser.add_argument("--cwd", default="")
    return parser


def _content(args: argparse.Namespace) -> tuple[str, str]:
    if args.file:
        path = Path(args.file)
        try:
            return path.read_text(encoding="utf-8"), str(path)
        except OSError as error:
            raise ValueError(f"unable to read script file: {path}") from error
    return args.command, "inline"


def _exit_code(decision: SafetyDecision) -> int:
    if decision == SafetyDecision.DENY:
        return EXIT_DENY
    if decision == SafetyDecision.NEEDS_HUMAN_REVIEW:
        return EXIT_REVIEW
    return EXIT_ALLOW


def run_cli(args: argparse.Namespace) -> int:
    """Run a validated CLI request."""
    guard = ToolScriptSafetyGuard.from_policy(args.policy)
    content, source = _content(args)
    payload = ScriptPayload(
        language=ScriptLanguage(args.language),
        content=content,
        source=source,
        argv=args.argv,
    )
    metadata = ToolMetadata(
        name=args.tool_name,
        description=args.tool_description,
        tags=args.tag,
    )
    request = adapt_cli_request(payload, metadata, guard.policy, args.cwd)
    request.env_keys = sorted(set(args.env_key))
    report = guard.scan(request)
    serialized = json.dumps(report.as_dict(), ensure_ascii=False, indent=2)
    if args.report:
        Path(args.report).write_text(serialized + "\n", encoding="utf-8")
    if args.audit:
        emit_report(JsonlAuditSink(args.audit), report, metadata.name)
    print(serialized)
    return _exit_code(report.decision)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point."""
    try:
        return run_cli(_parser().parse_args(argv))
    except (ValueError, OSError, SafetyAuditError) as error:
        safe_error, _ = _CLI_SANITIZER.sanitize(error)
        print(json.dumps({"error": safe_error}, ensure_ascii=False))
        return EXIT_ERROR
