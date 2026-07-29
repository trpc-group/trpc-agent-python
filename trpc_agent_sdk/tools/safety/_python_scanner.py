# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Python AST-based safety scanner.

Walks the parsed AST of a Python script and applies rules that detect
dangerous file operations, network egress, process/system calls,
dependency installation, resource abuse and secret leakage.

Using the ``ast`` module makes detection far more robust than naive
regex matching: we can resolve ``os.system("rm -rf /")`` even when it
is split across variables or wrapped in helper functions.
"""

from __future__ import annotations

import ast
import re
from typing import Any
from typing import Optional
from urllib.parse import urlparse

from ._models import Decision
from ._models import Finding
from ._models import RiskCategory
from ._models import RiskLevel
from ._models import ScriptType
from ._rules import Rule
from ._rules import ScanContext
from ._rules import global_rule_registry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Calls that spawn subprocesses or shells.
_SUBPROCESS_CALLS = {
    ("subprocess", "run"),
    ("subprocess", "call"),
    ("subprocess", "Popen"),
    ("subprocess", "check_call"),
    ("subprocess", "check_output"),
    ("subprocess", "getoutput"),
    ("subprocess", "getstatusoutput"),
    ("os", "system"),
    ("os", "popen"),
    ("os", "spawnl"),
    ("os", "spawnle"),
    ("os", "spawnlp"),
    ("os", "spawnlpe"),
    ("os", "spawnv"),
    ("os", "spawnvp"),
    ("os", "spawnve"),
    ("os", "spawnvpe"),
    ("os", "execv"),
    ("os", "execve"),
    ("os", "execvp"),
    ("os", "execvpe"),
    ("os", "execl"),
    ("os", "execle"),
    ("os", "execlp"),
    ("os", "execlpe"),
    ("pty", "spawn"),
}

# Calls that perform network I/O.
_NETWORK_CALLS = {
    ("requests", "get"),
    ("requests", "post"),
    ("requests", "put"),
    ("requests", "delete"),
    ("requests", "patch"),
    ("requests", "head"),
    ("requests", "request"),
    ("httpx", "get"),
    ("httpx", "post"),
    ("httpx", "put"),
    ("httpx", "delete"),
    ("httpx", "patch"),
    ("httpx", "head"),
    ("httpx", "request"),
    ("urllib", "urlopen"),
    ("urllib", "urlretrieve"),
    ("urllib.request", "urlopen"),
    ("urllib.request", "urlretrieve"),
    ("aiohttp", "ClientSession"),
    ("socket", "create_connection"),
    ("socket", "connect"),
}

# Calls that delete files/directories.
_DELETE_CALLS = {
    ("shutil", "rmtree"),
    ("os", "remove"),
    ("os", "unlink"),
    ("os", "rmdir"),
    ("os", "removedirs"),
    ("pathlib", "Path"),  # used with .unlink / .rmdir below
}

# Calls that access file paths (used by PyDangerousFileOpsRule).
_PATH_ACCESS_CALLS = {
    ("pathlib", "Path"),
    ("os", "listdir"), ("os", "scandir"), ("os", "walk"),
    ("os", "stat"), ("os", "lstat"),
    ("os.path", "exists"), ("os.path", "isfile"), ("os.path", "isdir"),
    ("os.path", "getsize"), ("os.path", "getatime"),
}

# Dependency-install command prefixes.
_INSTALL_PREFIXES = ("pip install", "pip3 install", "python -m pip install",
                     "npm install", "npm i ", "yarn add", "apt install",
                     "apt-get install", "brew install", "conda install",
                     "pip uninstall", "npm uninstall")

# --- Taint tracking: variables that may hold secrets ---

# Variable names that look like they hold secrets.
_SECRET_NAME_RE = re.compile(
    r"(?i)(?:api[_-]?key|secret|token|password|passwd|credential|"
    r"private[_-]?key|access[_-]?key|auth)"
)

# Method names that write or transmit data (potential secret-leak sinks).
_OUTPUT_METHODS = frozenset({
    "write", "write_text", "write_bytes",
    "send", "sendall",
    "debug", "info", "warning", "error", "critical", "exception", "log",
})


def _dotted_call(node: ast.Call) -> Optional[tuple[str, str]]:
    """Return ``(module, attr)`` for a call like ``os.system(...)``.

    Handles both ``os.system(...)`` and ``subprocess.run(...)`` style calls.
    Returns ``None`` for bare calls like ``print(...)``.
    """
    func = node.func
    if isinstance(func, ast.Attribute):
        if isinstance(func.value, ast.Name):
            return (func.value.id, func.attr)
        # e.g. urllib.request.urlopen  ->  ("urllib.request", "urlopen")
        if isinstance(func.value, ast.Attribute):
            parts: list[str] = []
            cur = func.value
            while isinstance(cur, ast.Attribute):
                parts.append(cur.attr)
                cur = cur.value
            if isinstance(cur, ast.Name):
                parts.append(cur.id)
                parts.reverse()
                return (".".join(parts), func.attr)
    return None


def _get_str_arg(node: ast.Call, index: int = 0) -> Optional[str]:
    """Extract a string literal from the *index*-th positional arg."""
    if index < len(node.args):
        arg = node.args[index]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return arg.value
        if isinstance(arg, ast.JoinedStr):
            # f-string — reconstruct best-effort
            parts = []
            for val in arg.values:
                if isinstance(val, ast.Constant) and isinstance(val.value, str):
                    parts.append(val.value)
                else:
                    parts.append("{...}")
            return "".join(parts)
    return None


def _get_int_arg(node: ast.Call, index: int = 0) -> Optional[int]:
    """Extract an integer literal from the *index*-th positional arg.

    Handles plain ``int`` constants (e.g. ``time.sleep(3600)``) and
    digit-only string constants (e.g. ``time.sleep("3600")``).
    ``bool`` values are excluded since ``bool`` is a subclass of ``int``.
    """
    if index < len(node.args):
        arg = node.args[index]
        if isinstance(arg, ast.Constant):
            if isinstance(arg.value, int) and not isinstance(arg.value, bool):
                return arg.value
            if isinstance(arg.value, str) and arg.value.isdigit():
                return int(arg.value)
        # Handle negative literals:  time.sleep(-1)
        if isinstance(arg, ast.UnaryOp) and isinstance(arg.op, ast.USub):
            if isinstance(arg.operand, ast.Constant):
                if isinstance(arg.operand.value, int) and not isinstance(arg.operand.value, bool):
                    return -arg.operand.value
    return None


def _get_all_str_args(node: ast.Call) -> list[str]:
    """Return all string-literal positional args."""
    result = []
    for arg in node.args:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            result.append(arg.value)
        elif isinstance(arg, ast.JoinedStr):
            parts = []
            for val in arg.values:
                if isinstance(val, ast.Constant) and isinstance(val.value, str):
                    parts.append(val.value)
                else:
                    parts.append("{...}")
            result.append("".join(parts))
        elif isinstance(arg, ast.List):
            for elt in arg.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    result.append(elt.value)
    return result


def _extract_domain(url: str) -> str:
    """Extract the domain (host) from a URL string."""
    if not url:
        return ""
    if "://" not in url:
        url = "http://" + url
    try:
        parsed = urlparse(url)
        return parsed.hostname or ""
    except Exception:  # pylint: disable=broad-except
        return ""


def _get_source_segment(source: str, node: ast.AST) -> str:
    """Return the source line for *node*, stripped of leading whitespace."""
    try:
        seg = ast.get_source_segment(source, node)
        if seg:
            return seg.strip()[:200]
    except Exception:  # pylint: disable=broad-except
        pass
    return ""


# ---------------------------------------------------------------------------
# Taint-tracking helpers
# ---------------------------------------------------------------------------

def _str_value(node: ast.AST) -> Optional[str]:
    """Extract a string literal from an arbitrary AST node."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for val in node.values:
            if isinstance(val, ast.Constant) and isinstance(val.value, str):
                parts.append(val.value)
            else:
                parts.append("{...}")
        return "".join(parts)
    return None


