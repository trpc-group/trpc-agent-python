# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Rules shared by Python and shell payloads."""

from __future__ import annotations

from dataclasses import dataclass
import math
import ntpath
import posixpath
import re
from urllib.parse import urlparse

from ._models import RiskCategory
from ._models import RiskLevel
from ._models import SafetyDecision
from ._models import SafetyFinding
from ._models import ScriptScanRequest
from ._models import ToolSafetyPolicy
from ._sanitizer import SafetySanitizer

_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_PATH_TOKEN_RE = re.compile(r"(?:[A-Za-z]:)?(?:~(?:[A-Za-z0-9_-]+)?(?:[/\\][^\s\"'|;&,)]+)?|[./\\][^\s\"'|;&,)]+)"
                            r"|\.env(?:\.\w+)?")
_SENSITIVE_PATH_RE = re.compile(
    r"(?i)(?:^|[/\\])(?:\.env(?:\.[^/\\\s]+)?|id_rsa|id_ed25519|credentials(?:\.json)?)(?:$|[/\\\s\"'])")
_SECRET_REFERENCE_RE = re.compile(r"(?i)(?:\$[{]?(?:api[_-]?key|token|password|secret)|"
                                  r"\b(?:api[_-]?key|token|password|private[_-]?key)\b)")
_POSIX_SYSTEM_PATHS = ("/bin", "/boot", "/dev", "/etc", "/lib", "/proc", "/root", "/sbin", "/sys", "/usr", "/var")
_WINDOWS_SYSTEM_PATHS = ("c:/program files", "c:/programdata", "c:/windows")


@dataclass(frozen=True)
class RuleSpec:
    """Static metadata for a safety rule."""

    category: RiskCategory
    risk_level: RiskLevel
    decision: SafetyDecision
    recommendation: str


def make_finding(rule_id: str, evidence: object, spec: RuleSpec,
                 sanitizer: SafetySanitizer) -> tuple[SafetyFinding, bool]:
    """Create a finding with safe evidence."""
    safe_evidence, redacted = sanitizer.sanitize(evidence)
    return SafetyFinding(
        category=spec.category,
        risk_level=spec.risk_level,
        rule_id=rule_id,
        evidence=safe_evidence,
        recommendation=spec.recommendation,
        decision=spec.decision,
    ), redacted


FILE_DENY = RuleSpec(
    RiskCategory.FILE,
    RiskLevel.HIGH,
    SafetyDecision.DENY,
    "Remove access to sensitive or forbidden paths.",
)
FILE_REVIEW = RuleSpec(
    RiskCategory.FILE,
    RiskLevel.MEDIUM,
    SafetyDecision.NEEDS_HUMAN_REVIEW,
    "Resolve the execution home before accessing a tilde path.",
)
NETWORK_DENY = RuleSpec(
    RiskCategory.NETWORK,
    RiskLevel.HIGH,
    SafetyDecision.DENY,
    "Use a policy-allowed destination or disable network access.",
)
NETWORK_REVIEW = RuleSpec(
    RiskCategory.NETWORK,
    RiskLevel.MEDIUM,
    SafetyDecision.NEEDS_HUMAN_REVIEW,
    "Resolve and approve the dynamic network destination.",
)
SECRET_DENY = RuleSpec(
    RiskCategory.SECRET,
    RiskLevel.CRITICAL,
    SafetyDecision.DENY,
    "Remove sensitive data from output and external sinks.",
)
POLICY_REVIEW = RuleSpec(
    RiskCategory.POLICY,
    RiskLevel.MEDIUM,
    SafetyDecision.NEEDS_HUMAN_REVIEW,
    "Reduce requested limits or obtain explicit approval.",
)


def host_allowed(host: str, policy: ToolSafetyPolicy) -> bool:
    """Return whether a normalized host matches the allowlist."""
    normalized = host.rstrip(".").lower()
    for allowed in policy.allowed_domains:
        candidate = allowed.rstrip(".").lower()
        if normalized == candidate or normalized.endswith("." + candidate):
            return True
    return False


def _path_forms(value: str) -> set[str]:
    text = value.strip().strip("\"'")
    return {
        text.replace("\\", "/").lower(),
        posixpath.normpath(text.replace("\\", "/")).lower(),
        ntpath.normpath(text.replace("/", "\\")).replace("\\", "/").lower(),
    }


