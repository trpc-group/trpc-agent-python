# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Built-in static rules for the tool script safety scanner.

The Python path operates on an ``ast`` tree and never imports or executes the
submitted source.  The Bash path first separates unquoted shell control
operators, then tokenizes each command with :mod:`shlex`.  This keeps comments
and quoted examples from being treated as executable commands.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from collections import deque
from dataclasses import dataclass
import fnmatch
from pathlib import PurePosixPath
import posixpath
import re
import shlex
from typing import Any
from typing import Iterable
from typing import Optional
from typing import Protocol
from typing import runtime_checkable
from urllib.parse import urlsplit

from ._models import RiskCategory
from ._models import RiskLevel
from ._models import SafetyDecision
from ._models import SafetyFinding
from ._models import SafetyScanRequest
from ._policy import ToolSafetyPolicy
from ._redaction import contains_private_key
from ._redaction import contains_secret_literal

_SENSITIVE_NAME_RE = re.compile(
    r"(?i)(?:^|_)(?:api_?key|access_?token|auth_?token|credential|password|passwd|private_?key|secret|token)(?:$|_)")
_SHELL_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_SHELL_VARIABLE_RE = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)[^}]*\}|([A-Za-z_][A-Za-z0-9_]*))")
_URL_RE = re.compile(r"(?i)^(?:https?|ftp)://")
_SIZE_RE = re.compile(r"(?i)^([0-9]+(?:\.[0-9]+)?)([kmgt]?i?b?)?$")
_HEREDOC_RE = re.compile(r"<<-?\s*(?:(['\"])([^'\"]+)\1|([A-Za-z_][A-Za-z0-9_]*))")

_PYTHON_CLIENT_TYPES = {
    "aiohttp.ClientSession",
    "httpx.AsyncClient",
    "httpx.Client",
    "requests.Session",
    "requests.sessions.Session",
    "socket.socket",
}
_PYTHON_PATH_TYPES = {
    "Path",
    "pathlib.Path",
    "pathlib.PosixPath",
    "pathlib.WindowsPath",
}
_PYTHON_PATH_RETURNING_METHODS = {
    "absolute",
    "cwd",
    "expanduser",
    "home",
    "joinpath",
    "resolve",
    "with_name",
    "with_stem",
    "with_suffix",
}


@dataclass(frozen=True)
class ShellCommand:
    """A tokenized Bash command and the control operator that follows it."""

    argv: tuple[str, ...]
    redirects: tuple[tuple[str, str], ...]
    assignments: tuple[str, ...]
    operator: Optional[str]
    line_number: int

    @property
    def executable(self) -> str:
        """Return the executable basename, normalized for policy matching."""

        if not self.argv:
            return ""
        if self.argv[0] == ".":
            return "."
        return PurePosixPath(self.argv[0]).name.lower()


@dataclass(frozen=True)
class SafetyRuleContext:
    """Parsed, immutable input shared by built-in and custom safety rules."""

    request: SafetyScanRequest
    python_tree: Optional[ast.AST] = None
    shell_commands: tuple[ShellCommand, ...] = ()
    python_aliases: tuple[tuple[str, str], ...] = ()
    python_instances: tuple[tuple[str, str], ...] = ()
    python_constants: tuple[tuple[str, str], ...] = ()
    shell_executable_text: str = ""

    @property
    def aliases(self) -> dict[str, str]:
        return dict(self.python_aliases)

    @property
    def instances(self) -> dict[str, str]:
        return dict(self.python_instances)

    @property
    def constants(self) -> dict[str, str]:
        return dict(self.python_constants)


@runtime_checkable
class SafetyRule(Protocol):
    """Protocol implemented by pluggable scanner rules."""

    rule_id: str

    def scan(self, context: SafetyRuleContext, policy: ToolSafetyPolicy) -> Iterable[SafetyFinding]:
        """Return zero or more findings without executing the request."""


class BaseSafetyRule:
    """Convenience base class for rules that emit ``SafetyFinding`` objects."""

    rule_id = "UNSPECIFIED"

    @staticmethod
    def _finding(
        *,
        rule_id: str,
        category: RiskCategory,
        risk_level: RiskLevel,
        decision: SafetyDecision,
        evidence: str,
        recommendation: str,
        node: Optional[ast.AST] = None,
        line_number: Optional[int] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> SafetyFinding:
        return SafetyFinding(
            rule_id=rule_id,
            category=category,
            risk_level=risk_level,
            decision=decision,
            evidence=evidence,
            recommendation=recommendation,
            line_number=line_number or getattr(node, "lineno", None),
            column=getattr(node, "col_offset", None),
            metadata=metadata or {},
        )


def _split_shell_line(line: str) -> list[tuple[str, Optional[str]]]:
    """Split one shell line on unquoted control operators."""

    segments: list[tuple[str, Optional[str]]] = []
    buffer: list[str] = []
    quote: Optional[str] = None
    escaped = False
    index = 0
    while index < len(line):
        char = line[index]
        if escaped:
            buffer.append(char)
            escaped = False
            index += 1
            continue
        if char == "\\" and quote != "'":
            buffer.append(char)
            escaped = True
            index += 1
            continue
        if quote:
            buffer.append(char)
            if char == quote:
                quote = None
            index += 1
            continue
        if char in ("'", '"'):
            quote = char
            buffer.append(char)
            index += 1
            continue
        if char == "#" and (index == 0 or line[index - 1].isspace() or line[index - 1] in ";|&"):
            break
        if char in ";|&":
            if char == "&" and ((index + 1 < len(line) and line[index + 1] == ">") or (buffer and buffer[-1] in "<>")):
                buffer.append(char)
                index += 1
                continue
            operator = char
            if index + 1 < len(line) and line[index + 1] == char and char in "|&":
                operator += char
                index += 1
            segments.append(("".join(buffer).strip(), operator))
            buffer = []
            index += 1
            continue
        buffer.append(char)
        index += 1
    if quote:
        raise ValueError("unterminated shell quote")
    if buffer or not segments:
        segments.append(("".join(buffer).strip(), None))
    return segments


def _tokenize_shell_segment(segment: str, operator: Optional[str], line_number: int) -> Optional[ShellCommand]:
    if not segment:
        return None
    lexer = shlex.shlex(segment, posix=True, punctuation_chars="<>")
    lexer.whitespace_split = True
    lexer.commenters = ""
    tokens = list(lexer)
    if not tokens:
        return None

    redirects: list[tuple[str, str]] = []
    command_tokens: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.isdigit() and index + 1 < len(tokens) and set(tokens[index + 1]) <= {"<", ">"}:
            token = tokens[index + 1]
            index += 1
        if token and set(token) <= {"<", ">"}:
            target = tokens[index + 1] if index + 1 < len(tokens) else ""
            if token not in {"<<", "<<<"}:
                redirects.append((token, target))
            index += 2
            continue
        command_tokens.append(token)
        index += 1

    assignments: list[str] = []
    while command_tokens and _SHELL_ASSIGNMENT_RE.match(command_tokens[0]):
        assignments.append(command_tokens.pop(0))
    for _ in range(4):
        if not command_tokens:
            break
        wrapper = PurePosixPath(command_tokens[0]).name.lower()
        if wrapper == "!":
            command_tokens.pop(0)
            continue
        if wrapper in {
                "!", "{", "builtin", "command", "do", "elif", "else", "if", "nohup", "then", "time", "until", "while"
        }:
            command_tokens.pop(0)
            while command_tokens and command_tokens[0].startswith("-"):
                command_tokens.pop(0)
            continue
        if wrapper == "env":
            original_tokens = command_tokens.copy()
            original_assignment_count = len(assignments)
            command_tokens.pop(0)
            while command_tokens and command_tokens[0].startswith("-"):
                command_tokens.pop(0)
            while command_tokens and _SHELL_ASSIGNMENT_RE.match(command_tokens[0]):
                assignments.append(command_tokens.pop(0))
            if not command_tokens:
                command_tokens = original_tokens
                del assignments[original_assignment_count:]
                break
            continue
        break
    if not command_tokens and not redirects and not assignments:
        return None
    return ShellCommand(
        argv=tuple(command_tokens),
        redirects=tuple(redirects),
        assignments=tuple(assignments),
        operator=operator,
        line_number=line_number,
    )


def _strip_heredocs(script: str) -> str:
    """Remove heredoc data while retaining executable substitutions and line numbers."""

    output = []
    terminator: Optional[str] = None
    quoted = False
    for line in script.splitlines(keepends=True):
        if terminator is not None:
            if line.strip() == terminator:
                terminator = None
                quoted = False
                output.append("\n" if line.endswith("\n") else "")
                continue
            substitutions = [] if quoted else re.findall(r"\$\([^)]*\)|`[^`]*`", line)
            retained = " ".join(substitutions)
            output.append(retained + ("\n" if line.endswith("\n") else ""))
            continue
        output.append(line)
        match = _HEREDOC_RE.search(line)
        if match:
            quoted = match.group(1) is not None
            terminator = match.group(2) or match.group(3)
    return "".join(output)


def parse_bash(script: str, *, nested_depth: int = 0) -> tuple[ShellCommand, ...]:
    """Parse Bash into command records using quote-aware splitting and shlex."""

    script = _strip_heredocs(script)
    commands: list[ShellCommand] = []
    for line_number, line in enumerate(script.splitlines(), start=1):
        for segment, operator in _split_shell_line(line):
            command = _tokenize_shell_segment(segment, operator, line_number)
            if command is None:
                continue
            commands.append(command)
            if nested_depth < 2 and command.executable in {"bash", "sh", "zsh"}:
                try:
                    command_index = command.argv.index("-c")
                except ValueError:
                    continue
                if command_index + 1 < len(command.argv):
                    nested = parse_bash(command.argv[command_index + 1], nested_depth=nested_depth + 1)
                    commands.extend(
                        ShellCommand(
                            argv=item.argv,
                            redirects=item.redirects,
                            assignments=item.assignments,
                            operator=item.operator,
                            line_number=line_number,
                        ) for item in nested)
    return tuple(commands)


def shell_executable_text(script: str) -> str:
    """Remove comments and single-quoted data while preserving shell expansions."""

    script = _strip_heredocs(script)
    output: list[str] = []
    quote: Optional[str] = None
    escaped = False
    index = 0
    while index < len(script):
        char = script[index]
        if escaped:
            output.append(char if quote != "'" else " ")
            escaped = False
            index += 1
            continue
        if char == "\\" and quote != "'":
            output.append(char)
            escaped = True
            index += 1
            continue
        if quote == "'":
            if char == "'":
                quote = None
            output.append(" ")
            index += 1
            continue
        if quote == '"':
            if char == '"':
                quote = None
            output.append(char)
            index += 1
            continue
        if char == "'":
            quote = "'"
            output.append(" ")
            index += 1
            continue
        if char == '"':
            quote = '"'
            output.append(char)
            index += 1
            continue
        if char == "#" and (index == 0 or script[index - 1].isspace() or script[index - 1] in ";|&"):
            while index < len(script) and script[index] != "\n":
                output.append(" ")
                index += 1
            continue
        output.append(char)
        index += 1
    return "".join(output)


def _python_import_binding(item: ast.alias) -> tuple[str, str]:
    """Return the name and module that a plain ``import`` actually binds."""

    if item.asname:
        return item.asname, item.name
    root_module = item.name.split(".", 1)[0]
    return root_module, root_module


def collect_python_metadata(tree: ast.AST) -> tuple[dict[str, str], dict[str, str]]:
    """Collect import aliases and well-known client instance assignments."""

    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                name, module = _python_import_binding(item)
                aliases[name] = module
        elif isinstance(node, ast.ImportFrom) and node.module:
            for item in node.names:
                aliases[item.asname or item.name] = f"{node.module}.{item.name}"

    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        if not isinstance(value, (ast.Name, ast.Attribute)):
            continue
        resolved = dotted_name(value, aliases)
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if resolved:
            for target in targets:
                if isinstance(target, ast.Name):
                    aliases[target.id] = resolved

    instances: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if isinstance(value, ast.Call):
                value_name = dotted_name(value.func, aliases)
                if value_name in _PYTHON_CLIENT_TYPES | _PYTHON_PATH_TYPES:
                    for target in targets:
                        if isinstance(target, ast.Name):
                            instances[target.id] = value_name
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if not item.optional_vars or not isinstance(item.optional_vars, ast.Name):
                    continue
                if isinstance(item.context_expr, ast.Call):
                    value_name = dotted_name(item.context_expr.func, aliases)
                    if value_name in {"httpx.Client", "httpx.AsyncClient", "aiohttp.ClientSession"}:
                        instances[item.optional_vars.id] = value_name
    return aliases, instances


def collect_python_constants(tree: ast.AST, aliases: dict[str, str]) -> dict[str, str]:
    """Collect simple string/path assignments without evaluating code."""

    constants: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        value = static_path(node.value, aliases, constants)
        if value is None:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                constants[target.id] = value
    return constants


def dotted_name(node: ast.AST, aliases: Optional[dict[str, str]] = None) -> str:
    """Return a best-effort dotted name without evaluating an AST node."""

    aliases = aliases or {}
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        prefix = dotted_name(node.value, aliases)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    if isinstance(node, ast.Call):
        call_name = dotted_name(node.func, aliases)
        if call_name in {"getattr", "builtins.getattr"} and len(node.args) >= 2:
            attribute = literal_string(node.args[1])
            prefix = dotted_name(node.args[0], aliases)
            if prefix and attribute:
                return f"{prefix}.{attribute}"
        if call_name in {"__import__", "builtins.__import__"} and node.args:
            module = literal_string(node.args[0])
            if module:
                fromlist = node.args[3] if len(node.args) > 3 else next(
                    (keyword.value for keyword in node.keywords if keyword.arg == "fromlist"),
                    None,
                )
                if ((isinstance(fromlist, (ast.List, ast.Set, ast.Tuple)) and fromlist.elts)
                        or (isinstance(fromlist, ast.Constant) and bool(fromlist.value))):
                    return module
                return module.split(".", 1)[0]
        return call_name
    return ""


def _is_pathlib_expression(
    node: ast.AST,
    aliases: dict[str, str],
    instances: dict[str, str],
) -> bool:
    if isinstance(node, ast.Name):
        return (instances.get(node.id) in _PYTHON_PATH_TYPES or aliases.get(node.id, node.id) in _PYTHON_PATH_TYPES)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return _is_pathlib_expression(node.left, aliases, instances)
    if isinstance(node, ast.Attribute):
        return node.attr in {"parent", "parents"} and _is_pathlib_expression(node.value, aliases, instances)
    if isinstance(node, ast.Subscript):
        return _is_pathlib_expression(node.value, aliases, instances)
    if isinstance(node, ast.Call):
        name = dotted_name(node.func, aliases)
        if name in _PYTHON_PATH_TYPES:
            return True
        return (isinstance(node.func, ast.Attribute) and node.func.attr in _PYTHON_PATH_RETURNING_METHODS
                and _is_pathlib_expression(node.func.value, aliases, instances))
    return False


def _node_bindings(node: ast.AST, attribute: str, fallback: dict[str, str]) -> dict[str, str]:
    bindings = getattr(node, attribute, None)
    return bindings if isinstance(bindings, dict) else fallback


def _node_aliases(node: ast.AST, context: SafetyRuleContext) -> dict[str, str]:
    return _node_bindings(node, "_safety_aliases", context.aliases)


def _node_instances(node: ast.AST, context: SafetyRuleContext) -> dict[str, str]:
    return _node_bindings(node, "_safety_instances", context.instances)


def _node_constants(node: ast.AST, context: SafetyRuleContext) -> dict[str, str]:
    return _node_bindings(node, "_safety_constants", context.constants)


def _node_ambiguous(node: ast.AST) -> set[str]:
    bindings = getattr(node, "_safety_ambiguous", None)
    return bindings if isinstance(bindings, set) else set()


def resolved_call_name(call: ast.Call, context: SafetyRuleContext) -> str:
    aliases = _node_aliases(call, context)
    name = dotted_name(call.func, aliases)
    if isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name):
        instance_type = _node_instances(call, context).get(call.func.value.id)
        if instance_type:
            return f"{instance_type}.{call.func.attr}"
    return name


