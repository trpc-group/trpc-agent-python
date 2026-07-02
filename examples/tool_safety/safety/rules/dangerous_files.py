# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
"""Rule: dangerous file operations.

Flags recursive deletion, overwriting system directories, accessing ``~/.ssh``,
reading ``.env`` / credential files, and policy-configured forbidden paths.
"""
from __future__ import annotations

import ast
import re

from .base import SafetyRule
from .base import bash_lines
from .base import evidence_snippet
from .base import get_string_literal
from .base import iter_python_calls
from .base import normalize_language
from .base import parse_python_ast
from ..policy import PolicyConfig
from ..types import RiskLevel
from ..types import SafetyFinding
from ..types import ScanInput


# Path substrings that indicate sensitive targets.
_SENSITIVE_PATH_PATTERNS = [
    (r"\.ssh\b", "~/.ssh / SSH keys"),
    (r"\.env\b", ".env file (often contains secrets)"),
    (r"\.aws/credentials\b", "AWS credentials file"),
    (r"\.netrc\b", ".netrc credentials file"),
    (r"\.npmrc\b", ".npmrc (may contain tokens)"),
    (r"\.pypirc\b", ".pypirc (may contain tokens)"),
    (r"\.gnupg\b", "GPG keyring"),
    (r"/etc/shadow\b", "system shadow password file"),
    (r"/etc/passwd\b", "system passwd file"),
    (r"id_rsa\b", "private SSH key"),
    (r"id_ed25519\b", "private SSH key"),
    (r"\.kube/config\b", "kubeconfig credentials"),
    (r"\.docker/config\.json\b", "docker credentials"),
]

# Recursive / forced delete patterns (bash).
_DELETE_PATTERNS = [
    re.compile(r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f?|-[a-zA-Z]*f[a-zA-Z]*r?)\b", re.IGNORECASE),
    re.compile(r"\brm\s+-rf\b", re.IGNORECASE),
    re.compile(r"\brmdir\s+/s\b", re.IGNORECASE),
    re.compile(r"\bdel\s+/[sq]\b", re.IGNORECASE),
    re.compile(r":\(\)\s*\{.*\};\s*:", re.IGNORECASE),  # fork bomb also deletes sanity
]

# System directories that must never be written/deleted.
_SYSTEM_DIRS = ["/etc", "/usr", "/bin", "/sbin", "/boot", "/sys", "/proc", "/dev", "C:\\Windows", "C:\\Program Files"]