def path_forbidden(value: str, request: ScriptScanRequest, policy: ToolSafetyPolicy) -> bool:
    """Check a literal path in target execution context."""
    forms = _path_forms(value)
    if request.execution_home and (value == "~" or value.startswith("~/")):
        suffix = "" if value == "~" else value[2:]
        forms.update(_path_forms(posixpath.join(request.execution_home, suffix)))
    if request.cwd and not posixpath.isabs(value) and not ntpath.isabs(value):
        forms.update(_path_forms(posixpath.join(request.cwd, value)))
    for forbidden in policy.forbidden_paths:
        candidates = _path_forms(forbidden)
        if forbidden.startswith("~/") and request.execution_home:
            candidates.update(_path_forms(posixpath.join(request.execution_home, forbidden[2:])))
        for form in forms:
            if any(form == item or form.startswith(item.rstrip("/") + "/") for item in candidates):
                return True
    return bool(_SENSITIVE_PATH_RE.search(value))


def path_is_system_location(value: str, cwd: str = "") -> bool:
    """Return whether a write target is a root or operating-system path."""
    forms = _path_forms(value)
    if cwd and not posixpath.isabs(value) and not ntpath.isabs(value):
        forms.update(_path_forms(posixpath.join(cwd, value)))
    for form in forms:
        if form in {"/", "c:/"}:
            return True
        roots = _POSIX_SYSTEM_PATHS + _WINDOWS_SYSTEM_PATHS
        if any(form == root or form.startswith(root + "/") for root in roots):
            return True
    return False


def scan_paths(text: str, request: ScriptScanRequest, policy: ToolSafetyPolicy,
               sanitizer: SafetySanitizer) -> tuple[list[SafetyFinding], bool]:
    """Scan literal text for forbidden paths."""
    findings = []
    redacted = False
    tokens = _PATH_TOKEN_RE.findall(text)
    for token in tokens:
        if path_forbidden(token, request, policy):
            finding, changed = make_finding("FILE002", token, FILE_DENY, sanitizer)
            findings.append(finding)
            redacted = redacted or changed
        elif _tilde_path_is_unresolved(token, request):
            finding, changed = make_finding("FILE003", token, FILE_REVIEW, sanitizer)
            findings.append(finding)
            redacted = redacted or changed
    return findings, redacted


def _tilde_path_is_unresolved(token: str, request: ScriptScanRequest) -> bool:
    if not token.startswith("~"):
        return False
    if token == "~" or token.startswith("~/"):
        return not request.execution_home
    return True


def scan_urls(text: str, policy: ToolSafetyPolicy, sanitizer: SafetySanitizer) -> tuple[list[SafetyFinding], bool]:
    """Scan literal URLs against the domain allowlist."""
    findings = []
    redacted = False
    for url in _URL_RE.findall(text):
        host = urlparse(url).hostname
        if not host or not host_allowed(host, policy):
            finding, changed = make_finding("NET001", url, NETWORK_DENY, sanitizer)
            findings.append(finding)
            redacted = redacted or changed
    return findings, redacted


def scan_secret_sink(text: str, sanitizer: SafetySanitizer, is_sink: bool) -> tuple[list[SafetyFinding], bool]:
    """Flag secret-looking values reaching an output sink."""
    if not is_sink or not _SECRET_REFERENCE_RE.search(text):
        return [], False
    finding, redacted = make_finding("SECRET001", text, SECRET_DENY, sanitizer)
    return [finding], redacted


def scan_limits(request: ScriptScanRequest, policy: ToolSafetyPolicy,
                sanitizer: SafetySanitizer) -> tuple[list[SafetyFinding], bool]:
    """Check request-level limits."""
    requested = request.requested_timeout_seconds
    if request.timeout_arg_name == "timeout_sec" and requested is not None and not float(requested).is_integer():
        finding, redacted = make_finding(
            "POLICY001",
            f"requested timeout_sec {requested}s is not an integer",
            POLICY_REVIEW,
            sanitizer,
        )
        return [finding], redacted
    if requested is None or 0 < requested <= policy.max_timeout_seconds:
        return [], False
    if not math.isfinite(requested):
        finding, redacted = make_finding(
            "POLICY001",
            f"requested timeout {requested}s is not finite",
            POLICY_REVIEW,
            sanitizer,
        )
        return [finding], redacted
    if requested <= 0:
        return [], False
    evidence = f"requested timeout {requested}s exceeds {policy.max_timeout_seconds}s"
    finding, redacted = make_finding("POLICY001", evidence, POLICY_REVIEW, sanitizer)
    return [finding], redacted
