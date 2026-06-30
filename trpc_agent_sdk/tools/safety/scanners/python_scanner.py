# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Python payload scanner.

Primary strategy is an AST walk (precise, no false hits from comments/strings);
on a ``SyntaxError`` it degrades to the shared regex text scan. The regex text
scan also runs on syntactically valid code to catch dangerous patterns embedded
in string literals (e.g. ``os.system("rm -rf /")`` or a hard-coded key).
"""

from __future__ import annotations

import ast
from typing import Optional

from ..models import RiskFinding
from ..models import ScanInput
from ..policy import SafetyPolicy
from ..rules import make_finding
from .base import ScannerABC
from .base import dedupe_findings
from .patterns import RE_SECRET_NAME
from .patterns import iter_forbidden_path_findings
from .patterns import iter_text_findings

# Callables that spawn processes via a list of args (no shell by default).
_SUBPROCESS_FUNCS = {
    "subprocess.run", "subprocess.Popen", "subprocess.call",
    "subprocess.check_call", "subprocess.check_output",
}
# Callables that always go through a shell.
_SHELL_FUNCS = {"os.system", "os.popen", "subprocess.getoutput", "subprocess.getstatusoutput"}
_EVAL_FUNCS = {"eval", "exec", "compile", "os.execv", "os.execve"}
_RECURSIVE_DELETE = {"shutil.rmtree"}
# Output sinks that could leak secrets.
_OUTPUT_ATTRS = {"write", "writelines", "info", "debug", "warning",
                 "error", "exception", "critical", "log"}
_LARGE_SLEEP_SECONDS = 3600


def _func_name(node: ast.AST) -> str:
    """Best-effort dotted name for a call target (e.g. ``subprocess.run``)."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _func_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _is_dynamic_string(node: Optional[ast.AST]) -> bool:
    """True if ``node`` is a dynamically built string (injection risk)."""
    if node is None:
        return False
    if isinstance(node, ast.JoinedStr):  # f-string
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        return True
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if node.func.attr in ("format", "join"):
            return True
    if isinstance(node, ast.Name):  # a variable, contents unknown -> treat as dynamic
        return True
    return False


def _is_constant_str(node: Optional[ast.AST]) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _shell_true(call: ast.Call) -> bool:
    for kw in call.keywords:
        if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
            return True
    return False


def _names_in(node: ast.AST):
    """Yield identifier names referenced inside ``node`` (incl. f-string parts)."""
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            yield child.id


def _loop_has_break(node: ast.AST) -> bool:
    """True if a ``break`` belongs to this loop (ignores breaks in nested loops)."""
    for child in node.body:  # type: ignore[attr-defined]
        for sub in ast.walk(child):
            if isinstance(sub, (ast.For, ast.While)):
                # breaks inside a nested loop bind to that loop; skip its subtree.
                continue
            if isinstance(sub, ast.Break):
                return True
    return False


class PythonScanner(ScannerABC):
    """AST-based scanner for Python payloads."""

    language = "python"

    def scan(self, scan_input: ScanInput, policy: SafetyPolicy) -> list[RiskFinding]:
        source = scan_input.script or ""
        lines = source.splitlines()
        findings: list[RiskFinding] = []

        def line_text(lineno: int) -> str:
            if 1 <= lineno <= len(lines):
                return lines[lineno - 1].strip()
            return source.strip()[:200]

        try:
            tree = ast.parse(source)
        except SyntaxError:
            # Degrade to text scan only.
            for rule_id, snippet, lineno in iter_text_findings(source, policy):
                findings.append(make_finding(rule_id, snippet, lineno))
            for rule_id, snippet, lineno in iter_forbidden_path_findings(source, policy):
                findings.append(make_finding(rule_id, snippet, lineno))
            return dedupe_findings(findings)

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                findings.extend(self._scan_call(node, line_text))
            elif isinstance(node, ast.While):
                if isinstance(node.test, ast.Constant) and node.test.value is True and not _loop_has_break(node):
                    findings.append(make_finding("RES_INFINITE_LOOP", line_text(node.lineno), node.lineno))

        # Always add regex text findings (string-embedded commands, secrets, URLs).
        for rule_id, snippet, lineno in iter_text_findings(source, policy):
            findings.append(make_finding(rule_id, snippet, lineno))

        # Policy-driven forbidden-path access (config-driven, no code change).
        for rule_id, snippet, lineno in iter_forbidden_path_findings(source, policy):
            findings.append(make_finding(rule_id, snippet, lineno))

        return dedupe_findings(findings)

    # ------------------------------------------------------------------ #
    def _scan_call(self, call: ast.Call, line_text) -> list[RiskFinding]:
        out: list[RiskFinding] = []
        name = _func_name(call.func)
        lineno = call.lineno
        first_arg = call.args[0] if call.args else None

        if name in _RECURSIVE_DELETE:
            out.append(make_finding("FILE_RM_RF", line_text(lineno), lineno))
        elif name in _EVAL_FUNCS:
            out.append(make_finding("EXEC_EVAL", line_text(lineno), lineno))
        elif name in _SHELL_FUNCS:
            if _is_dynamic_string(first_arg):
                out.append(make_finding("EXEC_SHELL_INJECTION", line_text(lineno), lineno))
            else:
                out.append(make_finding("EXEC_OS_SYSTEM", line_text(lineno), lineno))
        elif name in _SUBPROCESS_FUNCS:
            if _shell_true(call):
                if _is_dynamic_string(first_arg):
                    out.append(make_finding("EXEC_SHELL_INJECTION", line_text(lineno), lineno))
                else:
                    out.append(make_finding("EXEC_OS_SYSTEM", line_text(lineno), lineno))
            else:
                out.append(make_finding("EXEC_SUBPROCESS", line_text(lineno), lineno))
        elif name == "os.fork":
            out.append(make_finding("RES_FORK_BOMB", line_text(lineno), lineno))
        elif name == "time.sleep" and self._is_large_sleep(first_arg):
            out.append(make_finding("RES_LARGE_SLEEP", line_text(lineno), lineno))

        # Secret leakage through an output sink (print / logger / file.write).
        if self._is_output_sink(call.func) and self._args_reference_secret(call):
            out.append(make_finding("SECRET_LEAK_OUTPUT", line_text(lineno), lineno))

        return out

    @staticmethod
    def _is_large_sleep(node: Optional[ast.AST]) -> bool:
        return (isinstance(node, ast.Constant) and isinstance(node.value, (int, float))
                and node.value >= _LARGE_SLEEP_SECONDS)

    @staticmethod
    def _is_output_sink(func: ast.AST) -> bool:
        if isinstance(func, ast.Name) and func.id == "print":
            return True
        if isinstance(func, ast.Attribute) and func.attr in _OUTPUT_ATTRS:
            return True
        return False

    @staticmethod
    def _args_reference_secret(call: ast.Call) -> bool:
        for arg in call.args:
            for ident in _names_in(arg):
                if RE_SECRET_NAME.match(ident):
                    return True
        return False