def _expr_is_sensitive(
    node: ast.AST,
    tainted: set[str],
    patterns: list[re.Pattern],
) -> bool:
    """Return True if *node* evaluates to a sensitive value.

    A value is sensitive when it is (or derives from):

    * a variable already in *tainted*;
    * a variable whose name matches :data:`_SECRET_NAME_RE`;
    * a string literal that matches one of the compiled secret *patterns*;
    * a call to ``os.getenv`` / ``os.environ.get`` with a secret-looking key;
    * a subscript ``os.environ["API_KEY"]`` with a secret-looking key.
    """
    if isinstance(node, ast.Name):
        return node.id in tainted or bool(_SECRET_NAME_RE.search(node.id))
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        for pat in patterns:
            if pat.search(node.value):
                return True
        return "PRIVATE KEY-----" in node.value
    if isinstance(node, ast.Call):
        dotted = _dotted_call(node)
        if dotted in (("os", "getenv"), ("os.environ", "get")) and node.args:
            key = _get_str_arg(node, 0)
            if key and _SECRET_NAME_RE.search(key):
                return True
        if (isinstance(node.func, ast.Name) and node.func.id == "getenv"
                and node.args):
            key = _get_str_arg(node, 0)
            if key and _SECRET_NAME_RE.search(key):
                return True
    if isinstance(node, ast.Subscript):
        target = node.value
        is_environ = (
            (isinstance(target, ast.Attribute)
             and isinstance(target.value, ast.Name)
             and target.value.id == "os" and target.attr == "environ")
            or (isinstance(target, ast.Name) and target.id == "environ")
        )
        if is_environ:
            key = _str_value(node.slice)
            if key and _SECRET_NAME_RE.search(key):
                return True
    return any(
        _expr_is_sensitive(child, tainted, patterns)
        for child in ast.iter_child_nodes(node)
    )


