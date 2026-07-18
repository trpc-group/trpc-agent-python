# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0
"""Tests for per-block language scanning in code-executor wrappers."""
from __future__ import annotations

import asyncio

from trpc_agent_sdk.safety import PolicyConfig
from trpc_agent_sdk.safety import RiskLevel
from trpc_agent_sdk.safety import SafetyScanner
from trpc_agent_sdk.safety import ScanInput
from trpc_agent_sdk.safety._wrapper import SafetyGuardedCodeExecutor
from trpc_agent_sdk.safety._wrapper import _scan_code_input


class _Block:

    def __init__(self, code: str, language: str = ""):
        self.code = code
        self.language = language


class _Input:

    def __init__(self, code: str = "", code_blocks=None, language=None):
        self.code = code
        self.code_blocks = code_blocks or []
        self.language = language


class _Inner:

    def __init__(self):
        self.calls = 0

    async def execute_code(self, invocation_context, input_data):
        self.calls += 1
        return "executed"


def test_scan_code_input_bash_block_denies_rm():
    scanner = SafetyScanner(PolicyConfig())
    inp = _Input(code_blocks=[_Block("rm -rf /", language="bash")])
    report = _scan_code_input(scanner, inp)
    assert report is not None
    assert report.decision.value == "deny"
    assert "R001_dangerous_files" in report.rule_ids


def test_scan_code_input_infers_bash_when_language_empty():
    scanner = SafetyScanner(PolicyConfig())
    inp = _Input(code_blocks=[_Block("curl https://evil.example.com", language="")])
    report = _scan_code_input(scanner, inp)
    assert report is not None
    assert report.decision.value == "deny"


def test_safety_guarded_code_executor_blocks_bash_block():
    inner = _Inner()
    guarded = SafetyGuardedCodeExecutor(inner, PolicyConfig())
    inp = _Input(code_blocks=[_Block("rm -rf /tmp/x", language="bash")])
    result = asyncio.run(guarded.execute_code(None, inp))
    assert inner.calls == 0
    # Leaf import of create_code_execution_result (no docker package required).
    assert result is not None
    assert getattr(result, "outcome", None) is not None or "TOOL_SAFETY_DENY" in str(result)


def test_mislabeled_python_bash_payload_still_denied():
    """language='python' but content is bash rm -rf must still deny."""
    scanner = SafetyScanner(PolicyConfig())
    inp = _Input(code_blocks=[_Block("rm -rf /", language="python")])
    report = _scan_code_input(scanner, inp)
    assert report.decision.value == "deny"
    from trpc_agent_sdk.safety._ast_utils import normalize_language
    from trpc_agent_sdk.safety import ScanInput as SI
    lang = normalize_language(SI(script="rm -rf /", language=""))
    assert lang == "bash"
    # Explicit scan as bash content regardless of declared label.
    report2 = scanner.scan(SI(script="rm -rf /", language="bash"))
    assert report2.decision.value == "deny"


def test_blocked_true_when_block_on_review_and_review_decision():
    policy = PolicyConfig(
        deny_risk_level=RiskLevel.CRITICAL,
        review_risk_level=RiskLevel.MEDIUM,
        block_on_review=True,
    )
    report = SafetyScanner(policy).scan(ScanInput(script="sleep 1 &", language="bash"))
    assert report.decision.value == "needs_human_review"
    assert report.blocked is True