def literal_string(node: Optional[ast.AST]) -> Optional[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)):
        if isinstance(node.value, bytes):
            return node.value.decode("utf-8", errors="replace")
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
                return None
            parts.append(value.value)
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = literal_string(node.left)
        right = literal_string(node.right)
        return left + right if left is not None and right is not None else None
    return None


def literal_number(node: Optional[ast.AST]) -> Optional[float]:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        number = literal_number(node.operand)
        if number is not None:
            return -number if isinstance(node.op, ast.USub) else number
    if isinstance(node, ast.BinOp) and isinstance(node.op,
                                                  (ast.Add, ast.Div, ast.FloorDiv, ast.Mod, ast.Mult, ast.Sub)):
        left = literal_number(node.left)
        right = literal_number(node.right)
        if left is None or right is None:
            return None
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if right == 0:
            return None
        if isinstance(node.op, ast.Div):
            return left / right
        if isinstance(node.op, ast.FloorDiv):
            return left // right
        return left % right
    return None


def literal_truth(node: ast.AST) -> Optional[bool]:
    if isinstance(node, ast.Constant):
        return bool(node.value)
    number = literal_number(node)
    return bool(number) if number is not None else None


def static_path(
    node: Optional[ast.AST],
    aliases: Optional[dict[str, str]] = None,
    constants: Optional[dict[str, str]] = None,
) -> Optional[str]:
    constants = constants or {}
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    if isinstance(node, ast.Attribute) and node.attr == "parent":
        base = static_path(node.value, aliases, constants)
        return posixpath.dirname(base) if base is not None else None
    if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute) and node.value.attr == "parents"):
        base = static_path(node.value.value, aliases, constants)
        parent_index = literal_number(node.slice)
        if base is not None and parent_index is not None and parent_index.is_integer() and parent_index >= 0:
            for _ in range(int(parent_index) + 1):
                base = posixpath.dirname(base)
            return base
    value = literal_string(node)
    if value is not None:
        return value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left = static_path(node.left, aliases, constants)
        right = static_path(node.right, aliases, constants)
        if left is not None and right is not None:
            return posixpath.join(left, right)
        if right is not None and right.startswith("/"):
            return right
    if isinstance(node, ast.Call):
        name = dotted_name(node.func, aliases)
        if name in {"glob.glob", "glob.iglob"}:
            pathname_node = node.args[0] if node.args else next(
                (keyword.value for keyword in node.keywords if keyword.arg == "pathname"),
                None,
            )
            root_node = next(
                (keyword.value for keyword in node.keywords if keyword.arg == "root_dir"),
                None,
            )
            pathname = static_path(pathname_node, aliases, constants)
            root = static_path(root_node, aliases, constants)
            if pathname is not None and root is not None:
                return posixpath.join(root, pathname)
            if root_node is not None:
                return None
            return pathname
        if name in {
                "Path",
                "pathlib.Path",
                "PurePath",
                "pathlib.PurePath",
                "PurePosixPath",
                "pathlib.PurePosixPath",
        } and node.args:
            parts = [static_path(arg, aliases, constants) for arg in node.args]
            if all(part is not None for part in parts):
                return posixpath.join(*(str(part) for part in parts))
        if name in {"Path.home", "pathlib.Path.home"}:
            return "~"
        if name in {"os.path.expanduser", "os.path.abspath", "os.path.normpath", "os.path.realpath"} and node.args:
            return static_path(node.args[0], aliases, constants)
        if name.endswith((".absolute", ".expanduser", ".resolve")) and isinstance(node.func, ast.Attribute):
            return static_path(node.func.value, aliases, constants)
        if name.endswith(".joinpath"):
            base = static_path(node.func.value, aliases, constants) if isinstance(node.func, ast.Attribute) else None
            parts = [static_path(arg, aliases, constants) for arg in node.args]
            if base is not None and all(part is not None for part in parts):
                return posixpath.join(base, *(str(part) for part in parts))
            if all(part is not None for part in parts):
                for index in range(len(parts) - 1, -1, -1):
                    if str(parts[index]).startswith("/"):
                        return posixpath.join(*(str(part) for part in parts[index:]))
        if name.endswith((".with_name", ".with_stem", ".with_suffix")) and isinstance(node.func, ast.Attribute):
            base = static_path(node.func.value, aliases, constants)
            replacement = static_path(node.args[0], aliases, constants) if node.args else None
            if base is not None and replacement is not None:
                try:
                    path = PurePosixPath(base)
                    if node.func.attr == "with_name":
                        return str(path.with_name(replacement))
                    if node.func.attr == "with_stem":
                        return str(path.with_stem(replacement))
                    return str(path.with_suffix(replacement))
                except ValueError:
                    return None
        if name.endswith((".glob", ".rglob")) and isinstance(node.func, ast.Attribute):
            base = static_path(node.func.value, aliases, constants)
            pattern_node = node.args[0] if node.args else next(
                (keyword.value for keyword in node.keywords if keyword.arg == "pattern"),
                None,
            )
            pattern = static_path(pattern_node, aliases, constants)
            if base is not None and pattern is not None:
                return posixpath.join(base, pattern)
        if name == "os.path.join" and node.args:
            parts = [static_path(arg, aliases, constants) for arg in node.args]
            if all(part is not None for part in parts):
                return posixpath.join(*(str(part) for part in parts))
    return None


class _PythonBindingAnnotator(ast.NodeVisitor):
    """Attach the bindings visible at each AST node without executing code."""

    def __init__(self) -> None:
        self.aliases: dict[str, str] = {}
        self.instances: dict[str, str] = {}
        self.constants: dict[str, str] = {}
        self.ambiguous: set[str] = set()
        self._class_outer_states: list[tuple[dict[str, str], dict[str, str], dict[str, str], set[str]]] = []

    def _annotate(self, node: ast.AST) -> None:
        names = {
            child.id
            for child in ast.walk(node) if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load)
        }
        node._safety_aliases = {
            name: self.aliases[name]
            for name in names  # type: ignore[attr-defined]
            if name in self.aliases
        }
        node._safety_instances = {
            name: self.instances[name]
            for name in names  # type: ignore[attr-defined]
            if name in self.instances
        }
        node._safety_constants = {
            name: self.constants[name]
            for name in names  # type: ignore[attr-defined]
            if name in self.constants
        }
        node._safety_ambiguous = names & self.ambiguous  # type: ignore[attr-defined]

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        self._annotate(node)
        for argument in [*node.args, *(keyword.value for keyword in node.keywords)]:
            self._annotate(argument)
        if isinstance(node.func, ast.Attribute):
            self._annotate(node.func.value)
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:  # noqa: N802
        self._annotate(node)
        self.generic_visit(node)

    def _invalidate(self, target: ast.AST) -> None:
        if isinstance(target, ast.Name):
            self.aliases.pop(target.id, None)
            self.instances.pop(target.id, None)
            self.constants.pop(target.id, None)
            self.ambiguous.discard(target.id)
            return
        if isinstance(target, (ast.List, ast.Tuple)):
            for element in target.elts:
                self._invalidate(element)

    def _bind(self, target: ast.AST, value: ast.AST) -> None:
        self._invalidate(target)
        if not isinstance(target, ast.Name):
            return

        if (isinstance(value, ast.Attribute) and _is_pathlib_expression(value.value, self.aliases, self.instances)):
            self.aliases[target.id] = f"pathlib.Path.{value.attr}"
        elif isinstance(value, (ast.Name, ast.Attribute)):
            alias = dotted_name(value, self.aliases)
            if alias:
                self.aliases[target.id] = alias
        elif isinstance(value, ast.Call):
            dynamic_alias = dotted_name(value, self.aliases)
            function_name = dotted_name(value.func, self.aliases)
            if function_name in {"getattr", "builtins.getattr", "__import__", "builtins.__import__"}:
                self.aliases[target.id] = dynamic_alias
            if function_name in _PYTHON_CLIENT_TYPES | _PYTHON_PATH_TYPES:
                self.instances[target.id] = function_name

        if _is_pathlib_expression(value, self.aliases, self.instances):
            self.instances[target.id] = "pathlib.Path"

        path = static_path(value, self.aliases, self.constants)
        if path is not None:
            self.constants[target.id] = path

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        for item in node.names:
            name, module = _python_import_binding(item)
            self._invalidate(ast.Name(id=name))
            self.aliases[name] = module

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        if node.module:
            for item in node.names:
                name = item.asname or item.name
                self._invalidate(ast.Name(id=name))
                self.aliases[name] = f"{node.module}.{item.name}"

    def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
        self.visit(node.value)
        for target in node.targets:
            self._bind(target, node.value)
            self.visit(target)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:  # noqa: N802
        self.visit(node.annotation)
        if node.value is not None:
            self.visit(node.value)
            self._bind(node.target, node.value)
        else:
            self._invalidate(node.target)
        self.visit(node.target)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:  # noqa: N802
        self.visit(node.value)
        self._bind(node.target, node.value)
        self.visit(node.target)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:  # noqa: N802
        self.visit(node.target)
        self.visit(node.value)
        self._invalidate(node.target)

    def visit_Delete(self, node: ast.Delete) -> None:  # noqa: N802
        for target in node.targets:
            self._invalidate(target)
            self.visit(target)

    def _state(self) -> tuple[dict[str, str], dict[str, str], dict[str, str], set[str]]:
        return self.aliases.copy(), self.instances.copy(), self.constants.copy(), self.ambiguous.copy()

    def _restore(self, state: tuple[dict[str, str], dict[str, str], dict[str, str], set[str]]) -> None:
        self.aliases, self.instances, self.constants, self.ambiguous = state

    def _visit_branch(self, statements: list[ast.stmt],
                      state) -> tuple[dict[str, str], dict[str, str], dict[str, str], set[str]]:
        self._restore(tuple(item.copy() for item in state))
        for statement in statements:
            self.visit(statement)
        return self._state()

    @staticmethod
    def _merge_state(left, right):
        merged_maps = []
        ambiguous = left[3] | right[3]
        for left_map, right_map in zip(left[:3], right[:3]):
            merged = {name: value for name, value in left_map.items() if name in right_map and right_map[name] == value}
            ambiguous.update((set(left_map) | set(right_map)) - set(merged))
            merged_maps.append(merged)
        return *merged_maps, ambiguous

    def visit_If(self, node: ast.If) -> None:  # noqa: N802
        self.visit(node.test)
        base = self._state()
        truth = literal_truth(node.test)
        if truth is True:
            body = self._visit_branch(node.body, base)
            self._visit_branch(node.orelse, base)
            self._restore(body)
            return
        if truth is False:
            self._visit_branch(node.body, base)
            self._restore(self._visit_branch(node.orelse, base))
            return
        body = self._visit_branch(node.body, base)
        alternative = self._visit_branch(node.orelse, base) if node.orelse else base
        self._restore(self._merge_state(body, alternative))

    def _visit_loop(self, node: ast.For | ast.AsyncFor, never_runs: bool) -> None:
        self.visit(node.iter)
        base = self._state()
        if never_runs:
            self._visit_branch(node.body, base)
            self._restore(self._visit_branch(node.orelse, base))
            return
        loop_state = tuple(item.copy() for item in base)
        self._restore(loop_state)
        self._invalidate(node.target)
        target_names = {child.id for child in ast.walk(node.target) if isinstance(child, ast.Name)}
        self.ambiguous.update(target_names)
        for statement in node.body:
            self.visit(statement)
        body = self._state()
        alternative = self._visit_branch(node.orelse, base) if node.orelse else base
        self._restore(self._merge_state(body, alternative))

    def visit_For(self, node: ast.For) -> None:  # noqa: N802
        never_runs = isinstance(node.iter, (ast.List, ast.Set, ast.Tuple)) and not node.iter.elts
        self._visit_loop(node, never_runs)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:  # noqa: N802
        self._visit_loop(node, False)

    def visit_While(self, node: ast.While) -> None:  # noqa: N802
        self.visit(node.test)
        base = self._state()
        truth = literal_truth(node.test)
        if truth is False:
            self._visit_branch(node.body, base)
            self._restore(self._visit_branch(node.orelse, base))
            return
        body = self._visit_branch(node.body, base)
        alternative = self._visit_branch(node.orelse, base) if node.orelse else base
        self._restore(self._merge_state(body, alternative))

    def _visit_try(self, node: ast.Try | ast.TryStar) -> None:
        base = self._state()
        body = self._visit_branch(node.body, base)
        normal = self._visit_branch(node.orelse, body) if node.orelse else body
        handler_base = self._merge_state(base, body)
        outcomes = [normal]
        for handler in node.handlers:
            self._restore(tuple(item.copy() for item in handler_base))
            if handler.type is not None:
                self.visit(handler.type)
            if handler.name:
                self._invalidate(ast.Name(id=handler.name))
            for statement in handler.body:
                self.visit(statement)
            if handler.name:
                self._invalidate(ast.Name(id=handler.name))
            outcomes.append(self._state())
        merged = outcomes[0]
        for outcome in outcomes[1:]:
            merged = self._merge_state(merged, outcome)
        self._restore(merged)
        for statement in node.finalbody:
            self.visit(statement)

    def visit_Try(self, node: ast.Try) -> None:  # noqa: N802
        self._visit_try(node)

    def visit_TryStar(self, node: ast.TryStar) -> None:  # noqa: N802
        self._visit_try(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for default in [*node.args.defaults, *(item for item in node.args.kw_defaults if item is not None)]:
            self.visit(default)

        definition_state = self._state()
        arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
        if node.args.vararg is not None:
            arguments.append(node.args.vararg)
        if node.args.kwarg is not None:
            arguments.append(node.args.kwarg)
        path_arguments = set()
        for argument in arguments:
            annotation_name = dotted_name(argument.annotation, self.aliases) if argument.annotation is not None else ""
            annotation_name = literal_string(argument.annotation) or annotation_name
            if annotation_name in _PYTHON_PATH_TYPES:
                path_arguments.add(argument.arg)

        class_outer = self._class_outer_states.pop() if self._class_outer_states else None
        if class_outer is not None:
            self._restore(tuple(item.copy() for item in class_outer))
        for argument in arguments:
            self._invalidate(ast.Name(id=argument.arg))
            if argument.arg in path_arguments:
                self.instances[argument.arg] = "pathlib.Path"
        for statement in node.body:
            self.visit(statement)
        if class_outer is not None:
            self._class_outer_states.append(class_outer)
        self._restore(definition_state)
        self._invalidate(ast.Name(id=node.name))

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self._visit_function(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        for decorator in node.decorator_list:
            self.visit(decorator)
        for base in node.bases:
            self.visit(base)
        for keyword in node.keywords:
            self.visit(keyword.value)
        for type_parameter in getattr(node, "type_params", []):
            self.visit(type_parameter)

        outer = self._state()
        method_outer = self._class_outer_states[-1] if self._class_outer_states else outer
        self._class_outer_states.append(method_outer)
        self._restore(tuple(item.copy() for item in outer))
        for statement in node.body:
            self.visit(statement)
        self._class_outer_states.pop()
        self._restore(outer)
        self._invalidate(ast.Name(id=node.name))


def annotate_python_bindings(tree: ast.AST) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """Annotate nodes and return final straight-line bindings for custom rules."""

    annotator = _PythonBindingAnnotator()
    annotator.visit(tree)
    return annotator.aliases, annotator.instances, annotator.constants


def literal_sequence(node: Optional[ast.AST]) -> Optional[list[str]]:
    if isinstance(node, (ast.List, ast.Tuple)):
        values = [literal_string(item) for item in node.elts]
        if all(value is not None for value in values):
            return [str(value) for value in values]
    return None


def nested_find_commands(tokens: list[str]) -> Iterable[list[str]]:
    """Yield executable argv embedded in find -exec/-execdir actions."""

    for index, token in enumerate(tokens):
        if token not in {"-exec", "-execdir"} or index + 1 >= len(tokens):
            continue
        nested = []
        for item in tokens[index + 1:]:
            if item in {"+", ";"}:
                break
            if item != "{}":
                nested.append(item)
        if nested:
            yield nested


def python_command_tokens(call: ast.Call, context: SafetyRuleContext) -> Optional[list[str]]:
    name = resolved_call_name(call, context)
    if name == "asyncio.create_subprocess_exec":
        arguments = [literal_string(argument) for argument in call.args]
        return [str(argument) for argument in arguments] if arguments and all(arguments) else None
    if name not in {
            "asyncio.create_subprocess_shell",
            "os.system",
            "os.popen",
            "subprocess.call",
            "subprocess.check_call",
            "subprocess.check_output",
            "subprocess.getoutput",
            "subprocess.getstatusoutput",
            "subprocess.Popen",
            "subprocess.run",
    } or not call.args:
        return None
    sequence = literal_sequence(call.args[0])
    if sequence is not None:
        return sequence
    command = literal_string(call.args[0])
    if command is None:
        return None
    try:
        return shlex.split(command, posix=True)
    except ValueError:
        return [command]


def _keyword_bool(call: ast.Call, keyword_name: str) -> Optional[bool]:
    for keyword in call.keywords:
        if keyword.arg == keyword_name and isinstance(keyword.value, ast.Constant):
            if isinstance(keyword.value.value, bool):
                return keyword.value.value
    return None


def _hostname(target: str) -> tuple[Optional[str], bool]:
    """Return ``(hostname, is_dynamic)`` for URL/host-like input."""

    candidate = target.strip()
    if not candidate:
        return None, True
    if "\\" in candidate or any(ord(char) < 32 for char in candidate):
        return None, True
    if any(marker in candidate for marker in ("${", "$(`", "$(", "{{", "{%")) or candidate.startswith("$"):
        return None, True
    if "@" in candidate and not _URL_RE.match(candidate):
        candidate = candidate.rsplit("@", 1)[-1]
    if ":" in candidate and not _URL_RE.match(candidate) and "/" not in candidate:
        candidate = candidate.split(":", 1)[0]
    parsed = urlsplit(candidate if "://" in candidate else f"//{candidate}")
    if parsed.username is not None or parsed.password is not None:
        return None, True
    return (parsed.hostname.lower().rstrip(".") if parsed.hostname else None), False


def _network_targets(tokens: list[str]) -> tuple[list[str], bool]:
    if not tokens:
        return [], False
    executable = PurePosixPath(tokens[0]).name.lower()
    targets: list[str] = []
    dynamic = False
    if executable in {"curl", "wget"}:
        skip_next = False
        value_options = {
            "-A",
            "-d",
            "-e",
            "-H",
            "-o",
            "-u",
            "-X",
            "--data",
            "--header",
            "--output",
            "--request",
            "--user",
        }
        for index, token in enumerate(tokens[1:]):
            if skip_next:
                skip_next = False
                continue
            if token in {"--url"} and index + 2 <= len(tokens):
                skip_next = True
                targets.append(tokens[index + 2])
            elif token in value_options:
                skip_next = True
            elif token.startswith("-"):
                continue
            elif _URL_RE.match(token) or "$" in token:
                targets.append(token)
        dynamic = not targets
    elif executable in {"nc", "netcat", "ssh", "telnet"}:
        values = [token for token in tokens[1:] if not token.startswith("-")]
        if values:
            targets.append(values[0])
        else:
            dynamic = True
    elif executable in {"scp", "rsync"}:
        values = [token for token in tokens[1:] if not token.startswith("-")]
        for value in values:
            if ":" in value or "@" in value or "$" in value:
                targets.append(value.split(":", 1)[0])
        dynamic = not targets
    return targets, dynamic


def _is_sensitive_name(name: str, environment_keys: set[str]) -> bool:
    snake_case = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name.strip()).lower()
    normalized = snake_case.upper()
    return bool(_SENSITIVE_NAME_RE.search(snake_case)) or any(
        normalized == re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key.strip()).upper()
        and _SENSITIVE_NAME_RE.search(re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key.strip()).lower())
        for key in environment_keys)