def _collect_tainted_names(
    tree: ast.AST,
    patterns: list[re.Pattern],
) -> set[str]:
    """Collect names of variables that hold sensitive values.

    Runs two passes so that chained assignments
    (``a = b = os.getenv('API_KEY')``) propagate correctly.
    """
    tainted: set[str] = set()
    assignments = [
        n for n in ast.walk(tree)
        if isinstance(n, (ast.Assign, ast.AnnAssign, ast.NamedExpr))
    ]
    for _ in range(2):
        for node in assignments:
            if not _expr_is_sensitive(node.value, tainted, patterns):
                continue
            if isinstance(node, ast.Assign):
                targets = node.targets
            else:
                targets = [node.target]
            for target in targets:
                for child in ast.walk(target):
                    if isinstance(child, ast.Name):
                        tainted.add(child.id)
    return tainted


def _expr_references_tainted(node: ast.AST, tainted: set[str]) -> bool:
    """Return True if *node* references any variable in *tainted*."""
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and child.id in tainted:
            return True
    return False


def _is_output_call(node: ast.Call) -> bool:
    """Return True if *node* writes or transmits data (a leak sink)."""
    if isinstance(node.func, ast.Name) and node.func.id == "print":
        return True
    if isinstance(node.func, ast.Attribute) and node.func.attr in _OUTPUT_METHODS:
        return True
    return False


# ===========================================================================
# Python rules
# ===========================================================================

