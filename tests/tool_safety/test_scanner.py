# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0
"""End-to-end scan tests for sample scripts and custom rule registration."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from trpc_agent_sdk.safety import Decision
from trpc_agent_sdk.safety import PolicyConfig
from trpc_agent_sdk.safety import RiskLevel
from trpc_agent_sdk.safety import SafetyFinding
from trpc_agent_sdk.safety import SafetyRule
from trpc_agent_sdk.safety import SafetyScanner
from trpc_agent_sdk.safety import ScanInput
from trpc_agent_sdk.safety import clear_custom_rules
from trpc_agent_sdk.safety import register_custom_rule
from trpc_agent_sdk.safety import unregister_custom_rule


def _scan(scanner: SafetyScanner, path: Path):
    script = path.read_text(encoding="utf-8")
    lang = "python" if path.suffix == ".py" else "bash"
    return scanner.scan(ScanInput(script=script, language=lang, tool_name=path.name))


def _assert_report_well_formed(report) -> None:
    d = report.to_dict()
    for field in ("decision", "risk_level", "rule_ids"):
        assert field in d
    if report.findings:
        f = report.findings[0]
        assert f.rule_id
        assert f.evidence is not None
        assert f.recommendation
        assert f.risk_level in RiskLevel


# ---------------------------------------------------------------------------
# Canonical 12 samples
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
    _assert_report_well_formed(report)


def test_04_network_exfil(scanner, samples_dir):
    report = _scan(scanner, samples_dir / "04_network_exfil.py")
    assert report.decision == Decision.DENY
    assert "R002_network_egress" in report.rule_ids
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
    assert report.decision == Decision.DENY
    assert len(report.rule_ids) > 0
    _assert_report_well_formed(report)


def test_12_human_review(scanner, samples_dir):
    report = _scan(scanner, samples_dir / "12_human_review.py")
    assert report.decision in (Decision.NEEDS_HUMAN_REVIEW, Decision.DENY)
    assert report.decision != Decision.ALLOW
    _assert_report_well_formed(report)


def test_detection_rate(scanner, samples_dir):
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
    must = ["02_dangerous_delete.sh", "03_read_credentials.py", "04_network_exfil.py"]
    for name in must:
        assert _scan(scanner, samples_dir / name).decision == Decision.DENY, name


def test_manifest_cases(scanner, samples_dir, manifest_path):
    """All manifest cases must produce the expected decision."""
    data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    cases = data["cases"]
    failures = []
    for case in cases:
        name = case["file"]
        expect = case["expect"]
        report = _scan(scanner, samples_dir / name)
        ok = False
        if expect == "allow":
            ok = report.decision == Decision.ALLOW
        elif expect == "deny":
            ok = report.decision == Decision.DENY
        elif expect == "needs_human_review":
            ok = report.decision in (Decision.NEEDS_HUMAN_REVIEW, Decision.DENY)
        if not ok:
            failures.append(f"{name}: got {report.decision.value}, expect {expect}, rules={report.rule_ids}")
        must_rules = case.get("must_rules") or []
        for rid in must_rules:
            if rid not in report.rule_ids:
                failures.append(f"{name}: missing rule {rid}, got {report.rule_ids}")
    assert not failures, "\n".join(failures)


class _NoOpRule(SafetyRule):
    rule_id = "TEST_CUSTOM_001"
    rule_name = "test custom rule"
    risk_type = "test"
    default_level = RiskLevel.LOW

    def check(self, scan_input, policy):
        return [SafetyFinding(
            rule_id=self.rule_id,
            rule_name=self.rule_name,
            risk_type=self.risk_type,
            risk_level=self.default_level,
            evidence="custom rule triggered",
            line=1,
            recommendation="this is a test rule",
        )]


def test_register_custom_rule_is_picked_up_by_new_scanner():
    clear_custom_rules()
    custom = _NoOpRule()
    register_custom_rule(custom)
    try:
        scanner = SafetyScanner(policy=PolicyConfig())
        rule_ids = [r.rule_id for r in scanner.rules]
        assert "TEST_CUSTOM_001" in rule_ids
    finally:
        unregister_custom_rule("TEST_CUSTOM_001")
