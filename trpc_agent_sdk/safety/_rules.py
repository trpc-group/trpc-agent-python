# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Built-in safety rules for Python and Bash static scanning."""
from __future__ import annotations

import ast
import re
from abc import ABC
from abc import abstractmethod
from urllib.parse import urlparse

from ._ast_utils import bash_lines
from ._ast_utils import build_import_aliases
from ._ast_utils import evidence_snippet
from ._ast_utils import get_string_literal
from ._ast_utils import iter_python_calls
from ._ast_utils import normalize_language
from ._ast_utils import parse_python_ast
from ._ast_utils import path_expr_text
from ._ast_utils import resolve_name
from ._policy import PolicyConfig
from ._types import RiskLevel
from ._types import SafetyFinding
from ._types import ScanInput


class SafetyRule(ABC):
    """Base class for all safety rules."""

    rule_id: str = "base"
    rule_name: str = "base rule"
    risk_type: str = "generic"
    default_level: RiskLevel = RiskLevel.MEDIUM
    languages: tuple[str, ...] = ("python", "bash")

    @abstractmethod
    def check(self, scan_input: ScanInput, policy: PolicyConfig) -> list[SafetyFinding]:
        """Return findings for this rule. Empty list when nothing matches."""

    def applies(self, language: str) -> bool:
        """True when this rule should run for *language*."""
        return language in self.languages

    def _finding(
        self,
        evidence: str,
        line: int | None,
        rec: str,
        *,
        level: RiskLevel | None = None,
        message: str = "",
        extra: dict | None = None,
    ) -> SafetyFinding:
        meta = {"message": message or evidence}
        if extra:
            meta.update(extra)
        return SafetyFinding(
            rule_id=self.rule_id,
            rule_name=self.rule_name,
            risk_type=self.risk_type,
            risk_level=level or self.default_level,
            evidence=evidence_snippet(evidence),
            line=line,
            recommendation=rec,
            metadata=meta,
        )


# ---------------------------------------------------------------------------
# Sensitive path patterns
# ---------------------------------------------------------------------------

_SENSITIVE_PATH_PATTERNS = [
    (r"\.ssh\b", "~/.ssh / SSH keys"),
    (r"\.env\b", ".env file (often contains secrets)"),
    (r"\.aws/credentials\b", "AWS credentials file"),
    (r"\.aws\b", "AWS config directory"),
    (r"\.netrc\b", ".netrc credentials file"),
    (r"\.npmrc\b", ".npmrc (may contain tokens)"),
    (r"\.pypirc\b", ".pypirc (may contain tokens)"),
    (r"\.gnupg\b", "GPG keyring"),
    (r"/etc/shadow\b", "system shadow password file"),
    (r"/etc/passwd\b", "system passwd file"),
    (r"id_rsa\b", "private SSH key"),
    (r"id_ed25519\b", "private SSH key"),
    (r"\.kube/config\b", "kubeconfig credentials"),
    (r"\.docker/config\.json\b", "docker credentials"),
]

_DELETE_PATTERNS = [
    re.compile(r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f?|-[a-zA-Z]*f[a-zA-Z]*r?)\b", re.IGNORECASE),
    re.compile(r"\brm\s+-rf\b", re.IGNORECASE),
    re.compile(r"\brmdir\s+/s\b", re.IGNORECASE),
    re.compile(r"\bdel\s+/[sq]\b", re.IGNORECASE),
    re.compile(r"\bfind\b.*(-delete|-exec\s+rm\b)", re.IGNORECASE),
    re.compile(r"\bxargs\b.*\brm\b", re.IGNORECASE),
]

_SYSTEM_DIRS = [
    "/etc",
    "/usr",
    "/bin",
    "/sbin",
    "/boot",
    "/sys",
    "/proc",
    "/dev",
    "C:\\Windows",
    "C:\\Program Files",
]


def _matches_sensitive(target: str) -> bool:
    if not target:
        return False
    for pat, _ in _SENSITIVE_PATH_PATTERNS:
        if re.search(pat, target):
            return True
    return False


def _matches_system_dir(target: str) -> bool:
    if not target:
        return False
    return any(sd in target for sd in _SYSTEM_DIRS)


def _matches_forbidden(target: str, policy: PolicyConfig) -> bool:
    if not target:
        return False
    return any(fb in target for fb in policy.forbidden_paths)


# ---------------------------------------------------------------------------
# R001 Dangerous files
# ---------------------------------------------------------------------------


