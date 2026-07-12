# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Focused behavior tests for the static tool safety scanner."""

from __future__ import annotations

import hashlib
import json

import pytest

from trpc_agent_sdk.tools.safety._models import RiskCategory
from trpc_agent_sdk.tools.safety._models import RiskLevel
from trpc_agent_sdk.tools.safety._models import SafetyDecision
from trpc_agent_sdk.tools.safety._models import SafetyScanRequest
from trpc_agent_sdk.tools.safety._policy import ToolSafetyPolicy
from trpc_agent_sdk.tools.safety._rules import BaseSafetyRule
from trpc_agent_sdk.tools.safety._scanner import ToolSafetyScanner


def _request(script: str, language: str = "python", **kwargs) -> SafetyScanRequest:
    return SafetyScanRequest(script=script, language=language, tool_name="test_tool", **kwargs)


def _scan(script: str, language: str = "python", *, policy=None, **kwargs):
    return ToolSafetyScanner(policy=policy).scan(_request(script, language, **kwargs))


def _rules(report) -> set[str]:
    return set(report.rule_ids)


def test_safe_python_is_allowed_and_only_hash_is_retained():
    script = "values = [1, 2, 3]\nprint(sum(values))"

    report = _scan(script)

    assert report.decision == SafetyDecision.ALLOW
    assert report.blocked is False
    assert report.rule_id is None
    assert report.script_sha256 == hashlib.sha256(script.encode()).hexdigest()
    assert script not in report.model_dump_json()


def test_comments_and_inert_strings_do_not_trigger_command_rules():
    script = '''
# rm -rf / && curl https://evil.invalid
example = "sudo rm -rf / | pip install malware"
print(len(example))
'''

    report = _scan(script)

    assert report.decision == SafetyDecision.ALLOW
    assert report.findings == []


def test_bash_comments_and_quoted_operators_are_not_executable():
    script = 'echo "rm -rf / | curl https://evil.invalid" # sudo apt install bad'

    report = _scan(script, "bash")

    assert report.decision == SafetyDecision.ALLOW
    assert "PROC-PIPELINE" not in _rules(report)
    assert "FILE-DANGEROUS-DELETE" not in _rules(report)


def test_recursive_bash_delete_is_denied():
    report = _scan("rm -rf ./work", "bash")

    assert report.decision == SafetyDecision.DENY
    assert report.blocked is True
    assert report.rule_id == "FILE-DANGEROUS-DELETE"


def test_python_recursive_delete_is_denied():
    report = _scan("import shutil\nshutil.rmtree('/tmp/build')")

    assert report.decision == SafetyDecision.DENY
    assert "FILE-DANGEROUS-DELETE" in _rules(report)


@pytest.mark.parametrize(
    "script",
    [
        "open('~/.ssh/id_rsa').read()",
        "open('.env').read()",
        "from pathlib import Path\nPath.home().joinpath('.aws/credentials').read_text()",
    ],
)
def test_sensitive_paths_are_always_denied(script):
    report = _scan(script)

    assert report.decision == SafetyDecision.DENY
    assert "FILE-DENIED-PATH" in _rules(report)


def test_denied_cwd_and_argv_are_checked():
    report = _scan("print('ok')", cwd="/etc", argv=["--input", "~/.ssh/id_rsa"])

    assert report.decision == SafetyDecision.DENY
    assert {"POLICY-CWD", "FILE-DENIED-PATH"} <= _rules(report)


def test_non_whitelisted_requests_domain_is_denied():
    policy = ToolSafetyPolicy(allowed_domains=["example.com"])

    report = _scan("import requests\nrequests.get('https://evil.test/v1')", policy=policy)

    assert report.decision == SafetyDecision.DENY
    assert report.rule_id == "NET-NON-WHITELISTED"
    assert report.findings[0].metadata["hostname"] == "evil.test"


@pytest.mark.parametrize("host", ["example.com", "api.example.com", "deep.api.example.com."])
def test_domain_allowlist_matches_exact_and_label_boundary_subdomains(host):
    policy = ToolSafetyPolicy(allowed_domains=["example.com"])
    script = f"import requests\nrequests.get('https://{host}/v1')"

    report = _scan(script, policy=policy)

    assert report.decision == SafetyDecision.ALLOW


