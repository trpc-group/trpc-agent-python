#!/usr/bin/env python3
# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Examples for custom Tool Script Safety Guard policies and rules."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from trpc_agent_sdk.tools.safety import Decision
from trpc_agent_sdk.tools.safety import RiskFinding
from trpc_agent_sdk.tools.safety import RiskLevel
from trpc_agent_sdk.tools.safety import ToolSafetyPolicy
from trpc_agent_sdk.tools.safety import ToolScriptSafetyScanner
from trpc_agent_sdk.tools.safety import ToolScriptScanRequest

EXAMPLE_DIR = Path(__file__).resolve().parent


def deny_internal_admin_rule(request: ToolScriptScanRequest,
                             policy: ToolSafetyPolicy) -> Iterable[RiskFinding]:
    """Block an organization-specific command that built-in rules do not know."""
    del policy
    if "internal-admin" not in request.script:
        return []
    return [
        RiskFinding(
            rule_id="CUSTOM_INTERNAL_ADMIN_COMMAND",
            risk_type="process_command",
            risk_level=RiskLevel.HIGH,
            decision=Decision.DENY,
            evidence="internal-admin",
            recommendation="Route internal admin commands through an approved workflow.",
            message="A user-registered safety rule matched an internal admin command.",
        )
    ]


def build_scanner_from_yaml() -> ToolScriptSafetyScanner:
    policy = ToolSafetyPolicy.from_file(EXAMPLE_DIR / "tool_safety_policy.yaml", strict=True)
    scanner = ToolScriptSafetyScanner(policy)
    scanner.register_rule(deny_internal_admin_rule)
    return scanner


def build_scanner_from_dict() -> ToolScriptSafetyScanner:
    policy = ToolSafetyPolicy.from_dict(
        {
            "allowed_domains": ["api.example.com"],
            "allowed_commands": ["cat", "echo", "ls", "python3"],
            "denied_paths": ["~/.ssh", ".env", "*/.env", "*.pem", "*.key"],
            "max_timeout_seconds": 60,
            "max_output_bytes": 262144,
        },
        strict=True,
    )
    return ToolScriptSafetyScanner(policy, custom_rules=[deny_internal_admin_rule])


def scan_demo_cases(scanner: ToolScriptSafetyScanner) -> dict[str, dict]:
    cases = {
        "allowed_domain": ("import requests\nrequests.get('https://api.example.com/status')", "python"),
        "blocked_domain": ("import requests\nrequests.get('https://evil.example/collect')", "python"),
        "custom_rule": ("internal-admin rotate-production-secret", "bash"),
        "prompt_bypass_attempt": (
            "echo 'user asked to bypass safety checks'\nrm -rf /",
            "bash",
        ),
    }
    reports = {}
    for name, (script, language) in cases.items():
        report = scanner.scan_script(
            script,
            language,
            tool_name=name,
            tool_metadata={"bypass_safety": True},
        )
        reports[name] = report.to_dict()
    return reports


def main() -> None:
    scanner = build_scanner_from_yaml()
    print(json.dumps(scan_demo_cases(scanner), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
