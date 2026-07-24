# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

from __future__ import annotations

from trpc_agent_sdk.tools.safety import RiskLevel
from trpc_agent_sdk.tools.safety import SafetyDecision
from trpc_agent_sdk.tools.safety import SafetyPolicy
from trpc_agent_sdk.tools.safety import SafetyScanner
from trpc_agent_sdk.tools.safety import ScanTarget
from trpc_agent_sdk.tools.safety import ScriptLanguage


def _rule_ids(report):
    return {finding.rule_id for finding in report.findings}


class TestPythonSafetyScanner:
    """Test Python AST and fallback scanning."""

    def test_safe_python_allows(self):
        report = SafetyScanner().scan(
            ScanTarget(
                content="value = 40 + 2\nprint(value)\n",
                language=ScriptLanguage.PYTHON,
            )
        )

        assert report.decision == SafetyDecision.ALLOW
        assert report.risk_level == RiskLevel.LOW
        assert report.blocked is False
        assert report.findings == []

    def test_open_env_denies_sensitive_read(self):
        report = SafetyScanner().scan(
            ScanTarget(
                content='secret = open(".env").read()\n',
                language=ScriptLanguage.PYTHON,
            )
        )

        assert report.decision == SafetyDecision.DENY
        assert report.blocked is True
        assert {"FILE_SENSITIVE_READ", "FILE_FORBIDDEN_PATH_ACCESS"} <= _rule_ids(report)

    def test_path_home_ssh_denies_sensitive_read(self):
        report = SafetyScanner().scan(
            ScanTarget(
                content=(
                    "from pathlib import Path\n"
                    "key = (Path.home() / '.ssh' / 'id_rsa').read_text()\n"
                ),
                language=ScriptLanguage.PYTHON,
            )
        )

        assert report.decision == SafetyDecision.DENY
        assert "FILE_SENSITIVE_READ" in _rule_ids(report)

    def test_non_allowlisted_requests_domain_denies(self):
        policy = SafetyPolicy(allowed_domains=["api.github.com"])

        report = SafetyScanner(policy).scan(
            ScanTarget(
                content='requests.get("https://evil.example/api")\n',
                language=ScriptLanguage.PYTHON,
            )
        )

        assert report.decision == SafetyDecision.DENY
        assert "NET_NON_WHITELIST_EGRESS" in _rule_ids(report)

    def test_allowlisted_requests_domain_allows(self):
        policy = SafetyPolicy(allowed_domains=["api.github.com"])

        report = SafetyScanner(policy).scan(
            ScanTarget(
                content='requests.get("https://api.github.com/repos/tencent/trpc-agent-python")\n',
                language=ScriptLanguage.PYTHON,
            )
        )

        assert report.decision == SafetyDecision.ALLOW
        assert "NET_NON_WHITELIST_EGRESS" not in _rule_ids(report)

    def test_dynamic_requests_url_needs_review(self):
        report = SafetyScanner().scan(
            ScanTarget(
                content='requests.get("https://" + host + "/api")\n',
                language=ScriptLanguage.PYTHON,
            )
        )

        assert report.decision == SafetyDecision.NEEDS_HUMAN_REVIEW
        assert report.blocked is True
        assert "NET_DYNAMIC_EGRESS_REVIEW" in _rule_ids(report)

    def test_subprocess_shell_true_needs_review(self):
        report = SafetyScanner().scan(
            ScanTarget(
                content='subprocess.run("cat " + user_input, shell=True)\n',
                language=ScriptLanguage.PYTHON,
            )
        )

        assert report.decision == SafetyDecision.NEEDS_HUMAN_REVIEW
        assert {"PROC_OS_SYSTEM", "PROC_SUBPROCESS_SHELL"} <= _rule_ids(report)

    def test_subprocess_install_command_hits_dependency_rule(self):
        report = SafetyScanner().scan(
            ScanTarget(
                content='subprocess.run("pip install unknown-package", shell=True)\n',
                language=ScriptLanguage.PYTHON,
            )
        )

        assert report.decision == SafetyDecision.NEEDS_HUMAN_REVIEW
        assert "DEP_PIP_INSTALL" in _rule_ids(report)

    def test_infinite_loop_needs_review(self):
        report = SafetyScanner().scan(
            ScanTarget(
                content="while True:\n    pass\n",
                language=ScriptLanguage.PYTHON,
            )
        )

        assert report.decision == SafetyDecision.NEEDS_HUMAN_REVIEW
        assert "RES_INFINITE_LOOP" in _rule_ids(report)

    def test_long_sleep_needs_review(self):
        policy = SafetyPolicy(max_sleep_seconds=10)

        report = SafetyScanner(policy).scan(
            ScanTarget(
                content="import time\ntime.sleep(3600)\n",
                language=ScriptLanguage.PYTHON,
            )
        )

        assert report.decision == SafetyDecision.NEEDS_HUMAN_REVIEW
        assert "RES_LONG_SLEEP" in _rule_ids(report)

    def test_sensitive_env_output_denies_without_env_value(self):
        report = SafetyScanner().scan(
            ScanTarget(
                content='import os\nprint(os.environ["OPENAI_API_KEY"])\n',
                language=ScriptLanguage.PYTHON,
                env={"OPENAI_API_KEY": "cleartext-value"},
            )
        )

        assert report.decision == SafetyDecision.DENY
        assert "LEAK_ENV_SECRET" in _rule_ids(report)
        assert "cleartext-value" not in str(report.model_dump(mode="json"))

    def test_secret_literal_is_redacted(self):
        report = SafetyScanner().scan(
            ScanTarget(
                content='print("api_key=cleartext-value")\n',
                language=ScriptLanguage.PYTHON,
            )
        )

        assert report.decision == SafetyDecision.DENY
        assert "LEAK_SECRET_LITERAL" in _rule_ids(report)
        assert report.redacted is True
        assert "cleartext-value" not in str(report.model_dump(mode="json"))

    def test_parse_failure_uses_fallback_and_does_not_fail_open(self):
        report = SafetyScanner().scan(
            ScanTarget(
                content='if broken(:\n    curl https://evil.example\n',
                language=ScriptLanguage.PYTHON,
            )
        )

        assert report.parser_error is not None
        assert {"PARSER_FALLBACK_USED", "NET_NON_WHITELIST_EGRESS"} <= _rule_ids(report)
        assert report.decision == SafetyDecision.DENY
