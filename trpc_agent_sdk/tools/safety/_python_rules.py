# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Python AST safety rules."""

from __future__ import annotations

import ast
from dataclasses import dataclass
import math
import operator
import re
import shlex
from typing import Any
from urllib.parse import urlparse

from ._bash_rules import scan_bash
from ._common_rules import host_allowed
from ._common_rules import path_forbidden
from ._common_rules import path_is_system_location
from ._models import RiskCategory
from ._models import RiskLevel
from ._models import SafetyDecision
from ._models import SafetyFinding
from ._models import ScriptScanRequest
from ._models import ToolSafetyPolicy
from ._common_rules import make_finding
from ._common_rules import RuleSpec
from ._sanitizer import SafetySanitizer

_NETWORK_ROOTS = frozenset({"requests", "aiohttp", "socket", "urllib", "httpx"})
_NETWORK_METHODS = frozenset({
    "connect",
    "create_connection",
    "delete",
    "get",
    "head",
    "open",
    "options",
    "patch",
    "post",
    "put",
    "request",
    "send",
    "stream",
    "trace",
    "urlopen",
    "ws_connect",
})
_NETWORK_CLIENT_CONSTRUCTORS = frozenset({
    "AsyncClient",
    "Client",
    "ClientSession",
    "Session",
    "build_opener",
    "session",
    "socket",
})
_NETWORK_NON_IO_METHODS = _NETWORK_CLIENT_CONSTRUCTORS | frozenset({
    "ClientTimeout",
    "Cookies",
    "FormData",
    "Headers",
    "Limits",
    "PreparedRequest",
    "QueryParams",
    "Request",
    "TCPConnector",
    "Timeout",
    "URL",
    "aclose",
    "build_request",
    "close",
    "mount",
    "prepare_request",
})
_NETWORK_HELPER_PREFIXES = ("requests.auth.", "requests.cookies.", "requests.models.", "requests.utils.",
                            "urllib.parse.")
_PROCESS_CALLS = frozenset({
    "asyncio.create_subprocess_exec",
    "asyncio.create_subprocess_shell",
    "concurrent.futures.ProcessPoolExecutor",
    "multiprocessing.Pool",
    "multiprocessing.Process",
    "os.fork",
    "os.forkpty",
    "os.popen",
    "os.posix_spawn",
    "os.posix_spawnp",
    "os.startfile",
    "os.system",
    "pty.spawn",
    "anyio.open_process",
    "anyio.run_process",
    "subprocess.Popen",
    "subprocess.call",
    "subprocess.run",
    "trio.run_process",
})
_PROCESS_ROOTS = frozenset({"subprocess"})
_OS_PROCESS_PREFIXES = ("exec", "spawn")
_PROCESS_CREATION_METHODS = frozenset({"Pool", "Process", "ProcessPoolExecutor", "fork", "forkpty"})
_CONSERVATIVE_FILE_ROOTS = frozenset({"shutil"})
_RISK_SYMBOL_ROOTS = _NETWORK_ROOTS | _PROCESS_ROOTS | frozenset({
    "anyio",
    "asyncio",
    "concurrent",
    "multiprocessing",
    "os",
    "pty",
    "shutil",
    "trio",
})
_DELETE_CALLS = frozenset({"shutil.rmtree", "os.remove", "os.unlink", "os.rmdir"})
_DIRECT_FILE_CALLS = frozenset({"open", "builtins.open", "io.open", "os.open", "os.remove", "os.unlink", "os.rmdir"})
_PATH_METHODS = frozenset({"open", "read_text", "read_bytes", "write_text", "write_bytes", "unlink", "rmdir"})
_OUTPUT_CALLS = frozenset(
    {"print", "logging.info", "logging.warning", "logging.error", "logger.info", "logger.warning", "logger.error"})
_SECRET_NAME_RE = re.compile(
    r"(?i)(api[_-]?key|access[_-]?key|authorization|credential|token|password|passwd|secret|private[_-]?key)")
