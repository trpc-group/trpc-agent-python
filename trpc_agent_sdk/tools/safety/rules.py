# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Central rule catalogue for the Tool Script Safety Guard.

Each rule is pure metadata: a ``rule_id`` plus its risk type, severity, the
action it suggests and a human-readable recommendation. The *detection* logic
(AST walking, shlex tokenising, regex) lives in the scanners; scanners reference
rules by id and call :func:`make_finding` to build a :class:`RiskFinding`.

``rule_id`` follows ``<CATEGORY>_<ACTION>_<OBJECT>`` in UPPER_SNAKE_CASE with the
category prefixes FILE / SECRET / NET / EXEC / PRIV / PKG / RES.

The three categories the issue requires to be caught 100% of the time -- secret
reads, dangerous deletes and non-allow-listed network egress -- are fixed at
``CRITICAL`` + ``DENY`` here, so they deny regardless of policy threshold tuning.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import Evidence
from .models import RiskFinding
from .models import RiskLevel
from .models import RiskType
from .models import SuggestedAction


@dataclass(frozen=True)
class RuleSpec:
    """Immutable metadata for a single rule."""

    rule_id: str
    risk_type: RiskType
    risk_level: RiskLevel
    suggested_action: SuggestedAction
    recommendation: str


_R = RiskLevel
_T = RiskType
_A = SuggestedAction