class DangerousFilesRule(SafetyRule):
    """Detect dangerous file operations: recursive delete, system dirs, secrets."""

    rule_id = "R001_dangerous_files"
    rule_name = "Dangerous File Operation"
    risk_type = "dangerous_files"
    default_level = RiskLevel.CRITICAL
    languages = ("python", "bash")

    def check(self, scan_input: ScanInput, policy: PolicyConfig) -> list[SafetyFinding]:
        lang = normalize_language(scan_input)
        if lang == "python":
            return self._check_python(scan_input, policy)
        return self._check_bash(scan_input, policy)

    def _check_python(self, scan_input: ScanInput, policy: PolicyConfig) -> list[SafetyFinding]:
        findings: list[SafetyFinding] = []
        tree = parse_python_ast(scan_input.script)
        if tree is None:
            return findings
        aliases = build_import_aliases(tree)
        # Track local vars assigned from path-join expressions that mention
        # sensitive path fragments (e.g. path = os.path.join(..., ".ssh", ...)).
        path_vars = _collect_sensitive_path_vars(tree, aliases, policy)

        for node, name in iter_python_calls(tree, aliases):
            lname = name.lower()
            if lname in {
                    "shutil.rmtree",
                    "os.rmdir",
                    "os.remove",
                    "os.unlink",
                    "pathlib.path.unlink",
                    "pathlib.Path.unlink",
            } or lname.endswith(".unlink") or lname.endswith(".rmtree"):
                arg = node.args[0] if node.args else None
                target = get_string_literal(arg) or path_expr_text(arg) if arg else "<dynamic>"
                target = target or "<dynamic>"
                if "rmtree" in lname or _is_recursive_delete(name, node):
                    findings.append(
                        self._finding(
                            f"{name}({target})",
                            node.lineno,
                            "Avoid recursive deletion; restrict to known workspace paths.",
                            message=f"Recursive/forced delete via {name}({target!r})",
                        ))

            if lname in {"open", "builtins.open"} or lname.endswith(".open"):
                target = _first_str_or_path(node) or "<dynamic>"
                if isinstance(node.args[0], ast.Name) if node.args else False:
                    var = node.args[0].id
                    if var in path_vars:
                        target = path_vars[var]
                if _is_write_open(node):
                    if _matches_sensitive(target) or _matches_forbidden(target, policy) or _matches_system_dir(target):
                        findings.append(
                            self._finding(
                                f"open({target!r}, 'w')",
                                node.lineno,
                                "Do not write to system or credential paths.",
                                message=f"Write to sensitive path {target!r}",
                            ))
                else:
                    if _matches_sensitive(target) or _matches_forbidden(target, policy):
                        findings.append(
                            self._finding(
                                f"{name}({target!r})",
                                node.lineno,
                                "Do not read credential/secret files in tool scripts.",
                                message=f"Read sensitive file {target!r}",
                            ))

            # pathlib Path.read_text / read_bytes with sensitive target
            if lname.endswith(".read_text") or lname.endswith(".read_bytes"):
                target = _path_from_attr_call(node, aliases)
                if _matches_sensitive(target) or _matches_forbidden(target, policy):
                    findings.append(
                        self._finding(
                            f"{name}(...)",
                            node.lineno,
                            "Do not read credential/secret files in tool scripts.",
                            message=f"Read sensitive file via pathlib {target!r}",
                        ))

            # Path(...).joinpath(...).read_text pattern: also inspect path construction
            if "pathlib" in lname or lname.endswith("path") or lname.endswith("joinpath") or lname in {
                    "os.path.join",
                    "os.path.expanduser",
                    "posixpath.join",
                    "ntpath.join",
            }:
                target = path_expr_text(node)
                if _matches_sensitive(target) or _matches_forbidden(target, policy):
                    findings.append(
                        self._finding(
                            evidence_snippet(target) or name,
                            node.lineno,
                            "Do not construct paths into credential directories.",
                            message=f"Path construction toward sensitive location {target!r}",
                        ))

        # Flag assignments that build sensitive paths even if never opened.
        for var, target in path_vars.items():
            findings.append(
                self._finding(
                    f"{var}={target!r}",
                    None,
                    "Do not construct credential paths in tool scripts.",
                    message=f"Sensitive path assigned to {var!r}: {target!r}",
                ))

        return findings

    def _check_bash(self, scan_input: ScanInput, policy: PolicyConfig) -> list[SafetyFinding]:
        findings: list[SafetyFinding] = []
        for lineno, line in bash_lines(scan_input.script):
            for pat in _DELETE_PATTERNS:
                if pat.search(line):
                    findings.append(
                        self._finding(
                            line,
                            lineno,
                            "Avoid rm -rf and recursive deletion of unknown paths.",
                            message=f"Recursive/forced delete: {evidence_snippet(line)}",
                        ))
                    break
            for pat, desc in _SENSITIVE_PATH_PATTERNS:
                if re.search(pat, line):
                    findings.append(
                        self._finding(
                            line,
                            lineno,
                            f"Do not touch {desc} from tool scripts.",
                            message=f"Access to sensitive path ({desc}): {evidence_snippet(line)}",
                        ))
                    break
            for sd in _SYSTEM_DIRS:
                if sd in line and (">" in line or "rm " in line or "chmod" in line or "chown" in line):
                    findings.append(
                        self._finding(
                            line,
                            lineno,
                            "Never modify or delete system directories.",
                            message=f"Operation on system directory {sd!r}",
                        ))
                    break
            for fb in policy.forbidden_paths:
                if fb in line:
                    findings.append(
                        self._finding(
                            line,
                            lineno,
                            f"Path {fb!r} is forbidden by policy.",
                            message=f"Access to forbidden path ({fb!r})",
                        ))
                    break
        return findings


def _is_recursive_delete(name: str, node: ast.Call) -> bool:
    lname = name.lower()
    if "rmtree" in lname:
        return True
    for kw in node.keywords:
        if kw.arg in {"ignore_errors", "recursive", "force"}:
            val = kw.value
            if isinstance(val, ast.Constant) and val.value:
                return True
    return False


def _is_write_open(node: ast.Call) -> bool:
    mode_val = None
    for kw in node.keywords:
        if kw.arg == "mode":
            if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                mode_val = kw.value.value
            break
    if mode_val is None and len(node.args) >= 2:
        arg = node.args[1]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            mode_val = arg.value
    if not mode_val:
        return False
    return any(m in mode_val for m in ("w", "a", "x", "+"))


def _first_str_or_path(node: ast.Call) -> str | None:
    if not node.args:
        return None
    s = get_string_literal(node.args[0])
    if s:
        return s
    return path_expr_text(node.args[0]) or None


def _path_from_attr_call(node: ast.Call, aliases: dict[str, str]) -> str:
    """Recover path text from Path(...).read_text() style calls."""
    # node.func is Attribute(value=..., attr=read_text)
    func = node.func
    if isinstance(func, ast.Attribute):
        return path_expr_text(func.value)
    return ""


