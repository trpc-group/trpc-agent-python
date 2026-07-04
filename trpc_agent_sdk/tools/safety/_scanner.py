# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Static scanner for tool script and command safety."""

from __future__ import annotations

import ast
import re
import shlex
import time
from urllib.parse import urlparse

from ._matchers import is_command_allowed
from ._matchers import is_command_denied
from ._matchers import is_domain_allowed
from ._matchers import is_env_key_sensitive
from ._matchers import is_path_denied
from ._policy import SafetyPolicy
from ._policy import default_safety_policy
from ._redaction import contains_secret
from ._rules import is_rule_enabled
from ._rules import make_finding
from ._rules import merge_findings
from ._rules import should_block_decision
from ._types import SafetyReport
from ._types import ScanFinding
from ._types import ScanTarget
from ._types import ScriptLanguage

_SHELL_LANGUAGES = {ScriptLanguage.BASH, ScriptLanguage.SHELL}
_PYTHON_FEATURES_RE = re.compile(
    r"(^|\n)\s*(import|from|def|class|with|try|except)\b|"
    r"\b(print|open|subprocess|requests|Path|while\s+True|time\.sleep)\s*\(",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"https?://[^\s'\"<>)]*", re.IGNORECASE)
_SENSITIVE_PATH_RE = re.compile(
    r"(?i)(^|[/\\\s'\":])("
    r"\.env(?:\.[\w.-]+)?|"
    r"\.ssh(?:[/\\]|$)|"
    r"id_rsa|id_dsa|id_ed25519|"
    r"\.aws[/\\]credentials|"
    r"credentials?(?:\.[\w.-]+)?|"
    r"token\.(?:json|txt|env|key|pem|yml|yaml)|"
    r"private[_-]?key"
    r")"
)
_SYSTEM_PATH_RE = re.compile(r"(?i)^(?:/etc|/usr|/bin|/sbin)(?:/|$)|^[a-z]:[/\\]windows(?:[/\\]|$)")
_SHELL_CHAIN_RE = re.compile(r"(\|\||&&|\||;|\$\(|`)")
_BACKGROUND_RE = re.compile(r"(?i)(?:^|\s)nohup\s+|(?<!&)&\s*$")
_FORK_BOMB_RE = re.compile(r":\s*\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:")
_SHELL_SLEEP_RE = re.compile(r"(?i)(?:^|[;&|\s])sleep\s+([0-9]+(?:\.[0-9]+)?)")
_PYTHON_SLEEP_RE = re.compile(r"\btime\.sleep\s*\(\s*([0-9]+(?:\.[0-9]+)?)")
_PYTHON_WHILE_TRUE_RE = re.compile(r"\bwhile\s+(?:True|1)\s*:")
_ENV_REF_RE = re.compile(r"\$(?:\{(?P<braced>[A-Za-z_][A-Za-z0-9_]*)\}|(?P<bare>[A-Za-z_][A-Za-z0-9_]*))")
_NETWORK_COMMAND_RE = re.compile(r"(?i)(?:^|[\s;&|])(curl|wget|nc|ncat|ssh|scp)\b")
_DEPENDENCY_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"(?i)(?:^|[\s;&|])(?:python(?:3)?\s+-m\s+pip|pip(?:3)?)\s+install\b", "DEP_PIP_INSTALL"),
    (r"(?i)(?:^|[\s;&|])(?:npm\s+install|yarn\s+add|pnpm\s+add)\b", "DEP_NPM_INSTALL"),
    (
        r"(?i)(?:^|[\s;&|])(?:apt(?:-get)?|yum|dnf|apk|brew)\s+(?:install|add)\b",
        "DEP_SYSTEM_INSTALL",
    ),
)


