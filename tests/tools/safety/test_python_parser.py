# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for PythonParser."""

from __future__ import annotations

import pytest

from trpc_agent_sdk.tools.safety import Decision
from trpc_agent_sdk.tools.safety import PolicyConfig
from trpc_agent_sdk.tools.safety import PythonParser
from trpc_agent_sdk.tools.safety import RiskLevel
from trpc_agent_sdk.tools.safety import SafetyScanner
from trpc_agent_sdk.tools.safety import ScanRequest
from trpc_agent_sdk.tools.safety import ScriptLanguage


@pytest.fixture
def parser():
    return PythonParser(PolicyConfig.default())


class TestPythonParserSafe:

    def test_safe_print(self, parser):
        findings = parser.parse("print('hello')")
        assert findings == []

    def test_safe_arithmetic(self, parser):
        findings = parser.parse("x = 1 + 2\nprint(x)")
        assert findings == []


class TestPythonParserDangerousFileOps:

    def test_open_call(self, parser):
        findings = parser.parse("open('/etc/passwd')")
        rule_ids = {f.rule_id for f in findings}
        assert len(findings) >= 1
        assert "R001_FILE_DANGEROUS_OPEN" in rule_ids or "R001_CREDENTIAL_FILE_ACCESS" in rule_ids

    def test_shutil_rmtree(self, parser):
        findings = parser.parse("import shutil; shutil.rmtree('/tmp/danger')")
        rule_ids = {f.rule_id for f in findings}
        assert "R001_RECURSIVE_DELETE" in rule_ids

    def test_os_remove(self, parser):
        findings = parser.parse("import os; os.remove('/tmp/file')")
        rule_ids = {f.rule_id for f in findings}
        assert "R001_FILE_DELETE" in rule_ids


class TestPythonParserNetworkEgress:

    def test_requests_import(self, parser):
        findings = parser.parse("import requests")
        rule_ids = {f.rule_id for f in findings}
        assert "R002_NETWORK_EGRESS" in rule_ids

    def test_socket_import(self, parser):
        findings = parser.parse("import socket")
        rule_ids = {f.rule_id for f in findings}
        assert "R002_NETWORK_EGRESS" in rule_ids

    def test_requests_get_call(self, parser):
        findings = parser.parse("import requests; requests.get('https://evil.com')")
        rule_ids = {f.rule_id for f in findings}
        assert "R002_REQUESTS_EXTERNAL_REQUEST" in rule_ids


class TestPythonParserSystemCommands:

    def test_subprocess_run(self, parser):
        findings = parser.parse("import subprocess; subprocess.run(['ls'])")
        rule_ids = {f.rule_id for f in findings}
        assert "R003_SUBPROCESS_EXECUTION" in rule_ids

    def test_os_system(self, parser):
        findings = parser.parse("import os; os.system('ls')")
        rule_ids = {f.rule_id for f in findings}
        assert "R003_OS_SYSTEM_EXECUTION" in rule_ids

    def test_eval_call(self, parser):
        findings = parser.parse("eval('1+1')")
        rule_ids = {f.rule_id for f in findings}
        assert "R003_DYNAMIC_CODE_EXECUTION" in rule_ids

    def test_shell_true(self, parser):
        findings = parser.parse("import subprocess; subprocess.run('ls', shell=True)")
        rule_ids = {f.rule_id for f in findings}
        assert "R003_SHELL_PIPE_EXECUTION" in rule_ids


class TestPythonParserDependencyInstall:

    def test_pip_install_text(self, parser):
        findings = parser.parse("# pip install requests")
        rule_ids = {f.rule_id for f in findings}
        assert "R004_PIP_INSTALL" in rule_ids


class TestPythonParserResourceAbuse:

    def test_while_true(self, parser):
        findings = parser.parse("while True:\n    pass")
        rule_ids = {f.rule_id for f in findings}
        assert "R005_INFINITE_LOOP" in rule_ids


class TestPythonParserSecretExfiltration:

    def test_api_key_in_string(self, parser):
        findings = parser.parse('api_key = "sk-xxx"')
        # Evidence should be sanitized
        for f in findings:
            assert "sk-" not in f.evidence or "[SANITIZED]" in f.evidence


class TestPythonParserRegexFallback:

    def test_syntax_error_falls_back(self, parser):
        findings = parser.parse("this is not valid python !!!")
        parse_failures = [f for f in findings if f.rule_id == "R007_PARSE_FAILURE"]
        assert len(parse_failures) == 1
        assert parse_failures[0].risk_level == RiskLevel.HIGH
        assert parse_failures[0].metadata.get("parse_failed") is True

    def test_syntax_error_with_os_system_fails_closed(self):
        scanner = SafetyScanner(PolicyConfig.default())
        report = scanner.scan(
            ScanRequest(
                script="!\nimport os\nos.system('rm -rf /')",
                language=ScriptLanguage.PYTHON,
                tool_name="test",
            ))
        assert report.decision == Decision.DENY
        assert report.risk_level == RiskLevel.HIGH
        assert "R007_PARSE_FAILURE" in report.rule_ids


