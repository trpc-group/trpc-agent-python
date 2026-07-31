# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Quote/operator-aware conservative Shell fact extraction."""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass
from typing import Optional

from ._matchers import command_matches
from ._matchers import domain_matches
from ._matchers import path_matches
from ._models import RiskLevel
from ._models import SafetyCategory
from ._models import SafetyFinding
from ._policy import SafetyPolicy
from ._redaction import redact_text
from ._rule import NestedCandidate
from ._rule import ScanContext

_OPERATORS = ("2>>", "2>", "<<<", ">>", "<<", "&&", "||", ";", "\n", "|", "&", ">", "<")
_SEPARATORS = {";", "\n", "&&", "||", "&", "|"}
_REDIRECTS = {">", ">>", "<", "2>", "2>>", "<<", "<<<"}
_NETWORK_COMMANDS = {"curl", "wget", "ssh", "scp", "rsync", "nc", "netcat"}
_DEPENDENCY_COMMANDS = {
    "pip",
    "pip3",
    "npm",
    "yarn",
    "pnpm",
    "apt",
    "apt-get",
    "yum",
    "dnf",
    "apk",
    "brew",
}
_SYSTEM_COMMANDS = {"sudo", "su", "nohup", "kill", "pkill", "killall", "systemctl", "shutdown", "reboot"}
_SENSITIVE_HINTS = ("/.ssh", ".env", "credentials", "/.aws", "gcloud", "id_rsa", "private_key")


@dataclass(frozen=True)
class ShellToken:
    """One lexeme retaining quote/expansion and source position facts."""

    value: str
    raw: str
    line_number: int
    column_number: int
    operator: bool = False
    quoted: bool = False
    dynamic: bool = False


@dataclass(frozen=True)
class ShellRedirection:
    """Ordered redirection operation."""

    operator: str
    target: str
    line_number: int
    dynamic: bool = False


@dataclass(frozen=True)
class ShellCommand:
    """A normalized command segment in a group or pipeline."""

    command: str
    argv: tuple[str, ...]
    env: tuple[tuple[str, str], ...]
    redirections: tuple[ShellRedirection, ...]
    line_number: int
    raw: str
    dynamic: bool = False


class ShellParseError(ValueError):
    """Raised for unclosed quotes or escapes in the bounded lexer."""


def _lex_shell(source: str) -> tuple[ShellToken, ...]:
    """Tokenize one source once without claiming complete Bash semantics."""
    tokens: list[ShellToken] = []
    buffer: list[str] = []
    raw: list[str] = []
    quote: Optional[str] = None
    escaped = False
    quoted = False
    dynamic = False
    line = 1
    column = 0
    start_line = 1
    start_column = 0

    def flush() -> None:
        nonlocal buffer, raw, quoted, dynamic
        if raw:
            tokens.append(
                ShellToken(
                    value="".join(buffer),
                    raw="".join(raw),
                    line_number=start_line,
                    column_number=start_column,
                    quoted=quoted,
                    dynamic=dynamic,
                ))
        buffer = []
        raw = []
        quoted = False
        dynamic = False

    index = 0
    while index < len(source):
        char = source[index]
        if escaped:
            buffer.append(char)
            raw.append(char)
            escaped = False
            index += 1
            column += 1
            continue
        if quote:
            raw.append(char)
            if char == quote:
                quote = None
            elif char == "\\" and quote == '"':
                escaped = True
            else:
                buffer.append(char)
                if quote == '"' and char in {"$", "`"}:
                    dynamic = True
            index += 1
            column += 1
            continue
        if char in {"'", '"'}:
            if not raw:
                start_line, start_column = line, column
            quote = char
            quoted = True
            raw.append(char)
            index += 1
            column += 1
            continue
        if char == "\\":
            if not raw:
                start_line, start_column = line, column
            raw.append(char)
            escaped = True
            index += 1
            column += 1
            continue
        if char == "#" and not raw:
            while index < len(source) and source[index] != "\n":
                index += 1
                column += 1
            continue
        matched = next((operator for operator in _OPERATORS if source.startswith(operator, index)), None)
        if matched:
            flush()
            tokens.append(
                ShellToken(
                    value=matched,
                    raw=matched,
                    line_number=line,
                    column_number=column,
                    operator=True,
                ))
            index += len(matched)
            if matched == "\n":
                line += 1
                column = 0
            else:
                column += len(matched)
            continue
        if char.isspace():
            flush()
            index += 1
            column += 1
            continue
        if not raw:
            start_line, start_column = line, column
        buffer.append(char)
        raw.append(char)
        if char in {"$", "`"}:
            dynamic = True
        index += 1
        column += 1

    if quote:
        raise ShellParseError("unclosed quote")
    if escaped:
        raise ShellParseError("trailing escape")
    flush()
    return tuple(tokens)


