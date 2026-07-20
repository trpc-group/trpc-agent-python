# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Python script scanner for the Tool Script Safety Guard system.

This module provides the PythonScanner class which performs static analysis
on Python scripts using the ``ast`` module. It detects dangerous patterns
by walking the AST and matching against configured risk rules.
"""

from __future__ import annotations

import ast
import re
from typing import Optional

from trpc_agent_sdk.log import logger

from ._policy import SafetyPolicy
from ._types import RiskCategory
from ._types import RiskLevel
from ._types import RuleMatch
from ._types import ScanInput


class _DangerVisitor(ast.NodeVisitor):
    """AST visitor that collects dangerous patterns in Python code."""

    def __init__(self, policy: SafetyPolicy) -> None:
        super().__init__()
        self._policy = policy
        self.matches: list[RuleMatch] = []
        self._current_line = 0

    # ── Call visitor ──────────────────────────────────────────────────

    def visit_Call(self, node: ast.Call) -> None:
        """Visit a function call node."""
        func_name = self._get_call_name(node)

        # R004: Process execution
        if func_name in (
                "os.system",
                "os.popen",
                "subprocess.run",
                "subprocess.Popen",
                "subprocess.call",
                "subprocess.check_call",
                "subprocess.check_output",
                "pty.spawn",
                "os.execl",
                "os.execle",
                "os.execlp",
                "os.execv",
                "os.execve",
                "os.execvp",
                "os.fork",
                "os.spawnl",
                "os.spawnle",
                "os.spawnlp",
                "os.spawnv",
                "os.spawnve",
                "os.spawnvp",
        ):
            if self._is_rule_enabled("process_execution"):
                self._add_match("R004", RiskCategory.PROCESS_EXECUTION, func_name, node)

        # R001: Dangerous file operations via os / shutil
        if func_name in ("os.remove", "os.unlink", "shutil.rmtree", "os.rmdir", "os.removedirs"):
            if self._is_rule_enabled("dangerous_file_operations"):
                self._add_match("R001", RiskCategory.DANGEROUS_FILE_OPERATION, func_name, node)

        # R001: os.system/subprocess with dangerous args
        if func_name in ("os.system", "subprocess.run", "subprocess.Popen", "subprocess.call", "subprocess.check_call"):
            dangerous_args = self._check_dangerous_args(node)
            for arg in dangerous_args:
                if self._is_rule_enabled("dangerous_file_operations"):
                    self._add_match("R001", RiskCategory.DANGEROUS_FILE_OPERATION, f"{func_name}({arg})", node)

        # R002: Sensitive file reads
        if func_name in ("open", "pathlib.Path", "pathlib.Path.read_text", "pathlib.Path.read_bytes", "io.open",
                         "builtins.open"):
            path_arg = self._get_string_arg(node, 0)
            if path_arg and self._is_path_sensitive(path_arg):
                if self._is_rule_enabled("sensitive_file_read"):
                    self._add_match("R002", RiskCategory.DANGEROUS_FILE_OPERATION, f"Read sensitive file: {path_arg}",
                                    node)

        # R003: Network egress
        if func_name in (
                "requests.get",
                "requests.post",
                "requests.put",
                "requests.delete",
                "requests.patch",
                "requests.request",
                "urllib.request.urlopen",
                "urllib.request.Request",
                "urllib.urlopen",
                "aiohttp.ClientSession.get",
                "aiohttp.ClientSession.post",
                "aiohttp.ClientSession.request",
                "httpx.get",
                "httpx.post",
                "httpx.put",
                "httpx.delete",
                "httpx.request",
                "aiohttp.ClientSession",
        ):
            url_arg = self._get_string_arg(node, 0)
            if url_arg and not self._is_domain_allowed_in_url(url_arg):
                if self._is_rule_enabled("network_egress"):
                    self._add_match("R003", RiskCategory.NETWORK_EGRESS, f"Network request to: {url_arg}", node)

        if func_name in ("socket.connect", "socket.create_connection", "socket.socket.connect"):
            if self._is_rule_enabled("network_egress"):
                self._add_match("R003", RiskCategory.NETWORK_EGRESS, "socket connection", node)

        # R005: Dependency installation
        if func_name in ("subprocess.run", "subprocess.Popen", "subprocess.call", "subprocess.check_call", "os.system",
                         "os.popen"):
            cmd = self._get_command_arg(node)
            if cmd and self._is_install_command(cmd):
                if self._is_rule_enabled("dependency_installation"):
                    self._add_match("R005", RiskCategory.DEPENDENCY_INSTALLATION, f"Install command: {cmd}", node)

        # R006: Resource abuse
        if func_name in ("os.fork", ):
            if self._is_rule_enabled("resource_abuse"):
                self._add_match("R006", RiskCategory.RESOURCE_ABUSE, func_name, node)

        if func_name in ("time.sleep", "asyncio.sleep"):
            sleep_time = self._get_numeric_arg(node, 0)
            if sleep_time is not None and sleep_time >= 3600:
                if self._is_rule_enabled("resource_abuse"):
                    self._add_match("R006", RiskCategory.RESOURCE_ABUSE, f"Long sleep: {sleep_time}s", node)

        self.generic_visit(node)

    # ── Other visitors ────────────────────────────────────────────────

    def visit_While(self, node: ast.While) -> None:
        """Visit a while loop - check for infinite loops."""
        if isinstance(node.test, ast.Constant) and node.test.value is True:
            if self._is_rule_enabled("resource_abuse"):
                self._add_match("R006", RiskCategory.RESOURCE_ABUSE, "while True: (potential infinite loop)", node)
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        """Visit a for loop - check for large ranges."""
        if isinstance(node.iter, ast.Call):
            call = node.iter
            if isinstance(call.func, ast.Name) and call.func.id == "range":
                if len(call.args) >= 1:
                    arg = call.args[0]
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, int) and arg.value > 1000000:
                        if self._is_rule_enabled("resource_abuse"):
                            self._add_match("R006", RiskCategory.RESOURCE_ABUSE, f"Large range({arg.value})", node)
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        """Visit an assignment - check for sensitive info leak."""
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            val = node.value.value
            if self._is_sensitive_value(val) and self._is_rule_enabled("sensitive_info_leak"):
                targets = [self._get_target_name(t) for t in node.targets if t]
                target_str = ", ".join(t for t in targets if t)
                if target_str:
                    self._add_match("R007",
                                    RiskCategory.SENSITIVE_INFO_LEAK,
                                    f"Sensitive value assigned to: {target_str}",
                                    node,
                                    masked=True)
        self.generic_visit(node)

    # ── Helpers ──────────────────────────────────────────────────────

    def _get_call_name(self, node: ast.Call) -> str:
        if isinstance(node.func, ast.Attribute):
            obj_name = self._get_call_name_attr(node.func.value)
            return f"{obj_name}.{node.func.attr}" if obj_name else node.func.attr
        if isinstance(node.func, ast.Name):
            return node.func.id
        return ""

    @staticmethod
    def _get_call_name_attr(node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            inner = _DangerVisitor._get_call_name_attr(node.value)
            return f"{inner}.{node.attr}" if inner else node.attr
        return ""

    @staticmethod
    def _get_target_name(node: ast.AST) -> Optional[str]:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            inner = _DangerVisitor._get_target_name(node.value)
            return f"{inner}.{node.attr}" if inner else node.attr
        if isinstance(node, ast.Subscript):
            return _DangerVisitor._get_target_name(node.value)
        return None

    @staticmethod
    def _get_string_arg(node: ast.Call, index: int) -> Optional[str]:
        if index < len(node.args):
            arg = node.args[index]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                return arg.value
        return None

    @staticmethod
    def _get_numeric_arg(node: ast.Call, index: int) -> Optional[float]:
        if index < len(node.args):
            arg = node.args[index]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, (int, float)):
                return float(arg.value)
        return None

    def _get_command_arg(self, node: ast.Call) -> Optional[str]:
        cmd = self._get_string_arg(node, 0)
        if cmd:
            return cmd
        if len(node.args) > 0:
            first_arg = node.args[0]
            if isinstance(first_arg, ast.List) and first_arg.elts:
                # Reconstruct the full command from list elements
                parts = []
                for elt in first_arg.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        parts.append(elt.value)
                if parts:
                    return " ".join(parts)
        for kw in node.keywords:
            if kw.arg in ("args", "cmd", "command"):
                if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                    return kw.value.value
        return None

    def _is_path_sensitive(self, path: str) -> bool:
        path_expanded = path.replace("~", "")
        return self._policy.is_path_forbidden(path_expanded) or self._policy.is_path_forbidden(path)

    def _is_domain_allowed_in_url(self, url: str) -> bool:
        match = re.search(
            r"(?:https?://|ftp://)?([a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
            r"(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)+)",
            url,
        )
        if match:
            return self._policy.is_domain_allowed(match.group(1))
        return True

    @staticmethod
    def _is_install_command(cmd: str) -> bool:
        install_patterns = [
            r"pip\s+install",
            r"pip3\s+install",
            r"npm\s+install",
            r"npm\s+ci",
            r"apt\s+install",
            r"apt-get\s+install",
            r"brew\s+install",
            r"gem\s+install",
            r"cargo\s+install",
        ]
        return any(re.search(p, cmd, re.IGNORECASE) for p in install_patterns)

    def _check_dangerous_args(self, node: ast.Call) -> list[str]:
        cmd = self._get_command_arg(node)
        if not cmd:
            return []
        dangerous = []
        cfg = self._policy.rules.get("dangerous_file_operations")
        if cfg:
            for pattern in cfg.patterns:
                if re.search(pattern, cmd, re.IGNORECASE):
                    dangerous.append(cmd)
                    break
        return dangerous

    @staticmethod
    def _is_sensitive_value(value: str) -> bool:
        sensitive_patterns = [
            r"sk-[A-Za-z0-9]{20,}",
            r"sk-ant-[A-Za-z0-9]{20,}",
            r"ghp_[A-Za-z0-9]{36,}",
            r"gho_[A-Za-z0-9]{36,}",
            r"xox[baprs]-[A-Za-z0-9-]{10,}",
            r"AKIA[A-Z0-9]{16}",
            r"-----BEGIN\s+(RSA|EC|DSA|OPENSSH)\s+PRIVATE\s+KEY-----",
        ]
        return any(re.match(p, value) for p in sensitive_patterns)

    def _is_rule_enabled(self, rule_name: str) -> bool:
        rule = self._policy.rules.get(rule_name)
        return rule.enabled if rule else False

    def _add_match(self,
                   rule_id: str,
                   category: RiskCategory,
                   evidence: str,
                   node: ast.AST,
                   masked: bool = False) -> None:
        line_no = getattr(node, "lineno", 0)
        for existing in self.matches:
            if existing.rule_id == rule_id and existing.line_number == line_no:
                return
        self.matches.append(
            RuleMatch(
                rule_id=rule_id,
                risk_category=category,
                risk_level=self._get_rule_risk_level(rule_id),
                evidence=evidence[:200],
                line_number=line_no,
                recommendation=self._get_recommendation(rule_id),
                masked=masked,
            ))

    def _get_rule_risk_level(self, rule_id: str) -> RiskLevel:
        rule_name_map = {
            "R001": "dangerous_file_operations",
            "R002": "sensitive_file_read",
            "R003": "network_egress",
            "R004": "process_execution",
            "R005": "dependency_installation",
            "R006": "resource_abuse",
            "R007": "sensitive_info_leak",
        }
        rule_name = rule_name_map.get(rule_id, "")
        rule = self._policy.rules.get(rule_name)
        return rule.risk_level if rule else RiskLevel.HIGH

    @staticmethod
    def _get_recommendation(rule_id: str) -> str:
        recs = {
            "R001": "Remove or replace this destructive file operation.",
            "R002": "Do not read sensitive files. Use environment variables instead.",
            "R003": "Remove or whitelist this network request.",
            "R004": "Avoid executing system commands directly.",
            "R005": "Pre-install all dependencies; do not install during execution.",
            "R006": "Avoid infinite loops, long sleeps, or resource-exhaustive patterns.",
            "R007": "Do not hardcode secrets. Use environment variables or a secrets manager.",
        }
        return recs.get(rule_id, "Review this code for potential security risks.")


class PythonScanner:
    """Scanner for Python scripts using AST-based static analysis.

    Parses the script into an AST and walks the tree to detect
    dangerous patterns defined in the safety policy.
    """

    def __init__(self, policy: SafetyPolicy) -> None:
        self._policy = policy

    def scan(self, scan_input: ScanInput) -> list[RuleMatch]:
        """Scan a Python script for security risks using AST analysis.

        Args:
            scan_input: The script content and context to scan.

        Returns:
            A list of RuleMatch objects for each detected risk.
        """
        try:
            tree = ast.parse(scan_input.script_content)
        except SyntaxError as e:
            logger.warning("PythonScanner: Syntax error in script: %s", e)
            return self._text_scan(scan_input)

        visitor = _DangerVisitor(self._policy)
        visitor.visit(tree)
        return visitor.matches

    def _text_scan(self, scan_input: ScanInput) -> list[RuleMatch]:
        """Fallback text-based scan for scripts that fail AST parsing."""
        from ._bash_scanner import BashScanner
        bash_scanner = BashScanner(self._policy)
        return bash_scanner.scan(scan_input)
