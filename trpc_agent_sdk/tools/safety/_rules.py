"""SafetyRule protocol, default rule catalog, and registry.

Rules are deliberately grouped into three substantial rule objects
(``PythonScannerRule``, ``BashScannerRule``, ``CrossFieldScannerRule``)
so each scanner parses its input once and the rule catalog consumes the
resulting :class:`ScriptFacts` without re-walking the source.

Per-rule action overrides come from the policy (``rule_overrides``) and
are applied at finding construction time so the catalog stays declarative.
"""

from __future__ import annotations

import ipaddress
from typing import Iterable, Protocol, runtime_checkable

from trpc_agent_sdk.tools.safety._facts import (
    ConcurrencyFact,
    ScriptFacts,
)
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
    match_domain,
    match_path_glob,
)
from trpc_agent_sdk.tools.safety._redaction import Redactor


@runtime_checkable
class SafetyRule(Protocol):
    """A rule consumes a request and policy and emits findings.

    Rules must not perform file I/O, network access, or process creation.
    They are pure functions over their inputs.
    """

    rule_id: str

    def scan(
        self,
        request: SafetyScanRequest,
        policy: ToolSafetyPolicy,
    ) -> Iterable[SafetyFinding]:
        ...


# --------------------------------------------------------------------------- #
# Decision override helper
# --------------------------------------------------------------------------- #


def resolve_decision(
    rule_id: str,
    proposed: SafetyDecision,
    policy: ToolSafetyPolicy,
) -> SafetyDecision:
    """Apply ``rule_overrides`` and the unknown-construct default.

    ``rule_overrides`` wins when set; otherwise the proposed decision is
    returned unchanged.
    """

    override = policy.rule_overrides.get(rule_id)
    if override == "allow":
        return SafetyDecision.ALLOW
    if override == "needs_human_review":
        return SafetyDecision.NEEDS_HUMAN_REVIEW
    if override == "deny":
        return SafetyDecision.DENY
    return proposed


def _default_unknown(policy: ToolSafetyPolicy) -> SafetyDecision:
    mapping = {
        "allow": SafetyDecision.ALLOW,
        "needs_human_review": SafetyDecision.NEEDS_HUMAN_REVIEW,
        "deny": SafetyDecision.DENY,
    }
    return mapping.get(policy.defaults.unknown_construct, SafetyDecision.NEEDS_HUMAN_REVIEW)


# --------------------------------------------------------------------------- #
# Catalog: one checker per rule id
# --------------------------------------------------------------------------- #


def _finding(
    *,
    rule_id: str,
    category: RiskCategory,
    risk: RiskLevel,
    decision: SafetyDecision,
    snippet: str,
    line: int = 0,
    column: int = 0,
    language: ScriptLanguage,
    redactor: Redactor,
    recommendation: str,
    extras: dict[str, str] | None = None,
) -> SafetyFinding:
    evidence = redactor.build_evidence(
        snippet=snippet,
        line=line,
        column=column,
        language=language,
        extras=extras,
    )
    return SafetyFinding(
        rule_id=rule_id,
        category=category,
        risk_level=risk,
        decision=decision,
        evidence=evidence,
        recommendation=recommendation,
    )


def check_file_recursive_delete(
    facts: ScriptFacts,
    policy: ToolSafetyPolicy,
    language: ScriptLanguage,
    redactor: Redactor,
) -> list[SafetyFinding]:
    out: list[SafetyFinding] = []
    for fact in facts.file_deletes:
        if not fact.recursive:
            continue
        decision = resolve_decision("FILE001_RECURSIVE_DELETE", SafetyDecision.DENY, policy)
        out.append(
            _finding(
                rule_id="FILE001_RECURSIVE_DELETE",
                category=RiskCategory.FILE,
                risk=RiskLevel.CRITICAL,
                decision=decision,
                snippet=fact.snippet,
                line=fact.loc.line,
                column=fact.loc.column,
                language=language,
                redactor=redactor,
                recommendation="Avoid recursive delete; require explicit file list.",
                extras={
                    "target": fact.target,
                    "explicit": str(fact.explicit)
                },
            ))
    return out


