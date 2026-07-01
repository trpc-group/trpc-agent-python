# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Pattern-based Bash safety scanner rules."""

from __future__ import annotations

import re
import shlex
from math import inf
from typing import List
from typing import Optional

from .checker import Rule
from .checker import SafetyChecker
from .models import Finding
from .models import SafetyResult
from .models import SafetySeverity
from .models import ToolExecutionRequest
from .policy import SafetyPolicy

_BASH_LANGUAGES = {"bash", "sh", "shell", "zsh"}
_LONG_SLEEP_SECONDS = 3600


class BashLine:
    """One source line plus shell tokens."""

    def __init__(self, number: int, text: str):
        self.number = number
        self.text = text
        self.tokens = _split_shell_tokens(text)


class BashScanContext:
    """Tokenized Bash source."""

    def __init__(self, source: str):
        self.source = source
        self.lines = [BashLine(number, text) for number, text in enumerate(source.splitlines(), start=1)]


class BashRule(Rule):
    """Base class for pattern-backed Bash rules."""

    severity = SafetySeverity.HIGH

    async def check(self, request: ToolExecutionRequest, policy: SafetyPolicy) -> List[Finding]:
        source = _extract_bash_source(request)
        if not source:
            return []
        return self.check_script(BashScanContext(source), policy)

    def check_script(self, context: BashScanContext, policy: SafetyPolicy) -> List[Finding]:
        """Check Bash source and return findings."""
        raise NotImplementedError

    def _finding(self, message: str, line: BashLine, policy: SafetyPolicy, target: str = "") -> Finding:
        column = line.text.find(target) if target else -1
        return Finding(
            rule_id=self.rule_id,
            message=message,
            severity=policy.rule_severity(self.rule_id, self.severity),
            target=target,
            metadata={
                "line": line.number,
                "column": max(column, 0),
            },
        )


class RmRfRule(BashRule):
    """Detect rm -rf style deletion."""

    @property
    def rule_id(self) -> str:
        return "bash.rm_rf"

    def check_script(self, context: BashScanContext, policy: SafetyPolicy) -> List[Finding]:
        findings: list[Finding] = []
        for line in context.lines:
            for index, token in enumerate(line.tokens):
                command = _command_name(token)
                if command != "rm" or policy.is_command_allowed(self.rule_id, command):
                    continue
                flags = _collect_short_flags(line.tokens[index + 1:])
                if "r" in flags and "f" in flags:
                    findings.append(self._finding("Bash code calls rm -rf.", line, policy, "rm"))
                    break
        return findings


class CurlRule(BashRule):
    """Detect curl calls."""

    @property
    def rule_id(self) -> str:
        return "bash.curl"

    def check_script(self, context: BashScanContext, policy: SafetyPolicy) -> List[Finding]:
        return _command_findings(self, context, policy, {"curl"}, "Bash code calls curl.")


class WgetRule(BashRule):
    """Detect wget calls."""

    @property
    def rule_id(self) -> str:
        return "bash.wget"

    def check_script(self, context: BashScanContext, policy: SafetyPolicy) -> List[Finding]:
        return _command_findings(self, context, policy, {"wget"}, "Bash code calls wget.")


class SudoRule(BashRule):
    """Detect sudo calls."""

    @property
    def rule_id(self) -> str:
        return "bash.sudo"

    def check_script(self, context: BashScanContext, policy: SafetyPolicy) -> List[Finding]:
        return _command_findings(self, context, policy, {"sudo"}, "Bash code calls sudo.")


class AptInstallRule(BashRule):
    """Detect apt install calls."""

    @property
    def rule_id(self) -> str:
        return "bash.apt_install"

    def check_script(self, context: BashScanContext, policy: SafetyPolicy) -> List[Finding]:
        findings: list[Finding] = []
        for line in context.lines:
            command = _install_command(line.tokens, {"apt", "apt-get"})
            if command and not policy.is_command_allowed(self.rule_id, command):
                findings.append(self._finding("Bash code calls apt install.", line, policy, command))
        return findings