def _expression_contains_name(node: ast.AST, names: set[str]) -> bool:
    return any(isinstance(child, ast.Name) and child.id in names for child in ast.walk(node))


def _expression_has_secret_literal(node: ast.AST) -> bool:
    return any(
        isinstance(child, ast.Constant) and isinstance(child.value, str) and contains_secret_literal(child.value)
        for child in ast.walk(node))


def _size_value(value: str) -> Optional[int]:
    match = _SIZE_RE.match(value.strip())
    if not match:
        return None
    number = float(match.group(1))
    suffix = (match.group(2) or "").lower()
    multipliers = {
        "": 1,
        "b": 1,
        "k": 1000,
        "kb": 1000,
        "ki": 1024,
        "kib": 1024,
        "m": 1000**2,
        "mb": 1000**2,
        "mi": 1024**2,
        "mib": 1024**2,
        "g": 1000**3,
        "gb": 1000**3,
        "gi": 1024**3,
        "gib": 1024**3,
        "t": 1000**4,
        "tb": 1000**4,
        "ti": 1024**4,
        "tib": 1024**4,
    }
    multiplier = multipliers.get(suffix)
    return int(number * multiplier) if multiplier is not None else None


class PolicyLimitsRule(BaseSafetyRule):
    """Check request metadata and configured execution ceilings."""

    rule_id = "POLICY-LIMITS"

    def scan(self, context: SafetyRuleContext, policy: ToolSafetyPolicy) -> Iterable[SafetyFinding]:
        request = context.request
        if len(request.script.encode("utf-8", errors="replace")) > policy.max_script_bytes:
            yield self._finding(
                rule_id="POLICY-SCRIPT-SIZE",
                category=RiskCategory.POLICY_VIOLATION,
                risk_level=RiskLevel.HIGH,
                decision=SafetyDecision.DENY,
                evidence=f"script size exceeds the configured {policy.max_script_bytes}-byte limit",
                recommendation="Reduce the script size or explicitly raise the reviewed policy limit.",
            )
        if request.timeout_seconds is not None and (request.timeout_seconds == 0
                                                    or request.timeout_seconds > policy.max_timeout_seconds):
            timeout_description = ("unbounded" if request.timeout_seconds == 0 else f"{request.timeout_seconds:g}s")
            yield self._finding(
                rule_id="POLICY-TIMEOUT",
                category=RiskCategory.POLICY_VIOLATION,
                risk_level=RiskLevel.HIGH,
                decision=SafetyDecision.DENY,
                evidence=(f"requested timeout {timeout_description} exceeds the configured "
                          f"{policy.max_timeout_seconds:g}s maximum"),
                recommendation="Use a bounded timeout within policy.",
            )
        if request.output_limit_bytes is not None and request.output_limit_bytes > policy.max_output_bytes:
            yield self._finding(
                rule_id="POLICY-OUTPUT-LIMIT",
                category=RiskCategory.POLICY_VIOLATION,
                risk_level=RiskLevel.HIGH,
                decision=SafetyDecision.DENY,
                evidence=(f"requested output limit {request.output_limit_bytes} exceeds the configured "
                          f"{policy.max_output_bytes}-byte maximum"),
                recommendation="Lower the output limit and write large results to a managed artifact.",
            )
        if request.cwd and policy.is_path_denied(request.cwd):
            yield self._finding(
                rule_id="POLICY-CWD",
                category=RiskCategory.POLICY_VIOLATION,
                risk_level=RiskLevel.CRITICAL,
                decision=SafetyDecision.DENY,
                evidence="working directory resolves to a policy-denied path",
                recommendation="Run inside an isolated workspace directory.",
            )
        if context.python_tree is not None:
            for node in ast.walk(context.python_tree):
                if isinstance(node, ast.Call) and _node_ambiguous(node):
                    yield self._finding(
                        rule_id="POLICY-DYNAMIC-BINDING",
                        category=RiskCategory.POLICY_VIOLATION,
                        risk_level=RiskLevel.MEDIUM,
                        decision=SafetyDecision.NEEDS_HUMAN_REVIEW,
                        evidence="call depends on a binding with multiple control-flow values",
                        recommendation="Use one explicit callable and literal arguments before execution.",
                        node=node,
                    )


