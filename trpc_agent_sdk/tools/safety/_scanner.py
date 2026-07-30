#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Static Python/Bash scanner for executable Tool payloads."""

from __future__ import annotations

import ast
import re
import shlex
import time
from pathlib import PurePath
from typing import Iterable
from urllib.parse import urlparse

from ._models import RiskCategory
from ._models import RiskLevel
from ._models import SafetyDecision
from ._models import SafetyFinding
from ._models import SafetyReport
from ._models import ScriptLanguage
from ._models import ScriptPayload
from ._models import ScriptScanRequest
from ._policy import ToolSafetyPolicy
from ._redaction import redact_text

_DECISION_ORDER = {
    SafetyDecision.ALLOW: 0,
    SafetyDecision.NEEDS_HUMAN_REVIEW: 1,
    SafetyDecision.DENY: 2,
}
_RISK_ORDER = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
    RiskLevel.CRITICAL: 3,
}
_URL_RE = re.compile(r"https?://[^\s\"'|;&]+", re.IGNORECASE)
_SENSITIVE_NAME_RE = re.compile(r"(api[_-]?key|token|secret|password|passwd|private[_-]?key)", re.IGNORECASE)
_SHELL_VARIABLE_RE = re.compile(r"\$(?:\{)?([A-Za-z_][A-Za-z0-9_]*)\}?")
_DEPENDENCY_RE = re.compile(
    r"(?:^|[;&|]\s*|\s)(?:python\d*\s+-m\s+)?(?:pip|pip\d*|npm|yarn|pnpm|apt(?:-get)?|apk|brew)"
    r"\s+(?:install|add)\b",
    re.IGNORECASE,
)
_SHELL_BYPASS_RE = re.compile(r"(?:`[^`]+`|\$\([^)]*\)|\b(?:eval|bash|sh)\s+-c\b)")
_FORK_BOMB_RE = re.compile(r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:")


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _call_name(node.value.func) if isinstance(node.value, ast.Call) else _call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _import_aliases(tree: ast.AST) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                aliases[item.asname or item.name.split(".", 1)[0]] = item.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            for item in node.names:
                aliases[item.asname or item.name] = f"{node.module}.{item.name}"
    return aliases


def _resolve_alias(name: str, aliases: dict[str, str]) -> str:
    root, separator, remainder = name.partition(".")
    replacement = aliases.get(root)
    if not replacement:
        return name
    return f"{replacement}{separator}{remainder}" if separator else replacement


def _literal_string(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, (ast.List, ast.Tuple)):
        parts: list[str] = []
        for item in node.elts:
            value = _literal_string(item)
            if value is None:
                return None
            parts.append(value)
        return " ".join(parts)
    return None


def _assigned_names(node: ast.AST) -> set[str]:
    """Return simple names assigned by an assignment target."""

    return {child.id for child in ast.walk(node) if isinstance(child, ast.Name)}


def _static_value_size(node: ast.AST | None) -> int | None:
    """Estimate bytes for simple literal writes without evaluating code."""

    if isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)):
        return len(node.value.encode("utf-8") if isinstance(node.value, str) else node.value)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        if isinstance(node.left, ast.Constant) and isinstance(node.left.value, (str, bytes)):
            if isinstance(node.right, ast.Constant) and isinstance(node.right.value, int):
                unit = _static_value_size(node.left)
                return unit * node.right.value if unit is not None and node.right.value >= 0 else None
        if isinstance(node.right, ast.Constant) and isinstance(node.right.value, (str, bytes)):
            if isinstance(node.left, ast.Constant) and isinstance(node.left.value, int):
                unit = _static_value_size(node.right)
                return unit * node.left.value if unit is not None and node.left.value >= 0 else None
    return None


