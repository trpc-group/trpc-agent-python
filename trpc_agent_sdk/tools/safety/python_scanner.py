# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""AST-based Python safety scanner rules."""

from __future__ import annotations

import ast
from typing import List
from typing import Optional
from urllib.parse import urlparse

from .checker import Rule
from .checker import SafetyChecker
from .models import Finding
from .models import SafetyResult
from .models import SafetySeverity
from .models import ToolExecutionRequest
from .policy import SafetyPolicy

_PYTHON_LANGUAGES = {"python", "py", "python3", "tool_code"}


class PythonScanContext:
    """Parsed Python source plus lightweight symbol information."""

    def __init__(self, source: str, tree: ast.AST):
        self.source = source
        self.tree = tree
        self.aliases = self._collect_aliases(tree)
        self.socket_vars = self._collect_socket_vars(tree)

    @classmethod
    def create(cls, source: str) -> Optional["PythonScanContext"]:
        """Parse Python source. Return None when source is not valid Python."""
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return None
        return cls(source, tree)

    @staticmethod
    def _collect_aliases(tree: ast.AST) -> dict[str, str]:
        aliases: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for item in node.names:
                    if item.asname:
                        aliases[item.asname] = item.name
                    else:
                        local = item.name.split(".")[0]
                        aliases[local] = local
            elif isinstance(node, ast.ImportFrom) and node.module:
                for item in node.names:
                    local = item.asname or item.name
                    aliases[local] = f"{node.module}.{item.name}"
        return aliases

    def _collect_socket_vars(self, tree: ast.AST) -> set[str]:
        socket_vars: set[str] = set()
        for node in ast.walk(tree):
            value = None
            targets: list[ast.expr] = []
            if isinstance(node, ast.Assign):
                value = node.value
                targets = list(node.targets)
            elif isinstance(node, ast.AnnAssign):
                value = node.value
                targets = [node.target]
            if value is None or not self._is_socket_constructor(value):
                continue
            for target in targets:
                if isinstance(target, ast.Name):
                    socket_vars.add(target.id)
        return socket_vars

    def _is_socket_constructor(self, node: ast.AST) -> bool:
        if not isinstance(node, ast.Call):
            return False
        return self.resolve_call_name(node.func) in {"socket.socket"}

    def resolve_call_name(self, node: ast.AST) -> str:
        """Resolve a simple dotted call name with import aliases."""
        if isinstance(node, ast.Name):
            return self.aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            base = self.resolve_call_name(node.value)
            return f"{base}.{node.attr}" if base else node.attr
        if isinstance(node, ast.Call):
            return self.resolve_call_name(node.func)
        return ""


class PythonAstRule(Rule):
    """Base class for AST-backed Python rules."""

    severity = SafetySeverity.HIGH

    async def check(self, request: ToolExecutionRequest, policy: SafetyPolicy) -> List[Finding]:
        source = _extract_python_source(request)
        if not source:
            return []
        context = PythonScanContext.create(source)
        if context is None:
            return []
        return self.check_ast(context, policy)

    def check_ast(self, context: PythonScanContext, policy: SafetyPolicy) -> List[Finding]:
        """Check parsed Python source."""
        raise NotImplementedError

    def _finding(self, message: str, node: ast.AST, policy: SafetyPolicy, target: str = "") -> Finding:
        return Finding(
            rule_id=self.rule_id,
            message=message,
            severity=policy.rule_severity(self.rule_id, self.severity),
            target=target,
            metadata={
                "line": getattr(node, "lineno", 0),
                "column": getattr(node, "col_offset", 0),
            },
        )


class PythonCallRule(PythonAstRule):
    """Rule for matching fully-qualified call names."""

    call_names: set[str] = set()
    message = "Unsafe Python call detected."

    def check_ast(self, context: PythonScanContext, policy: SafetyPolicy) -> List[Finding]:
        findings: list[Finding] = []
        for node in ast.walk(context.tree):
            if not isinstance(node, ast.Call):
                continue
            call_name = context.resolve_call_name(node.func)
            if call_name in self.call_names and not policy.is_command_allowed(self.rule_id, call_name):
                findings.append(self._finding(self.message, node, policy, call_name))
        return findings