def check_file_denied_write(
    facts: ScriptFacts,
    policy: ToolSafetyPolicy,
    language: ScriptLanguage,
    redactor: Redactor,
) -> list[SafetyFinding]:
    out: list[SafetyFinding] = []
    for fact in facts.file_writes:
        if not fact.explicit:
            # Dynamic target -- escalate to review, then check deny list.
            if not _matches_denied_path(fact.target, policy):
                decision = resolve_decision(
                    "FILE002_DENIED_WRITE",
                    _default_unknown(policy),
                    policy,
                )
                if decision != SafetyDecision.ALLOW:
                    out.append(
                        _finding(
                            rule_id="FILE002_DENIED_WRITE",
                            category=RiskCategory.FILE,
                            risk=RiskLevel.MEDIUM,
                            decision=decision,
                            snippet=fact.snippet,
                            line=fact.loc.line,
                            column=fact.loc.column,
                            language=language,
                            redactor=redactor,
                            recommendation="Static target could not be resolved; review the write path.",
                            extras={"target": "<dynamic>"},
                        ))
            continue
        if _matches_denied_path(fact.target, policy):
            decision = resolve_decision("FILE002_DENIED_WRITE", SafetyDecision.DENY, policy)
            out.append(
                _finding(
                    rule_id="FILE002_DENIED_WRITE",
                    category=RiskCategory.FILE,
                    risk=RiskLevel.HIGH,
                    decision=decision,
                    snippet=fact.snippet,
                    line=fact.loc.line,
                    column=fact.loc.column,
                    language=language,
                    redactor=redactor,
                    recommendation="Write target is on the denied path list.",
                    extras={"target": fact.target},
                ))
    return out


def check_file_credential_read(
    facts: ScriptFacts,
    policy: ToolSafetyPolicy,
    language: ScriptLanguage,
    redactor: Redactor,
) -> list[SafetyFinding]:
    out: list[SafetyFinding] = []
    for fact in facts.file_reads:
        if fact.kind != "credential":
            continue
        decision = resolve_decision("FILE003_CREDENTIAL_READ", SafetyDecision.DENY, policy)
        out.append(
            _finding(
                rule_id="FILE003_CREDENTIAL_READ",
                category=RiskCategory.FILE,
                risk=RiskLevel.CRITICAL,
                decision=decision,
                snippet=fact.snippet,
                line=fact.loc.line,
                column=fact.loc.column,
                language=language,
                redactor=redactor,
                recommendation="Reading credentials at runtime is forbidden.",
                extras={"target": fact.target},
            ))
    return out


def check_file_dotenv_read(
    facts: ScriptFacts,
    policy: ToolSafetyPolicy,
    language: ScriptLanguage,
    redactor: Redactor,
) -> list[SafetyFinding]:
    out: list[SafetyFinding] = []
    for fact in facts.file_reads:
        if fact.kind != "dotenv":
            continue
        decision = resolve_decision("FILE004_DOTENV_READ", SafetyDecision.DENY, policy)
        out.append(
            _finding(
                rule_id="FILE004_DOTENV_READ",
                category=RiskCategory.FILE,
                risk=RiskLevel.HIGH,
                decision=decision,
                snippet=fact.snippet,
                line=fact.loc.line,
                column=fact.loc.column,
                language=language,
                redactor=redactor,
                recommendation="Loading .env at runtime can leak secrets; inject via env.",
                extras={"target": fact.target},
            ))
    return out


def check_network_non_allowlist(
    facts: ScriptFacts,
    policy: ToolSafetyPolicy,
    language: ScriptLanguage,
    redactor: Redactor,
) -> list[SafetyFinding]:
    out: list[SafetyFinding] = []
    allow = policy.network.allow_domains
    for fact in facts.network_calls:
        if fact.dynamic or not fact.target:
            continue
        if match_domain(fact.target, allow):
            continue
        decision = resolve_decision("NET001_DOMAIN_NOT_ALLOWED", SafetyDecision.DENY, policy)
        out.append(
            _finding(
                rule_id="NET001_DOMAIN_NOT_ALLOWED",
                category=RiskCategory.NETWORK,
                risk=RiskLevel.HIGH,
                decision=decision,
                snippet=fact.snippet,
                line=fact.loc.line,
                column=fact.loc.column,
                language=language,
                redactor=redactor,
                recommendation="Host is not on the allow list; add it explicitly.",
                extras={
                    "host": fact.target,
                    "library": fact.library
                },
            ))
    return out


