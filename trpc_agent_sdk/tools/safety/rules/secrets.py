"""Secrets exposure safety rule — detects hardcoded credentials and sensitive data leakage.

Rule IDs:
- SEC-001: Hardcoded secrets/credentials in source (HIGH)
- SEC-002: Environment variable leakage (MEDIUM)
"""

from __future__ import annotations

import ast
import re
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

# Variable name patterns that suggest secrets
_SECRET_VAR_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(password|passwd|pwd)", re.IGNORECASE),
    re.compile(r"(secret|token|api_?key|access_?key|auth)", re.IGNORECASE),
    re.compile(r"(private_?key|signing_?key|encryption_?key)", re.IGNORECASE),
    re.compile(r"(credentials?|client_?secret)", re.IGNORECASE),
    re.compile(r"(db_?(pass|password|uri|url))", re.IGNORECASE),
    re.compile(r"(connection_?string|conn_?str)", re.IGNORECASE),
]

# Value patterns that look like real secrets (high entropy / known formats)
_SECRET_VALUE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("AWS key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("GitHub token", re.compile(r"ghp_[A-Za-z0-9]{36,}")),
    ("GitHub token (old)", re.compile(r"github_pat_[A-Za-z0-9_]{22,}")),
    ("Slack token", re.compile(r"xox[bpors]-[A-Za-z0-9-]+")),
    ("Generic API key", re.compile(r"sk-[A-Za-z0-9]{32,}")),
    ("JWT", re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    ("Private key header", re.compile(r"-----BEGIN\s+(RSA\s+)?PRIVATE\s+KEY-----")),
    ("Basic auth header", re.compile(r"Basic\s+[A-Za-z0-9+/=]{20,}")),
    ("Bearer token", re.compile(r"Bearer\s+[A-Za-z0-9._\-]{20,}")),
]

# Bash patterns for secret exposure
_BASH_SECRET_PATTERNS: dict[str, str] = {
    "hardcoded_password": r"(?:PASSWORD|PASSWD|PWD)\s*=\s*['\"][^'\"]+['\"]",
    "hardcoded_token": r"(?:TOKEN|API_?KEY|SECRET|ACCESS_?KEY)\s*=\s*['\"][^'\"]+['\"]",
    "curl_auth": r"\bcurl\s+.*(-u|--user)\s+\S+:\S+",
    "export_secret": r"\bexport\s+(?:PASSWORD|TOKEN|SECRET|API_?KEY|ACCESS_?KEY)\s*=",
}

# Bash patterns for env var leakage
_BASH_ENV_LEAK_PATTERNS: dict[str, str] = {
    "echo_secret": r"\becho\s+.*\$\{?(?:PASSWORD|TOKEN|SECRET|API_?KEY|ACCESS_?KEY)",
    "printenv": r"\bprintenv\b",
    "env_dump": r"\benv\b\s*$",
    "set_dump": r"\bset\b\s*$",
}


def _is_secret_var_name(name: str) -> bool:
    """Check if a variable name suggests it holds a secret."""
    for pattern in _SECRET_VAR_PATTERNS:
        if pattern.search(name):
            return True
    return False


def _looks_like_real_secret(value: str) -> tuple[bool, str]:
    """Check if a string value looks like a real secret/credential.

    Returns (is_secret, pattern_name).
    """
    # Skip very short values (likely placeholders)
    if len(value) < 8:
        return False, ""
    # Skip obvious placeholders
    placeholders = {"xxx", "placeholder", "your_", "changeme", "todo", "fixme", "example"}
    value_lower = value.lower()
    if any(p in value_lower for p in placeholders):
        return False, ""

    for name, pattern in _SECRET_VALUE_PATTERNS:
        if pattern.search(value):
            return True, name
    return False, ""


# ---------------------------------------------------------------------------
# Rule: SEC-001 — Hardcoded secrets
# ---------------------------------------------------------------------------


@register_rule
class HardcodedSecretsRule(BaseRule):
    """Detects hardcoded secrets and credentials in source code."""

    rule_id = "SEC-001"
    category = RiskCategory.SECRETS
    severity = Severity.HIGH
    languages = [Language.PYTHON, Language.BASH]
    description = "Detects hardcoded credentials, API keys, and tokens in source code."

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

        # Check string assignments with secret-like variable names
        assignments = python_scanner.find_string_assignments(tree)
        for var_name, value in assignments.items():
            if _is_secret_var_name(var_name) and len(value) >= 8:
                findings.append(Finding(
                    rule_id=self.rule_id,
                    category=self.category,
                    severity=self.severity,
                    decision=Decision.DENY,
                    evidence=f'{var_name} = "{value[:20]}..."' if len(value) > 20 else f'{var_name} = "{value}"',
                    line_number=0,  # find_string_assignments doesn't track line numbers
                    description=f"Hardcoded secret in variable: {var_name}",
                    recommendation="Use environment variables or a secrets manager instead.",
                ))

        # Check all string constants for known secret patterns
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                is_secret, pattern_name = _looks_like_real_secret(node.value)
                if is_secret:
                    truncated = node.value[:30] + "..." if len(node.value) > 30 else node.value
                    findings.append(Finding(
                        rule_id=self.rule_id,
                        category=self.category,
                        severity=self.severity,
                        decision=Decision.DENY,
                        evidence=f'"{truncated}"',
                        line_number=node.lineno if hasattr(node, "lineno") else 0,
                        description=f"Hardcoded secret detected ({pattern_name})",
                        recommendation="Remove the secret. Use environment variables or a secrets manager.",
                    ))

        return findings

    def _scan_bash(self, ctx: "ScanContext") -> list[Finding]:
        findings: list[Finding] = []
        patterns = bash_scanner.CompiledPatternSet(_BASH_SECRET_PATTERNS)
        matches = bash_scanner.scan_lines(ctx.source_code, patterns)

        for m in matches:
            findings.append(Finding(
                rule_id=self.rule_id,
                category=self.category,
                severity=self.severity,
                decision=Decision.DENY,
                evidence=m.line_content,
                line_number=m.line_number,
                description=f"Hardcoded secret detected ({m.pattern_name})",
                recommendation="Use environment variables or a secrets manager instead.",
            ))

        # Also scan for known secret value patterns in all lines
        for line_num, line in enumerate(ctx.lines, start=1):
            if bash_scanner.is_comment_line(line):
                continue
            is_secret, pattern_name = _looks_like_real_secret(line)
            if is_secret:
                # Avoid duplicate if already caught by pattern above
                if not any(f.line_number == line_num for f in findings):
                    findings.append(Finding(
                        rule_id=self.rule_id,
                        category=self.category,
                        severity=self.severity,
                        decision=Decision.DENY,
                        evidence=line.strip()[:80],
                        line_number=line_num,
                        description=f"Secret pattern detected in code ({pattern_name})",
                        recommendation="Remove the secret. Use environment variables or a secrets manager.",
                    ))

        return findings


# ---------------------------------------------------------------------------
# Rule: SEC-002 — Environment variable leakage
# ---------------------------------------------------------------------------


@register_rule
class EnvLeakageRule(BaseRule):
    """Detects patterns that may leak environment variables containing secrets."""

    rule_id = "SEC-002"
    category = RiskCategory.SECRETS
    severity = Severity.MEDIUM
    languages = [Language.PYTHON, Language.BASH]
    description = "Detects patterns that may expose secrets via environment variable dumping or logging."

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

        # Detect os.environ dumping (print(os.environ), logging os.environ)
        print_calls = python_scanner.find_function_calls(tree, {"print", "logging.info",
                                                                 "logging.debug", "logger.info",
                                                                 "logger.debug"})
        for call in print_calls:
            # Check if any argument is os.environ
            for arg in call.args:
                if isinstance(arg, ast.Attribute):
                    if (isinstance(arg.value, ast.Name) and arg.value.id == "os"
                            and arg.attr == "environ"):
                        call_name = python_scanner.get_call_name(call)
                        findings.append(Finding(
                            rule_id=self.rule_id,
                            category=self.category,
                            severity=self.severity,
                            decision=Decision.NEEDS_HUMAN_REVIEW,
                            evidence=f"{call_name}(os.environ)",
                            line_number=call.lineno,
                            description="Environment variables may be leaked via output",
                            recommendation="Avoid printing full os.environ. Access specific variables only.",
                        ))

        return findings

    def _scan_bash(self, ctx: "ScanContext") -> list[Finding]:
        findings: list[Finding] = []
        patterns = bash_scanner.CompiledPatternSet(_BASH_ENV_LEAK_PATTERNS)
        matches = bash_scanner.scan_lines(ctx.source_code, patterns)

        for m in matches:
            findings.append(Finding(
                rule_id=self.rule_id,
                category=self.category,
                severity=self.severity,
                decision=Decision.NEEDS_HUMAN_REVIEW,
                evidence=m.line_content,
                line_number=m.line_number,
                description=f"Potential secret leakage via environment ({m.pattern_name})",
                recommendation="Avoid dumping all environment variables. They may contain secrets.",
            ))

        return findings