class PipInstallRule(BashRule):
    """Detect pip install calls."""

    @property
    def rule_id(self) -> str:
        return "bash.pip_install"

    def check_script(self, context: BashScanContext, policy: SafetyPolicy) -> List[Finding]:
        findings: list[Finding] = []
        for line in context.lines:
            command = _install_command(line.tokens, {"pip", "pip3"}) or _python_module_pip_command(line.tokens)
            if command and not policy.is_command_allowed(self.rule_id, command):
                findings.append(self._finding("Bash code calls pip install.", line, policy, command))
        return findings


class NpmInstallRule(BashRule):
    """Detect npm install calls."""

    @property
    def rule_id(self) -> str:
        return "bash.npm_install"

    def check_script(self, context: BashScanContext, policy: SafetyPolicy) -> List[Finding]:
        findings: list[Finding] = []
        for line in context.lines:
            command = _install_command(line.tokens, {"npm"})
            if command and not policy.is_command_allowed(self.rule_id, command):
                findings.append(self._finding("Bash code calls npm install.", line, policy, command))
        return findings


class BackgroundExecutionRule(BashRule):
    """Detect background execution with &."""

    @property
    def rule_id(self) -> str:
        return "bash.background_execution"

    def check_script(self, context: BashScanContext, policy: SafetyPolicy) -> List[Finding]:
        findings: list[Finding] = []
        for line in context.lines:
            if _has_background_operator(line.text):
                findings.append(self._finding("Bash code uses background execution.", line, policy, "&"))
        return findings


class ShellPipeRule(BashRule):
    """Detect shell pipes."""

    @property
    def rule_id(self) -> str:
        return "bash.shell_pipe"

    def check_script(self, context: BashScanContext, policy: SafetyPolicy) -> List[Finding]:
        findings: list[Finding] = []
        for line in context.lines:
            if _has_pipe_operator(line.text):
                findings.append(self._finding("Bash code uses a shell pipe.", line, policy, "|"))
        return findings


class ForkBombRule(BashRule):
    """Detect common fork bomb patterns."""

    @property
    def rule_id(self) -> str:
        return "bash.fork_bomb"

    def check_script(self, context: BashScanContext, policy: SafetyPolicy) -> List[Finding]:
        findings: list[Finding] = []
        for line in context.lines:
            if _is_fork_bomb(line.text):
                findings.append(self._finding("Bash code contains a fork bomb pattern.", line, policy, line.text.strip()))
        return findings


class LongSleepRule(BashRule):
    """Detect long sleep calls."""

    @property
    def rule_id(self) -> str:
        return "bash.long_sleep"

    def check_script(self, context: BashScanContext, policy: SafetyPolicy) -> List[Finding]:
        findings: list[Finding] = []
        max_timeout = policy.rule_max_timeout(self.rule_id, _LONG_SLEEP_SECONDS)
        for line in context.lines:
            for index, token in enumerate(line.tokens):
                if _command_name(token) != "sleep" or index + 1 >= len(line.tokens):
                    continue
                seconds = _sleep_seconds(line.tokens[index + 1])
                if seconds >= max_timeout:
                    findings.append(self._finding("Bash code calls sleep for a long duration.", line, policy, "sleep"))
                    break
        return findings


class BashScanner:
    """Convenience scanner using the default Bash safety rules."""

    def __init__(self, rules: Optional[list[Rule]] = None, policy: Optional[SafetyPolicy] = None):
        self._checker = SafetyChecker(rules or create_bash_rules(), policy)

    async def scan(self, source: str, policy: Optional[SafetyPolicy] = None) -> SafetyResult:
        """Scan Bash source and return a safety result."""
        request = ToolExecutionRequest(language="bash", script=source)
        return await self._checker.check(request, policy)