class TestGetattrEvasionCoverage:

    def test_getattr_builtins_popen(self, parser):
        """getattr(__builtins__, 'popen') → detected"""
        findings = parser.parse("getattr(__builtins__, 'popen')('ls')")
        rule_ids = {f.rule_id for f in findings}
        assert "R003_DYNAMIC_CODE_EXECUTION" in rule_ids

    def test_getattr_builtins_system(self, parser):
        """getattr(__builtins__, 'system') → detected"""
        findings = parser.parse("getattr(__builtins__, 'system')('whoami')")
        rule_ids = {f.rule_id for f in findings}
        assert "R003_DYNAMIC_CODE_EXECUTION" in rule_ids

    def test_builtins_eval(self, parser):
        """__builtins__.eval('1+1') → detected via last-segment matching"""
        findings = parser.parse("__builtins__.eval('1+1')")
        rule_ids = {f.rule_id for f in findings}
        assert "R003_DYNAMIC_CODE_EXECUTION" in rule_ids

    def test_builtins_exec(self, parser):
        """builtins.exec('x=1') → detected via last-segment matching"""
        findings = parser.parse("builtins.exec('x=1')")
        rule_ids = {f.rule_id for f in findings}
        assert "R003_DYNAMIC_CODE_EXECUTION" in rule_ids


class TestRawDottedNameCoverage:

    def test_call_on_expression_result(self, parser):
        """Call on unresolved expression → <expr> path in _raw_dotted_name"""
        findings = parser.parse("foo().bar()")
        # Should not crash; foo().bar() is safe
        assert isinstance(findings, list)

    def test_open_pem_detected(self, parser):
        """open('server.pem') → detected via suffix match."""
        findings = parser.parse("open('server.pem')")
        rule_ids = {f.rule_id for f in findings}
        assert "R001_CREDENTIAL_FILE_ACCESS" in rule_ids

    def test_open_key_write_detected(self, parser):
        """open('cert.key', 'w') → detected (suffix + write)."""
        findings = parser.parse("open('cert.key', 'w')")
        rule_ids = {f.rule_id for f in findings}
        assert "R001_CREDENTIAL_FILE_ACCESS" in rule_ids
        assert "R005_LARGE_FILE_WRITE" in rule_ids


class TestDynamicExecFallback:

    def test_custom_obj_eval_not_high(self, parser):
        """obj.eval() on a non-builtin object should not trigger HIGH DENY."""
        findings = parser.parse("obj = SomeClass()\nobj.eval('expr')")
        # Should not trigger R003_DYNAMIC_CODE_EXECUTION at HIGH level
        high_exec = any(f.rule_id == "R003_DYNAMIC_CODE_EXECUTION" and f.risk_level == "high" for f in findings)
        assert not high_exec

    def test_bare_eval_still_flagged(self, parser):
        """Bare eval() without module prefix should still be flagged HIGH."""
        findings = parser.parse("eval('1+1')")
        rule_ids = {f.rule_id for f in findings}
        assert "R003_DYNAMIC_CODE_EXECUTION" in rule_ids
        # Bare eval should be HIGH
        eval_findings = [f for f in findings if f.rule_id == "R003_DYNAMIC_CODE_EXECUTION"]
        assert any(f.risk_level == "high" for f in eval_findings)

    def test_builtins_eval_still_flagged(self, parser):
        """__builtins__.eval() should still be flagged HIGH."""
        findings = parser.parse("__builtins__.eval('print(1)')")
        rule_ids = {f.rule_id for f in findings}
        assert "R003_DYNAMIC_CODE_EXECUTION" in rule_ids


class TestEnvSecretAccess:

    def test_os_getenv_secret_key_detected(self, parser):
        """os.getenv('API_KEY') should be detected as secret env access."""
        findings = parser.parse("""
import os
secret = os.getenv('API_KEY')
""")
        rule_ids = {f.rule_id for f in findings}
        assert "R006_SECRET_ENV_ACCESS" in rule_ids

    def test_os_environ_get_secret_detected(self, parser):
        """os.environ.get('SECRET_TOKEN') should be detected."""
        findings = parser.parse("""
import os
secret = os.environ.get('SECRET_TOKEN')
""")
        rule_ids = {f.rule_id for f in findings}
        assert "R006_SECRET_ENV_ACCESS" in rule_ids

    def test_os_getenv_non_secret_not_flagged(self, parser):
        """os.getenv('PATH') should NOT be flagged (not a secret key name)."""
        findings = parser.parse("""
import os
path = os.getenv('PATH')
""")
        rule_ids = {f.rule_id for f in findings}
        assert "R006_SECRET_ENV_ACCESS" not in rule_ids
