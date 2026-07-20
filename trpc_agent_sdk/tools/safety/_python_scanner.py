# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""AST-based Python script scanner for the Tool Script Safety Guard.

This module complements the regex-based rules in ``_rules.py``.  Whereas the
built-in rules perform pattern matching against raw source text, the classes
here parse the Python source into an Abstract Syntax Tree and walk it with
context-aware visitors.  This catches obfuscation patterns that regex cannot,
such as::

    from os import system as sys_call       # alias resolution
    sys_call("id")                           # → detected as os.system

    getattr(__import__("os"), "system")("id")  # → detected as os.system

    requests = __import__("requests")        # → import tracking
    requests.get("http://evil.com")          # → detected with domain extraction

Design:
    The scanner walks the AST exactly **once** and collects every observation
    into a flat list of ``PythonScanFinding`` named tuples.  Rules in
    ``_rules.py`` query these findings via a few simple helper functions
    (``has_python_call``, ``get_python_urls``, ``get_python_file_reads``, …).
    This keeps the heavy lifting in one place while remaining compatible with
    the existing rule-callable contract.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Set
from typing import Tuple

# ---------------------------------------------------------------------------
# Finding dataclass  (lightweight — NOT the same as SafetyFinding)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PythonScanFinding:
    """A single observation produced by the AST walker.

    Attributes:
        kind: One of ``"call"``, ``"import"``, ``"file_read"``, ``"file_write"``,
              ``"file_delete"``, ``"url"``, ``"tainted_var"``, ``"secret_in_output"``,
              ``"loop"``, ``"fork"``, ``"eval_exec"``, ``"sleep"``.
        canonical_name: Fully-qualified name for calls (e.g. ``"os.system"``).
        line_number: 1-based source line.
        evidence: The relevant source snippet (truncated).
        extra: Additional structured data (e.g. URL string, file path).
    """

    kind: str
    canonical_name: str = ""
    line_number: int = 0
    evidence: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════════════
# Canonical name sets — grouped by risk category
# ═══════════════════════════════════════════════════════════════════════════

_DANGEROUS_FILE_CALLS: Set[str] = {
    "os.remove",
    "os.unlink",
    "os.rmdir",
    "os.removedirs",
    "shutil.rmtree",
    "shutil.move",
    "shutil.copy",
    "pathlib.Path.unlink",
    "pathlib.Path.rmdir",
}

_FILE_READ_CALLS: Set[str] = {
    "open",
    "io.open",
    "pathlib.Path.read_text",
    "pathlib.Path.read_bytes",
    "pathlib.Path.open",
    "builtins.open",
}

_FILE_WRITE_CALLS: Set[str] = {
    "pathlib.Path.write_text",
    "pathlib.Path.write_bytes",
    "shutil.copyfile",
    "shutil.copy",
}

_PROCESS_CALLS: Set[str] = {
    "os.system",
    "os.popen",
    "os.popen2",
    "os.popen3",
    "os.popen4",
    "subprocess.run",
    "subprocess.Popen",
    "subprocess.call",
    "subprocess.check_call",
    "subprocess.check_output",
    "pty.spawn",
    "os.execv",
    "os.execl",
    "os.execve",
    "os.execle",
    "os.execvp",
    "os.execlp",
    "os.execvpe",
    "os.spawnl",
    "os.spawnle",
    "os.spawnlp",
    "os.spawnlpe",
    "os.spawnv",
    "os.spawnve",
    "os.spawnvp",
    "os.spawnvpe",
}

_PRIVILEGE_CALLS: Set[str] = {
    "os.setuid",
    "os.setgid",
    "os.seteuid",
    "os.setegid",
    "os.setreuid",
    "os.setregid",
    "os.chown",
    "os.chmod",
    "os.fchown",
    "os.fchmod",
    "os.lchown",
}

