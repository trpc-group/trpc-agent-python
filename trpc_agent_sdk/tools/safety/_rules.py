# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Built-in safety rules for the Tool Script Safety Guard.

Each rule is a callable that receives the script content, the scan input,
and the current policy, and returns a list of ``SafetyFinding`` objects.

The six mandatory categories from the specification are implemented here:

1. **DangerousFileOpsRule**   — destructive file operations, credential access.
2. **NetworkEgressRule**       — outbound network access to non-whitelisted domains.
3. **ProcessAndSystemRule**    — subprocess, shell pipes, privilege escalation.
4. **DependencyInstallRule**   — package / dependency installation.
5. **ResourceAbuseRule**       — infinite loops, fork bombs, large writes.
6. **SensitiveInfoLeakRule**   — secrets in output / file writes / network.

Rules are **pluggable** — you can register additional rules via
:func:`register_rule` and they will be picked up by the scanner.
"""

from __future__ import annotations

import re
from typing import Callable
from typing import Optional

from trpc_agent_sdk.log import logger

from ._policy import SafetyPolicy
from ._types import RiskCategory
from ._types import RiskLevel
from ._types import SafetyFinding
from ._types import SafetyScanInput
from ._types import ScriptType

# ---------------------------------------------------------------------------
# Rule type
# ---------------------------------------------------------------------------

RuleCallable = Callable[[str, SafetyScanInput, SafetyPolicy], list[SafetyFinding]]

# Registry of additional user-defined rules
_EXTRA_RULES: list[RuleCallable] = []


def register_rule(rule: RuleCallable) -> None:
    """Register an additional safety rule that the scanner will invoke."""
    _EXTRA_RULES.append(rule)


def get_extra_rules() -> list[RuleCallable]:
    return list(_EXTRA_RULES)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_lines(script: str, pattern: str, *, script_type: Optional[ScriptType] = None) -> list[tuple[int, str]]:
    """Return (line_number, line_text) for every line matching *pattern* (regex).

    When *script_type* is PYTHON, ``#`` comments are stripped from each line
    before matching to avoid flagging commented-out code.
    """
    hits: list[tuple[int, str]] = []
    try:
        compiled = re.compile(pattern, re.IGNORECASE)
    except re.error:
        logger.warning("Invalid regex pattern in safety rule: %s", pattern)
        return hits
    for idx, line in enumerate(script.splitlines(), start=1):
        search_text = _strip_comments_for_script(line, script_type)
        if compiled.search(search_text):
            hits.append((idx, line.strip()))
    return hits


def _find_literal(script: str, pattern: str, *, script_type: Optional[ScriptType] = None) -> list[tuple[int, str]]:
    """Return (line_number, line_text) for every line containing *pattern* literally.

    Uses simple substring matching (case-insensitive) — safe for patterns
    with regex-special characters like ``|``, ``$(``, `` ` `` etc.

    When *script_type* is PYTHON or BASH, comments are stripped.
    """
    hits: list[tuple[int, str]] = []
    pattern_lower = pattern.lower()
    for idx, line in enumerate(script.splitlines(), start=1):
        search_text = _strip_comments_for_script(line, script_type)
        if pattern_lower in search_text.lower():
            hits.append((idx, line.strip()))
    return hits


def _strip_comments_for_script(line: str, script_type: Optional[ScriptType]) -> str:
    """Strip comments from a line based on script type.

    - PYTHON: strip ``# comment`` suffix (respects quotes).
    - BASH:   strip lines that start with ``#`` (full-line comments only;
              inline ``#`` in bash can be parameter expansion, so we keep it).
    - Other:  no stripping.
    """
    if script_type == ScriptType.PYTHON:
        return _strip_python_comment_line(line)
    if script_type == ScriptType.BASH:
        stripped = line.lstrip()
        if stripped.startswith("#") and not stripped.startswith("#!"):
            return ""  # whole line is a comment
    return line


def _strip_python_comment_line(line: str) -> str:
    """Remove ``# comment`` suffix AND string-literal content from a Python line.

    String literal content (``'…'``, ``\"…\"``, ``r'…'``, ``r\"…\"``,
    ``f'…'``, triple-quoted) is replaced with spaces so that regex patterns
    do not match code inside strings.  ``#`` outside strings terminates
    the line.
    """
    if line.lstrip().startswith("#!"):
        return line

    result: list[str] = []
    in_single = False
    in_double = False
    i = 0
    n = len(line)

    while i < n:
        ch = line[i]

        # Escape sequences
        if ch == "\\" and i + 1 < n:
            if in_single or in_double:
                result.append(" ")  # hide string content
                i += 2
                continue
            result.append(ch)
            result.append(line[i + 1])
            i += 2
            continue

        # Triple-quote detection (simplified — checks for ''' or \"\"\")
        if (not in_single and not in_double and i + 2 < n and ch in ("'", '"') and line[i:i + 3] == ch * 3):
            marker = ch * 3
            result.append(marker)
            i += 3
            # Skip until closing triple quote
            while i < n - 2:
                if line[i:i + 3] == marker:
                    result.append(marker)
                    i += 3
                    break
                result.append(" ")  # hide triple-quoted content
                i += 1
            continue

        # String start detection
        if ch in ("'", '"') and not in_double and not in_single:
            # Check for prefix: r, f, b, u, rf, fr, rb, br
            prefix = ""
            j = i - 1
            while j >= 0 and line[j].isalpha():
                j -= 1
            if j < i - 1:
                prefix = line[j + 1:i].lower()
            valid_prefix = prefix in ("", "r", "f", "b", "u", "rf", "fr", "rb", "br")
            is_string = valid_prefix or prefix == ""

            if ch == "'" and is_string:
                in_single = True
                result.append(ch)
                i += 1
                continue
            if ch == '"' and is_string:
                in_double = True
                result.append(ch)
                i += 1
                continue
            # Fall through — not a string start
            result.append(ch)
            i += 1
            continue

        if ch == "'" and in_single and not in_double:
            in_single = False
            result.append(ch)
            i += 1
            continue
        if ch == '"' and in_double and not in_single:
            in_double = False
            result.append(ch)
            i += 1
            continue

        # Inside string: hide content
        if in_single or in_double:
            result.append(" ")  # replace with space so regex doesn't match
            i += 1
            continue

        # Comment outside strings
        if ch == "#":
            break

        result.append(ch)
        i += 1

    return "".join(result)


def _build_finding(
    rule_id: str,
    category: RiskCategory,
    risk_level: RiskLevel,
    evidence: str,
    message: str,
    recommendation: str,
    line_number: int = 0,
    matched_pattern: str = "",
) -> SafetyFinding:
    return SafetyFinding(
        rule_id=rule_id,
        category=category,
        risk_level=risk_level,
        evidence=evidence[:500],  # truncate long evidence
        message=message,
        recommendation=recommendation,
        line_number=line_number,
        matched_pattern=matched_pattern,
    )


def _matches_any(script: str, patterns: list[str]) -> bool:
    for p in patterns:
        try:
            if re.search(p, script, re.IGNORECASE):
                return True
        except re.error:
            continue
    return False


# ========================================================================
# Rule 1 — Dangerous File Operations
# ========================================================================


class DangerousFileOpsRule:
    """Detects dangerous file operations: recursive delete, credential access, etc."""

    RULE_ID_PREFIX = "FILE"

    def __call__(
        self,
        script: str,
        scan_input: SafetyScanInput,
        policy: SafetyPolicy,
    ) -> list[SafetyFinding]:
        findings: list[SafetyFinding] = []
        cfg = policy.rule_configs.get("dangerous_file_ops", {})
        if not cfg.get("enabled", True):
            return findings

        # 1a. Blocklisted paths (hard-block)
        for blocked in policy.blocklist_paths:
            # Normalise path for matching
            pattern = re.escape(blocked).replace(r"\*", ".*")
            for line_no, line_text in _find_lines(script, pattern, script_type=scan_input.script_type):
                findings.append(
                    _build_finding(
                        rule_id=f"{self.RULE_ID_PREFIX}-001",
                        category=RiskCategory.DANGEROUS_FILE_OPS,
                        risk_level=RiskLevel.CRITICAL,
                        evidence=line_text,
                        message=f"Access to blocklisted path detected: {blocked}",
                        recommendation=f"Remove references to {blocked}. If legitimate, "
                        f"add the path to the policy whitelist.",
                        line_number=line_no,
                        matched_pattern=blocked,
                    ))

        # 1b. Blocklisted patterns
        for blocked_pat in policy.blocklist_patterns:
            for line_no, line_text in _find_lines(script, blocked_pat, script_type=scan_input.script_type):
                # Skip Bash echo/printf lines where rm/mkfs appears in a string
                if scan_input.script_type == ScriptType.BASH and _is_in_echo_string(line_text, blocked_pat):
                    continue
                if "rm" in blocked_pat.lower() or "mkfs" in blocked_pat.lower() or "dd" in blocked_pat.lower():
                    findings.append(
                        _build_finding(
                            rule_id=f"{self.RULE_ID_PREFIX}-002",
                            category=RiskCategory.DANGEROUS_FILE_OPS,
                            risk_level=RiskLevel.CRITICAL,
                            evidence=line_text,
                            message=f"Destructive blocklisted pattern matched: {blocked_pat}",
                            recommendation="Remove the destructive operation from the script.",
                            line_number=line_no,
                            matched_pattern=blocked_pat,
                        ))

        # 1c. Sensitive paths
        sensitive = cfg.get("sensitive_paths", [])
        for sens_path in sensitive:
            # Use word-boundary-aware matching to avoid .env matching "environ"
            pattern = _path_boundary_pattern(sens_path)
            for line_no, line_text in _find_lines(script, pattern, script_type=scan_input.script_type):
                findings.append(
                    _build_finding(
                        rule_id=f"{self.RULE_ID_PREFIX}-003",
                        category=RiskCategory.DANGEROUS_FILE_OPS,
                        risk_level=RiskLevel.HIGH,
                        evidence=line_text,
                        message=f"Access to sensitive path: {sens_path}",
                        recommendation=f"Ensure accessing {sens_path} is necessary. "
                        f"Consider using a dedicated secrets manager instead.",
                        line_number=line_no,
                        matched_pattern=sens_path,
                    ))

        # 1d. Credential file patterns
        cred_patterns = cfg.get("credential_file_patterns", [])
        for cred_pat in cred_patterns:
            for line_no, line_text in _find_lines(script, cred_pat, script_type=scan_input.script_type):
                findings.append(
                    _build_finding(
                        rule_id=f"{self.RULE_ID_PREFIX}-004",
                        category=RiskCategory.DANGEROUS_FILE_OPS,
                        risk_level=RiskLevel.CRITICAL,
                        evidence=line_text,
                        message=f"Credential file pattern matched: {cred_pat}",
                        recommendation="Do not read, write, or transmit credential files. "
                        "Use environment variables or a secrets manager.",
                        line_number=line_no,
                        matched_pattern=cred_pat,
                    ))

        # 1e. Destructive operations
        destructive = cfg.get("destructive_patterns", [])
        for dest_pat in destructive:
            for line_no, line_text in _find_lines(script, dest_pat, script_type=scan_input.script_type):
                # Skip Bash echo/printf lines where pattern appears in a string
                if scan_input.script_type == ScriptType.BASH and _is_in_echo_string(line_text, dest_pat):
                    continue
                findings.append(
                    _build_finding(
                        rule_id=f"{self.RULE_ID_PREFIX}-005",
                        category=RiskCategory.DANGEROUS_FILE_OPS,
                        risk_level=RiskLevel.CRITICAL,
                        evidence=line_text,
                        message=f"Destructive file operation detected: {line_text[:120]}",
                        recommendation="Avoid destructive operations. Use temporary "
                        "directories and clean up explicitly.",
                        line_number=line_no,
                        matched_pattern=dest_pat,
                    ))

        return findings


# ========================================================================
# Rule 2 — Network Egress
# ========================================================================


class NetworkEgressRule:
    """Detects outbound network requests to non-whitelisted destinations."""

    RULE_ID_PREFIX = "NET"

    def __call__(
        self,
        script: str,
        scan_input: SafetyScanInput,
        policy: SafetyPolicy,
    ) -> list[SafetyFinding]:
        findings: list[SafetyFinding] = []
        cfg = policy.rule_configs.get("network_egress", {})
        if not cfg.get("enabled", True):
            return findings

        python_funcs = cfg.get("python_functions", [])
        bash_cmds = cfg.get("bash_commands", [])

        if scan_input.script_type in (ScriptType.PYTHON, ScriptType.UNKNOWN):
            for func_pat in python_funcs:
                for line_no, line_text in _find_lines(script, func_pat, script_type=scan_input.script_type):
                    # Extract URL / domain for whitelist check (same as Bash branch)
                    url_match = _extract_url(line_text)
                    if url_match and policy.is_domain_whitelisted(url_match):
                        findings.append(
                            _build_finding(
                                rule_id=f"{self.RULE_ID_PREFIX}-002",
                                category=RiskCategory.NETWORK_EGRESS,
                                risk_level=RiskLevel.INFO,
                                evidence=line_text,
                                message=f"Python network call to whitelisted domain '{url_match}'.",
                                recommendation="No action needed — domain is whitelisted.",
                                line_number=line_no,
                                matched_pattern=func_pat,
                            ))
                    else:
                        findings.append(
                            _build_finding(
                                rule_id=f"{self.RULE_ID_PREFIX}-001",
                                category=RiskCategory.NETWORK_EGRESS,
                                risk_level=RiskLevel.HIGH,
                                evidence=line_text,
                                message=f"Network client library detected: {func_pat}",
                                recommendation="Ensure the target domain is whitelisted. "
                                "Restrict outbound network access at the network/firewall level.",
                                line_number=line_no,
                                matched_pattern=func_pat,
                            ))

        if scan_input.script_type in (ScriptType.BASH, ScriptType.UNKNOWN):
            for cmd in bash_cmds:
                for line_no, line_text in _find_literal(script, cmd, script_type=scan_input.script_type):
                    cmd_key = cmd.strip()

                    # FIXED: Check whitelist_commands — was dead code before
                    if policy.is_command_whitelisted(cmd_key):
                        findings.append(
                            _build_finding(
                                rule_id=f"{self.RULE_ID_PREFIX}-003",
                                category=RiskCategory.NETWORK_EGRESS,
                                risk_level=RiskLevel.INFO,
                                evidence=line_text,
                                message=f"Network command '{cmd_key}' is whitelisted — allowed.",
                                recommendation="No action needed — command is whitelisted.",
                                line_number=line_no,
                                matched_pattern=cmd_key,
                            ))
                        continue

                    # Extract potential URL / domain for whitelist check
                    url_match = _extract_url(line_text)
                    if url_match and policy.is_domain_whitelisted(url_match):
                        # Whitelisted — downgrade to info
                        findings.append(
                            _build_finding(
                                rule_id=f"{self.RULE_ID_PREFIX}-002",
                                category=RiskCategory.NETWORK_EGRESS,
                                risk_level=RiskLevel.INFO,
                                evidence=line_text,
                                message=f"Network command '{cmd_key}' targeting "
                                f"whitelisted domain '{url_match}'.",
                                recommendation="No action needed — domain is whitelisted.",
                                line_number=line_no,
                                matched_pattern=cmd_key,
                            ))
                    else:
                        findings.append(
                            _build_finding(
                                rule_id=f"{self.RULE_ID_PREFIX}-001",
                                category=RiskCategory.NETWORK_EGRESS,
                                risk_level=RiskLevel.HIGH,
                                evidence=line_text,
                                message=f"Network command detected: {cmd_key}",
                                recommendation="Verify the target domain. If safe, add it to "
                                "the policy whitelist domains.",
                                line_number=line_no,
                                matched_pattern=cmd_key,
                            ))

        return findings


# ========================================================================
# Rule 3 — Process & System Commands
# ========================================================================


class ProcessAndSystemRule:
    """Detects subprocess calls, shell pipes, privilege escalation, etc."""

    RULE_ID_PREFIX = "PROC"

    def __call__(
        self,
        script: str,
        scan_input: SafetyScanInput,
        policy: SafetyPolicy,
    ) -> list[SafetyFinding]:
        findings: list[SafetyFinding] = []
        cfg = policy.rule_configs.get("process_and_system", {})
        if not cfg.get("enabled", True):
            return findings

        python_funcs = cfg.get("python_functions", [])
        bash_patterns = cfg.get("bash_patterns", [])

        if scan_input.script_type in (ScriptType.PYTHON, ScriptType.UNKNOWN):
            for func_pat in python_funcs:
                for line_no, line_text in _find_lines(script, func_pat, script_type=scan_input.script_type):
                    # Privilege escalation is critical
                    # Skip safe compile() calls: re.compile() is regex compilation, not code injection
                    if "compile" in func_pat.lower() and "re.compile" in line_text.lower():
                        continue

                    if any(kw in func_pat.lower() for kw in ("setuid", "setgid", "seteuid", "setegid")):
                        risk = RiskLevel.CRITICAL
                    elif any(kw in func_pat.lower()
                             for kw in ("system", "popen", "subprocess", "eval", "exec", "__import__", "compile")):
                        risk = RiskLevel.HIGH
                    else:
                        risk = RiskLevel.MEDIUM

                    findings.append(
                        _build_finding(
                            rule_id=f"{self.RULE_ID_PREFIX}-001",
                            category=RiskCategory.PROCESS_AND_SYSTEM,
                            risk_level=risk,
                            evidence=line_text,
                            message=f"Process execution call detected: {func_pat}",
                            recommendation="Avoid spawning child processes from within "
                            "agent tools. Prefer library-based implementations.",
                            line_number=line_no,
                            matched_pattern=func_pat,
                        ))

        if scan_input.script_type in (ScriptType.BASH, ScriptType.UNKNOWN):
            for bash_pat in bash_patterns:
                for line_no, line_text in _find_literal(script, bash_pat, script_type=scan_input.script_type):
                    cmd_key = bash_pat.strip()

                    # Pipe operator on a whitelisted-commands-only line → downgrade to INFO
                    pipe_is_safe = False
                    if cmd_key == "|" and _all_commands_whitelisted(line_text, policy):
                        pipe_is_safe = True

                    # FIXED: Check whitelist_commands — was dead code before
                    # Whitelisted commands get downgraded to INFO or skipped
                    if policy.is_command_whitelisted(cmd_key) and cmd_key not in ("|", "$(", "`", "&>", "nohup",
                                                                                  "disown"):
                        # Explicitly whitelisted → informational only
                        findings.append(
                            _build_finding(
                                rule_id=f"{self.RULE_ID_PREFIX}-003",
                                category=RiskCategory.PROCESS_AND_SYSTEM,
                                risk_level=RiskLevel.INFO,
                                evidence=line_text,
                                message=f"Shell command '{cmd_key}' is whitelisted — allowed.",
                                recommendation="No action needed — command is whitelisted.",
                                line_number=line_no,
                                matched_pattern=cmd_key,
                            ))
                        continue  # Don't add a second finding for the same match

                    # Privilege escalation
                    if cmd_key in ("sudo", "su", "chroot"):
                        risk = RiskLevel.CRITICAL
                    elif cmd_key in ("mount", "umount", "systemctl", "kill -9"):
                        risk = RiskLevel.HIGH
                    elif cmd_key in ("|", "$(", "`"):
                        risk = RiskLevel.INFO if pipe_is_safe else RiskLevel.MEDIUM
                    else:
                        risk = RiskLevel.HIGH

                    findings.append(
                        _build_finding(
                            rule_id=f"{self.RULE_ID_PREFIX}-002",
                            category=RiskCategory.PROCESS_AND_SYSTEM,
                            risk_level=risk,
                            evidence=line_text,
                            message=f"Potentially dangerous shell pattern: {cmd_key}",
                            recommendation="Use safe alternatives or explicitly whitelist "
                            "the command in the policy.",
                            line_number=line_no,
                            matched_pattern=cmd_key,
                        ))

        return findings