class OsSystemRule(PythonCallRule):
    """Detect os.system calls."""

    @property
    def rule_id(self) -> str:
        return "python.os_system"

    call_names = {"os.system"}
    message = "Python code calls os.system."


class SubprocessRunRule(PythonCallRule):
    """Detect subprocess.run calls."""

    @property
    def rule_id(self) -> str:
        return "python.subprocess_run"

    call_names = {"subprocess.run"}
    message = "Python code calls subprocess.run."


class SubprocessPopenRule(PythonCallRule):
    """Detect subprocess.Popen calls."""

    @property
    def rule_id(self) -> str:
        return "python.subprocess_popen"

    call_names = {"subprocess.Popen"}
    message = "Python code calls subprocess.Popen."


class ShutilRmtreeRule(PythonCallRule):
    """Detect shutil.rmtree calls."""

    @property
    def rule_id(self) -> str:
        return "python.shutil_rmtree"

    call_names = {"shutil.rmtree"}
    message = "Python code calls shutil.rmtree."


class RequestsGetPostRule(PythonCallRule):
    """Detect requests.get and requests.post calls."""

    @property
    def rule_id(self) -> str:
        return "python.requests_get_post"

    call_names = {"requests.get", "requests.post"}
    message = "Python code makes an HTTP request with requests.get/post."

    def check_ast(self, context: PythonScanContext, policy: SafetyPolicy) -> List[Finding]:
        findings: list[Finding] = []
        for node in ast.walk(context.tree):
            if not isinstance(node, ast.Call):
                continue
            call_name = context.resolve_call_name(node.func)
            if call_name not in self.call_names:
                continue
            domain = _domain_from_call(node)
            if domain and policy.is_domain_allowed(self.rule_id, domain):
                continue
            findings.append(self._finding(self.message, node, policy, call_name))
        return findings


class SocketConnectRule(PythonAstRule):
    """Detect socket connect calls."""

    @property
    def rule_id(self) -> str:
        return "python.socket_connect"

    def check_ast(self, context: PythonScanContext, policy: SafetyPolicy) -> List[Finding]:
        findings: list[Finding] = []
        for node in ast.walk(context.tree):
            if not isinstance(node, ast.Call):
                continue
            call_name = context.resolve_call_name(node.func)
            is_direct_socket_call = call_name in {"socket.connect", "socket.socket.connect"}
            if not (is_direct_socket_call or self._is_socket_var_connect(node.func, context)):
                continue
            domain = _domain_from_call(node)
            if domain and policy.is_domain_allowed(self.rule_id, domain):
                continue
            findings.append(self._finding("Python code calls socket.connect.", node, policy, call_name or "connect"))
        return findings

    @staticmethod
    def _is_socket_var_connect(node: ast.AST, context: PythonScanContext) -> bool:
        return (isinstance(node, ast.Attribute) and node.attr == "connect" and isinstance(node.value, ast.Name)
                and node.value.id in context.socket_vars)


class PythonPathReadRule(PythonAstRule):
    """Base class for file-read rules that match path literals."""

    message = "Python code reads a sensitive path."

    def check_ast(self, context: PythonScanContext, policy: SafetyPolicy) -> List[Finding]:
        findings: list[Finding] = []
        for node in ast.walk(context.tree):
            if not isinstance(node, ast.Call) or not _is_file_read_call(node, context):
                continue
            for path in _path_strings_from_read_call(node):
                if self.path_matches(path, policy):
                    findings.append(self._finding(self.message, node, policy, path))
        return findings

    def path_matches(self, path: str, policy: SafetyPolicy) -> bool:
        """Return whether a path literal should be reported."""
        raise NotImplementedError


