# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Deterministic Python and Bash safety scanner."""

from __future__ import annotations

import ast
import hashlib
import os
from pathlib import Path
import re
import shlex
import time
from typing import Iterable
from urllib.parse import urlparse

from ._models import RiskLevel
from ._models import SafetyDecision
from ._models import SafetyFinding
from ._models import SafetyReport
from ._models import SafetyScanRequest
from ._models import ScriptLanguage
from ._policy import SafetyPolicy
from ._rules import make_finding

_RISK_ORDER = {
    RiskLevel.NONE: 0,
    RiskLevel.LOW: 1,
    RiskLevel.MEDIUM: 2,
    RiskLevel.HIGH: 3,
    RiskLevel.CRITICAL: 4,
}
_ACTION_ORDER = {
    SafetyDecision.ALLOW: 0,
    SafetyDecision.NEEDS_HUMAN_REVIEW: 1,
    SafetyDecision.DENY: 2,
}
_SENSITIVE_NAME_RE = re.compile(
    r"(?:^|_)(?:api_?key|token|secret|password|passwd|credential|private_?key)(?:$|_)",
    re.IGNORECASE,
)
_ASSIGNMENT_RE = re.compile(
    r"(?i)\b([A-Za-z_][A-Za-z0-9_]*(?:key|token|secret|password|credential)[A-Za-z0-9_]*)"
    r"\s*=\s*([\"'])(.*?)\2", )
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_API_TOKEN_RE = re.compile(r"\b(?:sk|pk)-[A-Za-z0-9_-]{16,}\b")
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [^-]*PRIVATE KEY-----.*?(?:-----END [^-]*PRIVATE KEY-----|$)",
    re.DOTALL,
)
_LONG_RANDOM_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9_+/=-]{32,}(?![A-Za-z0-9])")
_SYSTEM_PATHS = (
    Path("/bin"),
    Path("/boot"),
    Path("/dev"),
    Path("/etc"),
    Path("/lib"),
    Path("/Library"),
    Path("/private/etc"),
    Path("/sbin"),
    Path("/System"),
    Path("/usr"),
    Path("/var"),
)
_NETWORK_FUNCTIONS = {
    "requests.get",
    "requests.post",
    "requests.put",
    "requests.patch",
    "requests.delete",
    "requests.request",
    "httpx.get",
    "httpx.post",
    "httpx.put",
    "httpx.patch",
    "httpx.delete",
    "httpx.request",
    "aiohttp.request",
    "urllib.request.urlopen",
}
_SUBPROCESS_FUNCTIONS = {
    "subprocess.run",
    "subprocess.call",
    "subprocess.check_call",
    "subprocess.check_output",
    "subprocess.Popen",
}
_DEPENDENCY_COMMANDS = {"pip", "pip3", "npm", "yarn", "pnpm", "apt", "apt-get", "brew"}
_SHELL_SEPARATORS = {";", "&&", "||", "|", "&"}
_SHELL_BUILTINS = {"export", "if", "then", "else", "elif", "fi", "[", "]", "test", "true", "false", ":"}
_SENSITIVE_ARGV_SENTINEL = "__trpc_sensitive_argv__"


def _sanitize_evidence(value: str, limit: int = 120) -> str:
    """Redact common secrets and cap evidence length."""

    evidence = value.replace("\n", " ").replace("\r", " ").strip()
    evidence = _PRIVATE_KEY_RE.sub("[REDACTED_PRIVATE_KEY]", evidence)
    evidence = _ASSIGNMENT_RE.sub(lambda match: f'{match.group(1)}="[REDACTED]"', evidence)
    evidence = _BEARER_RE.sub("Bearer [REDACTED]", evidence)
    evidence = _API_TOKEN_RE.sub("[REDACTED]", evidence)
    evidence = _LONG_RANDOM_RE.sub("[REDACTED]", evidence)
    if len(evidence) > limit:
        return evidence[:limit - 3] + "..."
    return evidence