_NETWORK_CALLS: Set[str] = {
    "requests.get",
    "requests.post",
    "requests.put",
    "requests.patch",
    "requests.delete",
    "requests.head",
    "requests.options",
    "requests.request",
    "requests.Session.get",
    "requests.Session.post",
    "requests.Session.put",
    "requests.api.get",
    "requests.api.post",
    "httpx.get",
    "httpx.post",
    "httpx.put",
    "httpx.AsyncClient.get",
    "httpx.Client.get",
    "aiohttp.ClientSession.get",
    "aiohttp.ClientSession.post",
    "aiohttp.request",
    "urllib.request.urlopen",
    "urllib.request.urlretrieve",
    "socket.socket",
    "socket.connect",
    "socket.create_connection",
    "http.client.HTTPSConnection",
    "http.client.HTTPConnection",
    "websockets.connect",
    "websockets.serve",
    "ftplib.FTP",
    "smtplib.SMTP",
    "imaplib.IMAP4",
    "poplib.POP3",
    "telnetlib.Telnet",
}

_DYNAMIC_EXEC_CALLS: Set[str] = {
    "eval",
    "exec",
    "compile",
    "__import__",
    "getattr",
    "importlib.import_module",
    "importlib.__import__",
    "builtins.eval",
    "builtins.exec",
    "builtins.compile",
    "builtins.getattr",
}

_DEPENDENCY_CALLS: Set[str] = {
    "pip.main",
    "subprocess.run",  # when args include pip/install
}

_CONCURRENCY_CALLS: Set[str] = {
    "multiprocessing.Process",
    "multiprocessing.Pool",
    "concurrent.futures.ThreadPoolExecutor",
    "concurrent.futures.ProcessPoolExecutor",
    "threading.Thread",
    "os.fork",
}

_SLEEP_CALLS: Set[str] = {
    "time.sleep",
    "asyncio.sleep",
}

_OUTPUT_CALLS: Set[str] = {
    "print",
    "pprint.pprint",
    "logging.debug",
    "logging.info",
    "logging.warning",
    "logging.error",
    "logging.critical",
    "logging.log",
    "builtins.print",
}

# ---------------------------------------------------------------------------
# Sensitive-import heuristics (module name → risk kind)
# ---------------------------------------------------------------------------

_SENSITIVE_IMPORTS: Dict[str, str] = {
    "requests": "network",
    "aiohttp": "network",
    "httpx": "network",
    "urllib.request": "network",
    "urllib3": "network",
    "socket": "network",
    "http.client": "network",
    "ftplib": "network",
    "smtplib": "network",
    "subprocess": "process",
    "os": "process",
    "shutil": "file_ops",
    "ctypes": "process",
    "cffi": "process",
    "multiprocessing": "resource",
    "concurrent.futures": "resource",
    "threading": "resource",
    "pip": "dependency",
    "pkg_resources": "dependency",
    "importlib": "dynamic",
}

# ═══════════════════════════════════════════════════════════════════════════
# Python AST Scanner
# ═══════════════════════════════════════════════════════════════════════════