class PyDangerousFileOpsRule(Rule):
    """Detect dangerous file operations: recursive delete, credential reads."""

    rule_id = "PY-DANGEROUS-FILE-OPS"
    description = "Dangerous file operation: recursive delete, system directory access, or credential file read"
    category = RiskCategory.DANGEROUS_FILE_OPS
    default_risk_level = RiskLevel.HIGH
    default_decision = Decision.DENY
    applies_to = (ScriptType.PYTHON,)

    def check(self, ctx: ScanContext) -> list[Finding]:
        findings: list[Finding] = []
        tree = ctx.cached_tree
        if tree is None:
            return findings

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            dotted = _dotted_call(node)
            line_no = getattr(node, "lineno", None)

            # shutil.rmtree — recursive directory deletion
            if dotted == ("shutil", "rmtree"):
                path_arg = _get_str_arg(node, 0) or ""
                if ctx.policy.is_system_dir(path_arg) or not path_arg:
                    seg = _get_source_segment(ctx.script, node)
                    findings.append(self._make_finding(
                        seg or f"shutil.rmtree({path_arg!r})",
                        line_no,
                        "Avoid recursive deletion of system directories; "
                        "restrict to explicitly scoped project paths.",
                    ))
                elif path_arg.startswith(("/", "~", "C:\\")):
                    seg = _get_source_segment(ctx.script, node)
                    findings.append(self._make_finding(
                        seg or f"shutil.rmtree({path_arg!r})",
                        line_no,
                        "Recursive deletion of absolute paths is dangerous; "
                        "use a sandboxed, relative path.",
                    ))

            # os.remove / os.unlink / os.rmdir on protected paths
            if dotted in (("os", "remove"), ("os", "unlink"), ("os", "rmdir"), ("os", "removedirs")):
                path_arg = _get_str_arg(node, 0) or ""
                if ctx.policy.is_system_dir(path_arg) or ctx.policy.is_path_forbidden(path_arg):
                    seg = _get_source_segment(ctx.script, node)
                    findings.append(self._make_finding(
                        seg or f"{dotted[0]}.{dotted[1]}({path_arg!r})",
                        line_no,
                        "Do not delete protected system files or credential files.",
                    ))

            # open() / pathlib.Path() on forbidden paths (.env, ~/.ssh, …)
            # Also check os.listdir / os.scandir / os.walk / os.stat etc.
            # Note: open() is a bare call (dotted is None), so we must
            # check it *before* the "dotted is None" guard below.
            is_open_call = (
                isinstance(node.func, ast.Name) and node.func.id == "open"
            )
            if dotted in _PATH_ACCESS_CALLS or is_open_call:
                path_arg = _get_str_arg(node, 0) or ""
                if ctx.policy.is_path_forbidden(path_arg):
                    seg = _get_source_segment(ctx.script, node)
                    findings.append(self._make_finding(
                        seg or f"open({path_arg!r})",
                        line_no,
                        "Reading credential files (.env, ~/.ssh, etc.) is forbidden.",
                    ))

            if dotted is None:
                continue
        return findings


class PyNetworkEgressRule(Rule):
    """Detect outbound network calls to non-whitelisted domains."""

    rule_id = "PY-NETWORK-EGRESS"
    description = "Outbound network call to a non-whitelisted domain"
    category = RiskCategory.NETWORK_EGRESS
    default_risk_level = RiskLevel.MEDIUM
    default_decision = Decision.NEEDS_HUMAN_REVIEW
    applies_to = (ScriptType.PYTHON,)

    def check(self, ctx: ScanContext) -> list[Finding]:
        findings: list[Finding] = []
        tree = ctx.cached_tree
        if tree is None:
            return findings

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            dotted = _dotted_call(node)
            if dotted is None:
                continue
            if dotted not in _NETWORK_CALLS:
                continue
            line_no = getattr(node, "lineno", None)
            url = _get_str_arg(node, 0) or ""
            domain = _extract_domain(url)
            if domain and not ctx.policy.is_domain_allowed(domain):
                seg = _get_source_segment(ctx.script, node)
                findings.append(self._make_finding(
                    seg or f"{dotted[0]}.{dotted[1]}({url!r})",
                    line_no,
                    f"Domain '{domain}' is not in the whitelist. "
                    "Add it to allowed_domains in the policy file.",
                    risk_level=RiskLevel.HIGH,
                    decision=Decision.DENY,
                ))
            elif not domain:
                # Network call with a non-literal URL — flag for review.
                seg = _get_source_segment(ctx.script, node)
                findings.append(self._make_finding(
                    seg or f"{dotted[0]}.{dotted[1]}(...)",
                    line_no,
                    "Network call with a dynamic/non-literal URL cannot be "
                    "verified against the whitelist; requires human review.",
                    risk_level=RiskLevel.LOW,
                    decision=Decision.NEEDS_HUMAN_REVIEW,
                ))
        return findings


# Calls that always invoke a shell — can never be made safe.
_ALWAYS_SHELL_CALLS = {
    ("os", "system"),
    ("os", "popen"),
    ("subprocess", "getoutput"),
    ("subprocess", "getstatusoutput"),
}