@pytest.mark.parametrize("host", ["notexample.com", "example.com.evil.test", "bad-example.com"])
def test_domain_allowlist_rejects_suffix_spoofing(host):
    policy = ToolSafetyPolicy(allowed_domains=["example.com"])
    script = f"import requests\nrequests.get('https://{host}/v1')"

    report = _scan(script, policy=policy)

    assert report.decision == SafetyDecision.DENY
    assert "NET-NON-WHITELISTED" in _rules(report)


def test_dynamic_network_target_requires_review():
    policy = ToolSafetyPolicy(allowed_domains=["example.com"])

    report = _scan("import requests\nrequests.get(target_url)", policy=policy)

    assert report.decision == SafetyDecision.NEEDS_HUMAN_REVIEW
    assert report.rule_id == "NET-DYNAMIC-TARGET"
    assert report.blocked is True


def test_review_can_be_configured_not_to_block():
    policy = ToolSafetyPolicy(block_on_review=False)

    report = _scan("import requests\nrequests.get(target_url)", policy=policy)

    assert report.decision == SafetyDecision.NEEDS_HUMAN_REVIEW
    assert report.blocked is False


def test_socket_literal_target_uses_domain_policy():
    script = "import socket\nsock = socket.socket()\nsock.connect(('outside.test', 443))"

    report = _scan(script)

    assert report.decision == SafetyDecision.DENY
    assert "NET-NON-WHITELISTED" in _rules(report)


def test_database_connect_is_not_mistaken_for_socket_network_access():
    report = _scan("import sqlite3\nsqlite3.connect('cache.db')")

    assert report.decision == SafetyDecision.ALLOW


def test_subprocess_requires_review_even_with_argument_list():
    report = _scan("import subprocess\nsubprocess.run(['git', 'status'], check=True)")

    assert report.decision == SafetyDecision.NEEDS_HUMAN_REVIEW
    assert "PROC-SUBPROCESS" in _rules(report)


def test_subprocess_shell_true_is_denied():
    report = _scan("import subprocess\nsubprocess.run(user_command, shell=True)")

    assert report.decision == SafetyDecision.DENY
    assert report.rule_id == "PROC-SHELL-INJECTION"


def test_os_system_is_denied():
    report = _scan("import os\nos.system('echo hello')")

    assert report.decision == SafetyDecision.DENY
    assert "PROC-OS-SYSTEM" in _rules(report)


def test_bash_pipeline_requires_review_but_quoted_pipe_does_not():
    pipeline = _scan("cat input.txt | grep needle", "bash")
    quoted = _scan("echo 'input | output'", "bash")

    assert pipeline.decision == SafetyDecision.NEEDS_HUMAN_REVIEW
    assert "PROC-PIPELINE" in _rules(pipeline)
    assert quoted.decision == SafetyDecision.ALLOW


def test_background_process_requires_review():
    policy = ToolSafetyPolicy(allowed_commands=["echo", "sleep"])
    report = _scan("sleep 1 & echo done", "bash", policy=policy)

    assert report.decision == SafetyDecision.NEEDS_HUMAN_REVIEW
    assert "PROC-BACKGROUND" in _rules(report)


def test_privilege_escalation_is_denied():
    report = _scan("sudo cat /tmp/input", "bash")

    assert report.decision == SafetyDecision.DENY
    assert report.rule_id == "PROC-PRIVILEGE"


@pytest.mark.parametrize(
    "script",
    [
        "pip install untrusted-package",
        "npm install untrusted-package",
        "apt-get install untrusted-package",
        "python3 -m pip install untrusted-package",
    ],
)
def test_dependency_installation_is_denied(script):
    report = _scan(script, "bash")

    assert report.decision == SafetyDecision.DENY
    assert "DEP-INSTALL" in _rules(report)


def test_import_statement_is_not_dependency_installation():
    report = _scan("import pip\nprint(pip.__name__)")

    assert "DEP-INSTALL" not in _rules(report)
    assert report.decision == SafetyDecision.ALLOW