def create_bash_rules() -> list[Rule]:
    """Create the built-in Bash pattern safety rules."""
    return [
        RmRfRule(),
        CurlRule(),
        WgetRule(),
        SudoRule(),
        AptInstallRule(),
        PipInstallRule(),
        NpmInstallRule(),
        BackgroundExecutionRule(),
        ShellPipeRule(),
        ForkBombRule(),
        LongSleepRule(),
    ]


def _extract_bash_source(request: ToolExecutionRequest) -> str:
    language = (request.language or request.metadata.get("language") or "").strip().lower()
    if language and language not in _BASH_LANGUAGES:
        return ""
    for value in (
            request.script,
            request.args.get("code"),
            request.args.get("script"),
            request.metadata.get("code"),
            request.metadata.get("script"),
            request.metadata.get("bash_code"),
    ):
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _split_shell_tokens(line: str) -> list[str]:
    lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = "#"
    try:
        return list(lexer)
    except ValueError:
        return line.split()


def _command_name(token: str) -> str:
    return token.rsplit("/", 1)[-1]


def _command_findings(
    rule: BashRule,
    context: BashScanContext,
    policy: SafetyPolicy,
    names: set[str],
    message: str,
) -> list[Finding]:
    findings: list[Finding] = []
    for line in context.lines:
        for token in line.tokens:
            command = _command_name(token)
            if command in names and not policy.is_command_allowed(rule.rule_id, command):
                findings.append(rule._finding(message, line, policy, command))
                break
    return findings


def _collect_short_flags(tokens: list[str]) -> set[str]:
    flags: set[str] = set()
    for token in tokens:
        if token in {"|", "&", "&&", "||", ";"}:
            break
        if not token.startswith("-") or token == "-":
            continue
        flags.update(token.lstrip("-"))
    return flags


def _install_command(tokens: list[str], command_names: set[str]) -> str:
    for index, token in enumerate(tokens):
        command = _command_name(token)
        if command not in command_names:
            continue
        for candidate in tokens[index + 1:]:
            if candidate in {"|", "&", "&&", "||", ";"}:
                break
            if candidate.startswith("-"):
                continue
            if candidate == "install":
                return command
            break
    return ""


def _python_module_pip_command(tokens: list[str]) -> str:
    for index, token in enumerate(tokens):
        command = _command_name(token)
        if command not in {"python", "python3"}:
            continue
        window = tokens[index + 1:index + 4]
        if len(window) >= 3 and window[0] == "-m" and window[1] == "pip" and window[2] == "install":
            return "pip"
    return ""


def _has_background_operator(line: str) -> bool:
    for index in _operator_positions(line, "&"):
        before = line[index - 1] if index > 0 else ""
        after = line[index + 1] if index + 1 < len(line) else ""
        if before not in {"&", ">", "<"} and after != "&":
            return True
    return False


def _has_pipe_operator(line: str) -> bool:
    for index in _operator_positions(line, "|"):
        before = line[index - 1] if index > 0 else ""
        after = line[index + 1] if index + 1 < len(line) else ""
        if before != "|" and after != "|":
            return True
    return False


def _operator_positions(line: str, operator: str) -> list[int]:
    positions: list[int] = []
    quote = ""
    escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = ""
            continue
        if char in {"'", '"'}:
            quote = char
            continue
        if char == "#":
            break
        if char == operator:
            positions.append(index)
    return positions


def _is_fork_bomb(line: str) -> bool:
    compact = "".join(line.split())
    if ":(){:|:&};:" in compact:
        return True
    return bool(re.search(r"([A-Za-z_:][A-Za-z0-9_:]*)\(\)\{\1\|\1&};\1", compact))


def _sleep_seconds(value: str) -> float:
    if value.lower() in {"inf", "infinity"}:
        return inf
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([smhd]?)", value.lower())
    if not match:
        return 0
    amount = float(match.group(1))
    unit = match.group(2) or "s"
    scale = {
        "s": 1,
        "m": 60,
        "h": 3600,
        "d": 86400,
    }[unit]
    return amount * scale
