# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

from __future__ import annotations

import time

from trpc_agent_sdk.tools.safety import SafetyDecision
from trpc_agent_sdk.tools.safety import SafetyPolicy
from trpc_agent_sdk.tools.safety import SafetyScanner
from trpc_agent_sdk.tools.safety import ScanTarget
from trpc_agent_sdk.tools.safety import ScriptLanguage


def _rule_ids(report):
    return {finding.rule_id for finding in report.findings}


class TestScannerCommon:
    """Test scanner language inference, limits, and performance."""

    def test_unknown_with_command_uses_shell_scanner(self):
        report = SafetyScanner().scan(
            ScanTarget(command="rm -rf ~/.ssh", language=ScriptLanguage.UNKNOWN)
        )

        assert report.language == ScriptLanguage.SHELL
        assert report.decision == SafetyDecision.DENY
        assert "FILE_RECURSIVE_DELETE" in _rule_ids(report)

    def test_unknown_python_like_content_uses_python_scanner(self):
        report = SafetyScanner().scan(
            ScanTarget(content='open(".env").read()', language=ScriptLanguage.UNKNOWN)
        )

        assert report.language == ScriptLanguage.PYTHON
        assert report.decision == SafetyDecision.DENY
        assert "FILE_SENSITIVE_READ" in _rule_ids(report)

    def test_target_timeout_output_and_line_limits_create_resource_findings(self):
        policy = SafetyPolicy(max_timeout_seconds=2, max_output_bytes=10, max_script_lines=2)
        report = SafetyScanner(policy).scan(
            ScanTarget(
                content="echo one\necho two\necho three\n",
                language=ScriptLanguage.SHELL,
                timeout_seconds=30,
                output_limit_bytes=99,
            )
        )

        assert report.decision == SafetyDecision.NEEDS_HUMAN_REVIEW
        assert {"RES_LONG_SLEEP", "RES_LARGE_WRITE"} <= _rule_ids(report)

    def test_review_can_be_nonblocking_by_policy(self):
        policy = SafetyPolicy(review_blocks_execution=False)

        report = SafetyScanner(policy).scan(
            ScanTarget(command="cat data.txt | sort", language=ScriptLanguage.SHELL)
        )

        assert report.decision == SafetyDecision.NEEDS_HUMAN_REVIEW
        assert report.blocked is False

    def test_evidence_is_capped_by_policy(self):
        policy = SafetyPolicy(max_evidence_chars=40)
        report = SafetyScanner(policy).scan(
            ScanTarget(
                command="curl https://evil.example/" + "x" * 200,
                language=ScriptLanguage.SHELL,
            )
        )

        assert report.decision == SafetyDecision.DENY
        assert max(len(finding.evidence) for finding in report.findings) <= 40

    def test_scans_500_line_python_under_one_second(self):
        code = "\n".join(f"value_{index} = {index}" for index in range(500))
        scanner = SafetyScanner()

        started_at = time.perf_counter()
        report = scanner.scan(ScanTarget(content=code, language=ScriptLanguage.PYTHON))
        elapsed = time.perf_counter() - started_at

        assert report.decision == SafetyDecision.ALLOW
        assert elapsed < 1.0
        assert report.elapsed_ms < 1000

    def test_scans_500_line_bash_under_one_second(self):
        script = "\n".join(f"echo line-{index}" for index in range(500))
        scanner = SafetyScanner()

        started_at = time.perf_counter()
        report = scanner.scan(ScanTarget(content=script, language=ScriptLanguage.BASH))
        elapsed = time.perf_counter() - started_at

        assert report.decision == SafetyDecision.ALLOW
        assert elapsed < 1.0
        assert report.elapsed_ms < 1000
