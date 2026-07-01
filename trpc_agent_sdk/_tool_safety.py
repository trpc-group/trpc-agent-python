# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Structured safety review for generated code and shell actions."""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from typing import Any
from typing import Iterable
from urllib.parse import urlparse

from trpc_agent_sdk._tool_safety_policy import ToolSafetyPolicy
from trpc_agent_sdk._tool_safety_policy import load_tool_safety_policy


@dataclass(frozen=True)
class SafetyReview:
    """Result of a safety review decision."""

    decision: str
    rule_id: str
    finding: str
    report: dict[str, Any]
    audit: dict[str, Any]


@dataclass(frozen=True)
class Rule:
    """Pattern-based safety rule."""

    rule_id: str
    decision: str
    finding: str
    recommendation: str
    pattern: re.Pattern[str]


_RULES: tuple[Rule, ...] = (
    Rule(
        rule_id="dangerous_delete",
        decision="deny",
        finding="Destructive delete operation detected.",
        recommendation="Do not run destructive deletes without explicit user approval and scoped paths.",
        pattern=re.compile(
            r"\b(rm\s+-[^\n;&|]*[rf]|shutil\.rmtree|os\.remove|os\.unlink|Path\([^)]*\)\.unlink)",
            re.IGNORECASE,
        ),
    ),
    Rule(
        rule_id="subprocess_execution",
        decision="deny",
        finding="Subprocess execution from Python detected.",
        recommendation="Avoid spawning child processes from generated Python unless explicitly approved.",
        pattern=re.compile(r"\bsubprocess\.(?:run|Popen|call|check_call|check_output)\b|\bimport\s+subprocess\b"),
    ),
    Rule(
        rule_id="os_system_execution",
        decision="deny",
        finding="OS system command execution detected.",
        recommendation="Avoid os.system calls unless the command is explicitly approved.",
        pattern=re.compile(r"\bos\.system\s*\("),
    ),
    Rule(
        rule_id="package_install",
        decision="needs_human_review",
        finding="Package installation command detected.",
        recommendation="Send dependency installation through human review before mutating the environment.",
        pattern=re.compile(r"\b(?:pip|pip3|python(?:3)?\s+-m\s+pip)\s+install\b", re.IGNORECASE),
    ),
    Rule(
        rule_id="npm_install",
        decision="needs_human_review",
        finding="NPM package installation command detected.",
        recommendation="Send npm dependency installation through human review before mutating the environment.",
        pattern=re.compile(r"\bnpm\s+(?:install|i)\b", re.IGNORECASE),
    ),
    Rule(
        rule_id="apt_install",
        decision="needs_human_review",
        finding="APT package installation command detected.",
        recommendation="Send system package installation through human review before mutating the environment.",
        pattern=re.compile(r"\b(?:apt|apt-get)\s+install\b", re.IGNORECASE),
    ),
    Rule(
        rule_id="infinite_loop",
        decision="deny",
        finding="Potential unbounded loop detected.",
        recommendation="Add a bounded condition, timeout, or cancellation path before execution.",
        pattern=re.compile(r"\bwhile\s+True\s*:|\bfor\s+.*\bin\s+itertools\.count\s*\(", re.IGNORECASE),
    ),
    Rule(
        rule_id="sensitive_output",
        decision="deny",
        finding="Potential sensitive information output detected.",
        recommendation="Redact secrets and avoid printing environment variables or credential values.",
        pattern=re.compile(
            r"\bprint\s*\(\s*(?:os\.environ|.*(?:api[_-]?key|token|secret|password).*)\)|"
            r"\becho\s+\$?(?:API[_-]?KEY|TOKEN|SECRET|PASSWORD)\b",
            re.IGNORECASE,
        ),
    ),
    Rule(
        rule_id="wget_network",
        decision="deny",
        finding="Wget network command detected.",
        recommendation="Use allowlisted network access only and avoid direct wget calls from generated scripts.",
        pattern=re.compile(r"\bwget\b", re.IGNORECASE),
    ),
    Rule(
        rule_id="aiohttp_network",
        decision="deny",
        finding="aiohttp network client usage detected.",
        recommendation="Use allowlisted network access only and avoid direct aiohttp clients from generated scripts.",
        pattern=re.compile(r"\baiohttp\.ClientSession\b|\bimport\s+aiohttp\b|\bfrom\s+aiohttp\s+import\b"),
    ),
    Rule(
        rule_id="socket_network",
        decision="deny",
        finding="Socket network usage detected.",
        recommendation="Avoid raw socket access from generated scripts unless explicitly approved.",
        pattern=re.compile(r"\bsocket\.socket\s*\(|\bimport\s+socket\b|\bfrom\s+socket\s+import\b"),
    ),
    Rule(
        rule_id="fork_bomb",
        decision="deny",
        finding="Fork bomb pattern detected.",
        recommendation="Do not execute fork bombs or commands that recursively spawn processes.",
        pattern=re.compile(r":\(\)\s*\{.*\};\s*:", re.DOTALL),
    ),
    Rule(
        rule_id="bash_pipe",
        decision="needs_human_review",
        finding="Bash pipeline detected.",
        recommendation="Review piped shell commands because pipes can hide data flow between commands.",
        pattern=re.compile(r"(?<!\|)\|(?!\|)"),
    ),
    Rule(
        rule_id="shell_injection",
        decision="needs_human_review",
        finding="Shell command chaining pattern detected.",
        recommendation=("Review chained shell commands because they can indicate shell injection "
                        "or hidden side effects."),
        pattern=re.compile(r"(?:;|&&|\|\||`[^`]+`|\$\([^)]+\))"),
    ),
    Rule(
        rule_id="excessive_concurrency",
        decision="deny",
        finding="Excessive concurrency pattern detected.",
        recommendation="Limit concurrency to a bounded and reviewed value before execution.",
        pattern=re.compile(
            r"ThreadPoolExecutor\s*\([^)]*max_workers\s*=\s*(?:[1-9]\d{2,})|"
            r"ProcessPoolExecutor\s*\([^)]*max_workers\s*=\s*(?:[1-9]\d{2,})|"
            r"asyncio\.gather\s*\([^)]*range\s*\(\s*(?:[1-9]\d{3,})",
            re.DOTALL,
        ),
    ),
    Rule(
        rule_id="large_file_write",
        decision="deny",
        finding="Large file write pattern detected.",
        recommendation="Bound file writes to a reviewed size before execution.",
        pattern=re.compile(
            r"\bwrite(?:_bytes|_text)?\s*\([^)]*\*\s*(?:[1-9]\d{7,}|10\s*\*\*\s*[8-9])|"
            r"\btruncate\s+-s\s+\d+\s*[GTP]",
            re.IGNORECASE,
        ),
    ),
    Rule(
        rule_id="human_review_required",
        decision="needs_human_review",
        finding="Operation requires human review.",
        recommendation="Pause execution and ask a human to approve or reject this operation.",
        pattern=re.compile(
            r"\b(?:sudo|production|prod|deploy|restart\s+service|systemctl|kubectl\s+apply)\b",
            re.IGNORECASE,
        ),
    ),
)