class DangerousFilesRule(SafetyRule):
    """Detect dangerous file operations: recursive delete, system dirs, secrets."""

    rule_id = "R001_dangerous_files"
    rule_name = "Dangerous File Operation"
    risk_type = "dangerous_files"
    default_level = RiskLevel.CRITICAL
    languages = ("python", "bash")

    def check(self, scan_input: ScanInput, policy: PolicyConfig) -> list[SafetyFinding]:
        findings: list[SafetyFinding] = []
        lang = normalize_language(scan_input)
        if lang == "python":
            findings.extend(self._check_python(scan_input, policy))
        else:
            findings.extend(self._check_bash(scan_input, policy))
        return findings

    # ----- python -----

    def _check_python(self, scan_input: ScanInput, policy: PolicyConfig) -> list[SafetyFinding]:
        findings: list[SafetyFinding] = []
        tree = parse_python_ast(scan_input.script)
        if tree is None:
            return findings

        for node, name in iter_python_calls(tree):
            lname = name.lower()
            # shutil.rmtree / os.rmdir / os.remove / os.unlink with -r semantics
            if lname in {"shutil.rmtree", "os.rmdir", "os.remove", "os.unlink", "pathlib.path.unlink"}:
                arg = node.args[0] if node.args else None
                target = get_string_literal(arg) or _ast_str(arg) or "<dynamic>"
                if _is_recursive_delete(name, node):
                    findings.append(self._finding(
                        f"Recursive/forced delete via {name}({target!r})",
                        node.lineno,
                        evidence=f"{name}({target})",
                        rec="Avoid recursive deletion; restrict to known workspace paths.",
                    ))
            # open(..., 'w') / write to sensitive paths
            if lname in {"open", "builtins.open"} and _is_write_open(node):
                target = _first_str_arg(node) or "<dynamic>"
                if _matches_sensitive(target) or _matches_forbidden(target, policy) or _matches_system_dir(target):
                    findings.append(self._finding(
                        f"Write to sensitive path {target!r}",
                        node.lineno,
                        evidence=f"open({target!r}, 'w')",
                        rec="Do not write to system or credential paths.",
                    ))
            # Reading sensitive files
            if lname in {"open", "builtins.open", "pathlib.Path.read_text", "pathlib.path.read_text"} and not _is_write_open(node):
                target = _first_str_arg(node) or "<dynamic>"
                if _matches_sensitive(target):
                    findings.append(self._finding(
                        f"Read sensitive file {target!r}",
                        node.lineno,
                        evidence=f"{name}({target!r})",
                        rec="Do not read credential/secret files in tool scripts.",
                    ))
        return findings

    # ----- bash -----

    def _check_bash(self, scan_input: ScanInput, policy: PolicyConfig) -> list[SafetyFinding]:
        findings: list[SafetyFinding] = []
        for lineno, line in bash_lines(scan_input.script):
            for pat in _DELETE_PATTERNS:
                if pat.search(line):
                    findings.append(self._finding(
                        f"Recursive/forced delete: {evidence_snippet(line)}",
                        lineno,
                        evidence=line,
                        rec="Avoid rm -rf and recursive deletion of unknown paths.",
                    ))
                    break
            # cat/redirect to sensitive paths
            for pat, desc in _SENSITIVE_PATH_PATTERNS:
                if re.search(pat, line):
                    findings.append(self._finding(
                        f"Access to sensitive path ({desc}): {evidence_snippet(line)}",
                        lineno,
                        evidence=line,
                        rec=f"Do not touch {desc} from tool scripts.",
                    ))
                    break
            for sd in _SYSTEM_DIRS:
                if sd in line and (">" in line or "rm " in line or "chmod" in line or "chown" in line):
                    findings.append(self._finding(
                        f"Operation on system directory {sd!r}: {evidence_snippet(line)}",
                        lineno,
                        evidence=line,
                        rec="Never modify or delete system directories.",
                    ))
                    break
            for fb in policy.forbidden_paths:
                if fb in line:
                    findings.append(self._finding(
                        f"Access to forbidden path ({fb!r}): {evidence_snippet(line)}",
                        lineno,
                        evidence=line,
                        rec=f"Path {fb!r} is forbidden by policy.",
                    ))
                    break
        return findings

    def _finding(self, msg: str, line: int | None, evidence: str, rec: str) -> SafetyFinding:
        return SafetyFinding(
            rule_id=self.rule_id,
            rule_name=self.rule_name,
            risk_type=self.risk_type,
            risk_level=self.default_level,
            evidence=evidence_snippet(evidence) if evidence else msg,
            line=line,
            recommendation=rec,
            metadata={"message": msg},
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_recursive_delete(name: str, node: ast.Call) -> bool:
    """True for shutil.rmtree, or os.remove with ignore_errors/recursive kwarg."""
    lname = name.lower()
    if "rmtree" in lname:
        return True
    for kw in node.keywords:
        if kw.arg in {"ignore_errors", "recursive", "force"}:
            val = kw.value
            if isinstance(val, ast.Constant) and val.value:
                return True
    return False


def _is_write_open(node: ast.Call) -> bool:
    """True when open() is called with a write mode ('w','a','x','+').

    Only inspects the *mode* argument: the 2nd positional arg, or the
    ``mode=`` keyword. Checking every arg would misclassify filenames that
    happen to contain 'w'/'a'/'x' (e.g. ``id_rsa``, ``data.txt``).
    """
    mode_val = None
    # mode= keyword wins if present.
    for kw in node.keywords:
        if kw.arg == "mode":
            if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                mode_val = kw.value.value
            break
    # Otherwise 2nd positional arg (after the path).
    if mode_val is None and len(node.args) >= 2:
        arg = node.args[1]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            mode_val = arg.value
    if not mode_val:
        # No explicit mode => default 'r' (read). Not a write.
        return False
    return any(m in mode_val for m in ("w", "a", "x", "+"))


def _first_str_arg(node: ast.Call) -> str | None:
    if not node.args:
        return None
    return get_string_literal(node.args[0])


def _ast_str(node: ast.AST | None) -> str | None:
    if node is None:
        return None
    return get_string_literal(node)


def _matches_sensitive(target: str) -> bool:
    if not target:
        return False
    for pat, _ in _SENSITIVE_PATH_PATTERNS:
        if re.search(pat, target):
            return True
    return False


def _matches_system_dir(target: str) -> bool:
    if not target:
        return False
    return any(sd in target for sd in _SYSTEM_DIRS)


def _matches_forbidden(target: str, policy: PolicyConfig) -> bool:
    if not target:
        return False
    return any(fb in target for fb in policy.forbidden_paths)
