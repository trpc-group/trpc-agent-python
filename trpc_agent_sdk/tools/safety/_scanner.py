# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Static scanner for Python scripts and shell commands."""

from __future__ import annotations

import ast
import fnmatch
import re
import shlex
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ._rules import RULES
from ._types import RiskLevel
from ._types import SafetyDecision
from ._types import SafetyPolicy
from ._types import SafetyReport
from ._types import ScanFinding
from ._types import ScriptLanguage

_SECRET_VALUE_RE = re.compile(
    r"(?i)(api[_-]?key|token|password|secret|private[_-]?key)\s*[:=]\s*['\"]?([A-Za-z0-9_./+=:-]{8,})"
)
_PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
_URL_RE = re.compile(r"https?://[^\s'\"<>]+")
_DANGEROUS_DELETE_RE = re.compile(
    r"(?i)\b(rm\s+-[^\n;|&]*[rf]|del\s+/[fq]|rmdir\s+/s)\b")
_FORK_BOMB_RE = re.compile(r":\(\)\s*\{\s*:\|:\s*&\s*\}\s*;?\s*:")
_LONG_SLEEP_RE = re.compile(r"(?i)\b(?:sleep|timeout)\s+([0-9]{3,})\b")
_DEPENDENCY_RE = re.compile(
    r"(?i)\b(?:pip|pip3|python\s+-m\s+pip|npm|yarn|pnpm|apt|apt-get)\s+install\b"
)
_PRIVILEGE_RE = re.compile(r"(?i)(?:^|[;&|]\s*)(?:sudo|su)\b|\bchmod\s+777\b")
_SHELL_META_RE = re.compile(r"`[^`]+`|\$\([^)]+\)")
_SENSITIVE_OUTPUT_RE = re.compile(
    r"(?i)\b(print|echo|logger|logging\.\w+|write|requests\.\w+|curl)\b.*"
    r"\b(api[_-]?key|token|password|secret|private[_-]?key)\b")


