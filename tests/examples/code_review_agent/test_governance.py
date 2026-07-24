# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for sandbox governance."""

from __future__ import annotations

from examples.code_review_agent.agent.governance import SandboxRequest
from examples.code_review_agent.agent.governance import evaluate_sandbox_requests
from examples.code_review_agent.agent.schemas import SandboxPolicy


def test_allowlisted_script_is_allowed() -> None:
    allowed, decisions = evaluate_sandbox_requests(
        [SandboxRequest(script_name="static_rules", command=("python", "scripts/static_rules.py"))], SandboxPolicy()
    )

    assert len(allowed) == 1
    assert decisions[0].decision == "allow"


def test_high_risk_command_is_denied() -> None:
    allowed, decisions = evaluate_sandbox_requests(
        [SandboxRequest(script_name="static_rules", command=("python", "-c", "rm -rf /tmp/example"))], SandboxPolicy()
    )

    assert allowed == []
    assert decisions[0].decision == "deny"


def test_network_request_is_denied_by_default() -> None:
    allowed, decisions = evaluate_sandbox_requests(
        [SandboxRequest(script_name="static_rules", command=("python", "scripts/static_rules.py"), requires_network=True)],
        SandboxPolicy(network_allowed=False),
    )

    assert allowed == []
    assert decisions[0].decision == "deny"


def test_forbidden_path_is_denied() -> None:
    allowed, decisions = evaluate_sandbox_requests(
        [SandboxRequest(script_name="static_rules", command=("python", "scripts/static_rules.py"), input_paths=(".env",))],
        SandboxPolicy(),
    )

    assert allowed == []
    assert decisions[0].decision == "deny"


def test_output_budget_routes_to_human_review() -> None:
    allowed, decisions = evaluate_sandbox_requests(
        [
            SandboxRequest(
                script_name="static_rules",
                command=("python", "scripts/static_rules.py"),
                estimated_output_bytes=100,
            )
        ],
        SandboxPolicy(max_output_bytes=10),
    )

    assert allowed == []
    assert decisions[0].decision == "needs_human_review"
