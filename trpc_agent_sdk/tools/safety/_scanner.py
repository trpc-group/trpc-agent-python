# Tencent is pleased to support the open source community by making
# tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Core safety scanning engine. Mirrors trpc-agent-go/tool/safety/safety.go."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from ._types import (
    Decision,
    RiskLevel,
    Finding,
    Report,
    Request,
    Policy,
    finding_beats,
    DECISION_ALLOW,
    DECISION_DENY,
    DECISION_ASK,
    DECISION_NEEDS_HUMAN_REVIEW,
    RISK_LOW,
    RISK_MEDIUM,
    RISK_HIGH,
    RISK_CRITICAL,
)
from ._policy import default_policy
from ._redactor import Redactor
from ._shell_parse import (
    command_name,
    has_pipeline,
    extract_urls,
    extract_host,
    has_shell_bypass,
)

if TYPE_CHECKING:
    from ._types import CodeBlock


def scan(request: Request, policy: Policy | None = None) -> Report:
    """Evaluate a pending tool execution against the safety policy.

    Args:
        request: The tool execution request to check.
        policy: Safety policy. If None, default_policy() is used.

    Returns:
        A Report with decision, findings, audit info, and redacted fields.
    """
    if policy is None:
        policy = default_policy()

    start = time.time()
    redactor = Redactor()

    cmd = _request_command(request)
    findings: list[Finding] = []

    # 1. Scan envelope.
    findings.extend(_scan_envelope(request, policy))

    # 2. Scan env vars.
    findings.extend(_scan_env(request, policy))

    # 3. Scan shell command or code blocks.
    if request.code_blocks:
        for block in request.code_blocks:
            findings.extend(_scan_code_block(request, block, policy))
    elif cmd.strip():
        findings.extend(_scan_shell(request, cmd, policy))
    else:
        findings.append(
            _new_finding(
                DECISION_DENY,
                RISK_HIGH,
                "command.empty",
                ["command is empty"],
                "Provide an explicit command before invoking the tool.",
            ))

    # Build report from worst finding.
    report = _report_from_findings(request, cmd, findings, redactor)

    # Redact secrets throughout.
    report.command = redactor.redact(report.command)
    report.evidence = [_redact_list(redactor, report.evidence)]
    report.recommendation = redactor.redact(report.recommendation)
    report.safe_summary = redactor.redact(report.safe_summary)
    for f in report.findings:
        f.evidence = _redact_list(redactor, f.evidence)
        f.recommendation = redactor.redact(f.recommendation)
    report.redacted = redactor.changed

    report.duration_ms = int((time.time() - start) * 1000)
    return report


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _request_command(req: Request) -> str:
    parts = [req.command.strip()] if req.command.strip() else []
    parts.extend(req.args)
    return " ".join(parts)


def _new_finding(
    decision: Decision,
    risk: RiskLevel,
    rule_id: str,
    evidence: list[str],
    recommendation: str,
) -> Finding:
    return Finding(decision, risk, rule_id, evidence, recommendation)


def _redact_list(r: Redactor, items: list[str]) -> list[str]:
    return [r.redact(s) for s in items] if items else []


def _finding_beats(a: Finding, b: Finding) -> bool:
    return finding_beats(a, b)


# ---------------------------------------------------------------------------
# Envelope scan
# ---------------------------------------------------------------------------