# ========================================================================
# Rule 4 — Dependency Installation
# ========================================================================


class DependencyInstallRule:
    """Detects package / dependency installation commands."""

    RULE_ID_PREFIX = "DEP"

    def __call__(
        self,
        script: str,
        scan_input: SafetyScanInput,
        policy: SafetyPolicy,
    ) -> list[SafetyFinding]:
        findings: list[SafetyFinding] = []
        cfg = policy.rule_configs.get("dependency_install", {})
        if not cfg.get("enabled", True):
            return findings

        python_funcs = cfg.get("python_functions", [])
        bash_cmds = cfg.get("bash_commands", [])

        if scan_input.script_type in (ScriptType.PYTHON, ScriptType.UNKNOWN):
            for func_pat in python_funcs:
                for line_no, line_text in _find_lines(script, func_pat, script_type=scan_input.script_type):
                    findings.append(
                        _build_finding(
                            rule_id=f"{self.RULE_ID_PREFIX}-001",
                            category=RiskCategory.DEPENDENCY_INSTALL,
                            risk_level=RiskLevel.HIGH,
                            evidence=line_text,
                            message=f"Dependency installation detected: {func_pat}",
                            recommendation="Pre-install dependencies in the container image "
                            "or environment rather than at runtime.",
                            line_number=line_no,
                            matched_pattern=func_pat,
                        ))

        if scan_input.script_type in (ScriptType.BASH, ScriptType.UNKNOWN):
            for cmd in bash_cmds:
                for line_no, line_text in _find_literal(script, cmd, script_type=scan_input.script_type):
                    findings.append(
                        _build_finding(
                            rule_id=f"{self.RULE_ID_PREFIX}-002",
                            category=RiskCategory.DEPENDENCY_INSTALL,
                            risk_level=RiskLevel.HIGH,
                            evidence=line_text,
                            message=f"Package manager invocation: {cmd.strip()}",
                            recommendation="Dependencies should be declared statically "
                            "(requirements.txt, pyproject.toml, Dockerfile) and not "
                            "installed at tool execution time.",
                            line_number=line_no,
                            matched_pattern=cmd,
                        ))

        return findings