def _collect_sensitive_path_vars(
    tree: ast.AST,
    aliases: dict[str, str],
    policy: PolicyConfig,
) -> dict[str, str]:
    """Map local names bound to path expressions that mention sensitive fragments."""
    path_vars: dict[str, str] = {}
    join_names = {
        "os.path.join",
        "posixpath.join",
        "ntpath.join",
        "os.path.expanduser",
        "pathlib.path.joinpath",
        "pathlib.Path.joinpath",
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        value = node.value
        text = ""
        if isinstance(value, ast.Call):
            cname = resolve_name(value.func, aliases).lower()
            if cname in join_names or cname.endswith(".join") or cname.endswith(".joinpath") or cname.endswith(
                    ".expanduser"):
                text = path_expr_text(value)
            else:
                text = path_expr_text(value)
        else:
            text = path_expr_text(value)
        # Also collect constant string fragments from nested calls/args.
        if not text and isinstance(value, ast.Call):
            parts = []
            for arg in value.args:
                s = get_string_literal(arg)
                if s:
                    parts.append(s)
                else:
                    parts.append(path_expr_text(arg))
            text = "/".join(p for p in parts if p)
        if text and (_matches_sensitive(text) or _matches_forbidden(text, policy)):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    path_vars[target.id] = text
    return path_vars


# ---------------------------------------------------------------------------
# R002 Network
# ---------------------------------------------------------------------------

_PY_NET_CALLS = {
    "requests.get",
    "requests.post",
    "requests.put",
    "requests.delete",
    "requests.patch",
    "requests.head",
    "requests.options",
    "requests.request",
    "requests.session.get",
    "requests.session.post",
    "requests.session.put",
    "requests.session.delete",
    "requests.session.request",
    "httpx.get",
    "httpx.post",
    "httpx.request",
    "httpx.client.get",
    "httpx.client.post",
    "httpx.asyncclient.get",
    "httpx.asyncclient.post",
    "aiohttp.clientsession.get",
    "aiohttp.clientsession.post",
    "urllib.request.urlopen",
    "urllib.urlopen",
    "http.client.httpconnection",
    "http.client.httpsconnection",
    "socket.socket",
    "socket.create_connection",
}

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

    def _check_python(self, scan_input: ScanInput, policy: PolicyConfig) -> list[SafetyFinding]:
        findings: list[SafetyFinding] = []
        tree = parse_python_ast(scan_input.script)
        if tree is None:
            return findings
        aliases = build_import_aliases(tree)

        # Track variables assigned from Session()/Client() constructors.
        session_vars = _collect_session_vars(tree, aliases)

        for node, name in iter_python_calls(tree, aliases):
            lname = name.lower()
            # session_var.get(...) after s = requests.Session()
            if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                var = node.func.value.id
                if var in session_vars and node.func.attr.lower() in {
                        "get",
                        "post",
                        "put",
                        "delete",
                        "patch",
                        "request",
                        "head",
                }:
                    lname = session_vars[var] + "." + node.func.attr.lower()
            # Chained constructor call: httpx.Client().get(...) / requests.Session().post(...)
            if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Call):
                ctor = resolve_name(node.func.value.func, aliases).lower()
                method = node.func.attr.lower()
                if method in {"get", "post", "put", "delete", "patch", "request", "head"}:
                    if ctor in {
                            "requests.session",
                            "requests.sessions.session",
                            "httpx.client",
                            "httpx.asyncclient",
                            "aiohttp.clientsession",
                    }:
                        if "httpx" in ctor:
                            lname = "httpx.client." + method
                        elif "aiohttp" in ctor:
                            lname = "aiohttp.clientsession." + method
                        else:
                            lname = "requests.session." + method

            if lname not in _PY_NET_CALLS and not any(
                    lname.endswith("." + m.split(".")[-1]) and m.split(".")[0] in lname for m in _PY_NET_CALLS):
                # Accept any *.get/post under requests/httpx/aiohttp namespaces
                if not _is_net_call(lname):
                    continue

            host = _extract_host_from_call(node)
            if host is None:
                findings.append(
                    self._finding(
                        f"{name}(<dynamic>)",
                        node.lineno,
                        "Use a static, allow-listed URL. Dynamic targets require human review.",
                        level=RiskLevel.MEDIUM,
                        message=f"Network call {name}() with non-static target",
                        extra={"host": "<dynamic>"},
                    ))
                continue
            if not policy.is_domain_allowed(host):
                findings.append(
                    self._finding(
                        f"{name}(host={host!r})",
                        node.lineno,
                        f"Add {host!r} to whitelisted_domains or remove the call.",
                        message=f"Network call {name}() to non-allow-listed host {host!r}",
                        extra={"host": host},
                    ))
        return findings

    def _check_bash(self, scan_input: ScanInput, policy: PolicyConfig) -> list[SafetyFinding]:
        findings: list[SafetyFinding] = []
        for lineno, line in bash_lines(scan_input.script):
            tokens = line.split()
            if not tokens:
                continue
            # Skip leading env assignments: FOO=bar curl ...
            cmd = tokens[0]
            idx = 0
            while idx < len(tokens) and "=" in tokens[idx] and not tokens[idx].startswith("-"):
                idx += 1
            if idx < len(tokens):
                cmd = tokens[idx]
            cmd_base = cmd.split("/")[-1]
            if cmd_base not in _BASH_NET_COMMANDS:
                # Also catch /dev/tcp redirections
                if "/dev/tcp/" in line:
                    findings.append(
                        self._finding(
                            line,
                            lineno,
                            "Avoid /dev/tcp redirections for network egress.",
                            message="Bash /dev/tcp network egress",
                        ))
                continue
            host = _extract_host_from_bash(line, cmd_base)
            if host is None:
                findings.append(
                    self._finding(
                        line,
                        lineno,
                        "Use a static, allow-listed URL.",
                        level=RiskLevel.MEDIUM,
                        message=f"{cmd_base} with non-static target",
                        extra={"host": "<dynamic>"},
                    ))
                continue
            if not policy.is_domain_allowed(host):
                findings.append(
                    self._finding(
                        line,
                        lineno,
                        f"Add {host!r} to whitelisted_domains or remove the call.",
                        message=f"{cmd_base} to non-allow-listed host {host!r}",
                        extra={"host": host},
                    ))
        return findings


def _is_net_call(lname: str) -> bool:
    roots = ("requests.", "httpx.", "aiohttp.", "urllib.", "http.client.", "socket.")
    methods = (".get", ".post", ".put", ".delete", ".patch", ".request", ".urlopen", ".connect")
    if lname in {"socket.socket", "socket.create_connection"}:
        return True
    return any(lname.startswith(r) for r in roots) and any(lname.endswith(m) for m in methods)


def _collect_session_vars(tree: ast.AST, aliases: dict[str, str]) -> dict[str, str]:
    """Map local var names bound to requests.Session / httpx.Client instances."""
    sessions: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not isinstance(node.value, ast.Call):
            continue
        name = resolve_name(node.value.func, aliases).lower()
        tag = None
        if name in {"requests.session", "requests.sessions.session"}:
            tag = "requests.session"
        elif name in {"httpx.client", "httpx.asyncclient"}:
            tag = "httpx.client"
        elif name in {"aiohttp.clientsession"}:
            tag = "aiohttp.clientsession"
        if tag is None:
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                sessions[target.id] = tag
    return sessions


def _extract_host_from_call(node: ast.Call) -> str | None:
    if not node.args:
        for kw in node.keywords:
            if kw.arg in {"url", "host", "address", "server_hostname"}:
                return _host_from_value(kw.value)
        return None
    first = node.args[0]
    # socket.create_connection(("host", port)) / connect((host, port))
    if isinstance(first, (ast.Tuple, ast.List)) and first.elts:
        return _host_from_value(first.elts[0])
    return _host_from_value(first)