def check_network_ip_literals(
    facts: ScriptFacts,
    policy: ToolSafetyPolicy,
    language: ScriptLanguage,
    redactor: Redactor,
) -> list[SafetyFinding]:
    """Reject literal IP targets when the policy disables them.

    This remains separate from the domain allowlist rule: an operator may
    deliberately allow a loopback IP for a local test service, but still
    choose whether IP literals are globally acceptable.
    """

    if not policy.network.deny_ip_literals:
        return []
    out: list[SafetyFinding] = []
    for fact in facts.network_calls:
        if fact.dynamic or not _is_ip_literal(fact.target):
            continue
        decision = resolve_decision("NET003_IP_LITERAL", SafetyDecision.DENY, policy)
        out.append(
            _finding(
                rule_id="NET003_IP_LITERAL",
                category=RiskCategory.NETWORK,
                risk=RiskLevel.HIGH,
                decision=decision,
                snippet=fact.snippet,
                line=fact.loc.line,
                column=fact.loc.column,
                language=language,
                redactor=redactor,
                recommendation="Use an explicitly allowlisted DNS name instead of an IP literal.",
                extras={
                    "host": fact.target,
                    "library": fact.library
                },
            ))
    return out


def check_network_dynamic_target(
    facts: ScriptFacts,
    policy: ToolSafetyPolicy,
    language: ScriptLanguage,
    redactor: Redactor,
) -> list[SafetyFinding]:
    out: list[SafetyFinding] = []
    for fact in facts.network_calls:
        if not fact.dynamic:
            continue
        decision = resolve_decision(
            "NET002_DYNAMIC_TARGET",
            _default_unknown(policy),
            policy,
        )
        if decision == SafetyDecision.ALLOW:
            continue
        out.append(
            _finding(
                rule_id="NET002_DYNAMIC_TARGET",
                category=RiskCategory.NETWORK,
                risk=RiskLevel.MEDIUM,
                decision=decision,
                snippet=fact.snippet,
                line=fact.loc.line,
                column=fact.loc.column,
                language=language,
                redactor=redactor,
                recommendation="Network target is computed at runtime; review before executing.",
                extras={"library": fact.library},
            ))
    return out


def check_process_exec(
    facts: ScriptFacts,
    policy: ToolSafetyPolicy,
    language: ScriptLanguage,
    redactor: Redactor,
) -> list[SafetyFinding]:
    out: list[SafetyFinding] = []
    allow = policy.commands.allow
    deny = policy.commands.deny
    for fact in facts.process_calls:
        executable = _first_token(fact.command).lower()
        if executable in deny:
            decision = resolve_decision("PROC001_PROCESS_EXEC", SafetyDecision.DENY, policy)
            out.append(
                _finding(
                    rule_id="PROC001_PROCESS_EXEC",
                    category=RiskCategory.PROCESS,
                    risk=RiskLevel.HIGH,
                    decision=decision,
                    snippet=fact.snippet,
                    line=fact.loc.line,
                    column=fact.loc.column,
                    language=language,
                    redactor=redactor,
                    recommendation="Executable is on the deny list.",
                    extras={"executable": executable},
                ))
            continue
        # If shell=True, PROC002 handles it.
        if fact.shell is True:
            continue
        # If has operators, PROC003 handles it.
        if fact.has_operators:
            continue
        # Empty/dynamic executable: cannot statically resolve, emit review.
        if not executable:
            decision = resolve_decision(
                "PROC001_PROCESS_EXEC",
                _default_unknown(policy),
                policy,
            )
            if decision == SafetyDecision.ALLOW:
                continue
            out.append(
                _finding(
                    rule_id="PROC001_PROCESS_EXEC",
                    category=RiskCategory.PROCESS,
                    risk=RiskLevel.MEDIUM,
                    decision=decision,
                    snippet=fact.snippet,
                    line=fact.loc.line,
                    column=fact.loc.column,
                    language=language,
                    redactor=redactor,
                    recommendation="Process executable is computed at runtime; review before executing.",
                    extras={"executable": "<dynamic>"},
                ))
            continue
        # If the command is in user allow list, no finding.
        if allow and executable in allow:
            continue
        # Built-in safe commands are only a fallback when the operator did
        # not configure an explicit allow list.
        if not allow and executable in _SAFE_BASH_COMMANDS \
                and language == ScriptLanguage.BASH:
            continue
        # Allow list configured but command not in it -> review.
        if allow:
            decision = resolve_decision(
                "PROC001_PROCESS_EXEC",
                SafetyDecision.NEEDS_HUMAN_REVIEW,
                policy,
            )
            if decision == SafetyDecision.ALLOW:
                continue
            out.append(
                _finding(
                    rule_id="PROC001_PROCESS_EXEC",
                    category=RiskCategory.PROCESS,
                    risk=RiskLevel.LOW,
                    decision=decision,
                    snippet=fact.snippet,
                    line=fact.loc.line,
                    column=fact.loc.column,
                    language=language,
                    redactor=redactor,
                    recommendation="Executable is not on the configured allow list.",
                    extras={"executable": executable},
                ))
            continue
        # No allow list configured; trust the safe set + deny list above.
        # Other catalog rules still vet file/network/secret behavior.
    return out


