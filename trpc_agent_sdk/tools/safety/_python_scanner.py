# Copyright (C) 2026 Tencent. All rights reserved.
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Python scanner using the ast module with import-as alias tracking.

Aliases let us resolve `import os as x; x.system(...)` back to os.system so
trivial renaming cannot bypass detection.
"""
from __future__ import annotations

import ast
import re

from trpc_agent_sdk.tools.safety._policy import Policy
from trpc_agent_sdk.tools.safety._rules import R_CODE_UNSAFE_EVAL
from trpc_agent_sdk.tools.safety._rules import R_CODE_UNSAFE_EXEC
from trpc_agent_sdk.tools.safety._rules import R_FS_RECURSIVE_DELETE
from trpc_agent_sdk.tools.safety._rules import R_FS_READ_CREDENTIALS
from trpc_agent_sdk.tools.safety._rules import R_FS_SYSTEM_DIR
from trpc_agent_sdk.tools.safety._rules import R_NET_HTTP
from trpc_agent_sdk.tools.safety._rules import R_NET_SOCKET
from trpc_agent_sdk.tools.safety._rules import R_PROC_SUBPROCESS
from trpc_agent_sdk.tools.safety._rules import R_RES_CONCURRENT_FLOOD
from trpc_agent_sdk.tools.safety._rules import R_RES_INFINITE_LOOP
from trpc_agent_sdk.tools.safety._rules import R_RES_LARGE_WRITE
from trpc_agent_sdk.tools.safety._rules import R_SECRET_LOGGING
from trpc_agent_sdk.tools.safety._rules import R_SECRET_PRIVATE_KEY
from trpc_agent_sdk.tools.safety._types import Decision
from trpc_agent_sdk.tools.safety._types import Finding
from trpc_agent_sdk.tools.safety._types import RiskLevel

_CRED_PATH_RE = re.compile(r"(\.ssh|\.env|\.aws/credentials|id_rsa|id_ed25519|credentials)", re.I)
_URL_RE = re.compile(r"https?://([^/\s'\"']+)", re.I)
_SECRET_NAME_RE = re.compile(r"(api[_-]?key|secret|token|password|passwd|private[_-]?key)", re.I)
_PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
# System directories: writing here can brick the runtime (issue risk class #1).
_SYSTEM_DIRS = ("/etc/", "/usr/", "/bin/", "/sbin/", "/boot/", "/sys/",
                "/proc/", "/lib/", "/lib64/", "/var/", "/dev/")

# attribute path -> rule fired when called/used
_DANGEROUS_ATTR = {
    ("os", "system"): R_PROC_SUBPROCESS,
    ("subprocess", "call"): R_PROC_SUBPROCESS,
    ("subprocess", "run"): R_PROC_SUBPROCESS,
    ("subprocess", "Popen"): R_PROC_SUBPROCESS,
    ("os", "popen"): R_PROC_SUBPROCESS,
    ("shutil", "rmtree"): R_FS_RECURSIVE_DELETE,
}
_NET_MODULES = {"requests", "httpx", "aiohttp", "urllib.request"}


def scan_python(policy: Policy, script: str) -> list[Finding]:
    """Return findings for a python script. Never raises on syntax errors."""
    try:
        tree = ast.parse(script)
    except SyntaxError:
        return _heuristic_fallback(policy, script)

    aliases: dict[str, str] = {}  # local name -> module root name
    imported_attr: dict[str, str] = {}  # local name -> "module.attr"
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".")[0]
                aliases[local] = alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            mod = (node.module or "").split(".")[0]
            for alias in node.names:
                local = alias.asname or alias.name
                imported_attr[local] = f"{mod}.{alias.name}"

    findings: list[Finding] = []
    max_ev = policy.max_evidence_chars

    def add(rule_id: str, evidence: str, rec: str) -> None:
        meta = policy.rules[rule_id]
        findings.append(Finding(
            rule_id=rule_id, risk_level=meta.risk_level, rule_decision=meta.decision,
            evidence=evidence[:max_ev], recommendation=rec, language="python"))

    def resolve_attr(node: ast.AST) -> str:
        """Resolve `x.system` or `system` to 'module.attr' using alias tables."""
        if isinstance(node, ast.Attribute):
            base = resolve_attr(node.value)
            return f"{base}.{node.attr}" if base else node.attr
        if isinstance(node, ast.Name):
            if node.id in imported_attr:
                return imported_attr[node.id]
            if node.id in aliases:
                return aliases[node.id]
            return node.id
        return ""

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            fname = resolve_attr(func)
            # bare eval/exec
            if isinstance(func, ast.Name):
                if func.id == "eval":
                    add(R_CODE_UNSAFE_EVAL, "eval()", "eval executes arbitrary code.")
                elif func.id == "exec":
                    add(R_CODE_UNSAFE_EXEC, "exec()", "exec executes arbitrary code.")
                elif func.id == "__import__":
                    add(R_CODE_UNSAFE_EXEC, "__import__()", "dynamic import; review.")
            # attribute calls
            parts = fname.split(".")
            if len(parts) >= 2:
                attr_key = (parts[-2], parts[-1])
                if attr_key in _DANGEROUS_ATTR:
                    add(_DANGEROUS_ATTR[attr_key], f"{fname}()", f"{fname} is dangerous; review.")
            mod = fname.split(".")[0]
            if mod in _NET_MODULES:
                url = _extract_str_arg(node)
                # Conservatively fire: unknown/non-URL strings are treated as suspicious.
                if url and not _is_whitelisted(url, policy):
                    add(R_NET_HTTP, f"{fname}({url})", f"{url} not whitelisted.")
            # Socket handled at module level to catch ALL socket.* calls (not a per-attribute list).
            if mod == "socket":
                add(R_NET_SOCKET, f"{fname}()",
                    "raw socket use bypasses HTTP allowlist; review egress.")
            # Resource abuse: very large writes and concurrency floods.
            if _is_write_call(fname) and _has_huge_size(node):
                add(R_RES_LARGE_WRITE, f"{fname}(huge payload)",
                    "Very large write; possible disk/resource exhaustion. Review.")
            if _is_pool_call(fname) and _max_workers_too_large(node):
                add(R_RES_CONCURRENT_FLOOD, f"{fname}(max_workers>>)",
                    "Very large worker pool; possible resource exhaustion. Review.")
            if mod in ("open",) or fname.endswith(".open"):
                _check_open_path(node, policy, add)
        # infinite loop
        if isinstance(node, (ast.While,)) and _is_truthy(node.test):
            add(R_RES_INFINITE_LOOP, "while True:", "infinite loop; review.")
        # secret logging: assignment to a secret-named variable.
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and _SECRET_NAME_RE.search(t.id):
                    add(R_SECRET_LOGGING, f"secret assigned to {t.id}",
                        "secret-like variable; avoid logging.")
        # private-key literal embedded anywhere (independent of variable name).
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and _PRIVATE_KEY_RE.search(node.value):
            add(R_SECRET_PRIVATE_KEY, "private key literal",
                "embedded private key; refuse.")

    return findings


def _is_truthy(test: ast.AST) -> bool:
    return isinstance(test, ast.Constant) and test.value is True


def _extract_str_arg(call: ast.Call) -> str | None:
    if not call.args:
        return None
    first = call.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    return None


def _check_open_path(call: ast.Call, policy: Policy, add) -> None:
    if not call.args:
        return
    arg = call.args[0]
    path = ""
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        path = arg.value
    elif isinstance(arg, ast.JoinedStr):
        path = "".join(v.value for v in arg.values if isinstance(v, ast.Constant))
    mode = _open_mode(call)
    if path and _is_write_mode(mode) and path.startswith(_SYSTEM_DIRS):
        add(R_FS_SYSTEM_DIR, f"open('{path}','{mode}')",
            f"writing to system directory {path}; refuse.")
        return
    if path and _CRED_PATH_RE.search(path):
        add(R_FS_READ_CREDENTIALS, f"open('{path}')",
            f"reading credential path {path}; review.")
        return
    for denied in policy.denied_paths:
        norm = denied.replace("~", "/root")  # crude home expansion for matching
        if denied in path:
            add(R_FS_READ_CREDENTIALS, f"open('{path}')",
                f"path matches denied path {denied}.")
            return


_HUGE_BYTES = 10_000_000  # ~10 MB heuristic threshold for "large write"
_MAX_WORKERS = 100        # heuristic threshold for concurrency flood

_WRITE_METHODS = ("write", "write_bytes", "write_text")
_POOL_CLASSES = ("ThreadPoolExecutor", "ProcessPoolExecutor")


def _is_write_call(fname: str) -> bool:
    return fname in _WRITE_METHODS or any(fname.endswith("." + m) for m in _WRITE_METHODS)


def _is_pool_call(fname: str) -> bool:
    return fname in _POOL_CLASSES or any(fname.endswith("." + c) for c in _POOL_CLASSES)


def _huge_value(node: ast.AST) -> bool:
    """True if node is a large int literal or a multiply/power producing one."""
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return node.value >= _HUGE_BYTES
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Mult, ast.Pow)):
        return _huge_value(node.left) or _huge_value(node.right)
    return False


def _has_huge_size(call: ast.Call) -> bool:
    return any(_huge_value(a) for a in call.args) or any(
        _huge_value(kw.value) for kw in call.keywords
    )


def _max_workers_too_large(call: ast.Call) -> bool:
    for kw in call.keywords:
        if kw.arg == "max_workers" and isinstance(kw.value, ast.Constant) \
                and isinstance(kw.value.value, int) and kw.value.value > _MAX_WORKERS:
            return True
    return False


def _is_whitelisted(url: str, policy: Policy) -> bool:
    m = _URL_RE.search(url) if "://" in url else None
    host = (m.group(1) if m else url).lower()
    root = ".".join(host.split(".")[-2:]) if len(host.split(".")) >= 2 else host
    return root in {d.lower() for d in policy.whitelisted_domains} or host in policy.whitelisted_domains


def _open_mode(call: ast.Call) -> str:
    """Return the literal mode string of an open() call, else ''."""
    if len(call.args) >= 2:
        m = call.args[1]
        if isinstance(m, ast.Constant) and isinstance(m.value, str):
            return m.value
    for kw in call.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant) \
                and isinstance(kw.value.value, str):
            return kw.value.value
    return ""


def _is_write_mode(mode: str) -> bool:
    """True if the mode can create/modify a file (w x a +)."""
    return bool(set(mode) & set("wxa+"))


def _heuristic_fallback(policy: Policy, script: str) -> list[Finding]:
    """Best-effort when the script is not valid Python AST."""
    findings: list[Finding] = []
    max_ev = policy.max_evidence_chars

    def add(rule_id: str, evidence: str, rec: str) -> None:
        meta = policy.rules[rule_id]
        findings.append(Finding(
            rule_id=rule_id, risk_level=meta.risk_level, rule_decision=meta.decision,
            evidence=evidence[:max_ev], recommendation=rec, language="python"))

    if re.search(r"\beval\s*\(", script):
        add(R_CODE_UNSAFE_EVAL, "eval(", "eval executes arbitrary code.")
    if re.search(r"\bexec\s*\(", script):
        add(R_CODE_UNSAFE_EXEC, "exec(", "exec executes arbitrary code.")
    if re.search(r"shutil\.rmtree", script):
        add(R_FS_RECURSIVE_DELETE, "shutil.rmtree", "recursive delete.")
    return findings
