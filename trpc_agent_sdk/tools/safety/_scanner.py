# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Deterministic Python and Bash safety scanner."""

from __future__ import annotations

import ast
import hashlib
import os
from pathlib import Path
import re
import shlex
import sys
import time
from typing import Iterable
from urllib.parse import urlparse

from ._bash_analyzer import analyze_bash
from ._decision import aggregate_report
from ._ir import AnalysisResult
from ._models import AnalysisStatus
from ._models import SafetyDecision
from ._models import SafetyFinding
from ._models import SafetyReport
from ._models import SafetyScanRequest
from ._models import ScriptLanguage
from ._policy import SafetyPolicy
from ._rules import RULE_SPECS
from ._rules import make_finding
from ._rules import NON_RELAXABLE_REVIEW_RULE_IDS
from ._rules import SafetyRule
from ._values import AbstractValue
from ._values import ValueState

_SENSITIVE_NAME_RE = re.compile(
    r"(?:^|_)(?:api_?key|token|secret|password|passwd|passphrase|credential|"
    r"private_?key|database_?url|db_?pass(?:word)?)(?:$|_|\d)",
    re.IGNORECASE,
)
_ASSIGNMENT_RE = re.compile(
    r"(?i)\b([A-Za-z_][A-Za-z0-9_]*(?:key|token|secret|password|credential)[A-Za-z0-9_]*)"
    r"\s*=\s*([\"'])(.*?)\2", )
_KEYED_SECRET_RE = re.compile(
    r"""(?ix)
    (\b(?:api[-_]?key|token|secret|password|passwd|credential|private[-_]?key)\b
    \s*[:=]\s*)
    ([A-Za-z0-9._~+/=-]+)
    """, )
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_API_TOKEN_RE = re.compile(r"\b(?:sk|pk)-[A-Za-z0-9_-]{16,}\b")
_SENSITIVE_MAPPING_RE = re.compile(
    r"""(?ix)
    (["']?(?:x[-_]?api[-_]?key|api[-_]?key|authorization|token|secret|password|passwd|
    credential|private[-_]?key)["']?\s*:\s*)
    (["'])(.*?)\2
    """, )
_SENSITIVE_OPTION_RE = re.compile(
    r"""(?ix)
    (\s(?:-b|-u|--cookie|--user|--password|--token|--api-key)(?:=|\s+))
    (?:"[^"]*"|'[^']*'|[^\s]+)
    """, )
_SENSITIVE_HEADER_RE = re.compile(
    r"(?i)\b(?:authorization|proxy-authorization|cookie|set-cookie)\s*:\s*"
    r"(?:[^\s]+(?:\s+[^\s]+)?)", )
_SENSITIVE_ARGUMENT_RE = re.compile(
    r"""(?ix)
    \b(auth|cookies?)\s*=\s*
    (?:\([^)]*\)|\{[^}]*\}|["'][^"']*["']|[^,\s)]+)
    """, )
_COMPACT_COOKIE_RE = re.compile(r"(?i)(\s-b)[^\s]+")
_URL_USERINFO_RE = re.compile(r"(?i)(https?://)[^/\s@]+@")
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [^-]*PRIVATE KEY-----.*?(?:-----END [^-]*PRIVATE KEY-----|$)",
    re.DOTALL,
)
_LONG_RANDOM_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9_+/=-]{32,}(?![A-Za-z0-9])")
_SYSTEM_PATHS = (
    Path("/bin"),
    Path("/boot"),
    Path("/dev"),
    Path("/etc"),
    Path("/lib"),
    Path("/Library"),
    Path("/private/etc"),
    Path("/proc"),
    Path("/sbin"),
    Path("/sys"),
    Path("/System"),
    Path("/usr"),
    Path("/var"),
)
_SAFE_SYSTEM_WRITE_PATHS = {
    Path("/dev/null"),
}
_LOCAL_BINDING_PREFIX = "__local__."
_NETWORK_FUNCTIONS = {
    "requests.get",
    "requests.post",
    "requests.put",
    "requests.patch",
    "requests.delete",
    "requests.head",
    "requests.options",
    "requests.request",
    "httpx.get",
    "httpx.post",
    "httpx.put",
    "httpx.patch",
    "httpx.delete",
    "httpx.head",
    "httpx.options",
    "httpx.request",
    "aiohttp.request",
    "urllib.request.urlopen",
}
_KNOWN_CLIENT_CONSTRUCTORS = {
    "aiohttp.ClientSession",
    "httpx.AsyncClient",
    "httpx.Client",
    "requests.Session",
    "socket.socket",
}
_KNOWN_IMPORT_ROOTS = set(sys.stdlib_module_names) | {
    "aiohttp",
    "httpx",
    "requests",
}
_SUBPROCESS_FUNCTIONS = {
    "subprocess.run",
    "subprocess.call",
    "subprocess.check_call",
    "subprocess.check_output",
    "subprocess.getoutput",
    "subprocess.getstatusoutput",
    "subprocess.Popen",
}
_PURE_FUNCTIONS = {
    "abs",
    "all",
    "any",
    "bin",
    "bool",
    "bytearray",
    "bytes",
    "chr",
    "compile",
    "complex",
    "dict",
    "divmod",
    "enumerate",
    "float",
    "format",
    "frozenset",
    "globals",
    "hash",
    "hex",
    "id",
    "int",
    "isinstance",
    "issubclass",
    "iter",
    "len",
    "list",
    "locals",
    "memoryview",
    "next",
    "object",
    "oct",
    "ord",
    "pow",
    "range",
    "repr",
    "reversed",
    "round",
    "set",
    "slice",
    "str",
    "sum",
    "super",
    "tuple",
    "type",
    "vars",
    "zip",
}
_PURE_METHODS = {
    "add",
    "append",
    "capitalize",
    "casefold",
    "center",
    "clear",
    "copy",
    "count",
    "difference",
    "discard",
    "endswith",
    "expandtabs",
    "extend",
    "find",
    "format",
    "format_map",
    "get",
    "index",
    "insert",
    "intersection",
    "isalnum",
    "isalpha",
    "isascii",
    "isdecimal",
    "isdigit",
    "isidentifier",
    "islower",
    "isnumeric",
    "isprintable",
    "isspace",
    "istitle",
    "isupper",
    "items",
    "join",
    "keys",
    "ljust",
    "lower",
    "lstrip",
    "partition",
    "pop",
    "popitem",
    "remove",
    "removeprefix",
    "removesuffix",
    "replace",
    "reverse",
    "rfind",
    "rindex",
    "rjust",
    "rpartition",
    "rsplit",
    "rstrip",
    "setdefault",
    "sort",
    "split",
    "splitlines",
    "startswith",
    "strip",
    "swapcase",
    "symmetric_difference",
    "title",
    "translate",
    "union",
    "update",
    "upper",
    "values",
    "zfill",
}
_PURE_MODULE_PREFIXES = (
    "decimal.",
    "fractions.",
    "hashlib.",
    "math.",
    "os.path.",
    "random.",
    "statistics.",
)
_DEPENDENCY_COMMANDS = {"pip", "pip3", "npm", "yarn", "pnpm", "apt", "apt-get", "brew"}
_EXECUTION_ENV_KEYS = {
    "BASH_ENV",
    "DYLD_INSERT_LIBRARIES",
    "DYLD_LIBRARY_PATH",
    "ENV",
    "LD_LIBRARY_PATH",
    "LD_PRELOAD",
    "PATH",
    "PYTHONHOME",
    "PYTHONPATH",
}
_PROFILED_COMMANDS = {
    "bash",
    "busybox",
    "cat",
    "command",
    "cp",
    "curl",
    "dd",
    "echo",
    "env",
    "fallocate",
    "find",
    "grep",
    "head",
    "install",
    "ls",
    "mv",
    "nice",
    "nc",
    "netcat",
    "pwd",
    "python",
    "python3",
    "rm",
    "sh",
    "sleep",
    "sort",
    "tail",
    "tee",
    "telnet",
    "timeout",
    "toybox",
    "truncate",
    "uniq",
    "wc",
    "wget",
    "yes",
    "zsh",
}
_SHELL_SEPARATORS = {";", "&&", "||", "|", "&"}
_SHELL_BUILTINS = {
    "!",
    ":",
    "[",
    "]",
    "case",
    "do",
    "done",
    "elif",
    "else",
    "esac",
    "export",
    "false",
    "fi",
    "for",
    "function",
    "if",
    "in",
    "select",
    "test",
    "then",
    "time",
    "true",
    "until",
    "while",
    "{",
    "}",
}
_SENSITIVE_ARGV_SENTINEL = "__trpc_sensitive_argv__"


def _sanitize_evidence(value: str, limit: int = 120) -> str:
    """Redact common secrets and cap evidence length."""

    evidence = value.replace("\n", " ").replace("\r", " ").strip()
    if (_PRIVATE_KEY_RE.search(evidence) or _BEARER_RE.search(evidence) or _API_TOKEN_RE.search(evidence)
            or _SENSITIVE_HEADER_RE.search(evidence) or _SENSITIVE_OPTION_RE.search(evidence)
            or _SENSITIVE_ARGUMENT_RE.search(evidence) or _KEYED_SECRET_RE.search(evidence)
            or _ASSIGNMENT_RE.search(evidence) or _URL_USERINFO_RE.search(evidence)
            or ("=" in evidence and _SENSITIVE_NAME_RE.search(evidence))):
        return "[REDACTED_SENSITIVE_EVIDENCE]"
    evidence = _PRIVATE_KEY_RE.sub("[REDACTED_PRIVATE_KEY]", evidence)
    evidence = _ASSIGNMENT_RE.sub(lambda match: f'{match.group(1)}="[REDACTED]"', evidence)
    evidence = _KEYED_SECRET_RE.sub(lambda match: f"{match.group(1)}[REDACTED]", evidence)
    evidence = _SENSITIVE_MAPPING_RE.sub(lambda match: f'{match.group(1)}"[REDACTED]"', evidence)
    evidence = _BEARER_RE.sub("Bearer [REDACTED]", evidence)
    evidence = _API_TOKEN_RE.sub("[REDACTED]", evidence)
    evidence = _SENSITIVE_OPTION_RE.sub(lambda match: f"{match.group(1)}[REDACTED]", evidence)
    evidence = _SENSITIVE_HEADER_RE.sub(lambda match: f"{match.group(0).split(':', 1)[0]}: [REDACTED]", evidence)
    evidence = _SENSITIVE_ARGUMENT_RE.sub(lambda match: f"{match.group(1)}=[REDACTED]", evidence)
    evidence = _COMPACT_COOKIE_RE.sub(r"\1[REDACTED]", evidence)
    evidence = _URL_USERINFO_RE.sub(r"\1[REDACTED]@", evidence)
    evidence = _LONG_RANDOM_RE.sub("[REDACTED]", evidence)
    if len(evidence) > limit:
        return evidence[:limit - 3] + "..."
    return evidence


def _literal_string(node: ast.AST | None, constants: dict[str, str] | None = None) -> str | None:
    """Resolve a small, safe subset of statically-known strings."""

    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name) and constants is not None:
        return constants.get(node.id)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _literal_string(node.left, constants)
        right = _literal_string(node.right, constants)
        if left is not None and right is not None:
            return left + right
    return None


def _literal_int(node: ast.AST | None) -> int | None:
    """Resolve a statically-known integer without evaluating code."""

    if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
        return node.value
    return None


def _static_value_size(node: ast.AST | None) -> int | None:
    """Compute literal string/bytes size without allocating repeated data."""

    if isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)):
        return len(node.value.encode("utf-8")) if isinstance(node.value, str) else len(node.value)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _static_value_size(node.left)
        right = _static_value_size(node.right)
        if left is not None and right is not None:
            return left + right
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        left_size = _static_value_size(node.left)
        right_count = _literal_int(node.right)
        if left_size is not None and right_count is not None and right_count >= 0:
            return left_size * right_count
        right_size = _static_value_size(node.right)
        left_count = _literal_int(node.left)
        if right_size is not None and left_count is not None and left_count >= 0:
            return right_size * left_count
    return None


def _looks_like_secret_literal(value: str) -> bool:
    """Recognize high-confidence secret material in a sink argument."""

    normalized = value.strip()
    return bool(
        _PRIVATE_KEY_RE.search(normalized) or _BEARER_RE.search(normalized) or _API_TOKEN_RE.search(normalized)
        or _ASSIGNMENT_RE.search(normalized) or _KEYED_SECRET_RE.search(normalized)
        or _SENSITIVE_HEADER_RE.search(normalized))


def _call_name(node: ast.AST, aliases: dict[str, str]) -> str:
    """Return a dotted call name with import aliases resolved."""

    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        prefix = _call_name(node.value, aliases)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    if isinstance(node, ast.Call):
        return _call_name(node.func, aliases)
    return ""


def _keyword(call: ast.Call, name: str) -> ast.AST | None:
    for item in call.keywords:
        if item.arg == name:
            return item.value
    return None


def _command_parts(node: ast.AST | None, constants: dict[str, str] | None = None) -> list[str] | None:
    """Resolve a subprocess command expressed as a literal string or list."""

    if isinstance(node, (ast.List, ast.Tuple)):
        result: list[str] = []
        for item in node.elts:
            value = _literal_string(item, constants)
            if value is None:
                return None
            result.append(value)
        return result
    value = _literal_string(node, constants)
    if value is None:
        return None
    try:
        return shlex.split(value, posix=True)
    except ValueError:
        return None