class PythonScanner:
    """Walk a Python AST once and collect all security-relevant observations.

    Args:
        source: Raw Python source code.
        max_lines: Soft limit for scanning (exceeding this raises a flag).
    """

    def __init__(self, source: str, *, max_lines: int = 500) -> None:
        self._source = source
        self._lines = source.splitlines()
        self._max_lines = max_lines
        self._findings: List[PythonScanFinding] = []

        # Alias tracking: name → canonical dotted name
        self._aliases: Dict[str, str] = {}
        # Taint tracking: variable name → source of taint
        self._tainted: Dict[str, str] = {}
        # Class-instance tracking: variable → class name
        self._class_instances: Dict[str, str] = {}
        # Path construction tracking: variable → assembled path string
        self._path_chains: Dict[str, str] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def scan(self) -> List[PythonScanFinding]:
        """Parse source and return all findings."""
        if len(self._lines) > self._max_lines:
            # Don't abort — still scan, but flag it
            self._findings.append(
                PythonScanFinding(
                    kind="oversized",
                    evidence=f"{len(self._lines)} lines exceeds {self._max_lines}",
                    line_number=0,
                ))

        try:
            tree = ast.parse(self._source, mode="exec")
        except SyntaxError as exc:
            self._findings.append(
                PythonScanFinding(
                    kind="parse_error",
                    evidence=f"SyntaxError: {exc}",
                    line_number=exc.lineno or 0,
                ))
            return self._findings

        # Two-pass: first collect aliases and path chains, then visit
        self._collect_aliases(tree)
        self._visit_all(tree)

        return self._findings

    # ------------------------------------------------------------------
    # Pass 1: alias collection
    # ------------------------------------------------------------------

    def _collect_aliases(self, tree: ast.AST) -> None:
        """Walk top-level import nodes to build the alias table."""
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    # Key and value both use only the top-level module name
                    # so that ``import os.path`` maps to ``os``, not
                    # ``os.path`` — otherwise ``os.system("id")`` resolves
                    # to the non-existent ``os.path.system`` and is missed.
                    top = alias.name.split(".")[0]
                    name = alias.asname or top
                    self._aliases[name] = top
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    full = f"{module}.{alias.name}" if module else alias.name
                    name = alias.asname or alias.name
                    self._aliases[name] = full

    # ------------------------------------------------------------------
    # Pass 2: full AST visit
    # ------------------------------------------------------------------

    def _visit_all(self, tree: ast.AST) -> None:
        """Dispatch every interesting node type."""
        for node in ast.walk(tree):
            self._visit_node(node)

    def _visit_node(self, node: ast.AST) -> None:
        """Single-node dispatcher."""
        if isinstance(node, ast.Call):
            self._handle_call(node)
        elif isinstance(node, ast.Assign):
            self._handle_assign(node)
        elif isinstance(node, ast.AnnAssign):
            self._handle_ann_assign(node)
        elif isinstance(node, ast.AugAssign):
            self._handle_aug_assign(node)
        elif isinstance(node, ast.While):
            self._handle_while(node)
        elif isinstance(node, ast.For):
            self._handle_for(node)
        elif isinstance(node, ast.With):
            self._handle_with(node)

    # ------------------------------------------------------------------
    # Call handler — the core of the scanner
    # ------------------------------------------------------------------

    def _handle_call(self, node: ast.Call) -> None:
        """Analyse a function-call expression."""
        canonical = self._resolve_canonical(node.func)
        line_no = node.lineno or 0
        evidence = self._get_line(node.lineno)

        # Always run dynamic-call detection for patterns like
        # __import__("os").system("id") or importlib.import_module(...).method(...)
        # where the canonical name resolves to a non-empty dotted path
        # but the root is a dynamic-import primitive.
        self._check_dynamic_call(node, line_no, evidence)

        if not canonical:
            return

        # --- Dangerous file operations ---
        if canonical in _DANGEROUS_FILE_CALLS:
            if canonical == "shutil.rmtree":
                path = self._get_arg_string(node, 0)
                self._findings.append(
                    PythonScanFinding(
                        kind="file_delete",
                        canonical_name=canonical,
                        line_number=line_no,
                        evidence=evidence,
                        extra={"path": path or "?"},
                    ))
            else:
                self._findings.append(
                    PythonScanFinding(
                        kind="file_delete",
                        canonical_name=canonical,
                        line_number=line_no,
                        evidence=evidence,
                    ))

        # --- File reads ---
        if canonical in _FILE_READ_CALLS:
            mode = self._get_arg_string(node, 1) if canonical in ("open", "io.open", "builtins.open") else "r"
            mode_lower = (mode or "r").lower()
            path = self._get_arg_string(node, 0)
            # Determine if this is a credential path
            is_cred = bool(path) and _is_credential_path(path or "")
            self._findings.append(
                PythonScanFinding(
                    kind="file_read",
                    canonical_name=canonical,
                    line_number=line_no,
                    evidence=evidence,
                    extra={
                        "path": path or "?",
                        "mode": mode_lower,
                        "is_credential_path": is_cred,
                    },
                ))

        # --- File writes (open with 'w'/'a' mode) ---
        if canonical in _FILE_WRITE_CALLS or (canonical in ("open", "io.open", "builtins.open")
                                              and self._is_write_mode(node)):
            path = self._get_arg_string(node, 0)
            self._findings.append(
                PythonScanFinding(
                    kind="file_write",
                    canonical_name=canonical,
                    line_number=line_no,
                    evidence=evidence,
                    extra={"path": path or "?"},
                ))

        # --- Process commands ---
        if canonical in _PROCESS_CALLS:
            risk = "process"
            if canonical in _PRIVILEGE_CALLS:
                risk = "privilege"
            self._findings.append(
                PythonScanFinding(
                    kind="call",
                    canonical_name=canonical,
                    line_number=line_no,
                    evidence=evidence,
                    extra={"risk": risk},
                ))

        if canonical in _PRIVILEGE_CALLS:
            self._findings.append(
                PythonScanFinding(
                    kind="call",
                    canonical_name=canonical,
                    line_number=line_no,
                    evidence=evidence,
                    extra={"risk": "privilege"},
                ))

        # --- Network calls ---
        if canonical in _NETWORK_CALLS:
            url = self._get_arg_string(node, 0)
            domain = _extract_domain_from_url(url) if url else None
            self._findings.append(
                PythonScanFinding(
                    kind="url",
                    canonical_name=canonical,
                    line_number=line_no,
                    evidence=evidence,
                    extra={
                        "url": url or "?",
                        "domain": domain or "?",
                    },
                ))

        # --- Eval / exec / dynamic execution ---
        if canonical in _DYNAMIC_EXEC_CALLS:
            # Exempt re.compile / regex.compile (pattern compilation, not code exec).
            # Also exempt getattr(obj, attr, default) — property access, not exec.
            if canonical in ("compile", "getattr"):
                # Check if this is a re.compile / regex.compile call (Attribute-based)
                receiver = self._resolve_canonical(node.func.value) if isinstance(node.func, ast.Attribute) else ""
                if canonical == "compile" and receiver in ("re", "regex"):
                    pass  # re.compile(pattern) — safe, pattern compilation only
                # getattr(x, y, default) as plain expression (NOT immediately
                # called) is safe property access.
                elif (canonical == "getattr" and len(node.args) >= 3 and not isinstance(node.func, ast.Call)):
                    pass
                else:
                    self._findings.append(
                        PythonScanFinding(kind="eval_exec",
                                          canonical_name=canonical,
                                          line_number=line_no,
                                          evidence=evidence))
            else:
                self._findings.append(
                    PythonScanFinding(kind="eval_exec",
                                      canonical_name=canonical,
                                      line_number=line_no,
                                      evidence=evidence))

        # --- Concurrent / fork ---
        if canonical in _CONCURRENCY_CALLS:
            self._findings.append(
                PythonScanFinding(
                    kind="fork" if canonical == "os.fork" else "call",
                    canonical_name=canonical,
                    line_number=line_no,
                    evidence=evidence,
                    extra={"risk": "concurrency"},
                ))

        # --- Sleep ---
        if canonical in _SLEEP_CALLS:
            duration_arg = self._get_arg_value(node, 0)
            self._findings.append(
                PythonScanFinding(
                    kind="sleep",
                    canonical_name=canonical,
                    line_number=line_no,
                    evidence=evidence,
                    extra={"duration": duration_arg},
                ))

        # --- Output calls (for taint checking) ---
        if canonical in _OUTPUT_CALLS:
            self._check_output_taint(node, canonical, line_no, evidence)

    # ------------------------------------------------------------------
    # Dynamic call detection (getattr, __import__, etc.)
    # ------------------------------------------------------------------

    def _check_dynamic_call(self, node: ast.Call, line_no: int, evidence: str) -> None:
        """Detect getattr(obj,name)(), __import__(x).func(), importlib.import_module(x).func()."""
        func = node.func
        if isinstance(func, ast.Call):
            inner = self._resolve_canonical(func.func)
            if inner == "getattr":
                # getattr(obj, attr, default) with 3 args and NOT immediately
                # called: safe property access (e.g. x = getattr(cfg, "key", 42)).
                # Called form getattr(os,"system")(...) is caught by _handle_call.
                if len(func.args) >= 3:
                    pass
                else:
                    attr = self._get_arg_string(func, 1)
                    self._findings.append(
                        PythonScanFinding(
                            kind="eval_exec",
                            canonical_name=f"getattr(..., {attr!r})",
                            line_number=line_no,
                            evidence=evidence,
                        ))
            elif inner in ("__import__", "importlib.import_module"):
                self._findings.append(
                    PythonScanFinding(
                        kind="eval_exec",
                        canonical_name=inner,
                        line_number=line_no,
                        evidence=evidence,
                    ))
        elif isinstance(func, ast.Attribute):
            # __import__("os").system("id") — the receiver is a dynamic
            # import call whose result was then attribute-accessed
            receiver = self._resolve_canonical(func.value)
            if receiver in ("__import__", "importlib.import_module", "getattr"):
                self._findings.append(
                    PythonScanFinding(
                        kind="eval_exec",
                        canonical_name=f"{receiver}->{func.attr}",
                        line_number=line_no,
                        evidence=evidence,
                    ))

    # ------------------------------------------------------------------
    # Taint tracking: assignments and sinks
    # ------------------------------------------------------------------

    def _handle_assign(self, node: ast.Assign) -> None:
        """Track taint propagation through assignments."""
        for target in node.targets:
            var_name = self._get_name(target)
            if not var_name:
                continue

            # Check if RHS is an import / class instantiation
            if isinstance(node.value, ast.Call):
                canonical = self._resolve_canonical(node.value.func)
                if canonical in _NETWORK_CALLS:
                    self._class_instances[var_name] = canonical
                if canonical in _DYNAMIC_EXEC_CALLS or canonical in _PROCESS_CALLS:
                    # e = eval; e("code") / s = os.system; s("id")
                    self._aliases[var_name] = canonical

            # Propagate bare-name assignments: e = eval; m = __import__
            if isinstance(node.value, ast.Name):
                src_canonical = self._resolve_canonical(node.value)
                if src_canonical in _DYNAMIC_EXEC_CALLS or src_canonical in _PROCESS_CALLS:
                    self._aliases[var_name] = src_canonical

            # Propagate __import__ / importlib result
            if isinstance(node.value, ast.Call):
                inner = self._resolve_canonical(node.value.func)
                if inner in ("__import__", "importlib.import_module"):
                    self._aliases[var_name] = "__import__"

            # Check if RHS is from os.environ / os.getenv → taint
            # Only taint when the env key looks sensitive (KEY/TOKEN/SECRET/...)
            # Reading HOME/USER/PATH is normal and should not trigger alerts.
            if isinstance(node.value, ast.Subscript):
                canonical = self._resolve_canonical(node.value.value)
                if canonical in ("os.environ", ):
                    key = self._get_subscript_key(node.value)
                    if key and _is_sensitive_env_key(key):
                        self._tainted[var_name] = f"os.environ:{key}"

            if isinstance(node.value, ast.Call):
                canonical = self._resolve_canonical(node.value.func)
                if canonical in ("os.getenv", "os.environ.get"):
                    key = self._get_arg_string(node.value, 0)
                    if key and _is_sensitive_env_key(key):
                        self._tainted[var_name] = f"os.getenv:{key}"

            # Check if RHS is from open(cred_path) → taint via file read
            if isinstance(node.value, ast.Call):
                canonical = self._resolve_canonical(node.value.func)
                if canonical in _FILE_READ_CALLS:
                    path = self._get_arg_string(node.value, 0)
                    if path and _is_credential_path(path):
                        self._tainted[var_name] = f"file:{path}"

            # Check if RHS is a literal secret → taint
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                if _looks_like_secret(node.value.value):
                    self._tainted[var_name] = "literal_secret"

            # Track pathlib path chains
            if isinstance(node.value, ast.BinOp) and isinstance(node.value.op, ast.Div):
                path_str = self._resolve_path_chain(node.value)
                if path_str:
                    self._path_chains[var_name] = path_str

    def _handle_ann_assign(self, node: ast.AnnAssign) -> None:
        if isinstance(node.target, ast.Name) and isinstance(node.value, ast.Call):
            canonical = self._resolve_canonical(node.value.func)
            if canonical in _NETWORK_CALLS:
                self._class_instances[node.target.id] = canonical
            if canonical in ("os.getenv", "os.environ.get"):
                key = self._get_arg_string(node.value, 0)
                if key and _is_sensitive_env_key(key):
                    self._tainted[node.target.id] = f"os.getenv:{key}"

    def _handle_aug_assign(self, node: ast.AugAssign) -> None:
        """Track augmented assignment taint (x += secret)."""
        if isinstance(node.target, ast.Name):
            if node.target.id in self._tainted:
                pass  # already tainted

    def _check_output_taint(self, node: ast.Call, canonical: str, line_no: int, evidence: str) -> None:
        """Check if a tainted variable is being printed/logged."""
        if len(node.args) < 1:
            return
        first_arg = node.args[0]
        var_name = self._get_name(first_arg)
        if var_name and var_name in self._tainted:
            self._findings.append(
                PythonScanFinding(
                    kind="secret_in_output",
                    canonical_name=canonical,
                    line_number=line_no,
                    evidence=evidence,
                    extra={
                        "tainted_var": var_name,
                        "taint_source": self._tainted[var_name],
                    },
                ))
        # Also check f-strings and concatenation for tainted vars
        if isinstance(first_arg, ast.JoinedStr):
            for value in first_arg.values:
                vn = self._get_name(value)
                if vn and vn in self._tainted:
                    self._findings.append(
                        PythonScanFinding(
                            kind="secret_in_output",
                            canonical_name=canonical,
                            line_number=line_no,
                            evidence=evidence,
                            extra={
                                "tainted_var": vn,
                                "taint_source": self._tainted[vn],
                            },
                        ))
                    break

    # ------------------------------------------------------------------
    # Loop / resource patterns
    # ------------------------------------------------------------------

    def _handle_while(self, node: ast.While) -> None:
        """Detect infinite loops (while True, while 1)."""
        line_no = node.lineno or 0
        if isinstance(node.test, ast.Constant) and node.test.value in (True, 1):
            self._findings.append(
                PythonScanFinding(
                    kind="loop",
                    canonical_name="while_True",
                    line_number=line_no,
                    evidence=self._get_line(line_no),
                ))

    def _handle_for(self, node: ast.For) -> None:
        """Detect range(very_large) patterns in 1-/2-/3-arg forms."""
        line_no = node.lineno or 0
        if isinstance(node.iter, ast.Call):
            canonical = self._resolve_canonical(node.iter.func)
            if canonical == "range" and 1 <= len(node.iter.args) <= 3:
                # The stop-value argument index: arg 0 for range(stop),
                # arg 1 for range(start, stop) or range(start, stop, step).
                stop_idx = 0 if len(node.iter.args) == 1 else 1
                val = self._get_arg_value(node.iter, stop_idx)
                if isinstance(val, int) and val > 10_000_000:
                    self._findings.append(
                        PythonScanFinding(
                            kind="loop",
                            canonical_name="large_range",
                            line_number=line_no,
                            evidence=self._get_line(line_no),
                            extra={
                                "range_value": val,
                                "arg_count": len(node.iter.args)
                            },
                        ))

    def _handle_with(self, node: ast.With) -> None:
        """Detect with requests.Session() as s, etc."""
        for item in node.items:
            if isinstance(item.context_expr, ast.Call):
                canonical = self._resolve_canonical(item.context_expr.func)
                if canonical and "Session" in canonical:
                    if item.optional_vars:
                        name = self._get_name(item.optional_vars)
                        if name:
                            self._class_instances[name] = canonical

    # ------------------------------------------------------------------
    # Name resolution helpers
    # ------------------------------------------------------------------

    def _resolve_canonical(self, node: ast.expr) -> str:
        """Walk an AST expression to produce a dotted canonical name.

        Examples:
            ``os.system``       → ``"os.system"``
            ``sys_call``        → ``"os.system"``   (via alias resolution)
            ``requests.get``    → ``"requests.get"``
            ``obj.method()``    → ``"obj.method"``
            ``getattr(x, 'y')`` → ``"getattr"``
        """
        if isinstance(node, ast.Name):
            # Direct name lookup — consult class_instances first (so that
            # s.get("http://evil.com") where s = requests.Session() resolves
            # to "requests.Session.get"), then alias table, then bare name.
            cls = self._class_instances.get(node.id)
            if cls:
                return cls
            return self._aliases.get(node.id, node.id)

        if isinstance(node, ast.Attribute):
            # requests.get → requests.get
            value = self._resolve_canonical(node.value)
            return f"{value}.{node.attr}" if value else node.attr

        if isinstance(node, ast.Call):
            # getattr(obj, "method") → getattr
            inner = self._resolve_canonical(node.func)
            return inner

        if isinstance(node, ast.Subscript):
            # some_dict["key"] → resolve the dict name
            return self._resolve_canonical(node.value)

        return ""

    def _get_name(self, node: ast.expr) -> str:
        """Extract a simple variable name from a target."""
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return self._get_name(node.value)
        if isinstance(node, ast.Subscript):
            return self._get_name(node.value)
        if isinstance(node, ast.Tuple):
            return ""
        return ""

    def _get_subscript_key(self, node: ast.Subscript) -> Optional[str]:
        """Extract the key from ``os.environ['KEY']`` if it is a string constant."""
        if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
            return node.slice.value
        return None

    def _get_receiver_path(self, node: ast.Call) -> Optional[str]:
        """Extract the path argument from ``Path('...').method()`` patterns.

        When a call like ``Path('/etc/shadow').read_text()`` is detected,
        the path is in the Path() constructor, not in the method's arguments.
        """
        func = node.func
        if isinstance(func, ast.Attribute):
            receiver = func.value
            if isinstance(receiver, ast.Call):
                recv_canonical = self._resolve_canonical(receiver.func)
                if recv_canonical in ("pathlib.Path", "Path"):
                    return self._get_arg_string(receiver, 0)
        return None

    def _get_arg_string(self, node: ast.Call, index: int) -> Optional[str]:
        """Extract the *index* argument as a string if it is a constant."""
        # For Path('...').method() patterns, extract from the receiver
        path = self._get_receiver_path(node)
        if path:
            return path

        args = node.args
        if index >= len(args):
            # Try keyword args
            return None
        arg = args[index]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return arg.value
        if isinstance(arg, ast.JoinedStr):
            # F-string — try to resolve constant parts
            parts = [v.value for v in arg.values if isinstance(v, ast.Constant) and isinstance(v.value, str)]
            if parts:
                return "".join(parts)
            return None
        if isinstance(arg, ast.Name):
            # Try path chain tracking
            if arg.id in self._path_chains:
                return self._path_chains[arg.id]
            return None
        # For BinOp path construction: Path("x") / "y"
        if isinstance(arg, ast.BinOp):
            path = self._resolve_path_chain(arg)
            if path:
                return path
            return None
        return None

    def _get_arg_value(self, node: ast.Call, index: int) -> Any:
        """Extract argument value at *index* — returns int/str/None."""
        args = node.args
        if index >= len(args):
            return None
        arg = args[index]
        if isinstance(arg, ast.Constant):
            return arg.value
        if isinstance(arg, ast.UnaryOp) and isinstance(arg.op, ast.USub):
            if isinstance(arg.operand, ast.Constant) and isinstance(arg.operand.value, (int, float)):
                return -arg.operand.value
        return None

    def _is_write_mode(self, node: ast.Call) -> bool:
        """Check whether ``open(path, mode)`` is in write mode."""
        args = node.args
        # Look for positional mode arg (index 1) or keyword 'mode'
        if len(args) >= 2:
            mode_arg = args[1]
            if isinstance(mode_arg, ast.Constant) and isinstance(mode_arg.value, str):
                return any(c in mode_arg.value for c in "wa+")
        for kw in node.keywords:
            if kw.arg == "mode" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                return any(c in kw.value.value for c in "wa+")
        return False

    def _resolve_path_chain(self, node: ast.BinOp) -> Optional[str]:
        """Resolve Path('a') / 'b' / 'c' chains to a full path string."""
        parts: List[str] = []

        def _collect(n: ast.expr) -> bool:
            if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div):
                return _collect(n.left) and _collect(n.right)
            if isinstance(n, ast.Call):
                canonical = self._resolve_canonical(n.func)
                if canonical in ("pathlib.Path", "Path"):
                    if n.args:
                        path_part = self._get_arg_string(n, 0)
                        if path_part:
                            parts.append(path_part)
                            return True
                return False
            if isinstance(n, ast.Constant) and isinstance(n.value, str):
                parts.append(n.value)
                return True
            return False

        if _collect(node):
            return "/".join(parts)
        return None

    def _get_line(self, lineno: Optional[int]) -> str:
        """Return the source line at *lineno* (1-based), truncated."""
        if lineno and 1 <= lineno <= len(self._lines):
            line = self._lines[lineno - 1].strip()
            return line[:300]
        return ""


