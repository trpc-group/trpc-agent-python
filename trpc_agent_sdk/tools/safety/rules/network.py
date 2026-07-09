"""Network access safety rule — detects outbound network requests.

Rule IDs:
- NET-001: Network request to non-whitelisted domain (HIGH)
- NET-002: Use of raw socket or low-level network API (MEDIUM)
"""

from __future__ import annotations

import fnmatch
from typing import TYPE_CHECKING

from trpc_agent_sdk.tools.safety.models import (
    Decision,
    Finding,
    Language,
    RiskCategory,
    Severity,
)
from trpc_agent_sdk.tools.safety.rules._base import BaseRule, register_rule
from trpc_agent_sdk.tools.safety.scanner import bash_scanner, python_scanner

if TYPE_CHECKING:
    from trpc_agent_sdk.tools.safety.models import ScanContext
    from trpc_agent_sdk.tools.safety.policy import PolicyConfig

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Python functions that make network requests
_PYTHON_NETWORK_FUNCS: set[str] = {
    "requests.get",
    "requests.post",
    "requests.put",
    "requests.delete",
    "requests.patch",
    "requests.head",
    "requests.request",
    "urllib.request.urlopen",
    "urllib.request.urlretrieve",
    "httpx.get",
    "httpx.post",
    "httpx.put",
    "httpx.delete",
    "httpx.Client",
    "httpx.AsyncClient",
    "aiohttp.ClientSession",
    "http.client.HTTPConnection",
    "http.client.HTTPSConnection",
}

# Python low-level socket APIs
_PYTHON_SOCKET_FUNCS: set[str] = {
    "socket.socket",
    "socket.create_connection",
}

# Bash patterns for network access
_BASH_NETWORK_PATTERNS: dict[str, str] = {
    "curl": r"\bcurl\s+",
    "wget": r"\bwget\s+",
    "nc": r"\bnc\s+",
    "netcat": r"\bnetcat\s+",
    "ssh": r"\bssh\s+",
    "scp": r"\bscp\s+",
    "rsync_remote": r"\brsync\s+.*@",
    "ftp": r"\bftp\s+",
    "telnet": r"\btelnet\s+",
}


def _domain_matches_whitelist(domain: str, allowed_domains: list[str]) -> bool:
    """Check if a domain matches any pattern in the allowed list."""
    domain_lower = domain.lower()
    for pattern in allowed_domains:
        pattern_lower = pattern.lower()
        if fnmatch.fnmatch(domain_lower, pattern_lower):
            return True
        # Also check if exact match
        if domain_lower == pattern_lower:
            return True
    return False


def _extract_domain_from_python_arg(arg: str) -> str | None:
    """Try to extract domain from a URL string argument."""
    for prefix in ("https://", "http://", "ftp://"):
        if arg.lower().startswith(prefix):
            host_part = arg[len(prefix):]
            # Strip user:pass@
            if "@" in host_part.split("/")[0]:
                host_part = host_part.split("@", 1)[-1]
            host = host_part.split("/")[0].split(":")[0]
            if host and "." in host:
                return host.lower()
    return None


# ---------------------------------------------------------------------------
# Rule: NET-001 — Non-whitelisted network request
# ---------------------------------------------------------------------------