# Calls that replace the current process — always dangerous in agent context.
_EXEC_CALLS = {
    ("os", "execv"), ("os", "execve"), ("os", "execvp"), ("os", "execvpe"),
    ("os", "execl"), ("os", "execle"), ("os", "execlp"), ("os", "execlpe"),
}


class PyProcessSystemRule(Rule):
    """Detect subprocess / os.system / shell execution calls.

    Severity tiers:

    1. **CRITICAL / deny** — privilege escalation (``sudo``, ``su``).
    2. **HIGH / deny** — ``shell=True``, ``os.system``, ``os.popen``,
       ``os.exec*`` (always use a shell or replace the process), or a
       subprocess call with a *string* argument (potential injection).
    3. **MEDIUM / needs_human_review** — ``subprocess.run(['ls'])`` with a
       *list* argument.  List form is immune to shell injection, so it is
       not auto-denied; a human should still confirm the command is safe.
    """

    rule_id = "PY-PROCESS-SYSTEM"
    description = "Subprocess or system command execution detected"
    category = RiskCategory.PROCESS_SYSTEM
    default_risk_level = RiskLevel.HIGH
    default_decision = Decision.DENY
    applies_to = (ScriptType.PYTHON,)

    def check(self, ctx: ScanContext) -> list[Finding]:
        findings: list[Finding] = []
        tree = ctx.cached_tree
        if tree is None:
            return findings

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            dotted = _dotted_call(node)
            if dotted is None:
                continue
            if dotted not in _SUBPROCESS_CALLS:
                continue
            line_no = getattr(node, "lineno", None)
            cmd_args = _get_all_str_args(node)
            combined = " ".join(cmd_args)

            # 1. Privilege escalation — always CRITICAL deny
            if any(kw in combined for kw in ("sudo", "su -", "su root")):
                seg = _get_source_segment(ctx.script, node)
                findings.append(self._make_finding(
                    seg or f"{dotted[0]}.{dotted[1]}({combined!r})",
                    line_no,
                    "Privilege escalation (sudo/su) in subprocess call is forbidden.",
                    risk_level=RiskLevel.CRITICAL,
                ))
                continue

            # 2. shell=True — always HIGH deny (shell injection risk)
            if dotted[0] == "subprocess" and self._has_shell_true(node):
                seg = _get_source_segment(ctx.script, node)
                findings.append(self._make_finding(
                    seg or f"{dotted[0]}.{dotted[1]}(..., shell=True)",
                    line_no,
                    "shell=True with a string command is vulnerable to shell "
                    "injection; pass a list of arguments instead.",
                    risk_level=RiskLevel.HIGH,
                ))
                continue

            # 3. os.system / os.popen / subprocess.getoutput — always use
            #    a shell internally, so they can never be made safe.
            if dotted in _ALWAYS_SHELL_CALLS or dotted in _EXEC_CALLS:
                seg = _get_source_segment(ctx.script, node)
                findings.append(self._make_finding(
                    seg or f"{dotted[0]}.{dotted[1]}(...)",
                    line_no,
                    f"{dotted[0]}.{dotted[1]}() always invokes a shell or "
                    "replaces the process; use subprocess.run with a list "
                    "argument instead.",
                    risk_level=RiskLevel.HIGH,
                ))
                continue

            # 4. subprocess.* with a LIST argument — no shell injection
            #    possible.  Downgrade to needs_human_review.
            if node.args and isinstance(node.args[0], ast.List):
                seg = _get_source_segment(ctx.script, node)
                findings.append(self._make_finding(
                    seg or f"{dotted[0]}.{dotted[1]}([...])",
                    line_no,
                    "Subprocess call with a list argument is safer (no shell "
                    "injection), but still requires human review to confirm "
                    "the command is safe.",
                    risk_level=RiskLevel.MEDIUM,
                    decision=Decision.NEEDS_HUMAN_REVIEW,
                ))
                continue

            # 5. subprocess.* with a string argument (no shell=True) —
            #    still deny because string form may enable injection.
            seg = _get_source_segment(ctx.script, node)
            findings.append(self._make_finding(
                seg or f"{dotted[0]}.{dotted[1]}(...)",
                line_no,
                "Subprocess call with a string argument is vulnerable to "
                "injection; pass a list of arguments instead.",
                risk_level=RiskLevel.HIGH,
            ))
        return findings

    @staticmethod
    def _has_shell_true(node: ast.Call) -> bool:
        for kw in node.keywords:
            if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                return True
        return False


