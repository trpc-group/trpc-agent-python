"""Tests for the Bash lexer-lite scanner."""

from __future__ import annotations

import pytest

from trpc_agent_sdk.tools.safety._guard import ToolSafetyGuard
from trpc_agent_sdk.tools.safety._models import SafetyDecision, SafetyScanRequest, ScriptLanguage
from trpc_agent_sdk.tools.safety._policy import load_safety_policy_dict


@pytest.fixture
def guard(strict_policy_dict):
    return ToolSafetyGuard(load_safety_policy_dict(strict_policy_dict))


def scan(guard, script):
    return guard.scan(SafetyScanRequest(
        tool_name="t", language=ScriptLanguage.BASH, script=script,
    ))


def test_safe_bash_allows(guard):
    report = scan(guard, "echo hello\npwd\nls -la\n")
    assert report.decision == SafetyDecision.ALLOW


def test_recursive_delete_denies(guard):
    report = scan(guard, "rm -rf /tmp/x\n")
    assert "FILE001_RECURSIVE_DELETE" in report.rule_ids
    assert report.decision == SafetyDecision.DENY


def test_credential_read_denies(guard):
    report = scan(guard, "cat ~/.ssh/id_rsa\n")
    assert "FILE003_CREDENTIAL_READ" in report.rule_ids
    assert report.decision == SafetyDecision.DENY


def test_dotenv_read_denies(guard):
    report = scan(guard, "cat .env\n")
    assert "FILE004_DOTENV_READ" in report.rule_ids
    assert report.decision == SafetyDecision.DENY


def test_non_allowlist_network_denies(guard):
    report = scan(guard, "curl https://evil.example.com/x\n")
    assert "NET001_DOMAIN_NOT_ALLOWED" in report.rule_ids
    assert report.decision == SafetyDecision.DENY


def test_allowlist_network_allows(guard):
    report = scan(guard, "curl https://api.github.com/x\n")
    assert report.decision == SafetyDecision.ALLOW


def test_pip_install_denies(guard):
    report = scan(guard, "pip install numpy\n")
    assert "DEP001_ENV_MUTATION" in report.rule_ids
    assert report.decision == SafetyDecision.DENY


def test_python_module_pip_install_denies(guard):
    report = scan(guard, "python -m pip install numpy\n")
    assert "DEP001_ENV_MUTATION" in report.rule_ids
    assert report.decision == SafetyDecision.DENY


def test_apt_install_denies(guard):
    report = scan(guard, "apt-get install -y curl\n")
    assert "DEP001_ENV_MUTATION" in report.rule_ids
    assert report.decision == SafetyDecision.DENY


def test_pipeline_review(guard):
    report = scan(guard, "ls | grep foo\n")
    assert "PROC003_SHELL_OPERATOR" in report.rule_ids
    assert report.decision == SafetyDecision.NEEDS_HUMAN_REVIEW


def test_privilege_escalation_denies(guard):
    report = scan(guard, "sudo ls\n")
    assert "PROC004_PRIVILEGE" in report.rule_ids
    assert report.decision == SafetyDecision.DENY


def test_fork_bomb_denies(guard):
    report = scan(guard, ":(){ :|:& };\n")
    assert "RES002_FORK_BOMB" in report.rule_ids
    assert report.decision == SafetyDecision.DENY


def test_long_sleep_denies(guard):
    report = scan(guard, "sleep 120\n")
    assert "RES003_LONG_SLEEP" in report.rule_ids
    assert report.decision == SafetyDecision.DENY


def test_unbounded_loop_denies(guard):
    report = scan(guard, "while true; do echo x; done\n")
    assert "RES001_UNBOUNDED_LOOP" in report.rule_ids
    assert report.decision == SafetyDecision.DENY


def test_dynamic_eval_review(guard):
    report = scan(guard, "eval \"$(base64 -d <<<'xxx')\"\n")
    assert "OBF001_DYNAMIC_EXEC" in report.rule_ids


@pytest.mark.parametrize("script", [
    "source ./payload.sh\n",
    "xargs -a commands.txt sh\n",
    r"find . -exec sh {} \;\n",
])
def test_indirect_execution_requires_review(guard, script):
    report = scan(guard, script)
    assert "OBF001_DYNAMIC_EXEC" in report.rule_ids
    assert report.decision == SafetyDecision.NEEDS_HUMAN_REVIEW


@pytest.mark.parametrize("script", [
    "python payload.py\n",
    "node payload.js\n",
    "bash -x payload.sh\n",
    "python -m untrusted_module\n",
])
def test_interpreter_payload_requires_review(guard, script):
    report = scan(guard, script)
    assert "OBF001_DYNAMIC_EXEC" in report.rule_ids
    assert report.decision == SafetyDecision.NEEDS_HUMAN_REVIEW


def test_shebang_and_comment_ignored(guard):
    report = scan(guard, "#!/usr/bin/env bash\n# comment\necho hi\n")
    assert report.decision == SafetyDecision.ALLOW


def test_secret_env_to_echo_denies(guard):
    report = scan(guard, "echo \"token=$API_TOKEN\"\n")
    assert "SECRET001_LOG_SINK" in report.rule_ids
    assert report.decision == SafetyDecision.DENY


def test_unbalanced_quote_yields_parse_review(guard):
    report = scan(guard, "echo 'hello\n")
    assert "PARSE001_UNCERTAIN" in report.rule_ids
    assert report.decision == SafetyDecision.NEEDS_HUMAN_REVIEW


def test_quoted_separator_not_split(guard):
    # ``'; rm -rf /'`` is inside single quotes; lexer must not split.
    report = scan(guard, "echo '; rm -rf /'\n")
    # echo is safe, no shell_operator finding should fire on quoted text
    assert "PROC003_SHELL_OPERATOR" not in report.rule_ids