@register_rule
class NetworkRequestRule(BaseRule):
    """Detects network requests to non-whitelisted domains."""

    rule_id = "NET-001"
    category = RiskCategory.NETWORK
    severity = Severity.HIGH
    languages = [Language.PYTHON, Language.BASH]
    description = "Detects outbound network requests to non-whitelisted domains."

    def scan(self, ctx: "ScanContext", policy: "PolicyConfig | None" = None) -> list[Finding]:
        findings: list[Finding] = []
        allowed_domains = policy.network.allowed_domains if policy else []

        if ctx.language == Language.PYTHON and ctx.ast_tree is not None:
            findings.extend(self._scan_python(ctx, allowed_domains))
        elif ctx.language == Language.BASH:
            findings.extend(self._scan_bash(ctx, allowed_domains))

        return findings

    def _scan_python(self, ctx: "ScanContext", allowed_domains: list[str]) -> list[Finding]:
        findings: list[Finding] = []
        tree = ctx.ast_tree

        calls = python_scanner.find_function_calls(tree, _PYTHON_NETWORK_FUNCS)
        for call in calls:
            str_args = python_scanner.get_string_args(call)
            call_name = python_scanner.get_call_name(call)

            # Try to extract domain from string arguments
            domain_found = False
            for arg in str_args:
                domain = _extract_domain_from_python_arg(arg)
                if domain:
                    domain_found = True
                    if not _domain_matches_whitelist(domain, allowed_domains):
                        findings.append(Finding(
                            rule_id=self.rule_id,
                            category=self.category,
                            severity=self.severity,
                            decision=Decision.NEEDS_HUMAN_REVIEW,
                            evidence=f"{call_name}({arg!r})",
                            line_number=call.lineno,
                            description=f"Network request to non-whitelisted domain: {domain}",
                            recommendation=f"Add '{domain}' to network.allowed_domains if this is expected.",
                        ))

            # If no domain could be extracted (dynamic URL), flag as lower confidence
            if not domain_found and str_args:
                # Has args but none are URLs — likely not a network target concern
                pass
            elif not domain_found and not str_args:
                # No static args at all — could be dynamic URL
                findings.append(Finding(
                    rule_id=self.rule_id,
                    category=self.category,
                    severity=Severity.MEDIUM,
                    decision=Decision.NEEDS_HUMAN_REVIEW,
                    confidence=0.6,
                    evidence=call_name,
                    line_number=call.lineno,
                    description=f"Network call with non-static URL: {call_name}",
                    recommendation="Ensure the target URL is safe and expected.",
                ))

        return findings

    def _scan_bash(self, ctx: "ScanContext", allowed_domains: list[str]) -> list[Finding]:
        findings: list[Finding] = []

        for line_num, line in enumerate(ctx.lines, start=1):
            if bash_scanner.is_comment_line(line):
                continue
            effective = bash_scanner.strip_inline_comment(line).strip()
            if not effective:
                continue

            # Extract URLs from the line
            urls = bash_scanner.extract_urls_from_line(effective)
            for url in urls:
                domain = bash_scanner.extract_domain_from_url(url)
                if domain and not _domain_matches_whitelist(domain, allowed_domains):
                    findings.append(Finding(
                        rule_id=self.rule_id,
                        category=self.category,
                        severity=self.severity,
                        decision=Decision.NEEDS_HUMAN_REVIEW,
                        evidence=effective,
                        line_number=line_num,
                        description=f"Network request to non-whitelisted domain: {domain}",
                        recommendation=f"Add '{domain}' to network.allowed_domains if this is expected.",
                    ))

        return findings


# ---------------------------------------------------------------------------
# Rule: NET-002 — Raw socket / low-level network API
# ---------------------------------------------------------------------------


@register_rule
class RawSocketRule(BaseRule):
    """Detects use of raw sockets or low-level network APIs."""

    rule_id = "NET-002"
    category = RiskCategory.NETWORK
    severity = Severity.MEDIUM
    languages = [Language.PYTHON, Language.BASH]
    description = "Detects use of raw sockets or low-level network access."

    def scan(self, ctx: "ScanContext", policy: "PolicyConfig | None" = None) -> list[Finding]:
        findings: list[Finding] = []

        if ctx.language == Language.PYTHON and ctx.ast_tree is not None:
            findings.extend(self._scan_python(ctx))
        elif ctx.language == Language.BASH:
            findings.extend(self._scan_bash(ctx))

        return findings

    def _scan_python(self, ctx: "ScanContext") -> list[Finding]:
        findings: list[Finding] = []
        tree = ctx.ast_tree

        calls = python_scanner.find_function_calls(tree, _PYTHON_SOCKET_FUNCS)
        for call in calls:
            call_name = python_scanner.get_call_name(call)
            findings.append(Finding(
                rule_id=self.rule_id,
                category=self.category,
                severity=self.severity,
                decision=Decision.NEEDS_HUMAN_REVIEW,
                evidence=call_name,
                line_number=call.lineno,
                description=f"Low-level network API usage: {call_name}",
                recommendation="Prefer high-level HTTP libraries. Verify socket usage is necessary.",
            ))
        return findings

    def _scan_bash(self, ctx: "ScanContext") -> list[Finding]:
        findings: list[Finding] = []
        # nc/netcat/telnet are low-level network tools
        low_level_patterns = bash_scanner.CompiledPatternSet({
            "nc": r"\bnc\s+",
            "netcat": r"\bnetcat\s+",
            "telnet": r"\btelnet\s+",
        })
        matches = bash_scanner.scan_lines(ctx.source_code, low_level_patterns)

        for m in matches:
            findings.append(Finding(
                rule_id=self.rule_id,
                category=self.category,
                severity=self.severity,
                decision=Decision.NEEDS_HUMAN_REVIEW,
                evidence=m.line_content,
                line_number=m.line_number,
                description=f"Low-level network tool usage: {m.pattern_name}",
                recommendation="Verify this network access is necessary and targets are safe.",
            ))
        return findings
