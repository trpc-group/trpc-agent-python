# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Bash command safety scanner.

Bash is far harder to parse correctly than Python (shell quoting,
variable expansion, here-docs, command substitution …).  This scanner
uses a pragmatic combination of **line-level regex** and **command
tokenisation** via :mod:`shlex` to catch the high-signal patterns
required by the issue.

Known limitation: obfuscated commands (base64 decode | sh, variable
indirection, aliases) can bypass these checks.  The README documents
this and explains why the guard *complements* rather than *replaces*
sandbox isolation.
"""

from __future__ import annotations

import re
import shlex
from urllib.parse import urlparse

from ._models import Decision
from ._models import Finding
from ._models import RiskCategory
from ._models import RiskLevel
from ._models import ScriptType
from ._rules import Rule
from ._rules import ScanContext
from ._rules import global_rule_registry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_urls(text: str) -> list[str]:
    """Extract URL strings from *text*."""
    return re.findall(r'https?://[^\s\'"]+|ftp://[^\s\'"]+', text, re.IGNORECASE)


def _extract_domain(url: str) -> str:
    if not url:
        return ""
    if "://" not in url:
        url = "http://" + url
    try:
        return urlparse(url).hostname or ""
    except Exception:  # pylint: disable=broad-except
        return ""


def _redact(text: str) -> str:
    """Mask potential secrets in evidence text."""
    if len(text) <= 12:
        return text[:2] + "***"
    return text[:6] + "..." + text[-4:]


# ===========================================================================
# Bash rules
# ===========================================================================


class BashDangerousFileOpsRule(Rule):
    """Detect dangerous file operations in bash commands."""

    rule_id = "BASH-DANGEROUS-FILE-OPS"
    description = "Dangerous file operation: recursive delete, system directory, or credential file access"
    category = RiskCategory.DANGEROUS_FILE_OPS
    default_risk_level = RiskLevel.CRITICAL
    default_decision = Decision.DENY
    applies_to = (ScriptType.BASH, )

    # Match rm -rf with any target; specific system dirs are checked
    # dynamically against ctx.policy.protected_system_dirs in check().
    #
    # Three variants to cover common flag styles:
    #   1. rm -rf /         (joined short flags)
    #   2. rm -r -f /       (split short flags)
    #   3. rm --recursive --force /   (long flags)
    _RM_RF = re.compile(
        r"\brm\s+(?:"
        r"-[a-zA-Z]*r[a-zA-Z]*f"  # -rf, -rvf  (joined, r before f)
        r"|-[a-zA-Z]*f[a-zA-Z]*r"  # -fr, -fvr  (joined, f before r)
        r"|-[a-zA-Z]*r\s+-[a-zA-Z]*f"  # -r -f      (split short)
        r"|-[a-zA-Z]*f\s+-[a-zA-Z]*r"  # -f -r      (split short reversed)
        r"|--recursive\s+--force"  # long flags
        r"|--force\s+--recursive"  # long flags reversed
        r")\s+(?P<target>\S+)",
        re.IGNORECASE,
    )

    # Targets that are always dangerous regardless of policy config
    # (root /, home ~, $HOME — these are structural, not configurable).
    _RM_RF_ALWAYS_DANGEROUS = re.compile(
        r"^(?:/+\s*$|/+\*|~/?\s*$|~/?\*|\$HOME/?\*|\$HOME/?\s*$)",
        re.IGNORECASE,
    )

    def check(self, ctx: ScanContext) -> list[Finding]:
        findings: list[Finding] = []
        lines = ctx.cached_lines if ctx.cached_lines is not None else ctx.script.splitlines()

        # Compile credential-read command pattern once per scan.
        cred_cmds = ctx.policy.credential_read_commands
        if cred_cmds:
            read_cmds_pattern = re.compile(r"\b(" + "|".join(re.escape(c) for c in cred_cmds) + r")\b")
        else:
            read_cmds_pattern = None  # empty list → disable credential-read check

        for idx, line in enumerate(lines, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            # rm -rf on system dirs / home — target checked against policy
            match = self._RM_RF.search(stripped)
            if match:
                target = match.group("target")
                # Strip surrounding quotes so rm -rf "/" and rm -rf '/'
                # resolve to bare / and match the always-dangerous regex.
                if len(target) >= 2 and target[0] == target[-1] and target[0] in ('"', "'"):
                    target = target[1:-1]
                if self._RM_RF_ALWAYS_DANGEROUS.match(target) or ctx.policy.is_system_dir(target):
                    findings.append(
                        self._make_finding(
                            stripped[:200],
                            idx,
                            "Recursive deletion of system directories or home is "
                            "forbidden; restrict rm to scoped project paths.",
                        ))
                    continue

            # Accessing credential files — check if any path token is forbidden.
            if read_cmds_pattern is not None and read_cmds_pattern.search(stripped):
                for token in stripped.split():
                    # Handle --flag=value tokens (only for option-like tokens;
                    # plain tokens like "a=b.env" must be taken literally).
                    if token.startswith("-") and "=" in token:
                        token = token.split("=", 1)[1]
                    if ctx.policy.is_path_forbidden(token):
                        findings.append(
                            self._make_finding(
                                stripped[:200],
                                idx,
                                "Access to credential/forbidden file is not allowed.",
                            ))
                        break

            # dd to a block device
            if re.search(r"\bdd\b.*\bof=/dev/", stripped):
                findings.append(
                    self._make_finding(
                        stripped[:200],
                        idx,
                        "Writing to a block device with dd can destroy disk data.",
                    ))
        return findings


class BashNetworkEgressRule(Rule):
    """Detect outbound network calls to non-whitelisted domains."""

    rule_id = "BASH-NETWORK-EGRESS"
    description = "Outbound network call (curl/wget/nc) to a non-whitelisted domain"
    category = RiskCategory.NETWORK_EGRESS
    default_risk_level = RiskLevel.MEDIUM
    default_decision = Decision.NEEDS_HUMAN_REVIEW
    applies_to = (ScriptType.BASH, )

    _NET_CMDS = re.compile(r"\b(curl|wget|nc|netcat|socat|telnet|ssh|scp|rsync)\b", re.IGNORECASE)

    def check(self, ctx: ScanContext) -> list[Finding]:
        findings: list[Finding] = []
        lines = ctx.cached_lines if ctx.cached_lines is not None else ctx.script.splitlines()
        for idx, line in enumerate(lines, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if not self._NET_CMDS.search(stripped):
                continue

            urls = _extract_urls(stripped)
            if not urls:
                # Network command without an explicit URL — flag for review.
                findings.append(
                    self._make_finding(
                        stripped[:200],
                        idx,
                        "Network command with a non-literal target cannot be "
                        "verified against the whitelist; requires human review.",
                        risk_level=RiskLevel.LOW,
                    ))
                continue

            for url in urls:
                domain = _extract_domain(url)
                if domain and not ctx.policy.is_domain_allowed(domain):
                    findings.append(
                        self._make_finding(
                            stripped[:200],
                            idx,
                            f"Domain '{domain}' is not in the whitelist. "
                            "Add it to allowed_domains in the policy file.",
                            risk_level=RiskLevel.HIGH,
                            decision=Decision.DENY,
                        ))
        return findings


class BashProcessSystemRule(Rule):
    """Detect privilege escalation, shell injection and background processes."""

    rule_id = "BASH-PROCESS-SYSTEM"
    description = "Privilege escalation, shell injection, or background process detected"
    category = RiskCategory.PROCESS_SYSTEM
    default_risk_level = RiskLevel.HIGH
    default_decision = Decision.DENY
    applies_to = (ScriptType.BASH, )

    _SUDO = re.compile(r"\b(sudo|su\s+-|su\s+root|pkexec|doas)\b", re.IGNORECASE)
    _EVAL = re.compile(
        r"\b(?:eval|exec|source)\s+|(?:^|\s)\.\s+",
        re.IGNORECASE,
    )
    _PIPE_TO_SH = re.compile(r"\|\s*(sh|bash|zsh|python\d*|perl|ruby)\b", re.IGNORECASE)

    def check(self, ctx: ScanContext) -> list[Finding]:
        findings: list[Finding] = []
        lines = ctx.cached_lines if ctx.cached_lines is not None else ctx.script.splitlines()
        for idx, line in enumerate(lines, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            if self._SUDO.search(stripped):
                findings.append(
                    self._make_finding(
                        stripped[:200],
                        idx,
                        "Privilege escalation (sudo/su) is forbidden in "
                        "agent-generated scripts.",
                        risk_level=RiskLevel.CRITICAL,
                    ))
                continue

            if self._PIPE_TO_SH.search(stripped):
                findings.append(
                    self._make_finding(
                        stripped[:200],
                        idx,
                        "Piping output into a shell interpreter is a classic "
                        "shell-injection vector and is forbidden.",
                        risk_level=RiskLevel.CRITICAL,
                    ))
                continue

            if self._EVAL.search(stripped):
                findings.append(
                    self._make_finding(
                        stripped[:200],
                        idx,
                        "eval/exec/source can execute arbitrary dynamically-built "
                        "commands; avoid in agent scripts.",
                    ))
                continue

            # Trailing single & (background) — distinguish from &&
            if re.search(r"[^&]&\s*$", stripped):
                findings.append(
                    self._make_finding(
                        stripped[:200],
                        idx,
                        "Background process (&) may outlive the tool call and "
                        "leak resources; use explicit process management.",
                        risk_level=RiskLevel.LOW,
                        decision=Decision.NEEDS_HUMAN_REVIEW,
                    ))
        return findings


class BashDependencyInstallRule(Rule):
    """Detect package installation commands."""

    rule_id = "BASH-DEPENDENCY-INSTALL"
    description = "Package installation command (pip/npm/apt install) detected"
    category = RiskCategory.DEPENDENCY_INSTALL
    default_risk_level = RiskLevel.HIGH
    default_decision = Decision.DENY
    applies_to = (ScriptType.BASH, )

    _INSTALL = re.compile(
        r"\b(pip3?\s+install|python\d*\s+-m\s+pip\s+install|npm\s+(install|i)\s|"
        r"yarn\s+add|apt(-get)?\s+install|brew\s+install|conda\s+install|"
        r"pip3?\s+uninstall|npm\s+uninstall)\b",
        re.IGNORECASE,
    )

    def check(self, ctx: ScanContext) -> list[Finding]:
        findings: list[Finding] = []
        lines = ctx.cached_lines if ctx.cached_lines is not None else ctx.script.splitlines()
        for idx, line in enumerate(lines, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if self._INSTALL.search(stripped):
                findings.append(
                    self._make_finding(
                        stripped[:200],
                        idx,
                        "Dependency installation at runtime changes the execution "
                        "environment; declare dependencies in the project manifest.",
                    ))
        return findings


class BashResourceAbuseRule(Rule):
    """Detect fork bombs, infinite loops and excessive resource usage."""

    rule_id = "BASH-RESOURCE-ABUSE"
    description = "Resource-abuse pattern: fork bomb, infinite loop, or excessive sleep"
    category = RiskCategory.RESOURCE_ABUSE
    default_risk_level = RiskLevel.HIGH
    default_decision = Decision.DENY
    applies_to = (ScriptType.BASH, )

    # Classic fork bomb:  :(){ :|:& };:
    _FORK_BOMB = re.compile(r":\s*\(\s*\)\s*\{.*:.*:.*&.*\}\s*;\s*:", re.IGNORECASE)
    _WHILE_TRUE = re.compile(r"\bwhile\s+(true|1|\:)\s*;\s*do\b", re.IGNORECASE)
    _YES = re.compile(r"\byes\b(?:\s+(?!-)\S)?")
    _SLEEP = re.compile(r"\bsleep\s+(\d+)\b", re.IGNORECASE)

    def check(self, ctx: ScanContext) -> list[Finding]:
        findings: list[Finding] = []
        lines = ctx.cached_lines if ctx.cached_lines is not None else ctx.script.splitlines()
        for idx, line in enumerate(lines, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            if self._FORK_BOMB.search(stripped):
                findings.append(
                    self._make_finding(
                        stripped[:200],
                        idx,
                        "Fork bomb detected; this will exhaust process slots.",
                        risk_level=RiskLevel.CRITICAL,
                    ))
                continue

            if self._WHILE_TRUE.search(stripped):
                # Check if the loop body contains a break
                # (simple heuristic: look at the current line first for
                # inline break, then at subsequent lines until 'done')
                has_break = bool(re.search(r"\bbreak\b", stripped))
                if not has_break:
                    # Scan subsequent lines for break/done.
                    # idx is 1-based (from enumerate(start=1)); using it as
                    # a 0-based index naturally skips the current line
                    # (at lines[idx-1]) which was already checked above.
                    for next_lineno in range(idx, min(idx + 20, len(lines))):
                        if re.search(r"\bbreak\b", lines[next_lineno]):
                            has_break = True
                            break
                        if re.search(r"\bdone\b", lines[next_lineno]):
                            break
                if not has_break:
                    findings.append(
                        self._make_finding(
                            stripped[:200],
                            idx,
                            "Infinite loop without break will consume CPU; "
                            "add a termination condition.",
                        ))

            if self._YES.search(stripped):
                findings.append(
                    self._make_finding(
                        stripped[:200],
                        idx,
                        "'yes' command outputs indefinitely and can fill disk/memory.",
                        risk_level=RiskLevel.LOW,
                        decision=Decision.NEEDS_HUMAN_REVIEW,
                    ))

            sleep_match = self._SLEEP.search(stripped)
            if sleep_match:
                seconds = int(sleep_match.group(1))
                if seconds > ctx.policy.max_sleep_seconds:
                    findings.append(
                        self._make_finding(
                            stripped[:200],
                            idx,
                            f"sleep {seconds} exceeds the "
                            f"{ctx.policy.max_sleep_seconds}s limit; "
                            "use a shorter timeout with retry logic.",
                            risk_level=RiskLevel.LOW,
                            decision=Decision.NEEDS_HUMAN_REVIEW,
                        ))
        return findings


class BashSecretLeakRule(Rule):
    """Detect hardcoded secrets in echo/redirect/output commands."""

    rule_id = "BASH-SECRET-LEAK"
    description = "Hardcoded secret (API key, token, password) in command output"
    category = RiskCategory.SECRET_LEAK
    default_risk_level = RiskLevel.CRITICAL
    default_decision = Decision.DENY
    applies_to = (ScriptType.BASH, )

    _OUTPUT_CMDS = re.compile(r"\b(echo|printf|cat|tee|curl|wget)\b", re.IGNORECASE)

    # Environment variable names that strongly suggest a secret when echoed.
    _SECRET_ENV_VARS = re.compile(
        r"%\w*(API[_-]?KEY|SECRET|TOKEN|PASSWD|PASSWORD)\w*%|"
        r"\$\w*(API[_-]?KEY|SECRET|TOKEN|PASSWD|PASSWORD)\w*",
        re.IGNORECASE,
    )

    def check(self, ctx: ScanContext) -> list[Finding]:
        findings: list[Finding] = []
        compiled = ctx.compiled_secrets
        if compiled is None:
            compiled = [re.compile(p) for p in ctx.policy.secret_patterns]
        lines = ctx.cached_lines if ctx.cached_lines is not None else ctx.script.splitlines()
        for idx, line in enumerate(lines, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if not self._OUTPUT_CMDS.search(stripped):
                continue

            # Check for hardcoded secrets via regex patterns
            for pattern in compiled:
                if pattern.search(stripped):
                    findings.append(
                        self._make_finding(
                            _redact(stripped[:200]),
                            idx,
                            "Hardcoded secret detected in command output; "
                            "load secrets from environment variables instead.",
                        ))
                    break
            else:
                # Also flag echoing of secret-looking env vars (e.g. %API_KEY%, $SECRET)
                if self._SECRET_ENV_VARS.search(stripped):
                    findings.append(
                        self._make_finding(
                            _redact(stripped[:200]),
                            idx,
                            "Echoing environment variable that looks like a secret "
                            "(API key, token, password); redact before output.",
                            risk_level=RiskLevel.HIGH,
                            decision=Decision.DENY,
                        ))
        return findings


class BashShellInjectionRule(Rule):
    """Detect common shell-injection patterns: backticks, $(), chained &&."""

    rule_id = "BASH-SHELL-INJECTION"
    description = "Shell-injection pattern: command substitution or unsanitised chaining"
    category = RiskCategory.PROCESS_SYSTEM
    default_risk_level = RiskLevel.MEDIUM
    default_decision = Decision.NEEDS_HUMAN_REVIEW
    applies_to = (ScriptType.BASH, )

    _CMD_SUBST = re.compile(r"\$\(.+\)|`.+`")

    def check(self, ctx: ScanContext) -> list[Finding]:
        findings: list[Finding] = []
        lines = ctx.cached_lines if ctx.cached_lines is not None else ctx.script.splitlines()
        for idx, line in enumerate(lines, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if self._CMD_SUBST.search(stripped):
                findings.append(
                    self._make_finding(
                        stripped[:200],
                        idx,
                        "Command substitution ($(...) or backticks) can execute "
                        "arbitrary code built from untrusted input; sanitise inputs.",
                    ))
        return findings


class BashAllowedCommandRule(Rule):
    """Flag Bash commands not in the configured allow-list.

    This rule is **disabled by default** — it only runs when the policy's
    ``allowed_commands`` list is non-empty.  When enabled, any command
    whose first token is not in the allow-list is flagged for human
    review (not denied, because a non-whitelisted command is not
    necessarily dangerous).
    """

    rule_id = "BASH-COMMAND-WHITELIST"
    description = "Command not in the configured allow-list"
    category = RiskCategory.PROCESS_SYSTEM
    default_risk_level = RiskLevel.LOW
    default_decision = Decision.NEEDS_HUMAN_REVIEW
    applies_to = (ScriptType.BASH, )

    # Shell keywords / control structures that are never "commands".
    _KEYWORDS = frozenset({
        "if",
        "then",
        "else",
        "elif",
        "fi",
        "for",
        "in",
        "do",
        "done",
        "while",
        "until",
        "case",
        "esac",
        "function",
        "select",
        "{",
        "}",
        "!",
        "[",
        "[[",
    })

    def check(self, ctx: ScanContext) -> list[Finding]:
        allowed = ctx.policy.allowed_commands
        if not allowed:  # disabled by default
            return []
        allowed_lower = {c.lower().strip() for c in allowed}
        findings: list[Finding] = []
        lines = ctx.cached_lines if ctx.cached_lines is not None else ctx.script.splitlines()
        for idx, line in enumerate(lines, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            try:
                tokens = shlex.split(stripped, posix=True)
            except ValueError:
                # Unbalanced quotes etc — skip; other rules will catch.
                continue
            if not tokens:
                continue
            cmd = tokens[0]
            # Skip leading variable assignments:  VAR=value cmd ...
            while "=" in cmd and not cmd.startswith("-"):
                tokens = tokens[1:]
                if not tokens:
                    break
                cmd = tokens[0]
            if not cmd:
                continue
            # Strip directory prefix: /usr/bin/curl -> curl
            cmd_name = cmd.rsplit("/", 1)[-1].lower()
            # Skip shell keywords / control structures (also handles
            # full-path forms like /usr/bin/if after stripping).
            if cmd_name in self._KEYWORDS:
                continue
            if cmd_name not in allowed_lower:
                findings.append(
                    self._make_finding(
                        stripped[:200],
                        idx,
                        f"Command '{cmd}' is not in the allowed_commands "
                        "allow-list; add it to the policy file or use a "
                        "whitelisted alternative.",
                    ))
        return findings


# ---------------------------------------------------------------------------
# Register all built-in Bash rules
# ---------------------------------------------------------------------------


def _register_bash_rules() -> None:
    """Register the built-in Bash rules with the global registry."""
    for rule_cls in (
            BashDangerousFileOpsRule,
            BashNetworkEgressRule,
            BashProcessSystemRule,
            BashDependencyInstallRule,
            BashResourceAbuseRule,
            BashSecretLeakRule,
            BashShellInjectionRule,
            BashAllowedCommandRule,
    ):
        instance = rule_cls()
        global_rule_registry.register(instance)


_register_bash_rules()