class EnvFileReadRule(PythonPathReadRule):
    """Detect reads of .env files."""

    @property
    def rule_id(self) -> str:
        return "python.read_env_file"

    message = "Python code reads a .env file."

    def path_matches(self, path: str, policy: SafetyPolicy) -> bool:
        return policy.is_path_blocked(self.rule_id, path)


class SshPathReadRule(PythonPathReadRule):
    """Detect reads of ~/.ssh paths."""

    @property
    def rule_id(self) -> str:
        return "python.read_ssh_path"

    message = "Python code reads a ~/.ssh path."

    def path_matches(self, path: str, policy: SafetyPolicy) -> bool:
        return policy.is_path_blocked(self.rule_id, path)


class PythonScanner:
    """Convenience scanner using the default Python safety rules."""

    def __init__(self, rules: Optional[list[Rule]] = None, policy: Optional[SafetyPolicy] = None):
        self._checker = SafetyChecker(rules or create_python_rules(), policy)

    async def scan(self, source: str, policy: Optional[SafetyPolicy] = None) -> SafetyResult:
        """Scan Python source and return a safety result."""
        request = ToolExecutionRequest(language="python", script=source)
        return await self._checker.check(request, policy)


def create_python_rules() -> list[Rule]:
    """Create the built-in Python AST safety rules."""
    return [
        OsSystemRule(),
        SubprocessRunRule(),
        SubprocessPopenRule(),
        ShutilRmtreeRule(),
        EnvFileReadRule(),
        SshPathReadRule(),
        RequestsGetPostRule(),
        SocketConnectRule(),
    ]


def _extract_python_source(request: ToolExecutionRequest) -> str:
    language = (request.language or request.metadata.get("language") or "").strip().lower()
    if language and language not in _PYTHON_LANGUAGES:
        return ""
    for value in (
            request.script,
            request.args.get("code"),
            request.args.get("script"),
            request.metadata.get("code"),
            request.metadata.get("script"),
            request.metadata.get("python_code"),
    ):
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _is_file_read_call(node: ast.Call, context: PythonScanContext) -> bool:
    call_name = context.resolve_call_name(node.func)
    if call_name in {"open", "builtins.open", "io.open"}:
        return _mode_reads(node, default=True)
    if call_name.endswith(".open"):
        return _mode_reads(node, default=True)
    return call_name.endswith(".read_text") or call_name.endswith(".read_bytes")


def _mode_reads(node: ast.Call, default: bool) -> bool:
    mode = None
    if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str):
        mode = node.args[1].value
    for keyword in node.keywords:
        if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
            mode = keyword.value.value
            break
    if mode is None:
        return default
    return "r" in mode or "+" in mode


def _path_strings_from_read_call(node: ast.Call) -> list[str]:
    candidates: list[ast.AST] = []
    if _is_path_method_call(node):
        value = node.func.value  # type: ignore[union-attr]
        if isinstance(value, ast.Call):
            candidates.extend(value.args)
            candidates.extend(keyword.value for keyword in value.keywords if keyword.arg in {"path", "file"})
        else:
            candidates.append(value)
    else:
        if node.args:
            candidates.append(node.args[0])
        candidates.extend(keyword.value for keyword in node.keywords if keyword.arg in {"file", "path"})
    strings: list[str] = []
    for candidate in candidates:
        strings.extend(_literal_strings(candidate))
    return strings


def _is_path_method_call(node: ast.Call) -> bool:
    return isinstance(node.func, ast.Attribute) and node.func.attr in {"open", "read_text", "read_bytes"}


def _literal_strings(node: ast.AST) -> list[str]:
    strings: list[str] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            strings.append(child.value)
    return strings


def _domain_from_call(node: ast.Call) -> str:
    for arg in node.args:
        for value in _literal_strings(arg):
            domain = _domain_from_string(value)
            if domain:
                return domain
    for keyword in node.keywords:
        for value in _literal_strings(keyword.value):
            domain = _domain_from_string(value)
            if domain:
                return domain
    return ""


def _domain_from_string(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.hostname:
        return parsed.hostname.lower()
    host = value.split("/", 1)[0].split(":", 1)[0].strip()
    return host.lower()