class _FindingCollector:
    """Small helper that applies policy, redaction, and duplicate suppression."""

    def __init__(self, policy: SafetyPolicy):
        self.policy = policy
        self.findings: list[ScanFinding] = []
        self._seen: set[tuple[str, str, int | None]] = set()

    def add(
        self,
        rule_id: str,
        evidence: object,
        *,
        line: int | None = None,
        column: int | None = None,
        message: str | None = None,
        recommendation: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        if not is_rule_enabled(rule_id, self.policy):
            return

        finding = make_finding(
            rule_id,
            evidence,
            self.policy,
            line=line,
            column=column,
            message=message,
            recommendation=recommendation,
            metadata=metadata,
        )
        key = (finding.rule_id, finding.evidence, finding.line)
        if key in self._seen:
            return
        self._seen.add(key)
        self.findings.append(finding)


class SafetyScanner:
    """Scan tool scripts and commands before execution."""

    def __init__(self, policy: SafetyPolicy | None = None):
        self.policy = policy or default_safety_policy()

    def scan(self, target: ScanTarget) -> SafetyReport:
        """Scan a target and return a structured safety report."""

        started_at = time.perf_counter()
        collector = _FindingCollector(self.policy)
        parser_error: str | None = None
        language = self._infer_language(target)

        self._scan_target_limits(target, collector)

        if language == ScriptLanguage.PYTHON:
            parser_error = self._scan_python(target, collector)
        elif language in _SHELL_LANGUAGES:
            self._scan_shell_target(target, collector)
        else:
            self._scan_regex_fallback(self._normalized_text(target), collector)

        decision, risk_level = merge_findings(collector.findings)
        return SafetyReport(
            decision=decision,
            risk_level=risk_level,
            findings=collector.findings,
            elapsed_ms=(time.perf_counter() - started_at) * 1000,
            redacted=any(finding.redacted for finding in collector.findings),
            blocked=should_block_decision(decision, self.policy),
            language=language,
            policy_name=self.policy.name,
            parser_error=parser_error,
            metadata={"target_tool": target.tool_name} if target.tool_name else {},
        )

    def _infer_language(self, target: ScanTarget) -> ScriptLanguage:
        if target.language == ScriptLanguage.PYTHON:
            return ScriptLanguage.PYTHON
        if target.language in _SHELL_LANGUAGES:
            return target.language
        if target.command:
            return ScriptLanguage.SHELL
        if _PYTHON_FEATURES_RE.search(target.content or target.stdin):
            return ScriptLanguage.PYTHON
        return ScriptLanguage.SHELL

    def _normalized_text(self, target: ScanTarget) -> str:
        parts = [
            target.content,
            target.command,
            " ".join(str(arg) for arg in target.args),
            target.stdin,
            target.cwd,
            "\n".join(str(key) for key in target.env),
        ]
        return "\n".join(part for part in parts if part)

    def _scan_target_limits(self, target: ScanTarget, collector: _FindingCollector) -> None:
        script_text = "\n".join(part for part in (target.content, target.command, target.stdin) if part)
        if script_text and len(script_text.splitlines()) > self.policy.max_script_lines:
            collector.add(
                "RES_LARGE_WRITE",
                f"script has {len(script_text.splitlines())} lines",
                message="Script line count exceeds the safety policy limit.",
            )
        if target.timeout_seconds is not None and target.timeout_seconds > self.policy.max_timeout_seconds:
            collector.add(
                "RES_LONG_SLEEP",
                f"timeout_seconds={target.timeout_seconds}",
                message="Requested execution timeout exceeds the safety policy limit.",
            )
        if target.output_limit_bytes is not None and target.output_limit_bytes > self.policy.max_output_bytes:
            collector.add(
                "RES_LARGE_WRITE",
                f"output_limit_bytes={target.output_limit_bytes}",
                message="Requested output limit exceeds the safety policy limit.",
            )

    def _scan_python(self, target: ScanTarget, collector: _FindingCollector) -> str | None:
        source = target.content or target.stdin or target.command
        parser_error: str | None = None

        try:
            tree = ast.parse(source or "")
        except SyntaxError as ex:
            parser_error = f"SyntaxError: {ex.msg}"
            collector.add(
                "PARSER_FALLBACK_USED",
                ex.text or source[: self.policy.max_evidence_chars],
                line=ex.lineno,
                message="Python AST parsing failed and fallback scanning was used.",
            )
            self._scan_regex_fallback(self._normalized_text(target), collector)
            return parser_error

        self._scan_python_tree(tree, source or "", collector)
        extra_text = "\n".join(part for part in (target.command, " ".join(target.args), target.cwd) if part)
        if extra_text:
            self._scan_regex_fallback(extra_text, collector)
        self._scan_env_usage(target, self._normalized_text(target), collector)
        return parser_error

    def _scan_python_tree(self, tree: ast.AST, source: str, collector: _FindingCollector) -> None:
        path_home_seen = False
        sensitive_path_literals: list[tuple[str, int | None]] = []

        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if self._is_sensitive_path(node.value):
                    sensitive_path_literals.append((node.value, getattr(node, "lineno", None)))
                if contains_secret(node.value):
                    collector.add("LEAK_SECRET_LITERAL", node.value, line=getattr(node, "lineno", None))

            if isinstance(node, ast.While) and self._is_python_infinite_loop(node.test):
                collector.add("RES_INFINITE_LOOP", _source_segment(source, node), line=node.lineno)

            if isinstance(node, ast.Call):
                call_name = _full_name(node.func)
                evidence = _source_segment(source, node)
                if call_name == "Path.home" or call_name.endswith(".Path.home"):
                    path_home_seen = True
                self._scan_python_call(node, call_name, evidence, collector)

        if path_home_seen:
            for literal, line in sensitive_path_literals:
                collector.add("FILE_SENSITIVE_READ", f"Path.home() with {literal}", line=line)

    def _scan_python_call(
        self,
        node: ast.Call,
        call_name: str,
        evidence: str,
        collector: _FindingCollector,
    ) -> None:
        if call_name == "open":
            self._scan_python_open_call(node, evidence, collector)

        if call_name.endswith((".read_text", ".read_bytes", ".write_text", ".write_bytes", ".open")):
            self._scan_python_path_method(node, call_name, evidence, collector)

        if call_name in {"os.system", "commands.getoutput", "commands.getstatusoutput"}:
            collector.add("PROC_OS_SYSTEM", evidence, line=node.lineno)
            self._scan_shell_literals_from_call(node, collector)

        if _is_subprocess_call(call_name):
            collector.add(
                "PROC_OS_SYSTEM",
                evidence,
                line=node.lineno,
                message="Subprocess execution was detected.",
            )
            if _call_has_keyword_bool(node, "shell", True):
                collector.add("PROC_SUBPROCESS_SHELL", evidence, line=node.lineno)
            self._scan_shell_literals_from_call(node, collector)

        if _is_network_call(call_name):
            self._scan_python_network_call(node, call_name, evidence, collector)

        if call_name.endswith(".connect") or call_name.endswith(".create_connection"):
            self._scan_socket_call(node, evidence, collector)

        if call_name == "time.sleep" or call_name.endswith(".time.sleep"):
            seconds = _first_numeric_argument(node)
            if seconds is None or seconds > self.policy.max_sleep_seconds:
                collector.add("RES_LONG_SLEEP", evidence, line=node.lineno)

        if self._call_is_secret_sink(call_name) and _call_contains_secret_literal(node):
            collector.add("LEAK_SECRET_LITERAL", evidence, line=node.lineno)

        if self._call_references_sensitive_env(node):
            collector.add("LEAK_ENV_SECRET", evidence, line=node.lineno)

    def _scan_python_open_call(self, node: ast.Call, evidence: str, collector: _FindingCollector) -> None:
        path = _first_string_argument(node)
        mode = _string_argument_at(node, 1) or _keyword_string(node, "mode") or "r"
        if path is None:
            return
        if self._is_sensitive_path(path):
            collector.add("FILE_SENSITIVE_READ", evidence, line=node.lineno)
        if is_path_denied(path, self.policy):
            collector.add("FILE_FORBIDDEN_PATH_ACCESS", evidence, line=node.lineno)
        if _is_write_mode(mode) and self._is_system_path(path):
            collector.add("FILE_SYSTEM_OVERWRITE", evidence, line=node.lineno)

    def _scan_python_path_method(
        self,
        node: ast.Call,
        call_name: str,
        evidence: str,
        collector: _FindingCollector,
    ) -> None:
        path = _path_from_receiver(getattr(node.func, "value", None))
        if path is None and self._is_sensitive_path(evidence):
            path = evidence
        if path is None:
            return

        if self._is_sensitive_path(path):
            collector.add("FILE_SENSITIVE_READ", evidence, line=node.lineno)
        if is_path_denied(path, self.policy):
            collector.add("FILE_FORBIDDEN_PATH_ACCESS", evidence, line=node.lineno)
        if call_name.endswith((".write_text", ".write_bytes")) and self._is_system_path(path):
            collector.add("FILE_SYSTEM_OVERWRITE", evidence, line=node.lineno)

    def _scan_python_network_call(
        self,
        node: ast.Call,
        call_name: str,
        evidence: str,
        collector: _FindingCollector,
    ) -> None:
        url = _first_string_argument(node)
        if url is None:
            collector.add("NET_DYNAMIC_EGRESS_REVIEW", evidence, line=node.lineno)
            return
        self._scan_url(url, evidence, collector, line=node.lineno)

    def _scan_socket_call(self, node: ast.Call, evidence: str, collector: _FindingCollector) -> None:
        host = _socket_host_argument(node)
        if host is None:
            collector.add("NET_DYNAMIC_EGRESS_REVIEW", evidence, line=node.lineno)
            return
        if not is_domain_allowed(host, self.policy.allowed_domains):
            collector.add("NET_NON_WHITELIST_EGRESS", evidence, line=node.lineno, metadata={"host": host})

    def _scan_shell_literals_from_call(self, node: ast.Call, collector: _FindingCollector) -> None:
        for literal in _string_arguments(node):
            self._scan_shell_text(literal, collector)

    def _scan_shell_target(self, target: ScanTarget, collector: _FindingCollector) -> None:
        text = "\n".join(part for part in (target.content, target.command, " ".join(target.args), target.stdin) if part)
        self._scan_shell_text(text, collector)
        self._scan_env_usage(target, text, collector)

    def _scan_shell_text(self, text: str, collector: _FindingCollector) -> None:
        if not text:
            return

        for line_number, line in enumerate(text.splitlines() or [text], start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            self._scan_shell_line(stripped, line_number, collector)

    def _scan_shell_line(self, line: str, line_number: int, collector: _FindingCollector) -> None:
        lowered = line.lower()

        if _SHELL_CHAIN_RE.search(line):
            collector.add("PROC_SHELL_PIPE_OR_CHAIN", line, line=line_number)
        if _BACKGROUND_RE.search(line):
            collector.add("PROC_BACKGROUND_PROCESS", line, line=line_number)

        if is_command_denied(line, self.policy):
            collector.add(
                "POLICY_DENIED_COMMAND",
                line,
                line=line_number,
            )
        elif not is_command_allowed(line, self.policy):
            collector.add(
                "PROC_OS_SYSTEM",
                line,
                line=line_number,
                message="Command is not allowlisted by the safety policy.",
            )

        if re.search(r"(?i)(?:^|[\s;&|])(?:sudo|su|chown)\b|\bchmod\s+777\b", line):
            collector.add("PROC_PRIVILEGE_ESCALATION", line, line=line_number)

        if re.search(r"(?i)(?:^|[\s;&|])rm\s+(?:-[^\s]*r[^\s]*f|-[^\s]*f[^\s]*r)\b", line):
            collector.add("FILE_RECURSIVE_DELETE", line, line=line_number)
        if re.search(r"(?i)(?:^|[\s;&|])(?:rmdir\s+/s|del\s+/f)\b", line):
            collector.add("FILE_RECURSIVE_DELETE", line, line=line_number)

        self._scan_shell_paths(line, line_number, collector)
        self._scan_shell_network(line, line_number, collector)
        self._scan_shell_dependency_installs(line, line_number, collector)
        self._scan_shell_resource_patterns(line, line_number, collector)
        self._scan_shell_secret_leaks(line, line_number, collector)

    def _scan_shell_paths(self, line: str, line_number: int, collector: _FindingCollector) -> None:
        if self._is_sensitive_path(line):
            collector.add("FILE_SENSITIVE_READ", line, line=line_number)

        for token in _shell_tokens(line):
            clean_token = token.strip("\"'=:,")
            if is_path_denied(clean_token, self.policy):
                collector.add("FILE_FORBIDDEN_PATH_ACCESS", token, line=line_number)
            if self._is_system_path(clean_token) and re.search(r"(?i)(>|>>|\b(?:tee|cp|mv|install)\b)", line):
                collector.add("FILE_SYSTEM_OVERWRITE", line, line=line_number)

        if re.search(r"(?i)(>|>>)\s*(?:/etc|/usr|/bin|/sbin|[a-z]:[/\\]windows)\b", line):
            collector.add("FILE_SYSTEM_OVERWRITE", line, line=line_number)

    def _scan_shell_network(self, line: str, line_number: int, collector: _FindingCollector) -> None:
        has_network_command = bool(_NETWORK_COMMAND_RE.search(line))
        urls = list(_URL_RE.findall(line))
        for url in urls:
            self._scan_url(url, line, collector, line=line_number)

        if has_network_command and (not urls or _has_dynamic_shell_expansion(line)):
            if not urls or any(_has_dynamic_shell_expansion(url) for url in urls):
                collector.add("NET_DYNAMIC_EGRESS_REVIEW", line, line=line_number)

        if re.search(r"(?i)(?:^|[\s;&|])(?:nc|ncat|ssh|scp)\s+[A-Za-z0-9.-]+\b", line):
            host = _host_after_command(line)
            if host and not is_domain_allowed(host, self.policy.allowed_domains):
                collector.add("NET_NON_WHITELIST_EGRESS", line, line=line_number, metadata={"host": host})

    def _scan_shell_dependency_installs(self, line: str, line_number: int, collector: _FindingCollector) -> None:
        for pattern, rule_id in _DEPENDENCY_PATTERNS:
            if re.search(pattern, line):
                collector.add(rule_id, line, line=line_number)

    def _scan_shell_resource_patterns(self, line: str, line_number: int, collector: _FindingCollector) -> None:
        if _FORK_BOMB_RE.search(line):
            collector.add("RES_FORK_BOMB", line, line=line_number)
        if re.search(r"(?i)\bwhile\s+true\b|\bfor\s*\(\(\s*;\s*;\s*\)\)", line):
            collector.add("RES_INFINITE_LOOP", line, line=line_number)
        if re.search(r"(?i)(?:^|[\s;&|])yes\s*>", line) or re.search(r"(?i)\bdd\s+if=/dev/zero\b", line):
            collector.add("RES_LARGE_WRITE", line, line=line_number)

        for match in _SHELL_SLEEP_RE.finditer(line):
            seconds = _safe_float(match.group(1))
            if seconds is None or seconds > self.policy.max_sleep_seconds:
                collector.add("RES_LONG_SLEEP", line, line=line_number)

    def _scan_shell_secret_leaks(self, line: str, line_number: int, collector: _FindingCollector) -> None:
        env_keys = _env_refs(line)
        if any(is_env_key_sensitive(key, self.policy) for key in env_keys):
            if re.search(r"(?i)\b(echo|printf|printenv|env|curl|wget|tee)\b|>|>>", line):
                collector.add("LEAK_ENV_SECRET", line, line=line_number)

        if re.search(r"(?i)\benv\b.*\|.*\b(curl|wget|nc|ncat)\b", line):
            collector.add("LEAK_ENV_SECRET", line, line=line_number)

        if contains_secret(line):
            collector.add("LEAK_SECRET_LITERAL", line, line=line_number)

    def _scan_regex_fallback(self, text: str, collector: _FindingCollector) -> None:
        if not text:
            return
        self._scan_shell_text(text, collector)
        for match in _PYTHON_WHILE_TRUE_RE.finditer(text):
            collector.add("RES_INFINITE_LOOP", match.group(0))
        for match in _PYTHON_SLEEP_RE.finditer(text):
            seconds = _safe_float(match.group(1))
            if seconds is None or seconds > self.policy.max_sleep_seconds:
                collector.add("RES_LONG_SLEEP", match.group(0))

    def _scan_env_usage(self, target: ScanTarget, text: str, collector: _FindingCollector) -> None:
        if not target.env:
            return
        sensitive_keys = {key for key in target.env if is_env_key_sensitive(key, self.policy)}
        if not sensitive_keys:
            return
        leak_context = re.search(r"(?i)\b(env|printenv|echo|printf|curl|wget|tee)\b|>|>>", text or "")
        if leak_context and any(key in text for key in sensitive_keys):
            collector.add("LEAK_ENV_SECRET", "sensitive env key used in output or network context")

    def _scan_url(
        self,
        url: str,
        evidence: str,
        collector: _FindingCollector,
        *,
        line: int | None = None,
    ) -> None:
        if _has_dynamic_shell_expansion(url):
            collector.add("NET_DYNAMIC_EGRESS_REVIEW", evidence, line=line)
            return

        parsed = urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            collector.add("NET_DYNAMIC_EGRESS_REVIEW", evidence, line=line)
            return
        if not is_domain_allowed(hostname, self.policy.allowed_domains):
            collector.add("NET_NON_WHITELIST_EGRESS", evidence, line=line, metadata={"host": hostname})

    def _is_sensitive_path(self, value: str) -> bool:
        return bool(_SENSITIVE_PATH_RE.search(value))

    def _is_system_path(self, value: str) -> bool:
        return bool(_SYSTEM_PATH_RE.search(value.strip("\"'")))

    def _call_is_secret_sink(self, call_name: str) -> bool:
        return (
            call_name == "print"
            or call_name.endswith((".write", ".write_text", ".post", ".put", ".get"))
            or call_name in {"os.system"}
            or _is_subprocess_call(call_name)
        )

    def _call_references_sensitive_env(self, node: ast.Call) -> bool:
        for child in ast.walk(node):
            if isinstance(child, ast.Constant) and isinstance(child.value, str):
                if is_env_key_sensitive(child.value, self.policy):
                    return True
            if isinstance(child, ast.Attribute) and child.attr in {"environ", "getenv"}:
                return True
        return False

    def _is_python_infinite_loop(self, test: ast.AST) -> bool:
        if isinstance(test, ast.Constant):
            return test.value is True or test.value == 1
        return False


def _full_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _full_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    if isinstance(node, ast.Call):
        return _full_name(node.func)
    return ""


def _source_segment(source: str, node: ast.AST) -> str:
    segment = ast.get_source_segment(source, node)
    if segment:
        return segment
    return type(node).__name__


def _literal_string(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
                return None
            parts.append(value.value)
        return "".join(parts)
    return None


def _first_string_argument(node: ast.Call) -> str | None:
    return _string_argument_at(node, 0)


def _string_argument_at(node: ast.Call, index: int) -> str | None:
    if len(node.args) <= index:
        return None
    return _literal_string(node.args[index])


def _keyword_string(node: ast.Call, keyword_name: str) -> str | None:
    for keyword in node.keywords:
        if keyword.arg == keyword_name:
            return _literal_string(keyword.value)
    return None


def _string_arguments(node: ast.Call) -> tuple[str, ...]:
    values: list[str] = []
    for arg in node.args:
        literal = _literal_string(arg)
        if literal is not None:
            values.append(literal)
        elif isinstance(arg, (ast.List, ast.Tuple)):
            values.extend(value for value in (_literal_string(item) for item in arg.elts) if value is not None)
    return tuple(values)


def _call_has_keyword_bool(node: ast.Call, keyword_name: str, expected: bool) -> bool:
    for keyword in node.keywords:
        if keyword.arg != keyword_name:
            continue
        if isinstance(keyword.value, ast.Constant):
            return keyword.value.value is expected
    return False


def _first_numeric_argument(node: ast.Call) -> float | None:
    if not node.args:
        return None
    return _numeric_literal(node.args[0])


def _numeric_literal(node: ast.AST) -> float | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _numeric_literal(node.operand)
        if value is None:
            return None
        return -value if isinstance(node.op, ast.USub) else value
    return None


def _is_write_mode(mode: str) -> bool:
    return any(char in mode for char in ("w", "a", "x", "+"))


def _path_from_receiver(node: ast.AST | None) -> str | None:
    if node is None:
        return None
    literal = _literal_string(node)
    if literal is not None:
        return literal
    if isinstance(node, ast.Call) and _full_name(node.func).endswith("Path"):
        return _first_string_argument(node)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        parts = [part for part in (_path_from_receiver(node.left), _path_from_receiver(node.right)) if part]
        return "/".join(parts) if parts else None
    return None


def _is_subprocess_call(call_name: str) -> bool:
    return call_name in {
        "subprocess.run",
        "subprocess.Popen",
        "subprocess.call",
        "subprocess.check_call",
        "subprocess.check_output",
    }


def _is_network_call(call_name: str) -> bool:
    if call_name in {
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
        "urllib.request.urlopen",
    }:
        return True
    return call_name.endswith((".get", ".post", ".put", ".patch", ".delete")) and "ClientSession" in call_name


def _socket_host_argument(node: ast.Call) -> str | None:
    if not node.args:
        return None
    first = node.args[0]
    if isinstance(first, (ast.Tuple, ast.List)) and first.elts:
        return _literal_string(first.elts[0])
    return _literal_string(first)


def _call_contains_secret_literal(node: ast.Call) -> bool:
    return any(
        isinstance(child, ast.Constant) and isinstance(child.value, str) and contains_secret(child.value)
        for child in ast.walk(node)
    )


def _shell_tokens(line: str) -> tuple[str, ...]:
    try:
        return tuple(shlex.split(line, posix=True))
    except ValueError:
        return tuple(line.split())


def _env_refs(line: str) -> set[str]:
    refs: set[str] = set()
    for match in _ENV_REF_RE.finditer(line):
        refs.add(match.group("braced") or match.group("bare") or "")
    return {ref for ref in refs if ref}


def _has_dynamic_shell_expansion(value: str) -> bool:
    return "$" in value or "`" in value


def _host_after_command(line: str) -> str | None:
    tokens = _shell_tokens(line)
    for index, token in enumerate(tokens[:-1]):
        if token.lower() in {"nc", "ncat", "ssh", "scp"}:
            candidate = tokens[index + 1]
            if "@" in candidate:
                candidate = candidate.rsplit("@", 1)[-1]
            return candidate.split(":", 1)[0].strip()
    return None


def _safe_float(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None