def _host_from_value(value: ast.AST) -> str | None:
    s = get_string_literal(value)
    if s is None:
        # BinOp / JoinedStr partial — cannot fully resolve
        parts = []
        if isinstance(value, ast.BinOp):
            from ._ast_utils import collect_string_parts
            parts = collect_string_parts(value)
        if parts:
            joined = "".join(parts)
            return _host_from_string(joined)
        return None
    return _host_from_string(s)


def _host_from_string(s: str) -> str | None:
    if "://" in s:
        parsed = urlparse(s)
        host = parsed.hostname
        return host.lower() if host else None
    host = s.split("/")[0].split(":")[0].strip()
    if host and ("." in host or host == "localhost"):
        return host.lower()
    return None


def _extract_host_from_bash(line: str, cmd: str) -> str | None:
    url_match = re.search(r"https?://([^\s'\"|>;]+)", line)
    if url_match:
        host = url_match.group(1).split("/")[0].split(":")[0]
        return host.lower() if host else None
    tokens = line.split()
    for tok in tokens:
        if "://" in tok:
            return _host_from_string(tok)
    at_match = re.search(r"@([^\s:]+)", line)
    if at_match:
        return at_match.group(1).lower()
    if cmd in {"nc", "netcat", "telnet"} and len(tokens) >= 2:
        # skip flags
        for tok in tokens[1:]:
            if not tok.startswith("-"):
                return tok.lower()
    return None


# ---------------------------------------------------------------------------
# R003 Process / system
# ---------------------------------------------------------------------------

_PY_PROCESS_CALLS = {
    "os.system",
    "os.popen",
    "os.exec",
    "os.execv",
    "os.execve",
    "os.spawn",
    "os.spawnl",
    "os.spawnle",
    "os.spawnlp",
    "os.spawnlpe",
    "os.spawnv",
    "os.spawnve",
    "os.spawnvp",
    "os.spawnvpe",
    "subprocess.popen",
    "subprocess.run",
    "subprocess.call",
    "subprocess.check_call",
    "subprocess.check_output",
    "subprocess.getoutput",
    "subprocess.getstatusoutput",
    "commands.getoutput",
    "commands.getstatusoutput",
}

_PRIVILEGE_CMDS = {"sudo", "su", "doas", "pkexec", "runuser"}
_INJECTION_BUILTINS = {"eval", "exec", "compile", "builtins.eval", "builtins.exec"}
_DECODE_EXEC_BASH = re.compile(
    r"(base64\s+-d|base64\s+--decode|xxd\s+-r|openssl\s+enc\s+-d).*\|\s*(sh|bash|zsh|python)",
    re.IGNORECASE,
)


class ProcessRule(SafetyRule):
    """Detect process spawning, shell injection, and privilege escalation."""

    rule_id = "R003_process_system"
    rule_name = "Process / System Command"
    risk_type = "process"
    default_level = RiskLevel.HIGH
    languages = ("python", "bash")

    def check(self, scan_input: ScanInput, policy: PolicyConfig) -> list[SafetyFinding]:
        lang = normalize_language(scan_input)
        if lang == "python":
            return self._check_python(scan_input, policy)
        return self._check_bash(scan_input, policy)

    def _check_python(self, scan_input: ScanInput, policy: PolicyConfig) -> list[SafetyFinding]:
        findings: list[SafetyFinding] = []
        tree = parse_python_ast(scan_input.script)
        if tree is None:
            return findings
        aliases = build_import_aliases(tree)

        # Track local aliases of getattr (e.g. gattr = getattr).
        getattr_names = {"getattr", "builtins.getattr"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Name):
                src = resolve_name(node.value, aliases).lower()
                if src in {"getattr", "builtins.getattr"}:
                    for t in node.targets:
                        if isinstance(t, ast.Name):
                            getattr_names.add(t.id)

        for node, name in iter_python_calls(tree, aliases):
            lname = name.lower()
            if lname in _PY_PROCESS_CALLS or any(
                    lname.endswith("." + c.split(".")[-1]) and c.split(".")[0] in lname
                    for c in _PY_PROCESS_CALLS) or lname in {c.lower()
                                                             for c in _PY_PROCESS_CALLS}:
                shell_true = _has_shell_true(node)
                findings.append(
                    self._finding(
                        f"{name}(...)",
                        node.lineno,
                        "Avoid spawning subprocesses; if unavoidable use shell=False and validate args.",
                        level=RiskLevel.CRITICAL if shell_true else RiskLevel.HIGH,
                        message=f"Process spawn via {name}()",
                        extra={"shell_true": shell_true},
                    ))
            # getattr(os, "system") pattern, including local aliases of getattr.
            if (lname in getattr_names or name in getattr_names) and node.args:
                obj = resolve_name(node.args[0], aliases).lower() if node.args else ""
                attr = get_string_literal(node.args[1]) if len(node.args) > 1 else None
                if obj in {"os", "subprocess"} and attr in {
                        "system",
                        "popen",
                        "exec",
                        "execv",
                        "run",
                        "call",
                        "Popen",
                        "popen",
                }:
                    findings.append(
                        self._finding(
                            f"getattr({obj}, {attr!r})",
                            node.lineno,
                            "Dynamic process binding via getattr is not allowed.",
                            level=RiskLevel.CRITICAL,
                            message=f"Dynamic process spawn via getattr({obj}, {attr!r})",
                        ))
            if lname in _INJECTION_BUILTINS or name in _INJECTION_BUILTINS:
                findings.append(
                    self._finding(
                        f"{name}(...)",
                        node.lineno,
                        f"Remove {name}(); it allows arbitrary code execution.",
                        level=RiskLevel.CRITICAL,
                        message=f"Use of {name}() enables shell/code injection",
                    ))
            # importlib.import_module("os") — flag dynamic import of process modules
            if lname in {"importlib.import_module", "importlib.__import__", "__import__"}:
                mod = get_string_literal(node.args[0]) if node.args else None
                if mod in {"os", "subprocess", "commands", "pty"}:
                    findings.append(
                        self._finding(
                            f"{name}({mod!r})",
                            node.lineno,
                            "Do not dynamically import process-control modules.",
                            level=RiskLevel.HIGH,
                            message=f"Dynamic import of process module {mod!r}",
                        ))
        return findings

    def _check_bash(self, scan_input: ScanInput, policy: PolicyConfig) -> list[SafetyFinding]:
        findings: list[SafetyFinding] = []
        for lineno, line in bash_lines(scan_input.script):
            tokens = line.split()
            if not tokens:
                continue
            cmd = tokens[0].split("/")[-1]

            if policy.strict_command_allowlist and policy.allowed_commands:
                # Skip env assignments
                check_cmd = cmd
                if "=" in check_cmd and len(tokens) > 1:
                    check_cmd = tokens[1].split("/")[-1]
                if check_cmd not in policy.allowed_commands and check_cmd not in {
                        "if",
                        "then",
                        "fi",
                        "for",
                        "do",
                        "done",
                        "while",
                        "case",
                        "esac",
                        "[",
                        "[[",
                        "]",
                        "]]",
                        "export",
                        "set",
                        "unset",
                        ":",
                        "true",
                        "false",
                }:
                    findings.append(
                        self._finding(
                            line,
                            lineno,
                            f"Command {check_cmd!r} is not in allowed_commands.",
                            level=RiskLevel.HIGH,
                            message=f"Command not in allow-list: {check_cmd}",
                        ))

            if cmd in _PRIVILEGE_CMDS:
                findings.append(
                    self._finding(
                        line,
                        lineno,
                        f"Remove {cmd}; tool scripts must not escalate privileges.",
                        level=RiskLevel.CRITICAL,
                        message=f"Privilege escalation via {cmd}",
                    ))
            if line.rstrip().endswith("&") and not line.rstrip().endswith("&&"):
                findings.append(
                    self._finding(
                        line,
                        lineno,
                        "Avoid backgrounding processes in tool scripts.",
                        level=RiskLevel.MEDIUM,
                        message="Background process spawn",
                    ))
            if line.count("|") >= 3:
                findings.append(
                    self._finding(
                        line,
                        lineno,
                        "Review long pipelines for resource abuse.",
                        level=RiskLevel.LOW,
                        message=f"Complex shell pipeline ({line.count('|')} stages)",
                    ))
            if re.search(r"\$\([^)]*\$\{?[A-Za-z_][A-Za-z0-9_]*\}?[^)]*\)", line):
                findings.append(
                    self._finding(
                        line,
                        lineno,
                        "Avoid nesting $() with variable expansion; sanitize inputs.",
                        level=RiskLevel.HIGH,
                        message="Nested command substitution with variable expansion (injection risk)",
                    ))
            if re.search(r"\beval\b", line):
                findings.append(
                    self._finding(
                        line,
                        lineno,
                        "Remove eval; it enables shell injection.",
                        level=RiskLevel.CRITICAL,
                        message="Use of eval in bash",
                    ))
            if _DECODE_EXEC_BASH.search(line) or re.search(r"\|\s*(sh|bash|zsh)\b", line) and re.search(
                    r"base64|xxd|openssl", line, re.IGNORECASE):
                findings.append(
                    self._finding(
                        line,
                        lineno,
                        "Decode-and-execute pipelines are not allowed.",
                        level=RiskLevel.CRITICAL,
                        message="Decode-to-shell pipeline (base64/xxd | sh)",
                    ))
        return findings


