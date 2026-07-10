# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Rule definitions for the Tool Script Safety Guard.

Pattern rules use regex matching against raw script text.
AST rules parse Python AST for deeper structural analysis.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import Callable
from typing import Optional

from ._types import RiskLevel
from ._types import RiskType
from ._types import RuleFinding


@dataclass
class PatternRule:
    """A safety rule based on regex pattern matching."""
    rule_id: str
    risk_type: RiskType
    risk_level: RiskLevel
    message: str
    recommendation: str
    patterns: list[str]
    evidence_group: int = 0
    extra_check: Optional[Callable[[re.Match], bool]] = None

    def check(self, text: str) -> Optional[RuleFinding]:
        compiled_patterns = [re.compile(p, re.IGNORECASE | re.MULTILINE) for p in self.patterns]
        for pattern in compiled_patterns:
            match = pattern.search(text)
            if match:
                if self.extra_check and not self.extra_check(match):
                    continue
                evidence = match.group(0) if match.groups() else match.group(self.evidence_group)
                return RuleFinding(
                    rule_id=self.rule_id,
                    risk_type=self.risk_type,
                    risk_level=self.risk_level,
                    evidence=evidence.strip()[:200],
                    message=self.message,
                    recommendation=self.recommendation,
                )
        return None


@dataclass
class AstRule:
    """A safety rule based on Python AST analysis."""
    rule_id: str
    risk_type: RiskType
    risk_level: RiskLevel
    message: str
    recommendation: str
    check: Callable[[ast.AST], list[RuleFinding]]


