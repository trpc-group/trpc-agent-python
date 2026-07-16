# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for report building and rendering."""
import json
from pathlib import Path

from review.findings import Finding
from review.governance import GovernanceDecision
from review.report import build_report, render_markdown, write_reports
from review.sandbox import SandboxRunOutcome


def _report(reported=None, events=None):
    return build_report(
        task_id="t1", input_ref="x.diff", runtime="local", dry_run=True,
        diff_summary={"files_changed": 1},
        reported=reported or [],
        needs_review=[Finding(severity="low", category="db_lifecycle", file="b.py",
                              line=2, title="cursor", confidence=0.5)],
        deduped_count=1,
        filter_events=events or [GovernanceDecision("curl x", "deny", "network_policy", "no net")],
        sandbox_outcomes=[SandboxRunOutcome(script="check_security.py", status="ok",
                                            exit_code=0, duration_ms=10, timed_out=False,
                                            stdout="{}", stderr="")],
        metrics={"total_duration_ms": 100, "sandbox_duration_ms": 50, "tool_calls": 6,
                 "intercepts": 1, "findings_total": 1,
                 "severity_distribution": {"high": 1}, "error_distribution": {}},
        llm_summary="fine", warnings=["w1"])


def test_conclusion_blocked_on_high():
    rep = _report(reported=[Finding(severity="high", category="security", file="a.py",
                                    line=1, title="eval", confidence=0.9)])
    assert rep["conclusion"] == "blocked"


def test_conclusion_needs_attention_on_intercepts_only():
    rep = _report()
    assert rep["conclusion"] == "needs_attention"


def test_conclusion_pass_when_clean():
    rep = _report(events=[GovernanceDecision("ok", "allow")])
    assert rep["conclusion"] == "pass"


def test_report_sections_present():
    rep = _report()
    for key in ("summary", "findings", "needs_human_review", "filter_intercepts",
                "sandbox_runs", "metrics", "llm_summary", "warnings"):
        assert key in rep
    assert rep["summary"]["needs_human_review_count"] == 1
    assert rep["summary"]["deduplicated"] == 1


def test_markdown_contains_sections():
    md = render_markdown(_report())
    for heading in ("# Code Review Report", "## Findings", "## Needs Human Review",
                    "## Filter Intercepts", "## Sandbox Runs", "## Metrics"):
        assert heading in md


def test_write_reports_redacts(tmp_path):
    rep = _report(reported=[Finding(
        severity="critical", category="secret_leak", file="s.py", line=1,
        title="secret", evidence='k = "sk-abcdefghijklmnopqrstuvwxyz123456"',
        confidence=0.95)])
    json_path, md_path = write_reports(rep, str(tmp_path))
    text = Path(json_path).read_text() + Path(md_path).read_text()
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in text
    assert json.loads(Path(json_path).read_text())["task_id"] == "t1"
