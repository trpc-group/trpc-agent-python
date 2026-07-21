from pathlib import Path

import pytest
from pydantic import ValidationError

from trpc_agent_sdk.tools.safety import SafetyDecision
from trpc_agent_sdk.tools.safety import ToolSafetyPolicy
from trpc_agent_sdk.tools.safety import load_policy


def test_load_default_policy():
    policy = load_policy()

    assert policy.fail_closed is True
    assert policy.block_on_review is True
    assert policy.is_command_allowed("git")
    assert not policy.is_command_allowed("/tmp/untrusted/git")


def test_policy_yaml_is_strict(tmp_path):
    policy_file = tmp_path / "policy.yaml"
    policy_file.write_text("allowed_domains: [example.com]\nunknown_option: true\n", encoding="utf-8")

    with pytest.raises(ValidationError, match="unknown_option"):
        ToolSafetyPolicy.from_yaml(policy_file)


@pytest.mark.parametrize("content", ["- not-a-mapping\n", "allowed_domains: [broken\n"])
def test_policy_rejects_invalid_yaml_roots(tmp_path, content):
    policy_file = tmp_path / "policy.yaml"
    policy_file.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError, match="safety policy"):
        ToolSafetyPolicy.from_yaml(policy_file)


def test_policy_missing_file_has_clear_error(tmp_path):
    with pytest.raises(ValueError, match="unable to read safety policy"):
        ToolSafetyPolicy.from_yaml(tmp_path / "missing.yaml")


def test_domain_allowlist_uses_label_boundaries_and_normalizes_urls():
    policy = ToolSafetyPolicy(allowed_domains=["https://API.Example.com/path", "*.trusted.test"])

    assert policy.is_domain_allowed("api.example.com")
    assert policy.is_domain_allowed("v2.api.example.com")
    assert policy.is_domain_allowed("trusted.test")
    assert policy.is_domain_allowed("cdn.trusted.test")
    assert not policy.is_domain_allowed("api.example.com.evil.test")
    assert not policy.is_domain_allowed("notexample.com")


def test_policy_normalizes_command_basenames():
    policy = ToolSafetyPolicy(allowed_commands=["/usr/bin/GIT", " echo "])

    assert policy.allowed_commands == ["/usr/bin/git", "echo"]
    assert policy.is_command_allowed("/usr/bin/git")
    assert not policy.is_command_allowed("/opt/bin/git")
    assert not policy.is_command_allowed("git-upload-pack")


def test_basename_allowlist_does_not_allow_relative_executable_path():
    policy = ToolSafetyPolicy(allowed_commands=["git"])

    assert not policy.is_command_allowed("./git")
    assert not policy.is_command_allowed("tools/../git")
    assert ToolSafetyPolicy(allowed_commands=["./git"]).is_command_allowed("tools/../git")


@pytest.mark.parametrize(
    "candidate",
    [
        "~/.ssh/id_rsa",
        "$HOME/.ssh/id_ed25519",
        "${HOME}/.aws/credentials",
        ".env",
        "config/.env.production",
        "/etc/shadow",
        "/tmp/../etc/shadow",
        r"C:\\repo\\service.credentials.json",
    ],
)
def test_default_denied_paths(candidate):
    assert ToolSafetyPolicy().is_path_denied(candidate)


def test_home_prefix_does_not_match_unrelated_denied_home_path():
    policy = ToolSafetyPolicy(denied_paths=["~/.ssh"])

    assert not policy.is_path_denied("$HOME/documents/readme.txt")


def test_rule_action_override_is_case_insensitive():
    policy = ToolSafetyPolicy(rule_actions={"net001": "needs_human_review"})

    assert policy.action_for("NET001", SafetyDecision.DENY) is SafetyDecision.NEEDS_HUMAN_REVIEW
    assert policy.action_for("OTHER", SafetyDecision.ALLOW) is SafetyDecision.ALLOW


def test_policy_changes_without_code_changes(tmp_path):
    policy_file = Path(tmp_path) / "policy.yaml"
    policy_file.write_text(
        """
version: custom
allowed_domains:
  - internal.example
allowed_commands:
  - custom-runner
denied_paths:
  - /company/secrets
max_timeout_seconds: 12
max_output_bytes: 2048
""".strip(),
        encoding="utf-8",
    )

    policy = load_policy(policy_file)

    assert policy.version == "custom"
    assert policy.is_domain_allowed("api.internal.example")
    assert policy.is_command_allowed("custom-runner")
    assert policy.is_path_denied("/company/secrets/token")
    assert policy.max_timeout_seconds == 12
    assert policy.max_output_bytes == 2048