def _has_shell_true(node: ast.Call) -> bool:
    for kw in node.keywords:
        if kw.arg == "shell":
            val = kw.value
            if isinstance(val, ast.Constant) and val.value is True:
                return True
            if isinstance(val, ast.Name) and val.id == "True":
                return True
    return False


# ---------------------------------------------------------------------------
# R004 Dependency install
# ---------------------------------------------------------------------------

_INSTALL_REGEXES = [
    re.compile(r"\bpip3?\s+install\b", re.IGNORECASE),
    re.compile(r"\bpython3?\s+-m\s+pip\s+install\b", re.IGNORECASE),
    re.compile(r"\bnpm\s+install\b", re.IGNORECASE),
    re.compile(r"\bnpm\s+ci\b", re.IGNORECASE),
    re.compile(r"\byarn\s+add\b", re.IGNORECASE),
    re.compile(r"\bapt(?:-get)?\s+install\b", re.IGNORECASE),
    re.compile(r"\baptitude\s+install\b", re.IGNORECASE),
    re.compile(r"\bdnf\s+install\b", re.IGNORECASE),
    re.compile(r"\byum\s+install\b", re.IGNORECASE),
    re.compile(r"\bbrew\s+install\b", re.IGNORECASE),
    re.compile(r"\bconda\s+install\b", re.IGNORECASE),
    re.compile(r"\bpoetry\s+add\b", re.IGNORECASE),
    re.compile(r"\buv\s+pip\s+install\b", re.IGNORECASE),
    re.compile(r"\bgo\s+get\b", re.IGNORECASE),
    re.compile(r"\bcargo\s+add\b", re.IGNORECASE),
    re.compile(r"\bgem\s+install\b", re.IGNORECASE),
    re.compile(r"\bcomposer\s+require\b", re.IGNORECASE),
]