# ═══════════════════════════════════════════════════════════════════════════
# Public helpers — used by rules in _rules.py
# ═══════════════════════════════════════════════════════════════════════════


def scan_python(source: str, *, max_lines: int = 500) -> List[PythonScanFinding]:
    """Run the AST scanner on *source* and return all findings."""
    scanner = PythonScanner(source, max_lines=max_lines)
    return scanner.scan()


def has_python_call(findings: List[PythonScanFinding], canonical_prefix: str) -> bool:
    """Return True if any finding's canonical_name starts with *canonical_prefix*."""
    return any(f.canonical_name.startswith(canonical_prefix) for f in findings if f.kind == "call")


def get_python_urls(findings: List[PythonScanFinding]) -> List[Tuple[str, str, int]]:
    """Return (url, domain, line_number) tuples for all URL findings."""
    return [(f.extra.get("url", ""), f.extra.get("domain", ""), f.line_number) for f in findings if f.kind == "url"]


def get_python_file_reads(findings: List[PythonScanFinding]) -> List[PythonScanFinding]:
    """Return all file-read findings."""
    return [f for f in findings if f.kind == "file_read"]


def get_python_file_deletes(findings: List[PythonScanFinding]) -> List[PythonScanFinding]:
    """Return all file-delete findings."""
    return [f for f in findings if f.kind == "file_delete"]