def _commands(tokens: tuple[ShellToken, ...]) -> tuple[ShellCommand, ...]:
    groups: list[list[ShellToken]] = []
    current: list[ShellToken] = []
    for token in tokens:
        if token.operator and token.value in _SEPARATORS:
            if current:
                groups.append(current)
                current = []
            continue
        current.append(token)
    if current:
        groups.append(current)

    commands: list[ShellCommand] = []
    for group in groups:
        words: list[ShellToken] = []
        redirections: list[ShellRedirection] = []
        index = 0
        while index < len(group):
            token = group[index]
            operator = token.value
            if not token.operator and token.value in {
                    "1", "2"
            } and index + 1 < len(group) and group[index + 1].value in {">", ">>"}:
                operator = token.value + group[index + 1].value
                index += 1
                token = group[index]
            if token.operator and operator in _REDIRECTS:
                target = group[index + 1] if index + 1 < len(group) and not group[index + 1].operator else None
                redirections.append(
                    ShellRedirection(
                        operator=operator,
                        target=target.value if target else "",
                        line_number=token.line_number,
                        dynamic=target.dynamic if target else True,
                    ))
                index += 2 if target else 1
                continue
            if not token.operator:
                words.append(token)
            index += 1
        if not words:
            continue
        env: list[tuple[str, str]] = []
        command_index = 0
        while command_index < len(words) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", words[command_index].value):
            key, value = words[command_index].value.split("=", 1)
            env.append((key, value))
            command_index += 1
        if command_index >= len(words):
            continue
        command_token = words[command_index]
        command = command_token.value.rsplit("/", 1)[-1]
        argv = tuple(word.value for word in words[command_index + 1:])
        commands.append(
            ShellCommand(
                command=command,
                argv=argv,
                env=tuple(env),
                redirections=tuple(redirections),
                line_number=command_token.line_number,
                raw=" ".join(word.raw for word in words),
                dynamic=command_token.dynamic or any(word.dynamic for word in words[command_index + 1:]),
            ))
    return tuple(commands)


def _finding(
    rule_id: str,
    category: SafetyCategory,
    risk: RiskLevel,
    message: str,
    command: ShellCommand,
    *,
    hard_deny: bool = False,
) -> SafetyFinding:
    return SafetyFinding(
        rule_id=rule_id,
        category=category,
        risk_level=risk,
        message=message,
        evidence=redact_text(command.raw),
        recommendation="Review or remove the unsafe shell operation.",
        line_number=command.line_number,
        hard_deny=hard_deny,
    )


def _target_for_network(command: ShellCommand) -> str:
    for item in command.argv:
        if item.startswith("-"):
            continue
        if "://" in item or "@" in item or ":" in item or re.fullmatch(r"[A-Za-z0-9.-]+", item):
            return item
    return ""


def _is_sensitive_path(value: str, policy: SafetyPolicy) -> bool:
    lowered = value.lower().replace("\\", "/")
    return path_matches(value, policy.sensitive_paths) or any(hint in lowered for hint in _SENSITIVE_HINTS)