# --------------------------------------------------------------------------- #
# Rule catalogue. Order here is only for readability.
# --------------------------------------------------------------------------- #
_RULES: tuple[RuleSpec, ...] = (
    # --- Dangerous file operations -------------------------------------- #
    RuleSpec(
        "FILE_RM_RF", _T.DANGEROUS_FILE_OP, _R.CRITICAL, _A.DENY,
        "Recursive force-delete is destructive. Remove it or scope deletion to a "
        "reviewed, non-system directory."),
    RuleSpec(
        "FILE_FORBIDDEN_PATH", _T.DANGEROUS_FILE_OP, _R.HIGH, _A.DENY,
        "Access to a forbidden/system path is blocked. Use a path inside the workspace."),
    RuleSpec(
        "FILE_OVERWRITE_DEVICE", _T.DANGEROUS_FILE_OP, _R.HIGH, _A.DENY,
        "Writing to device/system files can brick the host. Avoid /dev, /proc, /sys."),
    RuleSpec(
        "PRIV_CHMOD_777", _T.DANGEROUS_FILE_OP, _R.MEDIUM, _A.REVIEW,
        "World-writable permissions (chmod 777) are risky. Use least-privilege modes."),
    # --- Secret leakage -------------------------------------------------- #
    RuleSpec(
        "SECRET_READ_SSH", _T.SECRET_LEAK, _R.CRITICAL, _A.DENY,
        "Reading SSH private keys is prohibited. Never load ~/.ssh credentials in tool scripts."),
    RuleSpec(
        "SECRET_READ_ENV", _T.SECRET_LEAK, _R.CRITICAL, _A.DENY,
        "Reading credential files (.env/.aws/credentials) is prohibited. Inject secrets via the "
        "runtime, not by reading files."),
    RuleSpec(
        "SECRET_LEAK_OUTPUT", _T.SECRET_LEAK, _R.CRITICAL, _A.DENY,
        "Printing or writing secrets (api_key/token/password) leaks them to logs or files. "
        "Redact or remove."),
    RuleSpec(
        "SECRET_HARDCODED", _T.SECRET_LEAK, _R.HIGH, _A.DENY,
        "Hard-coded credential detected. Load secrets from the environment instead."),
    # --- Network egress -------------------------------------------------- #
    RuleSpec(
        "NET_EGRESS_NON_ALLOWLIST", _T.NETWORK_EGRESS, _R.CRITICAL, _A.DENY,
        "Outbound connection to a non-allow-listed host. Add the host to allow_domains in the "
        "policy if it is trusted."),
    RuleSpec(
        "NET_INTERNAL_IP", _T.NETWORK_EGRESS, _R.CRITICAL, _A.DENY,
        "Connection to an internal/private IP range is blocked (SSRF risk)."),
    # --- Process / command execution ------------------------------------ #
    RuleSpec(
        "EXEC_SUBPROCESS", _T.PROCESS_EXEC, _R.MEDIUM, _A.REVIEW,
        "Spawning a subprocess needs human review. Confirm the command and arguments are trusted."),
    RuleSpec(
        "EXEC_OS_SYSTEM", _T.PROCESS_EXEC, _R.MEDIUM, _A.REVIEW,
        "Executing a shell command needs human review. Prefer argument lists over shell strings."),
    RuleSpec(
        "EXEC_SHELL_INJECTION", _T.PROCESS_EXEC, _R.HIGH, _A.DENY,
        "Dynamically built shell command is vulnerable to injection. Use a fixed argument list and "
        "never interpolate untrusted input."),
    RuleSpec(
        "EXEC_EVAL", _T.PROCESS_EXEC, _R.HIGH, _A.DENY,
        "Use of eval/exec/compile enables arbitrary code execution. Remove it."),
    RuleSpec(
        "EXEC_NON_ALLOWLIST_COMMAND", _T.PROCESS_EXEC, _R.MEDIUM, _A.REVIEW,
        "Command is not on the allowed_commands list. Needs human review."),
    RuleSpec(
        "PRIV_SUDO", _T.PROCESS_EXEC, _R.HIGH, _A.DENY,
        "Privilege escalation via sudo is not allowed in tool scripts."),
    # --- Dependency installation ---------------------------------------- #
    RuleSpec(
        "PKG_PIP_INSTALL", _T.DEPENDENCY_INSTALL, _R.MEDIUM, _A.REVIEW,
        "Installing Python packages changes the runtime. Needs human review."),
    RuleSpec(
        "PKG_NPM_INSTALL", _T.DEPENDENCY_INSTALL, _R.MEDIUM, _A.REVIEW,
        "Installing Node packages changes the runtime. Needs human review."),
    RuleSpec(
        "PKG_SYS_INSTALL", _T.DEPENDENCY_INSTALL, _R.MEDIUM, _A.REVIEW,
        "Installing system packages changes the host. Needs human review."),
    RuleSpec(
        "PKG_CURL_PIPE_SH", _T.DEPENDENCY_INSTALL, _R.CRITICAL, _A.DENY,
        "Piping downloaded content into a shell executes untrusted code. Never run 'curl | bash'."),
    # --- Resource abuse -------------------------------------------------- #
    RuleSpec(
        "RES_INFINITE_LOOP", _T.RESOURCE_ABUSE, _R.MEDIUM, _A.REVIEW,
        "Possible unbounded loop. Ensure a termination condition and rely on the sandbox timeout."),
    RuleSpec(
        "RES_FORK_BOMB", _T.RESOURCE_ABUSE, _R.HIGH, _A.DENY,
        "Fork-bomb pattern detected. This can exhaust the host."),
    RuleSpec(
        "RES_LARGE_SLEEP", _T.RESOURCE_ABUSE, _R.MEDIUM, _A.REVIEW,
        "Very long sleep can hang execution. Confirm intent."),
)

RULES: dict[str, RuleSpec] = {r.rule_id: r for r in _RULES}


def get_rule(rule_id: str) -> RuleSpec:
    """Return the :class:`RuleSpec` for ``rule_id`` or raise ``KeyError``."""
    return RULES[rule_id]


def make_finding(rule_id: str, snippet: str, line: int) -> RiskFinding:
    """Build a :class:`RiskFinding` from a rule id and the matched evidence.

    Args:
        rule_id: A key of :data:`RULES`.
        snippet: The offending text (will be redacted later by the engine).
        line: 1-based line number of the match within the payload.
    """
    spec = RULES[rule_id]
    return RiskFinding(
        rule_id=spec.rule_id,
        risk_type=spec.risk_type,
        risk_level=spec.risk_level,
        evidence=Evidence(snippet=snippet, line=line),
        recommendation=spec.recommendation,
        suggested_action=spec.suggested_action,
    )