def get_python_file_writes(findings: List[PythonScanFinding]) -> List[PythonScanFinding]:
    """Return all file-write findings."""
    return [f for f in findings if f.kind == "file_write"]


def get_python_dynamic_exec(findings: List[PythonScanFinding]) -> List[PythonScanFinding]:
    """Return all eval/exec/dynamic-import findings."""
    return [f for f in findings if f.kind == "eval_exec"]


def get_python_secret_flow(findings: List[PythonScanFinding]) -> List[PythonScanFinding]:
    """Return all tainted-var-in-output findings."""
    return [f for f in findings if f.kind == "secret_in_output"]


def get_python_loops(findings: List[PythonScanFinding]) -> List[PythonScanFinding]:
    """Return all infinite-loop findings."""
    return [f for f in findings if f.kind == "loop"]


def get_python_sleep(findings: List[PythonScanFinding]) -> List[PythonScanFinding]:
    """Return all sleep findings with duration info."""
    return [f for f in findings if f.kind == "sleep"]


def get_python_concurrency(findings: List[PythonScanFinding]) -> List[PythonScanFinding]:
    """Return all concurrency/fork findings."""
    return [f for f in findings if f.kind == "fork" or (f.kind == "call" and f.extra.get("risk") == "concurrency")]


# ═══════════════════════════════════════════════════════════════════════════
# Internal helpers
# ═══════════════════════════════════════════════════════════════════════════