class SafetyScanner:
    """Scan Python source or POSIX/Bash commands before execution."""

    def __init__(
            self,
            policy: SafetyPolicy | None = None,
            custom_rules: Iterable[SafetyRule] = (),
    ):
        self.policy = policy or SafetyPolicy()
        self.custom_rules = tuple(custom_rules)
        rule_ids = [rule.rule_id.strip() for rule in self.custom_rules]
        if any(not rule_id for rule_id in rule_ids):
            raise ValueError("custom safety rule ids cannot be blank")
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("custom safety rule ids must be unique")
        reserved_rule_ids = set(RULE_SPECS) | {"ALLOW-000"}
        collisions = sorted(set(rule_ids) & reserved_rule_ids)
        if collisions:
            raise ValueError("custom safety rule ids cannot replace built-in rule ids: " + ", ".join(collisions))

    @classmethod
    def from_yaml(
            cls,
            path: str | Path,
            custom_rules: Iterable[SafetyRule] = (),
    ) -> "SafetyScanner":
        """Create a scanner from a validated YAML policy."""

        return cls(SafetyPolicy.from_yaml(path), custom_rules)

    def scan(self, request: SafetyScanRequest) -> SafetyReport:
        """Scan one request and return a deterministic structured report."""

        started = time.perf_counter()
        digest_builder = hashlib.sha256()
        for value in (request.language.value, request.content):
            raw = value.encode("utf-8")
            digest_builder.update(len(raw).to_bytes(8, "big"))
            digest_builder.update(raw)
        digest = digest_builder.hexdigest()
        findings: list[SafetyFinding] = []

        content_bytes = len(request.content.encode("utf-8"))
        line_count = request.content.count("\n") + (1 if request.content else 0)
        if (content_bytes > self.policy.limits.max_script_size_bytes
                or line_count > self.policy.limits.max_script_lines):
            self._append(
                findings,
                "POLICY-002",
                f"script_size={content_bytes} bytes, lines={line_count}",
            )
            return self._report(
                findings,
                started,
                digest,
                analysis_status=AnalysisStatus.BUDGET_EXCEEDED,
            )

        if (request.timeout_seconds is not None and request.timeout_seconds > self.policy.limits.max_timeout_seconds):
            self._append(
                findings,
                "POLICY-001",
                f"timeout_seconds={request.timeout_seconds}",
            )

        try:
            if request.language == ScriptLanguage.PYTHON:
                analysis = self._scan_python(request)
            else:
                analysis = self._scan_bash(request)
            findings.extend(analysis.findings)
            for rule in self.custom_rules:
                for finding in rule.analyze(request, self.policy):
                    if finding.rule_id != rule.rule_id:
                        raise ValueError(f"custom safety rule {rule.rule_id!r} returned finding {finding.rule_id!r}", )
                    findings.append(
                        finding.model_copy(
                            update={
                                "evidence": _sanitize_evidence(finding.evidence),
                                "message": _sanitize_evidence(finding.message),
                                "recommendation": _sanitize_evidence(finding.recommendation),
                            }))
        except Exception as ex:  # pylint: disable=broad-except
            findings.append(self._analysis_failure(f"analyzer failed: {type(ex).__name__}"))
            return self._report(
                findings,
                started,
                digest,
                analysis_status=AnalysisStatus.INTERNAL_ERROR,
            )
        status = analysis.status
        if status == AnalysisStatus.COMPLETE and any(finding.rule_id == "PARSE-001" for finding in findings):
            status = AnalysisStatus.PARSE_ERROR
        elif status == AnalysisStatus.COMPLETE and any(finding.rule_id in NON_RELAXABLE_REVIEW_RULE_IDS
                                                       for finding in findings):
            status = AnalysisStatus.UNSUPPORTED
        return self._report(findings, started, digest, analysis_status=status)

    def _scan_python(self, request: SafetyScanRequest) -> AnalysisResult:
        try:
            tree = ast.parse(request.content)
        except (SyntaxError, ValueError, MemoryError, RecursionError) as ex:
            finding = self._analysis_failure(
                _sanitize_evidence(f"{type(ex).__name__}: {ex}"),
                line_number=getattr(ex, "lineno", None),
                column=getattr(ex, "offset", None),
            )
            return AnalysisResult(
                status=AnalysisStatus.PARSE_ERROR,
                findings=[finding],
            )

        aliases = self._analysis_aliases(tree)
        call_aliases = self._call_aliases(tree)
        constants = self._constant_strings(tree)
        constants = self._context_strings(tree, request, constants)
        parent = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        ambiguous_names = self._ambiguous_bindings(tree, parent)
        conditional_names = self._conditional_bindings(tree, parent)
        stable_constants = {name: value for name, value in constants.items() if name not in ambiguous_names}
        initial_sensitive_names = ({_SENSITIVE_ARGV_SENTINEL} if any(
            self._argument_contains_secret(value) for value in request.argv) else set())
        sensitive_names = self._sensitive_assignments(
            tree,
            aliases,
            initial_sensitive_names,
        )
        sensitive_network_clients = self._sensitive_network_clients(
            tree,
            aliases,
            sensitive_names,
        )
        functions = {
            node.name: node
            for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        file_tainted_names = self._file_tainted_assignments(
            tree,
            aliases,
            set(functions),
        )
        findings: list[SafetyFinding] = []
        unknowns: list[str] = []
        nested_statuses: list[AnalysisStatus] = []

        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Attribute):
                        owner = _call_name(target.value, aliases)
                        if (owner in _KNOWN_CLIENT_CONSTRUCTORS
                                and self._network_client_attribute_requires_review(target.attr, node.value)):
                            self._append_node(findings, "NET-002", target, request.content)
                    if not isinstance(target, ast.Subscript):
                        continue
                    owner = _call_name(target.value, aliases)
                    key = _literal_string(target.slice, stable_constants)
                    if owner == "os.environ" and key is not None and key.upper() in _EXECUTION_ENV_KEYS:
                        self._append_node(findings, "PROC-003", target, request.content)
            if isinstance(node, ast.Import):
                roots = {item.name.split(".", 1)[0] for item in node.names}
            elif isinstance(node, ast.ImportFrom):
                roots = {node.module.split(".", 1)[0]} if node.module else set()
                if node.level:
                    roots.add("")
            else:
                continue
            for root in roots:
                if root in _KNOWN_IMPORT_ROOTS:
                    continue
                unknowns.append(f"line={getattr(node, 'lineno', 0)}: unknown imported module")
                self._append_node(findings, "PROC-UNKNOWN-001", node, request.content)

        for node in ast.walk(tree):
            if isinstance(node, ast.While) and self._is_true(node.test):
                if any(
                        isinstance(child, ast.Call)
                        and _call_name(child.func, call_aliases.get(id(child), aliases)) == "os.fork"
                        for child in ast.walk(node)):
                    self._append_node(findings, "RES-001", node, request.content)
                else:
                    self._append_node(findings, "RES-002", node, request.content)
            if not isinstance(node, ast.Call):
                continue
            node_aliases = dict(call_aliases.get(id(node), aliases))
            called_name = self._root_name(node.func)
            if called_name in conditional_names:
                node_aliases.pop(called_name, None)
            call_name = _call_name(node.func, node_aliases)
            has_import_or_alias_provenance = called_name is None or called_name in node_aliases
            if called_name in conditional_names or self._argument_names(node) & ambiguous_names:
                self._append_node(findings, "PROC-003", node, request.content)
            if self._has_unknown_callable_lookup(node.func):
                unknowns.append(f"line={getattr(node, 'lineno', 0)}: reflective callable lookup")
            if call_name in {"__import__", "importlib.import_module"}:
                module_node = node.args[0] if node.args else None
                if _literal_string(module_node, stable_constants) is None:
                    unknowns.append(f"line={getattr(node, 'lineno', 0)}: dynamic import")
            if call_name not in functions:
                self._check_python_file(
                    findings,
                    node,
                    call_name,
                    request,
                    stable_constants,
                )
            if (call_name in _KNOWN_CLIENT_CONSTRUCTORS and self._network_client_constructor_requires_review(node)):
                self._append_node(findings, "NET-002", node, request.content)
            is_network = self._check_python_network(
                findings,
                node,
                call_name,
                request,
                stable_constants,
                has_import_or_alias_provenance,
            )
            self._check_python_process(
                findings,
                node,
                call_name,
                request,
                stable_constants,
                nested_statuses,
            )
            self._check_python_resource(findings, node, call_name, request.content, parent)
            function = functions.get(call_name)
            if function is not None:
                self._check_python_wrapper(
                    findings,
                    node,
                    function,
                    request,
                    stable_constants,
                    call_aliases,
                    aliases,
                    parent,
                    nested_statuses,
                    sensitive_names,
                    functions,
                )
            sink_values = [*node.args, *(keyword.value for keyword in node.keywords)]
            if self._is_secret_sink(call_name):
                for argument in sink_values:
                    is_local_call = (isinstance(argument, ast.Call)
                                     and _call_name(argument.func, node_aliases) in functions)
                    if (not is_local_call and self._contains_secret(
                            argument,
                            node_aliases,
                            sensitive_names,
                    ) or self._local_call_returns_secret(
                            argument,
                            functions,
                            node_aliases,
                            sensitive_names,
                    )):
                        self._append_node(
                            findings,
                            "SECRET-001",
                            node,
                            request.content,
                        )
                        break
            if is_network and any(
                    self._contains_file_taint(argument, node_aliases, file_tainted_names) for argument in sink_values):
                unknowns.append(f"line={getattr(node, 'lineno', 0)}: file content reaches network")
                self._append_node(findings, "PROC-UNKNOWN-001", node, request.content)
            if is_network and self._network_call_contains_secret(
                    node,
                    node_aliases,
                    sensitive_names,
                    sensitive_network_clients,
            ):
                self._append_node(findings, "SECRET-001", node, request.content)
            if not self._is_explained_python_call(node, call_name, functions, node_aliases):
                unknowns.append(f"line={getattr(node, 'lineno', 0)}: unknown callable")
                self._append_node(findings, "PROC-UNKNOWN-001", node, request.content)

        status = self._aggregate_analysis_status([
            AnalysisStatus.UNSUPPORTED if unknowns else AnalysisStatus.COMPLETE,
            *nested_statuses,
        ])
        return AnalysisResult(
            status=status,
            findings=findings,
            unknown_side_effects=unknowns,
        )

    def _check_python_wrapper(
        self,
        findings: list[SafetyFinding],
        invocation: ast.Call,
        function: ast.FunctionDef | ast.AsyncFunctionDef,
        request: SafetyScanRequest,
        constants: dict[str, str],
        call_aliases: dict[int, dict[str, str]],
        aliases: dict[str, str],
        parent: dict[ast.AST, ast.AST],
        nested_statuses: list[AnalysisStatus],
        sensitive_names: set[str],
        functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
        visited_functions: set[str] | None = None,
    ) -> None:
        """Propagate literal arguments through one local wrapper function."""

        visited = set(visited_functions or ())
        if function.name in visited:
            return
        visited.add(function.name)
        resolved = dict(constants)
        positional = list(function.args.posonlyargs) + list(function.args.args)
        sensitive_parameters = {
            parameter.arg
            for parameter in positional + list(function.args.kwonlyargs) if _SENSITIVE_NAME_RE.search(parameter.arg)
        }
        bound_parameters: set[str] = set()
        for parameter, argument in zip(positional, invocation.args):
            bound_parameters.add(parameter.arg)
            value = self._resolve_python_string(argument, constants, request)
            if value is not None:
                resolved[parameter.arg] = value
            if self._contains_secret(argument, aliases, sensitive_names):
                sensitive_parameters.add(parameter.arg)
        keyword_arguments = {keyword.arg: keyword.value for keyword in invocation.keywords if keyword.arg is not None}
        for parameter in positional + list(function.args.kwonlyargs):
            argument = keyword_arguments.get(parameter.arg)
            if argument is None:
                continue
            bound_parameters.add(parameter.arg)
            value = self._resolve_python_string(argument, constants, request)
            if value is not None:
                resolved[parameter.arg] = value
            if self._contains_secret(argument, aliases, sensitive_names):
                sensitive_parameters.add(parameter.arg)
        positional_defaults = zip(positional[-len(function.args.defaults):], function.args.defaults)
        for parameter, default in positional_defaults:
            if parameter.arg not in bound_parameters and self._contains_secret(default, aliases, sensitive_names):
                sensitive_parameters.add(parameter.arg)
        for parameter, default in zip(function.args.kwonlyargs, function.args.kw_defaults):
            if (parameter.arg not in bound_parameters and default is not None
                    and self._contains_secret(default, aliases, sensitive_names)):
                sensitive_parameters.add(parameter.arg)

        for child in ast.walk(function):
            if not isinstance(child, ast.Call):
                continue
            child_aliases = call_aliases.get(id(child), aliases)
            child_name = _call_name(child.func, child_aliases)
            self._check_python_file(findings, child, child_name, request, resolved)
            child_root = self._root_name(child.func)
            self._check_python_network(
                findings,
                child,
                child_name,
                request,
                resolved,
                child_root is None or child_root in child_aliases,
            )
            self._check_python_process(
                findings,
                child,
                child_name,
                request,
                resolved,
                nested_statuses,
            )
            self._check_python_resource(findings, child, child_name, request.content, parent)
            sink_values = [*child.args, *(keyword.value for keyword in child.keywords)]
            sensitive_scope = sensitive_names | sensitive_parameters
            leaks_secret = any(
                self._contains_secret(argument, child_aliases, sensitive_scope) or self._local_call_returns_secret(
                    argument,
                    functions,
                    child_aliases,
                    sensitive_scope,
                ) for argument in sink_values)
            if self._is_secret_sink(child_name) and leaks_secret:
                self._append_node(findings, "SECRET-001", child, request.content)
            nested_function = functions.get(child_name)
            if nested_function is not None:
                if child_name in visited:
                    self._append_node(
                        findings,
                        "RES-002",
                        child,
                        request.content,
                    )
                    continue
                self._check_python_wrapper(
                    findings,
                    child,
                    nested_function,
                    request,
                    resolved,
                    call_aliases,
                    aliases,
                    parent,
                    nested_statuses,
                    sensitive_names | sensitive_parameters,
                    functions,
                    visited,
                )

    def _scan_bash(self, request: SafetyScanRequest) -> AnalysisResult:
        findings: list[SafetyFinding] = []
        try:
            analysis = analyze_bash(request.content)
        except Exception as ex:  # pylint: disable=broad-except
            findings.append(self._analysis_failure(f"bash parser failed: {type(ex).__name__}"))
            return AnalysisResult(
                status=AnalysisStatus.INTERNAL_ERROR,
                findings=findings,
            )

        if analysis.has_parse_error or analysis.has_heredoc:
            findings.append(self._analysis_failure("bash syntax requires manual review"))
        if analysis.has_command_substitution or analysis.has_process_substitution:
            self._append(findings, "PROC-001", "nested command execution")
        if analysis.has_background_job:
            self._append(findings, "PROC-004", "background process")
        if analysis.has_fork_bomb:
            self._append(findings, "RES-001", "recursive process fan-out")
        if analysis.has_unbounded_loop:
            self._append(findings, "RES-002", "unbounded shell loop")

        sensitive_env_names = {key for key in request.env if _SENSITIVE_NAME_RE.search(key)}
        sensitive_argv = {
            str(index)
            for index, value in enumerate(request.argv, start=1) if self._argument_contains_secret(value)
        }
        positional_zero = request.metadata.get("bash_positional_zero")
        if positional_zero is not None and self._argument_contains_secret(positional_zero):
            sensitive_env_names.add("0")
        sensitive_env_names.update(sensitive_argv)
        if sensitive_argv:
            sensitive_env_names.update({"*", "@"})
        if any(
                self._bash_secret_flow(
                    list(command.tokens),
                    set(command.expanded_variables),
                    sensitive_env_names,
                ) for command in analysis.commands):
            self._append(findings, "SECRET-001", "sensitive value reaches an output sink")

        resolved_env = dict(request.env)
        resolved_env.update(analysis.assignments)
        if positional_zero is not None:
            resolved_env["0"] = positional_zero
        resolved_env.update({str(index): value for index, value in enumerate(request.argv, start=1)})
        resolved_request = request.model_copy(update={"env": resolved_env})
        nested_statuses: list[AnalysisStatus] = []
        for index, command in enumerate(analysis.commands):
            tokens = list(command.tokens)
            if len(tokens) == 1 and re.fullmatch(r"\d+", tokens[0]):
                continue
            if (index == 0 and request.argv and request.metadata.get("bash_positional_arguments") != "true"):
                tokens.extend(request.argv)
            if command.has_indirect_expansion:
                self._append(
                    findings,
                    "PROC-003",
                    "indirect shell parameter expansion",
                    line_number=command.line_number,
                    column=command.column,
                )
            self._check_bash_segment(
                findings,
                tokens,
                resolved_request,
                nested_statuses,
            )
        for redirect in analysis.redirects:
            target = redirect.target
            if target is None or self._is_dynamic_shell_value(target):
                self._append(
                    findings,
                    "PROC-003",
                    "dynamic redirection target",
                    line_number=redirect.line_number,
                    column=redirect.column,
                )
            elif redirect.operator.startswith("<") and redirect.operator != "<>":
                if self._is_sensitive_path(target, request.cwd):
                    self._append(
                        findings,
                        "FILE-003",
                        f"{redirect.operator} {target}",
                        line_number=redirect.line_number,
                        column=redirect.column,
                    )
            elif self._is_sensitive_path(target, request.cwd) or self._is_protected_write_path(target, request.cwd):
                self._append(
                    findings,
                    "FILE-004",
                    f"{redirect.operator} {target}",
                    line_number=redirect.line_number,
                    column=redirect.column,
                )
        output_redirect_lines = {
            redirect.line_number
            for redirect in analysis.redirects if redirect.operator.startswith(">")
        }
        for command in analysis.commands:
            tokens = list(command.tokens)
            executable = Path(tokens[0]).name if tokens else ""
            if (command.line_number in output_redirect_lines
                    and (executable == "yes" or executable == "cat" and "/dev/zero" in tokens[1:])):
                self._append(
                    findings,
                    "RES-003",
                    "unbounded producer writes to a file",
                    line_number=command.line_number,
                    column=command.column,
                )
        status = self._aggregate_analysis_status([
            AnalysisStatus.PARSE_ERROR if analysis.has_parse_error or analysis.has_heredoc else AnalysisStatus.COMPLETE,
            *nested_statuses,
        ])
        return AnalysisResult(status=status, findings=findings)

    def _check_python_file(
        self,
        findings: list[SafetyFinding],
        node: ast.Call,
        call_name: str,
        request: SafetyScanRequest,
        constants: dict[str, str],
    ) -> None:
        delete_calls = {
            "os.remove",
            "os.unlink",
            "shutil.rmtree",
            "pathlib.Path.unlink",
            "pathlib.Path.rmdir",
        }
        read_calls = {
            "pathlib.Path.read_text",
            "pathlib.Path.read_bytes",
        }
        write_calls = {
            "pathlib.Path.write_text",
            "pathlib.Path.write_bytes",
        }
        transfer_calls = {
            "os.rename",
            "os.replace",
            "shutil.copy",
            "shutil.copy2",
            "shutil.copyfile",
            "shutil.copytree",
            "shutil.move",
        }
        path_method = call_name.startswith("pathlib.Path.")
        path_open = call_name == "pathlib.Path.open"
        path_read = call_name in {"pathlib.Path.read_text", "pathlib.Path.read_bytes"}
        path_write = call_name in {"pathlib.Path.write_text", "pathlib.Path.write_bytes"}
        path_delete = call_name in {"pathlib.Path.unlink", "pathlib.Path.rmdir"}
        path_transfer = call_name in {"pathlib.Path.rename", "pathlib.Path.replace"}

        path_node: ast.AST | None = node.args[0] if node.args else None
        if call_name in read_calls | write_calls or path_method:
            if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Call):
                path_node = node.func.value.args[0] if node.func.value.args else None
            elif path_method and isinstance(node.func, ast.Attribute):
                path_node = node.func.value
        path = self._resolve_python_string(path_node, constants, request)

        if call_name in delete_calls or path_delete:
            if path is None:
                self._append_node(findings, "PROC-003", node, request.content)
            elif self._is_protected_delete(path, request.cwd):
                self._append_node(findings, "FILE-001", node, request.content)
            else:
                self._append_node(findings, "FILE-002", node, request.content)

        if call_name in {"open", "io.open"} or path_open:
            mode_node = node.args[1] if len(node.args) > 1 else _keyword(node, "mode")
            mode = "r" if mode_node is None else _literal_string(mode_node)
            if path is None:
                self._append_node(findings, "PROC-003", node, request.content)
            elif mode is None:
                if self._is_sensitive_path(path, request.cwd) or self._is_protected_write_path(path, request.cwd):
                    self._append_node(findings, "FILE-004", node, request.content)
                else:
                    self._append_node(findings, "PROC-003", node, request.content)
            elif (any(flag in mode for flag in "wax+")
                  and (self._is_sensitive_path(path, request.cwd) or self._is_protected_write_path(path, request.cwd))):
                self._append_node(findings, "FILE-004", node, request.content)
            elif self._is_sensitive_path(path, request.cwd):
                rule_id = "FILE-004" if any(flag in mode for flag in "wax+") else "FILE-003"
                self._append_node(findings, rule_id, node, request.content)

        if call_name in read_calls or path_read:
            if path is None:
                self._append_node(findings, "PROC-003", node, request.content)
            elif self._is_sensitive_path(path, request.cwd):
                self._append_node(findings, "FILE-003", node, request.content)
        if call_name in write_calls or path_write:
            if path is None:
                self._append_node(findings, "PROC-003", node, request.content)
            elif self._is_sensitive_path(path, request.cwd) or self._is_protected_write_path(path, request.cwd):
                self._append_node(findings, "FILE-004", node, request.content)

        if call_name in transfer_calls or path_transfer:
            source_node: ast.AST | None = node.args[0] if node.args else None
            destination_node: ast.AST | None = node.args[1] if len(node.args) > 1 else None
            if path_transfer and isinstance(node.func, ast.Attribute):
                destination_node = node.args[0] if node.args else None
                if isinstance(node.func.value, ast.Call) and node.func.value.args:
                    source_node = node.func.value.args[0]
            source_path = self._resolve_python_string(source_node, constants, request)
            destination_path = self._resolve_python_string(destination_node, constants, request)
            if source_path is None or destination_path is None:
                self._append_node(findings, "PROC-003", node, request.content)
            else:
                if self._is_sensitive_path(source_path, request.cwd):
                    self._append_node(findings, "FILE-003", node, request.content)
                if self._is_sensitive_path(destination_path, request.cwd) or self._is_protected_write_path(
                        destination_path, request.cwd):
                    self._append_node(findings, "FILE-004", node, request.content)

    def _check_python_network(
        self,
        findings: list[SafetyFinding],
        node: ast.Call,
        call_name: str,
        request: SafetyScanRequest,
        constants: dict[str, str],
        has_import_or_alias_provenance: bool,
    ) -> bool:
        network_methods = {
            "delete",
            "get",
            "head",
            "options",
            "patch",
            "post",
            "put",
            "request",
        }
        method = call_name.rsplit(".", 1)[-1]
        is_network = has_import_or_alias_provenance and (call_name in _NETWORK_FUNCTIONS or call_name.startswith(
            ("aiohttp.", "httpx.", "requests.")) and method in network_methods)
        if not is_network and isinstance(node.func, ast.Attribute) and node.func.attr in network_methods:
            known_client = (has_import_or_alias_provenance and call_name.startswith(
                ("aiohttp.", "httpx.", "requests.")))
            is_network = known_client
        is_socket_connect = (has_import_or_alias_provenance and call_name.endswith(".connect") and bool(node.args)
                             and isinstance(node.args[0], (ast.Tuple, ast.List)))
        if (has_import_or_alias_provenance
                and call_name in {"socket.socket", "socket.create_connection"}) or is_socket_connect:
            is_network = True
        if not is_network:
            return False
        if call_name == "socket.socket":
            self._append_node(findings, "NET-002", node, request.content)
            return True

        is_http_client = call_name.startswith(("aiohttp.", "httpx.", "requests."))
        if is_http_client:
            safe_keywords = {
                "allow_redirects",
                "data",
                "follow_redirects",
                "json",
                "method",
                "params",
                "timeout",
                "url",
            }
            if any(keyword.arg is None or keyword.arg not in safe_keywords for keyword in node.keywords):
                self._append_node(findings, "NET-002", node, request.content)
            positional_limit = 2 if call_name.endswith(".request") else 1
            if len(node.args) > positional_limit:
                self._append_node(findings, "NET-002", node, request.content)

        url_node = node.args[0] if node.args else _keyword(node, "url")
        if call_name.endswith(".request"):
            url_node = node.args[1] if len(node.args) > 1 else _keyword(node, "url")
        if (call_name == "socket.create_connection" or is_socket_connect) and node.args:
            target = node.args[0]
            if isinstance(target, (ast.Tuple, ast.List)) and target.elts:
                url_node = target.elts[0]
        target = self._resolve_python_string(url_node, constants, request)
        if target is None:
            self._append_node(findings, "NET-002", node, request.content)
            return True
        host = self._hostname(target)
        if host is None:
            self._append_node(findings, "NET-002", node, request.content)
        elif not self._domain_allowed(host):
            self._append_node(findings, "NET-001", node, request.content)
        parsed_target = urlparse(target if "://" in target else f"//{target}")
        if parsed_target.username is not None or parsed_target.password is not None:
            self._append_node(findings, "SECRET-001", node, request.content)
        redirect_keyword = ("follow_redirects" if call_name.startswith("httpx.") else "allow_redirects")
        redirect_node = _keyword(node, redirect_keyword)
        if call_name.startswith(("requests.", "aiohttp.")):
            if not (isinstance(redirect_node, ast.Constant) and redirect_node.value is False):
                self._append_node(findings, "NET-002", node, request.content)
        elif redirect_node is not None and not (isinstance(redirect_node, ast.Constant)
                                                and redirect_node.value is False):
            self._append_node(findings, "NET-002", node, request.content)
        for keyword in ("proxies", "proxy"):
            proxy_node = _keyword(node, keyword)
            if proxy_node is not None and not (isinstance(proxy_node, ast.Constant) and proxy_node.value is None):
                self._append_node(findings, "NET-002", node, request.content)
        if any(key.upper() in {
                "ALL_PROXY",
                "HTTP_PROXY",
                "HTTPS_PROXY",
                "NO_PROXY",
        } for key in request.env):
            self._append_node(findings, "NET-002", node, request.content)
        return True

    def _check_python_process(
        self,
        findings: list[SafetyFinding],
        node: ast.Call,
        call_name: str,
        request: SafetyScanRequest,
        constants: dict[str, str],
        nested_statuses: list[AnalysisStatus],
    ) -> None:
        if (call_name in {
                "eval",
                "exec",
                "os.system",
                "os.popen",
        } or call_name.startswith(("os.exec", "os.spawn"))):
            self._append_node(findings, "PROC-001", node, request.content)
            return
        if call_name in {"__import__", "importlib.import_module"}:
            module = _literal_string(node.args[0], constants) if node.args else None
            if module is None:
                self._append_node(findings, "PROC-UNKNOWN-001", node, request.content)
            return
        if call_name.startswith(("__import__.", "importlib.import_module.")) or self._uses_dynamic_callable(node.func):
            self._append_node(findings, "PROC-001", node, request.content)
            return
        dynamic_name = self._dynamic_callable_name(node.func)
        if dynamic_name in {
                "eval",
                "exec",
                "fork",
                "popen",
                "remove",
                "replace",
                "rmtree",
                "run",
                "spawn",
                "system",
                "unlink",
        }:
            self._append_node(findings, "PROC-001", node, request.content)
            return
        if self._has_unknown_callable_lookup(node.func):
            self._append_node(findings, "PROC-UNKNOWN-001", node, request.content)
            return
        if call_name not in _SUBPROCESS_FUNCTIONS:
            return
        supported_keywords = {
            "args",
            "capture_output",
            "check",
            "cwd",
            "encoding",
            "env",
            "errors",
            "executable",
            "input",
            "shell",
            "text",
            "timeout",
        }
        if any(keyword.arg is None or keyword.arg not in supported_keywords for keyword in node.keywords):
            self._append_node(findings, "PROC-003", node, request.content)
        timeout_node = _keyword(node, "timeout")
        if timeout_node is not None:
            timeout_value = (timeout_node.value if isinstance(timeout_node, ast.Constant)
                             and isinstance(timeout_node.value,
                                            (int, float)) and not isinstance(timeout_node.value, bool) else None)
            if timeout_value is None:
                self._append_node(findings, "PROC-003", node, request.content)
            elif timeout_value > self.policy.limits.max_timeout_seconds:
                self._append_node(findings, "POLICY-001", node, request.content)
        shell_node = _keyword(node, "shell")
        if isinstance(shell_node, ast.Constant) and shell_node.value is True:
            self._append_node(findings, "PROC-001", node, request.content)
        elif shell_node is not None and not (isinstance(shell_node, ast.Constant) and shell_node.value is False):
            self._append_node(findings, "PROC-003", node, request.content)
        command_node = node.args[0] if node.args else _keyword(node, "args")
        parts = _command_parts(command_node, constants)
        if parts is None or not parts:
            self._append_node(findings, "PROC-003", node, request.content)
        else:
            raw_command = parts[0]
            command_allowed = self._command_allowed(raw_command)
            if "/" in raw_command and not command_allowed:
                self._append_node(findings, "PROC-002", node, request.content)
            if self._environment_affects_execution(request.env, raw_command):
                self._append(findings, "PROC-003", "execution environment changes command resolution")
            process_env = _keyword(node, "env")
            if process_env is not None and self._python_env_requires_review(process_env, constants):
                self._append(findings, "PROC-003", "subprocess environment cannot be verified")
            process_cwd = _keyword(node, "cwd")
            nested_request = request
            if process_cwd is not None:
                resolved_cwd = self._resolve_python_string(
                    process_cwd,
                    constants,
                    request,
                )
                if resolved_cwd is None:
                    self._append(
                        findings,
                        "PROC-003",
                        "subprocess cwd cannot be resolved",
                    )
                else:
                    nested_request = request.model_copy(update={"cwd": resolved_cwd})
            executable = _keyword(node, "executable")
            if executable is not None:
                resolved_executable = self._resolve_python_string(executable, constants, request)
                if resolved_executable is None:
                    self._append(findings, "PROC-003", "subprocess executable cannot be resolved")
                elif not self._command_allowed(resolved_executable):
                    self._append(findings, "PROC-002", "subprocess executable is not allowlisted")
            if self._is_dependency_install(parts):
                self._append_node(findings, "DEP-001", node, request.content)
            elif not command_allowed:
                self._append_node(findings, "PROC-002", node, request.content)
            self._check_bash_segment(
                findings,
                parts,
                nested_request,
                nested_statuses,
            )
        if call_name == "subprocess.Popen":
            self._append_node(findings, "PROC-004", node, request.content)

    @staticmethod
    def _uses_dynamic_callable(node: ast.AST) -> bool:
        dangerous_attributes = {
            "exec",
            "fork",
            "popen",
            "remove",
            "replace",
            "rmtree",
            "run",
            "spawn",
            "system",
            "unlink",
        }
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            name = _call_name(child.func, {})
            if name == "__import__" or name.endswith(".import_module"):
                return True
            if name == "getattr" and len(child.args) >= 2:
                attribute = _literal_string(child.args[1])
                if attribute in dangerous_attributes:
                    return True
        return False

    @staticmethod
    def _dynamic_callable_name(node: ast.AST) -> str | None:
        """Return a literal callable name obtained through a mapping lookup."""

        if not isinstance(node, ast.Subscript):
            return None
        return _literal_string(node.slice)

    @staticmethod
    def _has_unknown_callable_lookup(node: ast.AST) -> bool:
        """Identify reflective callable lookup that cannot be proven safe."""

        if isinstance(node, ast.Call):
            return True
        if isinstance(node, ast.Subscript):
            return True
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            name = _call_name(child.func, {})
            if name in {"globals", "locals", "vars"}:
                return True
            if name == "getattr":
                attribute = _literal_string(child.args[1]) if len(child.args) >= 2 else None
                if attribute is None:
                    return True
        return False

    @classmethod
    def _is_explained_python_call(
        cls,
        node: ast.Call,
        call_name: str,
        functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
        aliases: dict[str, str],
    ) -> bool:
        """Return whether a call belongs to a modeled or proven-pure capability."""

        root_name = cls._root_name(node.func)
        bound_capability = root_name is not None and root_name in aliases
        if cls._has_unknown_callable_lookup(node.func):
            return True
        if call_name in functions or call_name in _PURE_FUNCTIONS:
            return True
        if call_name in _KNOWN_CLIENT_CONSTRUCTORS and bound_capability:
            return True
        if call_name in {"max", "min", "sorted"} and _keyword(node, "key") is None:
            return True
        if call_name in {"eval", "exec", "__import__", "importlib.import_module"}:
            return True
        if (bound_capability and call_name in _SUBPROCESS_FUNCTIONS) or call_name in {
                "os.system",
                "os.popen",
                "os.fork",
        } or call_name.startswith(("os.exec", "os.spawn")):
            return True
        if call_name == "open" or bound_capability and call_name in {
                "io.open",
                "pathlib.Path",
                "pathlib.Path.open",
                "pathlib.Path.read_text",
                "pathlib.Path.read_bytes",
                "pathlib.Path.write_text",
                "pathlib.Path.write_bytes",
                "pathlib.Path.unlink",
                "pathlib.Path.rmdir",
                "os.remove",
                "os.unlink",
                "os.rename",
                "os.replace",
                "shutil.copy",
                "shutil.copy2",
                "shutil.copyfile",
                "shutil.copytree",
                "shutil.move",
                "shutil.rmtree",
                "pathlib.Path.rename",
                "pathlib.Path.replace",
        }:
            return True
        if cls._is_explained_network_call(node, call_name, bound_capability):
            return True
        if bound_capability and (call_name in {
                "asyncio.gather",
                "asyncio.sleep",
                "asyncio.wait",
                "concurrent.futures.ProcessPoolExecutor",
                "concurrent.futures.ThreadPoolExecutor",
                "time.sleep",
        } or call_name.endswith(("ProcessPoolExecutor", "ThreadPoolExecutor"))):
            return True
        if call_name == "print" or bound_capability and (call_name.startswith("logging.") or call_name in {
                "sys.stderr.write",
                "sys.stdout.write",
        }):
            return True
        if bound_capability and call_name in {"os.environ.get", "os.getenv"}:
            return True
        if call_name == "getattr" and len(node.args) >= 2 and _literal_string(node.args[1]) is not None:
            return True
        if isinstance(node.func, ast.Attribute) and node.func.attr in {
                "close",
                "flush",
                "read",
                "readable",
                "readline",
                "readlines",
                "seek",
                "seekable",
                "tell",
                "truncate",
                "writable",
                "write",
                "writelines",
        }:
            owner = node.func.value
            if isinstance(owner, ast.Call) and _call_name(owner.func, {}) in {"open", "io.open"}:
                return True
        if bound_capability and (cls._is_explained_json_call(node, call_name)
                                 or cls._is_explained_regex_call(node, call_name)):
            return True
        if bound_capability and call_name.startswith(_PURE_MODULE_PREFIXES):
            return True
        if isinstance(node.func, ast.Attribute) and node.func.attr in _PURE_METHODS:
            owner = node.func.value
            if isinstance(owner, (ast.Constant, ast.Dict, ast.List, ast.Set, ast.Tuple)):
                return True
            if isinstance(owner, ast.Call) and _call_name(owner.func, {}) in {
                    "bytearray",
                    "bytes",
                    "dict",
                    "frozenset",
                    "list",
                    "set",
                    "str",
                    "tuple",
            }:
                return True
        return False

    @staticmethod
    def _is_explained_json_call(node: ast.Call, call_name: str) -> bool:
        if call_name not in {"json.dump", "json.dumps", "json.load", "json.loads"}:
            return False
        callback_keywords = {
            "cls",
            "default",
            "object_hook",
            "object_pairs_hook",
            "parse_constant",
            "parse_float",
            "parse_int",
        }
        return all(keyword.arg not in callback_keywords
                   or isinstance(keyword.value, ast.Constant) and keyword.value.value is None
                   for keyword in node.keywords)

    @staticmethod
    def _is_explained_regex_call(node: ast.Call, call_name: str) -> bool:
        if call_name in {
                "re.compile",
                "re.escape",
                "re.findall",
                "re.finditer",
                "re.fullmatch",
                "re.match",
                "re.search",
                "re.split",
        }:
            return True
        if call_name not in {"re.sub", "re.subn"} or len(node.args) < 2:
            return False
        replacement = node.args[1]
        return isinstance(replacement, ast.Constant) and isinstance(replacement.value, (str, bytes))

    @staticmethod
    def _is_explained_network_call(
        node: ast.Call,
        call_name: str,
        bound_capability: bool,
    ) -> bool:
        """Recognize the same bounded network capability family as the checker."""

        methods = {
            "delete",
            "get",
            "head",
            "options",
            "patch",
            "post",
            "put",
            "request",
        }
        method = call_name.rsplit(".", 1)[-1]
        if call_name in _NETWORK_FUNCTIONS and bound_capability:
            return True
        if (bound_capability and call_name.startswith(("aiohttp.", "httpx.", "requests.")) and method in methods):
            return True
        if call_name in {"socket.socket", "socket.create_connection"} and bound_capability:
            return True
        if call_name == "socket.socket.connect" and bound_capability:
            return True
        return False

    def _check_python_resource(
        self,
        findings: list[SafetyFinding],
        node: ast.Call,
        call_name: str,
        source: str,
        parent: dict[ast.AST, ast.AST],
    ) -> None:
        if call_name in {"time.sleep", "asyncio.sleep"} and node.args:
            value = node.args[0]
            seconds = value.value if isinstance(value, ast.Constant) and isinstance(value.value, (int, float)) else None
            if seconds is None or seconds > self.policy.limits.max_sleep_seconds:
                self._append_node(findings, "RES-002", node, source)

        if call_name.endswith(("ThreadPoolExecutor", "ProcessPoolExecutor")):
            workers_node = _keyword(node, "max_workers")
            if workers_node is None and node.args:
                workers_node = node.args[0]
            workers = _literal_int(workers_node)
            if workers is None or workers > self.policy.limits.max_concurrency:
                self._append_node(findings, "RES-002", node, source)

        if call_name in {"asyncio.gather", "asyncio.wait"}:
            concurrency = 0
            unknown = False
            for argument in node.args:
                if isinstance(argument, ast.Starred):
                    bound = self._static_iterable_bound(argument.value)
                    if bound is None:
                        unknown = True
                    else:
                        concurrency += bound
                else:
                    concurrency += 1
            if unknown or concurrency > self.policy.limits.max_concurrency:
                self._append_node(findings, "RES-002", node, source)

        if (call_name == "iter" and len(node.args) >= 2 and isinstance(node.args[0],
                                                                       (ast.Lambda, ast.Name, ast.Attribute))):
            self._append_node(findings, "RES-002", node, source)

        write_value: ast.AST | None = None
        if isinstance(node.func, ast.Attribute) and node.func.attr in {"write", "write_text", "write_bytes"
                                                                       } and node.args:
            write_value = node.args[0]
        size = _static_value_size(write_value)
        if size is not None and size > self.policy.limits.max_static_write_size_bytes:
            self._append_node(findings, "RES-003", node, source)
        elif write_value is not None and size is None:
            self._append_node(findings, "PROC-UNKNOWN-001", node, source)

        if call_name == "os.fork":
            self._append_node(findings, "PROC-004", node, source)
            current: ast.AST | None = node
            while current is not None:
                if isinstance(current, ast.While) and self._is_true(current.test):
                    self._append_node(findings, "RES-001", node, source)
                    break
                current = parent.get(current)

    def _check_bash_segment(
        self,
        findings: list[SafetyFinding],
        segment: list[str],
        request: SafetyScanRequest,
        nested_statuses: list[AnalysisStatus] | None = None,
    ) -> None:
        original_segment = list(segment)
        leading_assignments: list[str] = []
        while segment and self._is_shell_assignment(segment[0]):
            leading_assignments.append(segment[0])
            segment = segment[1:]
        if self._assignment_keys_affect_execution(leading_assignments):
            self._append(findings, "PROC-003", "shell environment changes command resolution")
        if segment and segment[0] in {"case", "for", "select"}:
            return
        if (len(segment) >= 2 and segment[0] in {"until", "while"}
                and (segment[1] in {":", "true", "false"} or re.fullmatch(r"\d+", segment[1]))):
            return
        while segment and segment[0] in {
                "do",
                "elif",
                "else",
                "if",
                "then",
                "until",
                "while",
        }:
            segment = segment[1:]
        if not segment:
            return
        evidence = " ".join(segment)
        raw_command = segment[0]
        resolved_command = self._resolve_shell_value(raw_command, request.env)
        if self._is_dynamic_shell_value(resolved_command):
            self._append(findings, "PROC-003", evidence)
            return
        command = Path(resolved_command).name
        command_allowed = self._command_allowed(resolved_command)
        if "/" in resolved_command and not command_allowed:
            self._append(findings, "PROC-002", evidence)
        if self._environment_affects_execution(request.env, resolved_command):
            self._append(findings, "PROC-003", "execution environment changes command resolution")
        if command == "env" and self._assignment_keys_affect_execution(original_segment[1:]):
            self._append(findings, "PROC-003", "env changes command resolution")

        wrapped = self._wrapped_command(segment)
        if wrapped:
            self._check_bash_segment(findings, wrapped, request, nested_statuses)
        if command in {"busybox", "toybox"}:
            if len(segment) > 1:
                self._check_bash_segment(findings, segment[1:], request, nested_statuses)
            else:
                self._append(findings, "PROC-003", evidence)
        if command in {".", "eval", "source"}:
            self._append(findings, "PROC-001", evidence)
        if command in {"sudo", "su", "pkexec", "doas"}:
            self._append(findings, "PROC-001", evidence)
            nested = self._privileged_command(segment)
            if nested:
                self._check_bash_segment(findings, nested, request, nested_statuses)
        if command == "rm":
            targets = [
                self._resolve_shell_value(token, request.env) for token in segment[1:] if not token.startswith("-")
            ]
            if any(self._is_dynamic_shell_value(target) for target in targets):
                self._append(findings, "PROC-003", evidence)
            recursive = any(token.startswith("-") and ("r" in token or "R" in token) for token in segment[1:])
            for target in targets:
                if self._is_protected_delete(target, request.cwd):
                    self._append(findings, "FILE-001", evidence)
                elif recursive:
                    self._append(findings, "FILE-002", evidence)
        if command == "find":
            delete_action = "-delete" in segment
            find_roots = [
                self._resolve_shell_value(token, request.env) for token in segment[1:]
                if not token.startswith("-") and token not in {";", "\\;", "+", "{}"}
            ]
            for action in {"-exec", "-execdir", "-ok", "-okdir"}:
                if action not in segment:
                    continue
                start = segment.index(action) + 1
                nested = []
                for token in segment[start:]:
                    if token in {";", "\\;", "+"}:
                        break
                    nested.append(token)
                if nested:
                    self._check_bash_segment(findings, nested, request, nested_statuses)
                    nested_command = Path(nested[0]).name
                    if (nested_command == "rm"
                            and any(token.startswith("-") and ("r" in token or "R" in token) for token in nested[1:])
                            and any(self._is_protected_delete(root, request.cwd) for root in find_roots)):
                        self._append(findings, "FILE-001", evidence)
                else:
                    self._append(findings, "PROC-003", evidence)
            if delete_action:
                roots = find_roots
                if not roots or any(self._is_dynamic_shell_value(root) for root in roots):
                    self._append(findings, "PROC-003", evidence)
                for root in roots:
                    if self._is_protected_delete(root, request.cwd):
                        self._append(findings, "FILE-001", evidence)
                    else:
                        self._append(findings, "FILE-002", evidence)
        if command in {
                "cat",
                "cp",
                "find",
                "grep",
                "head",
                "ls",
                "sort",
                "tail",
                "uniq",
                "wc",
        }:
            resolved_paths = [
                self._resolve_shell_value(token, request.env) for token in segment[1:] if not token.startswith("-")
            ]
            if any(self._is_dynamic_shell_value(path) for path in resolved_paths):
                self._append(findings, "PROC-003", evidence)
            if any(self._is_sensitive_path(path, request.cwd) for path in resolved_paths):
                self._append(findings, "FILE-003", evidence)
        if command == "sort":
            output_paths = self._option_values(segment, {"-o", "--output"})
            for output_path in output_paths:
                resolved_path = self._resolve_shell_value(
                    output_path,
                    request.env,
                )
                if self._is_dynamic_shell_value(resolved_path):
                    self._append(findings, "PROC-003", evidence)
                elif (self._is_sensitive_path(resolved_path, request.cwd) or self._is_protected_write_path(
                        resolved_path,
                        request.cwd,
                )):
                    self._append(findings, "FILE-004", evidence)
        if command in {"cp", "install", "mv"}:
            operands = [
                self._resolve_shell_value(token, request.env) for token in segment[1:] if not token.startswith("-")
            ]
            if any(self._is_dynamic_shell_value(operand) for operand in operands):
                self._append(findings, "PROC-003", evidence)
            if operands:
                if any(self._is_sensitive_path(source, request.cwd) for source in operands[:-1]):
                    self._append(findings, "FILE-003", evidence)
                destination = operands[-1]
                if (self._is_sensitive_path(destination, request.cwd)
                        or self._is_protected_write_path(destination, request.cwd)):
                    self._append(findings, "FILE-004", evidence)
        if command in {"tee", "truncate", "fallocate"}:
            targets = [
                self._resolve_shell_value(token, request.env) for token in segment[1:] if not token.startswith("-")
            ]
            if any(self._is_dynamic_shell_value(target) for target in targets):
                self._append(findings, "PROC-003", evidence)
            if any(
                    self._is_sensitive_path(token, request.cwd) or self._is_protected_write_path(token, request.cwd)
                    for token in targets):
                self._append(findings, "FILE-004", evidence)
        if command == "dd":
            targets = [
                self._resolve_shell_value(token.split("=", 1)[1], request.env) for token in segment[1:]
                if token.startswith("of=")
            ]
            if any(self._is_dynamic_shell_value(target) for target in targets):
                self._append(findings, "PROC-003", evidence)
            if any(
                    self._is_sensitive_path(token, request.cwd) or self._is_protected_write_path(token, request.cwd)
                    for token in targets):
                self._append(findings, "FILE-004", evidence)
            if (any(token == "if=/dev/zero" for token in segment[1:])
                    and not any(token.startswith(("count=", "iflag=count_bytes")) for token in segment[1:])):
                self._append(findings, "RES-003", evidence)
        if command == "head":
            count: int | None = None
            for index, token in enumerate(segment[1:], start=1):
                value: str | None = None
                if token in {"-c", "--bytes"} and index + 1 < len(segment):
                    value = segment[index + 1]
                elif token.startswith("--bytes="):
                    value = token.split("=", 1)[1]
                elif token.startswith("-c") and len(token) > 2:
                    value = token[2:]
                if value is not None:
                    count = self._parse_size(value)
                    break
            if ("/dev/zero" in segment[1:]
                    and (count is None or count > self.policy.limits.max_static_write_size_bytes)):
                self._append(findings, "RES-003", evidence)

        if command in {"curl", "wget"}:
            if self._unknown_network_options(segment):
                self._append(findings, "NET-002", evidence)
            if self._network_cli_contains_secret(segment):
                self._append(findings, "SECRET-001", evidence)
            if command == "curl" and any(
                    token in {"-L", "--location", "--location-trusted", "--resolve", "--connect-to", "-x", "--proxy"}
                    or token.startswith(("--resolve=", "--connect-to=", "--proxy=")) for token in segment[1:]):
                self._append(findings, "NET-002", evidence)
            if any(key.upper() in {
                    "ALL_PROXY",
                    "HTTP_PROXY",
                    "HTTPS_PROXY",
                    "NO_PROXY",
            } for key in request.env):
                self._append(findings, "NET-002", evidence)
            wget_no_redirect = any(token == "--max-redirect=0" for token in segment[1:]) or any(
                token == "--max-redirect" and index + 1 < len(segment) and segment[index + 1] == "0"
                for index, token in enumerate(segment[1:], start=1))
            if command == "wget" and not wget_no_redirect:
                self._append(findings, "NET-002", evidence)
            targets = self._network_targets(segment)
            if not targets:
                self._append(findings, "NET-002", evidence)
            for target in targets:
                resolved_target = self._resolve_shell_value(target, request.env)
                if self._is_dynamic_shell_value(resolved_target):
                    self._append(findings, "NET-002", evidence)
                    continue
                host = self._hostname(resolved_target)
                if host is None:
                    self._append(findings, "NET-002", evidence)
                elif not self._domain_allowed(host):
                    self._append(findings, "NET-001", evidence)
                parsed_target = urlparse(resolved_target if "://" in resolved_target else f"//{resolved_target}")
                if parsed_target.username is not None or parsed_target.password is not None:
                    self._append(findings, "SECRET-001", evidence)
            file_inputs = self._network_file_inputs(segment, request.env)
            if any(self._is_dynamic_shell_value(path) for _role, path in file_inputs):
                self._append(findings, "PROC-003", evidence)
            if any(self._is_sensitive_path(path, request.cwd) for _role, path in file_inputs):
                self._append(findings, "FILE-003", evidence)
                self._append(findings, "SECRET-001", evidence)
            for role, path in file_inputs:
                if self._is_dynamic_shell_value(path):
                    continue
                if role == "config":
                    self._append(findings, "NET-002", evidence)
                    self._append(findings, "PROC-UNKNOWN-001", evidence)
                    if nested_statuses is not None:
                        nested_statuses.append(AnalysisStatus.UNSUPPORTED)
                elif role in {"credential", "upload"}:
                    self._append(findings, "PROC-UNKNOWN-001", evidence)
                    if nested_statuses is not None:
                        nested_statuses.append(AnalysisStatus.UNSUPPORTED)
            output_paths = self._option_values(
                segment,
                {
                    "-c",
                    "-o",
                    "-O",
                    "--cookie-jar",
                    "--output",
                    "--output-document",
                },
            )
            for output_path in output_paths:
                resolved_path = self._resolve_shell_value(output_path, request.env)
                if self._is_dynamic_shell_value(resolved_path):
                    self._append(findings, "PROC-003", evidence)
                elif self._is_sensitive_path(resolved_path, request.cwd) or self._is_protected_write_path(
                        resolved_path, request.cwd):
                    self._append(findings, "FILE-004", evidence)
        if command in {"nc", "netcat", "telnet"}:
            hosts = [token for token in segment[1:] if not token.startswith("-")]
            if not hosts:
                self._append(findings, "NET-002", evidence)
            elif not self._domain_allowed(hosts[0]):
                self._append(findings, "NET-001", evidence)

        if self._is_dependency_install(segment):
            self._append(findings, "DEP-001", evidence)
        elif not command_allowed and command not in _SHELL_BUILTINS:
            self._append(findings, "PROC-002", evidence)
        elif command_allowed and command not in _PROFILED_COMMANDS:
            self._append(findings, "PROC-UNKNOWN-001", evidence)
            if nested_statuses is not None:
                nested_statuses.append(AnalysisStatus.UNSUPPORTED)
        elif command_allowed and self._unknown_profile_options(segment):
            self._append(findings, "PROC-UNKNOWN-001", evidence)
            if nested_statuses is not None:
                nested_statuses.append(AnalysisStatus.UNSUPPORTED)

        if command in {"sleep"}:
            try:
                seconds = float(segment[1]) if len(segment) > 1 else None
            except ValueError:
                seconds = None
            if seconds is None or seconds > self.policy.limits.max_sleep_seconds:
                self._append(findings, "RES-002", evidence)
        if self._bash_static_write_exceeds(segment):
            self._append(findings, "RES-003", evidence)
        self._check_bash_redirections(findings, original_segment, request)
        nested_status = self._scan_interpreter_parts(findings, segment, request)
        if nested_status is not None and nested_statuses is not None:
            nested_statuses.append(nested_status)
        elif command in {"bash", "python", "python3", "sh", "zsh"}:
            self._append(findings, "PROC-UNKNOWN-001", evidence)
            if nested_statuses is not None:
                nested_statuses.append(AnalysisStatus.UNSUPPORTED)

    def _check_bash_redirections(
        self,
        findings: list[SafetyFinding],
        tokens: list[str],
        request: SafetyScanRequest,
    ) -> None:
        for index, token in enumerate(tokens[:-1]):
            if token not in {">", ">>", ">|"}:
                continue
            target = tokens[index + 1]
            if self._is_sensitive_path(target, request.cwd) or self._is_protected_write_path(target, request.cwd):
                self._append(findings, "FILE-004", f"{token} {target}")
            previous = tokens[index - 1] if index > 0 else ""
            if previous == "yes":
                self._append(findings, "RES-003", f"yes {token} {target}")

    def _report(
        self,
        findings: Iterable[SafetyFinding],
        started: float,
        digest: str,
        *,
        analysis_status: AnalysisStatus = AnalysisStatus.COMPLETE,
    ) -> SafetyReport:
        return aggregate_report(
            findings,
            started=started,
            digest=digest,
            policy=self.policy,
            analysis_status=analysis_status,
        )

    def _append(
        self,
        findings: list[SafetyFinding],
        rule_id: str,
        evidence: str,
        *,
        line_number: int | None = None,
        column: int | None = None,
    ) -> None:
        finding = make_finding(
            rule_id,
            self.policy,
            _sanitize_evidence(evidence),
            line_number=line_number,
            column=column,
        )
        if finding is not None:
            findings.append(finding)

    @staticmethod
    def _analysis_failure(
        evidence: str,
        *,
        line_number: int | None = None,
        column: int | None = None,
    ) -> SafetyFinding:
        """Create an immutable fail-closed parser finding."""

        spec = RULE_SPECS["PARSE-001"]
        return SafetyFinding(
            rule_id=spec.rule_id,
            category=spec.category,
            risk_level=spec.risk_level,
            action=SafetyDecision.NEEDS_HUMAN_REVIEW,
            message=spec.message,
            evidence=_sanitize_evidence(evidence),
            line_number=line_number,
            column=column,
            recommendation=spec.recommendation,
        )

    def _append_node(
        self,
        findings: list[SafetyFinding],
        rule_id: str,
        node: ast.AST,
        source: str,
    ) -> None:
        evidence = ast.get_source_segment(source, node) or type(node).__name__
        self._append(
            findings,
            rule_id,
            evidence,
            line_number=getattr(node, "lineno", None),
            column=getattr(node, "col_offset", None),
        )

    @staticmethod
    def _normalize_bash_source(content: str) -> tuple[str, str]:
        """Remove shell continuations and mask inactive comments/single quotes."""

        normalized: list[str] = []
        active: list[str] = []
        state = "unquoted"
        index = 0
        while index < len(content):
            char = content[index]
            following = content[index + 1] if index + 1 < len(content) else ""
            if state != "single" and char == "\\" and following == "\n":
                index += 2
                continue
            if state in {"unquoted", "double"} and char == "\\" and following:
                normalized.extend((char, following))
                active.extend((" ", " "))
                index += 2
                continue
            if state == "comment":
                normalized.append(char)
                active.append("\n" if char == "\n" else " ")
                if char == "\n":
                    state = "unquoted"
                index += 1
                continue
            if state == "single":
                normalized.append(char)
                active.append(" ")
                if char == "'":
                    state = "unquoted"
                index += 1
                continue
            if char == "'" and state == "unquoted":
                state = "single"
                normalized.append(char)
                active.append(" ")
                index += 1
                continue
            if char == '"':
                state = "unquoted" if state == "double" else "double"
                normalized.append(char)
                active.append(char)
                index += 1
                continue
            if (char == "#" and state == "unquoted"
                    and (not normalized or normalized[-1].isspace() or normalized[-1] in ";|&()")):
                state = "comment"
                normalized.append(char)
                active.append(" ")
                index += 1
                continue
            normalized.append(char)
            active.append(char)
            index += 1
        return "".join(normalized), "".join(active)

    @staticmethod
    def _constant_strings(tree: ast.AST) -> dict[str, str]:
        constants: dict[str, str] = {}
        assignments = [node for node in ast.walk(tree) if isinstance(node, (ast.Assign, ast.AnnAssign))]
        for _ in range(len(assignments) + 1):
            changed = False
            for node in assignments:
                value = _literal_string(node.value, constants)
                if value is None:
                    continue
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name) and constants.get(target.id) != value:
                        constants[target.id] = value
                        changed = True
            if not changed:
                break
        return constants

    @staticmethod
    def _static_iterable_bound(node: ast.AST) -> int | None:
        if isinstance(node, (ast.List, ast.Set, ast.Tuple)):
            return len(node.elts)
        if not isinstance(node, (ast.GeneratorExp, ast.ListComp, ast.SetComp)) or len(node.generators) != 1:
            return None
        generator = node.generators[0]
        if generator.ifs or not isinstance(generator.iter, ast.Call):
            return None
        if _call_name(generator.iter.func, {}) != "range":
            return None
        values = [_literal_int(argument) for argument in generator.iter.args]
        if any(value is None for value in values) or not 1 <= len(values) <= 3:
            return None
        numeric = [value for value in values if value is not None]
        try:
            return len(range(*numeric))
        except ValueError:
            return None

    @staticmethod
    def _resolve_python_string(
        node: ast.AST | None,
        constants: dict[str, str],
        request: SafetyScanRequest,
    ) -> str | None:
        value = SafetyScanner._resolve_python_value(node, constants, request)
        if value.state == ValueState.KNOWN and isinstance(value.value, str):
            return value.value
        return None

    @staticmethod
    def _resolve_python_value(
        node: ast.AST | None,
        constants: dict[str, str],
        request: SafetyScanRequest,
    ) -> AbstractValue:
        """Resolve one bounded Python value without evaluating user code."""

        value = _literal_string(node, constants)
        if value is not None or node is None:
            return AbstractValue.known(value) if value is not None else AbstractValue.unknown("missing value")
        if isinstance(node, ast.Subscript):
            owner = _call_name(node.value, {})
            if owner == "sys.argv":
                index = _literal_int(node.slice)
                if index is not None and index > 0 and index <= len(request.argv):
                    return AbstractValue.known(request.argv[index - 1])
            if owner == "os.environ":
                key = _literal_string(node.slice, constants)
                if key is not None and key in request.env:
                    return AbstractValue.known(request.env[key])
        if isinstance(node, ast.Call):
            name = _call_name(node.func, {})
            if name in {"Path", "pathlib.Path"} and node.args:
                return SafetyScanner._resolve_python_value(node.args[0], constants, request)
            if name in {"os.getenv", "os.environ.get"} and node.args:
                key = _literal_string(node.args[0], constants)
                if key is not None and key in request.env:
                    return AbstractValue.known(request.env[key])
        return AbstractValue.unknown(type(node).__name__)

    @classmethod
    def _context_strings(
        cls,
        tree: ast.AST,
        request: SafetyScanRequest,
        initial: dict[str, str],
    ) -> dict[str, str]:
        constants = dict(initial)
        assignments = [node for node in ast.walk(tree) if isinstance(node, (ast.Assign, ast.AnnAssign))]
        for _ in range(len(assignments) + 1):
            changed = False
            for node in assignments:
                value = cls._resolve_python_string(node.value, constants, request)
                if value is None:
                    continue
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name) and constants.get(target.id) != value:
                        constants[target.id] = value
                        changed = True
            if not changed:
                break
        return constants

    def _scan_interpreter_parts(
        self,
        findings: list[SafetyFinding],
        parts: list[str],
        request: SafetyScanRequest,
    ) -> AnalysisStatus | None:
        if len(parts) < 3:
            return None
        command = Path(parts[0]).name
        option = parts[1]
        if option not in {"-c", "-lc"}:
            return None
        nested_request = request.model_copy(update={
            "content": parts[2],
            "argv": parts[3:],
        }, )
        if command in {"bash", "sh", "zsh"}:
            result = self._scan_bash(nested_request)
        elif command in {"python", "python3"} and option == "-c":
            result = self._scan_python(nested_request)
        else:
            return None
        findings.extend(result.findings)
        return result.status

    @staticmethod
    def _aggregate_analysis_status(statuses: list[AnalysisStatus], ) -> AnalysisStatus:
        """Keep the most conservative status across nested analyzers."""

        priority = {
            AnalysisStatus.COMPLETE: 0,
            AnalysisStatus.UNSUPPORTED: 1,
            AnalysisStatus.PARSE_ERROR: 2,
            AnalysisStatus.BUDGET_EXCEEDED: 3,
            AnalysisStatus.INTERNAL_ERROR: 4,
        }
        return max(statuses, key=priority.__getitem__, default=AnalysisStatus.INTERNAL_ERROR)

    @staticmethod
    def _is_shell_assignment(token: str) -> bool:
        return re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", token) is not None

    @staticmethod
    def _resolve_shell_value(token: str, env: dict[str, str]) -> str:
        match = re.fullmatch(
            r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*|\d+)\}|([A-Za-z_][A-Za-z0-9_]*|\d+))",
            token,
        )
        if match:
            return env.get(match.group(1) or match.group(2), token)
        return token

    @staticmethod
    def _is_dynamic_shell_value(token: str) -> bool:
        return "$" in token or "`" in token

    @staticmethod
    def _privileged_command(segment: list[str]) -> list[str]:
        options_with_value = {"-C", "-D", "-g", "-h", "-p", "-R", "-T", "-u"}
        index = 1
        while index < len(segment):
            token = segment[index]
            if token == "--":
                return segment[index + 1:]
            if token in options_with_value:
                index += 2
                continue
            if token.startswith("-"):
                index += 1
                continue
            return segment[index:]
        return []

    @staticmethod
    def _wrapped_command(segment: list[str]) -> list[str]:
        """Return the command executed by common POSIX wrapper utilities."""

        if len(segment) < 2:
            return []
        command = Path(segment[0]).name
        index = 1
        if command == "env":
            options_with_value = {"-C", "-S", "-u", "--chdir", "--split-string", "--unset"}
            while index < len(segment):
                token = segment[index]
                if token == "--":
                    return segment[index + 1:]
                if token.startswith("--split-string="):
                    try:
                        split = shlex.split(token.split("=", 1)[1], posix=True)
                    except ValueError:
                        return ["$UNRESOLVED_ENV_SPLIT"]
                    return split + segment[index + 1:]
                if token.startswith("-S") and token != "-S":
                    try:
                        split = shlex.split(token[2:], posix=True)
                    except ValueError:
                        return ["$UNRESOLVED_ENV_SPLIT"]
                    return split + segment[index + 1:]
                if token in options_with_value:
                    if token in {"-S", "--split-string"}:
                        if index + 1 >= len(segment):
                            return ["$UNRESOLVED_ENV_SPLIT"]
                        try:
                            split = shlex.split(
                                segment[index + 1],
                                posix=True,
                            )
                        except ValueError:
                            return ["$UNRESOLVED_ENV_SPLIT"]
                        return split + segment[index + 2:]
                    index += 2
                elif token.startswith("-") or SafetyScanner._is_shell_assignment(token):
                    index += 1
                else:
                    return segment[index:]
            return []
        if command == "command":
            if any(token in {"-V", "-v"} for token in segment[1:]):
                return []
            while index < len(segment) and segment[index].startswith("-"):
                index += 1
            return segment[index:]
        if command in {"builtin", "exec", "nohup", "setsid"}:
            options_with_value = {"-a"}
            while index < len(segment):
                token = segment[index]
                if token == "--":
                    return segment[index + 1:]
                if token in options_with_value:
                    index += 2
                elif token.startswith("-"):
                    index += 1
                else:
                    return segment[index:]
            return []
        if command == "nice":
            while index < len(segment):
                token = segment[index]
                if token in {"-n", "--adjustment"}:
                    index += 2
                elif token.startswith("--adjustment=") or re.fullmatch(r"-\d+", token):
                    index += 1
                else:
                    return segment[index:]
            return []
        if command == "timeout":
            options_with_value = {"-k", "-s", "--kill-after", "--signal"}
            while index < len(segment):
                token = segment[index]
                if token == "--":
                    index += 1
                    break
                if token in options_with_value:
                    index += 2
                elif token.startswith(("--kill-after=", "--signal=")) or token.startswith("-"):
                    index += 1
                else:
                    break
            if index < len(segment):
                index += 1  # duration
            return segment[index:]
        if command == "xargs":
            options_with_value = {
                "-E",
                "-I",
                "-L",
                "-P",
                "-n",
                "-s",
                "--eof",
                "--max-args",
                "--max-chars",
                "--max-lines",
                "--max-procs",
                "--replace",
            }
            while index < len(segment):
                token = segment[index]
                if token == "--":
                    return segment[index + 1:]
                if token in options_with_value:
                    index += 2
                elif token.startswith("-"):
                    index += 1
                else:
                    return segment[index:]
        return []

    @staticmethod
    def _network_targets(segment: list[str]) -> list[str]:
        targets: list[str] = []
        options_with_value = {
            "-A",
            "-b",
            "-d",
            "-F",
            "-H",
            "-K",
            "-o",
            "-T",
            "-u",
            "--body-file",
            "--cacert",
            "--ca-certificate",
            "--cert",
            "--certificate",
            "--config",
            "--cookie",
            "--data",
            "--data-binary",
            "--form",
            "--header",
            "--load-cookies",
            "--netrc-file",
            "--output",
            "--post-file",
            "--private-key",
            "--key",
            "--upload-file",
            "--user",
        }
        index = 1
        while index < len(segment):
            token = segment[index]
            if token.startswith("--url="):
                targets.append(token.split("=", 1)[1])
            elif token == "--url" and index + 1 < len(segment):
                targets.append(segment[index + 1])
                index += 1
            elif token in options_with_value:
                index += 1
            elif ("$" in token or "`" in token or "://" in token or (not token.startswith("-") and "." in token)):
                targets.append(token)
            index += 1
        return targets

    @staticmethod
    def _unknown_network_options(segment: list[str]) -> list[str]:
        """Return CLI options outside the reviewed curl/wget profile."""

        if not segment:
            return []
        command = Path(segment[0]).name
        value_options = {
            "-A",
            "-b",
            "-c",
            "-d",
            "-E",
            "-F",
            "-H",
            "-K",
            "-m",
            "-o",
            "-O",
            "-T",
            "-u",
            "-x",
            "-X",
            "--api-key",
            "--body-file",
            "--cacert",
            "--ca-certificate",
            "--cert",
            "--certificate",
            "--connect-timeout",
            "--connect-to",
            "--config",
            "--cookie",
            "--cookie-jar",
            "--data",
            "--data-binary",
            "--form",
            "--header",
            "--input-file",
            "--key",
            "--load-cookies",
            "--max-redirect",
            "--max-time",
            "--netrc-file",
            "--output",
            "--output-document",
            "--password",
            "--post-file",
            "--private-key",
            "--proxy",
            "--request",
            "--resolve",
            "--retry",
            "--retry-delay",
            "--token",
            "--upload-file",
            "--url",
            "--user",
        }
        flag_options = {
            "-f",
            "-L",
            "-n",
            "-q",
            "-s",
            "-S",
            "--compressed",
            "--fail",
            "--location",
            "--location-trusted",
            "--netrc",
            "--netrc-optional",
            "--no-progress-meter",
            "--quiet",
            "--show-error",
            "--silent",
        }
        if command == "wget":
            value_options.update({"-i"})
        unknown: list[str] = []
        index = 1
        while index < len(segment):
            token = segment[index]
            if token == "--":
                break
            option = token.split("=", 1)[0]
            if token.startswith("--"):
                if option in value_options:
                    if "=" not in token:
                        index += 1
                elif token not in flag_options:
                    unknown.append(token)
            elif token.startswith("-") and token != "-":
                if token[:2] in value_options:
                    if len(token) == 2:
                        index += 1
                elif token not in flag_options:
                    unknown.append(token)
            index += 1
        return unknown

    @staticmethod
    def _unknown_profile_options(segment: list[str]) -> list[str]:
        """Reject unconsumed options for finite non-network command profiles."""

        if not segment:
            return []
        command = Path(segment[0]).name
        if command in {
                "bash",
                "busybox",
                "command",
                "curl",
                "env",
                "exec",
                "nohup",
                "nice",
                "python",
                "python3",
                "setsid",
                "sh",
                "timeout",
                "wget",
                "xargs",
                "zsh",
        } or command in _SHELL_BUILTINS:
            return []
        short_flags = {
            "cat": set("AbEnstuvT"),
            "grep": set("EFHhIiLnqRrsvwxc"),
            "head": set("qv"),
            "ls": set("aAcdFhiklLnrRSt"),
            "pwd": set("LP"),
            "sort": set("dfghMnRrStuV"),
            "tail": set("Fqv"),
            "uniq": set("cdiu"),
            "wc": set("cLlmw"),
        }
        value_options = {
            "grep": {"-A", "-B", "-C", "-e", "-f", "-m"},
            "head": {"-c", "-n"},
            "sort": {"-k", "-o", "-t", "--key", "--output"},
            "tail": {"-c", "-n"},
            "uniq": {"-f", "-s", "-w"},
        }
        long_flags = {
            "cat": {
                "--number",
                "--number-nonblank",
                "--show-all",
                "--show-ends",
                "--show-tabs",
                "--squeeze-blank",
            },
            "grep": {
                "--count",
                "--extended-regexp",
                "--files-with-matches",
                "--fixed-strings",
                "--ignore-case",
                "--invert-match",
                "--line-number",
                "--no-messages",
                "--only-matching",
                "--quiet",
                "--recursive",
                "--word-regexp",
            },
            "head": {"--quiet", "--verbose"},
            "ls": {
                "--all",
                "--almost-all",
                "--classify",
                "--directory",
                "--human-readable",
                "--inode",
                "--long",
                "--numeric-uid-gid",
                "--recursive",
                "--reverse",
            },
            "pwd": {"--logical", "--physical"},
            "sort": {
                "--dictionary-order",
                "--general-numeric-sort",
                "--human-numeric-sort",
                "--month-sort",
                "--numeric-sort",
                "--random-sort",
                "--reverse",
                "--stable",
                "--unique",
            },
            "tail": {"--follow", "--quiet", "--verbose"},
            "uniq": {"--count", "--ignore-case", "--repeated", "--unique"},
            "wc": {"--bytes", "--chars", "--lines", "--max-line-length", "--words"},
        }
        find_options_with_value = {
            "-exec",
            "-execdir",
            "-gid",
            "-group",
            "-iname",
            "-inum",
            "-ipath",
            "-iregex",
            "-links",
            "-maxdepth",
            "-mindepth",
            "-name",
            "-newer",
            "-ok",
            "-okdir",
            "-path",
            "-perm",
            "-regex",
            "-size",
            "-type",
            "-uid",
            "-user",
        }
        find_flags = {
            "!",
            "(",
            ")",
            "-a",
            "-delete",
            "-false",
            "-mount",
            "-noignore_readdir_race",
            "-print",
            "-print0",
            "-prune",
            "-true",
            "-xdev",
        }
        if command == "find":
            unknown = []
            index = 1
            while index < len(segment):
                token = segment[index]
                if token in find_options_with_value:
                    index += 2
                    continue
                if token.startswith("-") and token not in find_flags:
                    unknown.append(token)
                index += 1
            return unknown
        if command not in short_flags:
            return []
        unknown = []
        index = 1
        while index < len(segment):
            token = segment[index]
            option = token.split("=", 1)[0]
            if option in value_options.get(command, set()):
                if "=" not in token:
                    index += 1
            elif token.startswith("--"):
                if token not in long_flags.get(command, set()):
                    unknown.append(token)
            elif token.startswith("-") and token != "-":
                letters = token[1:]
                if not letters or not set(letters) <= short_flags[command]:
                    unknown.append(token)
            index += 1
        return unknown

    @staticmethod
    def _option_values(segment: list[str], options: set[str]) -> list[str]:
        values: list[str] = []
        for index, token in enumerate(segment[:-1]):
            if token in options:
                values.append(segment[index + 1])
        for token in segment:
            for option in options:
                if token.startswith(option + "="):
                    values.append(token.split("=", 1)[1])
        return values

    def _network_file_inputs(
        self,
        segment: list[str],
        env: dict[str, str],
    ) -> list[tuple[str, str]]:
        paths: list[tuple[str, str]] = []
        command = Path(segment[0]).name if segment else ""
        config_options = {
            "-K",
            "--config",
        }
        if command == "wget":
            config_options.update({"-i", "--input-file"})
        upload_options = {
            "-T",
            "--body-file",
            "--post-file",
            "--upload-file",
        }
        credential_options = {
            "--cacert",
            "--ca-certificate",
            "--cert",
            "--certificate",
            "-E",
            "--key",
            "--load-cookies",
            "--netrc-file",
            "--private-key",
        }
        data_options = {"-d", "--data", "--data-binary", "--form", "-F"}
        indirect_options = {"-H", "--header"}
        if command == "curl":
            indirect_options.update({"-b", "--cookie"})
        for index, token in enumerate(segment[1:], start=1):
            value: str | None = None
            compact_option: str | None = None
            all_options = config_options | upload_options | credential_options | data_options | indirect_options
            if token in all_options and index + 1 < len(segment):
                value = segment[index + 1]
            elif any(token.startswith(option + "=") for option in all_options):
                value = token.split("=", 1)[1]
            else:
                compact_option = next(
                    (option for option in {
                        "-K",
                        "-T",
                        "-d",
                        "-F",
                        "-H",
                        "-b",
                        "-E",
                        *(("-i", ) if command == "wget" else ()),
                    } if token.startswith(option) and len(token) > len(option)),
                    None,
                )
                if compact_option is not None:
                    value = token[len(compact_option):]
            if value is None:
                continue
            option = compact_option if compact_option is not None else token.split("=", 1)[0]
            role: str
            if option in config_options:
                role = "config"
            elif option in upload_options:
                role = "upload"
            elif option in credential_options:
                role = "credential"
            elif option in indirect_options:
                role = "credential" if "@" in value else ""
            else:
                role = "upload" if "@" in value else ""
            if "=" in value and "@" in value:
                value = value.rsplit("@", 1)[1]
            elif value.startswith("@"):
                value = value[1:]
            elif not role:
                continue
            paths.append((role, self._resolve_shell_value(value, env)))
        if command == "curl" and any(token in {"-n", "--netrc", "--netrc-optional"} for token in segment[1:]):
            home = env.get("HOME")
            paths.append((
                "credential",
                str(Path(home) / ".netrc") if home else "$HOME/.netrc",
            ))
        return paths

    @staticmethod
    def _network_cli_contains_secret(segment: list[str]) -> bool:
        credential_options = {
            "-b",
            "-u",
            "--api-key",
            "--cookie",
            "--password",
            "--token",
            "--user",
        }
        header_options = {"-H", "--header"}
        for index, token in enumerate(segment[1:], start=1):
            option = token.split("=", 1)[0]
            value: str | None = None
            if option in credential_options | header_options:
                if "=" in token:
                    value = token.split("=", 1)[1]
                elif index + 1 < len(segment):
                    value = segment[index + 1]
            elif token.startswith(("-b", "-u")) and len(token) > 2:
                option = token[:2]
                value = token[2:]
            if not value:
                continue
            if option in credential_options:
                return True
            if option in header_options and _SENSITIVE_HEADER_RE.search(value):
                return True
        return False

    @staticmethod
    def _import_aliases(tree: ast.AST) -> dict[str, str]:
        aliases: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for item in node.names:
                    if item.asname:
                        aliases[item.asname] = item.name
                    else:
                        top_level = item.name.split(".")[0]
                        aliases[top_level] = top_level
            elif isinstance(node, ast.ImportFrom) and node.module:
                for item in node.names:
                    aliases[item.asname or item.name] = f"{node.module}.{item.name}"
        return aliases

    @classmethod
    def _analysis_aliases(cls, tree: ast.AST) -> dict[str, str]:
        """Resolve imports and simple callable/client aliases without execution."""

        aliases = cls._import_aliases(tree)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            cls._update_analysis_aliases(aliases, node)
        return aliases

    @classmethod
    def _call_aliases(cls, tree: ast.AST) -> dict[int, dict[str, str]]:
        parent = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        module = tree
        imports_by_scope: dict[int, dict[str, str]] = {}
        assignments_by_scope: dict[int, list[ast.Assign | ast.AnnAssign]] = {}
        for node in ast.walk(tree):
            scope = cls._lexical_scope(node, parent, module)
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                aliases = imports_by_scope.setdefault(id(scope), {})
                cls._update_import_aliases(aliases, node)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                if cls._is_conditional_binding(node, scope, parent):
                    continue
                assignments_by_scope.setdefault(id(scope), []).append(node)

        result: dict[int, dict[str, str]] = {}
        for node in (item for item in ast.walk(tree) if isinstance(item, ast.Call)):
            scope = cls._lexical_scope(node, parent, module)
            aliases: dict[str, str] = {}
            for binding_scope in cls._scope_chain(node, parent, module):
                if isinstance(binding_scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                    arguments = binding_scope.args
                    parameters = [
                        *arguments.posonlyargs,
                        *arguments.args,
                        *arguments.kwonlyargs,
                    ]
                    if arguments.vararg is not None:
                        parameters.append(arguments.vararg)
                    if arguments.kwarg is not None:
                        parameters.append(arguments.kwarg)
                    for parameter in parameters:
                        aliases[parameter.arg] = f"{_LOCAL_BINDING_PREFIX}{parameter.arg}"
                aliases.update(imports_by_scope.get(id(binding_scope), {}))
                assignments = sorted(
                    assignments_by_scope.get(id(binding_scope), []),
                    key=lambda item: (getattr(item, "lineno", 0), getattr(item, "col_offset", 0)),
                )
                for assignment in assignments:
                    if binding_scope is scope and getattr(assignment, "lineno", 0) > getattr(node, "lineno", 0):
                        break
                    cls._update_analysis_aliases(aliases, assignment)
            result[id(node)] = aliases
        return result

    @staticmethod
    def _update_import_aliases(
        aliases: dict[str, str],
        node: ast.Import | ast.ImportFrom,
    ) -> None:
        if isinstance(node, ast.Import):
            for item in node.names:
                if item.asname:
                    aliases[item.asname] = item.name
                else:
                    top_level = item.name.split(".")[0]
                    aliases[top_level] = top_level
        elif node.module:
            for item in node.names:
                aliases[item.asname or item.name] = f"{node.module}.{item.name}"

    @staticmethod
    def _lexical_scope(
        node: ast.AST,
        parent: dict[ast.AST, ast.AST],
        module: ast.AST,
    ) -> ast.AST:
        current = parent.get(node)
        while current is not None:
            if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
                return current
            current = parent.get(current)
        return module

    @staticmethod
    def _scope_chain(
        node: ast.AST,
        parent: dict[ast.AST, ast.AST],
        module: ast.AST,
    ) -> list[ast.AST]:
        scopes: list[ast.AST] = []
        current = parent.get(node)
        while current is not None:
            if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                scopes.append(current)
            elif isinstance(current, ast.ClassDef) and not any(
                    isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)) for scope in scopes):
                scopes.append(current)
            current = parent.get(current)
        scopes.append(module)
        scopes.reverse()
        return scopes

    @staticmethod
    def _is_conditional_binding(
        node: ast.Assign | ast.AnnAssign,
        scope: ast.AST,
        parent: dict[ast.AST, ast.AST],
    ) -> bool:
        current = parent.get(node)
        while current is not None and current is not scope:
            if isinstance(current, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.Match, ast.With,
                                    ast.AsyncWith, ast.comprehension)):
                return True
            current = parent.get(current)
        return False

    @classmethod
    def _ambiguous_bindings(
        cls,
        tree: ast.AST,
        parent: dict[ast.AST, ast.AST],
    ) -> set[str]:
        scopes: dict[str, set[int]] = {}
        ambiguous: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            scope = cls._lexical_scope(node, parent, tree)
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if not isinstance(target, ast.Name):
                    continue
                scopes.setdefault(target.id, set()).add(id(scope))
                if cls._is_conditional_binding(node, scope, parent):
                    ambiguous.add(target.id)
        ambiguous.update(name for name, binding_scopes in scopes.items() if len(binding_scopes) > 1)
        return ambiguous

    @classmethod
    def _conditional_bindings(
        cls,
        tree: ast.AST,
        parent: dict[ast.AST, ast.AST],
    ) -> set[str]:
        result: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            scope = cls._lexical_scope(node, parent, tree)
            if not cls._is_conditional_binding(node, scope, parent):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            result.update(target.id for target in targets if isinstance(target, ast.Name))
        return result

    @staticmethod
    def _root_name(node: ast.AST) -> str | None:
        current = node
        while isinstance(current, (ast.Attribute, ast.Call)):
            current = current.value if isinstance(current, ast.Attribute) else current.func
        return current.id if isinstance(current, ast.Name) else None

    @staticmethod
    def _argument_names(node: ast.Call) -> set[str]:
        return {
            child.id
            for value in [*node.args, *(keyword.value for keyword in node.keywords)]
            for child in ast.walk(value) if isinstance(child, ast.Name)
        }

    @staticmethod
    def _update_analysis_aliases(
        aliases: dict[str, str],
        node: ast.Assign | ast.AnnAssign,
    ) -> None:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        names = [target.id for target in targets if isinstance(target, ast.Name)]
        if not names:
            return
        value = node.value
        resolved = ""
        if isinstance(value, (ast.Name, ast.Attribute)):
            resolved = _call_name(value, aliases)
        elif isinstance(value, ast.Call):
            candidate = _call_name(value.func, aliases)
            if candidate == "pathlib.Path" or candidate in _KNOWN_CLIENT_CONSTRUCTORS:
                resolved = candidate
        for name in names:
            if resolved and resolved != name:
                aliases[name] = resolved
            else:
                aliases[name] = f"{_LOCAL_BINDING_PREFIX}{name}"

    def _sensitive_assignments(
        self,
        tree: ast.AST,
        aliases: dict[str, str],
        initial: set[str] | None = None,
    ) -> set[str]:
        result = set(initial or ())
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            value = node.value
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            is_secret = self._contains_secret(value, aliases, result)
            if not is_secret:
                is_secret = any(
                    isinstance(target, ast.Name) and _SENSITIVE_NAME_RE.search(target.id) for target in targets)
            if is_secret:
                for target in targets:
                    if isinstance(target, ast.Name):
                        result.add(target.id)
        return result

    @classmethod
    def _file_tainted_assignments(
        cls,
        tree: ast.AST,
        aliases: dict[str, str],
        local_functions: set[str],
    ) -> set[str]:
        """Track local values derived from file contents without reading files."""

        handles: set[str] = set()
        assignments = [node for node in ast.walk(tree) if isinstance(node, (ast.Assign, ast.AnnAssign))]
        for node in assignments:
            value = node.value
            if not isinstance(value, ast.Call):
                continue
            call_name = _call_name(value.func, aliases)
            if (call_name in local_functions or call_name not in {
                    "open",
                    "io.open",
                    "pathlib.Path.open",
            }):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            handles.update(target.id for target in targets if isinstance(target, ast.Name))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.With, ast.AsyncWith)):
                continue
            for item in node.items:
                context = item.context_expr
                if (isinstance(context, ast.Call)
                        and _call_name(context.func, aliases) in {"open", "io.open", "pathlib.Path.open"}
                        and isinstance(item.optional_vars, ast.Name)):
                    handles.add(item.optional_vars.id)

        tainted: set[str] = set()
        functions = [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for _ in range(len(assignments) + len(functions) + 1):
            changed = False
            for function in functions:
                if function.name in tainted:
                    continue
                if any(
                        cls._contains_file_taint(child.value, aliases, tainted | handles)
                        for child in ast.walk(function) if isinstance(child, ast.Return)):
                    tainted.add(function.name)
                    changed = True
            for node in assignments:
                if not cls._contains_file_taint(node.value, aliases, tainted | handles):
                    continue
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name) and target.id not in tainted:
                        tainted.add(target.id)
                        changed = True
            if not changed:
                break
        return tainted | handles

    def _sensitive_network_clients(
        self,
        tree: ast.AST,
        aliases: dict[str, str],
        sensitive_names: set[str],
    ) -> set[str]:
        """Track HTTP clients configured with credentials before a request."""

        result: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                value = node.value
                if isinstance(value, ast.Call) and _call_name(value.func, aliases) in _KNOWN_CLIENT_CONSTRUCTORS:
                    if self._call_has_credential_keywords(value, aliases, sensitive_names):
                        result.update(target.id for target in targets if isinstance(target, ast.Name))
                for target in targets:
                    if not isinstance(target, ast.Attribute) or target.attr not in {"auth", "cookies", "headers"}:
                        continue
                    owner = self._root_name(target)
                    if owner is None:
                        continue
                    if target.attr in {"auth", "cookies"} and self._has_nonempty_value(value):
                        result.add(owner)
                    elif self._contains_secret(value, aliases, sensitive_names):
                        result.add(owner)
        return result

    @classmethod
    def _network_client_constructor_requires_review(cls, node: ast.Call) -> bool:
        """Reject client defaults that can change the reviewed request route."""

        if node.args or any(keyword.arg is None for keyword in node.keywords):
            return True
        for keyword in node.keywords:
            if keyword.arg == "timeout":
                continue
            if keyword.arg in {"follow_redirects", "trust_env"}:
                if isinstance(keyword.value, ast.Constant) and keyword.value.value is False:
                    continue
                return True
            if keyword.arg in {"proxy", "proxies"}:
                if isinstance(keyword.value, ast.Constant) and keyword.value.value is None:
                    continue
                return True
            if keyword.arg in {
                    "auth",
                    "base_url",
                    "cert",
                    "cookies",
                    "headers",
                    "mounts",
                    "transport",
                    "verify",
            }:
                return True
            return True
        return False

    @staticmethod
    def _network_client_attribute_requires_review(attribute: str, value: ast.AST | None) -> bool:
        """Return whether persistent client state can change request behavior."""

        if attribute not in {
                "cert",
                "follow_redirects",
                "mounts",
                "proxies",
                "proxy",
                "transport",
                "trust_env",
                "verify",
        }:
            return False
        if attribute in {"follow_redirects", "trust_env"}:
            return not (isinstance(value, ast.Constant) and value.value is False)
        if attribute in {"proxy", "proxies"}:
            if isinstance(value, ast.Constant) and value.value is None:
                return False
            if isinstance(value, (ast.Dict, ast.List, ast.Set, ast.Tuple)):
                return bool(value.keys if isinstance(value, ast.Dict) else value.elts)
        return True

    def _network_call_contains_secret(
        self,
        node: ast.Call,
        aliases: dict[str, str],
        sensitive_names: set[str],
        sensitive_clients: set[str],
    ) -> bool:
        owner = self._root_name(node.func)
        if owner in sensitive_clients:
            return True
        return self._call_has_credential_keywords(node, aliases, sensitive_names)

    def _call_has_credential_keywords(
        self,
        node: ast.Call,
        aliases: dict[str, str],
        sensitive_names: set[str],
    ) -> bool:
        for keyword in node.keywords:
            if keyword.arg in {"auth", "cookies"} and self._has_nonempty_value(keyword.value):
                return True
            if keyword.arg in {"data", "headers", "json", "params"} and self._contains_secret(
                    keyword.value,
                    aliases,
                    sensitive_names,
            ):
                return True
        return False

    @staticmethod
    def _has_nonempty_value(node: ast.AST) -> bool:
        if isinstance(node, ast.Constant):
            return node.value is not None and node.value != ""
        if isinstance(node, (ast.Dict, ast.List, ast.Set, ast.Tuple)):
            return bool(node.keys if isinstance(node, ast.Dict) else node.elts)
        return True

    @classmethod
    def _contains_file_taint(
        cls,
        node: ast.AST | None,
        aliases: dict[str, str],
        tainted_names: set[str],
    ) -> bool:
        """Return whether an expression contains data read from a file."""

        if node is None:
            return False
        if isinstance(node, ast.Name) and node.id in tainted_names:
            return True
        if isinstance(node, ast.Call):
            call_name = _call_name(node.func, aliases)
            if call_name in {
                    "open",
                    "io.open",
                    "pathlib.Path.open",
                    "pathlib.Path.read_bytes",
                    "pathlib.Path.read_text",
            } or call_name in tainted_names:
                return True
            if isinstance(node.func, ast.Attribute) and node.func.attr in {"read", "readline", "readlines"}:
                owner = node.func.value
                if isinstance(owner, ast.Name) and owner.id in tainted_names:
                    return True
                if (isinstance(owner, ast.Call)
                        and _call_name(owner.func, aliases) in {"open", "io.open", "pathlib.Path.open"}):
                    return True
        return any(cls._contains_file_taint(child, aliases, tainted_names) for child in ast.iter_child_nodes(node))

    def _contains_secret(
        self,
        node: ast.AST | None,
        aliases: dict[str, str],
        sensitive_names: set[str],
    ) -> bool:
        if node is None:
            return False
        if (isinstance(node, ast.Call) and _call_name(node.func, aliases) == "len" and len(node.args) == 1
                and not node.keywords):
            return False
        parent = {child: owner for owner in ast.walk(node) for child in ast.iter_child_nodes(owner)}
        for child in ast.walk(node):
            if isinstance(child, ast.Dict):
                for key, value in zip(child.keys, child.values):
                    key_name = _literal_string(key)
                    if key_name and _SENSITIVE_NAME_RE.search(key_name.replace("-", "_")):
                        return True
                    if key_name and key_name.lower() == "authorization":
                        return True
            if isinstance(child, ast.Constant) and isinstance(child.value, str):
                if _looks_like_secret_literal(child.value):
                    return True
            if isinstance(child, ast.Name) and child.id in sensitive_names:
                return True
            if isinstance(child, (ast.Name, ast.Attribute)):
                name = _call_name(child, aliases)
                owner = parent.get(child)
                is_subscript_owner = isinstance(owner, ast.Subscript) and owner.value is child
                is_get_owner = (isinstance(owner, ast.Attribute) and owner.value is child and owner.attr == "get")
                if name in {"os.environ", "environ"} and not is_subscript_owner and not is_get_owner:
                    return True
                if (name == "sys.argv" and _SENSITIVE_ARGV_SENTINEL in sensitive_names and not is_subscript_owner):
                    return True
            if isinstance(child, ast.Subscript):
                name = _call_name(child.value, aliases)
                key = _literal_string(child.slice)
                if name in {"os.environ", "environ"} and key and _SENSITIVE_NAME_RE.search(key):
                    return True
                if name == "sys.argv" and _SENSITIVE_ARGV_SENTINEL in sensitive_names:
                    return True
            if isinstance(child, ast.Call):
                name = _call_name(child.func, aliases)
                if name in {"os.getenv", "os.environ.get", "environ.get"} and child.args:
                    key = _literal_string(child.args[0])
                    if key and _SENSITIVE_NAME_RE.search(key):
                        return True
                if name.endswith((".read_text", ".read_bytes")) and isinstance(child.func, ast.Attribute):
                    owner = child.func.value
                    if isinstance(owner, ast.Call) and owner.args:
                        path = _literal_string(owner.args[0])
                        if path and self._is_sensitive_path(path, None):
                            return True
                if name in {"open", "io.open"} and child.args:
                    path = _literal_string(child.args[0])
                    if path and self._is_sensitive_path(path, None):
                        return True
        return False

    def _local_call_returns_secret(
        self,
        node: ast.AST,
        functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
        aliases: dict[str, str],
        sensitive_names: set[str],
        visited: set[str] | None = None,
    ) -> bool:
        """Propagate secret values through bounded local function returns."""

        if not isinstance(node, ast.Call):
            return any(
                self._local_call_returns_secret(
                    child,
                    functions,
                    aliases,
                    sensitive_names,
                    visited,
                ) for child in ast.iter_child_nodes(node))
        function_name = _call_name(node.func, aliases)
        function = functions.get(function_name)
        if function is None:
            return False
        seen = set(visited or ())
        if function_name in seen:
            return False
        seen.add(function_name)
        local_sensitive = set(sensitive_names)
        positional = list(function.args.posonlyargs) + list(function.args.args)
        local_sensitive.update(parameter.arg for parameter in positional + list(function.args.kwonlyargs)
                               if _SENSITIVE_NAME_RE.search(parameter.arg))
        bound_parameters: set[str] = set()
        for parameter, argument in zip(positional, node.args):
            bound_parameters.add(parameter.arg)
            if (self._contains_secret(argument, aliases, sensitive_names)
                    or self._local_call_returns_secret(argument, functions, aliases, sensitive_names, seen)):
                local_sensitive.add(parameter.arg)
        keyword_arguments = {keyword.arg: keyword.value for keyword in node.keywords if keyword.arg is not None}
        for parameter in positional + list(function.args.kwonlyargs):
            argument = keyword_arguments.get(parameter.arg)
            if argument is not None and (self._contains_secret(argument, aliases, sensitive_names)
                                         or self._local_call_returns_secret(argument, functions, aliases,
                                                                            sensitive_names, seen)):
                local_sensitive.add(parameter.arg)
            if argument is not None:
                bound_parameters.add(parameter.arg)
        positional_defaults = zip(positional[-len(function.args.defaults):], function.args.defaults)
        for parameter, default in positional_defaults:
            if parameter.arg not in bound_parameters and self._contains_secret(default, aliases, sensitive_names):
                local_sensitive.add(parameter.arg)
        for parameter, default in zip(function.args.kwonlyargs, function.args.kw_defaults):
            if (parameter.arg not in bound_parameters and default is not None
                    and self._contains_secret(default, aliases, sensitive_names)):
                local_sensitive.add(parameter.arg)
        for child in ast.walk(function):
            if not isinstance(child, ast.Return) or child.value is None:
                continue
            if self._contains_secret(child.value, aliases, local_sensitive):
                return True
            if self._local_call_returns_secret(
                    child.value,
                    functions,
                    aliases,
                    local_sensitive,
                    seen,
            ):
                return True
        return False

    @staticmethod
    def _is_secret_sink(call_name: str) -> bool:
        suffixes = (
            ".debug",
            ".info",
            ".warning",
            ".error",
            ".critical",
            ".write",
            ".write_text",
            ".write_bytes",
            ".send",
        )
        return (call_name == "print" or call_name.startswith("logging.") or call_name.endswith(suffixes)
                or call_name in _NETWORK_FUNCTIONS or call_name.endswith(
                    (".delete", ".patch", ".post", ".put", ".request")))

    @staticmethod
    def _is_true(node: ast.AST) -> bool:
        try:
            return bool(ast.literal_eval(node))
        except (ValueError, TypeError):
            return False

    def _is_sensitive_path(self, value: str, cwd: str | None) -> bool:
        path = self._normalize_path(value, cwd)
        if len(path.parts) >= 3 and path.parts[1] == "proc" and path.name == "environ":
            return True
        for denied in self.policy.paths.denied:
            denied_expanded = Path(os.path.expanduser(denied))
            if denied == ".env":
                if path.name == ".env":
                    return True
                continue
            denied_path = (denied_expanded.resolve(
                strict=False) if denied_expanded.is_absolute() else self._normalize_path(denied, cwd))
            if path == denied_path or denied_path in path.parents:
                return True
        return False

    def _is_system_path(self, value: str, cwd: str | None) -> bool:
        path = self._normalize_path(value, cwd)
        return path == Path("/") or any(path == root or root in path.parents for root in _SYSTEM_PATHS)

    def _is_protected_write_path(self, value: str, cwd: str | None) -> bool:
        path = self._normalize_path(value, cwd)
        return path not in _SAFE_SYSTEM_WRITE_PATHS and self._is_system_path(value, cwd)

    def _is_protected_delete(self, value: str, cwd: str | None) -> bool:
        path = self._normalize_path(value, cwd)
        home = Path.home().resolve(strict=False)
        if path in {Path("/"), home} or self._is_system_path(value, cwd) or self._is_sensitive_path(value, cwd):
            return True
        if self.policy.paths.workspace_only_delete and cwd:
            workspace = Path(os.path.expanduser(cwd)).resolve(strict=False)
            return path != workspace and workspace not in path.parents
        return False

    @staticmethod
    def _normalize_path(value: str, cwd: str | None) -> Path:
        expanded = Path(os.path.expanduser(value))
        if not expanded.is_absolute():
            base = Path(os.path.expanduser(cwd)).resolve(strict=False) if cwd else Path.cwd()
            expanded = base / expanded
        return expanded.resolve(strict=False)

    def _domain_allowed(self, host: str) -> bool:
        normalized = host.lower().rstrip(".")
        for allowed in self.policy.network.allowed_domains:
            if normalized == allowed:
                return True
            if self.policy.network.allow_subdomains and normalized.endswith("." + allowed):
                return True
        return False

    def _command_allowed(self, raw_command: str) -> bool:
        """Match path-qualified commands exactly and PATH commands by name."""

        if "/" in raw_command:
            return raw_command in self.policy.commands.allowed
        return Path(raw_command).name in self.policy.commands.allowed

    @staticmethod
    def _assignment_keys_affect_execution(tokens: Iterable[str]) -> bool:
        return any(
            token.split("=", 1)[0].upper() in _EXECUTION_ENV_KEYS for token in tokens
            if SafetyScanner._is_shell_assignment(token))

    @staticmethod
    def _environment_affects_execution(env: dict[str, str], raw_command: str) -> bool:
        keys = {key.upper() for key in env}
        if keys & (_EXECUTION_ENV_KEYS - {"PATH"}):
            return True
        return "PATH" in keys and "/" not in raw_command

    @staticmethod
    def _python_env_requires_review(node: ast.AST, constants: dict[str, str]) -> bool:
        if isinstance(node, ast.Constant) and node.value is None:
            return False
        if not isinstance(node, ast.Dict):
            return True
        keys: set[str] = set()
        for key_node in node.keys:
            key = _literal_string(key_node, constants)
            if key is None:
                return True
            keys.add(key.upper())
        return bool(keys & _EXECUTION_ENV_KEYS)

    @staticmethod
    def _hostname(target: str) -> str | None:
        parsed = urlparse(target if "://" in target else f"//{target}")
        return parsed.hostname

    @staticmethod
    def _is_dependency_install(parts: list[str]) -> bool:
        if not parts:
            return False
        command = Path(parts[0]).name
        if command not in _DEPENDENCY_COMMANDS:
            return False
        actions = set(parts[1:])
        return bool(actions & {"install", "uninstall", "add", "remove"})

    @staticmethod
    def _argument_contains_secret(value: str) -> bool:
        if _looks_like_secret_literal(value):
            return True
        if "=" not in value:
            return False
        key, secret_value = value.split("=", 1)
        return bool(secret_value and _SENSITIVE_NAME_RE.search(key.lstrip("-")))

    @staticmethod
    def _command_segments(tokens: list[str]) -> list[list[str]]:
        segments: list[list[str]] = []
        current: list[str] = []
        for token in tokens:
            if token in _SHELL_SEPARATORS:
                if current:
                    segments.append(current)
                    current = []
            else:
                current.append(token)
        if current:
            segments.append(current)
        return segments

    @staticmethod
    def _bash_secret_flow(
        tokens: list[str],
        expanded_variables: set[str],
        sensitive_env_names: set[str],
    ) -> bool:
        sinks = {"echo", "printf", "curl", "wget", "nc", "tee", ">", ">>"}
        for segment in SafetyScanner._command_segments(tokens):
            if not any(token in sinks for token in segment):
                continue
            if any(_looks_like_secret_literal(token) for token in segment):
                return True
            if any(name in sensitive_env_names or _SENSITIVE_NAME_RE.search(name) for name in expanded_variables):
                return True
        return False

    def _bash_static_write_exceeds(self, segment: list[str]) -> bool:
        if not segment:
            return False
        command = Path(segment[0]).name
        limit = self.policy.limits.max_static_write_size_bytes
        if command in {"truncate", "fallocate"}:
            for index, token in enumerate(segment[:-1]):
                if token in {"-s", "--size", "-l", "--length"}:
                    size = self._parse_size(segment[index + 1])
                    return size is not None and size > limit
        if command == "dd":
            values = {
                key: self._parse_size(value)
                for token in segment[1:] if "=" in token for key, value in [token.split("=", 1)]
                if key in {"bs", "count"}
            }
            if values.get("bs") is not None and values.get("count") is not None:
                return values["bs"] * values["count"] > limit  # type: ignore[operator]
        return command == "yes" and any(token in {">", ">>"} for token in segment)

    @staticmethod
    def _parse_size(value: str) -> int | None:
        match = re.fullmatch(r"(\d+)([KMGTP]?)(?:i?B?)?", value, re.IGNORECASE)
        if not match:
            return None
        number = int(match.group(1))
        suffix = match.group(2).upper()
        multiplier = {
            "": 1,
            "K": 1024,
            "M": 1024**2,
            "G": 1024**3,
            "T": 1024**4,
            "P": 1024**5,
        }[suffix]
        return number * multiplier