class PyDependencyInstallRule(Rule):
    """Detect package installation commands (pip/npm/apt install)."""

    rule_id = "PY-DEPENDENCY-INSTALL"
    description = "Package installation command that mutates the runtime environment"
    category = RiskCategory.DEPENDENCY_INSTALL
    default_risk_level = RiskLevel.HIGH
    default_decision = Decision.DENY
    applies_to = (ScriptType.PYTHON,)

    def check(self, ctx: ScanContext) -> list[Finding]:
        findings: list[Finding] = []
        tree = ctx.cached_tree
        if tree is None:
            return findings

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            dotted = _dotted_call(node)
            if dotted is None or dotted not in _SUBPROCESS_CALLS:
                continue
            line_no = getattr(node, "lineno", None)
            cmd_args = _get_all_str_args(node)
            combined = " ".join(cmd_args)
            for prefix in _INSTALL_PREFIXES:
                if prefix in combined:
                    seg = _get_source_segment(ctx.script, node)
                    findings.append(self._make_finding(
                        seg or combined[:120],
                        line_no,
                        "Dependency installation at runtime changes the "
                        "execution environment; declare dependencies in the "
                        "project manifest instead.",
                    ))
                    break
        return findings


class PyResourceAbuseRule(Rule):
    """Detect resource-abuse patterns: infinite loops, fork, huge sleeps."""

    rule_id = "PY-RESOURCE-ABUSE"
    description = "Resource-abuse pattern: infinite loop, fork bomb, or excessive sleep"
    category = RiskCategory.RESOURCE_ABUSE
    default_risk_level = RiskLevel.HIGH
    default_decision = Decision.DENY
    applies_to = (ScriptType.PYTHON,)

    def check(self, ctx: ScanContext) -> list[Finding]:
        findings: list[Finding] = []
        tree = ctx.cached_tree
        if tree is None:
            return findings

        for node in ast.walk(tree):
            line_no = getattr(node, "lineno", None)

            # while True / while 1 without a break in the body
            if isinstance(node, ast.While):
                if isinstance(node.test, ast.Constant) and node.test.value in (True, 1):
                    has_break = any(isinstance(n, ast.Break) for n in ast.walk(node))
                    if not has_break:
                        seg = _get_source_segment(ctx.script, node)
                        findings.append(self._make_finding(
                            seg or "while True: ...",
                            line_no,
                            "Infinite loop without a break statement will "
                            "consume CPU indefinitely; add a termination condition.",
                        ))

            # os.fork()
            if isinstance(node, ast.Call):
                dotted = _dotted_call(node)
                if dotted == ("os", "fork"):
                    seg = _get_source_segment(ctx.script, node)
                    findings.append(self._make_finding(
                        seg or "os.fork()",
                        line_no,
                        "os.fork() can be used to create fork bombs; avoid "
                        "in agent-generated scripts.",
                    ))
                # Large sleep values
                if dotted == ("time", "sleep"):
                    val = _get_int_arg(node, 0)
                    if val is not None and val > ctx.policy.max_sleep_seconds:
                        seg = _get_source_segment(ctx.script, node)
                        findings.append(self._make_finding(
                            seg or f"time.sleep({val})",
                            line_no,
                            "Excessively long sleep blocks the event loop; "
                            "use a shorter timeout with retry logic.",
                            risk_level=RiskLevel.LOW,
                            decision=Decision.NEEDS_HUMAN_REVIEW,
                        ))

            # Huge range in a loop that writes data
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "range":
                args = [a for a in node.args if isinstance(a, ast.Constant)]
                for a in args:
                    if isinstance(a.value, int) and a.value > ctx.policy.max_range_size:
                        seg = _get_source_segment(ctx.script, node)
                        findings.append(self._make_finding(
                            seg or f"range({a.value})",
                            line_no,
                            "Very large iteration count may exhaust memory/CPU.",
                            risk_level=RiskLevel.LOW,
                            decision=Decision.NEEDS_HUMAN_REVIEW,
                        ))
        return findings