def _literal_string(node: ast.AST | None) -> str | None:
    """Resolve a small, safe subset of statically-known strings."""

    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _literal_string(node.left)
        right = _literal_string(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def _literal_int(node: ast.AST | None) -> int | None:
    """Resolve a statically-known integer without evaluating code."""

    if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
        return node.value
    return None


def _static_value_size(node: ast.AST | None) -> int | None:
    """Compute literal string/bytes size without allocating repeated data."""

    if isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)):
        return len(node.value.encode("utf-8")) if isinstance(node.value, str) else len(node.value)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _static_value_size(node.left)
        right = _static_value_size(node.right)
        if left is not None and right is not None:
            return left + right
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        left_size = _static_value_size(node.left)
        right_count = _literal_int(node.right)
        if left_size is not None and right_count is not None and right_count >= 0:
            return left_size * right_count
        right_size = _static_value_size(node.right)
        left_count = _literal_int(node.left)
        if right_size is not None and left_count is not None and left_count >= 0:
            return right_size * left_count
    return None


def _looks_like_secret_literal(value: str) -> bool:
    """Recognize high-confidence secret material in a sink argument."""

    normalized = value.strip()
    return bool(
        _PRIVATE_KEY_RE.search(normalized) or _BEARER_RE.search(normalized) or _API_TOKEN_RE.search(normalized)
        or _ASSIGNMENT_RE.search(normalized))


def _call_name(node: ast.AST, aliases: dict[str, str]) -> str:
    """Return a dotted call name with import aliases resolved."""

    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        prefix = _call_name(node.value, aliases)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    if isinstance(node, ast.Call):
        return _call_name(node.func, aliases)
    return ""


def _keyword(call: ast.Call, name: str) -> ast.AST | None:
    for item in call.keywords:
        if item.arg == name:
            return item.value
    return None


def _command_parts(node: ast.AST | None) -> list[str] | None:
    """Resolve a subprocess command expressed as a literal string or list."""

    if isinstance(node, (ast.List, ast.Tuple)):
        result: list[str] = []
        for item in node.elts:
            value = _literal_string(item)
            if value is None:
                return None
            result.append(value)
        return result
    value = _literal_string(node)
    if value is None:
        return None
    try:
        return shlex.split(value, posix=True)
    except ValueError:
        return None