_UNKNOWN_VALUE = object()
_MAX_STATIC_LITERAL_ITEMS = 256
_DYNAMIC_COMMAND_TOKEN = "__tool_safety_dynamic_arg__"


def _static_truthy(node: ast.AST) -> bool:
    """Return whether a literal loop condition is statically true."""
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        truthiness = _static_truthiness(node.operand)
        return False if truthiness is None else not truthiness
    truthiness = _static_truthiness(node)
    if truthiness is not None:
        return truthiness
    if isinstance(node, ast.Compare) and len(node.ops) == 1 and len(node.comparators) == 1:
        left = _static_value(node.left)
        right = _static_value(node.comparators[0])
        if left is _UNKNOWN_VALUE or right is _UNKNOWN_VALUE:
            return False
        comparisons = {
            ast.Eq: operator.eq,
            ast.NotEq: operator.ne,
            ast.Lt: operator.lt,
            ast.LtE: operator.le,
            ast.Gt: operator.gt,
            ast.GtE: operator.ge,
        }
        for kind, compare in comparisons.items():
            if isinstance(node.ops[0], kind):
                try:
                    return bool(compare(left, right))
                except (TypeError, ValueError):
                    return False
    return False


def _static_truthiness(node: ast.AST) -> bool | None:
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        truthiness = _static_truthiness(node.operand)
        return None if truthiness is None else not truthiness
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return bool(node.elts)
    if isinstance(node, ast.Dict):
        return bool(node.keys)
    value = _static_value(node)
    if value is not _UNKNOWN_VALUE:
        return bool(value)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        left = _static_value(node.left)
        right = _static_value(node.right)
        if _finite_number(left) and _finite_number(right):
            return bool(left * right)
        if isinstance(left, (str, bytes, list, tuple)) and isinstance(right, int) and not isinstance(right, bool):
            return bool(left) and right != 0
        if isinstance(right, (str, bytes, list, tuple)) and isinstance(left, int) and not isinstance(left, bool):
            return bool(right) and left != 0
        return None
    return None


def _finite_number(value: object) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    return isinstance(value, float) and math.isfinite(value)


def _static_value(node: ast.AST) -> object:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Tuple):
        if len(node.elts) > _MAX_STATIC_LITERAL_ITEMS:
            return _UNKNOWN_VALUE
        values = []
        for element in node.elts:
            value = _static_value(element)
            if value is _UNKNOWN_VALUE:
                return _UNKNOWN_VALUE
            values.append(value)
        return tuple(values)
    if isinstance(node, ast.List):
        if len(node.elts) > _MAX_STATIC_LITERAL_ITEMS:
            return _UNKNOWN_VALUE
        values = []
        for element in node.elts:
            value = _static_value(element)
            if value is _UNKNOWN_VALUE:
                return _UNKNOWN_VALUE
            values.append(value)
        return values
    if isinstance(node, ast.Set):
        if len(node.elts) > _MAX_STATIC_LITERAL_ITEMS:
            return _UNKNOWN_VALUE
        values = []
        for element in node.elts:
            value = _static_value(element)
            if value is _UNKNOWN_VALUE:
                return _UNKNOWN_VALUE
            values.append(value)
        try:
            return set(values)
        except TypeError:
            return _UNKNOWN_VALUE
    if isinstance(node, ast.Dict):
        if len(node.keys) > _MAX_STATIC_LITERAL_ITEMS:
            return _UNKNOWN_VALUE
        keys = []
        values = []
        for key, value in zip(node.keys, node.values):
            key_value = _static_value(key) if key is not None else _UNKNOWN_VALUE
            value_value = _static_value(value)
            if key_value is _UNKNOWN_VALUE or value_value is _UNKNOWN_VALUE:
                return _UNKNOWN_VALUE
            keys.append(key_value)
            values.append(value_value)
        try:
            return dict(zip(keys, values))
        except TypeError:
            return _UNKNOWN_VALUE
    return _UNKNOWN_VALUE