class DangerousFileRule(BaseSafetyRule):
    """Detect recursive deletion and access to policy-denied paths."""

    rule_id = "FILE"
    _PATH_CALLS = {
        "builtins.open",
        "glob.glob",
        "glob.iglob",
        "io.open",
        "open",
        "os.access",
        "os.chdir",
        "os.chmod",
        "os.chown",
        "os.fwalk",
        "os.link",
        "os.listdir",
        "os.lstat",
        "os.makedirs",
        "os.mkfifo",
        "os.mkdir",
        "os.mknod",
        "os.open",
        "os.path.exists",
        "os.path.getatime",
        "os.path.getctime",
        "os.path.getmtime",
        "os.path.getsize",
        "os.path.isdir",
        "os.path.isfile",
        "os.path.islink",
        "os.path.ismount",
        "os.path.lexists",
        "os.path.samefile",
        "os.readlink",
        "os.remove",
        "os.rename",
        "os.replace",
        "os.rmdir",
        "os.scandir",
        "os.stat",
        "os.symlink",
        "os.truncate",
        "os.unlink",
        "os.utime",
        "os.walk",
        "shutil.copy",
        "shutil.copy2",
        "shutil.copyfile",
        "shutil.copymode",
        "shutil.copystat",
        "shutil.copytree",
        "shutil.move",
        "shutil.rmtree",
    }
    _PATH_METHODS = {
        "chmod",
        "hardlink_to",
        "mkdir",
        "open",
        "read_bytes",
        "read_text",
        "rename",
        "replace",
        "rmdir",
        "samefile",
        "symlink_to",
        "touch",
        "unlink",
        "write_bytes",
        "write_text",
    }
    _PATHLIB_INSPECTION_METHODS = {
        "exists",
        "glob",
        "group",
        "is_block_device",
        "is_char_device",
        "is_dir",
        "is_fifo",
        "is_file",
        "is_junction",
        "is_mount",
        "is_socket",
        "is_symlink",
        "iterdir",
        "lstat",
        "owner",
        "readlink",
        "rglob",
        "stat",
        "walk",
    }
    _BASH_PATH_COMMANDS = {
        ".",
        "cat",
        "chmod",
        "chown",
        "cp",
        "find",
        "head",
        "less",
        "ln",
        "more",
        "mv",
        "rm",
        "rsync",
        "scp",
        "source",
        "tail",
        "tar",
    }
    _BASH_PROGRAM_COMMANDS = {"awk", "grep", "jq", "sed"}
    _BASH_POSITIONAL_PATH_COMMANDS = {"cut", "ls", "sort", "uniq", "wc"}
    _MULTI_PATH_CALLS = {
        "os.link",
        "os.rename",
        "os.replace",
        "os.path.samefile",
        "os.symlink",
        "shutil.copy",
        "shutil.copy2",
        "shutil.copyfile",
        "shutil.copymode",
        "shutil.copystat",
        "shutil.copytree",
        "shutil.move",
    }

    @staticmethod
    def _dangerous_rm(tokens: list[str]) -> bool:
        if not tokens or PurePosixPath(tokens[0]).name.lower() != "rm":
            return False
        flags = [token for token in tokens[1:] if token.startswith("-")]
        return any(flag.lower() == "--recursive" or (not flag.startswith("--") and any(char in {"r", "R"}
                                                                                       for char in flag[1:]))
                   for flag in flags)

    @staticmethod
    def _token_paths(token: str) -> Iterable[str]:
        candidates = [token]
        if token.startswith("-") and "=" in token:
            candidates.append(token.split("=", 1)[1])
        for candidate in candidates:
            yield candidate

    @staticmethod
    def _option_value(
        tokens: list[str],
        index: int,
        options: Iterable[str],
    ) -> Optional[tuple[str, int]]:
        """Return an option argument and the next index, including compact forms."""

        token = tokens[index]
        for option in options:
            if token == option:
                if index + 1 < len(tokens):
                    return tokens[index + 1], index + 2
                return "", index + 1
            if option.startswith("--") and token.startswith(f"{option}="):
                return token[len(option) + 1:], index + 1
            if (len(option) == 2 and not token.startswith("--") and token.startswith(option)
                    and len(token) > len(option)):
                return token[len(option):], index + 1
        return None

    @staticmethod
    def _clustered_short_option_value(
        tokens: list[str],
        index: int,
        option: str,
        prefix_flags: str,
    ) -> Optional[tuple[str, int]]:
        token = tokens[index]
        if not token.startswith("-") or token.startswith("--"):
            return None
        body = token[1:]
        option_index = body.find(option)
        if option_index <= 0 or any(flag not in prefix_flags for flag in body[:option_index]):
            return None
        attached = body[option_index + 1:]
        if attached:
            return attached, index + 1
        if index + 1 < len(tokens):
            return tokens[index + 1], index + 2
        return "", index + 1

    @classmethod
    def _program_command_file_paths(cls, tokens: list[str], executable: str) -> list[str]:
        source_path_options = {
            "awk": ("-f", "--file"),
            "grep": ("-f", "--file"),
            "jq": ("-f", "--from-file"),
            "sed": ("-f", "--file"),
        }
        inline_program_options = {
            "awk": ("-e", "--source"),
            "grep": ("-e", "--regexp"),
            "jq": (),
            "sed": ("-e", "--expression"),
        }
        value_options = {
            "awk": ("-F", "-v", "--assign", "--field-separator"),
            "grep": (
                "-A",
                "-B",
                "-C",
                "-D",
                "-d",
                "-m",
                "--after-context",
                "--before-context",
                "--binary-files",
                "--context",
                "--devices",
                "--directories",
                "--exclude",
                "--exclude-dir",
                "--include",
                "--label",
                "--max-count",
            ),
            "jq": ("--indent", ),
            "sed": ("-l", "--line-length"),
        }
        extra_path_options = {
            "awk": (),
            "grep": ("--exclude-from", ),
            "jq": ("-L", ),
            "sed": (),
        }
        source_prefix_flags = {
            "awk": "n",
            "grep": "EFGHhIiLlnoqRrsvVwxyZa",
            "jq": "CcMnrRsSej",
            "sed": "Enrsuz",
        }

        paths: list[str] = []
        operands: list[str] = []
        program_supplied = False
        jq_data_arguments = False
        index = 1
        while index < len(tokens):
            token = tokens[index]
            if token == "--":
                operands.extend(tokens[index + 1:])
                break
            if executable == "jq" and token in {"--arg", "--argjson"}:
                index = min(index + 3, len(tokens))
                continue
            if executable == "jq" and token in {"--argfile", "--rawfile", "--slurpfile"}:
                if index + 2 < len(tokens):
                    paths.append(tokens[index + 2])
                index = min(index + 3, len(tokens))
                continue
            if executable == "jq" and token in {"--args", "--jsonargs"}:
                jq_data_arguments = True
                index += 1
                continue

            matched = cls._clustered_short_option_value(tokens, index, "f", source_prefix_flags[executable])
            if matched is None:
                matched = cls._option_value(tokens, index, source_path_options[executable])
            if matched is not None:
                value, index = matched
                if value:
                    paths.append(value)
                program_supplied = True
                continue
            if executable != "jq":
                matched = cls._clustered_short_option_value(tokens, index, "e", source_prefix_flags[executable])
                if matched is not None:
                    _, index = matched
                    program_supplied = True
                    continue
            matched = cls._option_value(tokens, index, inline_program_options[executable])
            if matched is not None:
                _, index = matched
                program_supplied = True
                continue
            matched = cls._option_value(tokens, index, extra_path_options[executable])
            if matched is not None:
                value, index = matched
                if value:
                    paths.append(value)
                continue
            matched = cls._option_value(tokens, index, value_options[executable])
            if matched is not None:
                _, index = matched
                continue
            if token.startswith("-") and token != "-":
                index += 1
                continue
            operands.append(token)
            index += 1

        if not program_supplied and operands:
            operands = operands[1:]
        if executable == "awk":
            operands = [operand for operand in operands if not _SHELL_ASSIGNMENT_RE.match(operand)]
        if not (executable == "jq" and jq_data_arguments):
            paths.extend(operands)
        return paths

    @classmethod
    def _positional_command_file_paths(cls, tokens: list[str], executable: str) -> list[str]:
        value_options = {
            "cut": (
                "-b",
                "-c",
                "-d",
                "-f",
                "--bytes",
                "--characters",
                "--delimiter",
                "--fields",
                "--output-delimiter",
            ),
            "ls": (
                "-I",
                "-T",
                "-w",
                "--block-size",
                "--format",
                "--hide",
                "--ignore",
                "--indicator-style",
                "--quoting-style",
                "--sort",
                "--tabsize",
                "--time",
                "--time-style",
                "--width",
            ),
            "sort": (
                "-k",
                "-S",
                "-t",
                "--batch-size",
                "--buffer-size",
                "--compress-program",
                "--field-separator",
                "--key",
                "--parallel",
            ),
            "uniq": ("-f", "-s", "-w", "--check-chars", "--skip-chars", "--skip-fields"),
            "wc": (),
        }
        path_options = {
            "cut": (),
            "ls": (),
            "sort": (
                "-o",
                "-T",
                "--files0-from",
                "--out",
                "--output",
                "--random-source",
                "--temporary-directory",
            ),
            "uniq": (),
            "wc": ("--files0-from", ),
        }

        paths: list[str] = []
        index = 1
        while index < len(tokens):
            token = tokens[index]
            if token == "--":
                paths.extend(tokens[index + 1:])
                break
            if executable == "sort":
                clustered_path = cls._clustered_short_option_value(tokens, index, "o", "bdfghiMmnRrsuVz")
                if clustered_path is None:
                    clustered_path = cls._clustered_short_option_value(tokens, index, "T", "bdfghiMmnRrsuVz")
                if clustered_path is not None:
                    value, index = clustered_path
                    if value:
                        paths.append(value)
                    continue
            matched = cls._option_value(tokens, index, path_options[executable])
            if matched is not None:
                value, index = matched
                if value:
                    paths.append(value)
                continue
            matched = cls._option_value(tokens, index, value_options[executable])
            if matched is not None:
                _, index = matched
                continue
            if token.startswith("-") and token != "-":
                index += 1
                continue
            paths.append(token)
            index += 1
        return paths

    @classmethod
    def _bash_file_paths(cls, tokens: list[str]) -> list[str]:
        if not tokens:
            return []
        executable = "." if tokens[0] == "." else PurePosixPath(tokens[0]).name.lower()
        if executable in cls._BASH_PROGRAM_COMMANDS:
            paths = cls._program_command_file_paths(tokens, executable)
            if executable == "sed":
                programs, _ = ProcessRule._sed_programs(tuple(tokens))
                paths.extend(path for program in programs for path in ProcessRule._sed_program_file_paths(program))
            return paths
        if executable in cls._BASH_POSITIONAL_PATH_COMMANDS:
            return cls._positional_command_file_paths(tokens, executable)
        if executable == "git":
            paths: list[str] = []
            index = 1
            while index < len(tokens):
                matched = cls._option_value(tokens, index, ("-C", "--git-dir", "--work-tree"))
                if matched is not None:
                    value, index = matched
                    if value:
                        paths.append(value)
                    continue
                if tokens[index] == "--":
                    break
                if not tokens[index].startswith("-"):
                    break
                index += 1
            return paths
        if executable in cls._BASH_PATH_COMMANDS:
            paths = [path for token in tokens[1:] for path in cls._token_paths(token)]
            if executable == "tar":
                index = 1
                while index < len(tokens):
                    matched = cls._option_value(tokens, index,
                                                ("-f", "-T", "-X", "--exclude-from", "--file", "--files-from"))
                    if matched is not None:
                        value, index = matched
                        if value:
                            paths.append(value)
                        continue
                    index += 1
            return paths
        return []

    @staticmethod
    def _expand_shell_path(path: str, constants: dict[str, str]) -> str:

        def replace(match: re.Match[str]) -> str:
            name = match.group(1) or match.group(2)
            return constants.get(name, match.group(0))

        return _SHELL_VARIABLE_RE.sub(replace, path)

    @staticmethod
    def _brace_variants(path: str) -> list[str]:
        variants = [path]
        for _ in range(3):
            expanded = []
            changed = False
            for candidate in variants:
                match = re.search(r"\{([^{}]+,[^{}]+)\}", candidate)
                if not match:
                    expanded.append(candidate)
                    continue
                changed = True
                expanded.extend(candidate[:match.start()] + choice + candidate[match.end():]
                                for choice in match.group(1).split(",")[:16])
            variants = expanded[:32]
            if not changed:
                break
        return variants

    @staticmethod
    def _path_status(path: str, policy: ToolSafetyPolicy, cwd: Optional[str] = None) -> tuple[bool, bool]:
        """Return ``(denied, dynamic_match)`` for a path-like token."""

        candidate = path.strip().replace("\\", "/")
        variants = DangerousFileRule._brace_variants(candidate)
        normalized_variants = []
        for variant in variants:
            normalized = f"/{variant.lstrip('/')}" if variant.startswith("//") else variant
            if cwd and not normalized.startswith(("/", "~", "$")):
                normalized = posixpath.join(cwd.replace("\\", "/"), normalized)
            normalized_variants.append(posixpath.normpath(normalized))
        if any(policy.is_path_denied(variant) for variant in normalized_variants):
            return True, False
        normalized = normalized_variants[0]
        if "$" in normalized:
            return False, True
        if not any(char in normalized for char in "*?["):
            return False, False

        candidate_parts = normalized.strip("/").split("/")
        for raw_pattern in policy.denied_paths:
            pattern = raw_pattern.replace("\\", "/").lower()
            if any(char in pattern for char in "*?[") or pattern.startswith("~"):
                continue
            denied_parts = pattern.strip("/").split("/")
            if len(candidate_parts) >= len(denied_parts) and all(
                    fnmatch.fnmatchcase(denied_part, candidate_part.lower())
                    for denied_part, candidate_part in zip(denied_parts, candidate_parts)):
                return False, True
        return False, False

    @staticmethod
    def _curl_file_paths(tokens: list[str]) -> list[str]:
        paths: list[str] = []
        index = 1
        while index < len(tokens):
            token = tokens[index]
            if token in {"-T", "-d", "--upload-file", "--data", "--data-ascii", "--data-binary", "--data-raw"}:
                if index + 1 < len(tokens):
                    value = tokens[index + 1]
                    if token in {"-T", "--upload-file"} or value.startswith("@"):
                        paths.append(value.lstrip("@"))
                    index += 2
                    continue
            compact_upload = re.match(r"^(?:-T|--upload-file=)(.+)$", token)
            compact_data = re.match(r"^(?:-d|--data(?:-ascii|-binary|-raw)?=?)(@.+)$", token)
            if compact_upload:
                paths.append(compact_upload.group(1))
            elif compact_data:
                paths.append(compact_data.group(1).lstrip("@"))
            index += 1
        return paths

    @staticmethod
    def _python_path_nodes(node: ast.Call, name: str) -> list[ast.AST]:
        if name in {"glob.glob", "glob.iglob"}:
            return [node]
        positions = range(min(2, len(node.args))) if name in DangerousFileRule._MULTI_PATH_CALLS else range(
            min(1, len(node.args)))
        nodes = [node.args[index] for index in positions]
        keyword_names = {"dst", "file", "filename", "path", "pathname", "root_dir", "src", "target", "top"}
        nodes.extend(keyword.value for keyword in node.keywords if keyword.arg in keyword_names)
        return nodes

    def _scan_command(
        self,
        tokens: list[str],
        policy: ToolSafetyPolicy,
        *,
        line_number: Optional[int] = None,
        node: Optional[ast.AST] = None,
        redirects: Iterable[tuple[str, str]] = (),
        shell_constants: Optional[dict[str, str]] = None,
        cwd: Optional[str] = None,
    ) -> Iterable[SafetyFinding]:
        if self._dangerous_rm(tokens):
            yield self._finding(
                rule_id="FILE-DANGEROUS-DELETE",
                category=RiskCategory.DANGEROUS_FILE_OPERATION,
                risk_level=RiskLevel.CRITICAL,
                decision=SafetyDecision.DENY,
                evidence="recursive rm command detected",
                recommendation="Delete only explicit workspace files without recursive shell deletion.",
                node=node,
                line_number=line_number,
            )
        for nested in nested_find_commands(tokens):
            if self._dangerous_rm(nested):
                yield self._finding(
                    rule_id="FILE-DANGEROUS-DELETE",
                    category=RiskCategory.DANGEROUS_FILE_OPERATION,
                    risk_level=RiskLevel.CRITICAL,
                    decision=SafetyDecision.DENY,
                    evidence="find -exec performs recursive deletion",
                    recommendation="Remove recursive deletion from find actions.",
                    node=node,
                    line_number=line_number,
                )
        executable = "." if tokens and tokens[0] == "." else PurePosixPath(tokens[0]).name.lower() if tokens else ""
        if executable == "find" and "-delete" in tokens[1:]:
            yield self._finding(
                rule_id="FILE-DANGEROUS-DELETE",
                category=RiskCategory.DANGEROUS_FILE_OPERATION,
                risk_level=RiskLevel.CRITICAL,
                decision=SafetyDecision.DENY,
                evidence="find -delete recursively removes matched paths",
                recommendation="Delete only an explicit reviewed workspace file.",
                node=node,
                line_number=line_number,
            )
        if executable == "git" and len(tokens) > 1 and tokens[1] == "clean":
            flags = "".join(token.lstrip("-") for token in tokens[2:] if token.startswith("-"))
            if "f" in flags and any(flag in flags for flag in ("d", "x", "X")):
                yield self._finding(
                    rule_id="FILE-DANGEROUS-DELETE",
                    category=RiskCategory.DANGEROUS_FILE_OPERATION,
                    risk_level=RiskLevel.CRITICAL,
                    decision=SafetyDecision.DENY,
                    evidence="git clean force-removes untracked workspace content",
                    recommendation="Review and remove individual files without recursive force-cleaning.",
                    node=node,
                    line_number=line_number,
                )
        if executable == "git" and any(token == "--config-env" or token.startswith("--config-env=")
                                       for token in tokens[1:]):
            yield self._finding(
                rule_id="FILE-DYNAMIC-PATH",
                category=RiskCategory.DANGEROUS_FILE_OPERATION,
                risk_level=RiskLevel.HIGH,
                decision=SafetyDecision.NEEDS_HUMAN_REVIEW,
                evidence="git imports configuration from an environment variable",
                recommendation="Use fixed reviewed Git configuration without --config-env.",
                node=node,
                line_number=line_number,
            )

        paths = self._bash_file_paths(tokens)
        if executable == "curl":
            paths.extend(self._curl_file_paths(tokens))
        paths.extend(target for _, target in redirects if target)
        for path in paths:
            expanded_path = self._expand_shell_path(path, shell_constants or {})
            denied, dynamic = self._path_status(expanded_path, policy, cwd)
            if denied:
                yield self._finding(
                    rule_id="FILE-DENIED-PATH",
                    category=RiskCategory.DANGEROUS_FILE_OPERATION,
                    risk_level=RiskLevel.CRITICAL,
                    decision=SafetyDecision.DENY,
                    evidence=f"{executable or 'redirection'} targets a policy-denied path",
                    recommendation="Use files staged inside the isolated workspace.",
                    node=node,
                    line_number=line_number,
                )
            elif dynamic:
                yield self._finding(
                    rule_id="FILE-DYNAMIC-PATH",
                    category=RiskCategory.DANGEROUS_FILE_OPERATION,
                    risk_level=RiskLevel.HIGH,
                    decision=SafetyDecision.NEEDS_HUMAN_REVIEW,
                    evidence=f"{executable or 'redirection'} uses a dynamic path that may match a denied location",
                    recommendation="Use a canonical literal path inside the isolated workspace.",
                    node=node,
                    line_number=line_number,
                )

    def scan(self, context: SafetyRuleContext, policy: ToolSafetyPolicy) -> Iterable[SafetyFinding]:
        for argument in context.request.argv:
            denied, _ = self._path_status(argument, policy, context.request.cwd)
            if denied:
                yield self._finding(
                    rule_id="FILE-DENIED-PATH",
                    category=RiskCategory.DANGEROUS_FILE_OPERATION,
                    risk_level=RiskLevel.CRITICAL,
                    decision=SafetyDecision.DENY,
                    evidence="command-line arguments contain a policy-denied path",
                    recommendation="Pass only workspace-relative input paths.",
                )

        if context.python_tree is not None:
            for node in ast.walk(context.python_tree):
                if not isinstance(node, ast.Call):
                    continue
                name = resolved_call_name(node, context)
                if name in {"os.chdir", "os.fchdir"}:
                    yield self._finding(
                        rule_id="FILE-DYNAMIC-PATH",
                        category=RiskCategory.DANGEROUS_FILE_OPERATION,
                        risk_level=RiskLevel.HIGH,
                        decision=SafetyDecision.NEEDS_HUMAN_REVIEW,
                        evidence=f"{name} changes path resolution for later file operations",
                        recommendation="Keep the executor working directory fixed for the full invocation.",
                        node=node,
                    )
                if name == "shutil.rmtree":
                    yield self._finding(
                        rule_id="FILE-DANGEROUS-DELETE",
                        category=RiskCategory.DANGEROUS_FILE_OPERATION,
                        risk_level=RiskLevel.CRITICAL,
                        decision=SafetyDecision.DENY,
                        evidence="shutil.rmtree performs recursive deletion",
                        recommendation="Delete only explicitly reviewed workspace files.",
                        node=node,
                    )
                path_method_names = self._PATH_METHODS | self._PATHLIB_INSPECTION_METHODS
                if (not isinstance(node.func, ast.Attribute) and name.rsplit(".", 1)[-1] in path_method_names
                        and name.startswith(("Path.", "pathlib.Path."))):
                    yield self._finding(
                        rule_id="FILE-DYNAMIC-PATH",
                        category=RiskCategory.DANGEROUS_FILE_OPERATION,
                        risk_level=RiskLevel.HIGH,
                        decision=SafetyDecision.NEEDS_HUMAN_REVIEW,
                        evidence=f"bound {name} call no longer exposes its receiver path",
                        recommendation="Call the pathlib method directly on a canonical workspace Path.",
                        node=node,
                    )
                path_nodes: list[ast.AST] = []
                suppress_dynamic_path = False
                if name in self._PATH_CALLS:
                    path_nodes = self._python_path_nodes(node, name)
                    if any(keyword.arg in {"dir_fd", "dst_dir_fd", "src_dir_fd"} for keyword in node.keywords):
                        yield self._finding(
                            rule_id="FILE-DYNAMIC-PATH",
                            category=RiskCategory.DANGEROUS_FILE_OPERATION,
                            risk_level=RiskLevel.HIGH,
                            decision=SafetyDecision.NEEDS_HUMAN_REVIEW,
                            evidence=f"{name} resolves a relative path through a directory descriptor",
                            recommendation="Use an absolute canonical workspace path without dir_fd overrides.",
                            node=node,
                        )
                elif isinstance(node.func, ast.Attribute) and node.func.attr in path_method_names:
                    method_name = node.func.attr
                    receiver_aliases = _node_aliases(node.func.value, context)
                    receiver_instances = _node_instances(node.func.value, context)
                    receiver_constants = _node_constants(node.func.value, context)
                    receiver_is_path = _is_pathlib_expression(node.func.value, receiver_aliases, receiver_instances)
                    receiver_path = static_path(
                        node.func.value,
                        receiver_aliases,
                        receiver_constants,
                    )
                    receiver_is_string = (literal_string(node.func.value) is not None
                                          or (isinstance(node.func.value, ast.Name)
                                              and node.func.value.id in receiver_constants and not receiver_is_path))
                    if not receiver_is_string:
                        path_nodes = [node if method_name in {"glob", "rglob"} else node.func.value]
                        if method_name in {"hardlink_to", "rename", "replace", "samefile", "symlink_to"}:
                            if node.args:
                                path_nodes.append(node.args[0])
                            path_nodes.extend(keyword.value for keyword in node.keywords
                                              if keyword.arg in {"other_path", "target"})
                    suppress_dynamic_path = (method_name in self._PATHLIB_INSPECTION_METHODS and receiver_path is None
                                             and not receiver_is_path)
                for path_node in path_nodes:
                    path = static_path(
                        path_node,
                        _node_aliases(path_node, context),
                        _node_constants(path_node, context),
                    )
                    denied = path is not None and self._path_status(path, policy, context.request.cwd)[0]
                    if denied:
                        yield self._finding(
                            rule_id="FILE-DENIED-PATH",
                            category=RiskCategory.DANGEROUS_FILE_OPERATION,
                            risk_level=RiskLevel.CRITICAL,
                            decision=SafetyDecision.DENY,
                            evidence=f"{name or node.func.attr} targets a policy-denied path",
                            recommendation="Use files staged inside the isolated workspace.",
                            node=node,
                        )
                    elif path is None and not suppress_dynamic_path:
                        yield self._finding(
                            rule_id="FILE-DYNAMIC-PATH",
                            category=RiskCategory.DANGEROUS_FILE_OPERATION,
                            risk_level=RiskLevel.MEDIUM,
                            decision=SafetyDecision.NEEDS_HUMAN_REVIEW,
                            evidence=f"{name or node.func.attr} uses a path that cannot be resolved statically",
                            recommendation="Use a canonical literal path inside the isolated workspace.",
                            node=node,
                        )
                command_tokens = python_command_tokens(node, context)
                if command_tokens:
                    yield from self._scan_command(command_tokens, policy, node=node, cwd=context.request.cwd)

        shell_constants: dict[str, str] = {}
        for command in context.shell_commands:
            for assignment in command.assignments:
                variable, _, value = assignment.partition("=")
                if value and not _SHELL_VARIABLE_RE.search(value):
                    shell_constants[variable] = value
                else:
                    shell_constants.pop(variable, None)
            yield from self._scan_command(
                list(command.argv),
                policy,
                line_number=command.line_number,
                redirects=command.redirects,
                shell_constants=shell_constants,
                cwd=context.request.cwd,
            )


