# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Bash command safety rules."""

from __future__ import annotations

import os
import math
import re
import shlex

from ._common_rules import scan_secret_sink
from ._common_rules import host_allowed
from ._common_rules import path_is_system_location
from ._common_rules import scan_urls
from ._models import RiskCategory
from ._models import RiskLevel
from ._models import SafetyDecision
from ._models import SafetyFinding
from ._models import ScriptLanguage
from ._models import ScriptPayload
from ._models import ScriptScanRequest
from ._models import ToolSafetyPolicy
from ._common_rules import make_finding
from ._common_rules import RuleSpec
from ._sanitizer import SafetySanitizer

_COMMAND_SPLIT_RE = re.compile(r"\|\||&&|[|;&\n]")
_INSTALL_RE = re.compile(r"(?i)(?:^|\s)(?:pip3?|python\d*\s+-m\s+pip|npm|yarn|apt(?:-get)?|yum|dnf)"
                         r"\s+(?:install|add)\b")
_FORK_BOMB_RE = re.compile(r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:")
_INFINITE_LOOP_RE = re.compile(r"(?i)\bwhile\s+(?:true|:)\s*;?\s*do\b")
_SHELL_META_RE = re.compile(r"\$\(|`[^`]+`|(?:^|[^|])\|(?:[^|]|$)|&&|;|(?<![>&])&(?![>&])")
_SINK_RE = re.compile(r"(?i)(?:^|[;&|]\s*)(?:echo|printf|curl|wget)\b|(?:>|>>)")
_INTERPRETERS = frozenset({"sh", "bash", "zsh", "python", "python3"})
_SHELLS = frozenset({"sh", "bash", "zsh"})
_COMMAND_WRAPPERS = frozenset({"command", "exec", "nohup"})
_SHELL_KEYWORDS = frozenset({
    "case",
    "do",
    "done",
    "elif",
    "else",
    "esac",
    "fi",
    "for",
    "if",
    "in",
    "select",
    "then",
    "until",
    "while",
})
_COMMAND_PREFIX_KEYWORDS = frozenset({"do", "else", "then"})
_PROCESS_RUNNERS = frozenset({"chrt", "ionice", "nice", "setsid", "stdbuf", "taskset", "timeout", "xargs"})
_NETWORK_COMMANDS = frozenset({"curl", "ssh", "wget"})
_CURL_REMAP_OPTIONS = ("--config", "--connect-to", "--proxy", "--resolve", "-K", "-x")
_WGET_REMAP_OPTIONS = ("--config", "--execute", "-e")
_SSH_REMAP_OPTIONS = ("-D", "-F", "-J", "-L", "-R", "-W")
_SSH_REMAP_CONFIG_KEYS = frozenset({"localcommand", "proxycommand", "proxyjump"})
_SSH_OPTIONS_WITH_VALUES = frozenset({
    "-B", "-b", "-c", "-E", "-e", "-F", "-I", "-i", "-J", "-L", "-l", "-m", "-O", "-o", "-p", "-Q", "-R", "-S", "-W",
    "-w"
})
_REDIRECT_PATH_RE = re.compile(r"(?<![<>])\d*>>?\s*(?!&)([^\s;&|]+)")
_SAFE_REDIRECT_TARGETS = frozenset({"/dev/null", "/dev/stdout", "/dev/stderr"})

FILE_DELETE = RuleSpec(
    RiskCategory.FILE,
    RiskLevel.CRITICAL,
    SafetyDecision.DENY,
    "Remove recursive deletion or constrain it to an approved workspace.",
)
PROCESS_REVIEW = RuleSpec(
    RiskCategory.PROCESS,
    RiskLevel.MEDIUM,
    SafetyDecision.NEEDS_HUMAN_REVIEW,
    "Avoid shell composition or obtain explicit approval.",
)
PROCESS_DENY = RuleSpec(
    RiskCategory.PROCESS,
    RiskLevel.CRITICAL,
    SafetyDecision.DENY,
    "Remove privilege escalation or destructive shell behavior.",
)
DEPENDENCY_REVIEW = RuleSpec(
    RiskCategory.DEPENDENCY,
    RiskLevel.HIGH,
    SafetyDecision.NEEDS_HUMAN_REVIEW,
    "Pin and pre-approve dependencies outside the execution request.",
)
RESOURCE_DENY = RuleSpec(
    RiskCategory.RESOURCE,
    RiskLevel.CRITICAL,
    SafetyDecision.DENY,
    "Remove unbounded process or loop creation.",
)
RESOURCE_REVIEW = RuleSpec(
    RiskCategory.RESOURCE,
    RiskLevel.MEDIUM,
    SafetyDecision.NEEDS_HUMAN_REVIEW,
    "Reduce execution duration or obtain approval.",
)
COMMAND_REVIEW = RuleSpec(
    RiskCategory.POLICY,
    RiskLevel.MEDIUM,
    SafetyDecision.NEEDS_HUMAN_REVIEW,
    "Add the command to allowed_commands or use an approved command.",
)
NETWORK_REVIEW = RuleSpec(
    RiskCategory.NETWORK,
    RiskLevel.MEDIUM,
    SafetyDecision.NEEDS_HUMAN_REVIEW,
    "Use a literal allowlisted destination.",
)
NETWORK_DENY = RuleSpec(
    RiskCategory.NETWORK,
    RiskLevel.HIGH,
    SafetyDecision.DENY,
    "Remove destination remapping or use a directly allowlisted endpoint.",
)
FILE_WRITE_DENY = RuleSpec(
    RiskCategory.FILE,
    RiskLevel.CRITICAL,
    SafetyDecision.DENY,
    "Write only inside an approved workspace, never to system paths.",
)
FILE_WRITE_REVIEW = RuleSpec(
    RiskCategory.FILE,
    RiskLevel.MEDIUM,
    SafetyDecision.NEEDS_HUMAN_REVIEW,
    "Resolve and approve the dynamic write target.",
)


def _tokens(segment: str) -> list[str]:
    try:
        return shlex.split(segment, posix=True)
    except ValueError:
        return []


def _command_name(segment: str) -> str:
    tokens = _command_tokens(_tokens(segment))
    if not tokens:
        return ""
    return os.path.basename(tokens[0]).lower()


def _policy_command_name(segment: str) -> str:
    tokens = _command_tokens(_tokens(segment))
    while tokens and os.path.basename(tokens[0]).lower() in _COMMAND_PREFIX_KEYWORDS:
        tokens = _command_tokens(tokens[1:])
    return os.path.basename(tokens[0]).lower() if tokens else ""


def _unwrap_tokens(tokens: list[str]) -> list[str]:
    result = list(tokens)
    while result:
        name = os.path.basename(result[0]).lower()
        if name in _COMMAND_WRAPPERS:
            result = result[1:]
            continue
        if name != "env":
            break
        result = result[1:]
        while result and (result[0].startswith("-") or "=" in result[0]):
            result = result[1:]
    return result


def _command_tokens(tokens: list[str]) -> list[str]:
    result = _unwrap_tokens(tokens)
    while result and "=" in result[0] and not result[0].startswith(("/", ".")):
        result = _unwrap_tokens(result[1:])
    return result


def _recursive_rm(text: str) -> str | None:
    for segment in _COMMAND_SPLIT_RE.split(text):
        tokens = _command_tokens(_tokens(segment.strip()))
        if not tokens or os.path.basename(tokens[0]).lower() != "rm":
            continue
        options = [item for item in tokens[1:] if item.startswith("-")]
        if any(item == "--recursive" or (not item.startswith("--") and "r" in item[1:].lower()) for item in options):
            return segment.strip()
    return None


def _is_safe_redirect_target(target: str) -> bool:
    return target.replace("\\", "/").lower() in _SAFE_REDIRECT_TARGETS


def _dynamic_network_command(text: str) -> str | None:
    for segment in _COMMAND_SPLIT_RE.split(text):
        if _command_name(segment.strip()) not in {"curl", "wget"}:
            continue
        if not re.search(r"https?://", segment, re.IGNORECASE):
            return segment.strip()
    return None


def _unwrapped_segments(text: str) -> list[str]:
    return [" ".join(_unwrap_tokens(_tokens(item.strip()))) for item in _COMMAND_SPLIT_RE.split(text)]


def _network_command_text(text: str) -> str:
    return "\n".join(segment for segment in _unwrapped_segments(text) if _command_name(segment) in {"curl", "wget"})


def _option_matches(token: str, option: str) -> bool:
    if token == option or token.startswith(option + "="):
        return True
    if len(option) == 2 and option.startswith("-") and token.startswith("-") and not token.startswith("--"):
        return option[1] in token[1:]
    return False


def _has_option(tokens: list[str], options: tuple[str, ...]) -> bool:
    return any(_option_matches(token, option) for token in tokens[1:] for option in options)


def _ssh_target(tokens: list[str]) -> str | None:
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            return tokens[index + 1] if index + 1 < len(tokens) else None
        if not token.startswith("-"):
            return token
        index += 2 if token in _SSH_OPTIONS_WITH_VALUES else 1
    return None


def _ssh_remaps(tokens: list[str]) -> bool:
    if _has_option(tokens, _SSH_REMAP_OPTIONS):
        return True
    for index, token in enumerate(tokens[1:], start=1):
        option_value = tokens[index + 1] if token == "-o" and index + 1 < len(tokens) else token[2:]
        if token == "-o" or token.startswith("-o"):
            config_key = re.split(r"[=\s]", option_value.strip(), maxsplit=1)[0].lower()
            if config_key in _SSH_REMAP_CONFIG_KEYS:
                return True
    return False


def _network_option_findings(text: str, policy: ToolSafetyPolicy,
                             sanitizer: SafetySanitizer) -> tuple[list[SafetyFinding], bool]:
    findings = []
    redacted = False
    for segment in _COMMAND_SPLIT_RE.split(text):
        tokens = _unwrap_tokens(_tokens(segment.strip()))
        if not tokens:
            continue
        name = os.path.basename(tokens[0]).lower()
        remapped = name == "curl" and _has_option(tokens, _CURL_REMAP_OPTIONS)
        remapped = remapped or (name == "wget" and _has_option(tokens, _WGET_REMAP_OPTIONS))
        remapped = remapped or (name == "ssh" and _ssh_remaps(tokens))
        if remapped:
            finding, changed = make_finding("NET003", segment, NETWORK_DENY, sanitizer)
            findings.append(finding)
            redacted = redacted or changed
        if name == "ssh":
            ssh_findings, changed = _ssh_target_findings(tokens, policy, sanitizer)
            findings.extend(ssh_findings)
            redacted = redacted or changed
    return findings, redacted


def _ssh_target_findings(tokens: list[str], policy: ToolSafetyPolicy,
                         sanitizer: SafetySanitizer) -> tuple[list[SafetyFinding], bool]:
    target = _ssh_target(tokens)
    if not target or target.startswith(("$", "`")):
        finding, changed = make_finding("NET002", "dynamic SSH destination", NETWORK_REVIEW, sanitizer)
        return [finding], changed
    host = target.rsplit("@", 1)[-1].strip("[]")
    if host_allowed(host, policy):
        return [], False
    finding, changed = make_finding("NET001", target, NETWORK_DENY, sanitizer)
    return [finding], changed


def _runner_command(text: str) -> str | None:
    for segment in _COMMAND_SPLIT_RE.split(text):
        if _command_name(segment.strip()) in _PROCESS_RUNNERS:
            return segment.strip()
    return None


def _sleep_values(text: str) -> list[tuple[str, list[str]]]:
    values = []
    for segment in _COMMAND_SPLIT_RE.split(text):
        tokens = _unwrap_tokens(_tokens(segment.strip()))
        if tokens and os.path.basename(tokens[0]).lower() == "sleep":
            values.append((segment.strip(), tokens[1:]))
    return values


def stdin_language(text: str) -> ScriptLanguage | None:
    """Return interpreter language when stdin is executable source."""
    tokens = _unwrap_tokens(_tokens(text.strip()))
    if not tokens:
        return None
    name = os.path.basename(tokens[0]).lower()
    if name not in _INTERPRETERS or "-c" in tokens:
        return None
    args = tokens[1:]
    if args and "-" not in args:
        return None
    return ScriptLanguage.BASH if name in _SHELLS else ScriptLanguage.PYTHON


def _sleep_seconds(value: str) -> float:
    if not value:
        return float("inf")
    normalized = value.lower()
    if normalized == "infinity":
        return float("inf")
    factors = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    suffix = normalized[-1]
    try:
        if suffix in factors:
            seconds = float(normalized[:-1]) * factors[suffix]
        else:
            seconds = float(normalized)
        return seconds if math.isfinite(seconds) and seconds >= 0 else float("inf")
    except (ValueError, OverflowError):
        return float("inf")


def _sleep_exceeds(values: list[str], limit: float) -> bool:
    if not values:
        return True
    total = 0.0
    for value in values:
        seconds = _sleep_seconds(value)
        if not math.isfinite(seconds):
            return True
        total += seconds
    return total > limit


def _check_commands(text: str, policy: ToolSafetyPolicy,
                    sanitizer: SafetySanitizer) -> tuple[list[SafetyFinding], bool]:
    findings = []
    allowed = {os.path.basename(item).lower() for item in policy.allowed_commands}
    redacted = False
    for segment in _COMMAND_SPLIT_RE.split(text):
        name = _policy_command_name(segment.strip())
        if name and name not in allowed and name not in _SHELL_KEYWORDS:
            finding, changed = make_finding("POLICY002", name, COMMAND_REVIEW, sanitizer)
            findings.append(finding)
            redacted = redacted or changed
    return findings, redacted


def _static_rules(text: str, policy: ToolSafetyPolicy, sanitizer: SafetySanitizer,
                  request: ScriptScanRequest) -> tuple[list[SafetyFinding], bool]:
    findings = []
    redacted = False
    recursive_rm = _recursive_rm(text)
    runner = _runner_command(text)
    matches = [
        (recursive_rm, "FILE001", FILE_DELETE),
        (runner, "PROC001", PROCESS_REVIEW),
        (re.search(r"(?i)(?:^|\s)sudo(?:\s|$)", text), "PROC002", PROCESS_DENY),
        (_INSTALL_RE.search(text), "DEP001", DEPENDENCY_REVIEW),
        (_FORK_BOMB_RE.search(text), "RES001", RESOURCE_DENY),
        (_INFINITE_LOOP_RE.search(text), "RES001", RESOURCE_DENY),
        (_SHELL_META_RE.search(text), "PROC001", PROCESS_REVIEW),
    ]
    for match, rule_id, spec in matches:
        if match:
            evidence = match.group(0) if hasattr(match, "group") else match
            finding, changed = make_finding(rule_id, evidence, spec, sanitizer)
            findings.append(finding)
            redacted = redacted or changed
    for evidence, values in _sleep_values(text):
        if _sleep_exceeds(values, policy.long_sleep_seconds):
            finding, changed = make_finding("RES002", evidence, RESOURCE_REVIEW, sanitizer)
            findings.append(finding)
            redacted = redacted or changed
    if any(_tokens(segment.strip())[:1] == ["nohup"] for segment in _COMMAND_SPLIT_RE.split(text)):
        finding, changed = make_finding("PROC003", "nohup background execution", PROCESS_REVIEW, sanitizer)
        findings.append(finding)
        redacted = redacted or changed
    for match in _REDIRECT_PATH_RE.finditer(text):
        target = match.group(1).strip("\"'")
        if target.startswith(("$", "`")):
            finding, changed = make_finding("FILE003", match.group(0), FILE_WRITE_REVIEW, sanitizer)
            findings.append(finding)
            redacted = redacted or changed
        elif _is_safe_redirect_target(target):
            continue
        elif path_is_system_location(target, request.cwd):
            finding, changed = make_finding("FILE001", match.group(0), FILE_WRITE_DENY, sanitizer)
            findings.append(finding)
            redacted = redacted or changed
    dynamic_network = _dynamic_network_command(text)
    if dynamic_network:
        finding, changed = make_finding("NET002", dynamic_network, NETWORK_REVIEW, sanitizer)
        findings.append(finding)
        redacted = redacted or changed
    network_findings, changed = scan_urls(_network_command_text(text), policy, sanitizer)
    findings.extend(network_findings)
    redacted = redacted or changed
    option_findings, changed = _network_option_findings(text, policy, sanitizer)
    findings.extend(option_findings)
    redacted = redacted or changed
    secret_findings, changed = scan_secret_sink(text, sanitizer, bool(_SINK_RE.search(text)))
    return findings + secret_findings, redacted or changed


def nested_payloads(text: str) -> list[ScriptPayload]:
    """Extract literal ``shell/python -c`` and stdin payloads."""
    nested = []
    candidates = [text]
    candidates.extend(_COMMAND_SPLIT_RE.split(text))
    seen = set()
    for segment in candidates:
        tokens = _unwrap_tokens(_tokens(segment.strip()))
        if not tokens:
            continue
        name = os.path.basename(tokens[0]).lower()
        if name not in _INTERPRETERS or "-c" not in tokens:
            continue
        index = tokens.index("-c")
        if index + 1 >= len(tokens):
            continue
        language = ScriptLanguage.BASH if name in _SHELLS else ScriptLanguage.PYTHON
        key = (language, tokens[index + 1])
        if key in seen:
            continue
        seen.add(key)
        nested.append(ScriptPayload(
            language=language,
            content=tokens[index + 1],
            source=f"nested {name} -c",
        ))
    return nested


def scan_bash(text: str, policy: ToolSafetyPolicy, sanitizer: SafetySanitizer,
              request: ScriptScanRequest) -> tuple[list[SafetyFinding], bool]:
    """Scan Bash-specific constructs."""
    static_findings, static_redacted = _static_rules(text, policy, sanitizer, request)
    command_findings, command_redacted = _check_commands(text, policy, sanitizer)
    return static_findings + command_findings, static_redacted or command_redacted