# ========================================================================
# Rule 5 — Resource Abuse
# ========================================================================


class ResourceAbuseRule:
    """Detects infinite loops, fork bombs, large writes, long sleeps, etc."""

    RULE_ID_PREFIX = "RES"

    def __call__(
        self,
        script: str,
        scan_input: SafetyScanInput,
        policy: SafetyPolicy,
    ) -> list[SafetyFinding]:
        findings: list[SafetyFinding] = []
        cfg = policy.rule_configs.get("resource_abuse", {})
        if not cfg.get("enabled", True):
            return findings

        # 5a. Infinite loops
        loop_patterns = cfg.get("infinite_loop_patterns", [])
        for loop_pat in loop_patterns:
            for line_no, line_text in _find_lines(script, loop_pat, script_type=scan_input.script_type):
                findings.append(
                    _build_finding(
                        rule_id=f"{self.RULE_ID_PREFIX}-001",
                        category=RiskCategory.RESOURCE_ABUSE,
                        risk_level=RiskLevel.MEDIUM,
                        evidence=line_text,
                        message=f"Infinite loop pattern detected: {loop_pat}",
                        recommendation="Add a timeout, iteration limit, or exit condition.",
                        line_number=line_no,
                        matched_pattern=loop_pat,
                    ))

        # 5b. Fork bombs
        fork_patterns = cfg.get("fork_bomb_patterns", [])
        for fork_pat in fork_patterns:
            for line_no, line_text in _find_lines(script, fork_pat, script_type=scan_input.script_type):
                findings.append(
                    _build_finding(
                        rule_id=f"{self.RULE_ID_PREFIX}-002",
                        category=RiskCategory.RESOURCE_ABUSE,
                        risk_level=RiskLevel.CRITICAL,
                        evidence=line_text,
                        message=f"Fork bomb pattern detected: {fork_pat}",
                        recommendation="Fork bombs can crash the host. Remove immediately.",
                        line_number=line_no,
                        matched_pattern=fork_pat,
                    ))

        # 5c. Resource-heavy patterns
        heavy_patterns = cfg.get("resource_heavy_patterns", [])
        for heavy_pat in heavy_patterns:
            for line_no, line_text in _find_lines(script, heavy_pat, script_type=scan_input.script_type):
                findings.append(
                    _build_finding(
                        rule_id=f"{self.RULE_ID_PREFIX}-003",
                        category=RiskCategory.RESOURCE_ABUSE,
                        risk_level=RiskLevel.HIGH,
                        evidence=line_text,
                        message=f"Resource-heavy operation: {heavy_pat}",
                        recommendation="Limit I/O throughput and file sizes. "
                        "Use streaming or chunked writes.",
                        line_number=line_no,
                        matched_pattern=heavy_pat,
                    ))

        # 5d. Long sleeps
        threshold = cfg.get("long_sleep_threshold_seconds", 60)
        sleep_pattern = r"sleep\s+(\d+)"
        for m in re.finditer(sleep_pattern, script, re.IGNORECASE):
            duration = int(m.group(1))
            if duration > threshold:
                line_no = script[:m.start()].count("\n") + 1
                findings.append(
                    _build_finding(
                        rule_id=f"{self.RULE_ID_PREFIX}-004",
                        category=RiskCategory.RESOURCE_ABUSE,
                        risk_level=RiskLevel.MEDIUM,  # MEDIUM → needs_human_review
                        evidence=m.group(0),
                        message=f"Long sleep ({duration}s) exceeds threshold ({threshold}s)",
                        recommendation="Reduce sleep duration or use a task scheduler.",
                        line_number=line_no,
                        matched_pattern=m.group(0),
                    ))

        # 5e. Concurrent task spawning
        max_concurrent = cfg.get("max_concurrent_tasks", 20)
        conc_patterns = [
            r"ThreadPoolExecutor\s*\(.*max_workers\s*=\s*(\d+)",
            r"ProcessPoolExecutor\s*\(.*max_workers\s*=\s*(\d+)",
            r"concurrent\.futures",
            r"multiprocessing\.Pool\s*\(.*processes\s*=\s*(\d+)",
            r"&[\s]*done",
        ]
        for conc_pat in conc_patterns:
            for line_no, line_text in _find_lines(script, conc_pat, script_type=scan_input.script_type):
                findings.append(
                    _build_finding(
                        rule_id=f"{self.RULE_ID_PREFIX}-005",
                        category=RiskCategory.RESOURCE_ABUSE,
                        risk_level=RiskLevel.MEDIUM,
                        evidence=line_text,
                        message="Concurrent task spawning detected",
                        recommendation=f"Limit concurrency to at most {max_concurrent} "
                        "tasks. Use a task queue for larger workloads.",
                        line_number=line_no,
                        matched_pattern=conc_pat,
                    ))

        return findings


