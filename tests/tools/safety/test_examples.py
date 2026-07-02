# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests that public tool safety examples remain runnable."""

from __future__ import annotations

from pathlib import Path

from trpc_agent_sdk.tools.safety import ToolSafetyPolicy
from trpc_agent_sdk.tools.safety import ToolScriptSafetyScanner


REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_DIR = REPO_ROOT / "examples" / "tool_safety"


EXPECTED_DECISIONS = {
    "aiohttp_non_whitelist.py": "deny",
    "apt_install.sh": "deny",
    "background_process.sh": "needs_human_review",
    "bash_pipe.sh": "deny",
    "command_substitution.sh": "needs_human_review",
    "credential_file_key.py": "deny",
    "danger_delete.sh": "deny",
    "dependency_install.sh": "deny",
    "fork_bomb.sh": "deny",
    "human_review.py": "needs_human_review",
    "infinite_loop.py": "needs_human_review",
    "long_sleep.sh": "needs_human_review",
    "network_non_whitelist.py": "deny",
    "network_whitelist.py": "allow",
    "npm_install.sh": "deny",
    "os_system.py": "needs_human_review",
    "pip_module_install.py": "deny",
    "private_key_literal.py": "deny",
    "privilege_escalation.sh": "deny",
    "read_env.py": "deny",
    "read_secret.py": "deny",
    "safe_bash.sh": "allow",
    "safe_file_read.py": "allow",
    "safe_python.py": "allow",
    "sensitive_output.py": "deny",
    "shell_injection.py": "needs_human_review",
    "socket_access.py": "needs_human_review",
    "subprocess_call.py": "needs_human_review",
    "subprocess_danger_delete.py": "deny",
    "system_overwrite.sh": "deny",
    "unknown_network_dynamic.py": "needs_human_review",
}


def _language_for(path: Path) -> str:
    if path.suffix == ".py":
        return "python"
    if path.suffix == ".sh":
        return "bash"
    return "unknown"


def test_public_examples_scan_to_expected_decisions():
    policy = ToolSafetyPolicy.from_file(EXAMPLE_DIR / "tool_safety_policy.yaml")
    scanner = ToolScriptSafetyScanner(policy)
    sample_names = {path.name for path in (EXAMPLE_DIR / "samples").iterdir() if path.is_file()}

    assert sample_names == set(EXPECTED_DECISIONS)

    for name, expected_decision in EXPECTED_DECISIONS.items():
        path = EXAMPLE_DIR / "samples" / name
        report = scanner.scan_file(path, language=_language_for(path), tool_name=name)

        assert report.decision.value == expected_decision, name
        assert "decision" in report.to_dict()
        assert "risk_level" in report.to_dict()