class NetworkRule(BaseSafetyRule):
    """Detect literal non-whitelisted destinations and dynamic targets."""

    rule_id = "NET"
    _NETWORK_CALLS = {
        "aiohttp.request",
        "aiohttp.ClientSession.delete",
        "aiohttp.ClientSession.get",
        "aiohttp.ClientSession.head",
        "aiohttp.ClientSession.options",
        "aiohttp.ClientSession.patch",
        "aiohttp.ClientSession.post",
        "aiohttp.ClientSession.put",
        "aiohttp.ClientSession.request",
        "httpx.AsyncClient.delete",
        "httpx.AsyncClient.get",
        "httpx.AsyncClient.head",
        "httpx.AsyncClient.options",
        "httpx.AsyncClient.patch",
        "httpx.AsyncClient.post",
        "httpx.AsyncClient.put",
        "httpx.AsyncClient.request",
        "httpx.AsyncClient.stream",
        "httpx.Client.delete",
        "httpx.Client.get",
        "httpx.Client.head",
        "httpx.Client.options",
        "httpx.Client.patch",
        "httpx.Client.post",
        "httpx.Client.put",
        "httpx.Client.request",
        "httpx.Client.stream",
        "httpx.delete",
        "httpx.get",
        "httpx.head",
        "httpx.options",
        "httpx.patch",
        "httpx.post",
        "httpx.put",
        "httpx.request",
        "httpx.stream",
        "requests.Session.delete",
        "requests.Session.get",
        "requests.Session.head",
        "requests.Session.options",
        "requests.Session.patch",
        "requests.Session.post",
        "requests.Session.put",
        "requests.Session.request",
        "requests.api.delete",
        "requests.api.get",
        "requests.api.head",
        "requests.api.options",
        "requests.api.patch",
        "requests.api.post",
        "requests.api.put",
        "requests.api.request",
        "requests.sessions.Session.delete",
        "requests.sessions.Session.get",
        "requests.sessions.Session.head",
        "requests.sessions.Session.options",
        "requests.sessions.Session.patch",
        "requests.sessions.Session.post",
        "requests.sessions.Session.put",
        "requests.sessions.Session.request",
        "requests.delete",
        "requests.get",
        "requests.head",
        "requests.options",
        "requests.patch",
        "requests.post",
        "requests.put",
        "requests.request",
        "socket.create_connection",
        "socket.socket.connect",
        "socket.socket.connect_ex",
        "socket.socket.sendto",
        "urllib.request.urlopen",
    }
    _URL_SECOND_ARGUMENT = {
        "aiohttp.request",
        "aiohttp.ClientSession.request",
        "httpx.AsyncClient.request",
        "httpx.AsyncClient.stream",
        "httpx.Client.request",
        "httpx.Client.stream",
        "httpx.request",
        "httpx.stream",
        "requests.Session.request",
        "requests.api.request",
        "requests.sessions.Session.request",
        "requests.request",
    }
    _SOCKET_ARGUMENT = {
        "socket.create_connection": 0,
        "socket.socket.connect": 0,
        "socket.socket.connect_ex": 0,
        "socket.socket.sendto": 1,
    }

    def _target_finding(
        self,
        target: Optional[str],
        policy: ToolSafetyPolicy,
        *,
        dynamic: bool = False,
        node: Optional[ast.AST] = None,
        line_number: Optional[int] = None,
    ) -> Optional[SafetyFinding]:
        if dynamic or target is None:
            return self._finding(
                rule_id="NET-DYNAMIC-TARGET",
                category=RiskCategory.NETWORK_ACCESS,
                risk_level=RiskLevel.MEDIUM,
                decision=SafetyDecision.NEEDS_HUMAN_REVIEW,
                evidence="network destination cannot be resolved statically",
                recommendation="Use a literal URL whose hostname is policy-whitelisted.",
                node=node,
                line_number=line_number,
            )
        parsed_target = urlsplit(target if "://" in target else f"//{target}")
        if ("\\" in target or any(ord(char) < 32 for char in target) or parsed_target.username is not None
                or parsed_target.password is not None):
            return self._finding(
                rule_id="NET-AMBIGUOUS-URL",
                category=RiskCategory.NETWORK_ACCESS,
                risk_level=RiskLevel.HIGH,
                decision=SafetyDecision.DENY,
                evidence="network URL has an ambiguous or credential-bearing authority",
                recommendation="Use a canonical URL without backslashes, control characters, or userinfo.",
                node=node,
                line_number=line_number,
            )
        hostname, is_dynamic = _hostname(target)
        if is_dynamic or not hostname:
            return self._target_finding(None, policy, dynamic=True, node=node, line_number=line_number)
        if policy.is_domain_allowed(hostname):
            return None
        return self._finding(
            rule_id="NET-NON-WHITELISTED",
            category=RiskCategory.NETWORK_ACCESS,
            risk_level=RiskLevel.HIGH,
            decision=SafetyDecision.DENY,
            evidence=f"network destination hostname is not whitelisted: {hostname}",
            recommendation="Add the exact trusted domain to policy or remove the outbound request.",
            node=node,
            line_number=line_number,
            metadata={"hostname": hostname},
        )

    def _scan_tokens(
        self,
        tokens: list[str],
        policy: ToolSafetyPolicy,
        *,
        node: Optional[ast.AST] = None,
        line_number: Optional[int] = None,
    ) -> Iterable[SafetyFinding]:
        targets, dynamic = _network_targets(tokens)
        for target in targets:
            finding = self._target_finding(target, policy, node=node, line_number=line_number)
            if finding:
                yield finding
        if dynamic:
            finding = self._target_finding(None, policy, dynamic=True, node=node, line_number=line_number)
            if finding:
                yield finding

    def scan(self, context: SafetyRuleContext, policy: ToolSafetyPolicy) -> Iterable[SafetyFinding]:
        for argument in context.request.argv:
            if _URL_RE.match(argument) or argument.startswith("$"):
                finding = self._target_finding(argument, policy)
                if finding:
                    yield finding

        if context.python_tree is not None:
            for node in ast.walk(context.python_tree):
                if not isinstance(node, ast.Call):
                    continue
                name = resolved_call_name(node, context)
                target_node: Optional[ast.AST] = None
                if name in self._NETWORK_CALLS:
                    argument_index = 1 if name in self._URL_SECOND_ARGUMENT else self._SOCKET_ARGUMENT.get(name, 0)
                    target_node = node.args[argument_index] if len(node.args) > argument_index else None
                    for keyword in node.keywords:
                        if keyword.arg in {"address", "host", "url", "uri"}:
                            target_node = keyword.value
                            break
                if name in self._SOCKET_ARGUMENT:
                    if isinstance(target_node, (ast.Tuple, ast.List)) and target_node.elts:
                        target_node = target_node.elts[0]
                if target_node is not None or name in self._NETWORK_CALLS:
                    finding = self._target_finding(literal_string(target_node),
                                                   policy,
                                                   node=node,
                                                   dynamic=target_node is None or literal_string(target_node) is None)
                    if finding:
                        yield finding
                command_tokens = python_command_tokens(node, context)
                if command_tokens:
                    yield from self._scan_tokens(command_tokens, policy, node=node)

        for command in context.shell_commands:
            tokens = list(command.argv)
            yield from self._scan_tokens(tokens, policy, line_number=command.line_number)
            for nested in nested_find_commands(tokens):
                yield from self._scan_tokens(nested, policy, line_number=command.line_number)