# ========================================================================
# Rule 6 — Sensitive Information Leakage
# ========================================================================


class SensitiveInfoLeakRule:
    """Detects API keys, tokens, passwords, and private keys in script output."""

    RULE_ID_PREFIX = "LEAK"

    def __call__(
        self,
        script: str,
        scan_input: SafetyScanInput,
        policy: SafetyPolicy,
    ) -> list[SafetyFinding]:
        findings: list[SafetyFinding] = []
        cfg = policy.rule_configs.get("sensitive_info_leak", {})
        if not cfg.get("enabled", True):
            return findings

        # 6a. Secrets in hard-coded assignments
        secret_patterns = cfg.get("secret_patterns", [])
        for secret_pat in secret_patterns:
            for line_no, line_text in _find_lines(script, secret_pat, script_type=scan_input.script_type):
                findings.append(
                    _build_finding(
                        rule_id=f"{self.RULE_ID_PREFIX}-001",
                        category=RiskCategory.SENSITIVE_INFO_LEAK,
                        risk_level=RiskLevel.CRITICAL,
                        evidence=line_text,
                        message="Hard-coded secret / credential detected",
                        recommendation="Never hard-code secrets. Use environment "
                        "variables or a secrets manager (e.g., HashiCorp Vault, "
                        "AWS Secrets Manager).",
                        line_number=line_no,
                        matched_pattern=secret_pat,
                    ))

        # 6b. Output / logging of secrets
        output_commands = cfg.get("output_commands", [])
        for out_cmd in output_commands:
            for line_no, line_text in _find_lines(script, out_cmd, script_type=scan_input.script_type):
                findings.append(
                    _build_finding(
                        rule_id=f"{self.RULE_ID_PREFIX}-002",
                        category=RiskCategory.SENSITIVE_INFO_LEAK,
                        risk_level=RiskLevel.CRITICAL,
                        evidence=line_text,
                        message="Secret may be written to stdout, log, or file",
                        recommendation="Mask or strip secrets before logging. "
                        "Use structured logging with automatic PII redaction.",
                        line_number=line_no,
                        matched_pattern=out_cmd,
                    ))

        # 6c. File writes of secrets
        file_writes = cfg.get("sensitive_file_writes", [])
        for fw_pat in file_writes:
            for line_no, line_text in _find_lines(script, fw_pat, script_type=scan_input.script_type):
                findings.append(
                    _build_finding(
                        rule_id=f"{self.RULE_ID_PREFIX}-003",
                        category=RiskCategory.SENSITIVE_INFO_LEAK,
                        risk_level=RiskLevel.CRITICAL,
                        evidence=line_text,
                        message="Secret may be written to a file",
                        recommendation="Do not persist secrets to disk. "
                        "Use in-memory or ephemeral storage.",
                        line_number=line_no,
                        matched_pattern=fw_pat,
                    ))

        # 6d. Environment variable leakage (blocklisted env vars)
        for env_var in policy.blocklist_env_vars:
            env_pattern = rf"\b{re.escape(env_var)}\b"
            for line_no, line_text in _find_lines(script, env_pattern, script_type=scan_input.script_type):
                findings.append(
                    _build_finding(
                        rule_id=f"{self.RULE_ID_PREFIX}-004",
                        category=RiskCategory.SENSITIVE_INFO_LEAK,
                        risk_level=RiskLevel.HIGH,
                        evidence=line_text,
                        message=f"Reference to sensitive environment variable: {env_var}",
                        recommendation="Avoid reading sensitive env vars directly. "
                        "If needed, ensure they are not echoed or written out.",
                        line_number=line_no,
                        matched_pattern=env_var,
                    ))

        return findings