class SafetyScanner:
    """Static scanner for tool-provided scripts and commands."""
    def __init__(self, policy: SafetyPolicy | None = None):
        self.policy = policy or SafetyPolicy()

    def scan(
            self,
            *,
            content: str,
            language: str | ScriptLanguage = ScriptLanguage.UNKNOWN,
            tool_name: str = "",
            cwd: str = "",
            env: dict[str, Any] | None = None,
            metadata: dict[str, Any] | None = None,
    ) -> SafetyReport:
        """Scan script or command content."""
        start = time.perf_counter()
        lang = self._normalize_language(language, content)
        findings: list[ScanFinding] = []
        redacted = False
        error_message = None

        try:
            findings.extend(self._scan_common(content))
            if lang == ScriptLanguage.PYTHON:
                findings.extend(self._scan_python(content))
            elif lang == ScriptLanguage.BASH:
                findings.extend(self._scan_shell(content))

            if cwd:
                findings.extend(self._scan_paths(cwd, "cwd"))
            if env:
                findings.extend(self._scan_env(env))
            if metadata:
                findings.extend(self._scan_metadata(metadata))
        except Exception as ex:  # pylint: disable=broad-except
            error_message = str(ex)
            findings.append(self._finding("SCANNER_ERROR", str(ex)))

        if any("<redacted" in finding.evidence for finding in findings):
            redacted = True

        elapsed_ms = (time.perf_counter() - start) * 1000
        decision = self._decide(findings)
        risk_level = self._max_risk(findings)
        blocked = decision == SafetyDecision.DENY or (
            decision == SafetyDecision.NEEDS_HUMAN_REVIEW
            and self.policy.block_on_review)

        return SafetyReport(
            decision=decision,
            risk_level=risk_level,
            findings=findings,
            elapsed_ms=elapsed_ms,
            redacted=redacted,
            blocked=blocked,
            language=lang,
            tool_name=tool_name,
            error=error_message,
        )

    def _normalize_language(self, language: str | ScriptLanguage,
                            content: str) -> ScriptLanguage:
        value = language.value if isinstance(
            language, ScriptLanguage) else (language or "")
        lowered = value.lower()
        if lowered in ("python", "py", "python3"):
            return ScriptLanguage.PYTHON
        if lowered in ("bash", "sh", "shell"):
            return ScriptLanguage.BASH
        stripped = content.lstrip()
        if stripped.startswith(
            ("import ", "from ", "def ", "print(", "async def ")):
            return ScriptLanguage.PYTHON
        return ScriptLanguage.BASH if self._looks_like_shell(
            content) else ScriptLanguage.UNKNOWN

    def _looks_like_shell(self, content: str) -> bool:
        return bool(
            re.search(
                r"(^|\s)(curl|wget|rm|cat|grep|pip|npm|apt|sudo|bash|sh)\b",
                content))

    def _scan_common(self, content: str) -> list[ScanFinding]:
        findings: list[ScanFinding] = []
        findings.extend(self._scan_paths(content, "content"))

        if _PRIVATE_KEY_RE.search(content):
            findings.append(
                self._finding("SENSITIVE_OUTPUT", "<redacted:private-key>"))

        for match in _SECRET_VALUE_RE.finditer(content):
            findings.append(
                self._finding("SENSITIVE_OUTPUT",
                              f"{match.group(1)}=<redacted:secret>"))

        if _SENSITIVE_OUTPUT_RE.search(content):
            evidence = self._redact(
                self._line_for_match(content,
                                     _SENSITIVE_OUTPUT_RE.search(content)))
            findings.append(self._finding("SENSITIVE_OUTPUT", evidence))

        for match in _URL_RE.finditer(content):
            url = match.group(0)
            host = urlparse(url).hostname or ""
            if host and not self._is_domain_allowed(host):
                findings.append(self._finding("NET_NON_WHITELIST_EGRESS", url))

        if _FORK_BOMB_RE.search(content):
            findings.append(
                self._finding("RESOURCE_FORK_BOMB",
                              _FORK_BOMB_RE.search(content).group(0)))

        if _DEPENDENCY_RE.search(content):
            findings.append(
                self._finding(
                    "DEPENDENCY_INSTALL",
                    self._line_for_match(content,
                                         _DEPENDENCY_RE.search(content))))

        return findings

    def _scan_paths(self, text: str, source: str) -> list[ScanFinding]:
        findings: list[ScanFinding] = []
        normalized = text.replace("\\", "/")
        for raw_path in self.policy.denied_paths:
            path = raw_path.replace("\\", "/")
            candidates = {
                path, str(Path(path).expanduser()).replace("\\", "/")
            }
            for candidate in candidates:
                if candidate and (candidate in normalized or fnmatch.fnmatch(
                        normalized, f"*{candidate}*")):
                    rule_id = "FILE_SECRET_READ" if self._is_secret_path(
                        candidate) else "FILE_SYSTEM_PATH_WRITE"
                    findings.append(
                        self._finding(rule_id, f"{source}: {raw_path}"))
                    break
        return findings

    def _scan_env(self, env: dict[str, Any]) -> list[ScanFinding]:
        findings: list[ScanFinding] = []
        for key, value in env.items():
            if re.search(
                    r"(?i)(api[_-]?key|token|password|secret|private[_-]?key)",
                    str(key)):
                findings.append(
                    self._finding("SENSITIVE_OUTPUT",
                                  f"env.{key}=<redacted:secret>"))
            elif isinstance(value, str) and (_SECRET_VALUE_RE.search(value)
                                             or _PRIVATE_KEY_RE.search(value)):
                findings.append(
                    self._finding("SENSITIVE_OUTPUT",
                                  f"env.{key}=<redacted:secret>"))
        return findings

    def _scan_metadata(self, metadata: dict[str, Any]) -> list[ScanFinding]:
        findings: list[ScanFinding] = []
        timeout = self._number_from(
            metadata, ("timeout", "timeout_seconds", "max_timeout_seconds"))
        if timeout is not None and timeout > self.policy.max_timeout_seconds:
            findings.append(
                self._finding("RESOURCE_LONG_SLEEP", f"timeout={timeout}"))

        output_limit = self._number_from(
            metadata, ("max_output_bytes", "max_output_size", "output_limit"))
        if output_limit is not None and output_limit > self.policy.max_output_bytes:
            findings.append(
                self._finding("RESOURCE_OUTPUT_LIMIT",
                              f"max_output_bytes={output_limit}"))
        return findings

    def _scan_python(self, content: str) -> list[ScanFinding]:
        findings: list[ScanFinding] = []
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return findings + self._scan_shell(content)

        import_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                import_names.update(
                    alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                import_names.add(node.module.split(".")[0])

            if isinstance(node, ast.Call):
                call_name = self._call_name(node.func)
                if call_name in {"os.system", "os.popen"}:
                    findings.append(
                        self._finding("PROC_OS_SYSTEM",
                                      self._node_evidence(content, node),
                                      node))
                elif call_name.startswith("subprocess."):
                    rule_id = "PROC_OS_SYSTEM" if self._has_shell_true(
                        node) else "PROC_SUBPROCESS"
                    findings.append(
                        self._finding(rule_id,
                                      self._node_evidence(content, node),
                                      node))
                elif call_name in {"shutil.rmtree"}:
                    findings.append(
                        self._finding("FILE_RECURSIVE_DELETE",
                                      self._node_evidence(content, node),
                                      node))
                elif call_name in {"open", "Path.open", "pathlib.Path.open"}:
                    arg_text = self._first_string_arg(node)
                    if arg_text:
                        findings.extend(
                            self._scan_paths(arg_text, "python.open"))
                elif call_name in {
                        "requests.get", "requests.post", "requests.put",
                        "requests.delete", "aiohttp.ClientSession"
                }:
                    url = self._first_string_arg(node)
                    if url:
                        host = urlparse(url).hostname or ""
                        if host and not self._is_domain_allowed(host):
                            findings.append(
                                self._finding("NET_NON_WHITELIST_EGRESS", url,
                                              node))
                    else:
                        findings.append(
                            self._finding("NET_CLIENT_USAGE",
                                          self._node_evidence(content, node),
                                          node))
                elif call_name in {
                        "socket.socket", "socket.create_connection"
                }:
                    findings.append(
                        self._finding("NET_CLIENT_USAGE",
                                      self._node_evidence(content, node),
                                      node))
                elif call_name in {"time.sleep", "sleep"}:
                    if (node.args and isinstance(node.args[0], ast.Constant)
                            and isinstance(node.args[0].value, (int, float))):
                        if node.args[
                                0].value >= self.policy.max_timeout_seconds:
                            findings.append(
                                self._finding(
                                    "RESOURCE_LONG_SLEEP",
                                    self._node_evidence(content, node), node))

            if isinstance(node, ast.While) and isinstance(
                    node.test, ast.Constant) and node.test.value is True:
                findings.append(
                    self._finding("RESOURCE_INFINITE_LOOP",
                                  self._node_evidence(content, node), node))

        if {"requests", "aiohttp", "socket"} & import_names and not any(
                f.rule_id.startswith("NET_") for f in findings):
            findings.append(
                self._finding("NET_CLIENT_USAGE", "network client import"))
        return findings

    def _scan_shell(self, content: str) -> list[ScanFinding]:
        findings: list[ScanFinding] = []

        if _DANGEROUS_DELETE_RE.search(content):
            findings.append(
                self._finding(
                    "FILE_RECURSIVE_DELETE",
                    self._line_for_match(
                        content, _DANGEROUS_DELETE_RE.search(content))))
        if _LONG_SLEEP_RE.search(content):
            findings.append(
                self._finding(
                    "RESOURCE_LONG_SLEEP",
                    self._line_for_match(content,
                                         _LONG_SLEEP_RE.search(content))))
        if _PRIVILEGE_RE.search(content):
            findings.append(
                self._finding(
                    "PRIVILEGE_ESCALATION",
                    self._line_for_match(content,
                                         _PRIVILEGE_RE.search(content))))
        if _SHELL_META_RE.search(content):
            findings.append(
                self._finding(
                    "SHELL_INJECTION",
                    self._line_for_match(content,
                                         _SHELL_META_RE.search(content))))
        if re.search(r"(^|[^|])\|([^|]|$)", content):
            findings.append(
                self._finding("SHELL_PIPELINE",
                              self._first_line_with(content, "|")))
        if re.search(r"(?:^|[^&])&(?!&)", content):
            findings.append(
                self._finding("SHELL_BACKGROUND",
                              self._first_line_with(content, "&")))

        command = self._first_command(content)
        if command and self.policy.allowed_commands and command not in self.policy.allowed_commands:
            findings.append(self._finding("COMMAND_NOT_ALLOWED", command))

        if re.search(r"(?i)\b(curl|wget)\b",
                     content) and not _URL_RE.search(content):
            findings.append(
                self._finding(
                    "NET_CLIENT_USAGE",
                    self._first_line_with(content, "curl")
                    or self._first_line_with(content, "wget")))

        return findings

    def _first_command(self, content: str) -> str:
        stripped = content.strip()
        if not stripped:
            return ""
        try:
            tokens = shlex.split(stripped, comments=True, posix=True)
        except ValueError:
            tokens = stripped.split()
        if not tokens:
            return ""
        if tokens[0] in {
                "python", "python3"
        } and len(tokens) >= 4 and tokens[1:3] == ["-m", "pip"]:
            return "pip"
        return Path(tokens[0]).name

    def _number_from(self, data: dict[str, Any],
                     keys: tuple[str, ...]) -> float | None:
        for key in keys:
            value = data.get(key)
            if isinstance(value, (int, float)):
                return float(value)
            if isinstance(value, str):
                try:
                    return float(value)
                except ValueError:
                    continue
        return None

    def _is_secret_path(self, path: str) -> bool:
        return bool(
            re.search(
                r"(?i)(\.ssh|\.env|credential|secret|token|password|private[_-]?key)",
                path))

    def _is_domain_allowed(self, host: str) -> bool:
        host = host.lower().rstrip(".")
        for pattern in self.policy.allowed_domains:
            candidate = pattern.lower().rstrip(".")
            if candidate.startswith("*.") and host.endswith(candidate[1:]):
                return True
            if host == candidate:
                return True
        return False

    def _call_name(self, node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            parent = self._call_name(node.value)
            return f"{parent}.{node.attr}" if parent else node.attr
        return ""

    def _has_shell_true(self, node: ast.Call) -> bool:
        return any(
            keyword.arg == "shell" and isinstance(keyword.value, ast.Constant)
            and keyword.value.value is True for keyword in node.keywords)

    def _first_string_arg(self, node: ast.Call) -> str:
        if not node.args:
            return ""
        value = node.args[0]
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return value.value
        return ""

    def _node_evidence(self, content: str, node: ast.AST) -> str:
        segment = ast.get_source_segment(content, node) or ""
        return self._redact(segment[:200])

    def _line_for_match(self, content: str,
                        match: re.Match[str] | None) -> str:
        if not match:
            return ""
        start = content.rfind("\n", 0, match.start()) + 1
        end = content.find("\n", match.end())
        if end == -1:
            end = len(content)
        return self._redact(content[start:end][:200])

    def _first_line_with(self, content: str, needle: str) -> str:
        for line in content.splitlines() or [content]:
            if needle in line:
                return self._redact(line[:200])
        return ""

    def _redact(self, text: str) -> str:
        text = _PRIVATE_KEY_RE.sub("<redacted:private-key>", text)
        return _SECRET_VALUE_RE.sub(
            lambda m: f"{m.group(1)}=<redacted:secret>", text)

    def _finding(self,
                 rule_id: str,
                 evidence: str,
                 node: ast.AST | None = None) -> ScanFinding:
        rule = RULES[rule_id]
        override = self.policy.rules.get(rule_id)
        decision = override.decision if override and override.decision else rule.decision
        risk_level = override.risk_level if override and override.risk_level else rule.risk_level
        return ScanFinding(
            rule_id=rule_id,
            risk_type=rule.risk_type,
            risk_level=risk_level,
            decision=decision,
            message=rule.message,
            evidence=self._redact(evidence or rule.message),
            recommendation=rule.recommendation,
            line=getattr(node, "lineno", None),
            column=getattr(node, "col_offset", None),
        )

    def _decide(self, findings: list[ScanFinding]) -> SafetyDecision:
        enabled = [
            finding for finding in findings
            if self._rule_enabled(finding.rule_id)
        ]
        if not enabled:
            return SafetyDecision.ALLOW
        if any(f.decision == SafetyDecision.DENY for f in enabled):
            return SafetyDecision.DENY
        if self.policy.fail_closed and any(
                f.risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL}
                for f in enabled):
            return SafetyDecision.DENY
        if any(f.decision == SafetyDecision.NEEDS_HUMAN_REVIEW
               for f in enabled):
            return SafetyDecision.NEEDS_HUMAN_REVIEW
        return SafetyDecision.ALLOW

    def _max_risk(self, findings: list[ScanFinding]) -> RiskLevel:
        order = {
            RiskLevel.LOW: 0,
            RiskLevel.MEDIUM: 1,
            RiskLevel.HIGH: 2,
            RiskLevel.CRITICAL: 3,
        }
        enabled = [
            finding for finding in findings
            if self._rule_enabled(finding.rule_id)
        ]
        if not enabled:
            return RiskLevel.LOW
        return max((finding.risk_level for finding in enabled),
                   key=lambda level: order[level])

    def _rule_enabled(self, rule_id: str) -> bool:
        override = self.policy.rules.get(rule_id)
        return True if override is None else override.enabled
