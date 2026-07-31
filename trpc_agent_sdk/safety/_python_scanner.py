# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Single-parse AST fact extraction for Python source."""

from __future__ import annotations

import ast
import base64
import binascii
import re
from dataclasses import dataclass
from typing import Any
from typing import Optional

from ._matchers import domain_matches
from ._matchers import path_matches
from ._models import RiskLevel
from ._models import SafetyCategory
from ._models import SafetyFinding
from ._policy import SafetyPolicy
from ._redaction import redact_text
from ._rule import NestedCandidate
from ._rule import ScanContext


@dataclass(frozen=True)
class StaticValue:
    """A bounded static value with explicit knowledge state."""

    state: str
    value: Any = None

    @classmethod
    def known(cls, value: Any) -> "StaticValue":
        return cls("known", value)

    @classmethod
    def partial(cls, value: Any) -> "StaticValue":
        return cls("partially_known", value)

    @classmethod
    def unknown(cls) -> "StaticValue":
        return cls("unknown")


@dataclass(frozen=True)
class PythonCallFact:
    """Resolved call retained for custom rules and tests."""

    qualified_name: str
    line_number: int
    column_number: int
    positional: tuple[StaticValue, ...]
    keywords: tuple[tuple[str, StaticValue], ...]


_NETWORK_CALLS = {
    "requests.get",
    "requests.post",
    "requests.put",
    "requests.patch",
    "requests.delete",
    "requests.request",
    "httpx.get",
    "httpx.post",
    "httpx.put",
    "httpx.patch",
    "httpx.delete",
    "httpx.request",
    "aiohttp.ClientSession.get",
    "aiohttp.ClientSession.post",
    "aiohttp.ClientSession.request",
    "urllib.request.urlopen",
    "urllib.request.Request",
    "socket.create_connection",
    "socket.socket.connect",
}
_PROCESS_CALLS = {
    "subprocess.run",
    "subprocess.call",
    "subprocess.Popen",
    "subprocess.check_output",
    "subprocess.check_call",
    "os.system",
    "os.popen",
}
_DYNAMIC_CALLS = {"eval", "exec", "compile", "__import__"}
_SENSITIVE_HINTS = ("/.ssh", ".env", "credentials", "/.aws", "gcloud", "id_rsa", "private_key")


def _finding(
    rule_id: str,
    category: SafetyCategory,
    risk: RiskLevel,
    message: str,
    node: ast.AST,
    source: str,
    *,
    recommendation: str = "Review or remove the unsafe operation.",
    hard_deny: bool = False,
) -> SafetyFinding:
    lines = source.splitlines()
    line_number = getattr(node, "lineno", None)
    evidence = ""
    if line_number and line_number <= len(lines):
        evidence = redact_text(lines[line_number - 1].strip())
    return SafetyFinding(
        rule_id=rule_id,
        category=category,
        risk_level=risk,
        message=message,
        evidence=evidence,
        recommendation=recommendation,
        line_number=line_number,
        column_number=getattr(node, "col_offset", None),
        hard_deny=hard_deny,
    )