BUILTIN_PATTERN_RULES: list[PatternRule] = [
    PatternRule(
        rule_id="DANGEROUS_DELETE_001",
        risk_type=RiskType.DANGEROUS_FILE_OP,
        risk_level=RiskLevel.CRITICAL,
        message="Dangerous file deletion detected",
        recommendation="Use temporary directories or confirm deletion intent",
        patterns=[
            r"rm\s+(-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*[a-zA-Z]*|-[a-zA-Z]*f[a-zA-Z]*r[a-zA-Z]*[a-zA-Z]*)\s",
            r"shutil\.rmtree\s*\(",
            r"os\.remove\s*\(",
            r"os\.unlink\s*\(",
            r"pathlib\.Path\([^)]*\)\.unlink\s*\(",
            r"subprocess\.run\s*\(\s*\[.*rm.*\].*-rf",
        ],
    ),
    PatternRule(
        rule_id="SENSITIVE_PATH_002",
        risk_type=RiskType.DANGEROUS_FILE_OP,
        risk_level=RiskLevel.CRITICAL,
        message="Access to sensitive file path detected",
        recommendation="Avoid reading sensitive system and credential files",
        patterns=[
            r"(~\/\.ssh|/etc/passwd|/etc/shadow|~\/\.aws|~\/\.gcloud|~\/\.azure)",
            r"(\.env\b|~\/\.gitconfig|~\/\.gnupg|/root/|~\/\.kube)",
            r"(~\/\.docker|/var/run/docker\.sock)",
            r"(id_rsa|id_ed25519|id_ecdsa)",
            r"(open|with open)\s*\([^)]*(passwd|shadow|\.env|\.ssh|credentials|secret)",
        ],
    ),
    PatternRule(
        rule_id="NETWORK_CURL_003",
        risk_type=RiskType.NETWORK_ACCESS,
        risk_level=RiskLevel.HIGH,
        message="External network request via curl/wget detected",
        recommendation="Use whitelisted domains only or explicitly approve network access",
        patterns=[
            r"\bcurl\s+",
            r"\bwget\s+",
        ],
    ),
    PatternRule(
        rule_id="NETWORK_PYTHON_004",
        risk_type=RiskType.NETWORK_ACCESS,
        risk_level=RiskLevel.HIGH,
        message="Python HTTP request detected",
        recommendation="Restrict network calls to whitelisted domains",
        patterns=[
            r"requests\.(get|post|put|delete|patch|head|options)\s*\(",
            r"httpx\.(get|post|put|delete|patch)\s*\(",
            r"urllib\.request\.(urlopen|urlretrieve)\s*\(",
            r"aiohttp\.(ClientSession|request)\s*\(",
        ],
    ),
    PatternRule(
        rule_id="NETWORK_SOCKET_005",
        risk_type=RiskType.NETWORK_ACCESS,
        risk_level=RiskLevel.HIGH,
        message="Raw socket connection detected",
        recommendation="Use higher-level HTTP libraries with domain whitelisting",
        patterns=[
            r"socket\.(connect|create_connection|socket)\s*\(",
            r"socket\.socket\s*\(\s*socket\.AF_INET",
        ],
    ),
    PatternRule(
        rule_id="SUBPROCESS_006",
        risk_type=RiskType.SYSTEM_COMMAND,
        risk_level=RiskLevel.HIGH,
        message="Subprocess execution detected",
        recommendation="Avoid spawning subprocesses or use a restricted command set",
        patterns=[
            r"subprocess\.(run|Popen|call|check_output|check_call)\s*\(",
        ],
    ),
    PatternRule(
        rule_id="OS_SYSTEM_007",
        risk_type=RiskType.SYSTEM_COMMAND,
        risk_level=RiskLevel.HIGH,
        message="Shell command execution via os.system or equivalent detected",
        recommendation="Avoid os.system/os.popen; use safer alternatives",
        patterns=[
            r"os\.system\s*\(",
            r"os\.popen\s*\(",
            r"os\.execv",
            r"`[^`]+`",
        ],
    ),
    PatternRule(
        rule_id="DEP_INSTALL_008",
        risk_type=RiskType.DEPENDENCY_INSTALL,
        risk_level=RiskLevel.HIGH,
        message="Package or dependency installation detected",
        recommendation="Pre-install dependencies in the environment; do not install at runtime",
        patterns=[
            r"\bpip\s+(install|uninstall)\b",
            r"\bnpm\s+(install|uninstall|i\s)",
            r"\bapt(-get)?\s+(install|remove|purge)\b",
            r"\byum\s+(install|remove)\b",
            r"\bdnf\s+(install|remove)\b",
            r"\bpacman\s+(-S|-R)",
            r"\bpipx\s+install\b",
            r"python\s+(-m\s+)?pip\s+install\b",
        ],
    ),
    PatternRule(
        rule_id="PRIVILEGE_ESCALA_009",
        risk_type=RiskType.SYSTEM_COMMAND,
        risk_level=RiskLevel.CRITICAL,
        message="Privilege escalation or permission change detected",
        recommendation="Do not use sudo, chmod 777, or chown in tool scripts",
        patterns=[
            r"\bsudo\b",
            r"\bsu\s",
            r"chmod\s+(-R\s*)?(777|a\+rwx)",
            r"\bchown\b",
            r"setuid",
            r"seteuid",
        ],
    ),
    PatternRule(
        rule_id="SENSITIVE_LOG_010",
        risk_type=RiskType.SENSITIVE_INFO_LEAK,
        risk_level=RiskLevel.HIGH,
        message="Potential sensitive information exposure detected",
        recommendation="Do not log or output API keys, tokens, or passwords",
        patterns=[
            r"(print|write|log|logger)\.?[^(]*\([^)]*(api_key|API_KEY|password|PASSWORD|token|TOKEN|secret|SECRET|private_key|PRIVATE_KEY)",
            r"(api_key|API_KEY|password|PASSWORD|token|TOKEN|secret|SECRET)\s*=\s*[^#\n]{3,}",
        ],
    ),
    PatternRule(
        rule_id="FORK_BOMB_011",
        risk_type=RiskType.RESOURCE_ABUSE,
        risk_level=RiskLevel.CRITICAL,
        message="Fork bomb or mass process creation detected",
        recommendation="Remove fork bomb patterns from script",
        patterns=[
            r":\s*\(\s*\)\s*\{[^}]*:\|:.*};:",
            r"(os\.fork|multiprocessing\.Process)\s*\(\)",
        ],
    ),
    PatternRule(
        rule_id="INFINITE_LOOP_012",
        risk_type=RiskType.RESOURCE_ABUSE,
        risk_level=RiskLevel.MEDIUM,
        message="Infinite loop pattern detected",
        recommendation="Add exit conditions to loops; avoid while True without break",
        patterns=[
            r"\bwhile\s+True\s*:",
            r"\bwhile\s*\(\s*true\s*\)",
            r"\bfor\s*\(\s*;\s*;\s*\)",
            r"\bwhile\s+1\s*:",
        ],
    ),
    PatternRule(
        rule_id="SYSTEM_COMMAND_013",
        risk_type=RiskType.SYSTEM_COMMAND,
        risk_level=RiskLevel.MEDIUM,
        message="System command execution via Bash pipe detected",
        recommendation="Ensure piped commands do not chain dangerous operations",
        patterns=[
            r"(cat|less|more|head|tail)\s+\S+passwd\s*\|",
            r"\|\s*nc\s+",
            r"(cat|less|more|head|tail).*(passwd|shadow|ssh|credentials)\s*[\|\;]",
        ],
    ),
]