def _scan_envelope(req: Request, policy: Policy) -> list[Finding]:
    findings: list[Finding] = []
    if _path_denied(req.cwd, policy):
        findings.append(
            _new_finding(
                DECISION_DENY,
                RISK_CRITICAL,
                "sensitive.cwd_access",
                [f"working directory {req.cwd!r} is denied"],
                "Choose a workspace-relative working directory.",
            ))
    if req.backend == "hostexec" and (req.background or req.tty):
        findings.append(
            _new_finding(
                DECISION_NEEDS_HUMAN_REVIEW,
                RISK_HIGH,
                "hostexec.long_session",
                ["hostexec requested background or PTY execution"],
                "Require human approval for host PTY/background sessions.",
            ))
    if req.background:
        findings.append(
            _new_finding(
                DECISION_NEEDS_HUMAN_REVIEW,
                RISK_MEDIUM,
                "process.background",
                ["command may leave a background process behind"],
                "Run foreground commands with a bounded timeout.",
            ))
    if req.timeout_seconds > policy.max_timeout_seconds:
        findings.append(
            _new_finding(
                DECISION_DENY,
                RISK_HIGH,
                "resource.timeout_exceeded",
                [f"timeout {req.timeout_seconds}s exceeds policy max {policy.max_timeout_seconds}s"],
                "Use a shorter timeout.",
            ))
    if policy.max_output_bytes and req.max_output_bytes > policy.max_output_bytes:
        findings.append(
            _new_finding(
                DECISION_DENY,
                RISK_HIGH,
                "resource.output_limit_exceeded",
                [f"output cap {req.max_output_bytes} exceeds policy max {policy.max_output_bytes}"],
                "Lower the output cap.",
            ))
    return findings


# ---------------------------------------------------------------------------
# Env scan
# ---------------------------------------------------------------------------


def _scan_env(req: Request, policy: Policy) -> list[Finding]:
    findings: list[Finding] = []
    for k, v in req.env.items():
        if policy.env_allowlist and k.upper() not in {e.upper() for e in policy.env_allowlist}:
            findings.append(
                _new_finding(
                    DECISION_NEEDS_HUMAN_REVIEW,
                    RISK_MEDIUM,
                    "environment.non_whitelisted_variable",
                    [f"environment variable {k!r} is not allowlisted"],
                    "Only pass allowlisted env vars.",
                ))
        # Check for secrets in env values.
        if _looks_sensitive(k + "=" + v):
            findings.append(
                _new_finding(
                    DECISION_DENY,
                    RISK_CRITICAL,
                    "sensitive.secret_leak",
                    ["environment contains a likely secret"],
                    "Remove API keys/tokens from environment variables.",
                ))
    return findings


# ---------------------------------------------------------------------------
# Shell scan
# ---------------------------------------------------------------------------


def _scan_shell(req: Request, command: str, policy: Policy) -> list[Finding]:
    findings: list[Finding] = []

    # Raw text scan (before parsing).
    findings.extend(_scan_raw_command(command, policy))

    # Per-command scan.
    argv = command.strip().split()
    if argv:
        name = command_name(argv[0])
        if policy.denied_commands and name in {command_name(d) for d in policy.denied_commands}:
            findings.append(
                _new_finding(
                    DECISION_DENY,
                    RISK_HIGH,
                    "policy.denied_command",
                    [f"command {name!r} is denied"],
                    "Remove the denied command or update the policy.",
                ))

        findings.extend(_scan_dangerous_command(name, argv))
        findings.extend(_scan_review_command(command, policy))
        findings.extend(_scan_denied_paths(argv, policy))

    return findings


def _scan_raw_command(command: str, policy: Policy) -> list[Finding]:
    findings: list[Finding] = []
    lower = command.lower()

    # Secret detection.
    if _looks_sensitive(command):
        findings.append(
            _new_finding(
                DECISION_DENY,
                RISK_CRITICAL,
                "sensitive.secret_leak",
                ["command contains a likely secret"],
                "Remove API keys, tokens, and credentials from commands.",
            ))

    # Shell bypass.
    if has_shell_bypass(lower):
        findings.append(
            _new_finding(
                DECISION_DENY,
                RISK_HIGH,
                "shell.bypass",
                ["command contains shell bypass syntax or wrapper"],
                "Avoid sh -c, bash -c, eval, backticks, $(), and redirections.",
            ))

    # Pipeline review.
    if policy.review_shell_pipelines and has_pipeline(command):
        findings.append(
            _new_finding(
                DECISION_NEEDS_HUMAN_REVIEW,
                RISK_MEDIUM,
                "shell.pipeline_review",
                ["command contains a shell pipeline"],
                "Review multi-stage shell commands manually.",
            ))

    # Background detection.
    if " &" in lower or lower.endswith("&"):
        findings.append(
            _new_finding(
                DECISION_NEEDS_HUMAN_REVIEW,
                RISK_MEDIUM,
                "process.background",
                ["command may leave a background process"],
                "Run foreground commands with bounded timeout.",
            ))

    # Network scan.
    findings.extend(_scan_network(command, policy))

    # Resource scan.
    findings.extend(_scan_resource_patterns(lower, policy))

    return findings