class ProcessRule(BaseSafetyRule):
    """Detect process creation, shell injection, pipelines, and privilege use."""

    rule_id = "PROC"
    _SUBPROCESS_CALLS = {
        "asyncio.create_subprocess_exec",
        "asyncio.create_subprocess_shell",
        "subprocess.call",
        "subprocess.check_call",
        "subprocess.check_output",
        "subprocess.getoutput",
        "subprocess.getstatusoutput",
        "subprocess.Popen",
        "subprocess.run",
    }
    _SHELL_PROCESS_CALLS = {
        "asyncio.create_subprocess_shell",
        "subprocess.getoutput",
        "subprocess.getstatusoutput",
    }
    _ALTERNATIVE_PROCESS_CALLS = {
        "multiprocessing.Process",
        "os.execl",
        "os.execle",
        "os.execlp",
        "os.execlpe",
        "os.execv",
        "os.execve",
        "os.execvp",
        "os.execvpe",
        "os.posix_spawn",
        "os.posix_spawnp",
        "os.spawnl",
        "os.spawnle",
        "os.spawnlp",
        "os.spawnlpe",
        "os.spawnv",
        "os.spawnve",
        "os.spawnvp",
        "os.spawnvpe",
    }
    _EXECUTION_ENVIRONMENT = {
        "BASH_ENV",
        "ENV",
        "GIT_EXEC_PATH",
        "IFS",
        "LD_LIBRARY_PATH",
        "LD_PRELOAD",
        "PATH",
        "PYTHONPATH",
        "SHELLOPTS",
    }
    _SHELL_BUILTINS = {
        "!",
        "[",
        "[[",
        "]",
        "]]",
        "case",
        "cd",
        "declare",
        "do",
        "done",
        "else",
        "esac",
        "export",
        "false",
        "fi",
        "for",
        "function",
        "if",
        "in",
        "local",
        "read",
        "readonly",
        "return",
        "set",
        "shift",
        "test",
        "then",
        "time",
        "true",
        "until",
        "while",
        "{",
        "}",
    }
    _SED_ADDRESS = (r"(?:(?:\d+(?:~\d+)?|\$|/(?:\\.|[^/\n])*/)"
                    r"(?:\s*,\s*(?:\d+(?:~\d+)?|\$|/(?:\\.|[^/\n])*/|[+~]\d+))?\s*)?")

    @staticmethod
    def _sed_programs(tokens: tuple[str, ...]) -> tuple[list[str], bool]:
        """Extract inline sed programs and report whether an external program is used."""

        programs: list[str] = []
        operands: list[str] = []
        external_program = False
        index = 1
        while index < len(tokens):
            token = tokens[index]
            if token == "--":
                operands.extend(tokens[index + 1:])
                break
            clustered_expression = DangerousFileRule._clustered_short_option_value(tokens, index, "e", "Enrsuz")
            if clustered_expression is not None:
                program, index = clustered_expression
                if program:
                    programs.append(program)
                continue
            clustered_file = DangerousFileRule._clustered_short_option_value(tokens, index, "f", "Enrsuz")
            if clustered_file is not None:
                _, index = clustered_file
                external_program = True
                continue
            if token in {"-e", "--expression"}:
                if index + 1 < len(tokens):
                    programs.append(tokens[index + 1])
                index += 2
                continue
            if token.startswith("--expression="):
                programs.append(token.split("=", 1)[1])
                index += 1
                continue
            if token.startswith("-e") and len(token) > 2:
                programs.append(token[2:])
                index += 1
                continue
            if token in {"-f", "--file"}:
                external_program = True
                index += 2
                continue
            if token.startswith(("-f", "--file=")):
                external_program = True
                index += 1
                continue
            if token.startswith("-") and token != "-":
                index += 1
                continue
            operands.append(token)
            index += 1
        if not programs and not external_program and operands:
            programs.append(operands[0])
        return programs, external_program

    @staticmethod
    def _sed_program_executes_shell(program: str) -> bool:
        execute_command = re.compile(rf"(?:^|[;{{}}\n])\s*{ProcessRule._SED_ADDRESS}!?\s*e(?:\s|$)")
        if execute_command.search(program):
            return True
        substitutions = re.finditer(
            r"s(?P<delimiter>[^\\\n])(?:\\.|(?!(?P=delimiter)).)*(?P=delimiter)"
            r"(?:\\.|(?!(?P=delimiter)).)*(?P=delimiter)(?P<flags>[A-Za-z0-9]*)",
            program,
        )
        return any("e" in match.group("flags") for match in substitutions)

    @staticmethod
    def _sed_program_file_paths(program: str) -> list[str]:
        command_paths = [
            match.group("path").strip() for match in re.finditer(
                rf"(?:^|[;{{}}\n])\s*{ProcessRule._SED_ADDRESS}!?\s*[rRwW]\s+(?P<path>[^;\n]+)",
                program,
            )
        ]
        substitutions = re.finditer(
            r"s(?P<delimiter>[^\\\n])(?:\\.|(?!(?P=delimiter)).)*(?P=delimiter)"
            r"(?:\\.|(?!(?P=delimiter)).)*(?P=delimiter)(?P<flags>[A-Za-z0-9]*)"
            r"(?:\s+(?P<path>[^;\n]+))?",
            program,
        )
        command_paths.extend(
            match.group("path").strip() for match in substitutions
            if "w" in match.group("flags") and match.group("path"))
        return command_paths

    def scan(self, context: SafetyRuleContext, policy: ToolSafetyPolicy) -> Iterable[SafetyFinding]:
        inherited_overrides = sorted(name for name in context.request.environment_keys
                                     if name.upper() in self._EXECUTION_ENVIRONMENT)
        if context.request.metadata.get("background") is True:
            yield self._finding(
                rule_id="PROC-BACKGROUND",
                category=RiskCategory.PROCESS_EXECUTION,
                risk_level=RiskLevel.HIGH,
                decision=SafetyDecision.NEEDS_HUMAN_REVIEW,
                evidence="tool execution requests a background process",
                recommendation="Run the command in the foreground and wait for bounded completion.",
            )
        if inherited_overrides:
            yield self._finding(
                rule_id="POLICY-EXECUTION-ENV",
                category=RiskCategory.POLICY_VIOLATION,
                risk_level=RiskLevel.HIGH,
                decision=SafetyDecision.DENY,
                evidence="execution environment includes loader or command-resolution overrides",
                recommendation="Remove loader and command-resolution variables from the tool environment.",
                metadata={"environment_keys": inherited_overrides},
            )
        for argument in context.request.argv:
            if any(marker in argument for marker in (";", "&&", "||", "$(", "`", "\n")):
                yield self._finding(
                    rule_id="PROC-SHELL-INJECTION",
                    category=RiskCategory.PROCESS_EXECUTION,
                    risk_level=RiskLevel.HIGH,
                    decision=SafetyDecision.NEEDS_HUMAN_REVIEW,
                    evidence="command-line argument contains shell control syntax",
                    recommendation="Pass arguments as an exec-style list without shell expansion.",
                )

        if context.python_tree is not None:
            for node in ast.walk(context.python_tree):
                if not isinstance(node, ast.Call):
                    continue
                name = resolved_call_name(node, context)
                if name in self._SUBPROCESS_CALLS:
                    yield self._finding(
                        rule_id="PROC-SUBPROCESS",
                        category=RiskCategory.PROCESS_EXECUTION,
                        risk_level=RiskLevel.HIGH,
                        decision=SafetyDecision.NEEDS_HUMAN_REVIEW,
                        evidence=f"{name} launches an external process",
                        recommendation="Review the executable and pass an argument list with shell=False.",
                        node=node,
                    )
                    if name == "subprocess.Popen":
                        yield self._finding(
                            rule_id="PROC-BACKGROUND",
                            category=RiskCategory.PROCESS_EXECUTION,
                            risk_level=RiskLevel.HIGH,
                            decision=SafetyDecision.NEEDS_HUMAN_REVIEW,
                            evidence="subprocess.Popen may outlive the immediate tool call",
                            recommendation="Prefer a bounded foreground subprocess and wait for completion.",
                            node=node,
                        )
                    if name in self._SHELL_PROCESS_CALLS or _keyword_bool(node, "shell") is True:
                        yield self._finding(
                            rule_id="PROC-SHELL-INJECTION",
                            category=RiskCategory.PROCESS_EXECUTION,
                            risk_level=RiskLevel.CRITICAL,
                            decision=SafetyDecision.DENY,
                            evidence=f"{name} executes a command through a shell",
                            recommendation="Use shell=False with a fixed executable and argument list.",
                            node=node,
                        )
                elif name in self._ALTERNATIVE_PROCESS_CALLS:
                    yield self._finding(
                        rule_id="PROC-SUBPROCESS",
                        category=RiskCategory.PROCESS_EXECUTION,
                        risk_level=RiskLevel.HIGH,
                        decision=SafetyDecision.NEEDS_HUMAN_REVIEW,
                        evidence=f"{name} creates or replaces an external process",
                        recommendation="Use a reviewed, bounded subprocess through the guarded executor.",
                        node=node,
                    )
                elif name in {"os.system", "os.popen"}:
                    yield self._finding(
                        rule_id="PROC-OS-SYSTEM",
                        category=RiskCategory.PROCESS_EXECUTION,
                        risk_level=RiskLevel.CRITICAL,
                        decision=SafetyDecision.DENY,
                        evidence=f"{name} invokes a command through a shell",
                        recommendation="Use a reviewed exec-style subprocess without a shell.",
                        node=node,
                    )
                elif name in {"builtins.eval", "builtins.exec", "eval", "exec"}:
                    yield self._finding(
                        rule_id="PROC-SHELL-INJECTION",
                        category=RiskCategory.PROCESS_EXECUTION,
                        risk_level=RiskLevel.CRITICAL,
                        decision=SafetyDecision.DENY,
                        evidence=f"dynamic code execution through {name}",
                        recommendation="Replace dynamic evaluation with explicit, validated operations.",
                        node=node,
                    )
                tokens = python_command_tokens(node, context)
                if tokens and PurePosixPath(tokens[0]).name.lower() in {"doas", "su", "sudo"}:
                    yield self._finding(
                        rule_id="PROC-PRIVILEGE",
                        category=RiskCategory.PROCESS_EXECUTION,
                        risk_level=RiskLevel.CRITICAL,
                        decision=SafetyDecision.DENY,
                        evidence="command attempts privilege escalation",
                        recommendation="Run with the sandbox's unprivileged identity.",
                        node=node,
                    )

        executable_text = context.shell_executable_text
        if re.search(r"\$\([^)]*\)|`[^`]+`", executable_text):
            yield self._finding(
                rule_id="PROC-SHELL-INJECTION",
                category=RiskCategory.PROCESS_EXECUTION,
                risk_level=RiskLevel.CRITICAL,
                decision=SafetyDecision.DENY,
                evidence="shell command substitution requires review",
                recommendation="Avoid dynamically constructing commands from substitution output.",
            )
        if re.search(r"(?:^|[\s;|&])[<>]\(", executable_text):
            yield self._finding(
                rule_id="PROC-SHELL-INJECTION",
                category=RiskCategory.PROCESS_EXECUTION,
                risk_level=RiskLevel.CRITICAL,
                decision=SafetyDecision.DENY,
                evidence="shell process substitution executes a nested command",
                recommendation="Replace process substitution with a reviewed intermediate workspace file.",
            )

        persistent_overrides: set[str] = set()
        for command in context.shell_commands:
            executable = command.executable
            current_overrides = {
                assignment.partition("=")[0]
                for assignment in command.assignments
                if assignment.partition("=")[0].upper() in self._EXECUTION_ENVIRONMENT
            }
            overridden = sorted(persistent_overrides | current_overrides)
            if overridden and command.argv:
                yield self._finding(
                    rule_id="POLICY-EXECUTION-ENV",
                    category=RiskCategory.POLICY_VIOLATION,
                    risk_level=RiskLevel.HIGH,
                    decision=SafetyDecision.DENY,
                    evidence="command overrides executable-loading environment variables",
                    recommendation="Use the executor's trusted environment without per-command loader overrides.",
                    line_number=command.line_number,
                    metadata={"environment_keys": overridden},
                )
            if not command.argv:
                persistent_overrides.update(current_overrides)
            if command.operator == "|":
                yield self._finding(
                    rule_id="PROC-PIPELINE",
                    category=RiskCategory.PROCESS_EXECUTION,
                    risk_level=RiskLevel.MEDIUM,
                    decision=SafetyDecision.NEEDS_HUMAN_REVIEW,
                    evidence="shell pipeline detected",
                    recommendation="Review each pipeline stage and avoid passing untrusted data to a shell.",
                    line_number=command.line_number,
                )
            elif command.operator in {";", "&&", "||"}:
                yield self._finding(
                    rule_id="PROC-SHELL-INJECTION",
                    category=RiskCategory.PROCESS_EXECUTION,
                    risk_level=RiskLevel.MEDIUM,
                    decision=SafetyDecision.NEEDS_HUMAN_REVIEW,
                    evidence=f"shell command chaining uses {command.operator}",
                    recommendation="Use one explicit executable per guarded invocation.",
                    line_number=command.line_number,
                )
            elif command.operator == "&":
                yield self._finding(
                    rule_id="PROC-BACKGROUND",
                    category=RiskCategory.PROCESS_EXECUTION,
                    risk_level=RiskLevel.HIGH,
                    decision=SafetyDecision.NEEDS_HUMAN_REVIEW,
                    evidence="background shell process detected",
                    recommendation="Keep processes in the foreground with a bounded timeout.",
                    line_number=command.line_number,
                )
            if executable in {"doas", "su", "sudo"}:
                yield self._finding(
                    rule_id="PROC-PRIVILEGE",
                    category=RiskCategory.PROCESS_EXECUTION,
                    risk_level=RiskLevel.CRITICAL,
                    decision=SafetyDecision.DENY,
                    evidence="command attempts privilege escalation",
                    recommendation="Run with the sandbox's unprivileged identity.",
                    line_number=command.line_number,
                )
            if executable in {"eval", "exec"} or (executable in {"bash", "sh", "zsh"}
                                                  and any("$" in token for token in command.argv[1:])):
                yield self._finding(
                    rule_id="PROC-SHELL-INJECTION",
                    category=RiskCategory.PROCESS_EXECUTION,
                    risk_level=RiskLevel.CRITICAL,
                    decision=SafetyDecision.DENY,
                    evidence="dynamic shell evaluation detected",
                    recommendation="Replace dynamic shell evaluation with fixed exec-style arguments.",
                    line_number=command.line_number,
                )
            if executable in {"awk", "gawk", "mawk"} and any("system(" in token.replace(" ", "")
                                                             for token in command.argv[1:]):
                yield self._finding(
                    rule_id="PROC-SHELL-INJECTION",
                    category=RiskCategory.PROCESS_EXECUTION,
                    risk_level=RiskLevel.CRITICAL,
                    decision=SafetyDecision.DENY,
                    evidence="awk program invokes a system command",
                    recommendation="Remove system() calls from inline awk programs.",
                    line_number=command.line_number,
                )
            if executable == "sed":
                sed_programs, external_program = self._sed_programs(command.argv)
                executes_shell = any(self._sed_program_executes_shell(program) for program in sed_programs)
                if executes_shell or external_program:
                    yield self._finding(
                        rule_id="PROC-SHELL-INJECTION",
                        category=RiskCategory.PROCESS_EXECUTION,
                        risk_level=RiskLevel.CRITICAL if executes_shell else RiskLevel.HIGH,
                        decision=SafetyDecision.DENY if executes_shell else SafetyDecision.NEEDS_HUMAN_REVIEW,
                        evidence=("sed program executes a shell command"
                                  if executes_shell else "external sed program cannot be inspected statically"),
                        recommendation="Use an inline sed program without e commands or substitution e flags.",
                        line_number=command.line_number,
                    )
            if executable == "sort" and any(token == "--compress-program" or token.startswith("--compress-prog")
                                            for token in command.argv[1:]):
                yield self._finding(
                    rule_id="PROC-SUBPROCESS",
                    category=RiskCategory.PROCESS_EXECUTION,
                    risk_level=RiskLevel.HIGH,
                    decision=SafetyDecision.NEEDS_HUMAN_REVIEW,
                    evidence="sort delegates compression to an external process",
                    recommendation="Remove --compress-program and sort workspace files directly.",
                    line_number=command.line_number,
                )
            if executable == "git" and any(token.startswith("alias.") or "=!" in token for token in command.argv[1:]):
                yield self._finding(
                    rule_id="PROC-SHELL-INJECTION",
                    category=RiskCategory.PROCESS_EXECUTION,
                    risk_level=RiskLevel.CRITICAL,
                    decision=SafetyDecision.DENY,
                    evidence="git command defines or invokes a shell alias",
                    recommendation="Use fixed built-in git subcommands without shell aliases.",
                    line_number=command.line_number,
                )
            if executable == "git" and any(token.lower().startswith("core.sshcommand") for token in command.argv[1:]):
                yield self._finding(
                    rule_id="PROC-SHELL-INJECTION",
                    category=RiskCategory.PROCESS_EXECUTION,
                    risk_level=RiskLevel.CRITICAL,
                    decision=SafetyDecision.DENY,
                    evidence="git config injects an external SSH command",
                    recommendation="Use the executor's fixed Git transport configuration.",
                    line_number=command.line_number,
                )
            if executable == "find":
                for index, token in enumerate(command.argv[:-2]):
                    if token not in {"-exec", "-execdir"}:
                        continue
                    nested = [PurePosixPath(item).name.lower() for item in command.argv[index + 1:]]
                    if nested and nested[0] in {"bash", "sh", "zsh"} and "-c" in nested[1:]:
                        yield self._finding(
                            rule_id="PROC-SHELL-INJECTION",
                            category=RiskCategory.PROCESS_EXECUTION,
                            risk_level=RiskLevel.CRITICAL,
                            decision=SafetyDecision.DENY,
                            evidence="find -exec launches a nested command shell",
                            recommendation="Use a fixed non-shell find action over reviewed workspace paths.",
                            line_number=command.line_number,
                        )
                        break
            if executable in {"bash", "python", "python3", "sh", "zsh"} and "-c" in command.argv[1:]:
                yield self._finding(
                    rule_id="PROC-INTERPRETER-CODE",
                    category=RiskCategory.PROCESS_EXECUTION,
                    risk_level=RiskLevel.HIGH,
                    decision=SafetyDecision.NEEDS_HUMAN_REVIEW,
                    evidence=f"{executable} executes inline code",
                    recommendation="Scan a standalone script in its native language before execution.",
                    line_number=command.line_number,
                )
            raw_executable = command.argv[0] if command.argv else executable
            if executable and executable not in self._SHELL_BUILTINS and not policy.is_command_allowed(raw_executable):
                yield self._finding(
                    rule_id="POLICY-ARGV-COMMAND",
                    category=RiskCategory.POLICY_VIOLATION,
                    risk_level=RiskLevel.MEDIUM,
                    decision=SafetyDecision.NEEDS_HUMAN_REVIEW,
                    evidence=f"command is not in the configured allowlist: {executable}",
                    recommendation="Add the reviewed executable to allowed_commands or use an allowed command.",
                    line_number=command.line_number,
                    metadata={"command": executable},
                )


