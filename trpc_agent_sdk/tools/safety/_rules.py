# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Rule implementations for Python and Bash safety scanning."""

from __future__ import annotations

import ast
import re
import shlex
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ._policy import ToolSafetyPolicy
from ._types import Decision
from ._types import RiskFinding
from ._types import RiskLevel

URL_RE = re.compile(r"https?://[^\s'\"<>]+")
SENSITIVE_NAME_RE = re.compile(
    r"(api[_-]?key|secret|token|password|passwd|private[_-]?key|access[_-]?key|credential)",
    re.IGNORECASE,
)
PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
DEPENDENCY_INSTALL_RE = re.compile(
    r"\b(python\s+-m\s+pip|pip3?|npm|yarn|pnpm|apt(?:-get)?|brew|yum)\s+"
    r"(?:install|add|upgrade|update)\b",
    re.IGNORECASE,
)
LONG_SLEEP_RE = re.compile(r"\bsleep\s+(\d+)\b")
SHELL_FEATURE_RE = re.compile(r"(\||&&|\|\||;|`[^`]+`|\$\(|>\s*[^&]|>>|&\s*$)")
DYNAMIC_SECRET_PATH_RE = re.compile(
    r"(\.env|\.ssh|id_rsa|credentials?|token|secret|password|private[_-]?key)",
    re.IGNORECASE,
)
SENSITIVE_ENV_REFERENCE_RE = re.compile(
    r"\$(?:\{)?([A-Za-z_][A-Za-z0-9_]*(?:\})?)",
)

PATH_LITERAL_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:"
    r"~?/[^\s'\";|]+|"
    r"\.env(?![A-Za-z0-9_])|"
    r"[^\s'\";|]+(?:\.pem|\.key|\.token|token\.txt|credentials(?:\.[^\s'\";|]+)?)"
    r")",
    re.IGNORECASE,
)


def sanitize_text(text: str, limit: int = 180) -> tuple[str, bool]:
    """Mask obvious secret values in rule evidence."""
    sanitized = False
    value_patterns = [
        re.compile(r"(?i)(api[_-]?key|token|password|secret)\s*[:=]\s*['\"]?([^'\"\s]+)"),
        re.compile(r"(?i)(authorization:\s*bearer\s+)([a-z0-9._\-]+)"),
    ]
    result = text
    for pattern in value_patterns:
        new_result = pattern.sub(lambda match: f"{match.group(1)}=<redacted>", result)
        sanitized = sanitized or new_result != result
        result = new_result
    if PRIVATE_KEY_RE.search(result):
        result = PRIVATE_KEY_RE.sub("-----BEGIN <redacted> PRIVATE KEY-----", result)
        sanitized = True
    result = result.strip()
    if len(result) > limit:
        result = result[:limit] + "..."
    return result, sanitized


def _line_at(script: str, lineno: int | None) -> str:
    if not lineno:
        return ""
    lines = script.splitlines()
    if 1 <= lineno <= len(lines):
        return lines[lineno - 1]
    return ""


def _finding(
    rule_id: str,
    risk_type: str,
    risk_level: RiskLevel,
    decision: Decision,
    evidence: str,
    recommendation: str,
    message: str = "",
    line: int | None = None,
    column: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> RiskFinding:
    evidence_text, _ = sanitize_text(evidence)
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
        metadata=metadata or {},
    )


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _call_name(node.value)
        if parent:
            return f"{parent}.{node.attr}"
        return node.attr
    return ""


