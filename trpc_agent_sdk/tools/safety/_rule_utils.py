# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under the Apache License Version 2.0.
"""Shared helpers for tool safety scanning rules."""

from __future__ import annotations

import ast
import posixpath
import re
from pathlib import Path
from typing import Iterable
from typing import Optional
from urllib.parse import urlparse

from ._models import Decision
from ._models import RiskLevel
from ._models import SafetyFinding
from ._policy import ToolSafetyPolicy

SECRET_NAME_RE = re.compile(
    r"(?i)(api[_-]?key|access[_-]?token|auth[_-]?token|token|password|passwd|secret|private[_-]?key|credential)")
SECRET_LITERAL_RE = re.compile(r"(?i)(?:sk|pk|api|token|secret)[-_][a-z0-9_-]{8,}|bearer\s+[a-z0-9._~+/-]{8,}")
SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b([a-z0-9_-]*(?:api[_-]?key|token|password|passwd|secret|credential)[a-z0-9_-]*)"
    r"(\s*[\"']?\s*[:=]\s*)([\"']?)([^\"'\s,}]+)")
URL_RE = re.compile(r"https?://[^\s\"'<>]+")


def call_name(node: ast.AST) -> str:
    """Return a dotted name for a Python call expression."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = call_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def string_value(node: ast.AST) -> Optional[str]:
    """Return a statically known string, including joined literals."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = []
        for value in node.values:
            if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
                return None
            parts.append(value.value)
        return "".join(parts)
    return None


def redact(text: str, secrets: Iterable[str], max_chars: int) -> tuple[str, bool]:
    """Redact known environment secrets and credential-shaped literals."""
    redacted = text
    changed = False
    for secret in secrets:
        if secret and secret in redacted:
            redacted = redacted.replace(secret, "[REDACTED]")
            changed = True

    def replace_assignment(match: re.Match[str]) -> str:
        nonlocal changed
        changed = True
        return f"{match.group(1)}{match.group(2)}{match.group(3)}[REDACTED]"

    redacted = SECRET_ASSIGNMENT_RE.sub(replace_assignment, redacted)
    updated = SECRET_LITERAL_RE.sub("[REDACTED]", redacted)
    changed = changed or updated != redacted
    redacted = updated
    if len(redacted) > max_chars:
        redacted = f"{redacted[:max_chars - 3]}..."
    return redacted, changed


def line_evidence(script: str, line_number: int, secrets: Iterable[str], policy: ToolSafetyPolicy) -> tuple[str, bool]:
    """Get bounded, redacted source evidence for one line."""
    lines = script.splitlines()
    raw = lines[line_number - 1].strip() if 0 < line_number <= len(lines) else f"line {line_number}"
    return redact(raw, secrets, policy.max_evidence_chars)


def add_finding(
    findings: list[SafetyFinding],
    policy: ToolSafetyPolicy,
    *,
    category: str,
    rule_id: str,
    title: str,
    risk_level: RiskLevel,
    decision: Decision,
    evidence: str,
    recommendation: str,
    line_number: Optional[int] = None,
    redacted: bool = False,
) -> None:
    """Append a finding after applying policy overrides and disabled rules."""
    if rule_id in policy.disabled_rules:
        return
    findings.append(
        SafetyFinding(
            category=category,
            rule_id=rule_id,
            title=title,
            risk_level=policy.risk_level_for(rule_id, risk_level),
            decision=policy.decision_for(rule_id, decision),
            evidence=evidence,
            recommendation=recommendation,
            line_number=line_number,
            redacted=redacted,
        ))


def add_line_finding(
    findings: list[SafetyFinding],
    policy: ToolSafetyPolicy,
    script: str,
    line_number: int,
    secrets: Iterable[str],
    **kwargs,
) -> None:
    """Append a finding using source text as bounded evidence."""
    evidence, was_redacted = line_evidence(script, line_number, secrets, policy)
    add_finding(
        findings,
        policy,
        evidence=evidence,
        line_number=line_number,
        redacted=was_redacted,
        **kwargs,
    )


def path_is_denied(value: str, policy: ToolSafetyPolicy) -> bool:
    """Match protected path fragments without resolving or touching disk."""
    normalized = value.strip().replace("\\", "/")
    lowered = normalized.lower()
    lexical_path = posixpath.normpath(lowered)
    path_name = Path(lexical_path).name.lower()
    for denied in policy.denied_paths:
        denied_normalized = denied.replace("\\", "/").lower().rstrip("/")
        denied_name = Path(denied_normalized).name
        if denied_normalized.startswith("~/"):
            if denied_normalized[1:] in lowered or denied_normalized in lowered:
                return True
        elif denied_normalized.startswith("/"):
            if (lexical_path == denied_normalized or lexical_path.startswith(f"{denied_normalized}/")):
                return True
            relative_denied = re.escape(denied_normalized.lstrip("/"))
            traversal = rf"(?:^|/)(?:\.\./)+{relative_denied}(?:/|$)"
            if re.search(traversal, lowered):
                return True
        elif path_name == denied_name or f"/{denied_name}" in lowered:
            return True
    return False


def domain_allowed(url: str, policy: ToolSafetyPolicy) -> bool:
    """Return whether a URL hostname matches an exact or wildcard allowlist entry."""
    hostname = (urlparse(url).hostname or "").lower().rstrip(".")
    if not hostname:
        return False
    for pattern in policy.allowed_domains:
        if pattern.startswith("*."):
            suffix = pattern[2:]
            if hostname.endswith(f".{suffix}"):
                return True
        elif hostname == pattern:
            return True
    return False
