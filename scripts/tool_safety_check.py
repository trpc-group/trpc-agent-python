#!/usr/bin/env python3
#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Scan Python and Bash files without executing them."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from trpc_agent_sdk.tools.safety import JsonlAuditSink
from trpc_agent_sdk.tools.safety import SafetyDecision
from trpc_agent_sdk.tools.safety import ScriptLanguage
from trpc_agent_sdk.tools.safety import ScriptPayload
from trpc_agent_sdk.tools.safety import ScriptScanRequest
from trpc_agent_sdk.tools.safety import ToolMetadata
from trpc_agent_sdk.tools.safety import ToolSafetyGuard
from trpc_agent_sdk.tools.safety import ToolSafetyPolicy
from trpc_agent_sdk.tools.safety import ToolScriptSafetyScanner
from trpc_agent_sdk.tools.safety import load_policy


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", type=Path, help="Python or Bash files to scan")
    parser.add_argument("--policy", type=Path, help="Strict YAML policy file")
    parser.add_argument("--output", type=Path, help="Write the combined JSON report")
    parser.add_argument("--audit", type=Path, help="Append redacted JSONL audit events")
    parser.add_argument("--cwd", default="", help="Prospective execution working directory")
    parser.add_argument("--timeout", type=float, help="Prospective execution timeout")
    return parser


def _language(path: Path) -> ScriptLanguage:
    if path.suffix.lower() == ".py":
        return ScriptLanguage.PYTHON
    if path.suffix.lower() in {".sh", ".bash"}:
        return ScriptLanguage.BASH
    raise ValueError(f"cannot infer supported language from {path}")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        policy = load_policy(args.policy) if args.policy else ToolSafetyPolicy()
        scanner = ToolScriptSafetyScanner(policy)
        guard = ToolSafetyGuard(scanner=scanner, audit_sink=JsonlAuditSink(args.audit) if args.audit else None)
        reports = []
        worst = SafetyDecision.ALLOW
        order = {
            SafetyDecision.ALLOW: 0,
            SafetyDecision.NEEDS_HUMAN_REVIEW: 1,
            SafetyDecision.DENY: 2,
        }
        for path in args.files:
            content = path.read_text(encoding="utf-8")
            request = ScriptScanRequest(
                payloads=[ScriptPayload(language=_language(path), content=content, source=str(path))],
                cwd=args.cwd,
                metadata=ToolMetadata(name=path.name, tool_type="cli"),
                requested_timeout=args.timeout,
            )
            report = guard.check(request)
            reports.append({"path": str(path), **report.model_dump(mode="json"), "rule_ids": report.rule_ids})
            if order[report.decision] > order[worst]:
                worst = report.decision
        output = {
            "schema_version": "1",
            "policy_version": policy.version,
            "reports": reports,
        }
        rendered = json.dumps(output, ensure_ascii=False, indent=2)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(f"{rendered}\n", encoding="utf-8")
        else:
            print(rendered)
        return {
            SafetyDecision.ALLOW: 0,
            SafetyDecision.NEEDS_HUMAN_REVIEW: 2,
            SafetyDecision.DENY: 3,
        }[worst]
    except Exception as exc:  # pylint: disable=broad-except
        print(f"tool safety check failed closed: {type(exc).__name__}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