def _scan_dangerous_command(name: str, argv: list[str]) -> list[Finding]:
    if name == "rm" and _destructive_rm(argv):
        return [
            _new_finding(
                DECISION_DENY,
                RISK_CRITICAL,
                "dangerous.rm_rf",
                [" ".join(argv)],
                "Do not run recursive forced deletion through tool execution.",
            )
        ]
    if name == "chmod" and "-R" in argv:
        return [
            _new_finding(
                DECISION_NEEDS_HUMAN_REVIEW,
                RISK_HIGH,
                "dangerous.recursive_chmod",
                [" ".join(argv)],
                "Review recursive permission changes before executing.",
            )
        ]
    return []


def _destructive_rm(argv: list[str]) -> bool:
    recursive = any("-r" in a.lower() or "-rf" in a.lower() or "--recursive" in a.lower() for a in argv[1:]
                    if a.startswith("-"))
    return recursive


def _scan_review_command(command: str, policy: Policy) -> list[Finding]:
    lower = command.strip().lower()
    for review in policy.review_commands:
        if lower.startswith(review.lower().strip()):
            return [
                _new_finding(
                    DECISION_NEEDS_HUMAN_REVIEW,
                    RISK_MEDIUM,
                    "dependency.environment_change",
                    [f"command starts with {review!r}"],
                    "Dependency installation should be reviewed and pinned.",
                )
            ]
    return []


def _scan_denied_paths(argv: list[str], policy: Policy) -> list[Finding]:
    findings: list[Finding] = []
    for arg in argv[1:]:
        clean = arg.strip("\"'")
        if _path_denied(clean, policy):
            findings.append(
                _new_finding(
                    DECISION_DENY,
                    RISK_CRITICAL,
                    "sensitive.path_access",
                    [f"argument references denied path {clean!r}"],
                    "Do not access SSH keys, .env files, or system directories.",
                ))
    return findings


def _path_denied(path: str, policy: Policy) -> bool:
    if not path:
        return False
    normalized = _normalize_path(path)
    for denied in policy.denied_paths:
        d = _normalize_path(denied)
        if not d:
            continue
        if normalized == d or normalized.startswith(d + "/") or ("/" + d + "/") in ("/" + normalized + "/"):
            return True
    return False


def _normalize_path(p: str) -> str:
    p = p.strip().strip("\"'")
    p = p.replace("\\", "/")
    p = p.removeprefix("~/").removeprefix("./")
    return p.lower().rstrip("/")


# ---------------------------------------------------------------------------
# Network scan
# ---------------------------------------------------------------------------


def _scan_network(command: str, policy: Policy) -> list[Finding]:
    findings: list[Finding] = []
    urls = extract_urls(command)
    for raw in urls:
        host = extract_host(raw)
        if host and not _host_allowed(host, policy):
            findings.append(
                _new_finding(
                    DECISION_DENY,
                    RISK_HIGH,
                    "network.non_whitelisted_domain",
                    [f"domain {host!r} is not in network_allowlist"],
                    "Use a whitelisted domain or update network_allowlist.",
                ))
    return findings


def _host_allowed(host: str, policy: Policy) -> bool:
    host = host.lower()
    for allowed in policy.network_allowlist:
        a = allowed.strip().lower()
        if host == a or host.endswith("." + a):
            return True
    return False