class DependencyInstallRule(SafetyRule):
    """Detect package installation commands that mutate the environment."""

    rule_id = "R004_dependency_install"
    rule_name = "Dependency Installation"
    risk_type = "dependency_install"
    default_level = RiskLevel.HIGH
    languages = ("python", "bash")

    def check(self, scan_input: ScanInput, policy: PolicyConfig) -> list[SafetyFinding]:
        findings: list[SafetyFinding] = []
        if normalize_language(scan_input) == "python":
            findings.extend(self._check_python(scan_input, policy))
        findings.extend(self._check_shell_substrings(scan_input, policy))
        return findings

    def _check_python(self, scan_input: ScanInput, policy: PolicyConfig) -> list[SafetyFinding]:
        findings: list[SafetyFinding] = []
        tree = parse_python_ast(scan_input.script)
        if tree is None:
            return findings
        aliases = build_import_aliases(tree)
        for node, name in iter_python_calls(tree, aliases):
            lname = name.lower()
            if lname == "pip.main":
                findings.append(
                    self._finding(
                        f"{name}(...)",
                        node.lineno,
                        "Do not install packages at runtime; declare dependencies up front.",
                        message="Programmatic pip install via pip.main()",
                    ))
            # subprocess.run(["pip", "install", ...])
            if lname in _PY_PROCESS_CALLS or "subprocess" in lname:
                args_text = _subprocess_args_text(node)
                for pat in _INSTALL_REGEXES:
                    if pat.search(args_text):
                        findings.append(
                            self._finding(
                                args_text,
                                node.lineno,
                                "Pin dependencies in a lockfile instead of installing at runtime.",
                                message=f"Dependency install via {name}: {evidence_snippet(args_text)}",
                            ))
                        break
        return findings

    def _check_shell_substrings(self, scan_input: ScanInput, policy: PolicyConfig) -> list[SafetyFinding]:
        findings: list[SafetyFinding] = []
        for lineno, line in bash_lines(scan_input.script):
            for pat in _INSTALL_REGEXES:
                if pat.search(line):
                    findings.append(
                        self._finding(
                            line,
                            lineno,
                            "Pin dependencies in a lockfile instead of installing at runtime.",
                            message=f"Dependency install: {evidence_snippet(line)}",
                        ))
                    break
        if normalize_language(scan_input) == "python":
            tree = parse_python_ast(scan_input.script)
            if tree is not None:
                for node in ast.walk(tree):
                    if isinstance(node, ast.Constant) and isinstance(node.value, str):
                        for pat in _INSTALL_REGEXES:
                            if pat.search(node.value):
                                findings.append(
                                    self._finding(
                                        node.value,
                                        getattr(node, "lineno", None),
                                        "Do not embed install commands in string literals.",
                                        message="Embedded dependency install in string literal",
                                    ))
                                break
        return findings


def _subprocess_args_text(node: ast.Call) -> str:
    parts: list[str] = []
    for arg in node.args:
        s = get_string_literal(arg)
        if s is not None:
            parts.append(s)
            continue
        if isinstance(arg, (ast.List, ast.Tuple)):
            for elt in arg.elts:
                es = get_string_literal(elt)
                if es is not None:
                    parts.append(es)
    return " ".join(parts)


# ---------------------------------------------------------------------------
# R005 Resource abuse
# ---------------------------------------------------------------------------

_FORK_BOMB = re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\};\s*:", re.IGNORECASE)
_LONG_SLEEP_BASH = re.compile(r"\bsleep\s+(\d+)", re.IGNORECASE)
_DD_WRITE = re.compile(r"\bdd\b", re.IGNORECASE)
_BIG_WRITE = re.compile(r"(head|tail|yes|/dev/zero|/dev/urandom)", re.IGNORECASE)
_HIGH_CONCURRENCY = {
    "concurrent.futures.threadpoolexecutor",
    "concurrent.futures.processpoolexecutor",
    "multiprocessing.pool",
    "asyncio.gather",
}


class ResourceAbuseRule(SafetyRule):
    """Detect resource abuse patterns: infinite loops, fork bombs, big writes."""

    rule_id = "R005_resource_abuse"
    rule_name = "Resource Abuse"
    risk_type = "resource_abuse"
    default_level = RiskLevel.HIGH
    languages = ("python", "bash")

    def check(self, scan_input: ScanInput, policy: PolicyConfig) -> list[SafetyFinding]:
        findings: list[SafetyFinding] = []
        if normalize_language(scan_input) == "python":
            findings.extend(self._check_python(scan_input, policy))
        findings.extend(self._check_bash(scan_input, policy))
        return findings

    def _check_python(self, scan_input: ScanInput, policy: PolicyConfig) -> list[SafetyFinding]:
        findings: list[SafetyFinding] = []
        tree = parse_python_ast(scan_input.script)
        if tree is None:
            return findings
        aliases = build_import_aliases(tree)

        for node in ast.walk(tree):
            if isinstance(node, ast.While):
                if _is_truthy_constant(node.test) and not _has_direct_break(node):
                    findings.append(
                        self._finding(
                            f"while {ast.unparse(node.test)}: ...",
                            node.lineno,
                            "Add a termination condition or bounded iteration.",
                            level=RiskLevel.HIGH,
                            message="Infinite while loop with no break",
                        ))
            if isinstance(node, ast.Call):
                fname = resolve_name(node.func, aliases)
                fl = fname.lower()
                if "sleep" in fl.split(".")[-1]:
                    arg = node.args[0] if node.args else None
                    secs = _const_int(arg)
                    if secs is not None and secs >= policy.max_timeout_seconds:
                        findings.append(
                            self._finding(
                                f"sleep({secs})",
                                node.lineno,
                                f"Keep sleeps below {policy.max_timeout_seconds}s.",
                                level=RiskLevel.MEDIUM,
                                message=f"Long sleep({secs}s) exceeds timeout budget",
                            ))
                    elif secs is None and arg is not None:
                        findings.append(
                            self._finding(
                                "sleep(<dynamic>)",
                                node.lineno,
                                "Use a bounded constant sleep duration.",
                                level=RiskLevel.LOW,
                                message="sleep() with non-constant duration",
                            ))
                if fl in _HIGH_CONCURRENCY or any(c in fl for c in _HIGH_CONCURRENCY):
                    findings.append(
                        self._finding(
                            f"{fname}(...)",
                            node.lineno,
                            "Bound max_workers; unbounded pools can exhaust resources.",
                            level=RiskLevel.MEDIUM,
                            message=f"High-concurrency primitive {fname}()",
                        ))
        return findings

    def _check_bash(self, scan_input: ScanInput, policy: PolicyConfig) -> list[SafetyFinding]:
        findings: list[SafetyFinding] = []
        for lineno, line in bash_lines(scan_input.script):
            if _FORK_BOMB.search(line):
                findings.append(
                    self._finding(
                        line,
                        lineno,
                        "Remove fork bomb patterns entirely.",
                        level=RiskLevel.CRITICAL,
                        message="Fork bomb detected",
                    ))
            m = _LONG_SLEEP_BASH.search(line)
            if m and int(m.group(1)) >= policy.max_timeout_seconds:
                findings.append(
                    self._finding(
                        line,
                        lineno,
                        f"Keep sleeps below {policy.max_timeout_seconds}s.",
                        level=RiskLevel.MEDIUM,
                        message=f"Long sleep {m.group(1)}s exceeds timeout budget",
                    ))
            if _DD_WRITE.search(line):
                findings.append(
                    self._finding(
                        line,
                        lineno,
                        "Avoid dd in tool scripts; use bounded file operations.",
                        level=RiskLevel.HIGH,
                        message="dd can write large amounts of data",
                    ))
            if _BIG_WRITE.search(line) and ">" in line:
                findings.append(
                    self._finding(
                        line,
                        lineno,
                        "Cap output size; unbounded writes can fill disk.",
                        level=RiskLevel.MEDIUM,
                        message="Unbounded large write via shell",
                    ))
        return findings


