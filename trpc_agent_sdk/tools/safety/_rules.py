# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Static scanner rules for Python and shell scripts."""

from __future__ import annotations

import ast
import re
import shlex
from typing import Any
from urllib.parse import urlparse

from ._policy import ToolSafetyPolicy
from ._types import Decision
from ._types import RiskFinding
from ._types import RiskLevel

SENSITIVE_WORDS = (
    "api_key",
    "apikey",
    "auth_token",
    "credential",
    "password",
    "passwd",
    "private_key",
    "secret",
    "ssh_key",
    "token",
)

PY_NETWORK_METHODS = {"get", "post", "put", "patch", "delete", "request", "urlopen", "Request"}
NETWORK_COMMANDS = {"curl", "wget", "nc", "netcat", "socat", "ssh", "scp", "rsync", "openssl"}
LARGE_ALLOCATION_BYTES = 512 * 1024 * 1024
LARGE_ITERATION_COUNT = 10_000_000
SHELL_OPERATORS = ("|", ";", "&&", "||", "$(", "`", ">", ">>", "<", "<<")
SHELL_KEYWORDS = {
    "case",
    "do",
    "done",
    "else",
    "esac",
    "fi",
    "for",
    "function",
    "if",
    "then",
    "until",
    "while",
}


def sanitize_text(text: str, limit: int = 180) -> tuple[str, bool]:
    """Redact secrets and truncate evidence for reports and audit logs."""
    if text is None:
        return "", False

    sanitized = str(text)
    changed = False
    patterns = [
        (r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", "[REDACTED_PRIVATE_KEY]"),
        (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "[REDACTED_PRIVATE_KEY]"),
        (r"-----END [A-Z ]*PRIVATE KEY-----", "[REDACTED_PRIVATE_KEY]"),
        (
            r"(?i)(['\"])([A-Z0-9_]*(?:API[_-]?KEY|TOKEN|SECRET|PASSWORD|PASSWD|PRIVATE[_-]?KEY|SSH[_-]?KEY)"
            r"[A-Z0-9_]*)\1",
            r"\1[REDACTED_SECRET_NAME]\1",
        ),
        (
            r"(?i)\b(api[_-]?key|auth[_-]?token|token|secret|password|passwd|credential|private[_-]?key)"
            r"\b\s*[:=]\s*['\"]?[^'\"\s,;)]+",
            r"\1=[REDACTED_SECRET]",
        ),
        (r"(?i)\bBearer\s+[^'\"\s,;)]+", "Bearer [REDACTED_SECRET]"),
        (r"\b[A-Za-z0-9_/\-+=]{32,}\b", "[REDACTED_SECRET]"),
    ]
    for pattern, replacement in patterns:
        updated = re.sub(pattern, replacement, sanitized, flags=re.DOTALL)
        if updated != sanitized:
            changed = True
            sanitized = updated

    sanitized = sanitized.replace("\n", "\\n")
    if len(sanitized) > limit:
        sanitized = sanitized[:limit - 3] + "..."
        changed = True
    return sanitized, changed


def scan_text_patterns(script: str, policy: ToolSafetyPolicy, language: str) -> list[RiskFinding]:
    """Scan targeted text patterns that are useful even when parsing fails."""
    findings: list[RiskFinding] = []
    lines = script.splitlines()
    for line_no, line in enumerate(lines, start=1):
        if "-----BEGIN" in line and "PRIVATE KEY" in line:
            findings.append(
                _finding(
                    "PRIVATE_KEY_LITERAL",
                    "secret_literal",
                    RiskLevel.CRITICAL,
                    Decision.DENY,
                    line,
                    "Remove embedded private keys and load credentials from a secured secret manager.",
                    "Private key material appears in script text.",
                    line_no,
                ))
        if language.startswith("python") and re.search(r"\b(eval|exec|compile)\s*\(", line):
            findings.append(
                _finding(
                    "PY_DYNAMIC_CODE_TEXT",
                    "dynamic_code",
                    RiskLevel.MEDIUM,
                    Decision.NEEDS_HUMAN_REVIEW,
                    line,
                    "Avoid dynamic code execution or review the code path before running it.",
                    "Dynamic code execution appears in script text.",
                    line_no,
                ))
    return findings


def scan_python_script(script: str, policy: ToolSafetyPolicy) -> list[RiskFinding]:
    """Scan a Python script using AST plus targeted text fallback."""
    findings = scan_text_patterns(script, policy, "python")
    try:
        tree = ast.parse(script)
    except SyntaxError as exc:
        line = script.splitlines()[exc.lineno - 1] if exc.lineno and exc.lineno <= len(script.splitlines()) else ""
        findings.append(
            _finding(
                "PY_PARSE_ERROR",
                "parse_error",
                RiskLevel.LOW,
                Decision.NEEDS_HUMAN_REVIEW,
                line or str(exc),
                "Review unparsable Python before execution.",
                "Python parser could not parse this script.",
                exc.lineno,
                exc.offset,
            ))
        return findings

    visitor = _PythonSafetyVisitor(script, policy)
    visitor.visit(tree)
    findings.extend(visitor.findings)
    return _dedupe_findings(findings)


def scan_bash_script(script: str, policy: ToolSafetyPolicy) -> list[RiskFinding]:
    """Scan Bash or POSIX shell text without executing it."""
    findings: list[RiskFinding] = []
    for line_no, raw_line in enumerate(script.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        tokens = _shell_tokens(line)
        sensitive_read = _line_reads_sensitive_file(line, tokens, policy)
        network_send = _line_has_network_send(line)
        inline_script = _shell_inline_interpreter_script(tokens)
        if inline_script:
            findings.extend(scan_bash_script(inline_script, policy))

        if _is_fork_bomb(line):
            findings.append(
                _finding(
                    "BASH_FORK_BOMB",
                    "resource_exhaustion",
                    RiskLevel.CRITICAL,
                    Decision.DENY,
                    raw_line,
                    "Do not run fork bombs or recursive shell functions.",
                    "Fork bomb pattern detected.",
                    line_no,
                ))

        if _is_rm_rf_dangerous(tokens, policy):
            findings.append(
                _finding(
                    "BASH_DANGEROUS_RM_RF",
                    "dangerous_delete",
                    RiskLevel.CRITICAL,
                    Decision.DENY,
                    raw_line,
                    "Remove recursive force deletion of root, home, or denied paths.",
                    "Dangerous recursive delete detected.",
                    line_no,
                ))

        if sensitive_read:
            findings.append(
                _finding(
                    "BASH_SENSITIVE_FILE_READ",
                    "secret_read",
                    RiskLevel.HIGH,
                    Decision.DENY,
                    raw_line,
                    "Avoid reading denied credential or environment files in tool scripts.",
                    "Sensitive file read detected.",
                    line_no,
                ))

        if _redirects_to_denied_path(line, tokens, policy):
            findings.append(
                _finding(
                    "BASH_DENIED_PATH_WRITE",
                    "denied_path_write",
                    RiskLevel.CRITICAL,
                    Decision.DENY,
                    raw_line,
                    "Do not redirect or write to denied system or credential paths.",
                    "Write or redirect to denied path detected.",
                    line_no,
                ))

        if sensitive_read and network_send:
            findings.append(
                _finding(
                    "BASH_SECRET_EXFILTRATION",
                    "secret_exfiltration",
                    RiskLevel.CRITICAL,
                    Decision.DENY,
                    raw_line,
                    "Do not pipe secrets to network clients.",
                    "Sensitive file content is piped to a network command.",
                    line_no,
                ))

        if _is_find_delete(tokens):
            findings.append(
                _finding(
                    "BASH_FIND_DELETE_REVIEW",
                    "dangerous_delete",
                    RiskLevel.MEDIUM,
                    Decision.NEEDS_HUMAN_REVIEW,
                    raw_line,
                    "Review find -delete targets before execution.",
                    "find -delete can remove many files.",
                    line_no,
                ))

        if _is_xargs_rm_rf(line):
            findings.append(
                _finding(
                    "BASH_XARGS_RM_REVIEW",
                    "dangerous_delete",
                    RiskLevel.MEDIUM,
                    Decision.NEEDS_HUMAN_REVIEW,
                    raw_line,
                    "Review xargs-driven recursive deletion before execution.",
                    "xargs rm -rf uses dynamic deletion targets.",
                    line_no,
                ))

        network_findings = _network_findings(line, policy, raw_line, line_no)
        findings.extend(network_findings)

        if _is_dependency_install(tokens) and policy.deny_dependency_install:
            findings.append(
                _finding(
                    "BASH_DEPENDENCY_INSTALL",
                    "dependency_install",
                    RiskLevel.HIGH,
                    Decision.DENY,
                    raw_line,
                    "Preinstall dependencies through a reviewed build step instead of tool script execution.",
                    "Dependency installation command detected.",
                    line_no,
                ))

        if _is_privilege_escalation(tokens, line) and policy.deny_privilege_escalation:
            findings.append(
                _finding(
                    "BASH_PRIVILEGE_ESCALATION",
                    "privilege_escalation",
                    RiskLevel.HIGH,
                    Decision.DENY,
                    raw_line,
                    "Remove sudo, su, world-writable permissions, or root ownership changes.",
                    "Privilege escalation or unsafe permission change detected.",
                    line_no,
                ))

        if _has_background_process(line):
            findings.append(
                _finding(
                    "BASH_BACKGROUND_PROCESS",
                    "process_control",
                    RiskLevel.MEDIUM,
                    Decision.NEEDS_HUMAN_REVIEW,
                    raw_line,
                    "Review background processes and ensure they are bounded and observable.",
                    "Background process operator detected.",
                    line_no,
                ))

        if _is_unbounded_output(tokens):
            findings.append(
                _finding(
                    "BASH_UNBOUNDED_OUTPUT",
                    "resource_exhaustion",
                    RiskLevel.MEDIUM,
                    Decision.NEEDS_HUMAN_REVIEW,
                    raw_line,
                    "Bound commands that can produce unbounded output before execution.",
                    "Unbounded output command detected.",
                    line_no,
                ))

        if _is_zero_fill_write(tokens):
            findings.append(
                _finding(
                    "BASH_ZERO_FILL_WRITE_REVIEW",
                    "resource_exhaustion",
                    RiskLevel.MEDIUM,
                    Decision.NEEDS_HUMAN_REVIEW,
                    raw_line,
                    "Review large writes from /dev/zero and enforce size limits.",
                    "Potentially large zero-fill write detected.",
                    line_no,
                ))

        if _has_shell_operator(line) and policy.review_shell_features:
            findings.append(
                _finding(
                    "BASH_SHELL_FEATURES_REVIEW",
                    "shell_features",
                    RiskLevel.LOW,
                    Decision.NEEDS_HUMAN_REVIEW,
                    raw_line,
                    "Review shell operators, pipes, command substitution, and redirection before execution.",
                    "Shell operator or redirection detected.",
                    line_no,
                ))

        if _is_long_sleep(tokens, policy.long_sleep_seconds):
            findings.append(
                _finding(
                    "BASH_LONG_SLEEP",
                    "resource_wait",
                    RiskLevel.MEDIUM,
                    Decision.NEEDS_HUMAN_REVIEW,
                    raw_line,
                    "Reduce long sleeps or enforce an explicit timeout.",
                    "Sleep duration exceeds policy threshold.",
                    line_no,
                ))

        if re.search(r"\b(while|until)\s+true\b", line, flags=re.IGNORECASE):
            findings.append(
                _finding(
                    "BASH_INFINITE_LOOP",
                    "resource_exhaustion",
                    RiskLevel.MEDIUM,
                    Decision.NEEDS_HUMAN_REVIEW,
                    raw_line,
                    "Add an exit condition and a timeout before running the loop.",
                    "Unbounded shell loop detected.",
                    line_no,
                ))

        for command in _base_commands(line):
            if command in SHELL_KEYWORDS or "=" in command:
                continue
            if command in NETWORK_COMMANDS and not network_findings:
                continue
            if command and not policy.is_command_allowed(command):
                findings.append(
                    _finding(
                        "BASH_UNKNOWN_COMMAND_REVIEW",
                        "unknown_command",
                        RiskLevel.LOW,
                        Decision.NEEDS_HUMAN_REVIEW,
                        raw_line,
                        "Add reviewed commands to allowed_commands or inspect this command before execution.",
                        f"Command '{command}' is not in allowed_commands.",
                        line_no,
                    ))
    return _suppress_low_value_unknown_command_reviews(_dedupe_findings(findings))


class _PythonSafetyVisitor(ast.NodeVisitor):
    """AST visitor implementing deterministic Python safety rules."""

    def __init__(self, script: str, policy: ToolSafetyPolicy) -> None:
        self.script = script
        self.lines = script.splitlines()
        self.policy = policy
        self.aliases: dict[str, str] = {}
        self.constants: dict[str, str] = {}
        self.request_urls: dict[str, str | None] = {}
        self.sensitive_vars: set[str] = set()
        self.findings: list[RiskFinding] = []

    def visit_Import(self, node: ast.Import) -> Any:
        for alias in node.names:
            local = alias.asname or alias.name.split(".", 1)[0]
            self.aliases[local] = alias.name
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> Any:
        if not node.module:
            return self.generic_visit(node)
        for alias in node.names:
            local = alias.asname or alias.name
            if node.module == "pathlib" and alias.name == "Path":
                self.aliases[local] = "pathlib.Path"
            elif node.module == "subprocess":
                self.aliases[local] = f"subprocess.{alias.name}"
            elif node.module == "urllib.request":
                self.aliases[local] = f"urllib.request.{alias.name}"
            elif node.module in {"requests", "httpx", "aiohttp"}:
                self.aliases[local] = f"{node.module}.{alias.name}"
            else:
                self.aliases[local] = f"{node.module}.{alias.name}"
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> Any:
        value = self._resolve_string(node.value)
        sensitive = self._is_sensitive_source(node.value)
        request_url = self._request_url_assignment(node.value)
        if value is not None:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.constants[target.id] = value
        for target in node.targets:
            if isinstance(target, ast.Name):
                if sensitive:
                    self.sensitive_vars.add(target.id)
                if request_url[0]:
                    self.request_urls[target.id] = request_url[1]
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> Any:
        value = self._resolve_string(node.value) if node.value else None
        if value is not None and isinstance(node.target, ast.Name):
            self.constants[node.target.id] = value
        if node.value and isinstance(node.target, ast.Name):
            if self._is_sensitive_source(node.value):
                self.sensitive_vars.add(node.target.id)
            request_url = self._request_url_assignment(node.value)
            if request_url[0]:
                self.request_urls[node.target.id] = request_url[1]
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> Any:
        if isinstance(node.value, str) and "PRIVATE KEY" in node.value and "BEGIN" in node.value:
            self.findings.append(
                self._finding(
                    "PRIVATE_KEY_LITERAL",
                    "secret_literal",
                    RiskLevel.CRITICAL,
                    Decision.DENY,
                    node.value,
                    "Remove embedded private keys and load credentials from a secured secret manager.",
                    "Private key material appears in a string literal.",
                    node,
                ))
        self.generic_visit(node)

    def visit_While(self, node: ast.While) -> Any:
        if self._is_static_truthy(node.test):
            self.findings.append(
                self._finding(
                    "PY_INFINITE_LOOP",
                    "resource_exhaustion",
                    RiskLevel.MEDIUM,
                    Decision.NEEDS_HUMAN_REVIEW,
                    self._line(node),
                    "Add an exit condition and enforce a timeout.",
                    "Unbounded while True loop detected.",
                    node,
                ))
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> Any:
        name = self._call_name(node.func)
        self._check_sensitive_file_read(node, name)
        self._check_dangerous_delete(node, name)
        self._check_network(node, name)
        self._check_process_execution(node, name)
        self._check_dynamic_code(node, name)
        self._check_sleep(node, name)
        self._check_large_allocation(node, name)
        self._check_sensitive_output(node, name)
        self.generic_visit(node)

    def _check_sensitive_file_read(self, node: ast.Call, name: str) -> None:
        path = None
        if name in {"open", "io.open", "builtins.open"} and node.args:
            path = self._resolve_string(node.args[0])
        elif isinstance(node.func, ast.Attribute) and node.func.attr in {"read_text", "read_bytes", "open"}:
            path = self._path_from_constructor(node.func.value)
        if path and self.policy.is_path_denied(path):
            self.findings.append(
                self._finding(
                    "PY_SENSITIVE_FILE_READ",
                    "secret_read",
                    RiskLevel.HIGH,
                    Decision.DENY,
                    self._line(node),
                    "Avoid reading denied credential or environment files in tool scripts.",
                    "Sensitive file read detected.",
                    node,
                ))

    def _check_dangerous_delete(self, node: ast.Call, name: str) -> None:
        delete_calls = {
            "os.remove",
            "os.unlink",
            "os.rmdir",
            "shutil.rmtree",
            "pathlib.Path.unlink",
            "pathlib.Path.rmdir",
        }
        path = None
        if name in delete_calls and node.args:
            path = self._resolve_string(node.args[0])
        elif isinstance(node.func, ast.Attribute) and node.func.attr in {"unlink", "rmdir"}:
            path = self._path_from_constructor(node.func.value)
        if path and self.policy.is_path_denied(path):
            self.findings.append(
                self._finding(
                    "PY_DANGEROUS_DELETE",
                    "dangerous_delete",
                    RiskLevel.CRITICAL,
                    Decision.DENY,
                    self._line(node),
                    "Remove deletion of root, system, or credential paths.",
                    "Deletion call targets a denied path.",
                    node,
                ))
        elif path is None and self._is_delete_call(node, name):
            self.findings.append(
                self._finding(
                    "PY_DYNAMIC_DELETE_REVIEW",
                    "dangerous_delete",
                    RiskLevel.MEDIUM,
                    Decision.NEEDS_HUMAN_REVIEW,
                    self._line(node),
                    "Review dynamic deletion targets before execution.",
                    "Deletion call target is dynamic or unknown.",
                    node,
                ))

    def _check_network(self, node: ast.Call, name: str) -> None:
        is_http = self._is_python_http_call(name)
        if not is_http and name not in {"socket.socket", "socket.create_connection"}:
            return
        if name == "socket.socket":
            self.findings.append(
                self._finding(
                    "PY_SOCKET_REVIEW",
                    "network_access",
                    RiskLevel.MEDIUM,
                    Decision.NEEDS_HUMAN_REVIEW,
                    self._line(node),
                    "Review raw socket usage before execution.",
                    "Raw socket usage detected.",
                    node,
                ))
            return
        if name == "socket.create_connection":
            host = self._socket_create_connection_host(node)
            self._record_network_host(node, host, "PY_SOCKET_NON_WHITELIST", "PY_SOCKET_DYNAMIC_REVIEW")
            return

        url = self._network_url(node, name)
        host = urlparse(url).hostname if url else None
        self._record_network_host(node, host, "PY_NETWORK_NON_WHITELIST", "PY_DYNAMIC_NETWORK_REVIEW")

    def _check_process_execution(self, node: ast.Call, name: str) -> None:
        is_process = (name in {"os.system", "os.popen"} or name.startswith("subprocess.")
                      or name in {"subprocess.run", "subprocess.call", "subprocess.check_call", "subprocess.Popen"})
        if not is_process:
            return

        parts = self._command_sequence_from_process_call(node)
        command = None if parts else self._command_from_process_call(node)
        if parts:
            self.findings.extend(scan_bash_script(shlex.join(parts), self.policy))
            inline_script = _inline_interpreter_script(parts)
            if inline_script:
                language, script = inline_script
                if language == "python":
                    self.findings.extend(scan_python_script(script, self.policy))
                else:
                    self.findings.extend(scan_bash_script(script, self.policy))
        elif command:
            self.findings.extend(scan_bash_script(command, self.policy))

        if self._keyword_bool(node, "shell") and command is None:
            self.findings.append(
                self._finding(
                    "PY_SHELL_TRUE_DYNAMIC",
                    "process_execution",
                    RiskLevel.MEDIUM,
                    Decision.NEEDS_HUMAN_REVIEW,
                    self._line(node),
                    "Avoid shell=True with dynamic commands or review the command construction.",
                    "Dynamic shell=True subprocess command detected.",
                    node,
                ))

        if self.policy.review_process_execution:
            self.findings.append(
                self._finding(
                    "PY_PROCESS_EXECUTION_REVIEW",
                    "process_execution",
                    RiskLevel.MEDIUM,
                    Decision.NEEDS_HUMAN_REVIEW,
                    self._line(node),
                    "Review subprocess or shell execution before running the script.",
                    "Process execution call detected.",
                    node,
                ))

    def _check_dynamic_code(self, node: ast.Call, name: str) -> None:
        if name in {"eval", "exec", "compile", "__import__", "builtins.eval", "builtins.exec", "builtins.compile"}:
            if self.policy.review_dynamic_code:
                self.findings.append(
                    self._finding(
                        "PY_DYNAMIC_CODE_REVIEW",
                        "dynamic_code",
                        RiskLevel.MEDIUM,
                        Decision.NEEDS_HUMAN_REVIEW,
                        self._line(node),
                        "Avoid dynamic code execution or review the code path before running it.",
                        "Dynamic code execution detected.",
                        node,
                    ))

    def _check_sleep(self, node: ast.Call, name: str) -> None:
        if name != "time.sleep" or not node.args:
            return
        seconds = self._resolve_number(node.args[0])
        if seconds is not None and seconds > self.policy.long_sleep_seconds:
            self.findings.append(
                self._finding(
                    "PY_LONG_SLEEP",
                    "resource_wait",
                    RiskLevel.MEDIUM,
                    Decision.NEEDS_HUMAN_REVIEW,
                    self._line(node),
                    "Reduce long sleeps or enforce an explicit timeout.",
                    "Sleep duration exceeds policy threshold.",
                    node,
                ))

    def _check_large_allocation(self, node: ast.Call, name: str) -> None:
        if not node.args:
            return
        size = self._resolve_number(node.args[0])
        if size is None:
            return
        if name in {"bytearray", "bytes"} and size > LARGE_ALLOCATION_BYTES:
            self.findings.append(
                self._finding(
                    "PY_LARGE_ALLOCATION_REVIEW",
                    "resource_exhaustion",
                    RiskLevel.MEDIUM,
                    Decision.NEEDS_HUMAN_REVIEW,
                    self._line(node),
                    "Review large memory allocations and enforce resource limits.",
                    "Large in-memory allocation detected.",
                    node,
                ))
        elif name == "range" and size > LARGE_ITERATION_COUNT:
            self.findings.append(
                self._finding(
                    "PY_LARGE_ITERATION_REVIEW",
                    "resource_exhaustion",
                    RiskLevel.MEDIUM,
                    Decision.NEEDS_HUMAN_REVIEW,
                    self._line(node),
                    "Review very large loops and enforce a timeout.",
                    "Large iteration range detected.",
                    node,
                ))

    def _check_sensitive_output(self, node: ast.Call, name: str) -> None:
        output_call = (name == "print" or name.startswith(("logging.", "logger.")) or name.endswith(
            (".info", ".warning", ".error")))
        write_call = name.endswith((".write", ".writelines", ".send", ".sendall", ".post", ".put"))
        network_sink = self._is_python_http_call(name)
        if not (output_call or write_call or network_sink):
            return
        keyword_values = [keyword.value for keyword in node.keywords]
        if any(self._node_mentions_secret(arg) for arg in [*node.args, *keyword_values]):
            self.findings.append(
                self._finding(
                    "PY_SENSITIVE_OUTPUT",
                    "secret_output",
                    RiskLevel.HIGH,
                    Decision.DENY,
                    self._line(node),
                    "Do not print, log, write, or send variables that contain credentials or tokens.",
                    "Sensitive variable may be written to output, file, or network.",
                    node,
                ))

    def _is_python_http_call(self, name: str) -> bool:
        last = name.rsplit(".", 1)[-1]
        return name.startswith(("requests.", "httpx.", "aiohttp.", "urllib.request.")) and last in PY_NETWORK_METHODS

    def _network_url(self, node: ast.Call, name: str) -> str | None:
        url_node = node.args[0] if node.args else None
        for keyword in node.keywords:
            if keyword.arg == "url":
                url_node = keyword.value
        if name.endswith(".urlopen") and isinstance(url_node, ast.Name) and url_node.id in self.request_urls:
            return self.request_urls[url_node.id]
        return self._resolve_string(url_node) if url_node is not None else None

    def _record_network_host(
        self,
        node: ast.Call,
        host: str | None,
        deny_rule_id: str,
        review_rule_id: str,
    ) -> None:
        if host and self.policy.is_domain_allowed(host):
            return
        if host:
            self.findings.append(
                self._finding(
                    deny_rule_id,
                    "network_access",
                    RiskLevel.HIGH,
                    Decision.DENY,
                    self._line(node),
                    "Use only policy allowed_domains or remove outbound network access.",
                    f"Network request to non-whitelisted host '{host}'.",
                    node,
                ))
        elif self.policy.review_unknown_network:
            self.findings.append(
                self._finding(
                    review_rule_id,
                    "network_access",
                    RiskLevel.MEDIUM,
                    Decision.NEEDS_HUMAN_REVIEW,
                    self._line(node),
                    "Review dynamic URLs or constrain them to allowed_domains.",
                    "Network request target is dynamic or missing.",
                    node,
                ))

    def _socket_create_connection_host(self, node: ast.Call) -> str | None:
        if not node.args:
            return None
        address = node.args[0]
        if isinstance(address, (ast.Tuple, ast.List)) and address.elts:
            return self._resolve_string(address.elts[0])
        return self._resolve_string(address)

    def _is_delete_call(self, node: ast.Call, name: str) -> bool:
        if name in {"os.remove", "os.unlink", "os.rmdir", "shutil.rmtree"}:
            return True
        return isinstance(node.func, ast.Attribute) and node.func.attr in {"unlink", "rmdir"}

    def _request_url_assignment(self, node: ast.AST) -> tuple[bool, str | None]:
        if not isinstance(node, ast.Call):
            return False, None
        name = self._call_name(node.func)
        if name not in {"urllib.request.Request", "Request"}:
            return False, None
        url_node = node.args[0] if node.args else None
        for keyword in node.keywords:
            if keyword.arg == "url":
                url_node = keyword.value
        return True, self._resolve_string(url_node) if url_node is not None else None

    def _is_sensitive_source(self, node: ast.AST) -> bool:
        if isinstance(node, ast.Name):
            return node.id in self.sensitive_vars
        if isinstance(node, ast.Subscript):
            name = self._call_name(node.value)
            key = self._subscript_key(node)
            if name == "os.environ" and key and _contains_sensitive_key(key):
                return True
        if isinstance(node, ast.Call):
            name = self._call_name(node.func)
            if name == "os.getenv" and node.args:
                key = self._resolve_string(node.args[0])
                return bool(key and _contains_sensitive_key(key))
            sensitive_path = self._sensitive_path_from_read_call(node, name)
            if sensitive_path and self.policy.is_path_denied(sensitive_path):
                return True
        return any(self._is_sensitive_source(child) for child in ast.iter_child_nodes(node))

    def _sensitive_path_from_read_call(self, node: ast.Call, name: str) -> str | None:
        if name in {"open", "io.open", "builtins.open"} and node.args:
            return self._resolve_string(node.args[0])
        if isinstance(node.func, ast.Attribute) and node.func.attr in {"read", "read_text", "read_bytes"}:
            if isinstance(node.func.value, ast.Call):
                value_name = self._call_name(node.func.value.func)
                if value_name in {"open", "io.open", "builtins.open"} and node.func.value.args:
                    return self._resolve_string(node.func.value.args[0])
            return self._path_from_constructor(node.func.value)
        return None

    def _subscript_key(self, node: ast.Subscript) -> str | None:
        slice_node = node.slice
        if isinstance(slice_node, ast.Constant) and isinstance(slice_node.value, str):
            return slice_node.value
        return None

    def _command_from_process_call(self, node: ast.Call) -> str | None:
        if not node.args:
            return None
        arg = node.args[0]
        text = self._resolve_string(arg)
        if text is not None:
            return text
        return None

    def _command_sequence_from_process_call(self, node: ast.Call) -> list[str] | None:
        if not node.args:
            return None
        return self._resolve_string_sequence(node.args[0])

    def _path_from_constructor(self, node: ast.AST) -> str | None:
        path = self._path_from_path_expr(node)
        if path is not None:
            return path
        return None

    def _path_from_path_expr(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Call):
            name = self._call_name(node.func)
            if name in {"Path", "pathlib.Path"} and node.args:
                return self._resolve_string(node.args[0])
            if name in {"Path.home", "pathlib.Path.home"}:
                return "~"
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            left = self._path_from_path_expr(node.left)
            right = self._resolve_string(node.right)
            if left is not None and right is not None:
                return f"{left.rstrip('/')}/{right.strip('/')}"
        return None

    def _call_name(self, node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return self.aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            base = self._call_name(node.value)
            return f"{base}.{node.attr}" if base else node.attr
        if isinstance(node, ast.Call):
            return self._call_name(node.func)
        return ""

    def _resolve_string(self, node: ast.AST | None) -> str | None:
        if node is None:
            return None
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name):
            return self.constants.get(node.id)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left = self._resolve_string(node.left)
            right = self._resolve_string(node.right)
            if left is not None and right is not None:
                return left + right
        if isinstance(node, ast.JoinedStr):
            pieces: list[str] = []
            for value in node.values:
                if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
                    return None
                pieces.append(value.value)
            return "".join(pieces)
        return None

    def _resolve_string_sequence(self, node: ast.AST) -> list[str] | None:
        if isinstance(node, (ast.List, ast.Tuple)):
            parts: list[str] = []
            for item in node.elts:
                value = self._resolve_string(item)
                if value is None:
                    return None
                parts.append(value)
            return parts
        return None

    def _resolve_number(self, node: ast.AST) -> float | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.BinOp):
            left = self._resolve_number(node.left)
            right = self._resolve_number(node.right)
            if left is None or right is None:
                return None
            try:
                if isinstance(node.op, ast.Add):
                    return left + right
                if isinstance(node.op, ast.Sub):
                    return left - right
                if isinstance(node.op, ast.Mult):
                    return left * right
                if isinstance(node.op, ast.Div):
                    return left / right
                if isinstance(node.op, ast.Pow) and abs(right) <= 12:
                    return left**right
            except OverflowError:
                return float("inf")
        return None

    def _is_static_truthy(self, node: ast.AST) -> bool:
        if isinstance(node, ast.Constant):
            return bool(node.value)
        return False

    def _keyword_bool(self, node: ast.Call, key: str) -> bool:
        for keyword in node.keywords:
            if keyword.arg == key and isinstance(keyword.value, ast.Constant):
                return bool(keyword.value.value)
        return False

    def _node_mentions_secret(self, node: ast.AST) -> bool:
        if isinstance(node, ast.Name):
            return node.id in self.sensitive_vars or _contains_sensitive_word(node.id)
        if isinstance(node, ast.Attribute):
            return _contains_sensitive_word(node.attr) or self._node_mentions_secret(node.value)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return _contains_sensitive_word(node.value)
        if isinstance(node, (ast.BinOp, ast.JoinedStr, ast.Call, ast.Subscript)):
            return any(self._node_mentions_secret(child) for child in ast.iter_child_nodes(node))
        return False

    def _line(self, node: ast.AST) -> str:
        lineno = getattr(node, "lineno", None)
        if lineno and 1 <= lineno <= len(self.lines):
            return self.lines[lineno - 1].strip()
        return ""

    def _finding(
        self,
        rule_id: str,
        risk_type: str,
        risk_level: RiskLevel,
        decision: Decision,
        evidence: str,
        recommendation: str,
        message: str,
        node: ast.AST,
    ) -> RiskFinding:
        return _finding(
            rule_id,
            risk_type,
            risk_level,
            decision,
            evidence,
            recommendation,
            message,
            getattr(node, "lineno", None),
            getattr(node, "col_offset", None),
        )


def _finding(
    rule_id: str,
    risk_type: str,
    risk_level: RiskLevel,
    decision: Decision,
    evidence: str,
    recommendation: str,
    message: str,
    line: int | None = None,
    column: int | None = None,
) -> RiskFinding:
    evidence_text, sanitized = sanitize_text(evidence)
    return RiskFinding(
        rule_id=rule_id,
        risk_type=risk_type,
        risk_level=risk_level,
        decision=decision,
        evidence=evidence_text,
        recommendation=recommendation,
        message=message,
        line=line,
        column=column,
        metadata={"sanitized": sanitized} if sanitized else {},
    )


def _contains_sensitive_word(text: str) -> bool:
    lowered = str(text).lower()
    return any(word in lowered for word in SENSITIVE_WORDS)


def _contains_sensitive_key(text: str) -> bool:
    lowered = str(text).lower()
    if any(word in lowered for word in ("api_key", "apikey", "private_key", "ssh_key")):
        return True
    return bool(re.search(r"(^|[_\-.])(key|token|secret|password|passwd)($|[_\-.])", lowered))


def _shell_tokens(line: str) -> list[str]:
    try:
        return shlex.split(line, comments=True, posix=True)
    except ValueError:
        return line.split()


def _base_commands(line: str) -> list[str]:
    try:
        lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        tokens = line.split()

    commands: list[str] = []
    expect_command = True
    for token in tokens:
        if token in {"|", ";", "&&", "||", "&"}:
            expect_command = True
            continue
        if token in {">", ">>", "<", "<<", "2>", "2>>"}:
            expect_command = False
            continue
        if expect_command:
            command = token.strip()
            if command:
                commands.append(command.split("/")[-1].lower())
            expect_command = False
    return commands


def _line_reads_sensitive_file(line: str, tokens: list[str], policy: ToolSafetyPolicy) -> bool:
    if not tokens:
        return False
    for token in tokens[1:]:
        if token.startswith("@") and policy.is_path_denied(token[1:]):
            return True
    command = tokens[0].split("/")[-1]
    if command in {"cat", "head", "tail", "less", "more"}:
        return any(policy.is_path_denied(token) for token in tokens[1:])
    if command == "grep":
        return any(policy.is_path_denied(token)
                   for token in tokens[1:]) or (any(_contains_sensitive_word(token)
                                                    for token in tokens[1:]) and any(".env" in token
                                                                                     for token in tokens[1:]))
    return bool(re.search(r"\b(cat|grep|head|tail)\b.*(\.env|id_rsa|id_dsa|\.pem|\.key|/etc/passwd|/etc/shadow)", line))


def _line_has_network_send(line: str) -> bool:
    return bool(re.search(r"\b(curl|wget|nc|netcat|socat|ssh|scp|rsync|openssl)\b|/dev/tcp/", line))


def _is_rm_rf_dangerous(tokens: list[str], policy: ToolSafetyPolicy) -> bool:
    if not tokens or tokens[0].split("/")[-1] != "rm":
        return False
    flags = [token for token in tokens[1:] if token.startswith("-")]
    targets = [token for token in tokens[1:] if not token.startswith("-")]
    recursive = any("r" in flag for flag in flags)
    force = any("f" in flag for flag in flags)
    if not (recursive and force):
        return False
    return any(target in {"/", "~"} or target.startswith("~/.ssh") or policy.is_path_denied(target)
               for target in targets)


def _is_find_delete(tokens: list[str]) -> bool:
    return bool(tokens and tokens[0].split("/")[-1] == "find" and "-delete" in tokens[1:])


def _is_xargs_rm_rf(line: str) -> bool:
    return bool(re.search(r"\bxargs\b[^\n|;&]*\brm\b[^\n|;&]*-[^\n|;&]*r[^\n|;&]*f", line))


def _shell_inline_interpreter_script(tokens: list[str]) -> str | None:
    if not tokens:
        return None
    command = tokens[0].split("/")[-1].lower()
    if command not in {"bash", "sh"}:
        return None
    index = _option_value_index(tokens, {"-c", "-lc"})
    return tokens[index] if index is not None else None


def _redirects_to_denied_path(line: str, tokens: list[str], policy: ToolSafetyPolicy) -> bool:
    for match in re.finditer(r"(?:^|\s)(?:[0-9]?>{1,2})\s*([^&\s]+)", line):
        if policy.is_path_denied(match.group(1)):
            return True
    if tokens and tokens[0].split("/")[-1] == "tee":
        return any(policy.is_path_denied(token) for token in tokens[1:] if not token.startswith("-"))
    return False


def _network_findings(line: str, policy: ToolSafetyPolicy, raw_line: str, line_no: int) -> list[RiskFinding]:
    findings: list[RiskFinding] = []
    tokens = _shell_tokens(line)
    if not _line_has_network_send(line):
        return findings

    targets = _network_targets(line, tokens)
    if not targets and policy.review_unknown_network:
        findings.append(
            _finding(
                "BASH_DYNAMIC_NETWORK_REVIEW",
                "network_access",
                RiskLevel.MEDIUM,
                Decision.NEEDS_HUMAN_REVIEW,
                raw_line,
                "Review dynamic network targets or constrain them to allowed_domains.",
                "Network command target is dynamic or missing.",
                line_no,
            ))
    for host in targets:
        if host is None:
            if policy.review_unknown_network:
                findings.append(
                    _finding(
                        "BASH_DYNAMIC_NETWORK_REVIEW",
                        "network_access",
                        RiskLevel.MEDIUM,
                        Decision.NEEDS_HUMAN_REVIEW,
                        raw_line,
                        "Review dynamic network targets or constrain them to allowed_domains.",
                        "Network command target is dynamic or missing.",
                        line_no,
                    ))
            continue
        if not policy.is_domain_allowed(host):
            findings.append(
                _finding(
                    "BASH_NETWORK_NON_WHITELIST",
                    "network_access",
                    RiskLevel.HIGH,
                    Decision.DENY,
                    raw_line,
                    "Use only policy allowed_domains or remove outbound network access.",
                    f"Network request to non-whitelisted host '{host}'.",
                    line_no,
                ))
    return findings


def _network_targets(line: str, tokens: list[str]) -> list[str | None]:
    targets: list[str | None] = []
    for url in re.findall(r"https?://[^\s'\"`]+", line):
        targets.append(_clean_host(urlparse(url).hostname))

    for host in re.findall(r"/dev/tcp/([^/\s]+)/\S+", line):
        targets.append(_literal_or_dynamic_host(host))

    for match in re.finditer(r"\b(?:nc|netcat)\s+([^\s|;&]+)", line):
        host = match.group(1)
        if host.startswith("-") or host.isdigit():
            continue
        targets.append(_literal_or_dynamic_host(host))

    for host in re.findall(r"(?:tcp|udp|ssl|openssl):([^,:\s]+)", line, flags=re.IGNORECASE):
        targets.append(_literal_or_dynamic_host(host))

    for match in re.finditer(r"\bopenssl\s+s_client\b.*?\s-connect\s+([^\s|;&]+)", line):
        targets.append(_host_from_hostport(match.group(1)))

    for match in re.finditer(r"\bssh\s+(?:-[^\s]+\s+(?:[^\s]+\s+)*)?([^\s|;&]+)", line):
        targets.append(_literal_or_dynamic_host(match.group(1).rsplit("@", 1)[-1]))

    for match in re.finditer(r"\b(?:scp|rsync)\b[^\n|;&]*?(?:[^@\s:]+@)?([^:\s]+):", line):
        targets.append(_literal_or_dynamic_host(match.group(1)))

    if not tokens:
        return targets

    command = tokens[0].split("/")[-1].lower()
    if command in {"nc", "netcat"}:
        targets.append(_first_network_arg(tokens[1:]))
    elif command == "socat":
        return [target for target in targets if target != ""]
    elif command == "ssh":
        targets.append(_ssh_host(tokens[1:]))
    elif command in {"scp", "rsync"}:
        targets.extend(_remote_copy_hosts(tokens[1:]))
    elif command == "openssl" and "s_client" in [token.lower() for token in tokens]:
        for index, token in enumerate(tokens):
            if token == "-connect" and index + 1 < len(tokens):
                targets.append(_host_from_hostport(tokens[index + 1]))
    return [target for target in targets if target != ""]


def _first_network_arg(args: list[str]) -> str | None:
    skip_next = False
    for token in args:
        if skip_next:
            skip_next = False
            continue
        if token in {"-w", "-q", "-i", "-p"}:
            skip_next = True
            continue
        if token.startswith("-") or token.isdigit():
            continue
        return _literal_or_dynamic_host(token)
    return None


def _ssh_host(args: list[str]) -> str | None:
    skip_next = False
    for token in args:
        if skip_next:
            skip_next = False
            continue
        if token in {"-i", "-p", "-l", "-o"}:
            skip_next = True
            continue
        if token.startswith("-"):
            continue
        return _literal_or_dynamic_host(token.rsplit("@", 1)[-1])
    return None


def _remote_copy_hosts(args: list[str]) -> list[str | None]:
    hosts: list[str | None] = []
    for token in args:
        if token.startswith("-"):
            continue
        match = re.match(r"(?:[^@\s:]+@)?([^:\s]+):", token)
        if match:
            hosts.append(_literal_or_dynamic_host(match.group(1)))
    return hosts


def _host_from_hostport(value: str) -> str | None:
    return _literal_or_dynamic_host(value.rsplit(":", 1)[0])


def _literal_or_dynamic_host(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip().strip("\"'")
    if not value or any(marker in value for marker in ("$", "`", "(", ")", "{", "}")):
        return None
    return _clean_host(value.rsplit("@", 1)[-1])


def _clean_host(value: str | None) -> str | None:
    if not value:
        return None
    return value.strip().strip("[]").rstrip(".")


def _inline_interpreter_script(argv: list[str]) -> tuple[str, str] | None:
    if not argv:
        return None
    command = argv[0].split("/")[-1].lower()
    if command in {"python", "python3", "py"}:
        index = _option_value_index(argv, {"-c"})
        if index is not None:
            return "python", argv[index]
    if command in {"bash", "sh"}:
        index = _option_value_index(argv, {"-c", "-lc"})
        if index is not None:
            return "bash", argv[index]
    return None


def _option_value_index(argv: list[str], options: set[str]) -> int | None:
    for index, token in enumerate(argv[1:], start=1):
        if token in options and index + 1 < len(argv):
            return index + 1
    return None


def _is_dependency_install(tokens: list[str]) -> bool:
    if not tokens:
        return False
    lower = [token.lower() for token in tokens]
    command = lower[0].split("/")[-1]
    if command in {"pip", "pip3"} and len(lower) > 1 and lower[1] == "install":
        return True
    if command in {"python", "python3"} and len(lower) > 3 and lower[1:4] == ["-m", "pip", "install"]:
        return True
    if command in {"npm", "pnpm"} and len(lower) > 1 and lower[1] in {"install", "add", "update", "upgrade"}:
        return True
    if command == "yarn" and len(lower) > 1 and lower[1] in {"add", "install", "upgrade"}:
        return True
    if (command in {"apt", "apt-get", "brew", "yum"} and len(lower) > 1 and lower[1] in {
            "add",
            "install",
            "update",
            "upgrade",
    }):
        return True
    return False


def _is_privilege_escalation(tokens: list[str], line: str) -> bool:
    if not tokens:
        return False
    command = tokens[0].split("/")[-1]
    if command == "sudo" or (command == "su" and len(tokens) > 1 and tokens[1] == "-"):
        return True
    if command == "chmod" and any(token == "777" for token in tokens[1:]):
        return True
    if command == "chown" and any(token.startswith("root") for token in tokens[1:]):
        return True
    return bool(re.search(r"\b(sudo|su\s+-|chmod\s+777|chown\s+root)\b", line))


def _is_fork_bomb(line: str) -> bool:
    compact = re.sub(r"\s+", "", line)
    return ":(){:|:&};:" in compact or "(){:|:&};:" in compact


def _has_background_process(line: str) -> bool:
    return bool(re.search(r"(?<![&])&(?![&>])", line))


def _has_shell_operator(line: str) -> bool:
    return any(operator in line for operator in SHELL_OPERATORS)


def _is_long_sleep(tokens: list[str], threshold: int) -> bool:
    if len(tokens) < 2 or tokens[0].split("/")[-1] != "sleep":
        return False
    try:
        return float(tokens[1]) > threshold
    except ValueError:
        return True


def _is_unbounded_output(tokens: list[str]) -> bool:
    if not tokens:
        return False
    command = tokens[0].split("/")[-1].lower()
    return command == "yes"


def _is_zero_fill_write(tokens: list[str]) -> bool:
    if not tokens or tokens[0].split("/")[-1].lower() != "dd":
        return False
    has_zero_input = any(token == "if=/dev/zero" for token in tokens[1:])
    if not has_zero_input:
        return False
    output_targets = [token.split("=", 1)[1] for token in tokens[1:] if token.startswith("of=")]
    return not output_targets or any(target != "/dev/null" for target in output_targets)


def _suppress_low_value_unknown_command_reviews(findings: list[RiskFinding]) -> list[RiskFinding]:
    stronger_lines = {
        finding.line
        for finding in findings if finding.rule_id != "BASH_UNKNOWN_COMMAND_REVIEW" and (
            finding.decision == Decision.DENY
            or finding.risk_level in {RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL})
    }
    return [
        finding for finding in findings
        if finding.rule_id != "BASH_UNKNOWN_COMMAND_REVIEW" or finding.line not in stronger_lines
    ]


def _dedupe_findings(findings: list[RiskFinding]) -> list[RiskFinding]:
    seen: set[tuple[str, int | None, str]] = set()
    deduped: list[RiskFinding] = []
    for finding in findings:
        key = (finding.rule_id, finding.line, finding.evidence)
        if key not in seen:
            seen.add(key)
            deduped.append(finding)
    return deduped