class PySecretLeakRule(Rule):
    """Detect hardcoded secrets written to logs, files or network."""

    rule_id = "PY-SECRET-LEAK"
    description = "Hardcoded secret (API key, token, password, private key) detected"
    category = RiskCategory.SECRET_LEAK
    default_risk_level = RiskLevel.CRITICAL
    default_decision = Decision.DENY
    applies_to = (ScriptType.PYTHON,)

    def check(self, ctx: ScanContext) -> list[Finding]:
        findings: list[Finding] = []
        compiled = ctx.compiled_secrets
        if compiled is None:
            compiled = [re.compile(p) for p in ctx.policy.secret_patterns]

        # First: scan each source line for secret patterns (catches
        # assignments like ``api_key = 'sk-...'`` that the constant-only
        # walk below would miss).
        seen_lines: set[int] = set()
        lines = ctx.cached_lines if ctx.cached_lines is not None else ctx.script.splitlines()
        for idx, line in enumerate(lines, start=1):
            for pattern in compiled:
                if pattern.search(line):
                    if idx not in seen_lines:
                        seen_lines.add(idx)
                        findings.append(self._make_finding(
                            self._redact(line.strip()[:200]),
                            idx,
                            "Hardcoded secret detected; load secrets from "
                            "environment variables or a secret manager instead.",
                        ))
                    break

        # Second: walk the AST for string constants (catches secrets in
        # function-call arguments and multi-line strings).
        tree = ctx.cached_tree
        if tree is None:
            return findings

        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                line_no = getattr(node, "lineno", None)
                if line_no in seen_lines:
                    continue
                for pattern in compiled:
                    match = pattern.search(node.value)
                    if match:
                        seen_lines.add(line_no)
                        evidence = self._redact(node.value[:120])
                        findings.append(self._make_finding(
                            evidence,
                            line_no,
                            "Hardcoded secret detected; load secrets from "
                            "environment variables or a secret manager instead.",
                        ))
                        break

        # Third: taint tracking — detect secrets propagated through
        # variables (e.g. ``key = os.getenv('API_KEY'); print(key)``)
        # and then written to output sinks (print, write, send, log, ...).
        tainted = _collect_tainted_names(tree, compiled)
        if tainted:
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if not _is_output_call(node):
                    continue
                args = list(node.args) + [kw.value for kw in node.keywords]
                if not any(_expr_references_tainted(a, tainted) for a in args):
                    continue
                line_no = getattr(node, "lineno", None)
                if line_no in seen_lines:
                    continue
                seen_lines.add(line_no)
                seg = _get_source_segment(ctx.script, node)
                findings.append(self._make_finding(
                    self._redact(seg) if seg else
                    f"[REDACTED sensitive value passed to output at line {line_no}]",
                    line_no,
                    "Sensitive value may be written or transmitted; "
                    "pass credentials through a scoped secret provider "
                    "instead of logging or sending them directly.",
                    risk_level=RiskLevel.HIGH,
                ))
        return findings

    @staticmethod
    def _redact(text: str) -> str:
        """Mask the middle portion of a potential secret for evidence."""
        if len(text) <= 12:
            return text[:2] + "***"
        return text[:6] + "..." + text[-4:]


# ---------------------------------------------------------------------------
# Register all built-in Python rules
# ---------------------------------------------------------------------------

def _register_python_rules() -> None:
    """Register the built-in Python rules with the global registry."""
    for rule_cls in (
        PyDangerousFileOpsRule,
        PyNetworkEgressRule,
        PyProcessSystemRule,
        PyDependencyInstallRule,
        PyResourceAbuseRule,
        PySecretLeakRule,
    ):
        instance = rule_cls()
        global_rule_registry.register(instance)


_register_python_rules()