def check_shell_injection(
    facts: ScriptFacts,
    policy: ToolSafetyPolicy,
    language: ScriptLanguage,
    redactor: Redactor,
) -> list[SafetyFinding]:
    out: list[SafetyFinding] = []
    for fact in facts.process_calls:
        if fact.shell is not True:
            continue
        decision = resolve_decision("PROC002_SHELL_INJECTION", SafetyDecision.DENY, policy)
        out.append(
            _finding(
                rule_id="PROC002_SHELL_INJECTION",
                category=RiskCategory.PROCESS,
                risk=RiskLevel.CRITICAL,
                decision=decision,
                snippet=fact.snippet,
                line=fact.loc.line,
                column=fact.loc.column,
                language=language,
                redactor=redactor,
                recommendation="Use argument lists (shell=False) to avoid shell injection.",
                extras={"executable": _first_token(fact.command) or "<dynamic>"},
            ))
    return out


def check_shell_operator(
    facts: ScriptFacts,
    policy: ToolSafetyPolicy,
    language: ScriptLanguage,
    redactor: Redactor,
) -> list[SafetyFinding]:
    out: list[SafetyFinding] = []
    for fact in facts.shell_operators:
        decision = resolve_decision(
            "PROC003_SHELL_OPERATOR",
            _default_unknown(policy),
            policy,
        )
        if decision == SafetyDecision.ALLOW:
            continue
        out.append(
            _finding(
                rule_id="PROC003_SHELL_OPERATOR",
                category=RiskCategory.PROCESS,
                risk=RiskLevel.MEDIUM,
                decision=decision,
                snippet=fact.snippet,
                line=fact.loc.line,
                column=fact.loc.column,
                language=language,
                redactor=redactor,
                recommendation="Shell operators require review; split into explicit steps.",
                extras={"operator": fact.operator},
            ))
    return out


def check_privilege_escalation(
    facts: ScriptFacts,
    policy: ToolSafetyPolicy,
    language: ScriptLanguage,
    redactor: Redactor,
) -> list[SafetyFinding]:
    out: list[SafetyFinding] = []
    for fact in facts.privilege_commands:
        decision = resolve_decision("PROC004_PRIVILEGE", SafetyDecision.DENY, policy)
        out.append(
            _finding(
                rule_id="PROC004_PRIVILEGE",
                category=RiskCategory.PROCESS,
                risk=RiskLevel.CRITICAL,
                decision=decision,
                snippet=fact.snippet,
                line=fact.loc.line,
                column=fact.loc.column,
                language=language,
                redactor=redactor,
                recommendation="Privilege escalation commands are forbidden.",
                extras={"command": fact.command},
            ))
    return out