# ========================================================================
# Helpers
# ========================================================================


def _is_in_echo_string(line: str, pattern: str) -> bool:
    """Return True if *pattern* matches are ALL inside echo/printf string literals.

    In Bash, ``echo 'rm -rf /'`` is harmless.  But ``echo "rm -rf /"; rm -rf /``
    is dangerous — the first ``rm`` is harmless but the second is real.  This
    helper only suppresses a finding when the pattern appears **nowhere** outside
    echo/printf quoted strings on the line.
    """
    stripped = line.strip()
    # Only applies to echo / printf commands
    if not (stripped.startswith("echo ") or stripped.startswith("echo\t") or stripped.startswith("printf ")
            or stripped.startswith("printf\t") or stripped.startswith("/bin/echo ")
            or stripped.startswith("/usr/bin/echo ")):
        return False
    # Check if the pattern matches inside any quoted string
    try:
        pat = re.compile(pattern, re.IGNORECASE)
    except re.error:
        return False

    in_quotes = False
    for m in re.finditer(r"'[^']*'", stripped):
        if pat.search(m.group(0)):
            in_quotes = True
            break
    if not in_quotes:
        for m in re.finditer(r'"[^"]*"', stripped):
            if pat.search(m.group(0)):
                in_quotes = True
                break
    if not in_quotes:
        return False  # pattern not in any quoted string — normal danger report

    # Pattern found inside quotes.  Now check whether it ALSO appears
    # outside quotes (e.g. after ; / &&).  If so, it is a real danger.
    outside = re.sub(r"'[^']*'", " ", stripped)
    outside = re.sub(r'"[^"]*"', " ", outside)
    if pat.search(outside):
        return False  # match outside quotes → real danger

    return True


