# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Python script safety scanner using AST and regex fallback."""

from __future__ import annotations

import ast
from typing import List

from ._policy import PolicyConfig
from ._rules import (
    PYTHON_DANGEROUS_FILE_CALLS,
    PYTHON_DELETE_CALLS,
    PYTHON_DYNAMIC_EXEC_CALLS,
    PYTHON_INSTALL_PATTERNS,
    PYTHON_NETWORK_CALLS,
    PYTHON_NETWORK_IMPORTS,
    PYTHON_RESOURCE_PATTERNS,
    PYTHON_SYSTEM_CALLS,
    SENSITIVE_PATHS,
    sanitize_text,
)
from ._types import RiskLevel
from ._types import RiskType
from ._types import SafetyFinding


class _PythonVisitor(ast.NodeVisitor):
    """AST visitor that collects safety findings from Python code."""

    def __init__(self, secret_patterns: list[str] | None = None) -> None:
        self.findings: List[SafetyFinding] = []
        self._imported_modules: set[str] = set()
        self._aliases: dict[str, str] = {}  # local_name → fully_qualified_path
        self._secret_patterns = secret_patterns or []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            # Track alias: import os as myos → myos→os
            local_name = alias.asname or alias.name.split(".")[0]
            self._aliases[local_name] = alias.name
            self._imported_modules.add(alias.name)
            if alias.name in PYTHON_NETWORK_IMPORTS:
                self.findings.append(
                    SafetyFinding(
                        rule_id="R002_NETWORK_EGRESS",
                        rule_name="Network Library Import",
                        risk_type=RiskType.NETWORK_EGRESS,
                        risk_level=RiskLevel.MEDIUM,
                        evidence=sanitize_text(f"import {alias.name}", self._secret_patterns),
                        line=node.lineno,
                        recommendation="Review network access. Ensure only whitelisted domains are used.",
                    ))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            self._imported_modules.add(node.module)
            # Track aliases: from os import system → system→os.system
            for alias in node.names:
                if alias.name == "*":
                    continue
                local_name = alias.asname or alias.name
                self._aliases[local_name] = f"{node.module}.{alias.name}"
            if node.module in PYTHON_NETWORK_IMPORTS:
                self.findings.append(
                    SafetyFinding(
                        rule_id="R002_NETWORK_EGRESS",
                        rule_name="Network Library Import",
                        risk_type=RiskType.NETWORK_EGRESS,
                        risk_level=RiskLevel.MEDIUM,
                        evidence=sanitize_text(f"from {node.module} import ...", self._secret_patterns),
                        line=node.lineno,
                        recommendation="Review network access. Ensure only whitelisted domains are used.",
                    ))
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func_path = self._resolve_call_path(node.func)

        # Check getattr evasion before other call checks
        if func_path and (func_path == "getattr" or func_path.endswith(".getattr")):
            self._check_getattr_evasion(node)

        if func_path:
            self._check_system_calls(func_path, node)
            self._check_dangerous_file_calls(func_path, node)
            self._check_network_calls(func_path, node)
            self._check_dynamic_exec(func_path, node)
            self._check_shell_true(node)

        # Check string arguments for sensitive paths
        for arg in node.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                self._check_sensitive_path(arg.value, node.lineno)

        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str):
            self._check_sensitive_path(node.value, node.lineno)
        self.generic_visit(node)

    def visit_While(self, node: ast.While) -> None:
        if isinstance(node.test, ast.Constant) and node.test.value is True:
            self.findings.append(
                SafetyFinding(
                    rule_id="R005_INFINITE_LOOP",
                    rule_name="Infinite Loop",
                    risk_type=RiskType.RESOURCE_ABUSE,
                    risk_level=RiskLevel.MEDIUM,
                    evidence=sanitize_text("while True:", self._secret_patterns),
                    line=node.lineno,
                    recommendation="Avoid infinite loops. Use bounded loops with clear exit conditions.",
                ))
        self.generic_visit(node)

    @staticmethod
    def _raw_dotted_name(node: ast.expr) -> str:
        """Convert an attribute chain to a dotted string, e.g. os.path.join."""
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            base = _PythonVisitor._raw_dotted_name(node.value)
            return f"{base}.{node.attr}" if base else f"<expr>.{node.attr}"
        return "<expr>"

    def _resolve_call_path(self, node: ast.expr) -> str:
        """Resolve to a fully-qualified call path, expanding import aliases.

        Examples:
            os.system → os.system
            from os import system; system → os.system
            import os as myos; myos.system → os.system
        """
        raw = self._raw_dotted_name(node)
        if not raw or raw == "<expr>":
            return raw
        parts = raw.split(".")
        head = parts[0]
        if head in self._aliases:
            resolved_head = self._aliases[head]
            if len(parts) == 1:
                return resolved_head
            return resolved_head + "." + ".".join(parts[1:])
        return raw

    def _check_system_calls(self, func_path: str, node: ast.Call) -> None:
        if func_path in PYTHON_SYSTEM_CALLS:
            rule_id = PYTHON_SYSTEM_CALLS[func_path]
            self.findings.append(
                SafetyFinding(
                    rule_id=rule_id,
                    rule_name="System Command Execution",
                    risk_type=RiskType.SYSTEM_COMMAND,
                    risk_level=RiskLevel.HIGH,
                    evidence=sanitize_text(f"{func_path}(...)", self._secret_patterns),
                    line=node.lineno,
                    recommendation="Avoid executing system commands. Use safe library APIs instead.",
                ))

    def _check_dangerous_file_calls(self, func_path: str, node: ast.Call) -> None:
        if func_path in PYTHON_DANGEROUS_FILE_CALLS:
            info = PYTHON_DANGEROUS_FILE_CALLS[func_path]
            self.findings.append(
                SafetyFinding(
                    rule_id=info["rule_id"],
                    rule_name="Dangerous File Operation",
                    risk_type=RiskType.DANGEROUS_FILE_OPERATION,
                    risk_level=RiskLevel(info["risk"]),
                    evidence=sanitize_text(f"{func_path}(...)", self._secret_patterns),
                    line=node.lineno,
                    recommendation="Review file operation. Avoid accessing sensitive paths.",
                ))
        if func_path in PYTHON_DELETE_CALLS:
            self.findings.append(
                SafetyFinding(
                    rule_id=PYTHON_DELETE_CALLS[func_path],
                    rule_name="File Deletion",
                    risk_type=RiskType.DANGEROUS_FILE_OPERATION,
                    risk_level=RiskLevel.HIGH,
                    evidence=sanitize_text(f"{func_path}(...)", self._secret_patterns),
                    line=node.lineno,
                    recommendation="Review file deletion. Ensure target paths are safe.",
                ))

    def _check_network_calls(self, func_path: str, node: ast.Call) -> None:
        if func_path in PYTHON_NETWORK_CALLS:
            rule_id = PYTHON_NETWORK_CALLS[func_path]
            self.findings.append(
                SafetyFinding(
                    rule_id=rule_id,
                    rule_name="Network Request",
                    risk_type=RiskType.NETWORK_EGRESS,
                    risk_level=RiskLevel.HIGH,
                    evidence=sanitize_text(f"{func_path}(...)", self._secret_patterns),
                    line=node.lineno,
                    recommendation="Review network request. Ensure only whitelisted domains are used.",
                ))

    def _check_dynamic_exec(self, func_path: str, node: ast.Call) -> None:
        if func_path in PYTHON_DYNAMIC_EXEC_CALLS:
            rule_id = PYTHON_DYNAMIC_EXEC_CALLS[func_path]
            self.findings.append(
                SafetyFinding(
                    rule_id=rule_id,
                    rule_name="Dynamic Code Execution",
                    risk_type=RiskType.SYSTEM_COMMAND,
                    risk_level=RiskLevel.HIGH,
                    evidence=sanitize_text(f"{func_path}(...)", self._secret_patterns),
                    line=node.lineno,
                    recommendation="Avoid dynamic code execution. Use safe alternatives.",
                ))

    def _check_shell_true(self, node: ast.Call) -> None:
        for kw in node.keywords:
            if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                self.findings.append(
                    SafetyFinding(
                        rule_id="R003_SHELL_PIPE_EXECUTION",
                        rule_name="Shell=True Execution",
                        risk_type=RiskType.SYSTEM_COMMAND,
                        risk_level=RiskLevel.HIGH,
                        evidence=sanitize_text("shell=True", self._secret_patterns),
                        line=node.lineno,
                        recommendation="Avoid shell=True. Pass arguments as a list instead.",
                    ))

    def _check_sensitive_path(self, text: str, lineno: int) -> None:
        for sensitive in SENSITIVE_PATHS:
            if sensitive in text:
                self.findings.append(
                    SafetyFinding(
                        rule_id="R001_CREDENTIAL_FILE_ACCESS",
                        rule_name="Sensitive Path Access",
                        risk_type=RiskType.DANGEROUS_FILE_OPERATION,
                        risk_level=RiskLevel.HIGH,
                        evidence=sanitize_text(text, self._secret_patterns),
                        line=lineno,
                        recommendation="Avoid accessing sensitive file paths.",
                    ))
                return

    def _check_getattr_evasion(self, node: ast.Call) -> None:
        """Detect getattr(__builtins__, 'eval') style dynamic code execution."""
        if len(node.args) < 2:
            return
        attr_arg = node.args[1]
        targets: list[str] = []
        if isinstance(attr_arg, ast.Constant) and isinstance(attr_arg.value, str):
            targets = [attr_arg.value]
        elif isinstance(attr_arg, ast.BinOp) and isinstance(attr_arg.op, ast.Add):
            # getattr(..., 'ev'+'al') concatenation evasion
            left_val = getattr(attr_arg.left, 'value', None)
            right_val = getattr(attr_arg.right, 'value', None)
            if left_val is not None and right_val is not None:
                targets = [f"{left_val}{right_val}"]
            else:
                # Variable concatenation — can't resolve statically, flag for review
                self.findings.append(
                    SafetyFinding(
                        rule_id="R003_DYNAMIC_CODE_EXECUTION",
                        rule_name="Dynamic Code Execution via getattr (variable args)",
                        risk_type=RiskType.SYSTEM_COMMAND,
                        risk_level=RiskLevel.MEDIUM,
                        evidence=sanitize_text("getattr(..., <expr>)", self._secret_patterns),
                        line=node.lineno,
                        recommendation="getattr with non-constant arguments requires human review.",
                    ))
                return
        for t in targets:
            if t in ('eval', 'exec', 'system', 'popen'):
                self.findings.append(
                    SafetyFinding(
                        rule_id="R003_DYNAMIC_CODE_EXECUTION",
                        rule_name="Dynamic Code Execution via getattr",
                        risk_type=RiskType.SYSTEM_COMMAND,
                        risk_level=RiskLevel.HIGH,
                        evidence=sanitize_text(f"getattr(..., '{t}')", self._secret_patterns),
                        line=node.lineno,
                        recommendation="Avoid dynamic attribute access to builtins.",
                    ))
                return


