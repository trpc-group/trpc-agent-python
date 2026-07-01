# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0
"""End-to-end scan tests for the 12 sample scripts required by the issue.

Each sample maps to one of the issue's required test cases. The scanner must
produce a structured report with the expected decision and rule hits.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from examples.tool_safety.safety import Decision
from examples.tool_safety.safety import RiskLevel
from examples.tool_safety.safety import SafetyScanner
from examples.tool_safety.safety import ScanInput


def _scan(scanner: SafetyScanner, path: Path) -> "object":
    script = path.read_text(encoding="utf-8")
    lang = "python" if path.suffix == ".py" else "bash"
    return scanner.scan(ScanInput(script=script, language=lang, tool_name=path.name))


# ---------------------------------------------------------------------------
# 12 sample tests (issue required coverage)
# ---------------------------------------------------------------------------


def test_01_safe_python(scanner, samples_dir):
    report = _scan(scanner, samples_dir / "01_safe_python.py")
    assert report.decision == Decision.ALLOW
    assert report.findings == []
    _assert_report_well_formed(report)


def test_02_dangerous_delete(scanner, samples_dir):
    report = _scan(scanner, samples_dir / "02_dangerous_delete.sh")
    assert report.decision == Decision.DENY
    assert "R001_dangerous_files" in report.rule_ids
    _assert_report_well_formed(report)


def test_03_read_credentials(scanner, samples_dir):
    report = _scan(scanner, samples_dir / "03_read_credentials.py")
    assert report.decision == Decision.DENY
    assert "R001_dangerous_files" in report.rule_ids
    # 100% detection required by issue acceptance criterion 3
    _assert_report_well_formed(report)


def test_04_network_exfil(scanner, samples_dir):
    report = _scan(scanner, samples_dir / "04_network_exfil.py")
    assert report.decision == Decision.DENY
    assert "R002_network_egress" in report.rule_ids
    # 100% detection required by issue acceptance criterion 3
    _assert_report_well_formed(report)


def test_05_whitelist_network(scanner, samples_dir):
    report = _scan(scanner, samples_dir / "05_whitelist_network.py")
    assert report.decision == Decision.ALLOW
    _assert_report_well_formed(report)


def test_06_subprocess_call(scanner, samples_dir):
    report = _scan(scanner, samples_dir / "06_subprocess_call.py")
    assert report.decision == Decision.DENY
    assert "R003_process_system" in report.rule_ids
    _assert_report_well_formed(report)


def test_07_shell_injection(scanner, samples_dir):
    report = _scan(scanner, samples_dir / "07_shell_injection.sh")
    assert report.decision == Decision.DENY
    assert "R003_process_system" in report.rule_ids
    _assert_report_well_formed(report)


def test_08_dependency_install(scanner, samples_dir):
    report = _scan(scanner, samples_dir / "08_dependency_install.sh")
    assert report.decision == Decision.DENY
    assert "R004_dependency_install" in report.rule_ids
    _assert_report_well_formed(report)


def test_09_infinite_loop(scanner, samples_dir):
    report = _scan(scanner, samples_dir / "09_infinite_loop.py")
    assert report.decision == Decision.DENY
    assert "R005_resource_abuse" in report.rule_ids
    _assert_report_well_formed(report)


def test_10_secret_leak(scanner, samples_dir):
    report = _scan(scanner, samples_dir / "10_secret_leak.py")
    assert report.decision == Decision.DENY
    assert "R006_secret_leak" in report.rule_ids
    _assert_report_well_formed(report)


def test_11_bash_pipeline(scanner, samples_dir):
    report = _scan(scanner, samples_dir / "11_bash_pipeline.sh")
    # Must flag at least one risk (network/process/resource).
    assert report.decision == Decision.DENY
    assert len(report.rule_ids) > 0
    _assert_report_well_formed(report)


def test_12_human_review(scanner, samples_dir):
    report = _scan(scanner, samples_dir / "12_human_review.py")
    # Dynamic target => cannot prove safety => must NOT be ALLOW.
    assert report.decision in (Decision.NEEDS_HUMAN_REVIEW, Decision.DENY)
    assert report.decision != Decision.ALLOW
    _assert_report_well_formed(report)


# ---------------------------------------------------------------------------
# Aggregate acceptance criteria
# ---------------------------------------------------------------------------


def test_detection_rate(scanner, samples_dir):
    """Issue criterion 2: high-risk detection >= 90%, safe false-positive <= 10%."""
    dangerous = [
        "02_dangerous_delete.sh", "03_read_credentials.py", "04_network_exfil.py",
        "06_subprocess_call.py", "07_shell_injection.sh", "08_dependency_install.sh",
        "09_infinite_loop.py", "10_secret_leak.py", "11_bash_pipeline.sh",
    ]
    safe = ["01_safe_python.py", "05_whitelist_network.py"]

    detected = sum(
        1 for name in dangerous
        if _scan(scanner, samples_dir / name).decision == Decision.DENY
    )
    false_pos = sum(
        1 for name in safe
        if _scan(scanner, samples_dir / name).decision != Decision.ALLOW
    )
    assert detected / len(dangerous) >= 0.9
    assert false_pos / len(safe) <= 0.1


def test_required_100_percent_detection(scanner, samples_dir):
    """Issue criterion 3: read-creds / dangerous-delete / non-allowlist net = 100%."""
    must = ["02_dangerous_delete.sh", "03_read_credentials.py", "04_network_exfil.py"]
    for name in must:
        assert _scan(scanner, samples_dir / name).decision == Decision.DENY, name


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _assert_report_well_formed(report) -> None:
    """Issue criterion 5: report must carry the 5 required fields."""
    d = report.to_dict()
    for field in ("decision", "risk_level", "rule_ids"):
        assert field in d
    if report.findings:
        f = report.findings[0]
        assert f.rule_id
        assert f.evidence is not None
        assert f.recommendation
        assert f.risk_level in RiskLevel