FILE_DELETE = RuleSpec(
    RiskCategory.FILE,
    RiskLevel.CRITICAL,
    SafetyDecision.DENY,
    "Remove recursive deletion or constrain it to an approved workspace.",
)
FILE_DENY = RuleSpec(
    RiskCategory.FILE,
    RiskLevel.HIGH,
    SafetyDecision.DENY,
    "Remove access to sensitive or forbidden paths.",
)
FILE_REVIEW = RuleSpec(
    RiskCategory.FILE,
    RiskLevel.MEDIUM,
    SafetyDecision.NEEDS_HUMAN_REVIEW,
    "Resolve and approve the dynamic file path.",
)
NETWORK_DENY = RuleSpec(
    RiskCategory.NETWORK,
    RiskLevel.HIGH,
    SafetyDecision.DENY,
    "Use a literal destination from allowed_domains.",
)
NETWORK_REVIEW = RuleSpec(
    RiskCategory.NETWORK,
    RiskLevel.MEDIUM,
    SafetyDecision.NEEDS_HUMAN_REVIEW,
    "Resolve and approve the dynamic destination.",
)
PROCESS_REVIEW = RuleSpec(
    RiskCategory.PROCESS,
    RiskLevel.HIGH,
    SafetyDecision.NEEDS_HUMAN_REVIEW,
    "Use an approved direct API or obtain human approval.",
)
RESOURCE_DENY = RuleSpec(
    RiskCategory.RESOURCE,
    RiskLevel.CRITICAL,
    SafetyDecision.DENY,
    "Replace the unbounded loop with a bounded operation.",
)
RESOURCE_REVIEW = RuleSpec(
    RiskCategory.RESOURCE,
    RiskLevel.MEDIUM,
    SafetyDecision.NEEDS_HUMAN_REVIEW,
    "Reduce the requested resource use.",
)
SECRET_DENY = RuleSpec(
    RiskCategory.SECRET,
    RiskLevel.CRITICAL,
    SafetyDecision.DENY,
    "Remove sensitive values from output, files, and network sinks.",
)
SYNTAX_REVIEW = RuleSpec(
    RiskCategory.POLICY,
    RiskLevel.MEDIUM,
    SafetyDecision.NEEDS_HUMAN_REVIEW,
    "Fix the syntax or review the script manually.",
)


@dataclass(frozen=True)
class _PythonScanContext:
    source: str
    request: ScriptScanRequest
    policy: ToolSafetyPolicy
    sanitizer: SafetySanitizer