def check_dependency_install(
    facts: ScriptFacts,
    policy: ToolSafetyPolicy,
    language: ScriptLanguage,
    redactor: Redactor,
) -> list[SafetyFinding]:
    out: list[SafetyFinding] = []
    desired = policy.dependencies.decision.lower()
    proposed = {
        "allow": SafetyDecision.ALLOW,
        "needs_human_review": SafetyDecision.NEEDS_HUMAN_REVIEW,
        "deny": SafetyDecision.DENY,
    }.get(desired, SafetyDecision.DENY)
    for fact in facts.dependency_installs:
        decision = resolve_decision("DEP001_ENV_MUTATION", proposed, policy)
        if decision == SafetyDecision.ALLOW:
            continue
        risk = RiskLevel.HIGH if decision == SafetyDecision.DENY else RiskLevel.MEDIUM
        out.append(
            _finding(
                rule_id="DEP001_ENV_MUTATION",
                category=RiskCategory.DEPENDENCY,
                risk=risk,
                decision=decision,
                snippet=fact.snippet,
                line=fact.loc.line,
                column=fact.loc.column,
                language=language,
                redactor=redactor,
                recommendation="Dependency installation mutates the runtime image.",
                extras={
                    "manager": fact.manager,
                    "command": fact.command
                },
            ))
    return out


def check_unbounded_loop(
    facts: ScriptFacts,
    policy: ToolSafetyPolicy,
    language: ScriptLanguage,
    redactor: Redactor,
) -> list[SafetyFinding]:
    out: list[SafetyFinding] = []
    for fact in facts.unbounded_loops:
        decision = resolve_decision("RES001_UNBOUNDED_LOOP", SafetyDecision.DENY, policy)
        out.append(
            _finding(
                rule_id="RES001_UNBOUNDED_LOOP",
                category=RiskCategory.RESOURCE,
                risk=RiskLevel.HIGH,
                decision=decision,
                snippet=fact.snippet,
                line=fact.loc.line,
                column=fact.loc.column,
                language=language,
                redactor=redactor,
                recommendation="Loop has no static exit condition; rewrite with a bounded range.",
                extras={"kind": fact.kind},
            ))
    return out


def check_fork_bomb(
    facts: ScriptFacts,
    policy: ToolSafetyPolicy,
    language: ScriptLanguage,
    redactor: Redactor,
) -> list[SafetyFinding]:
    out: list[SafetyFinding] = []
    for fact in facts.fork_bombs:
        decision = resolve_decision("RES002_FORK_BOMB", SafetyDecision.DENY, policy)
        out.append(
            _finding(
                rule_id="RES002_FORK_BOMB",
                category=RiskCategory.RESOURCE,
                risk=RiskLevel.CRITICAL,
                decision=decision,
                snippet=fact.snippet,
                line=fact.loc.line,
                column=fact.loc.column,
                language=language,
                redactor=redactor,
                recommendation="Fork-bomb pattern detected.",
                extras={"pattern": fact.pattern},
            ))
    return out


def check_long_sleep(
    facts: ScriptFacts,
    policy: ToolSafetyPolicy,
    language: ScriptLanguage,
    redactor: Redactor,
) -> list[SafetyFinding]:
    out: list[SafetyFinding] = []
    threshold = policy.limits.max_sleep_seconds
    for fact in facts.long_sleeps:
        if fact.duration_seconds is None:
            decision = resolve_decision(
                "RES003_LONG_SLEEP",
                _default_unknown(policy),
                policy,
            )
            risk = RiskLevel.LOW
        elif fact.duration_seconds > threshold:
            decision = resolve_decision("RES003_LONG_SLEEP", SafetyDecision.DENY, policy)
            risk = RiskLevel.MEDIUM
        else:
            continue
        if decision == SafetyDecision.ALLOW:
            continue
        out.append(
            _finding(
                rule_id="RES003_LONG_SLEEP",
                category=RiskCategory.RESOURCE,
                risk=risk,
                decision=decision,
                snippet=fact.snippet,
                line=fact.loc.line,
                column=fact.loc.column,
                language=language,
                redactor=redactor,
                recommendation="Sleep exceeds or hides its duration; verify it is bounded.",
                extras={
                    "duration_seconds": str(fact.duration_seconds),
                    "raw": fact.raw,
                    "limit_seconds": str(threshold)
                },
            ))
    return out