class DependencyRule(BaseSafetyRule):
    """Detect commands that mutate the runtime dependency set."""

    rule_id = "DEP-INSTALL"
    _PYTHON_MODULE_PREFIX_FLAGS = frozenset("bBdEhiIOPqRsSuvVx")

    @classmethod
    def _python_module_invocation(cls, tokens: list[str]) -> Optional[tuple[str, list[str]]]:
        index = 1
        while index < len(tokens):
            token = tokens[index]
            if token == "-m":
                return ((tokens[index + 1].lower(),
                         [item.lower() for item in tokens[index + 2:]]) if index + 1 < len(tokens) else None)
            if token.startswith("-") and not token.startswith("--"):
                body = token[1:]
                module_index = body.find("m")
                if module_index >= 0 and all(flag in cls._PYTHON_MODULE_PREFIX_FLAGS for flag in body[:module_index]):
                    module = body[module_index + 1:]
                    if module:
                        return module.lower(), [item.lower() for item in tokens[index + 1:]]
                    return ((tokens[index + 1].lower(),
                             [item.lower() for item in tokens[index + 2:]]) if index + 1 < len(tokens) else None)
                index += 2 if token in {"-W", "-X"} else 1
                continue
            break
        return None

    @staticmethod
    def _is_install(tokens: list[str]) -> bool:
        if not tokens:
            return False
        normalized = [
            PurePosixPath(token).name.lower() if index == 0 else token.lower() for index, token in enumerate(tokens)
        ]
        executable = normalized[0]
        if executable in {
                "pip", "pip3", "npm", "pnpm", "yarn", "apt", "apt-get", "dnf", "yum", "apk", "brew", "cargo", "gem"
        }:
            return any(token in {"add", "i", "install"} for token in normalized[1:])
        if executable in {"python", "python3"} or re.fullmatch(r"python\d+(?:\.\d+)*", executable):
            module_invocation = DependencyRule._python_module_invocation(tokens)
            if module_invocation is not None:
                module, module_args = module_invocation
                module = module.removesuffix(".__main__")
                return module == "ensurepip" or (module in {"pip", "pip3"} and "install" in module_args)
        return False

    def _finding_for(
        self,
        tokens: list[str],
        *,
        node: Optional[ast.AST] = None,
        line_number: Optional[int] = None,
    ) -> Optional[SafetyFinding]:
        if not self._is_install(tokens):
            return None
        return self._finding(
            rule_id="DEP-INSTALL",
            category=RiskCategory.DEPENDENCY_INSTALLATION,
            risk_level=RiskLevel.HIGH,
            decision=SafetyDecision.DENY,
            evidence=f"dependency installation command detected: {PurePosixPath(tokens[0]).name}",
            recommendation="Bake reviewed dependencies into the runtime image instead of installing at execution time.",
            node=node,
            line_number=line_number,
        )

    def scan(self, context: SafetyRuleContext, policy: ToolSafetyPolicy) -> Iterable[SafetyFinding]:
        del policy
        if context.python_tree is not None:
            for node in ast.walk(context.python_tree):
                if not isinstance(node, ast.Call):
                    continue
                tokens = python_command_tokens(node, context)
                if tokens:
                    finding = self._finding_for(tokens, node=node)
                    if finding:
                        yield finding
                name = resolved_call_name(node, context)
                if name in {"pip.main", "pip._internal.main"} and any(
                        isinstance(arg, (ast.List, ast.Tuple)) and any(
                            literal_string(item) == "install" for item in arg.elts) for arg in node.args):
                    yield self._finding(
                        rule_id="DEP-INSTALL",
                        category=RiskCategory.DEPENDENCY_INSTALLATION,
                        risk_level=RiskLevel.HIGH,
                        decision=SafetyDecision.DENY,
                        evidence="pip install invoked through its Python API",
                        recommendation="Bake reviewed dependencies into the runtime image.",
                        node=node,
                    )
        for command in context.shell_commands:
            tokens = list(command.argv)
            finding = self._finding_for(tokens, line_number=command.line_number)
            if finding:
                yield finding
            for nested in nested_find_commands(tokens):
                finding = self._finding_for(nested, line_number=command.line_number)
                if finding:
                    yield finding


class ResourceRule(BaseSafetyRule):
    """Detect definite infinite loops and suspicious resource requests."""

    rule_id = "RES"

    @staticmethod
    def _loop_has_break(node: ast.While) -> bool:

        class BreakVisitor(ast.NodeVisitor):
            found = False

            def visit_Break(self, child: ast.Break) -> None:  # noqa: N802
                del child
                self.found = True

            def visit_For(self, child: ast.For) -> None:  # noqa: N802
                del child

            def visit_While(self, child: ast.While) -> None:  # noqa: N802
                del child

            def visit_FunctionDef(self, child: ast.FunctionDef) -> None:  # noqa: N802
                del child

            def visit_AsyncFunctionDef(self, child: ast.AsyncFunctionDef) -> None:  # noqa: N802
                del child

            def visit_If(self, child: ast.If) -> None:  # noqa: N802
                truth = literal_truth(child.test)
                statements = child.body if truth is True else child.orelse if truth is False else [
                    *child.body, *child.orelse
                ]
                for statement in statements:
                    self.visit(statement)

        visitor = BreakVisitor()
        for statement in node.body:
            visitor.visit(statement)
        return visitor.found

    @staticmethod
    def _constant_size(node: ast.AST) -> Optional[int]:
        if isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)):
            return len(node.value)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
            left_size = ResourceRule._constant_size(node.left)
            right_size = ResourceRule._constant_size(node.right)
            left_number = literal_number(node.left)
            right_number = literal_number(node.right)
            if left_size is not None and right_number is not None:
                return int(left_size * right_number)
            if right_size is not None and left_number is not None:
                return int(right_size * left_number)
        return None

    def scan(self, context: SafetyRuleContext, policy: ToolSafetyPolicy) -> Iterable[SafetyFinding]:
        if context.python_tree is not None:
            for node in ast.walk(context.python_tree):
                if isinstance(node, ast.While):
                    is_true = literal_truth(node.test) is True
                    if is_true and not self._loop_has_break(node):
                        yield self._finding(
                            rule_id="RES-INFINITE-LOOP",
                            category=RiskCategory.RESOURCE_ABUSE,
                            risk_level=RiskLevel.CRITICAL,
                            decision=SafetyDecision.DENY,
                            evidence="unbounded while loop has no reachable syntactic break",
                            recommendation="Add a bounded iteration count, cancellation check, or timeout.",
                            node=node,
                        )
                if not isinstance(node, ast.Call):
                    continue
                name = resolved_call_name(node, context)
                if name in {"os.fork", "os.forkpty"}:
                    yield self._finding(
                        rule_id="RES-FORK-BOMB",
                        category=RiskCategory.RESOURCE_ABUSE,
                        risk_level=RiskLevel.CRITICAL,
                        decision=SafetyDecision.DENY,
                        evidence=f"{name} creates an unmanaged child process",
                        recommendation="Use bounded executor-managed concurrency instead of forking.",
                        node=node,
                    )
                if name in {"asyncio.sleep", "time.sleep"} and node.args:
                    seconds = literal_number(node.args[0])
                    if seconds is not None and seconds > policy.long_sleep_seconds:
                        yield self._finding(
                            rule_id="RES-LONG-SLEEP",
                            category=RiskCategory.RESOURCE_ABUSE,
                            risk_level=RiskLevel.MEDIUM,
                            decision=SafetyDecision.NEEDS_HUMAN_REVIEW,
                            evidence=(f"sleep duration {seconds:g}s exceeds the "
                                      f"{policy.long_sleep_seconds:g}s threshold"),
                            recommendation="Use cancellable polling with short bounded waits.",
                            node=node,
                        )
                is_output_call = name in {"builtins.print", "print"} or name.endswith(
                    (".write", ".write_bytes", ".write_text"))
                if is_output_call and node.args:
                    size = self._constant_size(node.args[0])
                    if size is not None and size > policy.max_output_bytes:
                        yield self._finding(
                            rule_id="RES-LARGE-WRITE",
                            category=RiskCategory.RESOURCE_ABUSE,
                            risk_level=RiskLevel.HIGH,
                            decision=SafetyDecision.DENY,
                            evidence=f"constant write size {size} bytes exceeds the configured output maximum",
                            recommendation="Write a bounded result or use managed artifact streaming.",
                            node=node,
                        )
                if name.endswith(("ThreadPoolExecutor", "ProcessPoolExecutor", "Pool")):
                    workers_node = node.args[0] if node.args else None
                    for keyword in node.keywords:
                        if keyword.arg in {"max_workers", "processes"}:
                            workers_node = keyword.value
                    workers = literal_number(workers_node)
                    if workers is not None and workers > policy.max_concurrency:
                        yield self._finding(
                            rule_id="RES-HIGH-CONCURRENCY",
                            category=RiskCategory.RESOURCE_ABUSE,
                            risk_level=RiskLevel.HIGH,
                            decision=SafetyDecision.DENY,
                            evidence=(f"requested concurrency {workers:g} exceeds the configured "
                                      f"maximum {policy.max_concurrency}"),
                            recommendation="Reduce worker count to the configured concurrency limit.",
                            node=node,
                        )

        if re.search(r":\s*\(\s*\)\s*\{[^}]*:\s*\|\s*:\s*&[^}]*\}\s*;\s*:", context.shell_executable_text, re.DOTALL):
            yield self._finding(
                rule_id="RES-FORK-BOMB",
                category=RiskCategory.RESOURCE_ABUSE,
                risk_level=RiskLevel.CRITICAL,
                decision=SafetyDecision.DENY,
                evidence="shell fork-bomb pattern detected",
                recommendation="Remove recursive background process creation.",
            )
        if (re.search(r"\bwhile\s+(?:true|:|1)\s*;?\s*do\b", context.shell_executable_text, re.IGNORECASE)
                and not re.search(r"\bbreak\b", context.shell_executable_text)):
            yield self._finding(
                rule_id="RES-INFINITE-LOOP",
                category=RiskCategory.RESOURCE_ABUSE,
                risk_level=RiskLevel.CRITICAL,
                decision=SafetyDecision.DENY,
                evidence="unbounded shell while loop has no break",
                recommendation="Add a bounded iteration count, cancellation check, or timeout.",
            )

        for command in context.shell_commands:
            tokens = list(command.argv)
            if not tokens:
                continue
            executable = command.executable
            if executable == "sleep" and len(tokens) > 1:
                seconds = _size_value(tokens[1].rstrip("s"))
                if seconds is not None and seconds > policy.long_sleep_seconds:
                    yield self._finding(
                        rule_id="RES-LONG-SLEEP",
                        category=RiskCategory.RESOURCE_ABUSE,
                        risk_level=RiskLevel.MEDIUM,
                        decision=SafetyDecision.NEEDS_HUMAN_REVIEW,
                        evidence=f"sleep duration {seconds}s exceeds the configured threshold",
                        recommendation="Use a short bounded wait.",
                        line_number=command.line_number,
                    )
            if executable in {"fallocate", "truncate"}:
                size: Optional[int] = None
                for index, token in enumerate(tokens[1:]):
                    if token in {"-l", "-s", "--size"} and index + 2 <= len(tokens):
                        size = _size_value(tokens[index + 2])
                if size is not None and size > policy.max_output_bytes:
                    yield self._finding(
                        rule_id="RES-LARGE-WRITE",
                        category=RiskCategory.RESOURCE_ABUSE,
                        risk_level=RiskLevel.HIGH,
                        decision=SafetyDecision.DENY,
                        evidence=f"file allocation {size} bytes exceeds the configured output maximum",
                        recommendation="Create only bounded workspace outputs.",
                        line_number=command.line_number,
                    )
            if executable == "dd":
                block_size = 512
                count: Optional[int] = None
                for token in tokens[1:]:
                    if token.startswith("bs="):
                        block_size = _size_value(token[3:]) or block_size
                    elif token.startswith("count="):
                        parsed = _size_value(token[6:])
                        count = parsed
                if count is not None and block_size * count > policy.max_output_bytes:
                    yield self._finding(
                        rule_id="RES-LARGE-WRITE",
                        category=RiskCategory.RESOURCE_ABUSE,
                        risk_level=RiskLevel.HIGH,
                        decision=SafetyDecision.DENY,
                        evidence="dd output size exceeds the configured output maximum",
                        recommendation="Reduce block size/count and keep output bounded.",
                        line_number=command.line_number,
                    )