def _constant_string(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return None
    return None


def _constant_string_list(node: ast.AST | None) -> list[str] | None:
    if not isinstance(node, (ast.List, ast.Tuple)):
        return None
    values: list[str] = []
    for item in node.elts:
        if not isinstance(item, ast.Constant) or not isinstance(item.value, str):
            return None
        values.append(item.value)
    return values


def _extract_urls(text: str) -> list[str]:
    return [url.rstrip(").,;") for url in URL_RE.findall(text)]


def _network_finding(url: str, policy: ToolSafetyPolicy, evidence: str, line: int | None = None) -> RiskFinding | None:
    host = urlparse(url).hostname or ""
    if not host:
        return None
    if policy.is_domain_allowed(host):
        return None
    return _finding(
        "NETWORK_NON_WHITELIST_DOMAIN",
        "network_egress",
        RiskLevel.HIGH,
        Decision.DENY,
        evidence,
        f"Add {host} to allowed_domains only if this destination is trusted.",
        f"Network request targets non-whitelisted domain {host}.",
        line=line,
        metadata={"domain": host},
    )


def _sensitive_env_names(text: str) -> list[str]:
    names: list[str] = []
    for match in SENSITIVE_ENV_REFERENCE_RE.finditer(text):
        name = match.group(1).rstrip("}")
        if SENSITIVE_NAME_RE.search(name):
            names.append(name)
    return names


def _scan_denied_path_candidates(
    candidates: list[str],
    policy: ToolSafetyPolicy,
    evidence: str,
    language: str,
    line_no: int,
) -> list[RiskFinding]:
    findings: list[RiskFinding] = []
    seen: set[str] = set()
    for candidate in candidates:
        path_candidate = candidate.strip().strip("'\"")
        if not path_candidate or path_candidate in seen:
            continue
        seen.add(path_candidate)
        if policy.is_path_denied(path_candidate):
            findings.append(
                _finding(
                    "FILE_SECRET_PATH_ACCESS",
                    "dangerous_file_operation",
                    RiskLevel.CRITICAL,
                    Decision.DENY,
                    evidence,
                    "Remove direct credential file access or explicitly scope the tool to safe workspace files.",
                    f"Script references denied path {path_candidate}.",
                    line=line_no,
                    metadata={
                        "path": path_candidate,
                        "language": language
                    },
                ))
    return findings


def _bash_argument_path_candidates(line: str) -> list[str]:
    try:
        tokens = shlex.split(line, comments=True)
    except ValueError:
        tokens = line.split()
    if not tokens:
        return []

    candidates: list[str] = []
    skip_next = False
    redirection_tokens = {">", ">>", "<", "2>", "2>>", "&>", "&>>"}
    for index, token in enumerate(tokens):
        if skip_next:
            skip_next = False
            continue
        if index == 0 or token in {"|", "&&", "||", ";"}:
            continue
        if token in redirection_tokens:
            skip_next = True
            continue
        if token.startswith("-") or token.startswith("$") or "://" in token or "=" in token:
            continue
        cleaned = token.rstrip(").,")
        if cleaned:
            candidates.append(cleaned)
    return candidates


class PythonSafetyVisitor(ast.NodeVisitor):
    """AST visitor that collects Python script safety findings."""

    def __init__(self, script: str, policy: ToolSafetyPolicy):
        self.script = script
        self.policy = policy
        self.findings: list[RiskFinding] = []

    def visit_Call(self, node: ast.Call) -> Any:  # noqa: N802
        call_name = _call_name(node.func)
        evidence = _line_at(self.script, node.lineno) or call_name

        if call_name in {"eval", "exec", "compile", "__import__"}:
            self.findings.append(
                _finding(
                    "PY_DYNAMIC_CODE_EXECUTION",
                    "process_command",
                    RiskLevel.MEDIUM,
                    Decision.NEEDS_HUMAN_REVIEW,
                    evidence,
                    "Avoid dynamic code execution or require a human approval step.",
                    "Dynamic Python execution is difficult to statically validate.",
                    line=node.lineno,
                    column=node.col_offset,
                ))

        if call_name in {"open", "Path.open", "pathlib.Path.open"}:
            self._check_path_argument(node, evidence)

        if call_name.endswith((".read_text", ".read_bytes", ".write_text", ".write_bytes")):
            self._check_path_method(node, evidence)

        if call_name in {"shutil.rmtree", "os.remove", "os.unlink", "pathlib.Path.unlink"}:
            self._check_delete_call(node, evidence)

        if call_name in {"os.system", "os.popen"} or call_name.startswith("subprocess."):
            self._check_process_call(node, call_name, evidence)

        if call_name.startswith(("requests.", "httpx.", "urllib.request.")) or call_name.startswith("aiohttp."):
            self._check_network_call(node, call_name, evidence)

        if call_name in {"socket.socket", "socket.create_connection"}:
            self.findings.append(
                _finding(
                    "PY_SOCKET_NETWORK_ACCESS",
                    "network_egress",
                    RiskLevel.MEDIUM,
                    Decision.NEEDS_HUMAN_REVIEW,
                    evidence,
                    "Use an explicit URL-based client and configure allowed_domains, or require review.",
                    "Raw socket access may bypass domain allowlist checks.",
                    line=node.lineno,
                    column=node.col_offset,
                ))

        if call_name in {"print", "logging.info", "logging.warning", "logging.error", "logger.info", "logger.error"}:
            self._check_sensitive_output(node, evidence)
        if call_name in {"os.getenv", "os.environ.get"}:
            self._check_sensitive_env_read(node, evidence)

        self.generic_visit(node)

    def visit_While(self, node: ast.While) -> Any:  # noqa: N802
        if isinstance(node.test, ast.Constant) and node.test.value is True:
            self.findings.append(
                _finding(
                    "PY_INFINITE_LOOP",
                    "resource_abuse",
                    RiskLevel.MEDIUM,
                    Decision.NEEDS_HUMAN_REVIEW,
                    _line_at(self.script, node.lineno),
                    "Add a bounded condition, timeout, or cancellation check.",
                    "while True loop may run indefinitely.",
                    line=node.lineno,
                    column=node.col_offset,
                ))
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> Any:  # noqa: N802
        if isinstance(node.value, str):
            if PRIVATE_KEY_RE.search(node.value):
                self.findings.append(
                    _finding(
                        "SENSITIVE_PRIVATE_KEY_LITERAL",
                        "sensitive_information_leak",
                        RiskLevel.CRITICAL,
                        Decision.DENY,
                        node.value,
                        "Remove private key material from scripts and load secrets through a secret manager.",
                        "Private key material appears in script content.",
                        line=getattr(node, "lineno", None),
                        column=getattr(node, "col_offset", None),
                    ))
            for url in _extract_urls(node.value):
                finding = _network_finding(url, self.policy, node.value, getattr(node, "lineno", None))
                if finding:
                    self.findings.append(finding)
        self.generic_visit(node)

    def _check_path_argument(self, node: ast.Call, evidence: str) -> None:
        if not node.args:
            return
        path_text = _constant_string(node.args[0])
        if path_text and self.policy.is_path_denied(path_text):
            self.findings.append(
                _finding(
                    "FILE_SECRET_PATH_ACCESS",
                    "dangerous_file_operation",
                    RiskLevel.CRITICAL,
                    Decision.DENY,
                    evidence,
                    "Do not read or write denied paths such as .env, ~/.ssh, credential files, or system accounts.",
                    f"Script accesses denied path {path_text}.",
                    line=node.lineno,
                    column=node.col_offset,
                    metadata={"path": path_text},
                ))
        elif path_text is None and DYNAMIC_SECRET_PATH_RE.search(evidence):
            self.findings.append(
                _finding(
                    "FILE_DYNAMIC_SECRET_PATH_REVIEW",
                    "dangerous_file_operation",
                    RiskLevel.MEDIUM,
                    Decision.NEEDS_HUMAN_REVIEW,
                    evidence,
                    "Resolve dynamic paths before execution and confirm they cannot target .env, ~/.ssh, "
                    "or credentials.",
                    "Dynamic path construction references a sensitive path pattern.",
                    line=node.lineno,
                    column=node.col_offset,
                ))

    def _check_path_method(self, node: ast.Call, evidence: str) -> None:
        receiver = node.func.value if isinstance(node.func, ast.Attribute) else None
        path_text = None
        if isinstance(receiver, ast.Call) and _call_name(receiver.func) in {"Path", "pathlib.Path"} and receiver.args:
            path_text = _constant_string(receiver.args[0])
        if path_text and self.policy.is_path_denied(path_text):
            self.findings.append(
                _finding(
                    "FILE_SECRET_PATH_ACCESS",
                    "dangerous_file_operation",
                    RiskLevel.CRITICAL,
                    Decision.DENY,
                    evidence,
                    "Avoid reading or writing credential paths in tool scripts.",
                    f"Script accesses denied path {path_text}.",
                    line=node.lineno,
                    column=node.col_offset,
                    metadata={"path": path_text},
                ))

    def _check_delete_call(self, node: ast.Call, evidence: str) -> None:
        path_text = _constant_string(node.args[0]) if node.args else None
        home = str(Path.home())
        dangerous_target = path_text in {"/", "/tmp", "~", home} if path_text else False
        denied_target = bool(path_text and self.policy.is_path_denied(path_text))
        recursive_delete = _call_name(node.func) == "shutil.rmtree"
        if recursive_delete or dangerous_target or denied_target:
            self.findings.append(
                _finding(
                    "FILE_DANGEROUS_DELETE",
                    "dangerous_file_operation",
                    RiskLevel.CRITICAL,
                    Decision.DENY,
                    evidence,
                    "Avoid recursive or broad deletion in tool scripts; constrain deletes to explicit workspace paths.",
                    "Dangerous delete operation detected.",
                    line=node.lineno,
                    column=node.col_offset,
                    metadata={"path": path_text or "<dynamic>"},
                ))

    def _check_process_call(self, node: ast.Call, call_name: str, evidence: str) -> None:
        command_text = _constant_string(node.args[0]) if node.args else None
        command_args = _constant_string_list(node.args[0]) if node.args else None
        if command_args:
            command_text = shlex.join(command_args)
        shell_true = any(
            keyword.arg == "shell" and isinstance(keyword.value, ast.Constant) and keyword.value.value is True
            for keyword in node.keywords)

        if command_text:
            self.findings.extend(scan_bash_script(command_text, self.policy))
        elif shell_true:
            self.findings.append(
                _finding(
                    "PY_SHELL_INJECTION_RISK",
                    "process_command",
                    RiskLevel.HIGH,
                    Decision.NEEDS_HUMAN_REVIEW,
                    evidence,
                    "Avoid shell=True with dynamic input; pass an argument list and validate user-controlled values.",
                    "shell=True with a dynamic command may allow shell injection.",
                    line=node.lineno,
                    column=node.col_offset,
                ))

        if self.policy.review_process_execution:
            self.findings.append(
                _finding(
                    "PY_PROCESS_EXECUTION_REVIEW",
                    "process_command",
                    RiskLevel.MEDIUM,
                    Decision.NEEDS_HUMAN_REVIEW,
                    evidence,
                    "Review subprocess/os.system usage and prefer a constrained wrapper.",
                    f"Python process execution via {call_name} requires review.",
                    line=node.lineno,
                    column=node.col_offset,
                ))

    def _check_network_call(self, node: ast.Call, call_name: str, evidence: str) -> None:
        for arg in node.args:
            url = _constant_string(arg)
            if not url:
                continue
            for found in _extract_urls(url):
                finding = _network_finding(found, self.policy, evidence, node.lineno)
                if finding:
                    self.findings.append(finding)
                    return
            if url.startswith("http"):
                return
        if self.policy.review_unknown_network:
            self.findings.append(
                _finding(
                    "NETWORK_DYNAMIC_URL_REVIEW",
                    "network_egress",
                    RiskLevel.MEDIUM,
                    Decision.NEEDS_HUMAN_REVIEW,
                    evidence,
                    "Use literal URLs where possible or validate the destination against allowed_domains.",
                    f"{call_name} uses a dynamic URL that cannot be allowlist-checked statically.",
                    line=node.lineno,
                    column=node.col_offset,
                ))

    def _check_sensitive_output(self, node: ast.Call, evidence: str) -> None:
        rendered_args = [ast.unparse(arg) if hasattr(ast, "unparse") else "" for arg in node.args]
        joined = " ".join(rendered_args)
        if SENSITIVE_NAME_RE.search(joined):
            self.findings.append(
                _finding(
                    "SENSITIVE_OUTPUT",
                    "sensitive_information_leak",
                    RiskLevel.HIGH,
                    Decision.DENY,
                    evidence,
                    "Do not print or log secrets; redact values before writing logs or tool output.",
                    "Script appears to output a sensitive variable or credential.",
                    line=node.lineno,
                    column=node.col_offset,
                ))

    def _check_sensitive_env_read(self, node: ast.Call, evidence: str) -> None:
        key_text = _constant_string(node.args[0]) if node.args else None
        if key_text and SENSITIVE_NAME_RE.search(key_text):
            self.findings.append(
                _finding(
                    "SENSITIVE_ENV_READ_REVIEW",
                    "sensitive_information_leak",
                    RiskLevel.MEDIUM,
                    Decision.NEEDS_HUMAN_REVIEW,
                    evidence,
                    "Avoid passing secret environment values to tool scripts unless a human approves the flow.",
                    f"Script reads sensitive environment variable {key_text}.",
                    line=node.lineno,
                    column=node.col_offset,
                    metadata={"env_key": key_text},
                ))


def scan_python_script(script: str, policy: ToolSafetyPolicy) -> list[RiskFinding]:
    findings: list[RiskFinding] = []
    try:
        tree = ast.parse(script)
    except SyntaxError as ex:
        return [
            _finding(
                "PY_PARSE_ERROR_REVIEW",
                "unknown",
                RiskLevel.MEDIUM,
                Decision.NEEDS_HUMAN_REVIEW,
                str(ex),
                "Fix Python syntax before execution or require human review.",
                "Python script could not be parsed for AST-based safety checks.",
                line=ex.lineno,
                column=ex.offset,
            )
        ]

    visitor = PythonSafetyVisitor(script, policy)
    visitor.visit(tree)
    findings.extend(visitor.findings)
    findings.extend(scan_text_patterns(script, policy, language="python"))
    return _dedupe_findings(findings)


def scan_bash_script(script: str, policy: ToolSafetyPolicy) -> list[RiskFinding]:
    findings = scan_text_patterns(script, policy, language="bash")
    for line_no, line in enumerate(script.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        findings.extend(_scan_bash_line(stripped, policy, line_no))
    return _dedupe_findings(findings)


def scan_text_patterns(script: str, policy: ToolSafetyPolicy, language: str) -> list[RiskFinding]:
    findings: list[RiskFinding] = []
    for line_no, line in enumerate(script.splitlines(), start=1):
        if PRIVATE_KEY_RE.search(line):
            findings.append(
                _finding(
                    "SENSITIVE_PRIVATE_KEY_LITERAL",
                    "sensitive_information_leak",
                    RiskLevel.CRITICAL,
                    Decision.DENY,
                    line,
                    "Remove private key material from scripts and use a secret manager.",
                    "Private key material appears in script content.",
                    line=line_no,
                ))
        findings.extend(
            _scan_denied_path_candidates(PATH_LITERAL_RE.findall(line), policy, line, language, line_no))
        for url in _extract_urls(line):
            finding = _network_finding(url, policy, line, line_no)
            if finding:
                findings.append(finding)
        if DEPENDENCY_INSTALL_RE.search(line) and policy.deny_dependency_install:
            findings.append(
                _finding(
                    "DEPENDENCY_INSTALL",
                    "dependency_install",
                    RiskLevel.HIGH,
                    Decision.DENY,
                    line,
                    "Move dependency changes to a reviewed build step or allowlist the environment outside tool "
                    "execution.",
                    "Script changes runtime dependencies or system packages.",
                    line=line_no,
                ))
        if re.search(r"\b(api[_-]?key|token|password|secret)\b", line, re.IGNORECASE) and re.search(
                r"\b(print|echo|curl|requests|write|logging|logger)\b", line, re.IGNORECASE):
            findings.append(
                _finding(
                    "SENSITIVE_OUTPUT",
                    "sensitive_information_leak",
                    RiskLevel.HIGH,
                    Decision.DENY,
                    line,
                    "Redact secret values before logging, writing files, or making network requests.",
                    "Script may write or transmit sensitive information.",
                    line=line_no,
                ))
        if language == "bash":
            sensitive_envs = _sensitive_env_names(line)
            if sensitive_envs and re.search(r"\b(echo|printf|cat|curl|wget|tee|logger)\b", line, re.IGNORECASE):
                findings.append(
                    _finding(
                        "SENSITIVE_OUTPUT",
                        "sensitive_information_leak",
                        RiskLevel.HIGH,
                        Decision.DENY,
                        line,
                        "Redact secret values before logging, writing files, or making network requests.",
                        "Script appears to output a sensitive environment variable or credential.",
                        line=line_no,
                        metadata={"env_keys": sensitive_envs},
                    ))
        if re.search(r"\bos\.getenv\(['\"][^'\"]*(token|secret|password|api[_-]?key)[^'\"]*['\"]\)", line,
                     re.IGNORECASE) and re.search(r"\b(requests|curl|post|get|print|logging|logger)\b", line,
                                                  re.IGNORECASE):
            findings.append(
                _finding(
                    "SENSITIVE_ENV_EXFILTRATION_REVIEW",
                    "sensitive_information_leak",
                    RiskLevel.HIGH,
                    Decision.NEEDS_HUMAN_REVIEW,
                    line,
                    "Review any script that reads secret environment variables and sends or writes them.",
                    "Script appears to read a sensitive environment variable for output or network use.",
                    line=line_no,
                ))
        if language == "bash":
            findings.extend(
                _scan_denied_path_candidates(_bash_argument_path_candidates(line), policy, line, language, line_no))
    return findings


def _scan_bash_line(line: str, policy: ToolSafetyPolicy, line_no: int) -> list[RiskFinding]:
    findings: list[RiskFinding] = []
    if re.search(r"\brm\s+(-[a-zA-Z]*[rf][a-zA-Z]*|-[a-zA-Z]*r[a-zA-Z]*\s+-[a-zA-Z]*f)", line):
        findings.append(
            _finding(
                "BASH_RECURSIVE_DELETE",
                "dangerous_file_operation",
                RiskLevel.CRITICAL,
                Decision.DENY,
                line,
                "Avoid rm -rf in tool scripts; delete only explicit workspace files after validation.",
                "Recursive forced deletion detected.",
                line=line_no,
            ))

    if re.search(r"\bfind\b.+\s-delete\b", line):
        findings.append(
            _finding(
                "BASH_FIND_DELETE_REVIEW",
                "dangerous_file_operation",
                RiskLevel.HIGH,
                Decision.NEEDS_HUMAN_REVIEW,
                line,
                "Review find -delete commands and constrain them to explicit workspace paths.",
                "find -delete can remove many files recursively.",
                line=line_no,
            ))

    if re.search(r"\bxargs\b.+\brm\b.+-[^\s]*[rf]", line) or re.search(r"\brm\b.+-[^\s]*[rf].+\bxargs\b", line):
        findings.append(
            _finding(
                "BASH_XARGS_RM_REVIEW",
                "dangerous_file_operation",
                RiskLevel.HIGH,
                Decision.NEEDS_HUMAN_REVIEW,
                line,
                "Review xargs-driven deletes because the target set is generated at runtime.",
                "xargs rm can delete a dynamic set of paths.",
                line=line_no,
            ))

    if re.search(r"\bbase64\s+(-d|--decode)\b.*\|\s*(sh|bash)\b", line) or re.search(
            r"\|\s*base64\s+(-d|--decode)\b.*\|\s*(sh|bash)\b", line):
        findings.append(
            _finding(
                "BASH_BASE64_EXEC_REVIEW",
                "process_command",
                RiskLevel.HIGH,
                Decision.NEEDS_HUMAN_REVIEW,
                line,
                "Decode and review encoded payloads before executing them.",
                "Base64-decoded content is piped into a shell.",
                line=line_no,
            ))

    if re.search(r"\b(bash|sh)\s+-[lc]*c\b", line) or re.search(r"\bpython3?\s+-c\b", line):
        findings.append(
            _finding(
                "BASH_INLINE_INTERPRETER_REVIEW",
                "process_command",
                RiskLevel.MEDIUM,
                Decision.NEEDS_HUMAN_REVIEW,
                line,
                "Extract inline interpreter code into a separately scanned script before execution.",
                "Inline interpreter execution hides a second-stage script from simple command review.",
                line=line_no,
            ))

    if re.search(r"\b(sudo|su\s+-|chmod\s+777|chown\s+root)\b", line) and policy.deny_privilege_escalation:
        findings.append(
            _finding(
                "BASH_PRIVILEGE_ESCALATION",
                "process_command",
                RiskLevel.HIGH,
                Decision.DENY,
                line,
                "Remove privilege escalation from tool scripts and run with least privilege.",
                "Privilege escalation or unsafe permission change detected.",
                line=line_no,
            ))

    if ":(){ :|:& };:" in line or re.search(r"\b(fork|:)\s*\(\)\s*\{", line):
        findings.append(
            _finding(
                "BASH_FORK_BOMB",
                "resource_abuse",
                RiskLevel.CRITICAL,
                Decision.DENY,
                line,
                "Remove recursive process spawning and enforce process limits.",
                "Fork bomb pattern detected.",
                line=line_no,
            ))

    sleep_match = LONG_SLEEP_RE.search(line)
    if sleep_match and int(sleep_match.group(1)) > policy.long_sleep_seconds:
        findings.append(
            _finding(
                "BASH_LONG_SLEEP",
                "resource_abuse",
                RiskLevel.MEDIUM,
                Decision.NEEDS_HUMAN_REVIEW,
                line,
                "Use shorter sleeps, explicit timeouts, or asynchronous polling with cancellation.",
                "Long sleep may tie up execution resources.",
                line=line_no,
            ))

    if re.search(r"\b(while|until)\s+(true|:)", line):
        findings.append(
            _finding(
                "BASH_INFINITE_LOOP",
                "resource_abuse",
                RiskLevel.MEDIUM,
                Decision.NEEDS_HUMAN_REVIEW,
                line,
                "Add a bounded condition, timeout, or cancellation check.",
                "Infinite shell loop detected.",
                line=line_no,
            ))

    if SHELL_FEATURE_RE.search(line) and policy.review_shell_features:
        findings.append(
            _finding(
                "BASH_SHELL_FEATURE_REVIEW",
                "process_command",
                RiskLevel.LOW,
                Decision.NEEDS_HUMAN_REVIEW,
                line,
                "Review shell pipes, redirections, command substitution, and background processes before execution.",
                "Shell feature requires review because it may hide chained operations.",
                line=line_no,
            ))

    try:
        tokens = shlex.split(line, comments=True)
    except ValueError:
        tokens = line.split()
    if tokens:
        command = tokens[0]
        if command not in policy.allowed_commands and command in {
                "bash",
                "curl",
                "nc",
                "netcat",
                "python",
                "python3",
                "sh",
                "socat",
                "wget",
        }:
            findings.append(
                _finding(
                    "BASH_COMMAND_REVIEW",
                    "process_command",
                    RiskLevel.MEDIUM,
                    Decision.NEEDS_HUMAN_REVIEW,
                    line,
                    "Add trusted commands to allowed_commands or route execution through a constrained tool wrapper.",
                    f"Command {command} requires review under the current policy.",
                    line=line_no,
                    metadata={"command": command},
                ))
    return findings


def _dedupe_findings(findings: list[RiskFinding]) -> list[RiskFinding]:
    seen: set[tuple[str, int | None, str]] = set()
    unique: list[RiskFinding] = []
    for finding in findings:
        key = (finding.rule_id, finding.line, finding.evidence)
        if key in seen:
            continue
        seen.add(key)
        unique.append(finding)
    return unique