def check_concurrency(
    facts: ScriptFacts,
    policy: ToolSafetyPolicy,
    language: ScriptLanguage,
    redactor: Redactor,
) -> list[SafetyFinding]:
    out: list[SafetyFinding] = []
    for fact in facts.concurrency:
        threshold = _concurrency_limit_for(fact, policy)
        if fact.count is None:
            decision = resolve_decision(
                "RES004_CONCURRENCY",
                _default_unknown(policy),
                policy,
            )
            risk = RiskLevel.LOW
        elif fact.count > threshold:
            decision = resolve_decision("RES004_CONCURRENCY", SafetyDecision.DENY, policy)
            risk = RiskLevel.MEDIUM
        else:
            continue
        if decision == SafetyDecision.ALLOW:
            continue
        out.append(
            _finding(
                rule_id="RES004_CONCURRENCY",
                category=RiskCategory.RESOURCE,
                risk=risk,
                decision=decision,
                snippet=fact.snippet,
                line=fact.loc.line,
                column=fact.loc.column,
                language=language,
                redactor=redactor,
                recommendation="Concurrency exceeds policy; reduce fan-out.",
                extras={
                    "count": str(fact.count),
                    "raw": fact.raw,
                    "limit": str(threshold)
                },
            ))
    return out


def check_large_write(
    facts: ScriptFacts,
    policy: ToolSafetyPolicy,
    language: ScriptLanguage,
    redactor: Redactor,
) -> list[SafetyFinding]:
    out: list[SafetyFinding] = []
    threshold = policy.limits.max_file_write_bytes
    for fact in facts.large_writes:
        if fact.size is None:
            decision = resolve_decision(
                "RES005_LARGE_WRITE",
                _default_unknown(policy),
                policy,
            )
            risk = RiskLevel.LOW
        elif fact.size > threshold:
            decision = resolve_decision("RES005_LARGE_WRITE", SafetyDecision.DENY, policy)
            risk = RiskLevel.MEDIUM
        else:
            continue
        if decision == SafetyDecision.ALLOW:
            continue
        out.append(
            _finding(
                rule_id="RES005_LARGE_WRITE",
                category=RiskCategory.RESOURCE,
                risk=risk,
                decision=decision,
                snippet=fact.snippet,
                line=fact.loc.line,
                column=fact.loc.column,
                language=language,
                redactor=redactor,
                recommendation="File write exceeds size budget or hides its size.",
                extras={
                    "size_bytes": str(fact.size),
                    "target": fact.target,
                    "limit_bytes": str(threshold)
                },
            ))
    return out


def check_secret_to_output(
    facts: ScriptFacts,
    policy: ToolSafetyPolicy,
    language: ScriptLanguage,
    redactor: Redactor,
) -> list[SafetyFinding]:
    out: list[SafetyFinding] = []
    for fact in facts.secret_flows:
        if fact.sink_kind != "output":
            continue
        decision = resolve_decision("SECRET001_LOG_SINK", SafetyDecision.DENY, policy)
        out.append(
            _finding(
                rule_id="SECRET001_LOG_SINK",
                category=RiskCategory.SECRET,
                risk=RiskLevel.HIGH,
                decision=decision,
                snippet=fact.snippet,
                line=fact.loc.line,
                column=fact.loc.column,
                language=language,
                redactor=redactor,
                recommendation="Secret-looking value flows into a log/print sink.",
                extras={
                    "source": fact.source,
                    "sink": fact.sink
                },
            ))
    return out


def check_secret_to_file(
    facts: ScriptFacts,
    policy: ToolSafetyPolicy,
    language: ScriptLanguage,
    redactor: Redactor,
) -> list[SafetyFinding]:
    out: list[SafetyFinding] = []
    for fact in facts.secret_flows:
        if fact.sink_kind != "file":
            continue
        decision = resolve_decision("SECRET002_FILE_SINK", SafetyDecision.DENY, policy)
        out.append(
            _finding(
                rule_id="SECRET002_FILE_SINK",
                category=RiskCategory.SECRET,
                risk=RiskLevel.HIGH,
                decision=decision,
                snippet=fact.snippet,
                line=fact.loc.line,
                column=fact.loc.column,
                language=language,
                redactor=redactor,
                recommendation="Secret-looking value flows into a file sink.",
                extras={
                    "source": fact.source,
                    "sink": fact.sink
                },
            ))
    return out