_PATH_RULES: dict[str, tuple[str, str, str]] = {
    "read_dotenv": (
        "Attempt to read environment secret file detected.",
        "Do not read .env files unless the user explicitly requested credential inspection.",
        "deny",
    ),
    "read_ssh": (
        "Attempt to read SSH credentials detected.",
        "Do not access ~/.ssh or SSH private keys.",
        "deny",
    ),
}

_URL_RE = re.compile(r"https?://[^\s\"'`<>]+", re.IGNORECASE)


class SafetyReviewer:
    """Evaluate generated actions against deterministic safety rules."""

    def __init__(
        self,
        allowed_domains: Iterable[str] | None = None,
        *,
        policy: ToolSafetyPolicy | None = None,
        policy_path: str | None = None,
    ) -> None:
        base_policy = policy if policy is not None else load_tool_safety_policy(policy_path)
        if allowed_domains is not None:
            base_policy = base_policy.with_allowed_domains(_normalize_host(domain) for domain in allowed_domains)
        self.policy = base_policy

    def review(self, text: str, *, action_type: str = "python", tool_name: str = "") -> SafetyReview:
        """Return a structured decision for *text*."""
        started_at = time.perf_counter()
        source = text or ""
        network_review: SafetyReview | None = None
        urls = _extract_urls(source)
        if urls:
            network_review = self._review_network(source, urls, action_type, tool_name, started_at)
            if network_review is not None and network_review.decision != "allow":
                return network_review

        path_review = self._review_blocked_paths(source, action_type, tool_name, started_at)
        if path_review is not None:
            return path_review

        for rule in _RULES:
            match = rule.pattern.search(source)
            if match:
                evidence, desensitized = _redact_evidence(match.group(0))
                return self._build_review(
                    source=source,
                    action_type=action_type,
                    tool_name=tool_name,
                    decision=rule.decision,
                    rule_id=rule.rule_id,
                    finding=rule.finding,
                    risk_level=self.policy.risk_level_for(rule.rule_id),
                    recommendation=rule.recommendation,
                    evidence=evidence,
                    desensitized=desensitized,
                    started_at=started_at,
                )

        if network_review is not None:
            return network_review

        return self._build_review(
            source=source,
            action_type=action_type,
            tool_name=tool_name,
            decision="allow",
            rule_id="safe_python",
            finding="No risky code or command patterns detected.",
            risk_level=self.policy.risk_level_for("safe_python"),
            recommendation="Proceed with normal execution.",
            evidence="",
            desensitized=False,
            started_at=started_at,
        )

    def _review_network(
        self,
        source: str,
        urls: list[str],
        action_type: str,
        tool_name: str,
        started_at: float,
    ) -> SafetyReview | None:
        disallowed_hosts = [
            _normalize_host(urlparse(url).hostname or "") for url in urls
            if not _host_allowed(_normalize_host(urlparse(url).hostname or ""), self.policy.allowed_domains)
        ]
        if disallowed_hosts:
            return self._build_review(
                source=source,
                action_type=action_type,
                tool_name=tool_name,
                decision="deny",
                rule_id="network_not_allowlisted",
                finding="Network request targets a non-allowlisted domain.",
                risk_level=self.policy.risk_level_for("network_not_allowlisted"),
                recommendation="Only request domains that are explicitly allowlisted.",
                evidence=disallowed_hosts[0],
                desensitized=False,
                started_at=started_at,
            )
        return self._build_review(
            source=source,
            action_type=action_type,
            tool_name=tool_name,
            decision="allow",
            rule_id="network_allowlist",
            finding="Network request targets an allowlisted domain.",
            risk_level=self.policy.risk_level_for("network_allowlist"),
            recommendation="Proceed with the allowlisted network request.",
            evidence=_normalize_host(urlparse(urls[0]).hostname or ""),
            desensitized=False,
            started_at=started_at,
        )

    def _review_blocked_paths(
        self,
        source: str,
        action_type: str,
        tool_name: str,
        started_at: float,
    ) -> SafetyReview | None:
        for rule_id, (finding, recommendation, decision) in _PATH_RULES.items():
            for blocked_path in self.policy.blocked_paths_for(rule_id):
                if blocked_path and blocked_path in source:
                    return self._build_review(
                        source=source,
                        action_type=action_type,
                        tool_name=tool_name,
                        decision=decision,
                        rule_id=rule_id,
                        finding=finding,
                        risk_level=self.policy.risk_level_for(rule_id),
                        recommendation=recommendation,
                        evidence=blocked_path,
                        desensitized=False,
                        started_at=started_at,
                    )
        return None

    def _build_review(
        self,
        *,
        source: str,
        action_type: str,
        tool_name: str,
        decision: str,
        rule_id: str,
        finding: str,
        risk_level: str,
        recommendation: str,
        evidence: str,
        desensitized: bool,
        started_at: float,
    ) -> SafetyReview:
        source_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
        blocked = decision in {"deny", "needs_human_review"}
        latency = time.perf_counter() - started_at
        report = {
            "decision": decision,
            "rule_id": rule_id,
            "finding": finding,
            "risk_level": risk_level,
            "tool_name": tool_name,
            "blocked": blocked,
            "latency": latency,
            "desensitized": desensitized,
            "recommendation": recommendation,
            "evidence": evidence,
        }
        audit = {
            "decision":
            decision,
            "rule_id":
            rule_id,
            "risk_level":
            risk_level,
            "tool_name":
            tool_name,
            "blocked":
            blocked,
            "latency":
            latency,
            "desensitized":
            desensitized,
            "action_type":
            action_type,
            "input_sha256":
            source_hash,
            "allowed_domains":
            list(self.policy.allowed_domains),
            "rules_evaluated": ["safe_python", "network_allowlist", "network_not_allowlisted"] + list(_PATH_RULES) +
            [rule.rule_id for rule in _RULES],
        }
        return SafetyReview(
            decision=decision,
            rule_id=rule_id,
            finding=finding,
            report=report,
            audit=audit,
        )


def _extract_urls(text: str) -> list[str]:
    return [match.group(0).rstrip(").,;") for match in _URL_RE.finditer(text)]


def _host_allowed(host: str, allowed_domains: tuple[str, ...]) -> bool:
    if not host or not allowed_domains:
        return False
    return any(host == allowed or host.endswith(f".{allowed}") for allowed in allowed_domains)


def _normalize_host(host: str) -> str:
    host = host.strip().lower()
    if "://" in host:
        host = urlparse(host).hostname or ""
    if host.startswith("www."):
        host = host[4:]
    return host.rstrip(".")


def _redact_evidence(value: str) -> tuple[str, bool]:
    redacted = re.sub(
        r"(?i)(api[_-]?key|token|secret|password)\s*=\s*['\"]?[^'\"\s)]+",
        r"\1=<redacted>",
        value,
    )
    return redacted[:120], redacted != value


SafetyChecker = SafetyReviewer