class _PythonAnalyzer(ast.NodeVisitor):
    """Build shared facts in one traversal of one already-parsed AST."""

    def __init__(self, source: str, policy: SafetyPolicy):
        self.source = source
        self.policy = policy
        self.aliases: dict[str, str] = {}
        self.shadowed_names: set[str] = set()
        self.constants: dict[str, StaticValue] = {}
        self.sensitive_names: set[str] = set()
        self.object_origins: dict[str, str] = {}
        self.findings: list[SafetyFinding] = []
        self.nested: list[NestedCandidate] = []
        self.calls: list[PythonCallFact] = []
        self.function_stack: list[str] = []

    def _name(self, node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            if node.id in self.shadowed_names:
                return ""
            return self.aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            base = self._name(node.value)
            if isinstance(node.value, ast.Name) and node.value.id in self.object_origins:
                base = self.object_origins[node.value.id]
            elif isinstance(node.value, ast.Call):
                origin = self._name(node.value.func)
                if origin in {
                        "pathlib.Path", "requests.Session", "httpx.Client", "aiohttp.ClientSession", "socket.socket"
                }:
                    base = origin
            return f"{base}.{node.attr}" if base else node.attr
        return ""

    def _static(self, node: Optional[ast.AST]) -> StaticValue:
        if node is None:
            return StaticValue.unknown()
        if isinstance(node, ast.Constant) and isinstance(node.value, (str, int, float, bool, type(None))):
            return StaticValue.known(node.value)
        if isinstance(node, ast.Name):
            return self.constants.get(node.id, StaticValue.unknown())
        if isinstance(node, (ast.List, ast.Tuple)):
            items = [self._static(item) for item in node.elts]
            if all(item.state == "known" for item in items):
                return StaticValue.known([item.value for item in items])
            return StaticValue.partial([item.value if item.state != "unknown" else "{?}" for item in items])
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left = self._static(node.left)
            right = self._static(node.right)
            if left.state == right.state == "known":
                try:
                    return StaticValue.known(left.value + right.value)
                except (TypeError, ValueError):
                    return StaticValue.unknown()
            if left.state != "unknown" or right.state != "unknown":
                left_text = left.value if left.state != "unknown" else "{?}"
                right_text = right.value if right.state != "unknown" else "{?}"
                return StaticValue.partial(f"{left_text}{right_text}")
        if isinstance(node, ast.JoinedStr):
            pieces: list[str] = []
            complete = True
            for value in node.values:
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    pieces.append(value.value)
                elif isinstance(value, ast.FormattedValue):
                    static = self._static(value.value)
                    if static.state == "known":
                        pieces.append(str(static.value))
                    else:
                        pieces.append("{?}")
                        complete = False
            text = "".join(pieces)
            return StaticValue.known(text) if complete else StaticValue.partial(text)
        return StaticValue.unknown()

    def _is_sensitive_path(self, value: str) -> bool:
        lowered = value.lower().replace("\\", "/")
        return path_matches(value, self.policy.sensitive_paths) or any(hint in lowered for hint in _SENSITIVE_HINTS)

    def _expr_has_sensitive_source(self, node: ast.AST) -> bool:
        if isinstance(node, ast.Name) and node.id in self.sensitive_names:
            return True
        if isinstance(node, ast.Subscript):
            base = self._name(node.value)
            return base in {"os.environ", "environ"}
        if isinstance(node, ast.Call):
            name = self._name(node.func)
            if name in {"os.getenv", "os.environ.get", "environ.get"}:
                return True
            if name.endswith(".read") and isinstance(node.func, ast.Attribute):
                return self._expr_has_sensitive_source(node.func.value)
            if name in {"open", "pathlib.Path"} and node.args:
                value = self._static(node.args[0])
                return value.state == "known" and isinstance(value.value, str) and self._is_sensitive_path(value.value)
        return any(self._expr_has_sensitive_source(child) for child in ast.iter_child_nodes(node))

    def _keyword(self, node: ast.Call, name: str) -> Optional[ast.AST]:
        return next((item.value for item in node.keywords if item.arg == name), None)

    def _command_value(self, node: ast.Call) -> StaticValue:
        command = node.args[0] if node.args else self._keyword(node, "args")
        return self._static(command)

    def _inspect_command(self, node: ast.Call, name: str) -> None:
        command = self._command_value(node)
        text = ""
        argv: list[str] = []
        if command.state == "known" and isinstance(command.value, str):
            text = command.value
        elif command.state == "known" and isinstance(command.value, list):
            argv = [str(item) for item in command.value]
            text = " ".join(argv)
        else:
            self.findings.append(
                _finding(
                    "PY.PROCESS.DYNAMIC_COMMAND",
                    SafetyCategory.PROCESS,
                    RiskLevel.MEDIUM,
                    "Process command cannot be resolved statically.",
                    node,
                    self.source,
                ))
            return
        lowered = text.lower()
        if re.search(r"(?:^|\s)(?:pip|pip3)(?:\s|$)", lowered) or re.search(r"python(?:3)?\s+-m\s+pip", lowered):
            self.findings.append(
                _finding(
                    "PY.DEPENDENCY.INSTALL",
                    SafetyCategory.DEPENDENCY,
                    RiskLevel.HIGH,
                    "Command invokes a package installer.",
                    node,
                    self.source,
                ))
        shell_value = self._static(self._keyword(node, "shell"))
        if shell_value.state == "known" and shell_value.value is True:
            self.findings.append(
                _finding(
                    "PY.PROCESS.SHELL_TRUE",
                    SafetyCategory.DYNAMIC_EXECUTION,
                    RiskLevel.HIGH,
                    "subprocess shell=True enables shell interpretation.",
                    node,
                    self.source,
                ))
        tokens = argv or re.findall(r"[^\s]+", text)
        if len(tokens) >= 3 and tokens[0].rsplit("/", 1)[-1] in {"bash", "sh", "dash", "zsh"} and tokens[1] == "-c":
            self.nested.append(NestedCandidate("shell", " ".join(tokens[2:]), getattr(node, "lineno", None), name))
        if len(tokens) >= 3 and tokens[0].rsplit("/", 1)[-1] in {"python", "python3"} and tokens[1] == "-c":
            self.nested.append(NestedCandidate("python", " ".join(tokens[2:]), getattr(node, "lineno", None), name))

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            local_name = alias.asname or alias.name.split(".", 1)[0]
            self.shadowed_names.discard(local_name)
            self.aliases[local_name] = alias.name

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        for alias in node.names:
            local_name = alias.asname or alias.name
            self.shadowed_names.discard(local_name)
            self.aliases[local_name] = f"{module}.{alias.name}" if module else alias.name

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.function_stack.append(node.name)
        argument_names = {arg.arg for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)}
        saved_aliases = {name: self.aliases.pop(name) for name in argument_names if name in self.aliases}
        saved_shadowed = set(self.shadowed_names)
        self.shadowed_names.update(argument_names)
        self.generic_visit(node)
        self.aliases.update(saved_aliases)
        self.shadowed_names = saved_shadowed
        self.function_stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Assign(self, node: ast.Assign) -> None:
        static = self._static(node.value)
        sensitive = self._expr_has_sensitive_source(node.value)
        origin = ""
        if isinstance(node.value, ast.Call):
            origin = self._name(node.value.func)
            if origin == "requests.Session":
                origin = "requests.Session"
            elif origin == "httpx.Client":
                origin = "httpx.Client"
            elif origin == "aiohttp.ClientSession":
                origin = "aiohttp.ClientSession"
            elif origin == "socket.socket":
                origin = "socket.socket"
            elif origin == "pathlib.Path":
                origin = "pathlib.Path"
        for target in node.targets:
            if isinstance(target, ast.Name):
                self.aliases.pop(target.id, None)
                self.shadowed_names.add(target.id)
                if static.state != "unknown":
                    self.constants[target.id] = static
                else:
                    self.constants.pop(target.id, None)
                if sensitive:
                    self.sensitive_names.add(target.id)
                if origin:
                    self.object_origins[target.id] = origin
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if isinstance(node.target, ast.Name) and node.value is not None:
            self.aliases.pop(node.target.id, None)
            self.shadowed_names.add(node.target.id)
            static = self._static(node.value)
            if static.state != "unknown":
                self.constants[node.target.id] = static
            if self._expr_has_sensitive_source(node.value):
                self.sensitive_names.add(node.target.id)
        self.generic_visit(node)

    def visit_While(self, node: ast.While) -> None:
        value = self._static(node.test)
        if value.state == "known" and value.value is True:
            self.findings.append(
                _finding(
                    "PY.RESOURCE.INFINITE_LOOP",
                    SafetyCategory.RESOURCE,
                    RiskLevel.HIGH,
                    "Statically unconditional loop may not terminate.",
                    node,
                    self.source,
                ))
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        name = self._name(node.func)
        positional = tuple(self._static(arg) for arg in node.args)
        keywords = tuple((item.arg or "**", self._static(item.value)) for item in node.keywords)
        self.calls.append(
            PythonCallFact(
                qualified_name=name,
                line_number=getattr(node, "lineno", 1),
                column_number=getattr(node, "col_offset", 0),
                positional=positional,
                keywords=keywords,
            ))

        if name in {"open", "pathlib.Path"} and node.args:
            path = self._static(node.args[0])
            if path.state == "known" and isinstance(path.value, str) and self._is_sensitive_path(path.value):
                self.findings.append(
                    _finding(
                        "PY.SECRET.SENSITIVE_PATH_READ",
                        SafetyCategory.SECRET,
                        RiskLevel.HIGH,
                        "Code references a sensitive credential path.",
                        node,
                        self.source,
                        hard_deny=True,
                    ))

            if name == "open":
                mode_node = node.args[1] if len(node.args) > 1 else self._keyword(node, "mode")
                if mode_node is not None:
                    mode = self._static(mode_node)
                    if mode.state != "known":
                        self.findings.append(
                            _finding(
                                "PY.FILESYSTEM.DYNAMIC_MODE",
                                SafetyCategory.FILESYSTEM,
                                RiskLevel.MEDIUM,
                                "File open mode cannot be resolved statically.",
                                node,
                                self.source,
                            ))
                    elif isinstance(mode.value, str) and any(flag in mode.value for flag in "wax+"):
                        self.findings.append(
                            _finding(
                                "PY.FILESYSTEM.WRITE",
                                SafetyCategory.FILESYSTEM,
                                RiskLevel.MEDIUM,
                                "Code opens a file in a writing mode.",
                                node,
                                self.source,
                            ))
            if path.state != "known":
                self.findings.append(
                    _finding(
                        "PY.FILESYSTEM.DYNAMIC_PATH",
                        SafetyCategory.FILESYSTEM,
                        RiskLevel.MEDIUM,
                        "File path cannot be resolved statically.",
                        node,
                        self.source,
                    ))

        if name.endswith((".write_text", ".write_bytes")):
            self.findings.append(
                _finding(
                    "PY.FILESYSTEM.WRITE",
                    SafetyCategory.FILESYSTEM,
                    RiskLevel.MEDIUM,
                    "Code writes a filesystem path.",
                    node,
                    self.source,
                ))
        if name in {"os.remove", "os.unlink", "os.rmdir", "shutil.rmtree"} or name.endswith((".unlink", ".rmdir")):
            target = self._static(node.args[0] if node.args else None)
            protected = target.state != "known" or (isinstance(target.value, str)
                                                    and path_matches(target.value, self.policy.forbidden_paths))
            self.findings.append(
                _finding(
                    "PY.FILESYSTEM.DESTRUCTIVE_DELETE",
                    SafetyCategory.FILESYSTEM,
                    RiskLevel.CRITICAL if protected else RiskLevel.HIGH,
                    "Code performs a destructive filesystem delete.",
                    node,
                    self.source,
                    hard_deny=protected,
                ))

        network_name = name
        if name.startswith("requests.Session."):
            network_name = "requests." + name.rsplit(".", 1)[-1]
        elif name.startswith("httpx.Client."):
            network_name = "httpx." + name.rsplit(".", 1)[-1]
        if network_name in _NETWORK_CALLS or name.endswith(".connect") and name.startswith("socket.socket"):
            target_index = 1 if network_name.endswith(".request") and len(node.args) > 1 else 0
            target_node = node.args[target_index] if len(node.args) > target_index else self._keyword(node, "url")
            target = self._static(target_node)
            if target.state == "known" and isinstance(target.value, (str, list, tuple)):
                value = target.value[0] if isinstance(target.value, (list, tuple)) and target.value else target.value
                if not isinstance(value, str) or not domain_matches(value, self.policy.whitelisted_domains):
                    self.findings.append(
                        _finding(
                            "PY.NETWORK.NON_WHITELISTED",
                            SafetyCategory.NETWORK,
                            RiskLevel.HIGH,
                            "Network target is not on the domain allowlist.",
                            node,
                            self.source,
                            hard_deny=True,
                        ))
            else:
                self.findings.append(
                    _finding(
                        "PY.NETWORK.DYNAMIC_TARGET",
                        SafetyCategory.NETWORK,
                        RiskLevel.MEDIUM,
                        "Network target cannot be resolved statically.",
                        node,
                        self.source,
                    ))
            if any(self._expr_has_sensitive_source(arg) for arg in node.args[1:]) or any(
                    self._expr_has_sensitive_source(item.value) for item in node.keywords):
                self.findings.append(
                    _finding(
                        "PY.SECRET.EXFILTRATION",
                        SafetyCategory.SECRET,
                        RiskLevel.CRITICAL,
                        "Sensitive source flows to a network sink.",
                        node,
                        self.source,
                        hard_deny=True,
                    ))

        if name in _PROCESS_CALLS or name.startswith("os.exec"):
            self.findings.append(
                _finding(
                    "PY.PROCESS.SPAWN",
                    SafetyCategory.PROCESS,
                    RiskLevel.HIGH,
                    "Code starts or replaces a process.",
                    node,
                    self.source,
                ))
            self._inspect_command(node, name)

        if name in {"pip.main", "pip._internal.main"}:
            self.findings.append(
                _finding(
                    "PY.DEPENDENCY.INSTALL",
                    SafetyCategory.DEPENDENCY,
                    RiskLevel.HIGH,
                    "Code invokes the pip package installer.",
                    node,
                    self.source,
                ))

        if name in _DYNAMIC_CALLS:
            self.findings.append(
                _finding(
                    "PY.DYNAMIC.EXECUTION",
                    SafetyCategory.DYNAMIC_EXECUTION,
                    RiskLevel.HIGH,
                    f"Code invokes dynamic execution primitive {name}.",
                    node,
                    self.source,
                ))
            if name in {"eval", "exec"} and node.args:
                nested = self._static(node.args[0])
                if nested.state == "known" and isinstance(nested.value, str):
                    self.nested.append(NestedCandidate("python", nested.value, getattr(node, "lineno", None), name))

        if name in {"os.fork", "multiprocessing.Process", "threading.Thread", "asyncio.create_task"}:
            risk = RiskLevel.CRITICAL if name == "os.fork" else RiskLevel.MEDIUM
            self.findings.append(
                _finding(
                    "PY.RESOURCE.PROCESS_OR_TASK",
                    SafetyCategory.RESOURCE,
                    risk,
                    "Code creates a process, thread, or background task.",
                    node,
                    self.source,
                    hard_deny=name == "os.fork",
                ))

        if name in {"time.sleep", "asyncio.sleep"} and node.args:
            delay = self._static(node.args[0])
            if delay.state == "known" and isinstance(delay.value, (int, float)) and delay.value > 60:
                self.findings.append(
                    _finding(
                        "PY.RESOURCE.LONG_SLEEP",
                        SafetyCategory.RESOURCE,
                        RiskLevel.MEDIUM,
                        "Sleep duration exceeds the static review threshold.",
                        node,
                        self.source,
                    ))

        if name == "print" and any(self._expr_has_sensitive_source(arg) for arg in node.args):
            self.findings.append(
                _finding(
                    "PY.SECRET.OUTPUT",
                    SafetyCategory.SECRET,
                    RiskLevel.HIGH,
                    "Sensitive source flows to standard output.",
                    node,
                    self.source,
                    hard_deny=True,
                ))

        if name in {"base64.b64decode", "base64.urlsafe_b64decode"} and node.args:
            encoded = self._static(node.args[0])
            if encoded.state == "known" and isinstance(encoded.value, (str, bytes)):
                try:
                    decoded = base64.b64decode(encoded.value, validate=True)
                    if len(decoded) <= self.policy.nested.max_base64_decode_bytes:
                        text = decoded.decode("utf-8")
                        language = "python" if re.search(r"\b(?:import|def|print)\b", text) else "shell"
                        self.nested.append(
                            NestedCandidate(language, text, getattr(node, "lineno", None), "base64 decode"))
                except (binascii.Error, UnicodeDecodeError, ValueError):
                    pass

        if self.function_stack and name == self.function_stack[-1]:
            self.findings.append(
                _finding(
                    "PY.RESOURCE.RECURSION",
                    SafetyCategory.RESOURCE,
                    RiskLevel.MEDIUM,
                    "Function contains a direct recursive call.",
                    node,
                    self.source,
                ))
        self.generic_visit(node)


def build_python_context(source: str, policy: SafetyPolicy) -> ScanContext:
    """Parse Python exactly once and return one shared immutable context."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, MemoryError) as error:
        line = getattr(error, "lineno", None)
        finding = SafetyFinding(
            rule_id="PY.ANALYSIS.PARSE_FAILURE",
            category=SafetyCategory.ANALYSIS,
            risk_level=RiskLevel.MEDIUM,
            message="Python source could not be parsed.",
            evidence="<parse failure>",
            recommendation="Review the source before execution.",
            line_number=line if isinstance(line, int) and line > 0 else None,
        )
        return ScanContext(
            language="python",
            source=source,
            candidate_findings=(finding, ),
            analysis_complete=False,
            failure_code="python_parse_failure",
            parse_count=1,
        )
    analyzer = _PythonAnalyzer(source, policy)
    analyzer.visit(tree)
    return ScanContext(
        language="python",
        source=source,
        candidate_findings=tuple(analyzer.findings),
        nested_candidates=tuple(analyzer.nested),
        details=tuple(analyzer.calls),
        parse_count=1,
    )