_CRED_PATH_RE = re.compile(
    r"(?:\.ssh|\.gnupg|\.aws|\.gcloud|\.azure|\.pem|\.key|id_rsa|id_ed25519|id_ecdsa|"
    r"credentials|secrets|\.env|config\.json|"
    r"/etc/(?:shadow|passwd|sudoers|hosts)|"
    r"/proc/(?:self|\d+)/(?:mem|cmdline|environ)|"
    r"/var/run/docker\.sock)",
    re.IGNORECASE,
)


def _is_credential_path(path: str) -> bool:
    """Return True if *path* looks like a credential/secret path."""
    return bool(_CRED_PATH_RE.search(path))


_SECRET_LOOKALIKE_RE = re.compile(
    r"(?:sk-[a-zA-Z0-9]{20,}|ghp_[a-zA-Z0-9]{20,}|AKIA[0-9A-Z]{16}|"
    r"eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}|"
    r"xox[baprs]-[a-zA-Z0-9-]+|AIza[0-9A-Za-z\-_]{35})", )


def _looks_like_secret(value: str) -> bool:
    """Return True if *value* looks like a hard-coded API key / token."""
    if len(value) < 20:
        return False
    return bool(_SECRET_LOOKALIKE_RE.search(value))


_SENSITIVE_ENV_KEY_RE = re.compile(
    r"(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH|PRIVATE)",
    re.IGNORECASE,
)


def _is_sensitive_env_key(key: str) -> bool:
    """Return True if an environment variable key looks sensitive.

    ``HOME``, ``USER``, ``PATH``, ``LANG``, etc. are NOT sensitive.
    ``AWS_SECRET_ACCESS_KEY``, ``GITHUB_TOKEN``, etc. ARE sensitive.
    """
    return bool(_SENSITIVE_ENV_KEY_RE.search(key))


def _extract_domain_from_url(url: Optional[str]) -> Optional[str]:
    """Extract bare hostname from a URL, stripping userinfo and port."""
    if not url:
        return None
    m = re.search(r"https?://([^\s/\"']+)", url)
    if m:
        host = m.group(1)
        if "@" in host:
            host = host.rsplit("@", 1)[-1]
        if ":" in host:
            host = host.rsplit(":", 1)[0]
        return host
    return None