def _is_truthy_constant(node: ast.AST) -> bool:
    return (isinstance(node, ast.Constant) and bool(node.value)) or (isinstance(node, ast.Name)
                                                                     and node.id in {"True", "__debug__"})


def _has_direct_break(loop_node: ast.AST) -> bool:
    """True when the loop body contains a break not nested in an inner loop."""
    body = getattr(loop_node, "body", []) + getattr(loop_node, "orelse", [])
    for stmt in body:
        if isinstance(stmt, ast.Break):
            return True
        if isinstance(stmt, (ast.For, ast.While, ast.AsyncFor)):
            continue
        for child in ast.walk(stmt):
            if child is stmt:
                continue
            if isinstance(child, (ast.For, ast.While, ast.AsyncFor)):
                # Do not descend into nested loops.
                break
            if isinstance(child, ast.Break):
                return True
        else:
            # walk finished without hitting nested loop break-skip — check nested
            if _walk_break_skipping_loops(stmt):
                return True
    return False


def _walk_break_skipping_loops(node: ast.AST) -> bool:
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.Break):
            return True
        if isinstance(child, (ast.For, ast.While, ast.AsyncFor)):
            continue
        if _walk_break_skipping_loops(child):
            return True
    return False


def _const_int(node: ast.AST | None) -> int | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return int(node.value)
    return None


# ---------------------------------------------------------------------------
# R006 Secret leak
# ---------------------------------------------------------------------------

_DEFAULT_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)aws(.{0,20})?(secret|sk)[^\n]{0,20}[A-Za-z0-9/+=]{40}"),
    re.compile(r"(?i)(api[_-]?key|access[_-]?token|auth[_-]?token|secret[_-]?key)"
               r"\s*[=:]\s*['\"][A-Za-z0-9_\-]{16,}['\"]"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9_\-\.]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"gh[ps]_[A-Za-z0-9]{36}"),
    re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
]

_SECRET_NAME_HINTS = {
    "api_key",
    "apikey",
    "token",
    "password",
    "passwd",
    "secret",
    "access_key",
    "private_key",
    "client_secret",
    "openai_api_key",
    "auth_token",
    "access_token",
    "secret_key",
}

_LEAK_SINKS_PY = {
    "print",
    "logging.info",
    "logging.debug",
    "logging.warning",
    "logging.error",
    "logging.critical",
    "logger.info",
    "logger.debug",
    "logger.warning",
    "logger.error",
    "logger.critical",
    "open",
    "requests.post",
    "requests.put",
    "requests.get",
    "httpx.post",
}

_ENV_SECRET_KEYS = re.compile(r"(?i)(API[_-]?KEY|TOKEN|SECRET|PASSWORD|PASSWD|PRIVATE[_-]?KEY|ACCESS[_-]?KEY|"
                              r"OPENAI|ANTHROPIC|AWS_SECRET|GITHUB_TOKEN|GH_TOKEN)")


class SecretLeakRule(SafetyRule):
    """Detect sensitive data being written to logs, files, or network."""

    rule_id = "R006_secret_leak"
    rule_name = "Sensitive Information Leakage"
    risk_type = "secret_leak"
    default_level = RiskLevel.CRITICAL
    languages = ("python", "bash")

    def check(self, scan_input: ScanInput, policy: PolicyConfig) -> list[SafetyFinding]:
        lang = normalize_language(scan_input)
        findings: list[SafetyFinding] = []
        if lang == "python":
            findings.extend(self._check_python(scan_input, policy))
        findings.extend(self._check_bash(scan_input, policy))
        return findings

    def _patterns(self, policy: PolicyConfig) -> list[re.Pattern]:
        patterns = list(_DEFAULT_SECRET_PATTERNS)
        for extra in policy.secret_patterns:
            try:
                patterns.append(re.compile(extra))
            except re.error:
                continue
        return patterns

    def _check_python(self, scan_input: ScanInput, policy: PolicyConfig) -> list[SafetyFinding]:
        findings: list[SafetyFinding] = []
        tree = parse_python_ast(scan_input.script)
        if tree is None:
            return findings
        aliases = build_import_aliases(tree)
        patterns = self._patterns(policy)

        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for pat in patterns:
                    if pat.search(node.value):
                        findings.append(
                            self._finding(
                                redact(node.value),
                                getattr(node, "lineno", None),
                                "Move secrets to env vars / secret manager; never hardcode.",
                                message="Hardcoded secret in string literal",
                            ))
                        break

        for node, name in iter_python_calls(tree, aliases):
            lname = name.lower()
            # os.environ["OPENAI_API_KEY"] / os.getenv("TOKEN") printed or sent
            if lname in {"os.getenv", "os.environ.get"} or lname.endswith(".getenv"):
                key = get_string_literal(node.args[0]) if node.args else None
                if key and _ENV_SECRET_KEYS.search(key):
                    findings.append(
                        self._finding(
                            f"{name}({key!r})",
                            node.lineno,
                            "Do not read secret env vars for logging or exfiltration.",
                            message=f"Secret environment variable access {key!r}",
                            level=RiskLevel.HIGH,
                        ))

            # Only inspect arguments of known leak sinks / network calls.
            # Non-sink helpers like validate(token) must not be auto-denied.
            if lname not in {s.lower() for s in _LEAK_SINKS_PY} and not _is_net_call(lname):
                continue
            for arg in node.args:
                if isinstance(arg, ast.Name) and _looks_like_secret_name(arg.id):
                    findings.append(
                        self._finding(
                            f"{name}(..., {arg.id}, ...)",
                            node.lineno,
                            f"Do not log or write {arg.id}; redact before output.",
                            message=f"Secret-like variable {arg.id!r} passed to {name}()",
                        ))
                elif isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    for pat in patterns:
                        if pat.search(arg.value):
                            findings.append(
                                self._finding(
                                    f"{name}({redact(arg.value)})",
                                    node.lineno,
                                    "Do not pass secrets to logging/file functions.",
                                    message=f"Secret literal passed to {name}()",
                                ))
                            break
                elif isinstance(arg, ast.Subscript):
                    # os.environ["OPENAI_API_KEY"]
                    if _is_environ_subscript(arg, aliases):
                        findings.append(
                            self._finding(
                                f"{name}(os.environ[...])",
                                node.lineno,
                                "Do not print or transmit secret environment variables.",
                                message=f"Environment secret passed to {name}()",
                            ))
        # Direct print(os.environ["X"])
        for node in ast.walk(tree):
            if isinstance(node, ast.Subscript) and _is_environ_subscript(node, aliases):
                key = get_string_literal(node.slice) if hasattr(node, "slice") else None
                if key is None or _ENV_SECRET_KEYS.search(str(key) if key else "SECRET"):
                    # Flag any environ subscript used as expression; severity HIGH
                    findings.append(
                        self._finding(
                            "os.environ[...]",
                            getattr(node, "lineno", None),
                            "Avoid reading secret environment keys in tool scripts.",
                            message="Direct access to environment secret key",
                            level=RiskLevel.HIGH,
                        ))
        return findings

    def _check_bash(self, scan_input: ScanInput, policy: PolicyConfig) -> list[SafetyFinding]:
        findings: list[SafetyFinding] = []
        patterns = self._patterns(policy)
        for lineno, line in bash_lines(scan_input.script):
            assign_match = re.match(r"(?i)([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(\S+)", line)
            if assign_match and _looks_like_secret_name(assign_match.group(1)):
                val = assign_match.group(2).strip("'\"")
                if len(val) >= 12:
                    findings.append(
                        self._finding(
                            f"{assign_match.group(1)}={redact(val)}",
                            lineno,
                            "Load secrets from env, not inline assignment.",
                            message=f"Secret assigned to {assign_match.group(1)!r}",
                        ))
            for pat in patterns:
                if pat.search(line):
                    findings.append(
                        self._finding(
                            redact(line),
                            lineno,
                            "Remove hardcoded secrets from scripts.",
                            message="Secret pattern in command",
                        ))
                    break
            # curl --data @.env / -d @$HOME/.env
            if re.search(r"(curl|wget).*(--data|-d|--upload-file|-T)\s*@?", line, re.I):
                if re.search(r"\.env|id_rsa|credentials|token|secret", line, re.I):
                    findings.append(
                        self._finding(
                            line,
                            lineno,
                            "Do not upload credential files over the network.",
                            message="Possible credential exfiltration via curl/wget",
                        ))
        return findings


