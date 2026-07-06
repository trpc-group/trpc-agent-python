# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Security-risk rules (category: security_risk)."""

from __future__ import annotations

import re
from typing import Any
from typing import Dict
from typing import List

from .rulebase import RuleContext
from .rulebase import SEVERITY_CRITICAL
from .rulebase import SEVERITY_HIGH
from .rulebase import SEVERITY_MEDIUM
from .rulebase import is_code_line
from .rulebase import iter_added_lines
from .rulebase import make_finding

CATEGORY = "security_risk"

_RE_OS_SYSTEM = re.compile(r"\bos\.system\s*\(")
_RE_SHELL_TRUE = re.compile(r"\bsubprocess\.\w+\s*\(.*shell\s*=\s*True")
_RE_EVAL_EXEC = re.compile(r"(?<![\w.])(eval|exec)\s*\(")
_RE_PICKLE_LOADS = re.compile(r"\bpickle\.loads?\s*\(")
_RE_YAML_LOAD = re.compile(r"\byaml\.load\s*\(")
_RE_EXECUTE = re.compile(r"\.execute(?:many)?\s*\(")
_RE_SQL_DYNAMIC = re.compile(
    r"""\.execute(?:many)?\s*\(\s*(?:f["']|["'][^"']*["']\s*(?:%|\+|\.format)|\w+\s*%|\w+\s*\+)""")
_RE_VERIFY_FALSE = re.compile(r"\bverify\s*=\s*False\b")
_RE_CMD_INJECTION = re.compile(
    r"\b(?:os\.system|subprocess\.(?:run|call|Popen|check_output|check_call))"
    r"\s*\(\s*(?:f[\"']|[\"'][^\"']*[\"']\s*\+|\w+\s*\+|.*\+\s*\w+)")
_RE_MD5_PASSWORD = re.compile(r"\bmd5\s*\(")
_RE_PASSWORD_WORD = re.compile(r"(?i)passw(?:or)?d|pwd")


def check_file(ctx: RuleContext) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    path = ctx.path
    for lineno, content, _hunk in iter_added_lines(ctx.file_entry):
        if not is_code_line(content):
            continue
        if _RE_OS_SYSTEM.search(content):
            findings.append(
                make_finding("SEC001", CATEGORY, SEVERITY_HIGH, 0.9, path, lineno,
                             "Command execution via os.system", content,
                             "Replace os.system with subprocess.run([...], shell=False) and pass "
                             "arguments as a list; never build shell command strings."))
        if _RE_SHELL_TRUE.search(content):
            findings.append(
                make_finding("SEC002", CATEGORY, SEVERITY_HIGH, 0.9, path, lineno,
                             "subprocess called with shell=True", content,
                             "Drop shell=True and pass the command as an argument list to avoid "
                             "shell injection."))
        if _RE_EVAL_EXEC.search(content):
            findings.append(
                make_finding("SEC003", CATEGORY, SEVERITY_HIGH, 0.75, path, lineno,
                             "Dynamic code execution via eval/exec", content,
                             "Avoid eval/exec on data; use ast.literal_eval, a dispatch table or an "
                             "explicit parser instead."))
        if _RE_PICKLE_LOADS.search(content):
            findings.append(
                make_finding("SEC004", CATEGORY, SEVERITY_HIGH, 0.85, path, lineno,
                             "Unsafe deserialization via pickle", content,
                             "Never unpickle untrusted data; prefer json or a schema-validated "
                             "format."))
        if _RE_YAML_LOAD.search(content) and "SafeLoader" not in content and "safe_load" not in content:
            findings.append(
                make_finding("SEC005", CATEGORY, SEVERITY_MEDIUM, 0.85, path, lineno,
                             "yaml.load without SafeLoader", content,
                             "Use yaml.safe_load(...) or pass Loader=yaml.SafeLoader."))
        if _RE_SQL_DYNAMIC.search(content):
            findings.append(
                make_finding("SEC006", CATEGORY, SEVERITY_CRITICAL, 0.85, path, lineno,
                             "SQL statement built from dynamic strings (SQL injection risk)", content,
                             "Use parameterized queries: cursor.execute(\"... WHERE name = %s\", "
                             "(name,)) instead of f-strings or string concatenation."))
        if _RE_VERIFY_FALSE.search(content):
            findings.append(
                make_finding("SEC008", CATEGORY, SEVERITY_HIGH, 0.9, path, lineno,
                             "TLS certificate verification disabled (verify=False)", content,
                             "Keep certificate verification on; pin an internal CA bundle via "
                             "verify=\"/path/ca.pem\" when needed."))
        if _RE_CMD_INJECTION.search(content):
            findings.append(
                make_finding("SEC009", CATEGORY, SEVERITY_CRITICAL, 0.88, path, lineno,
                             "Shell command built from dynamic input (command injection risk)", content,
                             "Never concatenate or interpolate user input into a command string; "
                             "pass an argv list and validate inputs."))
        if _RE_MD5_PASSWORD.search(content) and _RE_PASSWORD_WORD.search(content):
            findings.append(
                make_finding("SEC007", CATEGORY, SEVERITY_MEDIUM, 0.6, path, lineno,
                             "Possible weak password hashing with MD5", content,
                             "Use a password KDF such as bcrypt, scrypt or argon2 instead of MD5."))
    return findings