def _check_subprocess_shell_true(tree: ast.AST) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                if isinstance(node.func.value, ast.Name) and node.func.value.id == "subprocess":
                    if node.func.attr in ("run", "Popen", "call", "check_output", "check_call"):
                        for kw in node.keywords:
                            if kw.arg == "shell" and (
                                isinstance(kw.value, ast.Constant) and kw.value.value is True
                            ):
                                findings.append(RuleFinding(
                                    rule_id="SUBPROCESS_SHELL_001",
                                    risk_type=RiskType.SYSTEM_COMMAND,
                                    risk_level=RiskLevel.CRITICAL,
                                    evidence=f"subprocess.{node.func.attr}(shell=True) at line {node.lineno}",
                                    message="subprocess call with shell=True enables shell injection",
                                    recommendation="Set shell=False and pass arguments as a list",
                                ))
    return findings


def _check_python_network_calls(tree: ast.AST) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    network_libs = {"requests", "httpx", "urllib", "aiohttp", "socket"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                if isinstance(node.func.value, ast.Name) and node.func.value.id in network_libs:
                    findings.append(RuleFinding(
                        rule_id="NETWORK_AST_001",
                        risk_type=RiskType.NETWORK_ACCESS,
                        risk_level=RiskLevel.HIGH,
                        evidence=f"{node.func.value.id}.{node.func.attr}() at line {node.lineno}",
                        message=f"Network call via '{node.func.value.id}' library detected",
                        recommendation="Ensure the target domain is whitelisted",
                    ))
    return findings


def _check_sensitive_write(tree: ast.AST) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    sensitive_names = {"api_key", "password", "token", "secret", "credential",
                       "private_key", "API_KEY", "PASSWORD", "TOKEN", "SECRET"}
    output_funcs = {"print", "write", "info", "debug", "error", "warning", "log"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in output_funcs:
                for arg in node.args:
                    if isinstance(arg, ast.Name) and arg.id.lower() in {n.lower() for n in sensitive_names}:
                        findings.append(RuleFinding(
                            rule_id="SENSITIVE_AST_001",
                            risk_type=RiskType.SENSITIVE_INFO_LEAK,
                            risk_level=RiskLevel.HIGH,
                            evidence=f"{node.func.id}({arg.id}) at line {node.lineno}",
                            message=f"Sensitive variable '{arg.id}' passed to output function",
                            recommendation="Do not output sensitive values to logs or files",
                        ))
    return findings


BUILTIN_AST_RULES: list[AstRule] = [
    AstRule(
        rule_id="SUBPROCESS_SHELL_001",
        risk_type=RiskType.SYSTEM_COMMAND,
        risk_level=RiskLevel.CRITICAL,
        message="subprocess call with shell=True enables shell injection",
        recommendation="Set shell=False and pass arguments as a list",
        check=_check_subprocess_shell_true,
    ),
    AstRule(
        rule_id="NETWORK_AST_001",
        risk_type=RiskType.NETWORK_ACCESS,
        risk_level=RiskLevel.HIGH,
        message="Python network library call detected",
        recommendation="Ensure the target domain is whitelisted",
        check=_check_python_network_calls,
    ),
    AstRule(
        rule_id="SENSITIVE_AST_001",
        risk_type=RiskType.SENSITIVE_INFO_LEAK,
        risk_level=RiskLevel.HIGH,
        message="Sensitive variable passed to output function",
        recommendation="Do not output sensitive values to logs or files",
        check=_check_sensitive_write,
    ),
]