def _looks_like_secret_name(name: str) -> bool:
    lname = name.lower()
    return any(hint in lname for hint in _SECRET_NAME_HINTS)


def _is_environ_subscript(node: ast.Subscript, aliases: dict[str, str]) -> bool:
    value = node.value
    resolved = resolve_name(value, aliases).lower()
    return resolved in {"os.environ", "os.environb"}


def redact(text: str, keep: int = 4) -> str:
    """Redact all but the first *keep* chars of a suspected secret."""
    if len(text) <= keep:
        return "***"
    return text[:keep] + "***"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# R007 Dynamic code execution
# ---------------------------------------------------------------------------


class CodeExecutionRule(SafetyRule):
    """Detect dynamic code execution patterns that enable arbitrary code run."""

    rule_id = "R007_code_execution"
    rule_name = "Dynamic Code Execution"
    risk_type = "code_execution"
    default_level = RiskLevel.CRITICAL
    languages = ("python", "bash")

    def check(self, scan_input: ScanInput, policy: PolicyConfig) -> list[SafetyFinding]:
        findings: list[SafetyFinding] = []
        lang = normalize_language(scan_input)
        if lang == "python":
            tree = parse_python_ast(scan_input.script)
            if tree is None:
                return findings
            aliases = build_import_aliases(tree)
            for node, name in iter_python_calls(tree, aliases):
                lname = name.lower()
                if lname in {"eval", "exec", "compile", "builtins.eval", "builtins.exec", "builtins.compile"}:
                    findings.append(
                        self._finding(
                            f"{name}(...)",
                            node.lineno,
                            f"Remove {name}(); it allows arbitrary code execution.",
                            level=RiskLevel.CRITICAL,
                            message=f"Dynamic code execution via {name}()",
                        ))
                if lname in {"__import__", "importlib.import_module"}:
                    mod = get_string_literal(node.args[0]) if node.args else None
                    if mod in {"os", "subprocess", "pty", "ctypes"}:
                        findings.append(
                            self._finding(
                                f"{name}({mod!r})",
                                node.lineno,
                                "Do not dynamically import process-control modules.",
                                level=RiskLevel.HIGH,
                                message=f"Dynamic import of process module {mod!r}",
                            ))
        for lineno, line in bash_lines(scan_input.script):
            if re.search(r"\beval\b", line):
                findings.append(
                    self._finding(
                        line,
                        lineno,
                        "Remove eval; it enables shell injection.",
                        level=RiskLevel.CRITICAL,
                        message="Use of eval in bash",
                    ))
            if re.search(r"base64\s+(-d|--decode).*\|\s*(sh|bash|zsh|python)", line,
                         re.I) or (re.search(r"\|\s*(sh|bash|zsh)\b", line)
                                   and re.search(r"base64|xxd|openssl", line, re.I)):
                findings.append(
                    self._finding(
                        line,
                        lineno,
                        "Decode-and-execute pipelines are not allowed.",
                        level=RiskLevel.CRITICAL,
                        message="Decode-to-shell pipeline (base64/xxd | sh)",
                    ))
            # find -delete / xargs rm
            if re.search(r"\bfind\b.*(-delete|-exec\s+rm\b)", line, re.I):
                findings.append(
                    self._finding(
                        line,
                        lineno,
                        "Avoid find -delete / -exec rm in tool scripts.",
                        level=RiskLevel.HIGH,
                        message="find-based mass delete",
                    ))
            if re.search(r"\bxargs\b.*\brm\b", line, re.I):
                findings.append(
                    self._finding(
                        line,
                        lineno,
                        "Avoid xargs rm pipelines.",
                        level=RiskLevel.HIGH,
                        message="xargs rm mass delete",
                    ))
        return findings


def default_rules() -> list[SafetyRule]:
    """Return the default ordered set of built-in safety rules."""
    return [
        DangerousFilesRule(),
        NetworkRule(),
        ProcessRule(),
        DependencyInstallRule(),
        ResourceAbuseRule(),
        SecretLeakRule(),
        CodeExecutionRule(),
    ]