def check_secret_to_network(
    facts: ScriptFacts,
    policy: ToolSafetyPolicy,
    language: ScriptLanguage,
    redactor: Redactor,
) -> list[SafetyFinding]:
    out: list[SafetyFinding] = []
    for fact in facts.secret_flows:
        if fact.sink_kind != "network":
            continue
        decision = resolve_decision("SECRET003_NETWORK_SINK", SafetyDecision.DENY, policy)
        out.append(
            _finding(
                rule_id="SECRET003_NETWORK_SINK",
                category=RiskCategory.SECRET,
                risk=RiskLevel.HIGH,
                decision=decision,
                snippet=fact.snippet,
                line=fact.loc.line,
                column=fact.loc.column,
                language=language,
                redactor=redactor,
                recommendation="Secret-looking value flows into a network sink.",
                extras={
                    "source": fact.source,
                    "sink": fact.sink
                },
            ))
    return out


def check_parse_error(
    facts: ScriptFacts,
    policy: ToolSafetyPolicy,
    language: ScriptLanguage,
    redactor: Redactor,
) -> list[SafetyFinding]:
    out: list[SafetyFinding] = []
    for fact in facts.parse_errors:
        decision = resolve_decision(
            "PARSE001_UNCERTAIN",
            _default_unknown(policy),
            policy,
        )
        if decision == SafetyDecision.ALLOW:
            continue
        out.append(
            _finding(
                rule_id="PARSE001_UNCERTAIN",
                category=RiskCategory.ANALYSIS,
                risk=RiskLevel.MEDIUM,
                decision=decision,
                snippet=fact.snippet or fact.message,
                line=fact.loc.line,
                column=fact.loc.column,
                language=language,
                redactor=redactor,
                recommendation="Could not parse the script; treat as untrusted.",
                extras={"message": fact.message},
            ))
    return out


def check_dynamic_exec(
    facts: ScriptFacts,
    policy: ToolSafetyPolicy,
    language: ScriptLanguage,
    redactor: Redactor,
) -> list[SafetyFinding]:
    out: list[SafetyFinding] = []
    for fact in facts.dynamic_execs:
        decision = resolve_decision(
            "OBF001_DYNAMIC_EXEC",
            _default_unknown(policy),
            policy,
        )
        if decision == SafetyDecision.ALLOW:
            continue
        out.append(
            _finding(
                rule_id="OBF001_DYNAMIC_EXEC",
                category=RiskCategory.ANALYSIS,
                risk=RiskLevel.HIGH,
                decision=decision,
                snippet=fact.snippet,
                line=fact.loc.line,
                column=fact.loc.column,
                language=language,
                redactor=redactor,
                recommendation="Dynamic code execution hides intent; replace with a static call.",
                extras={"kind": fact.kind},
            ))
    return out


CATALOG = (
    check_file_recursive_delete,
    check_file_denied_write,
    check_file_credential_read,
    check_file_dotenv_read,
    check_network_non_allowlist,
    check_network_ip_literals,
    check_network_dynamic_target,
    check_process_exec,
    check_shell_injection,
    check_shell_operator,
    check_privilege_escalation,
    check_dependency_install,
    check_unbounded_loop,
    check_fork_bomb,
    check_long_sleep,
    check_concurrency,
    check_large_write,
    check_secret_to_output,
    check_secret_to_file,
    check_secret_to_network,
    check_parse_error,
    check_dynamic_exec,
)


def evaluate_facts(
    facts: ScriptFacts,
    policy: ToolSafetyPolicy,
    language: ScriptLanguage,
    redactor: Redactor,
) -> list[SafetyFinding]:
    """Run every catalog checker and return the combined findings."""

    out: list[SafetyFinding] = []
    for checker in CATALOG:
        out.extend(checker(facts, policy, language, redactor))
    return out


# --------------------------------------------------------------------------- #
# Rule objects
# --------------------------------------------------------------------------- #


class _LanguageScannerRule:
    """Shared base for Python/Bash rules.

    Subclasses set :attr:`language` and :attr:`_extract` so the rule
    delegates parsing to the right scanner once, then runs the catalog.
    """

    rule_id = "language_scanner"

    def __init__(self, language: ScriptLanguage) -> None:
        self.language = language

    def _extract(self, request: SafetyScanRequest) -> ScriptFacts:
        raise NotImplementedError

    def scan(
        self,
        request: SafetyScanRequest,
        policy: ToolSafetyPolicy,
    ) -> Iterable[SafetyFinding]:
        if request.language != self.language:
            return []
        if not request.script:
            return []
        facts = self._extract(request)
        redactor = Redactor(env_values=request.env.values())
        return evaluate_facts(facts, policy, self.language, redactor)