# ---------------------------------------------------------------------------
# Resource scan
# ---------------------------------------------------------------------------


def _scan_resource_patterns(lower: str, policy: Policy) -> list[Finding]:
    findings: list[Finding] = []
    if _long_sleep(lower):
        findings.append(
            _new_finding(
                DECISION_NEEDS_HUMAN_REVIEW,
                RISK_MEDIUM,
                "resource.long_sleep",
                ["command contains a long sleep"],
                "Use a shorter sleep or bounded wait condition.",
            ))
    if _infinite_loop(lower):
        findings.append(
            _new_finding(
                DECISION_DENY,
                RISK_HIGH,
                "resource.infinite_loop",
                ["command appears to contain an infinite loop"],
                "Replace unbounded loop with bounded command and timeout.",
            ))
    return findings


def _long_sleep(lower: str) -> bool:
    import re
    m = re.search(r"\bsleep\s+(\d+)", lower)
    if m:
        return int(m.group(1)) > 300
    return False


def _infinite_loop(lower: str) -> bool:
    patterns = ["while true", "while(1)", "for ;;", "for(;;)", "while 1"]
    return any(p in lower for p in patterns)


# ---------------------------------------------------------------------------
# Code block scan
# ---------------------------------------------------------------------------


def _scan_code_block(req: Request, block: CodeBlock, policy: Policy) -> list[Finding]:
    findings: list[Finding] = []
    lang = block.language.strip().lower()
    code = block.code.strip()
    if _looks_sensitive(code):
        findings.append(
            _new_finding(
                DECISION_DENY,
                RISK_CRITICAL,
                "sensitive.secret_leak",
                ["code block contains a likely secret"],
                "Remove secrets from code blocks.",
            ))
    if lang in ("bash", "sh", "shell", ""):
        # Treat as shell command.
        findings.extend(_scan_shell(req, code, policy))
    else:
        lower = code.lower()
        if any(kw in lower for kw in ("os.system", "subprocess.", "exec(")):
            findings.append(
                _new_finding(
                    DECISION_NEEDS_HUMAN_REVIEW,
                    RISK_MEDIUM,
                    "codeexec.host_command_bridge",
                    [f"{lang} code can launch shell commands"],
                    "Review code that bridges code execution into shell execution.",
                ))
        findings.extend(_scan_network(code, policy))
    return findings


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------


def _report_from_findings(
    req: Request,
    command: str,
    findings: list[Finding],
    redactor: Redactor,
) -> Report:
    best = Finding(DECISION_ALLOW, RISK_LOW, "", [], "Command matched no high-risk safety rules.")
    for f in findings:
        if finding_beats(f, best):
            best = f

    decision = best.decision
    blocked = decision in (DECISION_DENY, DECISION_ASK, DECISION_NEEDS_HUMAN_REVIEW)

    safe_summary = ""
    if decision == DECISION_ALLOW:
        if req.code_blocks:
            safe_summary = (f"{req.backend} scan allowed {len(req.code_blocks)} code block(s); "
                            f"no high-risk patterns matched.")
        else:
            safe_summary = (f"{req.backend} scan allowed command {command!r}; "
                            f"no high-risk patterns matched.")

    return Report(
        decision=decision,
        risk_level=best.risk_level,
        rule_id=best.rule_id,
        evidence=best.evidence,
        recommendation=best.recommendation,
        tool_name=req.tool_name,
        command=redactor.redact(command),
        backend=req.backend,
        blocked=blocked,
        safe_summary=safe_summary,
        findings=findings,
    )


def _looks_sensitive(text: str) -> bool:
    from ._redactor import _SECRET_VALUE_RE, _SECRET_NAME_RE
    if _SECRET_VALUE_RE.search(text):
        return True
    return bool(_SECRET_NAME_RE.search(text) and "=" in text)
