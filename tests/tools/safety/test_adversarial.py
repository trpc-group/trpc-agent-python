# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Adversarial / evasion tests for the Tool Script Safety Guard."""

from __future__ import annotations

from trpc_agent_sdk.tools.safety import PolicyConfig
from trpc_agent_sdk.tools.safety import SafetyScanner
from trpc_agent_sdk.tools.safety import ScanRequest
from trpc_agent_sdk.tools.safety import ScriptLanguage


class TestImportAliasEvasion:
    """Verify import aliases are resolved so evasion via renaming is detected."""

    def test_from_os_import_system(self):
        """from os import system; system('ls') → detected as os.system"""
        scanner = SafetyScanner(PolicyConfig.default())
        report = scanner.scan(ScanRequest(
            script="from os import system\nsystem('ls')",
            language=ScriptLanguage.PYTHON,
            tool_name="test",
        ))
        rule_ids = {f.rule_id for f in report.findings}
        assert "R003_OS_SYSTEM_EXECUTION" in rule_ids, (
            f"Expected R003_OS_SYSTEM_EXECUTION, got {rule_ids}")

    def test_import_os_as_myos(self):
        """import os as myos; myos.system('whoami') → detected"""
        scanner = SafetyScanner(PolicyConfig.default())
        report = scanner.scan(ScanRequest(
            script="import os as myos\nmyos.system('whoami')",
            language=ScriptLanguage.PYTHON,
            tool_name="test",
        ))
        rule_ids = {f.rule_id for f in report.findings}
        assert "R003_OS_SYSTEM_EXECUTION" in rule_ids

    def test_from_subprocess_import_run(self):
        """from subprocess import run; run(['ls']) → detected"""
        scanner = SafetyScanner(PolicyConfig.default())
        report = scanner.scan(ScanRequest(
            script="from subprocess import run\nrun(['ls'])",
            language=ScriptLanguage.PYTHON,
            tool_name="test",
        ))
        rule_ids = {f.rule_id for f in report.findings}
        assert "R003_SUBPROCESS_EXECUTION" in rule_ids


class TestGetattrEvasion:
    """Verify getattr-based dynamic code execution is detected."""

    def test_getattr_builtins_eval(self):
        """getattr(__builtins__, 'eval')('1+1') → detected"""
        scanner = SafetyScanner(PolicyConfig.default())
        report = scanner.scan(ScanRequest(
            script="getattr(__builtins__, 'eval')('1+1')",
            language=ScriptLanguage.PYTHON,
            tool_name="test",
        ))
        rule_ids = {f.rule_id for f in report.findings}
        assert "R003_DYNAMIC_CODE_EXECUTION" in rule_ids

    def test_getattr_builtins_exec(self):
        """getattr(__builtins__, 'exec')('import os') → detected"""
        scanner = SafetyScanner(PolicyConfig.default())
        report = scanner.scan(ScanRequest(
            script="getattr(__builtins__, 'exec')('import os')",
            language=ScriptLanguage.PYTHON,
            tool_name="test",
        ))
        rule_ids = {f.rule_id for f in report.findings}
        assert "R003_DYNAMIC_CODE_EXECUTION" in rule_ids

    def test_getattr_with_concatenation(self):
        """getattr(__builtins__, 'ev'+'al')('1+1') → detected via BinOp"""
        scanner = SafetyScanner(PolicyConfig.default())
        report = scanner.scan(ScanRequest(
            script="getattr(__builtins__, 'ev'+'al')('1+1')",
            language=ScriptLanguage.PYTHON,
            tool_name="test",
        ))
        rule_ids = {f.rule_id for f in report.findings}
        assert "R003_DYNAMIC_CODE_EXECUTION" in rule_ids


class TestBase64PipeEvasion:
    """Verify base64 pipeline execution patterns are flagged."""

    def test_base64_decode_sh(self):
        """echo ... | base64 -d | sh → detected as shell pipeline"""
        scanner = SafetyScanner(PolicyConfig.default())
        report = scanner.scan(ScanRequest(
            script="echo d2hvYW1p | base64 -d | sh",
            language=ScriptLanguage.BASH,
            tool_name="test",
        ))
        rule_ids = {f.rule_id for f in report.findings}
        assert "R003_SHELL_PIPE_EXECUTION" in rule_ids, (
            f"Expected R003_SHELL_PIPE_EXECUTION, got {rule_ids}")


class TestSensitivePathInArgs:
    """Verify sensitive paths accessed via variable arguments are flagged."""

    def test_dynamic_path_in_string(self):
        """open('/home/user/.env') → flagged"""
        scanner = SafetyScanner(PolicyConfig.default())
        report = scanner.scan(ScanRequest(
            script="open('.env')",
            language=ScriptLanguage.PYTHON,
            tool_name="test",
        ))
        rule_ids = {f.rule_id for f in report.findings}
        assert "R001_CREDENTIAL_FILE_ACCESS" in rule_ids or "R001_FILE_DANGEROUS_OPEN" in rule_ids