class ToolScriptSafetyScanner:
    """Scan normalized requests without executing or importing their content."""

    def __init__(self, policy: ToolSafetyPolicy | None = None):
        self.policy = policy or ToolSafetyPolicy()

    def scan(self, request: ScriptScanRequest) -> SafetyReport:
        """Return a redacted report for all executable payloads."""

        started = time.perf_counter()
        findings: list[SafetyFinding] = []
        redacted = False
        secrets = list(request.env.values())
        sensitive_values = [value for name, value in request.env.items() if value and _SENSITIVE_NAME_RE.search(name)]

        findings.extend(self._scan_context(request))
        for index, payload in enumerate(request.payloads):
            _, payload_redacted = redact_text(
                payload.content,
                secrets=secrets,
                max_chars=max(len(payload.content) + 1, self.policy.evidence_max_chars),
            )
            redacted = redacted or payload_redacted
            if len(payload.content.splitlines()) > self.policy.max_script_lines:
                findings.append(
                    self._finding(
                        RiskCategory.RESOURCE_ABUSE,
                        RiskLevel.HIGH,
                        "RESOURCE_SCRIPT_TOO_LARGE",
                        f"script has {len(payload.content.splitlines())} lines",
                        "Reduce the script size or review it outside the execution path.",
                        SafetyDecision.DENY,
                        index,
                        secrets,
                    )
                )
            if payload.language == ScriptLanguage.PYTHON:
                payload_findings = self._scan_python(payload, index, secrets, sensitive_values)
            else:
                payload_findings = self._scan_bash(payload, index, secrets, sensitive_values)
            findings.extend(payload_findings)

        unique: list[SafetyFinding] = []
        seen: set[tuple[str, int, str]] = set()
        for finding in findings:
            key = (finding.rule_id, finding.payload_index, finding.evidence)
            if key not in seen and finding.rule_id not in self.policy.disabled_rules:
                seen.add(key)
                unique.append(finding)
            redacted = redacted or "[REDACTED]" in finding.evidence or finding.evidence.endswith("…")

        if unique:
            decision = max((item.decision for item in unique), key=_DECISION_ORDER.__getitem__)
            risk_level = max((item.risk_level for item in unique), key=_RISK_ORDER.__getitem__)
            summary = f"{decision.value}: {len(unique)} finding(s)"
        else:
            decision = SafetyDecision.ALLOW
            risk_level = RiskLevel.LOW
            summary = "allow: no configured risk rule matched"

        return SafetyReport(
            decision=decision,
            risk_level=risk_level,
            findings=unique,
            duration_ms=round((time.perf_counter() - started) * 1000, 3),
            redacted=redacted,
            summary=summary,
            policy_version=self.policy.version,
            review_required=decision == SafetyDecision.NEEDS_HUMAN_REVIEW,
        )

    def failure_report(self, error: Exception, *, rule_id: str = "SCAN_INTERNAL_ERROR") -> SafetyReport:
        """Create a fail-closed report without exposing exception text."""

        finding = self._finding(
            RiskCategory.SCAN_ERROR,
            RiskLevel.HIGH,
            rule_id,
            f"safety scanner failed with {type(error).__name__}",
            "Fix the scanner failure before retrying execution.",
            SafetyDecision.DENY,
            0,
            (),
        )
        return SafetyReport(
            decision=SafetyDecision.DENY,
            risk_level=RiskLevel.HIGH,
            findings=[finding],
            duration_ms=0,
            redacted=True,
            summary=f"deny: {rule_id.lower()} failed closed",
            policy_version=self.policy.version,
        )

    def _scan_context(self, request: ScriptScanRequest) -> list[SafetyFinding]:
        findings: list[SafetyFinding] = []
        secrets = list(request.env.values())
        if request.cwd and self._is_forbidden_path(request.cwd):
            findings.append(
                self._finding(
                    RiskCategory.FILE_OPERATION,
                    RiskLevel.CRITICAL,
                    "FILE_FORBIDDEN_CWD",
                    request.cwd,
                    "Use a workspace-relative working directory.",
                    SafetyDecision.DENY,
                    0,
                    secrets,
                )
            )
        if request.requested_timeout is not None and request.requested_timeout > self.policy.max_timeout_seconds:
            findings.append(
                self._finding(
                    RiskCategory.RESOURCE_ABUSE,
                    RiskLevel.HIGH,
                    "RESOURCE_TIMEOUT_LIMIT",
                    f"requested timeout {request.requested_timeout}s exceeds {self.policy.max_timeout_seconds}s",
                    "Use a timeout within the configured maximum.",
                    SafetyDecision.DENY,
                    0,
                    secrets,
                )
            )
        if request.requested_timeout is not None and request.requested_timeout <= 0:
            findings.append(
                self._finding(
                    RiskCategory.RESOURCE_ABUSE,
                    RiskLevel.HIGH,
                    "RESOURCE_TIMEOUT_REQUIRED",
                    "executor has no positive execution timeout",
                    "Configure a positive execution timeout before enabling code execution.",
                    SafetyDecision.DENY,
                    0,
                    secrets,
                )
            )
        if request.max_output_bytes is not None and request.max_output_bytes > self.policy.max_output_bytes:
            findings.append(
                self._finding(
                    RiskCategory.RESOURCE_ABUSE,
                    RiskLevel.HIGH,
                    "RESOURCE_OUTPUT_LIMIT",
                    f"requested output {request.max_output_bytes} bytes exceeds {self.policy.max_output_bytes}",
                    "Use an output limit within the configured maximum.",
                    SafetyDecision.DENY,
                    0,
                    secrets,
                )
            )
        for payload_index, payload in enumerate(request.payloads):
            for argument in payload.argv:
                if self._is_forbidden_path(argument):
                    findings.append(
                        self._finding(
                            RiskCategory.FILE_OPERATION,
                            RiskLevel.CRITICAL,
                            "FILE_FORBIDDEN_ARGUMENT",
                            argument,
                            "Remove sensitive paths from command arguments.",
                            SafetyDecision.DENY,
                            payload_index,
                            secrets,
                        )
                    )
        return findings

    def _scan_python(
        self,
        payload: ScriptPayload,
        payload_index: int,
        secrets: Iterable[str],
        sensitive_values: Iterable[str],
    ) -> list[SafetyFinding]:
        try:
            tree = ast.parse(payload.content)
        except (SyntaxError, ValueError) as exc:
            return [
                self._finding(
                    RiskCategory.POLICY,
                    RiskLevel.MEDIUM,
                    "PYTHON_PARSE_UNCERTAIN",
                    f"Python parser returned {type(exc).__name__}",
                    "Require human review for code that cannot be parsed reliably.",
                    SafetyDecision.NEEDS_HUMAN_REVIEW,
                    payload_index,
                    secrets,
                )
            ]

        findings: list[SafetyFinding] = []
        aliases = _import_aliases(tree)
        tainted_names: set[str] = set()
        assignments: list[tuple[list[ast.AST], ast.AST]] = []
        for assignment in ast.walk(tree):
            value: ast.AST | None = None
            targets: list[ast.AST] = []
            if isinstance(assignment, ast.Assign):
                value = assignment.value
                targets = list(assignment.targets)
            elif isinstance(assignment, ast.AnnAssign):
                value = assignment.value
                targets = [assignment.target]
            elif isinstance(assignment, ast.NamedExpr):
                value = assignment.value
                targets = [assignment.target]
            if value is not None:
                assignments.append((targets, value))

        changed = True
        while changed:
            changed = False
            for targets, value in assignments:
                if not self._contains_sensitive_reference(value, tainted_names, sensitive_values):
                    continue
                for target in targets:
                    new_names = _assigned_names(target) - tainted_names
                    if new_names:
                        tainted_names.update(new_names)
                        changed = True

        for node in ast.walk(tree):
            if isinstance(node, ast.While) and isinstance(node.test, ast.Constant) and node.test.value is True:
                findings.append(
                    self._finding(
                        RiskCategory.RESOURCE_ABUSE,
                        RiskLevel.HIGH,
                        "RESOURCE_INFINITE_LOOP",
                        self._python_evidence(payload.content, node),
                        "Use a bounded loop with an explicit exit condition.",
                        SafetyDecision.DENY,
                        payload_index,
                        secrets,
                    )
                )
            if not isinstance(node, ast.Call):
                continue
            name = _resolve_alias(_call_name(node.func), aliases)
            evidence = self._python_evidence(payload.content, node)

            file_method = node.func.attr if isinstance(node.func, ast.Attribute) else ""
            if name in {"shutil.rmtree", "os.remove", "os.unlink", "Path.unlink", "Path.rmdir"} or file_method in {
                "unlink",
                "rmdir",
            }:
                findings.append(
                    self._finding(
                        RiskCategory.FILE_OPERATION,
                        RiskLevel.CRITICAL if name == "shutil.rmtree" else RiskLevel.HIGH,
                        "FILE_DESTRUCTIVE_OPERATION",
                        evidence,
                        "Remove destructive file operations or constrain them to an isolated workspace.",
                        SafetyDecision.DENY,
                        payload_index,
                        secrets,
                    )
                )

            if name == "open" or file_method in {
                "open",
                "read_text",
                "read_bytes",
                "write",
                "write_text",
                "write_bytes",
            }:
                if name == "open":
                    path_node = (
                        node.args[0]
                        if node.args
                        else next(
                            (keyword.value for keyword in node.keywords if keyword.arg in {"file", "path"}),
                            None,
                        )
                    )
                elif isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Call):
                    path_node = node.func.value.args[0] if node.func.value.args else None
                else:
                    path_node = None
                path = _literal_string(path_node)
                if path is None or self._is_forbidden_path(path):
                    findings.append(
                        self._finding(
                            RiskCategory.FILE_OPERATION,
                            RiskLevel.CRITICAL if path and self._is_forbidden_path(path) else RiskLevel.MEDIUM,
                            "FILE_SENSITIVE_PATH" if path else "FILE_DYNAMIC_PATH",
                            evidence,
                            "Use a verified workspace-relative path.",
                            (
                                SafetyDecision.DENY
                                if path and self._is_forbidden_path(path)
                                else SafetyDecision.NEEDS_HUMAN_REVIEW
                            ),
                            payload_index,
                            secrets,
                        )
                    )
                mode_node: ast.AST | None = None
                if name == "open":
                    mode_node = (
                        node.args[1]
                        if len(node.args) > 1
                        else next(
                            (keyword.value for keyword in node.keywords if keyword.arg == "mode"),
                            None,
                        )
                    )
                elif file_method == "open":
                    mode_node = (
                        node.args[0]
                        if node.args
                        else next(
                            (keyword.value for keyword in node.keywords if keyword.arg == "mode"),
                            None,
                        )
                    )
                mode = _literal_string(mode_node) or "r"
                writes_file = (
                    file_method in {"write", "write_text", "write_bytes"}
                    or (file_method == "open" or name == "open")
                    and any(flag in mode for flag in "wax+")
                )
                if writes_file and path and self._is_protected_write_path(path):
                    findings.append(
                        self._finding(
                            RiskCategory.FILE_OPERATION,
                            RiskLevel.CRITICAL,
                            "FILE_SYSTEM_WRITE",
                            evidence,
                            "Write only to a reviewed workspace-relative path.",
                            SafetyDecision.DENY,
                            payload_index,
                            secrets,
                        )
                    )
                data_node = (
                    node.args[0] if file_method in {"write", "write_text", "write_bytes"} and node.args else None
                )
                if data_node is not None and self._contains_sensitive_reference(
                    data_node, tainted_names, sensitive_values
                ):
                    findings.append(
                        self._finding(
                            RiskCategory.SENSITIVE_DATA,
                            RiskLevel.CRITICAL,
                            "SENSITIVE_FILE_WRITE",
                            evidence,
                            "Do not persist credentials or secret-bearing environment variables.",
                            SafetyDecision.DENY,
                            payload_index,
                            secrets,
                        )
                    )
                static_size = _static_value_size(data_node)
                if static_size is not None and static_size > self.policy.max_file_write_bytes:
                    findings.append(
                        self._finding(
                            RiskCategory.RESOURCE_ABUSE,
                            RiskLevel.HIGH,
                            "RESOURCE_LARGE_FILE_WRITE",
                            evidence,
                            "Keep generated files within the configured write-size limit.",
                            SafetyDecision.DENY,
                            payload_index,
                            secrets,
                        )
                    )

            if self._is_network_call(name):
                keyword_target = next(
                    (keyword.value for keyword in node.keywords if keyword.arg in {"url", "host"}),
                    None,
                )
                argument_index = 1 if name.endswith(".request") else 0
                target_node = node.args[argument_index] if len(node.args) > argument_index else keyword_target
                if name.startswith("socket.") and isinstance(target_node, (ast.Tuple, ast.List)) and target_node.elts:
                    target_node = target_node.elts[0]
                target = _literal_string(target_node)
                findings.extend(self._network_findings(target, evidence, payload_index, secrets))
                if self._contains_sensitive_reference(node, tainted_names, sensitive_values):
                    findings.append(
                        self._finding(
                            RiskCategory.SENSITIVE_DATA,
                            RiskLevel.CRITICAL,
                            "SENSITIVE_EXFILTRATION",
                            evidence,
                            "Do not send credentials or secret-bearing environment variables over the network.",
                            SafetyDecision.DENY,
                            payload_index,
                            secrets,
                        )
                    )

            if name.startswith("subprocess.") or name in {"os.system", "os.popen"}:
                command = _literal_string(node.args[0]) if node.args else None
                shell_enabled = name in {"os.system", "os.popen"} or any(
                    keyword.arg == "shell" and isinstance(keyword.value, ast.Constant) and keyword.value.value is True
                    for keyword in node.keywords
                )
                if command and _DEPENDENCY_RE.search(command):
                    findings.append(
                        self._finding(
                            RiskCategory.DEPENDENCY_INSTALL,
                            RiskLevel.HIGH,
                            "DEPENDENCY_INSTALL",
                            evidence,
                            "Pin and approve dependencies outside the agent execution path.",
                            SafetyDecision.NEEDS_HUMAN_REVIEW,
                            payload_index,
                            secrets,
                        )
                    )
                if command:
                    findings.extend(
                        self._scan_bash(
                            ScriptPayload(language=ScriptLanguage.BASH, content=command),
                            payload_index,
                            secrets,
                            sensitive_values,
                        )
                    )
                findings.append(
                    self._finding(
                        RiskCategory.PROCESS_EXECUTION,
                        RiskLevel.HIGH if shell_enabled else RiskLevel.MEDIUM,
                        "PROCESS_SHELL_EXECUTION" if shell_enabled else "PROCESS_SUBPROCESS",
                        evidence,
                        "Review the resolved executable and arguments before execution.",
                        SafetyDecision.DENY if shell_enabled else SafetyDecision.NEEDS_HUMAN_REVIEW,
                        payload_index,
                        secrets,
                    )
                )

            if name in {"eval", "exec", "builtins.eval", "builtins.exec"}:
                findings.append(
                    self._finding(
                        RiskCategory.PROCESS_EXECUTION,
                        RiskLevel.HIGH,
                        "PROCESS_DYNAMIC_CODE",
                        evidence,
                        "Do not evaluate dynamically supplied code in an agent execution path.",
                        SafetyDecision.DENY,
                        payload_index,
                        secrets,
                    )
                )

            if name in {"os.fork", "os.forkpty"}:
                findings.append(
                    self._finding(
                        RiskCategory.RESOURCE_ABUSE,
                        RiskLevel.HIGH,
                        "RESOURCE_PROCESS_FORK",
                        evidence,
                        "Use a bounded executor instead of creating child processes directly.",
                        SafetyDecision.DENY,
                        payload_index,
                        secrets,
                    )
                )
            elif name.startswith("multiprocessing.") or name in {"asyncio.create_task", "asyncio.gather"}:
                findings.append(
                    self._finding(
                        RiskCategory.RESOURCE_ABUSE,
                        RiskLevel.MEDIUM,
                        "RESOURCE_CONCURRENCY",
                        evidence,
                        "Review and bound process or task concurrency.",
                        SafetyDecision.NEEDS_HUMAN_REVIEW,
                        payload_index,
                        secrets,
                    )
                )

            if name in {"time.sleep", "asyncio.sleep"} and node.args:
                duration = node.args[0].value if isinstance(node.args[0], ast.Constant) else None
                if not isinstance(duration, (int, float)) or duration > self.policy.max_sleep_seconds:
                    findings.append(
                        self._finding(
                            RiskCategory.RESOURCE_ABUSE,
                            RiskLevel.MEDIUM,
                            "RESOURCE_LONG_SLEEP",
                            evidence,
                            "Use a bounded sleep within the configured limit.",
                            SafetyDecision.NEEDS_HUMAN_REVIEW,
                            payload_index,
                            secrets,
                        )
                    )

            if name in {
                "print",
                "logging.info",
                "logging.warning",
                "logging.error",
                "logger.info",
                "logger.warning",
                "logger.error",
            } and self._contains_sensitive_reference(node, tainted_names, sensitive_values):
                findings.append(
                    self._finding(
                        RiskCategory.SENSITIVE_DATA,
                        RiskLevel.CRITICAL,
                        "SENSITIVE_OUTPUT",
                        evidence,
                        "Do not emit credentials or secret-bearing environment variables.",
                        SafetyDecision.DENY,
                        payload_index,
                        secrets,
                    )
                )
        return findings

    def _scan_bash(
        self,
        payload: ScriptPayload,
        payload_index: int,
        secrets: Iterable[str],
        sensitive_values: Iterable[str],
    ) -> list[SafetyFinding]:
        script = payload.content
        if payload.argv and payload.source in {"command", "cmd"}:
            script = f"{script} {shlex.join(payload.argv)}"
        executable_script = "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("#"))
        findings: list[SafetyFinding] = []
        secret_values = [value for value in sensitive_values if value]

        if _FORK_BOMB_RE.search(executable_script):
            findings.append(
                self._finding(
                    RiskCategory.RESOURCE_ABUSE,
                    RiskLevel.CRITICAL,
                    "RESOURCE_FORK_BOMB",
                    script,
                    "Remove unbounded process creation.",
                    SafetyDecision.DENY,
                    payload_index,
                    secrets,
                )
            )
        if re.search(r"\b(?:while\s+true|while\s+:|for\s*\(\s*;\s*;\s*\))\b", executable_script):
            findings.append(
                self._finding(
                    RiskCategory.RESOURCE_ABUSE,
                    RiskLevel.HIGH,
                    "RESOURCE_INFINITE_LOOP",
                    script,
                    "Use a bounded loop with an explicit exit condition.",
                    SafetyDecision.DENY,
                    payload_index,
                    secrets,
                )
            )
        if any(self._path_appears(executable_script, path) for path in self.policy.forbidden_paths):
            findings.append(
                self._finding(
                    RiskCategory.FILE_OPERATION,
                    RiskLevel.CRITICAL,
                    "FILE_SENSITIVE_PATH",
                    script,
                    "Do not access configured sensitive paths.",
                    SafetyDecision.DENY,
                    payload_index,
                    secrets,
                )
            )
        writes_data = bool(
            re.search(r"(?:^|[^<])(?:>>?|[0-9]+>>?)\s*\S+", executable_script)
            or re.search(r"\b(?:tee|cp|mv|install|dd|truncate|fallocate)\b", executable_script)
        )
        if writes_data and any(
            self._path_appears(executable_script, path) for path in self.policy.protected_write_paths
        ):
            findings.append(
                self._finding(
                    RiskCategory.FILE_OPERATION,
                    RiskLevel.CRITICAL,
                    "FILE_SYSTEM_WRITE",
                    script,
                    "Write only to a reviewed workspace-relative path.",
                    SafetyDecision.DENY,
                    payload_index,
                    secrets,
                )
            )
        sensitive_reference = any(
            _SENSITIVE_NAME_RE.search(match.group(1)) for match in _SHELL_VARIABLE_RE.finditer(executable_script)
        ) or any(secret in executable_script for secret in secret_values)
        network_command = any(
            command in {"curl", "wget", "nc", "ncat", "telnet"}
            for command, _ in self._shell_commands(executable_script)
        )
        if network_command and sensitive_reference:
            findings.append(
                self._finding(
                    RiskCategory.SENSITIVE_DATA,
                    RiskLevel.CRITICAL,
                    "SENSITIVE_EXFILTRATION",
                    script,
                    "Do not send credentials or secret-bearing environment variables over the network.",
                    SafetyDecision.DENY,
                    payload_index,
                    secrets,
                )
            )
        if writes_data and sensitive_reference:
            findings.append(
                self._finding(
                    RiskCategory.SENSITIVE_DATA,
                    RiskLevel.CRITICAL,
                    "SENSITIVE_FILE_WRITE",
                    script,
                    "Do not persist credentials or secret-bearing environment variables.",
                    SafetyDecision.DENY,
                    payload_index,
                    secrets,
                )
            )
        write_size = self._bash_write_size(executable_script)
        if write_size is not None and write_size > self.policy.max_file_write_bytes:
            findings.append(
                self._finding(
                    RiskCategory.RESOURCE_ABUSE,
                    RiskLevel.HIGH,
                    "RESOURCE_LARGE_FILE_WRITE",
                    script,
                    "Keep generated files within the configured write-size limit.",
                    SafetyDecision.DENY,
                    payload_index,
                    secrets,
                )
            )
        if re.search(r"(?:^|[;&|\n]\s*)sudo\b", executable_script):
            findings.append(
                self._finding(
                    RiskCategory.PROCESS_EXECUTION,
                    RiskLevel.CRITICAL,
                    "PROCESS_PRIVILEGE_ESCALATION",
                    script,
                    "Do not run privileged commands from an agent tool.",
                    SafetyDecision.DENY,
                    payload_index,
                    secrets,
                )
            )
        if _SHELL_BYPASS_RE.search(executable_script):
            findings.append(
                self._finding(
                    RiskCategory.PROCESS_EXECUTION,
                    RiskLevel.HIGH,
                    "PROCESS_SHELL_BYPASS",
                    script,
                    "Avoid eval, nested shells, and command substitution.",
                    SafetyDecision.DENY,
                    payload_index,
                    secrets,
                )
            )
        if _DEPENDENCY_RE.search(executable_script):
            findings.append(
                self._finding(
                    RiskCategory.DEPENDENCY_INSTALL,
                    RiskLevel.HIGH,
                    "DEPENDENCY_INSTALL",
                    script,
                    "Pin and approve dependencies outside the agent execution path.",
                    SafetyDecision.NEEDS_HUMAN_REVIEW,
                    payload_index,
                    secrets,
                )
            )
        if "|" in executable_script and "||" not in executable_script:
            findings.append(
                self._finding(
                    RiskCategory.PROCESS_EXECUTION,
                    RiskLevel.MEDIUM,
                    "PROCESS_PIPELINE",
                    script,
                    "Review every command and data flow in the pipeline.",
                    SafetyDecision.NEEDS_HUMAN_REVIEW,
                    payload_index,
                    secrets,
                )
            )
        if re.search(r"(?:^|[^&])&(?:\s|$)", executable_script):
            findings.append(
                self._finding(
                    RiskCategory.PROCESS_EXECUTION,
                    RiskLevel.MEDIUM,
                    "PROCESS_BACKGROUND",
                    script,
                    "Run the command in the foreground with a bounded timeout.",
                    SafetyDecision.NEEDS_HUMAN_REVIEW,
                    payload_index,
                    secrets,
                )
            )
        if re.search(
            r"\b(?:echo|printf)\b[^\n]*(?:\$(?:\{)?(?:API_KEY|TOKEN|SECRET|PASSWORD|PASSWD))",
            executable_script,
            re.IGNORECASE,
        ):
            findings.append(
                self._finding(
                    RiskCategory.SENSITIVE_DATA,
                    RiskLevel.CRITICAL,
                    "SENSITIVE_OUTPUT",
                    script,
                    "Do not print secret-bearing environment variables.",
                    SafetyDecision.DENY,
                    payload_index,
                    secrets,
                )
            )

        for match in _URL_RE.finditer(executable_script):
            findings.extend(self._network_findings(match.group(0), match.group(0), payload_index, secrets))
        if re.search(r"\b(?:curl|wget)\b", executable_script) and not _URL_RE.search(executable_script):
            findings.extend(self._network_findings(None, script, payload_index, secrets))

        for match in re.finditer(r"\bsleep\s+([^\s;&|]+)", executable_script):
            try:
                duration = float(match.group(1))
            except ValueError:
                duration = self.policy.max_sleep_seconds + 1
            if duration > self.policy.max_sleep_seconds:
                findings.append(
                    self._finding(
                        RiskCategory.RESOURCE_ABUSE,
                        RiskLevel.MEDIUM,
                        "RESOURCE_LONG_SLEEP",
                        match.group(0),
                        "Use a bounded sleep within the configured limit.",
                        SafetyDecision.NEEDS_HUMAN_REVIEW,
                        payload_index,
                        secrets,
                    )
                )

        for command, arguments in self._shell_commands(executable_script):
            if command == "rm":
                flags = "".join(argument[1:] for argument in arguments if argument.startswith("-"))
                if "r" in flags and "f" in flags:
                    findings.append(
                        self._finding(
                            RiskCategory.FILE_OPERATION,
                            RiskLevel.CRITICAL,
                            "FILE_RECURSIVE_DELETE",
                            " ".join([command, *arguments]),
                            "Remove recursive forced deletion from agent-executed commands.",
                            SafetyDecision.DENY,
                            payload_index,
                            secrets,
                        )
                    )
            if command == "find" and "-delete" in arguments:
                findings.append(
                    self._finding(
                        RiskCategory.FILE_OPERATION,
                        RiskLevel.HIGH,
                        "FILE_DESTRUCTIVE_OPERATION",
                        " ".join([command, *arguments]),
                        "Remove recursive deletion or constrain it to an isolated workspace.",
                        SafetyDecision.DENY,
                        payload_index,
                        secrets,
                    )
                )
            if command in {"mkfs", "fdisk"} or (
                command == "dd" and any(argument.startswith("of=/dev/") for argument in arguments)
            ):
                findings.append(
                    self._finding(
                        RiskCategory.FILE_OPERATION,
                        RiskLevel.CRITICAL,
                        "FILE_DEVICE_OVERWRITE",
                        " ".join([command, *arguments]),
                        "Do not modify block devices from an agent tool.",
                        SafetyDecision.DENY,
                        payload_index,
                        secrets,
                    )
                )
            if command in {"nc", "ncat", "telnet"}:
                host = next((arg for arg in arguments if not arg.startswith("-") and not arg.isdigit()), None)
                findings.extend(
                    self._network_findings(
                        f"tcp://{host}" if host else None,
                        " ".join([command, *arguments]),
                        payload_index,
                        secrets,
                    )
                )
            if command not in self.policy.allowed_commands:
                findings.append(
                    self._finding(
                        RiskCategory.PROCESS_EXECUTION,
                        RiskLevel.MEDIUM,
                        "PROCESS_COMMAND_NOT_ALLOWLISTED",
                        command,
                        "Add a reviewed command name to allowed_commands or require human approval.",
                        SafetyDecision.NEEDS_HUMAN_REVIEW,
                        payload_index,
                        secrets,
                    )
                )
        return findings

    def _network_findings(
        self,
        target: str | None,
        evidence: str,
        payload_index: int,
        secrets: Iterable[str],
    ) -> list[SafetyFinding]:
        if target is None:
            return [
                self._finding(
                    RiskCategory.NETWORK_ACCESS,
                    RiskLevel.HIGH,
                    "NETWORK_DYNAMIC_TARGET",
                    evidence,
                    "Resolve and review the destination before execution.",
                    SafetyDecision.NEEDS_HUMAN_REVIEW,
                    payload_index,
                    secrets,
                )
            ]
        hostname = (urlparse(target).hostname or "").lower().rstrip(".")
        if hostname and any(
            hostname == allowed or hostname.endswith(f".{allowed}") for allowed in self.policy.allowed_domains
        ):
            return []
        return [
            self._finding(
                RiskCategory.NETWORK_ACCESS,
                RiskLevel.CRITICAL,
                "NETWORK_DOMAIN_NOT_ALLOWED",
                target,
                "Add a reviewed hostname to allowed_domains or remove the network request.",
                SafetyDecision.DENY,
                payload_index,
                secrets,
            )
        ]

    def _finding(
        self,
        category: RiskCategory,
        risk_level: RiskLevel,
        rule_id: str,
        evidence: str,
        recommendation: str,
        decision: SafetyDecision,
        payload_index: int,
        secrets: Iterable[str],
    ) -> SafetyFinding:
        safe_evidence, _ = redact_text(evidence, secrets=secrets, max_chars=self.policy.evidence_max_chars)
        safe_recommendation, _ = redact_text(
            recommendation,
            secrets=secrets,
            max_chars=self.policy.evidence_max_chars,
        )
        return SafetyFinding(
            category=category,
            risk_level=risk_level,
            rule_id=rule_id,
            evidence=safe_evidence,
            recommendation=safe_recommendation,
            decision=decision,
            payload_index=payload_index,
        )

    def _is_forbidden_path(self, value: str) -> bool:
        return any(self._path_appears(value, path) for path in self.policy.forbidden_paths)

    def _is_protected_write_path(self, value: str) -> bool:
        return any(self._path_appears(value, path) for path in self.policy.protected_write_paths)

    @staticmethod
    def _path_appears(value: str, configured: str) -> bool:
        normalized_value = value.replace("\\", "/")
        normalized_path = configured.replace("\\", "/")
        expanded = normalized_path.replace("~/", "/home/user/")
        candidates = {normalized_path, expanded}
        if normalized_path.startswith("~/"):
            candidates.add(normalized_path[2:])
        if PurePath(normalized_path).name.startswith(".env"):
            return bool(re.search(r"(?:^|[/\s\"'])\.env(?:\.[\w-]+)?(?:$|[/\s\"'])", normalized_value))
        return any(candidate and candidate in normalized_value for candidate in candidates)

    @staticmethod
    def _is_network_call(name: str) -> bool:
        return (
            name in {"urllib.request.urlopen", "socket.create_connection"}
            or name.startswith("requests.")
            or name.startswith("aiohttp.")
            or name.startswith("httpx.")
            or name.startswith("socket.")
        )

    @staticmethod
    def _contains_sensitive_reference(
        node: ast.AST,
        tainted_names: Iterable[str] = (),
        sensitive_values: Iterable[str] = (),
    ) -> bool:
        tainted = set(tainted_names)
        explicit_values = [value for value in sensitive_values if value]
        for child in ast.walk(node):
            if isinstance(child, ast.Constant) and isinstance(child.value, str):
                value = child.value
                if any(secret in value for secret in explicit_values):
                    return True
                identifier = re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,80}", value)
                assignment = re.search(
                    r"(?i)\b(?:api[_-]?key|token|secret|password|passwd|private[_-]?key)\b\s*[:=]",
                    value,
                )
                if (identifier and _SENSITIVE_NAME_RE.search(value)) or assignment or "PRIVATE KEY-----" in value:
                    return True
            if isinstance(child, ast.Name):
                if child.id in tainted or _SENSITIVE_NAME_RE.search(child.id):
                    return True
        return False

    @classmethod
    def _bash_write_size(cls, script: str) -> int | None:
        """Estimate explicit large writes from common shell utilities."""

        sizes: list[int] = []
        for match in re.finditer(r"\b(?:truncate\s+-s|fallocate\s+-l)\s+([0-9]+(?:\.[0-9]+)?[KMGT]?(?:i?B)?)", script):
            size = cls._parse_size(match.group(1))
            if size is not None:
                sizes.append(size)
        for command, arguments in cls._shell_commands(script):
            if command != "dd":
                continue
            block_size = next((arg[3:] for arg in arguments if arg.startswith("bs=")), None)
            count = next((arg[6:] for arg in arguments if arg.startswith("count=")), None)
            parsed_block = cls._parse_size(block_size) if block_size else None
            try:
                parsed_count = int(count) if count is not None else None
            except ValueError:
                parsed_count = None
            if parsed_block is not None and parsed_count is not None and parsed_count >= 0:
                sizes.append(parsed_block * parsed_count)
        return max(sizes) if sizes else None

    @staticmethod
    def _parse_size(value: str) -> int | None:
        match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)([KMGT]?)(?:i?B)?", value, re.IGNORECASE)
        if not match:
            return None
        factor = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}[match.group(2).upper()]
        return int(float(match.group(1)) * factor)

    @staticmethod
    def _python_evidence(source: str, node: ast.AST) -> str:
        return ast.get_source_segment(source, node) or type(node).__name__

    @staticmethod
    def _shell_commands(script: str) -> list[tuple[str, list[str]]]:
        cleaned = re.sub(r"^\s*(?:#![^\n]*\n|set\s+-[^\n]*\n)*", "", script)
        tokens: list[str] = []
        for line in cleaned.splitlines() or [cleaned]:
            try:
                lexer = shlex.shlex(line, posix=True, punctuation_chars=";&|")
                lexer.commenters = "#"
                lexer.whitespace_split = True
                tokens.extend(lexer)
                tokens.append(";")
            except ValueError:
                return [("<unparsed>", [])]
        segments: list[list[str]] = []
        current: list[str] = []
        for token in tokens:
            if token and set(token) <= {";", "&", "|"}:
                if current:
                    segments.append(current)
                    current = []
            else:
                current.append(token)
        if current:
            segments.append(current)

        commands: list[tuple[str, list[str]]] = []
        shell_keywords = {"if", "then", "do", "done", "fi", "elif", "else", "in"}
        for segment in segments:
            while segment and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", segment[0]):
                segment.pop(0)
            while segment and segment[0] in shell_keywords:
                segment.pop(0)
            while segment and segment[0] in {"command", "env", "nohup", "sudo"}:
                segment.pop(0)
                while segment and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", segment[0]):
                    segment.pop(0)
            if segment:
                commands.append((segment[0].rsplit("/", 1)[-1], segment[1:]))
        return commands
