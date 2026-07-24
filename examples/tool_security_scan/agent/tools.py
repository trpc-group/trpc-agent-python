"""Tool execution security scanning and filter/monitoring utilities.

Provides:
- Pattern-based security scanning for tool inputs
- Filter/block rules for dangerous operations
- Execution monitoring and audit logging
"""

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime


# Security scan patterns — extendable per deployment needs
SECURITY_PATTERNS = {
    "shell_injection": re.compile(r"[;&|`$(){}\[\]]|rm\s+-rf|sudo\b|chmod|mkfs", re.I),
    "path_traversal": re.compile(r"\.\.[/\\]|~[/\\]|/etc/(passwd|shadow|sudoers)", re.I),
    "network_exfil": re.compile(r"curl\b|wget\b|nc\s+-|socat|ssh\s+-L", re.I),
    "code_execution": re.compile(r"__import__|exec\s*\(|eval\s*\(|compile\s*\(", re.I),
    "env_access": re.compile(r"(?<!\\w)(AWS_|AZURE_|GCP_|OPENAI_API_KEY|DATABASE_URL)", re.I),
}

INTEGRITY_PATTERNS = {
    "large_size": lambda x: len(str(x)) > 10_000,
    "json_depth": lambda x: _json_depth(x) > 20,
    "repeated_input": lambda x, seen: hash(str(x)) in seen,
}


def _json_depth(obj, depth=0):
    if isinstance(obj, dict):
        return max((_json_depth(v, depth + 1) for v in obj.values()), default=depth)
    if isinstance(obj, list):
        return max((_json_depth(v, depth) for v in obj), default=depth)
    return depth


@dataclass
class ScanResult:
    tool_name: str
    passed: bool
    violations: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    scanned_at: str = field(default_factory=lambda: datetime.now().isoformat())


def scan_tool_input(tool_name: str, tool_args: dict,
                    allow_patterns: list[str] | None = None) -> ScanResult:
    """Scan tool input against security patterns.

    Args:
        tool_name: Name of the tool being executed.
        tool_args: Arguments being passed to the tool.
        allow_patterns: Optional regex patterns to whitelist.

    Returns a ScanResult with violations if any patterns match.
    """
    args_str = json.dumps(tool_args)
    result = ScanResult(tool_name=tool_name)
    allow_re = [re.compile(p) for p in (allow_patterns or [])]

    for category, pattern in SECURITY_PATTERNS.items():
        matches = pattern.findall(args_str)
        for match in matches:
            match_str = match if isinstance(match, str) else match
            if any(a.search(match_str) for a in allow_re):
                continue
            result.violations.append({
                "category": category,
                "match": match_str[:200],
                "description": f"Potential {category.replace('_', ' ')} detected",
            })

    # Integrity checks
    if INTEGRITY_PATTERNS["large_size"](args_str):
        result.warnings.append({"category": "large_size", "description": "Input exceeds 10KB"})

    result.passed = len(result.violations) == 0
    return result


def filter_tool_call(tool_name: str, tool_args: dict,
                     blocked_tools: list[str] | None = None) -> dict:
    """Filter/block tool calls based on security policy.

    Returns: {"allowed": bool, "reason": str, "sanitized_args": dict}
    """
    blocked = (blocked_tools or []) + ["eval", "exec", "__import__", "os.system"]

    if tool_name in blocked:
        return {"allowed": False, "reason": f"Tool '{tool_name}' is blocked", "sanitized_args": {}}

    scan = scan_tool_input(tool_name, tool_args)
    if not scan.passed:
        return {
            "allowed": False,
            "reason": f"Security violation: {[v['category'] for v in scan.violations]}",
            "sanitized_args": {},
        }

    return {"allowed": True, "reason": "", "sanitized_args": tool_args}
