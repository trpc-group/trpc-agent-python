# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for the decision engine: report shape, aggregation and redaction."""

from __future__ import annotations

import pytest

from trpc_agent_sdk.tools.safety.engine import SafetyEngine
from trpc_agent_sdk.tools.safety.models import Decision
from trpc_agent_sdk.tools.safety.models import Language
from trpc_agent_sdk.tools.safety.models import RiskLevel
from trpc_agent_sdk.tools.safety.models import ScanInput
from trpc_agent_sdk.tools.safety.policy import SafetyPolicy


@pytest.fixture
def engine():
    return SafetyEngine(SafetyPolicy(allow_domains=["api.example.com"],
                                     allowed_commands=["ls", "echo"]))


def _scan(engine, code, lang=Language.PYTHON):
    return engine.scan(ScanInput(script=code, language=lang, tool_name="t"))


class TestReportFields:
    """Acceptance 5: report carries the five required elements."""

    def test_report_has_five_required_elements(self, engine):
        report = _scan(engine, 'import shutil\nshutil.rmtree("/")')
        data = report.to_dict()
        assert data["decision"] == "deny"
        assert data["risk_level"] == "critical"
        assert data["findings"], "expected at least one finding"
        finding = data["findings"][0]
        assert finding["rule_id"]
        assert finding["evidence"]["snippet"]
        assert finding["evidence"]["line"] >= 1
        assert finding["recommendation"]

    def test_scan_duration_is_recorded(self, engine):
        report = _scan(engine, "x = 1")
        assert report.scan_duration_ms >= 0.0


class TestAggregation:
    """Acceptance covered by design section 4."""

    def test_no_findings_is_allow(self, engine):
        report = _scan(engine, "print('hi')\nresult = 2 + 2")
        assert report.decision == Decision.ALLOW
        assert report.risk_level == RiskLevel.LOW

    def test_critical_denies(self, engine):
        assert _scan(engine, 'open("/root/.ssh/id_rsa").read()').decision == Decision.DENY

    def test_medium_is_review(self, engine):
        assert _scan(engine, 'import subprocess\nsubprocess.run(["ls"])').decision == Decision.NEEDS_HUMAN_REVIEW

    def test_deny_wins_over_review(self, engine):
        code = 'import subprocess, shutil\nsubprocess.run(["ls"])\nshutil.rmtree("/")'
        assert _scan(engine, code).decision == Decision.DENY

    def test_risk_level_is_max(self, engine):
        code = 'import subprocess, shutil\nsubprocess.run(["ls"])\nshutil.rmtree("/")'
        assert _scan(engine, code).risk_level == RiskLevel.CRITICAL


class TestRedaction:

    def test_hardcoded_secret_is_redacted(self, engine):
        report = _scan(engine, 'key = "AKIAIOSFODNN7EXAMPLE"')
        assert report.redacted is True
        joined = " ".join(f.evidence.snippet for f in report.findings)
        assert "AKIAIOSFODNN7EXAMPLE" not in joined

    def test_no_redaction_flag_when_nothing_masked(self, engine):
        report = _scan(engine, 'import subprocess\nsubprocess.run(["ls"])')
        assert report.redacted is False


class TestPerformance:
    """Acceptance 4: a single 500-line scan completes well under one second."""

    def test_500_line_scan_under_one_second(self, engine):
        import time
        script = "\n".join(f"v{i} = {i} * {i} + {i}" for i in range(500))
        start = time.perf_counter()
        engine.scan(ScanInput(script=script, language=Language.PYTHON, tool_name="perf"))
        assert (time.perf_counter() - start) < 1.0

    def test_pathological_long_line_is_bounded(self, engine):
        # A very long single line must not hang the scanner (ReDoS guard).
        import time
        script = "x = '" + ("a" * 500_000) + "'"
        start = time.perf_counter()
        engine.scan(ScanInput(script=script, language=Language.PYTHON, tool_name="redos"))
        assert (time.perf_counter() - start) < 1.0