def test_definite_infinite_loop_is_denied_but_loop_with_break_is_allowed():
    infinite = _scan("while True:\n    pass")
    bounded = _scan("while True:\n    break")

    assert infinite.decision == SafetyDecision.DENY
    assert "RES-INFINITE-LOOP" in _rules(infinite)
    assert bounded.decision == SafetyDecision.ALLOW


def test_shell_fork_bomb_is_denied():
    report = _scan(":(){ :|:& };:", "bash")

    assert report.decision == SafetyDecision.DENY
    assert "RES-FORK-BOMB" in _rules(report)


def test_long_sleep_requires_review():
    policy = ToolSafetyPolicy(long_sleep_seconds=5)

    report = _scan("import time\ntime.sleep(10)", policy=policy)

    assert report.decision == SafetyDecision.NEEDS_HUMAN_REVIEW
    assert "RES-LONG-SLEEP" in _rules(report)


def test_large_constant_write_is_denied():
    policy = ToolSafetyPolicy(max_output_bytes=100)

    report = _scan("open('out.txt', 'w').write('x' * 101)", policy=policy)

    assert report.decision == SafetyDecision.DENY
    assert "RES-LARGE-WRITE" in _rules(report)


def test_excessive_concurrency_is_denied():
    policy = ToolSafetyPolicy(max_concurrency=4)
    script = "from concurrent.futures import ThreadPoolExecutor\npool = ThreadPoolExecutor(max_workers=8)"

    report = _scan(script, policy=policy)

    assert report.decision == SafetyDecision.DENY
    assert "RES-HIGH-CONCURRENCY" in _rules(report)


def test_sensitive_environment_value_flow_to_print_is_denied():
    request = SafetyScanRequest.from_execution(
        script="import os\ntoken = os.getenv('API_TOKEN')\nprint(token)",
        language="python",
        environment={"API_TOKEN": "must-never-appear"},
    )

    report = ToolSafetyScanner().scan(request)
    serialized = report.model_dump_json()

    assert report.decision == SafetyDecision.DENY
    assert {"SECRET-ENV-READ", "SECRET-EXPOSURE"} <= _rules(report)
    assert "must-never-appear" not in serialized


def test_sensitive_environment_value_flow_to_network_is_denied():
    script = "import os, requests\ntoken = os.getenv('AUTH_TOKEN')\nrequests.post('https://example.com', data=token)"
    policy = ToolSafetyPolicy(allowed_domains=["example.com"])

    report = _scan(script, policy=policy, environment_keys=["AUTH_TOKEN"])

    assert report.decision == SafetyDecision.DENY
    assert "SECRET-EXPOSURE" in _rules(report)
    assert "NET-NON-WHITELISTED" not in _rules(report)


def test_private_key_evidence_is_always_redacted():
    script = '''
private_key = """-----BEGIN PRIVATE KEY-----
very-secret-private-key-material
-----END PRIVATE KEY-----"""
print(private_key)
'''

    report = _scan(script)
    serialized = report.model_dump_json()

    assert report.decision == SafetyDecision.DENY
    assert "SECRET-PRIVATE-KEY" in _rules(report)
    assert "very-secret-private-key-material" not in serialized
    assert "[REDACTED_PRIVATE_KEY]" in serialized


@pytest.mark.parametrize(
    ("kwargs", "rule_id"),
    [
        ({
            "timeout_seconds": 11
        }, "POLICY-TIMEOUT"),
        ({
            "output_limit_bytes": 101
        }, "POLICY-OUTPUT-LIMIT"),
    ],
)
def test_execution_limits_are_enforced(kwargs, rule_id):
    policy = ToolSafetyPolicy(max_timeout_seconds=10, max_output_bytes=100)

    report = _scan("print('ok')", policy=policy, **kwargs)

    assert report.decision == SafetyDecision.DENY
    assert rule_id in _rules(report)


