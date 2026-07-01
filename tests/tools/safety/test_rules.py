"""Tests for additional tool safety rules."""

from __future__ import annotations

import pytest

from trpc_agent_sdk._tool_safety import SafetyReviewer
from trpc_agent_sdk._tool_safety_policy import ToolSafetyPolicy


@pytest.mark.parametrize(
    ("source", "action_type", "decision", "rule_id", "finding"),
    [
        (
            "import os\nos.system('id')",
            "python",
            "deny",
            "os_system_execution",
            "OS system command execution detected.",
        ),
        (
            "echo safe; whoami",
            "bash",
            "needs_human_review",
            "shell_injection",
            "Shell command chaining pattern detected.",
        ),
        (
            "wget $TARGET_URL",
            "bash",
            "deny",
            "wget_network",
            "Wget network command detected.",
        ),
        (
            "npm install left-pad",
            "bash",
            "needs_human_review",
            "npm_install",
            "NPM package installation command detected.",
        ),
        (
            "apt-get install curl",
            "bash",
            "needs_human_review",
            "apt_install",
            "APT package installation command detected.",
        ),
        (
            "import aiohttp\nasync with aiohttp.ClientSession() as session:\n    pass",
            "python",
            "deny",
            "aiohttp_network",
            "aiohttp network client usage detected.",
        ),
        (
            "import socket\nclient = socket.socket()",
            "python",
            "deny",
            "socket_network",
            "Socket network usage detected.",
        ),
        (
            ":(){ :|:& }; :",
            "bash",
            "deny",
            "fork_bomb",
            "Fork bomb pattern detected.",
        ),
        (
            "from concurrent.futures import ThreadPoolExecutor\nThreadPoolExecutor(max_workers=1000)",
            "python",
            "deny",
            "excessive_concurrency",
            "Excessive concurrency pattern detected.",
        ),
        (
            "from pathlib import Path\nPath('big.bin').write_bytes(b'0' * 100000000)",
            "python",
            "deny",
            "large_file_write",
            "Large file write pattern detected.",
        ),
    ],
)
def test_additional_rules_are_independent_and_structured(source, action_type, decision, rule_id, finding) -> None:
    review = SafetyReviewer().review(source, action_type=action_type, tool_name="safety_test")

    assert review.decision == decision
    assert review.rule_id == rule_id
    assert review.finding == finding
    assert review.report["finding"] == finding
    assert review.report["rule_id"] == rule_id
    assert review.report["decision"] == decision
    assert review.audit["rule_id"] == rule_id
    assert review.audit["decision"] == decision
    assert rule_id in review.audit["rules_evaluated"]


@pytest.mark.parametrize(
    ("source", "action_type", "rule_id"),
    [
        ("import os\nos.system('id')", "python", "os_system_execution"),
        ("echo safe; whoami", "bash", "shell_injection"),
        ("wget $TARGET_URL", "bash", "wget_network"),
        ("npm install left-pad", "bash", "npm_install"),
        ("apt install curl", "bash", "apt_install"),
        ("import aiohttp", "python", "aiohttp_network"),
        ("import socket", "python", "socket_network"),
        (":(){ :|:& }; :", "bash", "fork_bomb"),
        ("ThreadPoolExecutor(max_workers=1000)", "python", "excessive_concurrency"),
        ("Path('big.bin').write_bytes(b'0' * 100000000)", "python", "large_file_write"),
    ],
)
def test_additional_rules_get_risk_level_from_policy(source, action_type, rule_id) -> None:
    policy = ToolSafetyPolicy(risk_levels={rule_id: "policy_override"})

    review = SafetyReviewer(policy=policy).review(source, action_type=action_type)

    assert review.rule_id == rule_id
    assert review.report["risk_level"] == "policy_override"
    assert review.audit["risk_level"] == "policy_override"