def _all_commands_whitelisted(line: str, policy: SafetyPolicy) -> bool:
    """Return True if every command in a piped bash line is whitelisted."""
    cmds = []
    for part in line.split("|"):
        part = part.strip()
        if part:
            first_word = part.split()[0] if part.split() else ""
            if first_word and not first_word.startswith("-"):
                cmds.append(first_word)
    if not cmds:
        return False
    return all(policy.is_command_whitelisted(c) for c in cmds)


def _path_boundary_pattern(path: str) -> str:
    """Build a regex that matches *path* as a path component, not a substring.

    ``.env`` should match ``./.env`` and ``cat .env`` but NOT ``os.environ``.
    """
    escaped = re.escape(path)
    # If the path starts with a dot (like .env), require a path boundary before it
    if path.startswith("."):
        return r"(?:^|[\s/'\"`;|&(])" + escaped + r"(?:$|[\s/'\"`;|&)])"
    else:
        return escaped.replace(r"\*", ".*")


def _extract_url(text: str) -> Optional[str]:
    """Naive domain extractor from a line of text — used for whitelist checks."""
    # Match http(s)://domain or domain-like patterns after curl/wget
    m = re.search(r"https?://([^\s/\"':]+)", text)
    if m:
        return m.group(1)
    # Also try bare domain patterns like 'api.example.com'
    # Must have at least one dot separating valid TLD-like segments
    m = re.search(r"(?:^|\s)((?:[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?\.)+[a-zA-Z]{2,})", text)
    if m:
        candidate = m.group(0).strip()
        # Filter out obvious false positives: Python method calls, variable names, etc.
        if "(" in candidate or candidate.startswith("."):
            return None
        return candidate
    return None


# ========================================================================
# Built-in rule list
# ========================================================================

_BUILTIN_RULES: list[RuleCallable] = [
    DangerousFileOpsRule(),
    NetworkEgressRule(),
    ProcessAndSystemRule(),
    DependencyInstallRule(),
    ResourceAbuseRule(),
    SensitiveInfoLeakRule(),
]


def get_builtin_rules() -> list[RuleCallable]:
    return list(_BUILTIN_RULES)


def get_all_rules() -> list[RuleCallable]:
    """Return built-in + user-registered rules."""
    return get_builtin_rules() + get_extra_rules()