class PythonParser:
    """Safety scanner for Python scripts using AST with regex fallback."""

    def __init__(self, policy: PolicyConfig) -> None:
        self._policy = policy

    def parse(self, script: str) -> List[SafetyFinding]:
        """Scan a Python script and return safety findings."""
        try:
            return self._ast_scan(script)
        except SyntaxError:
            return self._regex_fallback(script)

    def _ast_scan(self, script: str) -> List[SafetyFinding]:
        visitor = _PythonVisitor(self._policy.secret_patterns)
        tree = ast.parse(script)
        visitor.visit(tree)

        # Also run text-based checks (dependency install, resource patterns)
        self._scan_text_patterns(script, visitor.findings)

        return visitor.findings

    def _regex_fallback(self, script: str) -> List[SafetyFinding]:
        findings: List[SafetyFinding] = []
        self._scan_text_patterns(script, findings)

        # Mark all findings as needs_human_review due to parse failure
        for f in findings:
            f.risk_level = RiskLevel.MEDIUM
            f.metadata["parse_failed"] = True
            f.recommendation = "AST parsing failed — results are from regex heuristics. Manual review required."

        # Also add a top-level finding about the parse failure
        findings.append(
            SafetyFinding(
                rule_id="R003_SHELL_PIPE_EXECUTION",
                rule_name="Parse Failure",
                risk_type=RiskType.SYSTEM_COMMAND,
                risk_level=RiskLevel.MEDIUM,
                evidence=sanitize_text(script[:200], self._policy.secret_patterns),
                recommendation="Python script could not be parsed as AST. Manual review required.",
                metadata={"parse_failed": True},
            ))
        return findings

    def _scan_text_patterns(self, script: str, findings: List[SafetyFinding]) -> None:
        # Dependency install patterns (gated by review_package_install)
        if self._policy.review_package_install:
            for pattern, rule_id in PYTHON_INSTALL_PATTERNS:
                for match in pattern.finditer(script):
                    findings.append(
                        SafetyFinding(
                            rule_id=rule_id,
                            rule_name="Dependency Installation",
                            risk_type=RiskType.DEPENDENCY_INSTALL,
                            risk_level=RiskLevel.MEDIUM,
                            evidence=sanitize_text(match.group(), self._policy.secret_patterns),
                            recommendation="Dependency installation modifies the runtime environment. Review required.",
                        ))

        # Resource abuse patterns
        for pattern, rule_id, risk in PYTHON_RESOURCE_PATTERNS:
            for match in pattern.finditer(script):
                level = RiskLevel(risk)
                if rule_id == "R005_LONG_RUNNING_SLEEP":
                    try:
                        sleep_sec = int(match.group(1))
                        if sleep_sec <= self._policy.max_timeout_seconds:
                            continue
                    except ValueError:
                        pass
                # Gate large file write on max_file_write_bytes threshold
                if rule_id == "R005_LARGE_FILE_WRITE":
                    try:
                        write_bytes = int(match.group(1))
                        if write_bytes <= self._policy.max_file_write_bytes:
                            continue
                    except (ValueError, IndexError):
                        pass
                findings.append(
                    SafetyFinding(
                        rule_id=rule_id,
                        rule_name="Resource Abuse",
                        risk_type=RiskType.RESOURCE_ABUSE,
                        risk_level=level,
                        evidence=sanitize_text(match.group(), self._policy.secret_patterns),
                        recommendation="Review resource usage pattern.",
                    ))