class PythonRuleVisitor(ast.NodeVisitor):
    """Single-pass, bounded Python rule visitor."""

    def __init__(self, context: _PythonScanContext):
        self._context = context
        self._aliases: dict[str, str] = {}
        self._constants: dict[str, str] = {}
        self._path_values: dict[str, str | None] = {}
        self._secret_names: set[str] = set()
        self._assigned_names: set[str] = set()
        self._uncertain_names: set[str] = set()
        self.findings: list[SafetyFinding] = []
        self.redacted = False

    def _add(self, rule_id: str, node: ast.AST, spec: RuleSpec) -> None:
        evidence = ast.get_source_segment(self._context.source, node)
        finding, changed = make_finding(rule_id, evidence or node.__class__.__name__, spec, self._context.sanitizer)
        self.findings.append(finding)
        self.redacted = self.redacted or changed

    def _name(self, node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return self._aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            prefix = self._name(node.value)
            return f"{prefix}.{node.attr}" if prefix else node.attr
        if isinstance(node, ast.Call):
            dynamic_name = self._dynamic_attribute_name(node)
            if dynamic_name:
                return dynamic_name
            return self._name(node.func)
        return ""

    def _dynamic_attribute_name(self, node: ast.Call) -> str:
        if self._name(node.func) != "getattr" or len(node.args) < 2:
            return ""
        attr = self._string(node.args[1])
        if attr is None:
            return ""
        prefix = self._name(node.args[0])
        return f"{prefix}.{attr}" if prefix else ""

    def _string(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name):
            return self._constants.get(node.id)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left, right = self._string(node.left), self._string(node.right)
            return left + right if left is not None and right is not None else None
        return None

    def _contains_secret(self, node: ast.AST) -> bool:
        for child in ast.walk(node):
            if isinstance(child, ast.Name):
                if child.id in self._secret_names or _SECRET_NAME_RE.search(child.id):
                    return True
            if isinstance(child, ast.Attribute) and _SECRET_NAME_RE.search(child.attr):
                return True
            if isinstance(child, ast.Constant) and isinstance(child.value, str):
                if _SECRET_NAME_RE.search(child.value) or "PRIVATE KEY-----" in child.value:
                    return True
        return False

    def visit_Import(self, node: ast.Import) -> Any:
        for item in node.names:
            name = item.asname or item.name
            if self._invalidate_binding(name):
                self._aliases[name] = item.name

    def visit_ImportFrom(self, node: ast.ImportFrom) -> Any:
        module = node.module or ""
        for item in node.names:
            name = item.asname or item.name
            if self._invalidate_binding(name):
                self._aliases[name] = f"{module}.{item.name}".strip(".")

    def visit_Assign(self, node: ast.Assign) -> Any:
        for target in node.targets:
            self._bind_target(target, node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> Any:
        self._bind_target(node.target, node.value)
        self.generic_visit(node)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> Any:
        self._bind_target(node.target, node.value)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> Any:
        self._invalidate_target(node.target)
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> Any:
        self._invalidate_target(node.target)
        self.generic_visit(node)

    visit_AsyncFor = visit_For

    def visit_With(self, node: ast.With) -> Any:
        for item in node.items:
            self.visit(item.context_expr)
            if item.optional_vars is not None:
                self._bind_target(item.optional_vars, item.context_expr)
                self._bind_network_context_target(item.optional_vars, item.context_expr)
        for statement in node.body:
            self.visit(statement)

    visit_AsyncWith = visit_With

    def _bind_network_context_target(self, target: ast.AST, context_expr: ast.AST) -> None:
        if not isinstance(target, ast.Name):
            return
        symbolic = self._symbolic_value(context_expr)
        if symbolic.split(".", 1)[0] in _NETWORK_ROOTS:
            self._aliases[target.id] = symbolic

    def visit_FunctionDef(self, node: ast.FunctionDef) -> Any:
        for item in [*node.decorator_list, *node.args.defaults, *node.args.kw_defaults]:
            if item is not None:
                self.visit(item)
        self._invalidate_binding(node.name)
        outer = self._binding_state()
        arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
        arguments.extend(item for item in (node.args.vararg, node.args.kwarg) if item)
        for argument in arguments:
            self._invalidate_binding(argument.arg)
        for statement in node.body:
            self.visit(statement)
        self._restore_binding_state(outer)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node: ast.Lambda) -> Any:
        for item in [*node.args.defaults, *node.args.kw_defaults]:
            if item is not None:
                self.visit(item)
        outer = self._binding_state()
        arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
        arguments.extend(item for item in (node.args.vararg, node.args.kwarg) if item)
        for argument in arguments:
            self._invalidate_binding(argument.arg)
        self.visit(node.body)
        self._restore_binding_state(outer)

    def visit_ListComp(self, node: ast.ListComp) -> Any:
        self._visit_comprehension(node.generators, [node.elt])

    visit_SetComp = visit_ListComp
    visit_GeneratorExp = visit_ListComp

    def visit_DictComp(self, node: ast.DictComp) -> Any:
        self._visit_comprehension(node.generators, [node.key, node.value])

    def _visit_comprehension(self, generators: list[ast.comprehension], outputs: list[ast.AST]) -> None:
        outer = self._binding_state()
        for generator in generators:
            self.visit(generator.iter)
            self._invalidate_target(generator.target)
            for condition in generator.ifs:
                self.visit(condition)
        for output in outputs:
            self.visit(output)
        self._restore_binding_state(outer)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> Any:
        if node.name:
            self._invalidate_binding(node.name)
        self.generic_visit(node)

    def _bind_target(self, target: ast.AST, value_node: ast.AST | None) -> None:
        if not isinstance(target, ast.Name):
            self._invalidate_target(target)
            return
        was_secret = target.id in self._secret_names
        previous_alias = self._aliases.get(target.id)
        can_track = self._invalidate_binding(target.id)
        is_secret = bool(_SECRET_NAME_RE.search(target.id))
        is_secret = is_secret or (value_node is not None and self._contains_secret(value_node))
        if was_secret or is_secret:
            self._secret_names.add(target.id)
        else:
            self._secret_names.discard(target.id)
        if value_node is None:
            return
        value = self._string(value_node)
        symbolic = self._symbolic_value(value_node)
        if self._is_risk_symbol(symbolic):
            self._aliases[target.id] = symbolic
        elif not can_track and self._is_risk_symbol(previous_alias):
            self._aliases[target.id] = previous_alias or ""
        if not can_track:
            return
        if value is not None:
            self._constants[target.id] = value
        if symbolic and target.id not in self._aliases:
            self._aliases[target.id] = symbolic
        if self._is_path_constructor(value_node):
            self._path_values[target.id] = self._path_constructor_value(value_node)

    @staticmethod
    def _is_risk_symbol(symbolic: str | None) -> bool:
        return bool(symbolic and symbolic.split(".", 1)[0] in _RISK_SYMBOL_ROOTS)

    def _invalidate_target(self, target: ast.AST) -> None:
        for item in ast.walk(target):
            if isinstance(item, ast.Name):
                self._invalidate_binding(item.id)

    def _invalidate_binding(self, name: str) -> bool:
        self._constants.pop(name, None)
        self._aliases.pop(name, None)
        self._path_values.pop(name, None)
        if name in self._assigned_names:
            self._uncertain_names.add(name)
        self._assigned_names.add(name)
        return name not in self._uncertain_names

    def _binding_state(self) -> tuple:
        return (
            dict(self._aliases),
            dict(self._constants),
            dict(self._path_values),
            set(self._secret_names),
            set(self._assigned_names),
            set(self._uncertain_names),
        )

    def _restore_binding_state(self, state: tuple) -> None:
        (
            self._aliases,
            self._constants,
            self._path_values,
            self._secret_names,
            self._assigned_names,
            self._uncertain_names,
        ) = state

    def _symbolic_value(self, node: ast.AST) -> str:
        if isinstance(node, (ast.Name, ast.Attribute)):
            return self._name(node)
        if isinstance(node, ast.Call):
            name = self._name(node.func)
            if (name.split(".", 1)[0] in _NETWORK_ROOTS and name.split(".")[-1] in _NETWORK_CLIENT_CONSTRUCTORS):
                return name
        return ""

    def visit_While(self, node: ast.While) -> Any:
        if _static_truthy(node.test):
            self._add("RES001", node, RESOURCE_DENY)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> Any:
        name = self._name(node.func)
        if name in _DELETE_CALLS:
            self._add("FILE001", node, FILE_DELETE)
        if self._is_process_call(name):
            self._add("PROC001", node, PROCESS_REVIEW)
            self._scan_process_payload(node, name)
        self._scan_resource(node, name)
        self._scan_file_access(node, name)
        if self._is_network_call(name):
            self._scan_network(node, name)
        elif self._is_unknown_network_call(name):
            self._add("NET002", node, NETWORK_REVIEW)
        self._scan_secret_sink(node, name)
        self.generic_visit(node)

    def _scan_resource(self, node: ast.Call, name: str) -> None:
        policy = self._context.policy
        if name.endswith(".sleep") and self._number_arg(node) > policy.long_sleep_seconds:
            self._add("RES002", node, RESOURCE_REVIEW)
        if name.endswith("ThreadPoolExecutor") and self._worker_count(node) > policy.max_concurrency:
            self._add("RES002", node, RESOURCE_REVIEW)
        if name == "asyncio.gather" and self._gather_is_large(node):
            self._add("RES002", node, RESOURCE_REVIEW)
        if name.endswith(".write"):
            values = list(node.args) + [item.value for item in node.keywords]
            if any(self._estimated_size(arg) > policy.large_write_bytes for arg in values):
                self._add("RES002", node, RESOURCE_REVIEW)

    def _scan_secret_sink(self, node: ast.Call, name: str) -> None:
        values = list(node.args) + [item.value for item in node.keywords]
        is_output = name in _OUTPUT_CALLS
        is_data_sink = name.endswith((".write", ".send")) or self._is_network_call(name)
        if (is_output or is_data_sink) and any(self._contains_secret(arg) for arg in values):
            self._add("SECRET001", node, SECRET_DENY)

    def _scan_process_payload(self, node: ast.Call, name: str) -> None:
        command = self._process_command(node, name)
        if command is None:
            return
        findings, changed = scan_bash(
            command,
            self._context.policy,
            self._context.sanitizer,
            self._context.request,
        )
        self.findings.extend(findings)
        self.redacted = self.redacted or changed

    def _process_command(self, node: ast.Call, name: str) -> str | None:
        root = name.split(".", 1)[0]
        tail = name.split(".")[-1]
        if root == "subprocess":
            command_node = node.args[0] if node.args else self._keyword_node(node, "args", "cmd")
            executable_node = self._keyword_node(node, "executable")
            if executable_node is not None:
                return self._command_with_executable(executable_node, command_node)
            return self._command(command_node) if command_node is not None else None
        if name == "asyncio.create_subprocess_exec":
            executable_node = node.args[0] if node.args else self._keyword_node(node, "program")
            return self._command_with_argument_nodes(executable_node, node.args[1:])
        if name == "asyncio.create_subprocess_shell":
            command_node = node.args[0] if node.args else self._keyword_node(node, "cmd", "program")
            return self._command(command_node) if command_node is not None else None
        if name in {"anyio.open_process", "anyio.run_process", "trio.run_process"}:
            command_node = node.args[0] if node.args else self._keyword_node(node, "command")
            return self._command(command_node) if command_node is not None else None
        if name in {"os.posix_spawn", "os.posix_spawnp"}:
            executable_node = node.args[0] if node.args else self._keyword_node(node, "path", "file")
            command_node = node.args[1] if len(node.args) > 1 else self._keyword_node(node, "argv", "args")
            return self._command_with_executable(executable_node, command_node)
        if name == "pty.spawn":
            command_node = node.args[0] if node.args else self._keyword_node(node, "argv")
            return self._command(command_node) if command_node is not None else None
        if tail in _PROCESS_CREATION_METHODS:
            return None
        if tail.startswith("execv"):
            executable_node = node.args[0] if node.args else self._keyword_node(node, "path", "file")
            command_node = node.args[1] if len(node.args) > 1 else self._keyword_node(node, "args", "argv")
            return self._command_with_executable(executable_node, command_node)
        if tail.startswith("execl"):
            executable_node = node.args[0] if node.args else self._keyword_node(node, "path", "file")
            argument_nodes = self._strip_exec_env(tail, node.args[1:])
            return self._command_with_argument_nodes(executable_node, argument_nodes[1:])
        if tail.startswith("spawnv"):
            executable_node = node.args[1] if len(node.args) > 1 else self._keyword_node(node, "path", "file")
            command_node = node.args[2] if len(node.args) > 2 else self._keyword_node(node, "args", "argv")
            return self._command_with_executable(executable_node, command_node)
        if tail.startswith("spawnl"):
            executable_node = node.args[1] if len(node.args) > 1 else self._keyword_node(node, "path", "file")
            argument_nodes = self._strip_exec_env(tail, node.args[2:])
            return self._command_with_argument_nodes(executable_node, argument_nodes[1:])
        command_node = node.args[0] if node.args else self._keyword_node(node, "command", "cmd")
        return self._command(command_node) if command_node is not None else None

    @staticmethod
    def _keyword_node(node: ast.Call, *names: str) -> ast.AST | None:
        return next((item.value for item in node.keywords if item.arg in names), None)

    def _command_with_executable(self, executable_node: ast.AST | None, argv_node: ast.AST | None) -> str | None:
        if executable_node is None:
            return self._command(argv_node) if argv_node is not None else None
        if isinstance(argv_node, (ast.List, ast.Tuple)):
            return self._command_with_argument_nodes(executable_node, argv_node.elts[1:])
        executable_value = self._string(executable_node)
        executable = shlex.quote(executable_value) if executable_value is not None else None
        argv = self._command(argv_node) if argv_node is not None else None
        return argv or executable

    def _command_with_argument_nodes(self, executable_node: ast.AST | None,
                                     argument_nodes: list[ast.AST]) -> str | None:
        if executable_node is None:
            return self._command_from_parts(argument_nodes)
        command = self._command_from_parts([executable_node, *argument_nodes])
        return command or self._command(executable_node)

    @staticmethod
    def _strip_exec_env(tail: str, nodes: list[ast.AST]) -> list[ast.AST]:
        return nodes[:-1] if tail.endswith("e") else nodes

    def _command(self, node: ast.AST) -> str | None:
        value = self._string(node)
        if value is not None:
            return value
        if isinstance(node, (ast.List, ast.Tuple)):
            return self._command_from_parts(node.elts)
        return None

    def _command_from_parts(self, nodes: list[ast.AST]) -> str | None:
        if not nodes:
            return None
        parts = [self._string(item) for item in nodes]
        return " ".join(shlex.quote(part) if part is not None else _DYNAMIC_COMMAND_TOKEN for part in parts)

    def _is_network_call(self, name: str) -> bool:
        root = name.split(".", 1)[0]
        tail = name.split(".")[-1]
        return root in _NETWORK_ROOTS and tail in _NETWORK_METHODS

    def _is_unknown_network_call(self, name: str) -> bool:
        root = name.split(".", 1)[0]
        tail = name.split(".")[-1]
        if root not in _NETWORK_ROOTS or tail in _NETWORK_NON_IO_METHODS:
            return False
        return not name.startswith(_NETWORK_HELPER_PREFIXES)

    def _is_process_call(self, name: str) -> bool:
        root = name.split(".", 1)[0]
        tail = name.split(".")[-1]
        return (name in _PROCESS_CALLS or root in _PROCESS_ROOTS
                or (root == "os" and tail.startswith(_OS_PROCESS_PREFIXES))
                or (root in {"concurrent", "multiprocessing"} and tail in _PROCESS_CREATION_METHODS))

    def _scan_network(self, node: ast.Call, name: str) -> None:
        target_node = self._network_target_node(node, name)
        target = self._network_target(target_node) if target_node else None
        if target is None:
            self._add("NET002", node, NETWORK_REVIEW)
            return
        host = urlparse(target).hostname or target.split(":", 1)[0]
        if not host_allowed(host, self._context.policy):
            self._add("NET001", node, NETWORK_DENY)

    @staticmethod
    def _network_target_node(node: ast.Call, name: str) -> ast.AST | None:
        for keyword in node.keywords:
            if keyword.arg == "url":
                return keyword.value
        tail = name.split(".")[-1]
        if tail == "send":
            return None
        index = 1 if tail in {"request", "stream"} else 0
        return node.args[index] if len(node.args) > index else None

    def _network_target(self, node: ast.AST) -> str | None:
        value = self._string(node)
        if value is not None:
            return value
        if isinstance(node, (ast.Tuple, ast.List)) and node.elts:
            return self._string(node.elts[0])
        return None

    def _scan_file_access(self, node: ast.Call, name: str) -> None:
        path_node = None
        recognized = False
        if name in _DIRECT_FILE_CALLS and node.args:
            recognized, path_node = True, node.args[0]
        elif name.split(".")[-1] in _PATH_METHODS:
            recognized, path_node = self._receiver_path(node.func)
        elif name.split(".", 1)[0] in _CONSERVATIVE_FILE_ROOTS and name not in _DELETE_CALLS:
            self._add("FILE003", node, FILE_REVIEW)
            return
        if not recognized:
            return
        path = self._string(path_node) if isinstance(path_node, ast.AST) else path_node
        if path is None:
            self._add("FILE003", node, FILE_REVIEW)
        elif self._is_write_call(node, name) and path_is_system_location(path, self._context.request.cwd):
            self._add("FILE001", node, FILE_DELETE)
        elif path_forbidden(path, self._context.request, self._context.policy):
            self._add("FILE002", node, FILE_DENY)

    def _is_write_call(self, node: ast.Call, name: str) -> bool:
        tail = name.split(".")[-1]
        if tail in {"write_text", "write_bytes"}:
            return True
        if name == "os.open":
            if len(node.args) <= 1:
                return True
            try:
                flag_text = ast.get_source_segment(self._context.source, node.args[1])
            except (IndexError, UnicodeError):
                flag_text = None
            if not flag_text:
                return True
            if re.search(r"O_(?:WRONLY|RDWR|CREAT|TRUNC|APPEND)", flag_text):
                return True
            return not bool(re.fullmatch(r"\s*(?:(?:os\.)?O_RDONLY|0)\s*", flag_text))
        if tail != "open":
            return False
        mode_node = node.args[1] if len(node.args) > 1 else None
        for keyword in node.keywords:
            if keyword.arg == "mode":
                mode_node = keyword.value
        mode = self._string(mode_node) if mode_node else "r"
        return mode is None or any(flag in mode for flag in "wax+")

    def _receiver_path(self, func: ast.AST) -> tuple[bool, ast.AST | str | None]:
        if not isinstance(func, ast.Attribute):
            return False, None
        receiver = func.value
        if self._is_path_constructor(receiver):
            return True, receiver.args[0] if receiver.args else None  # type: ignore[attr-defined]
        if isinstance(receiver, ast.Name) and receiver.id in self._path_values:
            return True, self._path_values[receiver.id]
        return False, None

    def _is_path_constructor(self, node: ast.AST) -> bool:
        return isinstance(node, ast.Call) and self._name(node.func) == "pathlib.Path"

    def _path_constructor_value(self, node: ast.AST) -> str | None:
        if not isinstance(node, ast.Call) or not node.args:
            return None
        return self._string(node.args[0])

    def _estimated_size(self, node: ast.AST) -> int:
        text = self._string(node)
        if text is not None:
            return len(text.encode("utf-8"))
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
            values = (node.left, node.right)
            text_node = next((item for item in values if self._string(item) is not None), None)
            count_node = next(
                (item for item in values if isinstance(item, ast.Constant) and isinstance(item.value, int)), None)
            if text_node is not None and count_node is not None:
                return len((self._string(text_node) or "").encode()) * count_node.value
        return 0

    @staticmethod
    def _number_arg(node: ast.Call) -> float:
        if node.args and isinstance(node.args[0], ast.Constant):
            value = node.args[0].value
            if isinstance(value, (int, float)):
                return float(value)
        return 0.0

    @staticmethod
    def _worker_count(node: ast.Call) -> int:
        if node.args and isinstance(node.args[0], ast.Constant):
            if isinstance(node.args[0].value, int):
                return node.args[0].value
        for keyword in node.keywords:
            if keyword.arg == "max_workers" and isinstance(keyword.value, ast.Constant):
                if isinstance(keyword.value.value, int):
                    return keyword.value.value
        return 0

    def _gather_is_large(self, node: ast.Call) -> bool:
        if any(isinstance(arg, ast.Starred) for arg in node.args):
            return True
        return len(node.args) > self._context.policy.max_concurrency


def scan_python(text: str, request: ScriptScanRequest, policy: ToolSafetyPolicy,
                sanitizer: SafetySanitizer) -> tuple[list[SafetyFinding], bool]:
    """Parse and scan Python source."""
    try:
        tree = ast.parse(text)
    except SyntaxError as error:
        finding, redacted = make_finding("PY001", str(error), SYNTAX_REVIEW, sanitizer)
        return [finding], redacted
    context = _PythonScanContext(text, request, policy, sanitizer)
    visitor = PythonRuleVisitor(context)
    visitor.visit(tree)
    return visitor.findings, visitor.redacted