def _matches_denied_path(target: str, policy: ToolSafetyPolicy) -> bool:
    if not target:
        return False
    from trpc_agent_sdk.tools.safety._policy import normalize_script_path_for_match

    normalized = normalize_script_path_for_match(target)
    for pattern in policy.paths.deny:
        if match_path_glob(normalized, pattern) or \
                match_path_glob(target, pattern):
            return True
    # Path basename matching for relative references like ".env".
    name = normalized.rsplit("/", 1)[-1]
    if name == ".env":
        return True
    for pattern in policy.paths.deny:
        if match_path_glob(name, pattern.rsplit("/", 1)[-1]):
            return True
    return False


def _first_token(command: str) -> str:
    if not command:
        return ""
    return command.strip().split()[0] if command.strip() else ""


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return True


def _concurrency_limit_for(
    fact: ConcurrencyFact,
    policy: ToolSafetyPolicy,
) -> int:
    process_primitives = {
        "multiprocessing.Process",
        "multiprocessing.Pool",
        "concurrent.futures.ProcessPoolExecutor",
        "background-jobs",
    }
    if fact.raw in process_primitives:
        return policy.limits.max_processes
    return policy.limits.max_parallel_tasks


# Read-only / informational Bash commands that are exempt from PROC001.
# Other rules (file read of credentials, secret flow, network checks)
# still apply when these commands touch sensitive targets.
_SAFE_BASH_COMMANDS: frozenset[str] = frozenset({
    "echo",
    "printf",
    "ls",
    "ll",
    "pwd",
    "cd",
    "cat",
    "head",
    "tail",
    "less",
    "more",
    "view",
    "grep",
    "egrep",
    "fgrep",
    "rg",
    "ack",
    "wc",
    "sort",
    "uniq",
    "tr",
    "cut",
    "paste",
    "expand",
    "unexpand",
    "date",
    "cal",
    "uptime",
    "whoami",
    "id",
    "groups",
    "hostname",
    "uname",
    "env",
    "printenv",
    "set",
    "true",
    "false",
    "test",
    "[",
    "basename",
    "dirname",
    "realpath",
    "readlink",
    "which",
    "whereis",
    "file",
    "stat",
    "ldd",
    "du",
    "df",
    "free",
    "vmstat",
    "seq",
    "yes",
    "tee",  # tee writes files but FILE002 catches sensitive targets
    "xargs",  # may invoke subcommands; those are recorded as separate facts
    "find",  # find -delete handled separately
    "ps",
    "lsof",
    "netstat",
    "ss",
    "ip",
    "ifconfig",
    "arp",
    "man",
    "help",
    "type",
    "command",
    "hash",
    "getopts",
    "getopt",
    "sleep",  # RES003 catches long sleeps
    "clear",
    "reset",
    "history",
    "alias",
    "unalias",
    "set",
    "unset",
    "export",
    "shift",
    "shopt",
    "source",
    ".",
    "python",
    "python3",
    "python2",
    "node",
    "ruby",
    "perl",
    "git",
    "go",
    "cargo",
    "rustc",
    "gcc",
    "clang",
    "curl",
    "wget",  # NET001/NET002 vet targets
    "ssh",
    "scp",
    "sftp",  # treated as network calls
    "tar",
    "zip",
    "unzip",
    "gzip",
    "gunzip",
    "bzip2",
    "xz",
})


def default_rules() -> list[SafetyRule]:
    """Return the default rule set.

    Rules are constructed lazily so importing this module is cheap. The
    Python and Bash scanners are wired here; custom rules can be appended
    or used as a replacement set.
    """

    from trpc_agent_sdk.tools.safety._python_scanner import PythonScannerRule
    from trpc_agent_sdk.tools.safety._bash_scanner import BashScannerRule
    from trpc_agent_sdk.tools.safety._cross_field_scanner import CrossFieldScannerRule

    return [
        PythonScannerRule(),
        BashScannerRule(),
        CrossFieldScannerRule(),
    ]
