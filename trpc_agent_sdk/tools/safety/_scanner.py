# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Static scanner for Python and Bash tool scripts."""

from __future__ import annotations

import ast
import re
import shlex
import time
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Iterable
from typing import Sequence
from urllib.parse import urlparse

from ._policy import ToolSafetyPolicy
from ._types import SafetyDecision
from ._types import SafetyRiskLevel
from ._types import ToolSafetyFinding
from ._types import ToolSafetyReport
from ._types import ToolSafetyScanRequest
from ._types import max_risk_level
from ._types import risk_level_value


SECRET_VALUE_RE = re.compile(
    r"(?i)(api[_-]?key|access[_-]?token|secret|password|private[_-]?key|authorization)\s*[:=]\s*"
    r"['\"]?([A-Za-z0-9_./+=:-]{12,})"
)
URL_RE = re.compile(r"(?i)\bhttps?://[^\s'\"<>)]+" )
ENV_VAR_RE = re.compile(r"\$(?:\{)?([A-Za-z_][A-Za-z0-9_]*)")
SHELL_CONTROL_RE = re.compile(r"(\|\||&&|;|\||`|\$\(|>|<)")
FORK_BOMB_RE = re.compile(r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:")

NETWORK_COMMANDS = {"curl", "wget", "nc", "netcat", "ssh", "scp", "rsync", "ftp"}
INSTALL_COMMANDS = {
    "apt",
    "apt-get",
    "brew",
    "conda",
    "easy_install",
    "gem",
    "npm",
    "pip",
    "pip3",
    "pnpm",
    "poetry",
    "uv",
    "yarn",
}
DANGEROUS_SHELL_COMMANDS = {
    "chmod",
    "chown",
    "dd",
    "kill",
    "killall",
    "mkfs",
    "mount",
    "reboot",
    "service",
    "shutdown",
    "sudo",
    "su",
    "systemctl",
}
SECRET_ENV_NAMES = {
    "API_KEY",
    "ACCESS_TOKEN",
    "AUTHORIZATION",
    "GITHUB_TOKEN",
    "OPENAI_API_KEY",
    "PASSWORD",
    "PRIVATE_KEY",
    "SECRET",
    "TOKEN",
}
SENSITIVE_SINK_NAMES = {"print", "logging.info", "logging.warning", "logging.error", "logger.info", "logger.warning",
                        "logger.error", "sys.stdout.write", "sys.stderr.write"}
NETWORK_PY_MODULES = {"requests", "aiohttp", "httpx", "urllib", "socket", "websocket"}
SUBPROCESS_CALLS = {"subprocess.run", "subprocess.call", "subprocess.Popen", "subprocess.check_call",
                    "subprocess.check_output", "os.system", "os.popen", "os.spawnl", "os.spawnle", "os.spawnlp",
                    "os.spawnlpe", "os.spawnv", "os.spawnve", "os.spawnvp", "os.spawnvpe", "pty.spawn"}
FILE_OPEN_CALLS = {"open", "Path.open", "pathlib.Path.open"}
DANGEROUS_FILE_CALLS = {"os.remove", "os.unlink", "os.rmdir", "shutil.rmtree", "Path.unlink", "Path.rmdir",
                        "pathlib.Path.unlink", "pathlib.Path.rmdir"}
INSTALL_PY_MODULES = {"pip", "ensurepip"}
SHELL_WRITE_COMMANDS = {"cp", "install", "mkdir", "mv", "tee", "touch"}
PARALLEL_PY_CALLS = {
    "asyncio.gather",
    "concurrent.futures.ProcessPoolExecutor",
    "concurrent.futures.ThreadPoolExecutor",
    "multiprocessing.Pool",
}


class ToolSafetyScanner:
    """Policy-driven static scanner for tool scripts."""

    def __init__(self, policy: ToolSafetyPolicy | None = None):
        self.policy = policy or ToolSafetyPolicy()

    def scan(self, request: ToolSafetyScanRequest) -> ToolSafetyReport:
        """Scan a script and return a structured report."""
        started = time.perf_counter()
        findings: list[ToolSafetyFinding] = []
        language = normalize_language(request.language)
        script = request.script or ""

        if language == "python":
            findings.extend(self._scan_python(script))
        elif language == "bash":
            findings.extend(self._scan_bash(script))
        else:
            findings.append(
                finding(
                    "TSG-LANG-UNKNOWN",
                    "unknown_language",
                    SafetyRiskLevel.MEDIUM,
                    f"Unsupported script language: {request.language}",
                    request.language,
                    "Require a human review or add a language-specific scanner before execution.",
                ))

        findings.extend(self._scan_common_text(script))
        findings.extend(self._scan_command_args(request.command_args))
        findings.extend(self._scan_env(request.env))

        findings = dedupe_findings(findings)
        decision = self._decide(findings)
        duration_ms = (time.perf_counter() - started) * 1000
        risk_level = max_risk_level([item.risk_level for item in findings])
        redacted = any(item.redacted for item in findings)
        rule_ids = [item.rule_id for item in findings]
        primary_rule_id = primary_rule(findings)
        blocked = decision == SafetyDecision.DENY or (
            decision == SafetyDecision.NEEDS_HUMAN_REVIEW and self.policy.block_on_review)
        report = ToolSafetyReport(
            decision=decision,
            risk_level=risk_level,
            findings=findings,
            duration_ms=duration_ms,
            language=language,
            scanned_at=datetime.now(timezone.utc).isoformat(),
            tool_name=request.tool_name,
            cwd=request.cwd,
            policy_name=self.policy.name,
            policy_version=self.policy.version,
            blocked=blocked,
            redacted=redacted,
            summary=build_summary(decision, risk_level, findings),
            telemetry_attributes={
                "tool.safety.decision": decision.value,
                "tool.safety.risk_level": risk_level.value,
                "tool.safety.rule_id": primary_rule_id,
                "tool.safety.rule_ids": ",".join(rule_ids),
                "tool.safety.finding_count": len(findings),
                "tool.safety.redacted": redacted,
                "tool.safety.blocked": blocked,
                "tool.safety.duration_ms": duration_ms,
            },
        )
        return report

    def _decide(self, findings: Sequence[ToolSafetyFinding]) -> SafetyDecision:
        if not findings:
            return SafetyDecision.ALLOW
        max_level = max(risk_level_value(item.risk_level) for item in findings)
        if max_level >= risk_level_value(self.policy.deny_risk_level):
            return SafetyDecision.DENY
        if max_level >= risk_level_value(self.policy.review_risk_level):
            return SafetyDecision.NEEDS_HUMAN_REVIEW
        return SafetyDecision.ALLOW

    def _scan_common_text(self, script: str) -> list[ToolSafetyFinding]:
        findings: list[ToolSafetyFinding] = []
        for match in SECRET_VALUE_RE.finditer(script):
            line_no = line_number(script, match.start())
            findings.append(
                finding(
                    "TSG-SECRETS-LITERAL",
                    "sensitive_information_leak",
                    SafetyRiskLevel.HIGH,
                    "Script contains a literal secret-like value.",
                    redact_secret(match.group(0)),
                    "Move secrets to a managed secret store and avoid printing or embedding them in scripts.",
                    line_no=line_no,
                    redacted=True,
                ))
        if FORK_BOMB_RE.search(script):
            findings.append(
                finding(
                    "TSG-RESOURCE-FORK-BOMB",
                    "resource_abuse",
                    SafetyRiskLevel.CRITICAL,
                    "Script contains a shell fork bomb pattern.",
                    ":(){ :|:& };:",
                    "Reject the script and investigate the tool input source.",
                ))
        return findings

    def _scan_command_args(self, command_args: Sequence[str]) -> list[ToolSafetyFinding]:
        findings: list[ToolSafetyFinding] = []
        if not command_args:
            return findings
        command = " ".join(str(arg) for arg in command_args)
        findings.extend(self._scan_bash(command, from_args=True))
        return findings

    def _scan_env(self, env: object) -> list[ToolSafetyFinding]:
        findings: list[ToolSafetyFinding] = []
        if not isinstance(env, dict):
            return findings
        for key, value in env.items():
            key_text = str(key)
            if is_secret_name(key_text) and value:
                findings.append(
                    finding(
                        "TSG-SECRETS-ENV",
                        "sensitive_information_leak",
                        SafetyRiskLevel.MEDIUM,
                        f"Environment variable {key_text} appears to contain a secret.",
                        f"{key_text}=<redacted>",
                        "Pass only the minimum required environment to tools and redact secrets in audit logs.",
                        redacted=True,
                    ))
        return findings

    def _scan_python(self, script: str) -> list[ToolSafetyFinding]:
        findings: list[ToolSafetyFinding] = []
        try:
            tree = ast.parse(script)
        except SyntaxError as exc:
            return [
                finding(
                    "TSG-PY-SYNTAX",
                    "parse_error",
                    SafetyRiskLevel.MEDIUM,
                    "Python script could not be parsed for static analysis.",
                    exc.text.strip() if exc.text else str(exc),
                    "Require human review or fix syntax before execution.",
                    line_no=exc.lineno,
                )
            ]

        annotate_parents(tree)
        analyzer = _PythonAnalyzer(script, self.policy)
        analyzer.visit(tree)
        findings.extend(analyzer.findings)
        return findings

    def _scan_bash(self, script: str, *, from_args: bool = False) -> list[ToolSafetyFinding]:
        findings: list[ToolSafetyFinding] = []
        for line_no, raw_line in enumerate(script.splitlines() or [script], start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            findings.extend(self._scan_bash_line(line, line_no, from_args=from_args))
        return findings

    def _scan_bash_line(self, line: str, line_no: int, *, from_args: bool = False) -> list[ToolSafetyFinding]:
        findings: list[ToolSafetyFinding] = []
        try:
            tokens = shlex.split(line, posix=True)
        except ValueError:
            tokens = line.split()

        if not tokens:
            return findings

        if SHELL_CONTROL_RE.search(line):
            findings.append(
                finding(
                    "TSG-SHELL-CONTROL",
                    "process_command",
                    SafetyRiskLevel.MEDIUM,
                    "Shell control operators can hide chained commands or injection payloads.",
                    line,
                    "Split commands into explicit argument lists or require human review for shell metacharacters.",
                    line_no=line_no,
                ))

        if re.search(r"\bwhile\s+true\b|\bwhile\s+:\b|\bfor\s+\(\s*;\s*;\s*\)", line):
            findings.append(
                finding(
                    "TSG-RESOURCE-INFINITE-LOOP",
                    "resource_abuse",
                    SafetyRiskLevel.HIGH,
                    "Bash script contains an apparent infinite loop.",
                    line,
                    "Add bounded iteration and enforce runtime limits before execution.",
                    line_no=line_no,
                ))

        if re.search(r"\bsleep\s+(\d+)", line):
            seconds = int(re.search(r"\bsleep\s+(\d+)", line).group(1))  # type: ignore[union-attr]
            if seconds > self.policy.max_sleep_seconds:
                findings.append(
                    finding(
                        "TSG-RESOURCE-LONG-SLEEP",
                        "resource_abuse",
                        SafetyRiskLevel.MEDIUM,
                        f"Sleep duration {seconds}s exceeds policy limit.",
                        line,
                        "Use shorter sleeps and rely on executor timeout controls.",
                        line_no=line_no,
                    ))

        if has_background_operator(tokens, line):
            findings.append(
                finding(
                    "TSG-PROCESS-BACKGROUND",
                    "process_command",
                    SafetyRiskLevel.MEDIUM,
                    "Shell command starts a background process.",
                    line,
                    "Run tools in the foreground with explicit timeout and cancellation controls.",
                    line_no=line_no,
                ))

        for url in URL_RE.findall(line):
            host = urlparse(url).hostname or ""
            if not self.policy.is_domain_allowed(host):
                findings.append(
                    finding(
                        "TSG-NETWORK-NONALLOWLIST",
                        "network_egress",
                        SafetyRiskLevel.HIGH,
                        f"Network request targets non-allowlisted domain {host}.",
                        url,
                        "Add the domain to allowed_domains only after review, or block the request.",
                        line_no=line_no,
                    ))

        commands = split_shell_commands(tokens)
        for command_tokens in commands:
            if not command_tokens:
                continue
            command = Path(command_tokens[0]).name.lower()
            evidence = " ".join(command_tokens)
            if command in {"bash", "sh"} and any(token in {"-c", "-lc"} for token in command_tokens):
                nested_script = command_tokens[-1]
                nested_findings = self._scan_bash(nested_script, from_args=from_args)
                for item in nested_findings:
                    item.line_no = line_no
                    findings.append(item)
            if command in NETWORK_COMMANDS and not line_has_allowlisted_url(line, self.policy):
                findings.append(
                    finding(
                        "TSG-NETWORK-COMMAND",
                        "network_egress",
                        SafetyRiskLevel.HIGH,
                        f"Command {command} can open outbound network connections.",
                        evidence,
                        "Restrict network tools to allowlisted domains or use a reviewed fetch tool.",
                        line_no=line_no,
                    ))
            if command in INSTALL_COMMANDS and is_install_invocation(command_tokens):
                findings.append(
                    finding(
                        "TSG-DEPENDENCY-INSTALL",
                        "dependency_install",
                        SafetyRiskLevel.HIGH,
                        f"Command {command} may install dependencies or modify the runtime.",
                        evidence,
                        "Prebuild dependencies in a trusted image or require human review.",
                        line_no=line_no,
                    ))
            timeout_seconds = timeout_seconds_from_tokens(command_tokens)
            if timeout_seconds is not None and timeout_seconds > self.policy.max_timeout_seconds:
                findings.append(
                    finding(
                        "TSG-RESOURCE-LONG-TIMEOUT",
                        "resource_abuse",
                        SafetyRiskLevel.MEDIUM,
                        f"Command timeout {timeout_seconds}s exceeds policy limit.",
                        evidence,
                        "Keep tool timeouts within policy.max_timeout_seconds.",
                        line_no=line_no,
                    ))
            parallel_tasks = parallel_tasks_from_tokens(command_tokens)
            if parallel_tasks is not None and (
                    parallel_tasks == 0 or parallel_tasks > self.policy.max_parallel_tasks):
                findings.append(
                    finding(
                        "TSG-RESOURCE-PARALLELISM",
                        "resource_abuse",
                        SafetyRiskLevel.HIGH if parallel_tasks == 0 else SafetyRiskLevel.MEDIUM,
                        f"Command can start {parallel_tasks or 'unbounded'} parallel task(s).",
                        evidence,
                        "Bound parallelism to policy.max_parallel_tasks and enforce process limits.",
                        line_no=line_no,
                    ))
            if command in DANGEROUS_SHELL_COMMANDS:
                findings.append(
                    finding(
                        "TSG-SHELL-DANGEROUS-COMMAND",
                        "process_command",
                        SafetyRiskLevel.HIGH,
                        f"Command {command} can alter system state or privileges.",
                        evidence,
                        "Deny privileged/system commands unless executed in a locked-down sandbox.",
                        line_no=line_no,
                    ))
            if command == "rm" and is_recursive_delete(command_tokens):
                risk = SafetyRiskLevel.CRITICAL if targets_system_or_denied_path(command_tokens, self.policy) else SafetyRiskLevel.HIGH
                findings.append(
                    finding(
                        "TSG-FILE-RECURSIVE-DELETE",
                        "dangerous_file_operation",
                        risk,
                        "Recursive delete command detected.",
                        evidence,
                        "Reject destructive recursive deletion or restrict it to disposable workspace paths.",
                        line_no=line_no,
                    ))
            if any(self.policy.is_denied_path(token) for token in command_tokens):
                findings.append(
                    finding(
                        "TSG-FILE-DENIED-PATH",
                        "dangerous_file_operation",
                        SafetyRiskLevel.HIGH,
                        "Command references a denied sensitive path.",
                        evidence,
                        "Remove access to credential paths such as .env, ~/.ssh, and cloud credential files.",
                        line_no=line_no,
                    ))
            if command in SHELL_WRITE_COMMANDS and any(
                    self.policy.is_system_write_path(token) for token in command_tokens[1:]):
                findings.append(
                    finding(
                        "TSG-FILE-SYSTEM-WRITE",
                        "dangerous_file_operation",
                        SafetyRiskLevel.CRITICAL,
                        "Command writes to a protected system path.",
                        evidence,
                        "Block writes outside the configured workspace.",
                        line_no=line_no,
                    ))
            for target in redirection_targets(command_tokens):
                if self.policy.is_system_write_path(target):
                    findings.append(
                        finding(
                            "TSG-FILE-SYSTEM-WRITE",
                            "dangerous_file_operation",
                            SafetyRiskLevel.CRITICAL,
                            "Shell redirection writes to a protected system path.",
                            evidence,
                            "Block writes outside the configured workspace.",
                            line_no=line_no,
                        ))
                if self.policy.is_denied_path(target):
                    findings.append(
                        finding(
                            "TSG-FILE-DENIED-PATH",
                            "dangerous_file_operation",
                            SafetyRiskLevel.HIGH,
                            "Shell redirection references a denied sensitive path.",
                            evidence,
                            "Remove direct access to credential paths such as .env, ~/.ssh, and cloud credential files.",
                            line_no=line_no,
                        ))
            if command in {"cat", "grep", "awk", "sed", "tail", "head"} and any(
                    self.policy.is_denied_path(token) for token in command_tokens[1:]):
                findings.append(
                    finding(
                        "TSG-SECRETS-READ",
                        "sensitive_information_leak",
                        SafetyRiskLevel.HIGH,
                        "Command appears to read a secret or credential file.",
                        evidence,
                        "Block direct reads of secrets and use a secret manager interface instead.",
                        line_no=line_no,
                    ))
            if command in {"dd", "truncate", "fallocate"}:
                findings.append(
                    finding(
                        "TSG-RESOURCE-LARGE-WRITE",
                        "resource_abuse",
                        SafetyRiskLevel.MEDIUM,
                        "Command can create very large files.",
                        evidence,
                        "Set disk quotas and require review for bulk writes.",
                        line_no=line_no,
                    ))

        for env_name in ENV_VAR_RE.findall(line):
            if is_secret_name(env_name) and re.search(r"\b(echo|printf|curl|wget|tee)\b", line):
                findings.append(
                    finding(
                        "TSG-SECRETS-SINK",
                        "sensitive_information_leak",
                        SafetyRiskLevel.HIGH,
                        f"Sensitive environment variable {env_name} is sent to output or network sink.",
                        redact_secret(line),
                        "Do not print or transmit secret environment variables.",
                        line_no=line_no,
                        redacted=True,
                    ))

        if from_args and not self.policy.is_command_allowed(tokens[0]):
            findings.append(
                finding(
                    "TSG-COMMAND-NOTALLOWED",
                    "process_command",
                    SafetyRiskLevel.MEDIUM,
                    f"Command {tokens[0]} is not in the policy allowed_commands list.",
                    line,
                    "Add the command to allowed_commands after review or deny the tool invocation.",
                    line_no=line_no,
                ))

        return findings


class _PythonAnalyzer(ast.NodeVisitor):
    """AST-based analyzer for Python scripts."""

    def __init__(self, script: str, policy: ToolSafetyPolicy):
        self.script = script
        self.policy = policy
        self.findings: list[ToolSafetyFinding] = []
        self.imports: dict[str, str] = {}
        self.object_types: dict[str, str] = {}
        self.secret_names: set[str] = set()
        self.opened_paths: dict[str, str] = {}

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        for alias in node.names:
            self.imports[alias.asname or alias.name.split(".")[0]] = alias.name
            if alias.name.split(".")[0] in NETWORK_PY_MODULES:
                self.add(
                    "TSG-NETWORK-IMPORT",
                    "network_egress",
                    SafetyRiskLevel.LOW,
                    f"Python imports network-capable module {alias.name}.",
                    self.segment(node),
                    "Review imported network modules when calls use dynamic or non-allowlisted targets.",
                    node,
                )
            if alias.name.split(".")[0] in INSTALL_PY_MODULES:
                self.add(
                    "TSG-DEPENDENCY-INSTALL-API",
                    "dependency_install",
                    SafetyRiskLevel.MEDIUM,
                    f"Python imports dependency installer module {alias.name}.",
                    self.segment(node),
                    "Avoid runtime dependency installation in tool scripts.",
                    node,
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        module = node.module or ""
        for alias in node.names:
            local = alias.asname or alias.name
            self.imports[local] = f"{module}.{alias.name}" if module else alias.name
        if module.split(".")[0] in NETWORK_PY_MODULES:
            self.add(
                "TSG-NETWORK-IMPORT",
                "network_egress",
                SafetyRiskLevel.LOW,
                f"Python imports network-capable module {module}.",
                self.segment(node),
                "Review imported network modules when calls use dynamic or non-allowlisted targets.",
                node,
            )
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
        value_text = self.constant_string(node.value)
        value_call_name = self.call_name(node.value.func) if isinstance(node.value, ast.Call) else ""
        for target in node.targets:
            target_name = self.name_of(target)
            if target_name and is_secret_name(target_name):
                self.secret_names.add(target_name)
                if value_text:
                    self.add(
                        "TSG-SECRETS-LITERAL",
                        "sensitive_information_leak",
                        SafetyRiskLevel.HIGH,
                        f"Variable {target_name} appears to store a literal secret.",
                        redact_secret(self.segment(node)),
                        "Do not embed secrets in scripts; fetch them through approved secret APIs.",
                        node,
                        redacted=True,
                    )
            if target_name and value_call_name in {"socket.socket"}:
                self.object_types[target_name] = value_call_name
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:  # noqa: N802
        if isinstance(node.iter, ast.Call) and self.call_name(node.iter.func) == "range":
            count = self.range_count(node.iter)
            if count is not None and count > self.policy.max_loop_iterations:
                self.add(
                    "TSG-RESOURCE-LARGE-LOOP",
                    "resource_abuse",
                    SafetyRiskLevel.MEDIUM,
                    f"Loop iteration count {count} exceeds policy limit.",
                    self.segment(node),
                    "Bound loop sizes and enforce executor CPU timeouts.",
                    node,
                )
        self.generic_visit(node)

    def visit_While(self, node: ast.While) -> None:  # noqa: N802
        if isinstance(node.test, ast.Constant) and node.test.value is True:
            self.add(
                "TSG-RESOURCE-INFINITE-LOOP",
                "resource_abuse",
                SafetyRiskLevel.HIGH,
                "Python script contains while True.",
                self.segment(node),
                "Add a bounded exit condition and enforce runtime limits.",
                node,
            )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        call_name = self.call_name(node.func)
        if not call_name:
            self.generic_visit(node)
            return

        if call_name in SUBPROCESS_CALLS:
            self.handle_process_call(node, call_name)
        elif call_name in DANGEROUS_FILE_CALLS:
            self.handle_dangerous_file_call(node, call_name)
        elif call_name in FILE_OPEN_CALLS:
            self.handle_open_call(node, call_name)
        elif any(call_name.startswith(f"{module}.") for module in NETWORK_PY_MODULES):
            self.handle_network_call(node, call_name)
        elif call_name in {"time.sleep", "asyncio.sleep"}:
            self.handle_sleep_call(node, call_name)
        elif call_name in SENSITIVE_SINK_NAMES:
            self.handle_sensitive_sink(node, call_name)
        elif call_name in {"os.fork", "multiprocessing.Process"}:
            self.add(
                "TSG-RESOURCE-PROCESS-FANOUT",
                "resource_abuse",
                SafetyRiskLevel.MEDIUM,
                f"{call_name} can spawn additional processes.",
                self.segment(node),
                "Limit process fan-out and run inside a sandbox with process limits.",
                node,
            )
        elif call_name in PARALLEL_PY_CALLS:
            self.handle_parallel_call(node, call_name)
        elif call_name.endswith(".read_text") or call_name.endswith(".read_bytes"):
            self.handle_path_read(node, call_name)
        elif call_name.endswith(".write_text") or call_name.endswith(".write_bytes"):
            self.handle_path_write(node, call_name)

        self.generic_visit(node)

    def handle_process_call(self, node: ast.Call, call_name: str) -> None:
        first_arg = node.args[0] if node.args else None
        command_text = self.constant_string(first_arg) if first_arg else ""
        shell_true = keyword_bool(node, "shell")
        risk = SafetyRiskLevel.HIGH if shell_true else SafetyRiskLevel.MEDIUM
        self.add(
            "TSG-PROCESS-SUBPROCESS",
            "process_command",
            risk,
            f"{call_name} executes an external command.",
            self.segment(node),
            "Prefer reviewed tool APIs over subprocess calls; require review for shell=True.",
            node,
        )
        if shell_true or (command_text and SHELL_CONTROL_RE.search(command_text)):
            self.add(
                "TSG-SHELL-INJECTION",
                "process_command",
                SafetyRiskLevel.HIGH,
                "Subprocess call uses shell execution or shell metacharacters.",
                self.segment(node),
                "Use create_subprocess_exec/list arguments and validate every argument.",
                node,
            )
        if command_text:
            scanner = ToolSafetyScanner(self.policy)
            for item in scanner._scan_bash(command_text, from_args=True):  # pylint: disable=protected-access
                item.line_no = node.lineno
                self.findings.append(item)
        timeout_seconds = keyword_number(node, "timeout")
        if timeout_seconds is not None and timeout_seconds > self.policy.max_timeout_seconds:
            self.add(
                "TSG-RESOURCE-LONG-TIMEOUT",
                "resource_abuse",
                SafetyRiskLevel.MEDIUM,
                f"Subprocess timeout {timeout_seconds}s exceeds policy limit.",
                self.segment(node),
                "Keep tool timeouts within policy.max_timeout_seconds.",
                node,
            )
        if keyword_bool(node, "capture_output") or any(
                self.call_name(keyword.value) == "subprocess.PIPE"
                for keyword in node.keywords
                if keyword.arg in {"stdout", "stderr"}):
            self.add(
                "TSG-RESOURCE-OUTPUT-CAPTURE",
                "resource_abuse",
                SafetyRiskLevel.MEDIUM,
                "Subprocess captures unbounded output.",
                self.segment(node),
                "Enforce policy.max_output_bytes when capturing tool output.",
                node,
            )

    def handle_dangerous_file_call(self, node: ast.Call, call_name: str) -> None:
        path_text = self.constant_string(node.args[0]) if node.args else ""
        risk = SafetyRiskLevel.CRITICAL if self.policy.is_system_write_path(path_text) else SafetyRiskLevel.HIGH
        self.add(
            "TSG-FILE-DANGEROUS-OP",
            "dangerous_file_operation",
            risk,
            f"{call_name} can delete files or directories.",
            self.segment(node),
            "Reject destructive file operations unless scoped to a disposable workspace.",
            node,
        )
        if path_text and self.policy.is_denied_path(path_text):
            self.add_denied_path(node, path_text)

    def handle_open_call(self, node: ast.Call, call_name: str) -> None:
        path_text = self.constant_string(node.args[0]) if node.args else ""
        mode = "r"
        if len(node.args) > 1:
            mode = self.constant_string(node.args[1]) or mode
        for kw in node.keywords:
            if kw.arg == "mode":
                mode = self.constant_string(kw.value) or mode
        if path_text and self.policy.is_denied_path(path_text):
            risk_type = "sensitive_information_leak" if "r" in mode and not any(flag in mode for flag in "wa+") else "dangerous_file_operation"
            self.add(
                "TSG-FILE-DENIED-PATH",
                risk_type,
                SafetyRiskLevel.HIGH,
                "Python file access references a denied sensitive path.",
                self.segment(node),
                "Remove direct access to credential paths such as .env, ~/.ssh, and cloud credential files.",
                node,
            )
        if path_text and any(flag in mode for flag in "wa+"):
            risk = SafetyRiskLevel.CRITICAL if self.policy.is_system_write_path(path_text) else SafetyRiskLevel.MEDIUM
            if risk == SafetyRiskLevel.CRITICAL:
                self.add(
                    "TSG-FILE-SYSTEM-WRITE",
                    "dangerous_file_operation",
                    risk,
                    "Python file write targets a protected system path.",
                    self.segment(node),
                    "Block writes outside the configured workspace.",
                    node,
                )
        parent = getattr(node, "parent_assign_target", None)
        if parent and path_text:
            self.opened_paths[parent] = path_text

    def handle_path_write(self, node: ast.Call, call_name: str) -> None:
        path_text = self.path_literal_from_node(getattr(node.func, "value", None))
        if path_text and self.policy.is_system_write_path(path_text):
            self.add(
                "TSG-FILE-SYSTEM-WRITE",
                "dangerous_file_operation",
                SafetyRiskLevel.CRITICAL,
                "Path write targets a protected system path.",
                self.segment(node),
                "Block writes outside the configured workspace.",
                node,
            )
        literal_size = sum(len(self.constant_string(arg) or "") for arg in node.args)
        if literal_size > self.policy.max_literal_write_bytes:
            self.add(
                "TSG-RESOURCE-LARGE-WRITE",
                "resource_abuse",
                SafetyRiskLevel.MEDIUM,
                "Large literal write exceeds policy limit.",
                self.segment(node),
                "Stream bounded output and enforce max output size.",
                node,
            )

    def handle_path_read(self, node: ast.Call, call_name: str) -> None:
        path_text = self.path_literal_from_node(getattr(node.func, "value", None))
        if path_text and self.policy.is_denied_path(path_text):
            self.add(
                "TSG-FILE-DENIED-PATH",
                "sensitive_information_leak",
                SafetyRiskLevel.HIGH,
                "Python path read references a denied sensitive path.",
                self.segment(node),
                "Remove direct reads of secrets and use a secret manager interface instead.",
                node,
            )

    def handle_network_call(self, node: ast.Call, call_name: str) -> None:
        urls = [self.network_target_from_arg(arg) for arg in node.args]
        urls.extend(self.constant_string(kw.value) for kw in node.keywords if kw.arg in {"url", "host"})
        literal_urls = [url for url in urls if url]
        if not literal_urls:
            self.add(
                "TSG-NETWORK-DYNAMIC",
                "network_egress",
                SafetyRiskLevel.MEDIUM,
                f"{call_name} opens a network connection with a dynamic target.",
                self.segment(node),
                "Require human review or constrain network targets to policy allowed_domains.",
                node,
            )
            return
        for url in literal_urls:
            host = extract_host(url)
            if not self.policy.is_domain_allowed(host):
                self.add(
                    "TSG-NETWORK-NONALLOWLIST",
                    "network_egress",
                    SafetyRiskLevel.HIGH,
                    f"Network request targets non-allowlisted domain {host or url}.",
                    self.segment(node),
                    "Add the domain to allowed_domains only after review, or block the request.",
                    node,
                )

    def network_target_from_arg(self, node: ast.AST | None) -> str:
        value = self.constant_string(node)
        if value:
            return value
        if isinstance(node, ast.Tuple) and node.elts:
            return self.constant_string(node.elts[0])
        return ""

    def handle_parallel_call(self, node: ast.Call, call_name: str) -> None:
        max_workers = keyword_number(node, "max_workers")
        if call_name == "asyncio.gather":
            task_count = len(node.args)
            if task_count > self.policy.max_parallel_tasks:
                self.add(
                    "TSG-RESOURCE-PARALLELISM",
                    "resource_abuse",
                    SafetyRiskLevel.MEDIUM,
                    f"asyncio.gather starts {task_count} concurrent task(s).",
                    self.segment(node),
                    "Bound concurrency to policy.max_parallel_tasks.",
                    node,
                )
            return
        if max_workers is None:
            self.add(
                "TSG-RESOURCE-PARALLELISM",
                "resource_abuse",
                SafetyRiskLevel.MEDIUM,
                f"{call_name} may create concurrent workers without a policy-bound limit.",
                self.segment(node),
                "Set max_workers within policy.max_parallel_tasks.",
                node,
            )
        elif max_workers > self.policy.max_parallel_tasks:
            self.add(
                "TSG-RESOURCE-PARALLELISM",
                "resource_abuse",
                SafetyRiskLevel.MEDIUM,
                f"{call_name} max_workers={max_workers} exceeds policy limit.",
                self.segment(node),
                "Set max_workers within policy.max_parallel_tasks.",
                node,
            )

    def handle_sleep_call(self, node: ast.Call, call_name: str) -> None:
        seconds = numeric_constant(node.args[0]) if node.args else None
        if seconds is not None and seconds > self.policy.max_sleep_seconds:
            self.add(
                "TSG-RESOURCE-LONG-SLEEP",
                "resource_abuse",
                SafetyRiskLevel.MEDIUM,
                f"{call_name} duration {seconds}s exceeds policy limit.",
                self.segment(node),
                "Use shorter sleeps and rely on executor timeout controls.",
                node,
            )

    def handle_sensitive_sink(self, node: ast.Call, call_name: str) -> None:
        for arg in node.args:
            names = collect_names(arg)
            if any(is_secret_name(name) or name in self.secret_names for name in names):
                self.add(
                    "TSG-SECRETS-SINK",
                    "sensitive_information_leak",
                    SafetyRiskLevel.HIGH,
                    f"Sensitive variable is sent to {call_name}.",
                    redact_secret(self.segment(node)),
                    "Do not log, print, write, or transmit secret values.",
                    node,
                    redacted=True,
                )

    def add_denied_path(self, node: ast.AST, path_text: str) -> None:
        self.add(
            "TSG-FILE-DENIED-PATH",
            "dangerous_file_operation",
            SafetyRiskLevel.HIGH,
            f"Path {path_text} matches denied_paths policy.",
            self.segment(node),
            "Remove direct access to credential paths or change policy after review.",
            node,
        )

    def add(
        self,
        rule_id: str,
        risk_type: str,
        risk_level: SafetyRiskLevel,
        message: str,
        evidence: str,
        recommendation: str,
        node: ast.AST,
        *,
        redacted: bool = False,
    ) -> None:
        self.findings.append(
            finding(
                rule_id,
                risk_type,
                risk_level,
                message,
                evidence,
                recommendation,
                line_no=getattr(node, "lineno", None),
                column=getattr(node, "col_offset", None),
                redacted=redacted,
            ))

    def segment(self, node: ast.AST) -> str:
        return ast.get_source_segment(self.script, node) or self.node_line(node)

    def node_line(self, node: ast.AST) -> str:
        lineno = getattr(node, "lineno", 1) or 1
        lines = self.script.splitlines()
        if 1 <= lineno <= len(lines):
            return lines[lineno - 1].strip()
        return ""

    def call_name(self, node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return self.imports.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id in self.object_types:
                return f"{self.object_types[node.value.id]}.{node.attr}"
            value_name = self.call_name(node.value)
            if value_name:
                if isinstance(node.value, ast.Name) and "." in value_name and value_name.endswith(f".{node.attr}"):
                    return value_name
                return f"{value_name}.{node.attr}"
            return node.attr
        if isinstance(node, ast.Call):
            return self.call_name(node.func)
        return ""

    def name_of(self, node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return ""

    def constant_string(self, node: ast.AST | None) -> str:
        if node is None:
            return ""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.JoinedStr):
            parts = []
            for value in node.values:
                if isinstance(value, ast.Constant):
                    parts.append(str(value.value))
                else:
                    parts.append("{}")
            return "".join(parts)
        if isinstance(node, ast.List):
            values = [self.constant_string(item) for item in node.elts]
            if any(not value for value in values):
                return ""
            return " ".join(value for value in values if value)
        if isinstance(node, ast.Tuple):
            values = [self.constant_string(item) for item in node.elts]
            if any(not value for value in values):
                return ""
            return " ".join(value for value in values if value)
        if isinstance(node, ast.Call) and self.call_name(node.func) in {"str", "Path", "pathlib.Path"} and node.args:
            return self.constant_string(node.args[0])
        return ""

    def path_literal_from_node(self, node: ast.AST | None) -> str:
        """Extract a literal path from Path("x").expanduser().read_text() chains."""
        if node is None:
            return ""
        direct = self.constant_string(node)
        if direct:
            return direct
        if isinstance(node, ast.Call):
            call_name = self.call_name(node.func)
            if call_name in {"Path", "pathlib.Path", "str"} and node.args:
                return self.constant_string(node.args[0])
            if isinstance(node.func, ast.Attribute) and node.func.attr in {"expanduser", "resolve", "absolute"}:
                return self.path_literal_from_node(node.func.value)
        if isinstance(node, ast.Attribute):
            return self.path_literal_from_node(node.value)
        return ""

    def range_count(self, node: ast.Call) -> int | None:
        values = [numeric_constant(arg) for arg in node.args]
        if not values or any(value is None for value in values):
            return None
        numbers = [int(value) for value in values if value is not None]
        if len(numbers) == 1:
            return max(numbers[0], 0)
        if len(numbers) >= 2:
            start, stop = numbers[:2]
            step = numbers[2] if len(numbers) >= 3 and numbers[2] != 0 else 1
            return max((stop - start + (step - 1)) // step, 0)
        return None


def normalize_language(language: str | None) -> str:
    value = (language or "").lower()
    if value in {"py", "python", "python3", "tool_code"}:
        return "python"
    if value in {"bash", "sh", "shell"}:
        return "bash"
    return value or "python"


def finding(
    rule_id: str,
    risk_type: str,
    risk_level: SafetyRiskLevel,
    message: str,
    evidence: str,
    recommendation: str,
    *,
    line_no: int | None = None,
    column: int | None = None,
    redacted: bool = False,
) -> ToolSafetyFinding:
    """Build a finding with bounded evidence."""
    evidence_text = (evidence or "").strip()
    if len(evidence_text) > 240:
        evidence_text = evidence_text[:237] + "..."
    return ToolSafetyFinding(
        rule_id=rule_id,
        risk_type=risk_type,
        risk_level=risk_level,
        message=message,
        evidence=evidence_text,
        recommendation=recommendation,
        line_no=line_no,
        column=column,
        redacted=redacted,
    )


def dedupe_findings(findings: Iterable[ToolSafetyFinding]) -> list[ToolSafetyFinding]:
    seen = set()
    deduped = []
    for item in findings:
        key = (item.rule_id, item.line_no, item.evidence)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def build_summary(decision: SafetyDecision, risk_level: SafetyRiskLevel, findings: Sequence[ToolSafetyFinding]) -> str:
    if not findings:
        return "No safety risks matched the configured rules."
    return f"{decision.value} due to {len(findings)} finding(s); highest risk is {risk_level.value}."


def primary_rule(findings: Sequence[ToolSafetyFinding]) -> str:
    """Return the highest-risk rule id for telemetry and audit summaries."""
    if not findings:
        return ""
    return max(findings, key=lambda item: risk_level_value(item.risk_level)).rule_id


def line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def redact_secret(text: str) -> str:
    text = SECRET_VALUE_RE.sub(lambda m: f"{m.group(1)}=<redacted>", text)
    text = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{12,}", r"\1<redacted>", text)
    text = re.sub(r"(?i)(sk-[A-Za-z0-9]{6})[A-Za-z0-9_-]+", r"\1<redacted>", text)
    return text


def is_secret_name(name: str | None) -> bool:
    if not name:
        return False
    normalized = re.sub(r"[^A-Za-z0-9]", "_", name).upper()
    if normalized in SECRET_ENV_NAMES:
        return True
    return any(token in normalized for token in ("API_KEY", "TOKEN", "PASSWORD", "SECRET", "PRIVATE_KEY", "CREDENTIAL"))


def numeric_constant(node: ast.AST | None) -> float | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    return None


def keyword_bool(node: ast.Call, name: str) -> bool:
    for keyword in node.keywords:
        if keyword.arg == name and isinstance(keyword.value, ast.Constant):
            return bool(keyword.value.value)
    return False


def keyword_number(node: ast.Call, name: str) -> float | None:
    for keyword in node.keywords:
        if keyword.arg == name:
            return numeric_constant(keyword.value)
    return None


def collect_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            names.add(child.id)
        elif isinstance(child, ast.Attribute):
            names.add(child.attr)
    return names


def extract_host(value: str) -> str:
    parsed = urlparse(value if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", value) else f"//{value}")
    return parsed.hostname or ""


def split_shell_commands(tokens: Sequence[str]) -> list[list[str]]:
    commands: list[list[str]] = []
    current: list[str] = []
    separators = {"|", "||", "&&", ";", "&"}
    for token in tokens:
        if token in separators:
            if current:
                commands.append(current)
                current = []
        elif token in {"sudo", "env", "nohup", "time", "command"} and not current:
            current.append(token)
        else:
            current.append(token)
    if current:
        if current[0] in {"sudo", "env", "nohup", "time", "command"} and len(current) > 1:
            commands.append(current[1:])
        else:
            commands.append(current)
    return commands


def has_background_operator(tokens: Sequence[str], line: str) -> bool:
    return "&" in tokens or bool(re.search(r"(?<!&)&\s*(?:$|[#;])", line))


def is_install_invocation(tokens: Sequence[str]) -> bool:
    command = Path(tokens[0]).name.lower() if tokens else ""
    normalized = [token.lower() for token in tokens]
    if command in {"pip", "pip3", "npm", "pnpm", "yarn", "apt", "apt-get", "brew", "conda", "gem"}:
        return any(token in normalized for token in {"install", "add", "update", "upgrade"})
    if command == "uv":
        return any(token in normalized for token in {"pip", "add", "sync"})
    if command == "poetry":
        return any(token in normalized for token in {"add", "install", "update"})
    return False


def is_recursive_delete(tokens: Sequence[str]) -> bool:
    if not tokens or Path(tokens[0]).name.lower() != "rm":
        return False
    return any(token.startswith("-") and "r" in token.lower() for token in tokens[1:])


def targets_system_or_denied_path(tokens: Sequence[str], policy: ToolSafetyPolicy) -> bool:
    return any(policy.is_system_write_path(token) or policy.is_denied_path(token) for token in tokens[1:])


def redirection_targets(tokens: Sequence[str]) -> list[str]:
    targets: list[str] = []
    for index, token in enumerate(tokens):
        if token in {">", ">>", "1>", "1>>", "2>", "2>>"}:
            if index + 1 < len(tokens):
                targets.append(tokens[index + 1])
            continue
        match = re.match(r"^(?:[12])?>{1,2}(.+)$", token)
        if match:
            targets.append(match.group(1))
    return targets


def timeout_seconds_from_tokens(tokens: Sequence[str]) -> float | None:
    command = Path(tokens[0]).name.lower() if tokens else ""
    if command != "timeout":
        return None
    for token in tokens[1:]:
        if token.startswith("-"):
            continue
        return duration_token_seconds(token)
    return None


def duration_token_seconds(token: str) -> float | None:
    match = re.match(r"^(\d+(?:\.\d+)?)([smhd]?)$", token.lower())
    if not match:
        return None
    value = float(match.group(1))
    unit = match.group(2)
    multipliers = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}
    return value * multipliers.get(unit, 1)


def parallel_tasks_from_tokens(tokens: Sequence[str]) -> int | None:
    command = Path(tokens[0]).name.lower() if tokens else ""
    if command == "parallel":
        for index, token in enumerate(tokens):
            if token in {"-j", "--jobs"} and index + 1 < len(tokens):
                return int_token(tokens[index + 1])
            if token.startswith("-j") and len(token) > 2:
                return int_token(token[2:])
            if token.startswith("--jobs="):
                return int_token(token.split("=", 1)[1])
        return 0
    if command == "xargs":
        for index, token in enumerate(tokens):
            if token in {"-P", "--max-procs"} and index + 1 < len(tokens):
                return int_token(tokens[index + 1])
            if token.startswith("-P") and len(token) > 2:
                return int_token(token[2:])
            if token.startswith("--max-procs="):
                return int_token(token.split("=", 1)[1])
    return None


def int_token(token: str) -> int | None:
    try:
        return int(token)
    except ValueError:
        return None


def line_has_allowlisted_url(line: str, policy: ToolSafetyPolicy) -> bool:
    urls = URL_RE.findall(line)
    if not urls:
        return False
    return all(policy.is_domain_allowed(urlparse(url).hostname or "") for url in urls)


def annotate_parents(tree: ast.AST) -> None:
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            setattr(child, "parent", node)
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    setattr(node.value, "parent_assign_target", target.id)