class SensitiveDataRule(BaseSafetyRule):
    """Perform conservative static taint checks from secrets to output sinks."""

    rule_id = "SECRET"
    _ENV_CALLS = {"os.getenv", "os.environ.get", "environ.get"}
    _LOG_METHODS = {"critical", "debug", "error", "exception", "info", "log", "warning"}
    _NETWORK_PREFIXES = ("aiohttp.", "httpx.", "requests.", "socket.", "urllib.request.")

    def _source_description(
        self,
        node: ast.AST,
        context: SafetyRuleContext,
        environment_keys: set[str],
    ) -> tuple[bool, Optional[str], bool]:
        """Return ``(is_sensitive, source_name, contains_private_key)``."""

        private_key = False
        for child in ast.walk(node):
            if isinstance(child, ast.Constant) and isinstance(child.value, str):
                private_key = private_key or contains_private_key(child.value)
                if contains_secret_literal(child.value):
                    return True, None, private_key
            if isinstance(child, ast.Subscript) and dotted_name(child.value, _node_aliases(child,
                                                                                           context)) == "os.environ":
                key = literal_string(child.slice)
                if key and _is_sensitive_name(key, environment_keys):
                    return True, key, private_key
            if isinstance(child, ast.Call):
                call_name = resolved_call_name(child, context)
                if call_name in self._ENV_CALLS and child.args:
                    key = literal_string(child.args[0])
                    if key and _is_sensitive_name(key, environment_keys):
                        return True, key, private_key
        return False, None, private_key

    def _collect_taint(self, context: SafetyRuleContext) -> tuple[set[str], set[str], list[SafetyFinding]]:
        environment_keys = set(context.request.environment_keys)
        tainted = {
            node.arg
            for node in ast.walk(context.python_tree)
            if isinstance(node, ast.arg) and _is_sensitive_name(node.arg, environment_keys)
        }
        private_names: set[str] = set()
        source_findings: list[SafetyFinding] = []
        dependents: dict[str, list[str]] = defaultdict(list)

        def assigned_names(target: ast.AST) -> list[str]:
            if isinstance(target, ast.Name):
                return [target.id]
            if isinstance(target, (ast.Attribute, ast.Subscript)):
                return assigned_names(target.value)
            if isinstance(target, (ast.List, ast.Tuple)):
                return [name for element in target.elts for name in assigned_names(element)]
            return []

        for node in ast.walk(context.python_tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
                continue
            value = node.value
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = list(dict.fromkeys(name for target in targets for name in assigned_names(target)))
            dependencies = {child.id for child in ast.walk(value) if isinstance(child, ast.Name)}
            for dependency in dependencies:
                dependents[dependency].extend(names)
            sensitive, source_name, private_key = self._source_description(value, context, environment_keys)
            for name in names:
                if sensitive or _is_sensitive_name(name, environment_keys):
                    tainted.add(name)
                if private_key:
                    private_names.add(name)
            if source_name:
                source_findings.append(
                    self._finding(
                        rule_id="SECRET-ENV-READ",
                        category=RiskCategory.SENSITIVE_DATA_EXPOSURE,
                        risk_level=RiskLevel.MEDIUM,
                        decision=SafetyDecision.NEEDS_HUMAN_REVIEW,
                        evidence=f"sensitive environment key is read: {source_name}",
                        recommendation="Inject only the minimum secret and never return or persist its value.",
                        node=node,
                    ))
            if private_key:
                source_findings.append(
                    self._finding(
                        rule_id="SECRET-PRIVATE-KEY",
                        category=RiskCategory.SENSITIVE_DATA_EXPOSURE,
                        risk_level=RiskLevel.HIGH,
                        decision=SafetyDecision.NEEDS_HUMAN_REVIEW,
                        evidence="private-key material is embedded in the script [REDACTED_PRIVATE_KEY]",
                        recommendation="Load private keys from a managed secret provider and do not expose them.",
                        node=node,
                    ))

        queue = deque(tainted)
        while queue:
            source = queue.popleft()
            for target in dependents.get(source, []):
                if target not in tainted:
                    tainted.add(target)
                    queue.append(target)
        private_queue = deque(private_names)
        while private_queue:
            source = private_queue.popleft()
            for target in dependents.get(source, []):
                if target not in private_names:
                    private_names.add(target)
                    private_queue.append(target)
        return tainted, private_names, source_findings

    def _is_sink(self, node: ast.Call, context: SafetyRuleContext) -> Optional[str]:
        name = resolved_call_name(node, context)
        if name in {"builtins.print", "print", "pprint.pprint"}:
            return "standard output"
        if isinstance(node.func, ast.Attribute) and node.func.attr in self._LOG_METHODS:
            return "logging"
        if isinstance(node.func, ast.Attribute) and node.func.attr in {
                "send", "sendall", "write", "write_bytes", "write_text", "writelines"
        }:
            return "file or network output"
        if name.startswith(self._NETWORK_PREFIXES):
            return "network request"
        if name in {"json.dump", "pickle.dump", "yaml.dump"}:
            return "file output"
        return None

    def _scan_python(self, context: SafetyRuleContext) -> Iterable[SafetyFinding]:
        tainted, _, source_findings = self._collect_taint(context)
        yield from source_findings
        environment_keys = set(context.request.environment_keys)
        function_defs = {
            function.name: function
            for function in ast.walk(context.python_tree)
            if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        parameter_names = {
            name: [
                *(argument.arg for argument in function.args.posonlyargs),
                *(argument.arg for argument in function.args.args),
                *(argument.arg for argument in function.args.kwonlyargs),
            ]
            for name, function in function_defs.items()
        }

        def expression_names(node: ast.AST) -> set[str]:
            return {child.id for child in ast.walk(node) if isinstance(child, ast.Name)}

        def call_name(node: ast.Call) -> str:
            return dotted_name(node.func, _node_aliases(node, context))

        def call_arguments(node: ast.Call, function_name: str) -> dict[str, ast.AST]:
            names = parameter_names.get(function_name, [])
            arguments = {names[index]: value for index, value in enumerate(node.args) if index < len(names)}
            arguments.update({keyword.arg: keyword.value for keyword in node.keywords if keyword.arg in names})
            return arguments

        defaults: dict[str, dict[str, ast.AST]] = {}
        for name, function in function_defs.items():
            positional = [*function.args.posonlyargs, *function.args.args]
            positional_defaults = {
                argument.arg: value
                for argument, value in zip(positional[-len(function.args.defaults):], function.args.defaults)
            } if function.args.defaults else {}
            keyword_defaults = {
                argument.arg: value
                for argument, value in zip(function.args.kwonlyargs, function.args.kw_defaults) if value is not None
            }
            defaults[name] = {**positional_defaults, **keyword_defaults}

        function_sink_params: dict[str, set[str]] = {name: set() for name in function_defs}
        for name, function in function_defs.items():
            parameters = set(parameter_names[name])
            for child in ast.walk(function):
                if not isinstance(child, ast.Call) or not self._is_sink(child, context):
                    continue
                arguments = [*child.args, *(keyword.value for keyword in child.keywords)]
                referenced = {item for argument in arguments for item in expression_names(argument)}
                function_sink_params[name].update(parameters & referenced)

        changed = True
        while changed:
            changed = False
            for name, function in function_defs.items():
                parameters = set(parameter_names[name])
                for child in ast.walk(function):
                    if not isinstance(child, ast.Call):
                        continue
                    callee = call_name(child)
                    arguments = call_arguments(child, callee)
                    for sink_parameter in function_sink_params.get(callee, set()):
                        argument = arguments.get(sink_parameter) or defaults.get(callee, {}).get(sink_parameter)
                        if argument is None:
                            continue
                        forwarded = parameters & expression_names(argument)
                        if not forwarded <= function_sink_params[name]:
                            function_sink_params[name].update(forwarded)
                            changed = True

        secret_return_functions: set[str] = set()
        for name, function in function_defs.items():
            for child in ast.walk(function):
                if not isinstance(child, ast.Return) or child.value is None:
                    continue
                if (self._source_description(child.value, context, environment_keys)[0]
                        or _expression_has_secret_literal(child.value)):
                    secret_return_functions.add(name)
                    break
        changed = True
        while changed:
            changed = False
            for name, function in function_defs.items():
                if name in secret_return_functions:
                    continue
                returns_secret = any(
                    isinstance(child, ast.Call) and call_name(child) in secret_return_functions
                    for returned in ast.walk(function)
                    if isinstance(returned, ast.Return) and returned.value is not None
                    for child in ast.walk(returned.value))
                if returns_secret:
                    secret_return_functions.add(name)
                    changed = True

        def expression_is_sensitive(node: Optional[ast.AST]) -> bool:
            if node is None:
                return False
            return (_expression_contains_name(node, tainted) or _expression_has_secret_literal(node)
                    or self._source_description(node, context, environment_keys)[0] or any(
                        isinstance(child, ast.Call) and call_name(child) in secret_return_functions
                        for child in ast.walk(node)))

        for node in ast.walk(context.python_tree):
            if not isinstance(node, ast.Call):
                continue
            local_name = call_name(node)
            if local_name in function_sink_params:
                arguments = call_arguments(node, local_name)
                exposed_parameters = {
                    name
                    for name in function_sink_params[local_name]
                    if expression_is_sensitive(arguments.get(name) or defaults.get(local_name, {}).get(name))
                }
                if exposed_parameters:
                    yield self._finding(
                        rule_id="SECRET-EXPOSURE",
                        category=RiskCategory.SENSITIVE_DATA_EXPOSURE,
                        risk_level=RiskLevel.CRITICAL,
                        decision=SafetyDecision.DENY,
                        evidence="function forwards sensitive data to an output sink",
                        recommendation="Remove secrets from function arguments that reach output sinks.",
                        node=node,
                    )
            sink = self._is_sink(node, context)
            if not sink:
                continue
            sensitive_names = sorted({
                child.id
                for argument in [*node.args, *(keyword.value for keyword in node.keywords)]
                for child in ast.walk(argument) if isinstance(child, ast.Name) and child.id in tainted
            })
            direct_sensitive = any(
                expression_is_sensitive(argument)
                for argument in [*node.args, *(keyword.value for keyword in node.keywords)])
            if sensitive_names or direct_sensitive:
                variables = ", ".join(sensitive_names[:4]) if sensitive_names else "redacted secret material"
                yield self._finding(
                    rule_id="SECRET-EXPOSURE",
                    category=RiskCategory.SENSITIVE_DATA_EXPOSURE,
                    risk_level=RiskLevel.CRITICAL,
                    decision=SafetyDecision.DENY,
                    evidence=f"{sink} receives sensitive data from: {variables}",
                    recommendation="Remove the secret from logs, files, outputs, and network payloads.",
                    node=node,
                )

    def _scan_bash(self, context: SafetyRuleContext) -> Iterable[SafetyFinding]:
        environment_keys = set(context.request.environment_keys)
        tainted_names = {key for key in environment_keys if _is_sensitive_name(key, environment_keys)}
        for command in context.shell_commands:
            for assignment in command.assignments:
                name, _, value = assignment.partition("=")
                if _is_sensitive_name(name, environment_keys) or contains_secret_literal(value):
                    tainted_names.add(name)
            if not command.argv:
                continue
            executable = command.executable
            arguments = list(command.argv[1:])
            if executable in {"declare", "export"}:
                for argument in arguments:
                    name, separator, value = argument.partition("=")
                    if separator and (_is_sensitive_name(name, environment_keys) or contains_secret_literal(value)):
                        tainted_names.add(name)

            dumped_names: set[str] = set()
            positional = [argument for argument in arguments if not argument.startswith("-")]
            if executable == "env":
                dumped_names.update(tainted_names)
            elif executable == "printenv":
                if positional:
                    dumped_names.update(name for name in positional
                                        if name in tainted_names or _is_sensitive_name(name, environment_keys))
                else:
                    dumped_names.update(tainted_names)
            elif executable == "set" and not arguments:
                dumped_names.update(tainted_names)
            elif executable == "export" and (not arguments or "-p" in arguments):
                dumped_names.update(tainted_names)
            elif executable == "declare":
                option_letters = "".join(argument.lstrip("-") for argument in arguments if argument.startswith("-"))
                if not positional and (not arguments or "p" in option_letters or "x" in option_letters):
                    dumped_names.update(tainted_names)
                elif "p" in option_letters:
                    dumped_names.update(name for name in positional
                                        if name in tainted_names or _is_sensitive_name(name, environment_keys))
            if dumped_names:
                variables = ", ".join(sorted(dumped_names)[:4])
                yield self._finding(
                    rule_id="SECRET-EXPOSURE",
                    category=RiskCategory.SENSITIVE_DATA_EXPOSURE,
                    risk_level=RiskLevel.CRITICAL,
                    decision=SafetyDecision.DENY,
                    evidence=f"shell environment dump exposes sensitive variables: {variables}",
                    recommendation="Do not print the process environment; select only non-sensitive variables.",
                    line_number=command.line_number,
                )
            sink = executable in {"echo", "logger", "printf"
                                  } or executable in {"curl", "nc", "netcat", "rsync", "scp", "wget"}
            sink = sink or any(operator in {">", ">>"} for operator, _ in command.redirects)
            if not sink:
                continue
            referenced = set()
            literal_secret = False
            for token in command.argv[1:]:
                referenced.update(match.group(1) or match.group(2) for match in _SHELL_VARIABLE_RE.finditer(token))
                literal_secret = literal_secret or contains_secret_literal(token)
            exposed = sorted(name for name in referenced
                             if name in tainted_names or _is_sensitive_name(name, environment_keys))
            if exposed or literal_secret:
                variables = ", ".join(exposed[:4]) if exposed else "redacted secret material"
                yield self._finding(
                    rule_id="SECRET-EXPOSURE",
                    category=RiskCategory.SENSITIVE_DATA_EXPOSURE,
                    risk_level=RiskLevel.CRITICAL,
                    decision=SafetyDecision.DENY,
                    evidence=f"shell output sink receives sensitive data from: {variables}",
                    recommendation="Remove the secret from logs, files, outputs, and network payloads.",
                    line_number=command.line_number,
                )

    def scan(self, context: SafetyRuleContext, policy: ToolSafetyPolicy) -> Iterable[SafetyFinding]:
        del policy
        if context.python_tree is not None:
            yield from self._scan_python(context)
        yield from self._scan_bash(context)


DEFAULT_RULES: tuple[SafetyRule, ...] = (
    PolicyLimitsRule(),
    DangerousFileRule(),
    NetworkRule(),
    ProcessRule(),
    DependencyRule(),
    ResourceRule(),
    SensitiveDataRule(),
)

__all__ = [
    "BaseSafetyRule",
    "DEFAULT_RULES",
    "SafetyRule",
    "SafetyRuleContext",
    "ShellCommand",
    "collect_python_metadata",
    "parse_bash",
    "shell_executable_text",
]
