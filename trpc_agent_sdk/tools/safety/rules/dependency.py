"""Dependency installation safety rule — detects package install operations.

Rule IDs:
- DEP-001: Package installation detected (MEDIUM)
- DEP-002: Installation from untrusted source (HIGH)
"""

from __future__ import annotations

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

# Python functions that install packages
_PYTHON_INSTALL_FUNCS: set[str] = {
    "subprocess.run",
    "subprocess.call",
    "subprocess.check_call",
    "subprocess.check_output",
    "subprocess.Popen",
    "os.system",
    "os.popen",
}

# Keywords in command strings that indicate package installation
_INSTALL_KEYWORDS: set[str] = {
    "pip install",
    "pip3 install",
    "conda install",
    "easy_install",
    "npm install",
    "npm i ",
    "yarn add",
    "pnpm add",
    "apt install",
    "apt-get install",
    "yum install",
    "brew install",
    "gem install",
    "cargo install",
}

# Bash patterns for package installation
_BASH_INSTALL_PATTERNS: dict[str, str] = {
    "pip_install": r"\bpip3?\s+install\b",
    "conda_install": r"\bconda\s+install\b",
    "npm_install": r"\bnpm\s+(install|i)\b",
    "yarn_add": r"\byarn\s+add\b",
    "apt_install": r"\bapt(-get)?\s+install\b",
    "yum_install": r"\byum\s+install\b",
    "brew_install": r"\bbrew\s+install\b",
    "gem_install": r"\bgem\s+install\b",
    "cargo_install": r"\bcargo\s+install\b",
}

# Patterns indicating untrusted source installation
_BASH_UNTRUSTED_PATTERNS: dict[str, str] = {
    "pip_url": r"\bpip3?\s+install\s+.*https?://",
    "pip_git": r"\bpip3?\s+install\s+git\+",
    "pip_index_url": r"\bpip3?\s+install\s+.*--index-url\s+",
    "pip_extra_index": r"\bpip3?\s+install\s+.*--extra-index-url\s+",
    "curl_pipe_bash": r"\bcurl\s+.*\|\s*(bash|sh|zsh)\b",
    "wget_pipe_bash": r"\bwget\s+.*\|\s*(bash|sh|zsh)\b",
    "npm_url": r"\bnpm\s+install\s+https?://",
}


# ---------------------------------------------------------------------------
# Rule: DEP-001 — Package installation detected
# ---------------------------------------------------------------------------


@register_rule
class PackageInstallRule(BaseRule):
    """Detects package installation operations."""

    rule_id = "DEP-001"
    category = RiskCategory.DEPENDENCY
    severity = Severity.MEDIUM
    languages = [Language.PYTHON, Language.BASH]
    description = "Detects package installation commands that may introduce dependencies."

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

        calls = python_scanner.find_function_calls(tree, _PYTHON_INSTALL_FUNCS)
        for call in calls:
            str_args = python_scanner.get_string_args(call)
            for arg in str_args:
                arg_lower = arg.lower()
                for keyword in _INSTALL_KEYWORDS:
                    if keyword in arg_lower:
                        call_name = python_scanner.get_call_name(call)
                        findings.append(Finding(
                            rule_id=self.rule_id,
                            category=self.category,
                            severity=self.severity,
                            decision=Decision.NEEDS_HUMAN_REVIEW,
                            evidence=f"{call_name}({arg!r})",
                            line_number=call.lineno,
                            description=f"Package installation detected: {arg}",
                            recommendation="Verify the package is trusted and version-pinned.",
                        ))
                        break

        return findings

    def _scan_bash(self, ctx: "ScanContext") -> list[Finding]:
        findings: list[Finding] = []
        patterns = bash_scanner.CompiledPatternSet(_BASH_INSTALL_PATTERNS)
        matches = bash_scanner.scan_lines(ctx.source_code, patterns)

        for m in matches:
            findings.append(Finding(
                rule_id=self.rule_id,
                category=self.category,
                severity=self.severity,
                decision=Decision.NEEDS_HUMAN_REVIEW,
                evidence=m.line_content,
                line_number=m.line_number,
                description=f"Package installation detected ({m.pattern_name})",
                recommendation="Verify the package is trusted and version-pinned.",
            ))

        return findings


# ---------------------------------------------------------------------------
# Rule: DEP-002 — Untrusted source installation
# ---------------------------------------------------------------------------


@register_rule
class UntrustedSourceRule(BaseRule):
    """Detects package installation from untrusted sources."""

    rule_id = "DEP-002"
    category = RiskCategory.DEPENDENCY
    severity = Severity.HIGH
    languages = [Language.PYTHON, Language.BASH]
    description = "Detects package installation from URLs, custom indices, or piped scripts."

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

        calls = python_scanner.find_function_calls(tree, _PYTHON_INSTALL_FUNCS)
        for call in calls:
            str_args = python_scanner.get_string_args(call)
            for arg in str_args:
                arg_lower = arg.lower()
                # Check for URL-based installation
                if ("pip install" in arg_lower or "pip3 install" in arg_lower):
                    if ("http://" in arg_lower or "https://" in arg_lower or
                            "git+" in arg_lower or "--index-url" in arg_lower or
                            "--extra-index-url" in arg_lower):
                        call_name = python_scanner.get_call_name(call)
                        findings.append(Finding(
                            rule_id=self.rule_id,
                            category=self.category,
                            severity=self.severity,
                            decision=Decision.DENY,
                            evidence=f"{call_name}({arg!r})",
                            line_number=call.lineno,
                            description=f"Installation from untrusted source: {arg}",
                            recommendation="Only install packages from official registries (PyPI).",
                        ))

        return findings

    def _scan_bash(self, ctx: "ScanContext") -> list[Finding]:
        findings: list[Finding] = []
        patterns = bash_scanner.CompiledPatternSet(_BASH_UNTRUSTED_PATTERNS)
        matches = bash_scanner.scan_lines(ctx.source_code, patterns)

        for m in matches:
            # curl|bash is always DENY
            decision = Decision.DENY if "pipe_bash" in m.pattern_name else Decision.NEEDS_HUMAN_REVIEW
            severity = Severity.HIGH

            findings.append(Finding(
                rule_id=self.rule_id,
                category=self.category,
                severity=severity,
                decision=decision,
                evidence=m.line_content,
                line_number=m.line_number,
                description=f"Installation from untrusted source ({m.pattern_name})",
                recommendation="Avoid installing from URLs or piping scripts. Use official registries.",
            ))

        return findings