def _extract_heredocs(source: str) -> tuple[NestedCandidate, ...]:
    candidates: list[NestedCandidate] = []
    lines = source.splitlines()
    index = 0
    while index < len(lines):
        match = re.search(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?", lines[index])
        if not match:
            index += 1
            continue
        delimiter = match.group(1)
        body: list[str] = []
        cursor = index + 1
        while cursor < len(lines) and lines[cursor].strip() != delimiter:
            body.append(lines[cursor])
            cursor += 1
        if cursor < len(lines) and body:
            candidates.append(NestedCandidate("shell", "\n".join(body), index + 2, "heredoc"))
            index = cursor + 1
        else:
            index += 1
    return tuple(candidates)


def build_shell_context(
    source: str,
    policy: SafetyPolicy,
    *,
    structured_argv: bool = False,
) -> ScanContext:
    """Lex Shell exactly once and build shared command/pipeline facts."""
    try:
        tokens = _lex_shell(source)
    except ShellParseError:
        finding = SafetyFinding(
            rule_id="SH.ANALYSIS.PARSE_FAILURE",
            category=SafetyCategory.ANALYSIS,
            risk_level=RiskLevel.MEDIUM,
            message="Shell source contains an unclosed quote or escape.",
            evidence="<parse failure>",
            recommendation="Review the source before execution.",
        )
        return ScanContext(
            language="shell",
            source=source,
            candidate_findings=(finding, ),
            analysis_complete=False,
            failure_code="shell_parse_failure",
            parse_count=1,
        )

    commands = _commands(tokens)
    findings: list[SafetyFinding] = []
    nested: list[NestedCandidate] = list(_extract_heredocs(source))

    if re.search(r":\s*\(\s*\)\s*\{[^}]*:\s*\|\s*:[^}]*\}\s*;?\s*:", source, re.DOTALL):
        command = commands[0] if commands else ShellCommand(":", (), (), (), 1, ":(){ :|:& };:")
        findings.append(
            _finding(
                "SH.RESOURCE.FORK_BOMB",
                SafetyCategory.RESOURCE,
                RiskLevel.CRITICAL,
                "Shell source contains a fork-bomb pattern.",
                command,
                hard_deny=True,
            ))
    if re.search(r"\bwhile\s+(?:true|:)\s*;?\s*do\b", source):
        command = commands[0] if commands else ShellCommand("while", (), (), (), 1, "while true")
        findings.append(
            _finding(
                "SH.RESOURCE.INFINITE_LOOP",
                SafetyCategory.RESOURCE,
                RiskLevel.HIGH,
                "Shell source contains a statically infinite loop.",
                command,
            ))

    for command in commands:
        executable = command.command
        argv = list(command.argv)
        lowered = [item.lower() for item in argv]

        if executable.startswith("$") or executable.startswith("`"):
            findings.append(
                _finding(
                    "SH.DYNAMIC.COMMAND",
                    SafetyCategory.DYNAMIC_EXECUTION,
                    RiskLevel.MEDIUM,
                    "Command name cannot be resolved statically.",
                    command,
                ))
            continue

        if structured_argv and not command_matches(executable, policy.allowed_commands):
            findings.append(
                _finding(
                    "SH.PROCESS.COMMAND_NOT_ALLOWED",
                    SafetyCategory.PROCESS,
                    RiskLevel.MEDIUM,
                    "Structured command is not on the command allowlist.",
                    command,
                ))

        destructive = False
        protected = False
        if executable == "rm" and any("r" in item and "f" in item for item in lowered if item.startswith("-")):
            destructive = True
            targets = [item for item in argv if not item.startswith("-")]
            protected = not targets or any(
                path_matches(item, policy.forbidden_paths) or "$" in item for item in targets)
        elif executable in {"rmdir", "unlink"}:
            destructive = True
            protected = not argv or any(path_matches(item, policy.forbidden_paths) or "$" in item for item in argv)
        elif executable == "find" and "-delete" in argv:
            destructive = True
            protected = any(
                path_matches(item, policy.forbidden_paths) or "$" in item for item in argv if not item.startswith("-"))
        if destructive:
            findings.append(
                _finding(
                    "SH.FILESYSTEM.DESTRUCTIVE_DELETE",
                    SafetyCategory.FILESYSTEM,
                    RiskLevel.CRITICAL if protected else RiskLevel.HIGH,
                    "Shell command performs recursive or destructive deletion.",
                    command,
                    hard_deny=protected,
                ))

        if executable in {"dd", "mkfs", "mount"} or executable in {"chmod", "chown"} and any(
                path_matches(item, policy.forbidden_paths) or "$" in item for item in argv):
            findings.append(
                _finding(
                    "SH.FILESYSTEM.SYSTEM_MUTATION",
                    SafetyCategory.FILESYSTEM,
                    RiskLevel.CRITICAL,
                    "Command can mutate protected filesystems or devices.",
                    command,
                    hard_deny=True,
                ))
        if executable == "sudo" and argv and argv[0] == "tee":
            findings.append(
                _finding(
                    "SH.FILESYSTEM.SUDO_TEE",
                    SafetyCategory.FILESYSTEM,
                    RiskLevel.CRITICAL,
                    "sudo tee can write protected files.",
                    command,
                    hard_deny=True,
                ))

        for redirection in command.redirections:
            if redirection.operator in {">", ">>", "1>", "1>>", "2>", "2>>"}:
                if redirection.dynamic:
                    findings.append(
                        _finding(
                            "SH.FILESYSTEM.DYNAMIC_REDIRECTION",
                            SafetyCategory.FILESYSTEM,
                            RiskLevel.MEDIUM,
                            "Redirection target cannot be resolved statically.",
                            command,
                        ))
                elif _is_sensitive_path(redirection.target, policy) or path_matches(redirection.target,
                                                                                    policy.forbidden_paths):
                    findings.append(
                        _finding(
                            "SH.FILESYSTEM.PROTECTED_REDIRECTION",
                            SafetyCategory.FILESYSTEM,
                            RiskLevel.CRITICAL,
                            "Redirection writes a sensitive or protected path.",
                            command,
                            hard_deny=True,
                        ))

        if executable in _NETWORK_COMMANDS:
            target = _target_for_network(command)
            if not target or "$" in target or "`" in target:
                findings.append(
                    _finding(
                        "SH.NETWORK.DYNAMIC_TARGET",
                        SafetyCategory.NETWORK,
                        RiskLevel.MEDIUM,
                        "Network target cannot be resolved statically.",
                        command,
                    ))
            elif not domain_matches(target, policy.whitelisted_domains):
                findings.append(
                    _finding(
                        "SH.NETWORK.NON_WHITELISTED",
                        SafetyCategory.NETWORK,
                        RiskLevel.HIGH,
                        "Network target is not on the domain allowlist.",
                        command,
                        hard_deny=True,
                    ))

        if executable in _DEPENDENCY_COMMANDS or executable in {"python", "python3"} and argv[:2] == ["-m", "pip"]:
            findings.append(
                _finding(
                    "SH.DEPENDENCY.INSTALL",
                    SafetyCategory.DEPENDENCY,
                    RiskLevel.HIGH,
                    "Command invokes a package or system dependency manager.",
                    command,
                ))

        if executable in _SYSTEM_COMMANDS or command_matches(executable, policy.denied_commands):
            hard = executable in {"shutdown", "reboot"} or command_matches(executable, policy.denied_commands)
            findings.append(
                _finding(
                    "SH.PROCESS.SYSTEM_COMMAND",
                    SafetyCategory.PROCESS,
                    RiskLevel.CRITICAL if hard else RiskLevel.HIGH,
                    "Command controls processes, privileges, or system services.",
                    command,
                    hard_deny=hard,
                ))

        if executable in {"env", "sudo", "nohup", "command"} and argv:
            wrapped = argv[0].rsplit("/", 1)[-1]
            wrapped_command = ShellCommand(
                command=wrapped,
                argv=tuple(argv[1:]),
                env=command.env,
                redirections=command.redirections,
                line_number=command.line_number,
                raw=command.raw,
                dynamic=command.dynamic,
            )
            if wrapped in _NETWORK_COMMANDS:
                target = _target_for_network(wrapped_command)
                if not target or "$" in target or "`" in target:
                    findings.append(
                        _finding(
                            "SH.NETWORK.DYNAMIC_TARGET",
                            SafetyCategory.NETWORK,
                            RiskLevel.MEDIUM,
                            "Wrapped network target cannot be resolved statically.",
                            command,
                        ))
                elif not domain_matches(target, policy.whitelisted_domains):
                    findings.append(
                        _finding(
                            "SH.NETWORK.NON_WHITELISTED",
                            SafetyCategory.NETWORK,
                            RiskLevel.HIGH,
                            "Wrapped network target is not on the domain allowlist.",
                            command,
                            hard_deny=True,
                        ))
            if wrapped in _DEPENDENCY_COMMANDS:
                findings.append(
                    _finding(
                        "SH.DEPENDENCY.INSTALL",
                        SafetyCategory.DEPENDENCY,
                        RiskLevel.HIGH,
                        "Wrapped command invokes a dependency manager.",
                        command,
                    ))

        if executable == "sleep" and argv:
            try:
                seconds = float(re.sub(r"[^0-9.]", "", argv[0]))
            except ValueError:
                seconds = 0
            if seconds > 60:
                findings.append(
                    _finding(
                        "SH.RESOURCE.LONG_SLEEP",
                        SafetyCategory.RESOURCE,
                        RiskLevel.MEDIUM,
                        "Sleep duration exceeds the static review threshold.",
                        command,
                    ))

        if executable in {"eval", "source", "."}:
            findings.append(
                _finding(
                    "SH.DYNAMIC.EVAL_OR_SOURCE",
                    SafetyCategory.DYNAMIC_EXECUTION,
                    RiskLevel.MEDIUM if command.dynamic else RiskLevel.HIGH,
                    "Shell command evaluates or sources additional content.",
                    command,
                ))
            if executable == "eval" and argv and "$" not in " ".join(argv):
                nested.append(NestedCandidate("shell", " ".join(argv), command.line_number, "eval"))

        if executable in {"bash", "sh", "dash", "zsh"} and len(argv) >= 2 and argv[0] == "-c":
            nested.append(NestedCandidate("shell", argv[1], command.line_number, f"{executable} -c"))
        if executable in {"python", "python3"} and len(argv) >= 2 and argv[0] == "-c":
            nested.append(NestedCandidate("python", argv[1], command.line_number, f"{executable} -c"))

        if executable == "base64" and any(item in {"-d", "--decode"} for item in argv):
            encoded = next((item for item in argv if not item.startswith("-")), "")
            if encoded:
                try:
                    decoded = base64.b64decode(encoded, validate=True)
                    if len(decoded) <= policy.nested.max_base64_decode_bytes:
                        nested.append(
                            NestedCandidate("shell", decoded.decode("utf-8"), command.line_number, "base64 decode"))
                except (binascii.Error, UnicodeDecodeError, ValueError):
                    pass

    pipeline_commands = [item.command for item in commands]
    if any(item in {"curl", "wget"} for item in pipeline_commands) and any(item in {"sh", "bash", "dash", "zsh"}
                                                                           for item in pipeline_commands):
        culprit = next(item for item in commands if item.command in {"curl", "wget"})
        findings.append(
            _finding(
                "SH.NESTED.DOWNLOAD_EXECUTE",
                SafetyCategory.NESTED_SCRIPT,
                RiskLevel.CRITICAL,
                "Pipeline downloads content and executes it as a script.",
                culprit,
                hard_deny=True,
            ))
    if "base64" in pipeline_commands and any(item in {"sh", "bash", "dash", "zsh"} for item in pipeline_commands):
        culprit = next(item for item in commands if item.command == "base64")
        findings.append(
            _finding(
                "SH.NESTED.BASE64_EXECUTE",
                SafetyCategory.NESTED_SCRIPT,
                RiskLevel.CRITICAL,
                "Pipeline decodes data and executes it as a script.",
                culprit,
                hard_deny=True,
            ))

    for index, command in enumerate(commands):
        if command.command == "cat" and any(_is_sensitive_path(item, policy) for item in command.argv):
            findings.append(
                _finding(
                    "SH.SECRET.SENSITIVE_PATH_READ",
                    SafetyCategory.SECRET,
                    RiskLevel.HIGH,
                    "Command reads a sensitive credential path.",
                    command,
                    hard_deny=True,
                ))
            if any(item.command in _NETWORK_COMMANDS for item in commands[index + 1:]):
                findings.append(
                    _finding(
                        "SH.SECRET.EXFILTRATION",
                        SafetyCategory.SECRET,
                        RiskLevel.CRITICAL,
                        "Sensitive file content flows to a network command.",
                        command,
                        hard_deny=True,
                    ))

    for match in re.finditer(r"\$\(([^()]*)\)", source, re.DOTALL):
        nested.append(
            NestedCandidate("shell", match.group(1),
                            source.count("\n", 0, match.start()) + 1, "command substitution"))

    return ScanContext(
        language="shell",
        source=source,
        candidate_findings=tuple(findings),
        nested_candidates=tuple(nested),
        details=commands,
        parse_count=1,
    )
