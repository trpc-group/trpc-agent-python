"""Cross-field scanner that correlates request fields.

Looks at argv, cwd, env, timeout, output budget, and tool metadata
together. These checks do not depend on the script body and therefore
run for every request regardless of language.
"""

from __future__ import annotations

from typing import Iterable

from trpc_agent_sdk.tools.safety._models import (
    RiskCategory,
    RiskLevel,
    SafetyDecision,
    SafetyFinding,
    SafetyScanRequest,
    ScriptLanguage,
)
from trpc_agent_sdk.tools.safety._policy import (
    ToolSafetyPolicy,
    match_path_glob,
    normalize_script_path_for_match,
)
from trpc_agent_sdk.tools.safety._redaction import Redactor
from trpc_agent_sdk.tools.safety._rules import (
    SafetyRule,
    _default_unknown,
    _finding,
    resolve_decision,
)

_CWD_RULE_ID = "FILE002_DENIED_WRITE"
_TIMEOUT_RULE_ID = "RES003_LONG_SLEEP"
_ARGV_RULE_ID = "PROC001_PROCESS_EXEC"
_TOOL_MAPPING_RULE_ID = "PARSE001_UNCERTAIN"


class CrossFieldScannerRule(SafetyRule):
    """Runs the cross-field catalog against request fields."""

    rule_id = "cross_field_scanner"

    def scan(
        self,
        request: SafetyScanRequest,
        policy: ToolSafetyPolicy,
    ) -> Iterable[SafetyFinding]:
        redactor = Redactor(env_values=request.env.values())
        findings: list[SafetyFinding] = []
        findings.extend(self._check_cwd(request, policy, redactor))
        findings.extend(self._check_timeout(request, policy, redactor))
        findings.extend(self._check_argv(request, policy, redactor))
        findings.extend(self._check_tool_mapping(request, policy, redactor))
        findings.extend(self._check_output_budget(request, policy, redactor))
        return findings

    # ----- checks ----- #

    def _check_cwd(
        self,
        request: SafetyScanRequest,
        policy: ToolSafetyPolicy,
        redactor: Redactor,
    ) -> list[SafetyFinding]:
        if not request.cwd:
            return []
        normalized = normalize_script_path_for_match(request.cwd)
        # Detect escaping paths (../../etc)
        if ".." in request.cwd.replace("\\", "/").split("/"):
            decision = resolve_decision(_CWD_RULE_ID, SafetyDecision.DENY, policy)
            return [
                _finding(
                    rule_id=_CWD_RULE_ID,
                    category=RiskCategory.FILE,
                    risk=RiskLevel.HIGH,
                    decision=decision,
                    snippet=f"cwd={request.cwd}",
                    language=ScriptLanguage.UNKNOWN,
                    redactor=redactor,
                    recommendation="cwd attempts to escape via '..'.",
                    extras={"cwd": "<redacted>"},
                )
            ]
        for pattern in policy.paths.deny:
            if match_path_glob(normalized, pattern):
                decision = resolve_decision(_CWD_RULE_ID, SafetyDecision.DENY, policy)
                return [
                    _finding(
                        rule_id=_CWD_RULE_ID,
                        category=RiskCategory.FILE,
                        risk=RiskLevel.HIGH,
                        decision=decision,
                        snippet=f"cwd={request.cwd}",
                        language=ScriptLanguage.UNKNOWN,
                        redactor=redactor,
                        recommendation="cwd is on the denied path list.",
                        extras={"matched_pattern": pattern},
                    )
                ]
        return []

    def _check_timeout(
        self,
        request: SafetyScanRequest,
        policy: ToolSafetyPolicy,
        redactor: Redactor,
    ) -> list[SafetyFinding]:
        if request.requested_timeout_seconds is None:
            return []
        limit = policy.limits.max_timeout_seconds
        if request.requested_timeout_seconds > limit:
            decision = resolve_decision(_TIMEOUT_RULE_ID, SafetyDecision.DENY, policy)
            return [
                _finding(
                    rule_id=_TIMEOUT_RULE_ID,
                    category=RiskCategory.RESOURCE,
                    risk=RiskLevel.MEDIUM,
                    decision=decision,
                    snippet=f"timeout={request.requested_timeout_seconds}s",
                    language=ScriptLanguage.UNKNOWN,
                    redactor=redactor,
                    recommendation="Requested timeout exceeds policy limit.",
                    extras={"limit_seconds": str(limit)},
                )
            ]
        return []

    def _check_argv(
        self,
        request: SafetyScanRequest,
        policy: ToolSafetyPolicy,
        redactor: Redactor,
    ) -> list[SafetyFinding]:
        if not request.argv:
            return []
        findings: list[SafetyFinding] = []
        deny = policy.commands.deny
        allow = policy.commands.allow
        for idx, arg in enumerate(request.argv):
            token = arg.strip().split()[0].lower() if arg.strip() else ""
            if not token:
                continue
            if token in deny:
                decision = resolve_decision(_ARGV_RULE_ID, SafetyDecision.DENY, policy)
                findings.append(
                    _finding(
                        rule_id=_ARGV_RULE_ID,
                        category=RiskCategory.PROCESS,
                        risk=RiskLevel.HIGH,
                        decision=decision,
                        snippet=f"argv[{idx}]={token}",
                        language=ScriptLanguage.UNKNOWN,
                        redactor=redactor,
                        recommendation="argv references a denied executable.",
                        extras={
                            "index": str(idx),
                            "executable": token
                        },
                    ))
                continue
            if allow and token not in allow and _looks_like_executable(arg):
                decision = resolve_decision(
                    _ARGV_RULE_ID,
                    _default_unknown(policy),
                    policy,
                )
                if decision == SafetyDecision.ALLOW:
                    continue
                findings.append(
                    _finding(
                        rule_id=_ARGV_RULE_ID,
                        category=RiskCategory.PROCESS,
                        risk=RiskLevel.LOW,
                        decision=decision,
                        snippet=f"argv[{idx}]={token}",
                        language=ScriptLanguage.UNKNOWN,
                        redactor=redactor,
                        recommendation="argv contains an executable not on the allow list.",
                        extras={
                            "index": str(idx),
                            "executable": token
                        },
                    ))
        return findings

    def _check_tool_mapping(
        self,
        request: SafetyScanRequest,
        policy: ToolSafetyPolicy,
        redactor: Redactor,
    ) -> list[SafetyFinding]:
        flag = request.metadata.get("execution_capable")
        is_exec_capable = flag in (True, "true", "True", 1)
        if not is_exec_capable:
            return []
        adapter_id = request.metadata.get("adapter_id")
        if adapter_id and adapter_id in policy.tools:
            return []
        # Built-in adapters are vetted by the tool_adapter module; the
        # cross-field check only fires for unknown execution-capable tools.
        from trpc_agent_sdk.tools.safety._tool_adapter import _BUILTIN_DEFAULTS
        if adapter_id and adapter_id in _BUILTIN_DEFAULTS:
            return []
        decision = resolve_decision(
            _TOOL_MAPPING_RULE_ID,
            _default_unknown(policy),
            policy,
        )
        if decision == SafetyDecision.ALLOW:
            return []
        return [
            _finding(
                rule_id=_TOOL_MAPPING_RULE_ID,
                category=RiskCategory.ANALYSIS,
                risk=RiskLevel.MEDIUM,
                decision=decision,
                snippet="tool marked execution_capable without adapter mapping",
                language=ScriptLanguage.UNKNOWN,
                redactor=redactor,
                recommendation="Declare a tool adapter mapping in policy.tools or disable execution.",
                extras={
                    "tool_name": request.tool_name,
                    "tool_kind": request.tool_kind.value
                },
            )
        ]

    def _check_output_budget(
        self,
        request: SafetyScanRequest,
        policy: ToolSafetyPolicy,
        redactor: Redactor,
    ) -> list[SafetyFinding]:
        if request.requested_output_bytes is None:
            return []
        limit = policy.limits.max_output_bytes
        if request.requested_output_bytes > limit:
            decision = resolve_decision("RES005_LARGE_WRITE", SafetyDecision.DENY, policy)
            return [
                _finding(
                    rule_id="RES005_LARGE_WRITE",
                    category=RiskCategory.RESOURCE,
                    risk=RiskLevel.MEDIUM,
                    decision=decision,
                    snippet=f"requested_output={request.requested_output_bytes}",
                    language=ScriptLanguage.UNKNOWN,
                    redactor=redactor,
                    recommendation="Requested output exceeds policy max_output_bytes.",
                    extras={"limit_bytes": str(limit)},
                )
            ]
        return []


def _looks_like_executable(text: str) -> bool:
    """Heuristic: looks like a command name (not a path or option)."""

    if not text:
        return False
    if text.startswith("-"):
        return False
    if "/" in text or "\\" in text:
        return False
    return all(ch.isalnum() or ch in "_-." for ch in text)
