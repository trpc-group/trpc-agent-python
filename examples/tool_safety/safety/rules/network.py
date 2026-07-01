# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
"""Rule: network egress to non-allow-listed domains.

Flags curl/wget/requests/aiohttp/socket/urllib calls whose target host is not
in the policy's ``whitelisted_domains``. When no allow-list is configured, all
network egress is flagged.
"""
from __future__ import annotations

import ast
import re
from urllib.parse import urlparse

from .base import SafetyRule
from .base import bash_lines
from .base import evidence_snippet
from .base import get_string_literal
from .base import iter_python_calls
from .base import normalize_language
from .base import parse_python_ast
from ..policy import PolicyConfig
from ..types import RiskLevel
from ..types import SafetyFinding
from ..types import ScanInput


# Python callables that initiate network requests.
_PY_NET_CALLS = {
    "requests.get", "requests.post", "requests.put", "requests.delete", "requests.patch",
    "requests.head", "requests.options", "requests.request",
    "httpx.get", "httpx.post", "httpx.request",
    "aiohttp.ClientSession.get", "aiohttp.ClientSession.post",
    "urllib.request.urlopen", "urllib.urlopen",
    "http.client.HTTPConnection", "http.client.HTTPSConnection",
    "socket.socket",
}

# Bash commands that initiate network requests.
_BASH_NET_COMMANDS = {"curl", "wget", "nc", "netcat", "telnet", "ftp", "scp", "rsync"}


class NetworkRule(SafetyRule):
    """Detect network egress to hosts outside the allow-list."""

    rule_id = "R002_network_egress"
    rule_name = "Network Egress"
    risk_type = "network"
    default_level = RiskLevel.HIGH
    languages = ("python", "bash")

    def check(self, scan_input: ScanInput, policy: PolicyConfig) -> list[SafetyFinding]:
        lang = normalize_language(scan_input)
        if lang == "python":
            return self._check_python(scan_input, policy)
        return self._check_bash(scan_input, policy)

    # ----- python -----

    def _check_python(self, scan_input: ScanInput, policy: PolicyConfig) -> list[SafetyFinding]:
        findings: list[SafetyFinding] = []
        tree = parse_python_ast(scan_input.script)
        if tree is None:
            return findings

        for node, name in iter_python_calls(tree):
            lname = name.lower()
            if lname not in {c.lower() for c in _PY_NET_CALLS}:
                continue
            host = _extract_host_from_call(node)
            if host is None:
                # Dynamic target: flag for review (cannot prove safety).
                findings.append(self._finding(
                    f"Network call {name}() with non-static target",
                    node.lineno,
                    evidence=f"{name}(<dynamic>)",
                    host="<dynamic>",
                    rec="Use a static, allow-listed URL. Dynamic targets require human review.",
                    level=RiskLevel.MEDIUM,
                ))
                continue
            if not policy.is_domain_allowed(host):
                findings.append(self._finding(
                    f"Network call {name}() to non-allow-listed host {host!r}",
                    node.lineno,
                    evidence=f"{name}(host={host!r})",
                    host=host,
                    rec=f"Add {host!r} to whitelisted_domains or remove the call.",
                ))
        return findings

    # ----- bash -----

    def _check_bash(self, scan_input: ScanInput, policy: PolicyConfig) -> list[SafetyFinding]:
        findings: list[SafetyFinding] = []
        for lineno, line in bash_lines(scan_input.script):
            tokens = line.split()
            if not tokens:
                continue
            cmd = tokens[0]
            if cmd not in _BASH_NET_COMMANDS:
                continue
            host = _extract_host_from_bash(line, cmd)
            if host is None:
                findings.append(self._finding(
                    f"{cmd} with non-static target",
                    lineno,
                    evidence=line,
                    host="<dynamic>",
                    rec="Use a static, allow-listed URL.",
                    level=RiskLevel.MEDIUM,
                ))
                continue
            if not policy.is_domain_allowed(host):
                findings.append(self._finding(
                    f"{cmd} to non-allow-listed host {host!r}",
                    lineno,
                    evidence=line,
                    host=host,
                    rec=f"Add {host!r} to whitelisted_domains or remove the call.",
                ))
        return findings

    def _finding(self, msg, line, evidence, host, rec, level=None) -> SafetyFinding:
        return SafetyFinding(
            rule_id=self.rule_id,
            rule_name=self.rule_name,
            risk_type=self.risk_type,
            risk_level=level or self.default_level,
            evidence=evidence_snippet(evidence),
            line=line,
            recommendation=rec,
            metadata={"message": msg, "host": host},
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_host_from_call(node: ast.Call) -> str | None:
    """Best-effort extract a hostname from a network call's first string arg."""
    if not node.args:
        # Check url= keyword.
        for kw in node.keywords:
            if kw.arg in {"url", "host", "address"}:
                return _host_from_value(kw.value)
        return None
    return _host_from_value(node.args[0])


def _host_from_value(value: ast.AST) -> str | None:
    s = get_string_literal(value)
    if s is None:
        return None
    return _host_from_string(s)


def _host_from_string(s: str) -> str | None:
    """Extract host from a URL or bare host string."""
    if "://" in s:
        parsed = urlparse(s)
        host = parsed.hostname
        return host.lower() if host else None
    # Bare host:port or host
    host = s.split("/")[0].split(":")[0].strip()
    if host and ("." in host or host == "localhost"):
        return host.lower()
    return None


def _extract_host_from_bash(line: str, cmd: str) -> str | None:
    """Extract host from common curl/wget/nc invocations."""
    # curl/wget URL
    url_match = re.search(r"https?://([^\s'\"|>;]+)", line)
    if url_match:
        host = url_match.group(1).split("/")[0].split(":")[0]
        return host.lower() if host else None
    # curl --user host:port or nc host port
    tokens = line.split()
    for tok in tokens:
        if "://" in tok:
            return _host_from_string(tok)
    # nc host port / scp user@host:/path
    at_match = re.search(r"@([^\s:]+)", line)
    if at_match:
        return at_match.group(1).lower()
    if cmd in {"nc", "netcat", "telnet"} and len(tokens) >= 2:
        return tokens[1].lower()
    return None