def test_oversized_script_is_rejected_without_embedding_script():
    policy = ToolSafetyPolicy(max_script_bytes=16)
    script = "print('this script is too large')"

    report = _scan(script, policy=policy)

    assert report.decision == SafetyDecision.DENY
    assert report.rule_ids == ["POLICY-SCRIPT-SIZE"]
    assert script not in report.model_dump_json()


def test_syntax_error_follows_fail_closed_policy():
    closed = _scan("if True print('bad')", policy=ToolSafetyPolicy(fail_closed=True))
    opened = _scan("if True print('bad')", policy=ToolSafetyPolicy(fail_closed=False))

    assert closed.decision == SafetyDecision.NEEDS_HUMAN_REVIEW
    assert closed.rule_ids == ["SCAN-SYNTAX"]
    assert opened.decision == SafetyDecision.ALLOW


def test_empty_script_follows_fail_closed_policy():
    closed = _scan("", policy=ToolSafetyPolicy(fail_closed=True))
    opened = _scan("", policy=ToolSafetyPolicy(fail_closed=False))

    assert closed.decision == SafetyDecision.NEEDS_HUMAN_REVIEW
    assert closed.rule_ids == ["SCAN-EMPTY"]
    assert opened.decision == SafetyDecision.ALLOW


def test_policy_rule_action_changes_decision_without_code_change():
    policy = ToolSafetyPolicy(rule_actions={"RES-INFINITE-LOOP": SafetyDecision.ALLOW})

    report = _scan("while True:\n    pass", policy=policy)

    assert report.decision == SafetyDecision.ALLOW
    assert report.findings[0].decision == SafetyDecision.ALLOW


def test_allowed_commands_policy_changes_unknown_bash_command_decision():
    default_report = _scan("date", "bash")
    configured_report = _scan("date", "bash", policy=ToolSafetyPolicy(allowed_commands=["date"]))

    assert default_report.decision == SafetyDecision.NEEDS_HUMAN_REVIEW
    assert "POLICY-ARGV-COMMAND" in _rules(default_report)
    assert configured_report.decision == SafetyDecision.ALLOW


class _CustomRule(BaseSafetyRule):
    rule_id = "CUSTOM-001"

    def scan(self, context, policy):
        del context, policy
        yield self._finding(
            rule_id=self.rule_id,
            category=RiskCategory.POLICY_VIOLATION,
            risk_level=RiskLevel.HIGH,
            decision=SafetyDecision.DENY,
            evidence="token=supersecretvalue",
            recommendation="Do not expose token=anothersecretvalue.",
            metadata={"authorization": "Bearer abcdefghijklmnopqrstuvwxyz"},
        )


class _BrokenRule:
    rule_id = "CUSTOM-BROKEN"

    def scan(self, context, policy):
        del context, policy
        raise RuntimeError("secret-token-should-not-leak")


def test_custom_rule_is_pluggable_and_sanitized():
    report = ToolSafetyScanner(rules=[_CustomRule()]).scan(_request("print('ok')"))
    serialized = json.dumps(report.model_dump(mode="json"))

    assert report.decision == SafetyDecision.DENY
    assert report.rule_ids == ["CUSTOM-001"]
    assert "supersecretvalue" not in serialized
    assert "anothersecretvalue" not in serialized
    assert "abcdefghijklmnopqrstuvwxyz" not in serialized


def test_rule_exception_requires_review_when_fail_closed():
    report = ToolSafetyScanner(rules=[_BrokenRule()]).scan(_request("print('ok')"))

    assert report.decision == SafetyDecision.NEEDS_HUMAN_REVIEW
    assert report.rule_ids == ["SCAN-RULE-ERROR"]
    assert "secret-token-should-not-leak" not in report.model_dump_json()


def test_rule_exception_is_skipped_when_fail_open():
    scanner = ToolSafetyScanner(policy=ToolSafetyPolicy(fail_closed=False), rules=[_BrokenRule()])

    report = scanner.scan(_request("print('ok')"))

    assert report.decision == SafetyDecision.ALLOW


def test_invalid_custom_rule_is_rejected_at_construction():
    with pytest.raises(TypeError, match="safety rules"):
        ToolSafetyScanner(rules=[object()])