class SafetyScanner:
    """Scan Python source or POSIX/Bash commands before execution."""

    def __init__(self, policy: SafetyPolicy | None = None):
        self.policy = policy or SafetyPolicy()

    @classmethod
    def from_yaml(cls, path: str | Path) -> "SafetyScanner":
        """Create a scanner from a validated YAML policy."""

        return cls(SafetyPolicy.from_yaml(path))

    def scan(self, request: SafetyScanRequest) -> SafetyReport:
        """Scan one request and return a deterministic structured report."""

        started = time.perf_counter()
        digest = hashlib.sha256(request.content.encode("utf-8")).hexdigest()
        findings: list[SafetyFinding] = []

        content_bytes = len(request.content.encode("utf-8"))
        line_count = request.content.count("\n") + (1 if request.content else 0)
        if (content_bytes > self.policy.limits.max_script_size_bytes
                or line_count > self.policy.limits.max_script_lines):
            self._append(
                findings,
                "POLICY-002",
                f"script_size={content_bytes} bytes, lines={line_count}",
            )
            return self._report(findings, started, digest)

        if (request.timeout_seconds is not None and request.timeout_seconds > self.policy.limits.max_timeout_seconds):
            self._append(
                findings,
                "POLICY-001",
                f"timeout_seconds={request.timeout_seconds}",
            )

        if request.language == ScriptLanguage.PYTHON:
            findings.extend(self._scan_python(request))
        else:
            findings.extend(self._scan_bash(request))
        return self._report(findings, started, digest)

    def _scan_python(self, request: SafetyScanRequest) -> list[SafetyFinding]:
        try:
            tree = ast.parse(request.content)
        except (SyntaxError, ValueError, MemoryError, RecursionError) as ex:
            finding = make_finding(
                "PARSE-001",
                self.policy,
                _sanitize_evidence(f"{type(ex).__name__}: {ex}"),
                line_number=getattr(ex, "lineno", None),
                column=getattr(ex, "offset", None),
            )
            return [finding] if finding is not None else []

        aliases = self._import_aliases(tree)
        parent = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        sensitive_names = self._sensitive_assignments(tree, aliases)
        if any(self._argument_contains_secret(value) for value in request.argv):
            sensitive_names.add(_SENSITIVE_ARGV_SENTINEL)
        findings: list[SafetyFinding] = []

        for node in ast.walk(tree):
            if isinstance(node, ast.While) and self._is_true(node.test):
                if any(
                        isinstance(child, ast.Call) and _call_name(child.func, aliases) == "os.fork"
                        for child in ast.walk(node)):
                    self._append_node(findings, "RES-001", node, request.content)
                else:
                    self._append_node(findings, "RES-002", node, request.content)
            if not isinstance(node, ast.Call):
                continue
            call_name = _call_name(node.func, aliases)
            self._check_python_file(findings, node, call_name, request)
            self._check_python_network(findings, node, call_name, request.content)
            self._check_python_process(findings, node, call_name, request.content)
            self._check_python_resource(findings, node, call_name, request.content, parent)
            sink_values = [*node.args, *(keyword.value for keyword in node.keywords)]
            if self._is_secret_sink(call_name) and any(
                    self._contains_secret(argument, aliases, sensitive_names) for argument in sink_values):
                self._append_node(findings, "SECRET-001", node, request.content)

        return findings

    def _scan_bash(self, request: SafetyScanRequest) -> list[SafetyFinding]:
        findings: list[SafetyFinding] = []
        content = request.content
        if request.argv:
            content = f"{content} {shlex.join(request.argv)}"
        if "$(" in content or "`" in content:
            self._append(findings, "PROC-001", content)
        if re.search(r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:", content):
            self._append(findings, "RES-001", content)
        if re.search(r"\b(?:while\s+(?:true|:)|for\s*\(\(\s*;\s*;\s*\)\))", content):
            self._append(findings, "RES-002", content)

        try:
            lexer = shlex.shlex(content, posix=True, punctuation_chars=True)
            lexer.whitespace_split = True
            lexer.commenters = "#"
            tokens = list(lexer)
        except ValueError as ex:
            self._append(findings, "PARSE-001", f"{type(ex).__name__}: {ex}")
            return findings

        if "<<" in tokens or "<<<" in tokens:
            self._append(findings, "PARSE-001", content)
        if "nohup" in tokens or "&" in tokens:
            self._append(findings, "PROC-004", content)

        sensitive_env_names = {key for key in request.env if _SENSITIVE_NAME_RE.search(key)}
        if self._bash_secret_flow(tokens, sensitive_env_names):
            self._append(findings, "SECRET-001", content)

        for segment in self._command_segments(tokens):
            self._check_bash_segment(findings, segment, request)
        self._check_bash_redirections(findings, tokens, request)
        return findings

    def _check_python_file(
        self,
        findings: list[SafetyFinding],
        node: ast.Call,
        call_name: str,
        request: SafetyScanRequest,
    ) -> None:
        delete_calls = {
            "os.remove",
            "os.unlink",
            "shutil.rmtree",
            "pathlib.Path.unlink",
            "pathlib.Path.rmdir",
        }
        read_calls = {
            "pathlib.Path.read_text",
            "pathlib.Path.read_bytes",
        }
        write_calls = {
            "pathlib.Path.write_text",
            "pathlib.Path.write_bytes",
        }

        path_node: ast.AST | None = node.args[0] if node.args else None
        path_method_suffixes = (
            ".open",
            ".read_text",
            ".read_bytes",
            ".write_text",
            ".write_bytes",
            ".unlink",
            ".rmdir",
        )
        if call_name in read_calls | write_calls or call_name.endswith(path_method_suffixes):
            if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Call):
                path_node = node.func.value.args[0] if node.func.value.args else None
        path = _literal_string(path_node)

        if call_name in delete_calls or call_name.endswith((".unlink", ".rmdir")):
            if path is None:
                self._append_node(findings, "PROC-003", node, request.content)
            elif self._is_protected_delete(path, request.cwd):
                self._append_node(findings, "FILE-001", node, request.content)
            else:
                self._append_node(findings, "FILE-002", node, request.content)

        if call_name in {"open", "io.open", "pathlib.Path.open"} or call_name.endswith(".open"):
            mode_node = node.args[1] if len(node.args) > 1 else _keyword(node, "mode")
            mode = _literal_string(mode_node) or "r"
            if (path is not None and any(flag in mode for flag in "wax+")
                    and (self._is_sensitive_path(path, request.cwd) or self._is_system_path(path, request.cwd))):
                self._append_node(findings, "FILE-004", node, request.content)
            elif path is not None and self._is_sensitive_path(path, request.cwd):
                rule_id = "FILE-004" if any(flag in mode for flag in "wax+") else "FILE-003"
                self._append_node(findings, rule_id, node, request.content)
            elif path is None and any(flag in mode for flag in "wax+"):
                self._append_node(findings, "PROC-003", node, request.content)

        if call_name in read_calls or call_name.endswith((".read_text", ".read_bytes")):
            if path is not None and self._is_sensitive_path(path, request.cwd):
                self._append_node(findings, "FILE-003", node, request.content)
        if call_name in write_calls or call_name.endswith((".write_text", ".write_bytes")):
            if (path is not None
                    and (self._is_sensitive_path(path, request.cwd) or self._is_system_path(path, request.cwd))):
                self._append_node(findings, "FILE-004", node, request.content)

    def _check_python_network(
        self,
        findings: list[SafetyFinding],
        node: ast.Call,
        call_name: str,
        source: str,
    ) -> None:
        is_network = call_name in _NETWORK_FUNCTIONS
        if not is_network and isinstance(node.func, ast.Attribute) and node.func.attr in {
                "get",
                "post",
                "put",
                "patch",
                "delete",
                "request",
        }:
            first = _literal_string(node.args[0]) if node.args else None
            is_network = first is not None and first.startswith(("http://", "https://"))
        is_socket_connect = (call_name.endswith(".connect") and bool(node.args)
                             and isinstance(node.args[0], (ast.Tuple, ast.List)))
        if call_name in {"socket.socket", "socket.create_connection"} or is_socket_connect:
            is_network = True
        if not is_network:
            return
        if call_name == "socket.socket":
            self._append_node(findings, "NET-002", node, source)
            return

        url_node = node.args[0] if node.args else _keyword(node, "url")
        if call_name.endswith(".request") and len(node.args) > 1:
            url_node = node.args[1]
        if (call_name == "socket.create_connection" or is_socket_connect) and node.args:
            target = node.args[0]
            if isinstance(target, (ast.Tuple, ast.List)) and target.elts:
                url_node = target.elts[0]
        target = _literal_string(url_node)
        if target is None:
            self._append_node(findings, "NET-002", node, source)
            return
        host = self._hostname(target)
        if host is None or not self._domain_allowed(host):
            self._append_node(findings, "NET-001", node, source)

    def _check_python_process(
        self,
        findings: list[SafetyFinding],
        node: ast.Call,
        call_name: str,
        source: str,
    ) -> None:
        if call_name in {"eval", "exec", "os.system", "os.popen"}:
            self._append_node(findings, "PROC-001", node, source)
            return
        if call_name not in _SUBPROCESS_FUNCTIONS:
            return
        shell_node = _keyword(node, "shell")
        if isinstance(shell_node, ast.Constant) and shell_node.value is True:
            self._append_node(findings, "PROC-001", node, source)
        command_node = node.args[0] if node.args else _keyword(node, "args")
        parts = _command_parts(command_node)
        if parts is None or not parts:
            self._append_node(findings, "PROC-003", node, source)
        else:
            command = Path(parts[0]).name
            if self._is_dependency_install(parts):
                self._append_node(findings, "DEP-001", node, source)
            elif command not in self.policy.commands.allowed:
                self._append_node(findings, "PROC-002", node, source)
        if call_name == "subprocess.Popen":
            self._append_node(findings, "PROC-004", node, source)

    def _check_python_resource(
        self,
        findings: list[SafetyFinding],
        node: ast.Call,
        call_name: str,
        source: str,
        parent: dict[ast.AST, ast.AST],
    ) -> None:
        if call_name in {"time.sleep", "asyncio.sleep"} and node.args:
            value = node.args[0]
            seconds = value.value if isinstance(value, ast.Constant) and isinstance(value.value, (int, float)) else None
            if seconds is None or seconds > self.policy.limits.max_sleep_seconds:
                self._append_node(findings, "RES-002", node, source)

        if call_name.endswith(("ThreadPoolExecutor", "ProcessPoolExecutor")):
            workers_node = _keyword(node, "max_workers")
            if workers_node is None and node.args:
                workers_node = node.args[0]
            workers = _literal_int(workers_node)
            if workers is None or workers > self.policy.limits.max_concurrency:
                self._append_node(findings, "RES-002", node, source)

        write_value: ast.AST | None = None
        if isinstance(node.func, ast.Attribute) and node.func.attr in {"write", "write_text", "write_bytes"
                                                                       } and node.args:
            write_value = node.args[0]
        size = _static_value_size(write_value)
        if size is not None and size > self.policy.limits.max_static_write_size_bytes:
            self._append_node(findings, "RES-003", node, source)

        if call_name == "os.fork":
            current: ast.AST | None = node
            while current is not None:
                if isinstance(current, ast.While) and self._is_true(current.test):
                    self._append_node(findings, "RES-001", node, source)
                    break
                current = parent.get(current)

    def _check_bash_segment(
        self,
        findings: list[SafetyFinding],
        segment: list[str],
        request: SafetyScanRequest,
    ) -> None:
        while segment and segment[0] in {"if", "then", "elif", "else"}:
            segment = segment[1:]
        if not segment:
            return
        command = Path(segment[0]).name
        evidence = " ".join(segment)

        if command in {"sudo", "su", "pkexec", "doas"}:
            self._append(findings, "PROC-001", evidence)
        if command == "rm":
            targets = [token for token in segment[1:] if not token.startswith("-")]
            recursive = any(token.startswith("-") and ("r" in token or "R" in token) for token in segment[1:])
            for target in targets:
                if self._is_protected_delete(target, request.cwd):
                    self._append(findings, "FILE-001", evidence)
                elif recursive:
                    self._append(findings, "FILE-002", evidence)
        if command in {"cat", "cp", "head", "tail", "grep", "find"}:
            if any(self._is_sensitive_path(token, request.cwd) for token in segment[1:] if not token.startswith("-")):
                self._append(findings, "FILE-003", evidence)
        if command in {"tee", "truncate", "fallocate"}:
            targets = [token for token in segment[1:] if not token.startswith("-")]
            if any(
                    self._is_sensitive_path(token, request.cwd) or self._is_system_path(token, request.cwd)
                    for token in targets):
                self._append(findings, "FILE-004", evidence)
        if command == "dd":
            targets = [token.split("=", 1)[1] for token in segment[1:] if token.startswith("of=")]
            if any(
                    self._is_sensitive_path(token, request.cwd) or self._is_system_path(token, request.cwd)
                    for token in targets):
                self._append(findings, "FILE-004", evidence)

        if command in {"curl", "wget"}:
            targets = [token for token in segment[1:] if "://" in token]
            if not targets:
                self._append(findings, "NET-002", evidence)
            for target in targets:
                host = self._hostname(target)
                if host is None:
                    self._append(findings, "NET-002", evidence)
                elif not self._domain_allowed(host):
                    self._append(findings, "NET-001", evidence)
        if command in {"nc", "netcat", "telnet"}:
            hosts = [token for token in segment[1:] if not token.startswith("-")]
            if not hosts:
                self._append(findings, "NET-002", evidence)
            elif not self._domain_allowed(hosts[0]):
                self._append(findings, "NET-001", evidence)

        if self._is_dependency_install(segment):
            self._append(findings, "DEP-001", evidence)
        elif command not in self.policy.commands.allowed and command not in _SHELL_BUILTINS and command not in {
                "rm",
                "curl",
                "wget",
                "nc",
                "netcat",
                "telnet",
                "sudo",
                "su",
                "pkexec",
                "doas",
                "nohup",
                "truncate",
                "fallocate",
                "dd",
                "yes",
        }:
            self._append(findings, "PROC-002", evidence)

        if command in {"sleep"}:
            try:
                seconds = float(segment[1]) if len(segment) > 1 else None
            except ValueError:
                seconds = None
            if seconds is None or seconds > self.policy.limits.max_sleep_seconds:
                self._append(findings, "RES-002", evidence)
        if self._bash_static_write_exceeds(segment):
            self._append(findings, "RES-003", evidence)

    def _check_bash_redirections(
        self,
        findings: list[SafetyFinding],
        tokens: list[str],
        request: SafetyScanRequest,
    ) -> None:
        for index, token in enumerate(tokens[:-1]):
            if token not in {">", ">>", ">|"}:
                continue
            target = tokens[index + 1]
            if self._is_sensitive_path(target, request.cwd) or self._is_system_path(target, request.cwd):
                self._append(findings, "FILE-004", f"{token} {target}")
            previous = tokens[index - 1] if index > 0 else ""
            if previous == "yes":
                self._append(findings, "RES-003", f"yes {token} {target}")

    def _report(
        self,
        findings: Iterable[SafetyFinding],
        started: float,
        digest: str,
    ) -> SafetyReport:
        unique: dict[tuple[str, int | None, int | None, str], SafetyFinding] = {}
        for finding in findings:
            key = (finding.rule_id, finding.line_number, finding.column, finding.evidence)
            unique[key] = finding
        ordered = sorted(
            unique.values(),
            key=lambda finding: (
                -_ACTION_ORDER[finding.action],
                -_RISK_ORDER[finding.risk_level],
                finding.line_number or 0,
                finding.column or 0,
                finding.rule_id,
            ),
        )
        if any(finding.action == SafetyDecision.DENY for finding in ordered):
            decision = SafetyDecision.DENY
        elif any(finding.action == SafetyDecision.NEEDS_HUMAN_REVIEW for finding in ordered):
            decision = SafetyDecision.NEEDS_HUMAN_REVIEW
        else:
            decision = SafetyDecision.ALLOW
        risk_level = max(
            (finding.risk_level for finding in ordered),
            key=lambda level: _RISK_ORDER[level],
            default=RiskLevel.NONE,
        )
        return SafetyReport(
            decision=decision,
            risk_level=risk_level,
            findings=ordered,
            duration_ms=(time.perf_counter() - started) * 1000,
            sanitized=True,
            policy_version=self.policy.version,
            input_sha256=digest,
        )

    def _append(
        self,
        findings: list[SafetyFinding],
        rule_id: str,
        evidence: str,
        *,
        line_number: int | None = None,
        column: int | None = None,
    ) -> None:
        finding = make_finding(
            rule_id,
            self.policy,
            _sanitize_evidence(evidence),
            line_number=line_number,
            column=column,
        )
        if finding is not None:
            findings.append(finding)

    def _append_node(
        self,
        findings: list[SafetyFinding],
        rule_id: str,
        node: ast.AST,
        source: str,
    ) -> None:
        evidence = ast.get_source_segment(source, node) or type(node).__name__
        self._append(
            findings,
            rule_id,
            evidence,
            line_number=getattr(node, "lineno", None),
            column=getattr(node, "col_offset", None),
        )

    @staticmethod
    def _import_aliases(tree: ast.AST) -> dict[str, str]:
        aliases: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for item in node.names:
                    aliases[item.asname or item.name.split(".")[0]] = item.name
            elif isinstance(node, ast.ImportFrom) and node.module:
                for item in node.names:
                    aliases[item.asname or item.name] = f"{node.module}.{item.name}"
        return aliases

    def _sensitive_assignments(self, tree: ast.AST, aliases: dict[str, str]) -> set[str]:
        result: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            value = node.value
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            is_secret = self._contains_secret(value, aliases, result)
            if not is_secret:
                is_secret = any(
                    isinstance(target, ast.Name) and _SENSITIVE_NAME_RE.search(target.id) for target in targets)
            if is_secret:
                for target in targets:
                    if isinstance(target, ast.Name):
                        result.add(target.id)
        return result

    def _contains_secret(
        self,
        node: ast.AST | None,
        aliases: dict[str, str],
        sensitive_names: set[str],
    ) -> bool:
        if node is None:
            return False
        for child in ast.walk(node):
            if isinstance(child, ast.Constant) and isinstance(child.value, str):
                if _looks_like_secret_literal(child.value):
                    return True
            if isinstance(child, ast.Name) and child.id in sensitive_names:
                return True
            if isinstance(child, ast.Subscript):
                name = _call_name(child.value, aliases)
                key = _literal_string(child.slice)
                if name in {"os.environ", "environ"} and key and _SENSITIVE_NAME_RE.search(key):
                    return True
                if name == "sys.argv" and _SENSITIVE_ARGV_SENTINEL in sensitive_names:
                    return True
            if isinstance(child, ast.Call):
                name = _call_name(child.func, aliases)
                if name in {"os.getenv", "os.environ.get", "environ.get"} and child.args:
                    key = _literal_string(child.args[0])
                    if key and _SENSITIVE_NAME_RE.search(key):
                        return True
                if name.endswith((".read_text", ".read_bytes")) and isinstance(child.func, ast.Attribute):
                    owner = child.func.value
                    if isinstance(owner, ast.Call) and owner.args:
                        path = _literal_string(owner.args[0])
                        if path and self._is_sensitive_path(path, None):
                            return True
                if name in {"open", "io.open"} and child.args:
                    path = _literal_string(child.args[0])
                    if path and self._is_sensitive_path(path, None):
                        return True
        return False

    @staticmethod
    def _is_secret_sink(call_name: str) -> bool:
        suffixes = (
            ".debug",
            ".info",
            ".warning",
            ".error",
            ".critical",
            ".write",
            ".write_text",
            ".write_bytes",
            ".send",
        )
        return (call_name == "print" or call_name.startswith("logging.") or call_name.endswith(suffixes)
                or call_name in _NETWORK_FUNCTIONS)

    @staticmethod
    def _is_true(node: ast.AST) -> bool:
        return isinstance(node, ast.Constant) and node.value is True

    def _is_sensitive_path(self, value: str, cwd: str | None) -> bool:
        path = self._normalize_path(value, cwd)
        for denied in self.policy.paths.denied:
            denied_expanded = Path(os.path.expanduser(denied))
            if denied == ".env":
                if path.name == ".env":
                    return True
                continue
            denied_path = (denied_expanded.resolve(
                strict=False) if denied_expanded.is_absolute() else self._normalize_path(denied, cwd))
            if path == denied_path or denied_path in path.parents:
                return True
        return False

    def _is_system_path(self, value: str, cwd: str | None) -> bool:
        path = self._normalize_path(value, cwd)
        return path == Path("/") or any(path == root or root in path.parents for root in _SYSTEM_PATHS)

    def _is_protected_delete(self, value: str, cwd: str | None) -> bool:
        path = self._normalize_path(value, cwd)
        home = Path.home().resolve(strict=False)
        if path in {Path("/"), home} or self._is_system_path(value, cwd) or self._is_sensitive_path(value, cwd):
            return True
        if self.policy.paths.workspace_only_delete and cwd:
            workspace = Path(os.path.expanduser(cwd)).resolve(strict=False)
            return path != workspace and workspace not in path.parents
        return False

    @staticmethod
    def _normalize_path(value: str, cwd: str | None) -> Path:
        expanded = Path(os.path.expandvars(os.path.expanduser(value)))
        if not expanded.is_absolute():
            base = Path(os.path.expanduser(cwd)).resolve(strict=False) if cwd else Path.cwd()
            expanded = base / expanded
        return expanded.resolve(strict=False)

    def _domain_allowed(self, host: str) -> bool:
        normalized = host.lower().rstrip(".")
        for allowed in self.policy.network.allowed_domains:
            if normalized == allowed:
                return True
            if self.policy.network.allow_subdomains and normalized.endswith("." + allowed):
                return True
        return False

    @staticmethod
    def _hostname(target: str) -> str | None:
        parsed = urlparse(target if "://" in target else f"//{target}")
        return parsed.hostname

    @staticmethod
    def _is_dependency_install(parts: list[str]) -> bool:
        if not parts:
            return False
        command = Path(parts[0]).name
        if command not in _DEPENDENCY_COMMANDS:
            return False
        actions = set(parts[1:])
        return bool(actions & {"install", "uninstall", "add", "remove"})

    @staticmethod
    def _argument_contains_secret(value: str) -> bool:
        if _looks_like_secret_literal(value):
            return True
        if "=" not in value:
            return False
        key, secret_value = value.split("=", 1)
        return bool(secret_value and _SENSITIVE_NAME_RE.search(key.lstrip("-")))

    @staticmethod
    def _command_segments(tokens: list[str]) -> list[list[str]]:
        segments: list[list[str]] = []
        current: list[str] = []
        for token in tokens:
            if token in _SHELL_SEPARATORS:
                if current:
                    segments.append(current)
                    current = []
            else:
                current.append(token)
        if current:
            segments.append(current)
        return segments

    @staticmethod
    def _bash_secret_flow(tokens: list[str], sensitive_env_names: set[str]) -> bool:
        sinks = {"echo", "printf", "curl", "wget", "nc", "tee", ">", ">>"}
        if any(token in sinks for token in tokens) and any(_looks_like_secret_literal(token) for token in tokens):
            return True
        names = set(sensitive_env_names)
        names.update(token[1:].strip("{}") for token in tokens
                     if token.startswith("$") and _SENSITIVE_NAME_RE.search(token[1:].strip("{}")))
        if not names:
            return False
        references = {f"${name}" for name in names} | {f"${{{name}}}" for name in names}
        for index, token in enumerate(tokens):
            if not any(reference in token for reference in references):
                continue
            prefix = tokens[max(0, index - 4):index]
            if any(item in sinks for item in prefix):
                return True
        return False

    def _bash_static_write_exceeds(self, segment: list[str]) -> bool:
        if not segment:
            return False
        command = Path(segment[0]).name
        limit = self.policy.limits.max_static_write_size_bytes
        if command in {"truncate", "fallocate"}:
            for index, token in enumerate(segment[:-1]):
                if token in {"-s", "--size", "-l", "--length"}:
                    size = self._parse_size(segment[index + 1])
                    return size is not None and size > limit
        if command == "dd":
            values = {
                key: self._parse_size(value)
                for token in segment[1:] if "=" in token for key, value in [token.split("=", 1)]
                if key in {"bs", "count"}
            }
            if values.get("bs") is not None and values.get("count") is not None:
                return values["bs"] * values["count"] > limit  # type: ignore[operator]
        return command == "yes" and any(token in {">", ">>"} for token in segment)

    @staticmethod
    def _parse_size(value: str) -> int | None:
        match = re.fullmatch(r"(\d+)([KMGTP]?)(?:i?B?)?", value, re.IGNORECASE)
        if not match:
            return None
        number = int(match.group(1))
        suffix = match.group(2).upper()
        multiplier = {
            "": 1,
            "K": 1024,
            "M": 1024**2,
            "G": 1024**3,
            "T": 1024**4,
            "P": 1024**5,
        }[suffix]
        return number * multiplier
