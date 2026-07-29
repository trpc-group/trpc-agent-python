#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

"""Full-file AST confirmation for deterministic Python security findings.

The diff parser owns syntax validation and records a sanitized warning when a
complete Python file cannot be parsed.  This module never attempts AST parsing
for hunk-only inputs, deleted files, or parser-downgraded files.  A changed-line
review may only report an AST node that intersects a newly changed line.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import Dict, List, Set, Tuple

from .diff_parser import ChangeSet
from .rule_engine import ReviewRule, RuleMatch
from .secret_rules import redact_text


_SQL_KEYWORDS = re.compile(r"\b(?:select|insert|update|delete)\b", re.IGNORECASE)


@dataclass(frozen=True)
class _ASTCandidate:
    """One AST-confirmed dangerous construct before review-scope filtering."""

    rule_id: str
    severity: str
    title: str
    start_line: int
    end_line: int


class _SecurityVisitor(ast.NodeVisitor):
    """Collect the A4 security patterns from an already validated AST."""

    def __init__(self) -> None:
        """初始化候选集合以及受支持的导入别名映射。"""

        self.candidates: List[_ASTCandidate] = []
        self.module_alias_scopes: List[Dict[str, str]] = [{}]
        self.callable_alias_scopes: List[Dict[str, str]] = [{}]
        self.shadowed_name_scopes: List[Set[str]] = [set()]

    def visit_Import(self, node: ast.Import) -> None:
        """记录 builtins、os 和 subprocess 的显式模块别名。"""

        for alias in node.names:
            if alias.name not in {"builtins", "os", "subprocess"}:
                continue
            local_name = alias.asname or alias.name
            self._bind_module_alias(local_name, alias.name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """记录可确定来源的危险函数导入别名。"""

        supported = {
            ("builtins", "eval"): "builtins.eval",
            ("builtins", "exec"): "builtins.exec",
            ("os", "system"): "os.system",
            ("os", "popen"): "os.popen",
            ("subprocess", "getoutput"): "subprocess.getoutput",
            (
                "subprocess",
                "getstatusoutput",
            ): "subprocess.getstatusoutput",
        }
        for alias in node.names:
            canonical = supported.get((node.module, alias.name))
            if canonical is not None:
                self._bind_callable_alias(
                    alias.asname or alias.name,
                    canonical,
                )

    def visit_Assign(self, node: ast.Assign) -> None:
        """按 Python 求值顺序访问赋值，并使重绑定名称失效。"""

        self.visit(node.value)
        for target in node.targets:
            self._shadow_target(target)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        """访问带类型标注的赋值，并使重绑定名称失效。"""

        if node.value is not None:
            self.visit(node.value)
        self._shadow_target(node.target)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """在独立函数作用域中访问同步函数体。"""

        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """在独立函数作用域中访问异步函数体。"""

        self._visit_function(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        """在独立参数作用域中访问 lambda 表达式。"""

        self._push_scope(self._argument_names(node.args))
        self.visit(node.body)
        self._pop_scope()

    def visit_Call(self, node: ast.Call) -> None:
        """检查一次调用中的动态执行、命令执行和 shell 参数。"""

        self._record_sql_format(node)
        self._record_dynamic_code(node)
        self._record_os_system(node)
        self._record_subprocess_shell(node)
        self.generic_visit(node)

    def visit_BinOp(self, node: ast.BinOp) -> None:
        """检查通过加法或百分号格式化动态构造的 SQL。"""

        if (
            isinstance(node.op, (ast.Add, ast.Mod))
            and self._contains_sql_text(node)
            and self._contains_dynamic_value(node)
        ):
            self._record(
                node,
                "security.sql-interpolation",
                "high",
                "SQL is built with dynamic string interpolation",
            )
        self.generic_visit(node)

    def visit_JoinedStr(self, node: ast.JoinedStr) -> None:
        """记录包含 SQL 关键字和插值表达式的 f-string 候选项。"""

        constants = [
            value.value
            for value in node.values
            if isinstance(value, ast.Constant) and isinstance(value.value, str)
        ]
        if any(_SQL_KEYWORDS.search(value) for value in constants) and any(
            isinstance(value, ast.FormattedValue) for value in node.values
        ):
            self._record(
                node,
                "security.sql-fstring",
                "high",
                "SQL is built with an interpolated f-string",
            )
        self.generic_visit(node)

    def _record_dynamic_code(self, node: ast.Call) -> None:
        """记录直接、限定名或静态 getattr 形式的 eval/exec 调用。"""

        canonical = self._canonical_callable(node.func)
        if canonical in {"eval", "builtins.eval"}:
            self._record(
                node,
                "security.dynamic-eval",
                "critical",
                "Dynamic eval can execute untrusted input",
            )
        elif canonical in {"exec", "builtins.exec"}:
            self._record(
                node,
                "security.dynamic-exec",
                "critical",
                "Dynamic exec can execute untrusted input",
            )

    def _record_sql_format(self, node: ast.Call) -> None:
        """记录在 SQL 字符串上调用 format 的动态插值。"""

        if not (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "format"
            and self._contains_sql_text(node.func.value)
            and (node.args or node.keywords)
        ):
            return
        self._record(
            node,
            "security.sql-interpolation",
            "high",
            "SQL is built with dynamic string interpolation",
        )

    def _record_os_system(self, node: ast.Call) -> None:
        """记录 os.system、os.popen 及其显式导入别名。"""

        canonical = self._canonical_callable(node.func)
        if canonical == "os.system":
            self._record(
                node,
                "security.os-system",
                "high",
                "os.system invokes a shell command",
            )
        elif canonical == "os.popen":
            self._record(
                node,
                "security.os-popen",
                "high",
                "os.popen invokes a shell command",
            )

    def _record_subprocess_shell(self, node: ast.Call) -> None:
        """记录隐式 shell API 以及显式 shell=True 的 subprocess 调用。"""

        canonical = self._canonical_callable(node.func)
        if canonical in {
            "subprocess.getoutput",
            "subprocess.getstatusoutput",
        }:
            self._record(
                node,
                "security.subprocess-shell-command",
                "high",
                "subprocess helper executes through a shell",
            )
            return
        if canonical not in {
            "subprocess.run",
            "subprocess.call",
            "subprocess.check_call",
            "subprocess.check_output",
            "subprocess.Popen",
        }:
            return
        if any(
            keyword.arg == "shell"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value is True
            for keyword in node.keywords
        ):
            self._record(
                node,
                "security.subprocess-shell-true",
                "high",
                "subprocess executes with shell=True",
            )

    def _canonical_callable(self, node: ast.AST) -> str | None:
        """把受支持的调用表达式解析为稳定的标准名称。"""

        if isinstance(node, ast.Name):
            imported = self._resolve_alias(
                node.id,
                self.callable_alias_scopes,
            )
            if imported is not None:
                return imported
            if node.id in {"eval", "exec"} and not self._is_bound(node.id):
                return node.id
            return None
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            module = self._resolve_alias(
                node.value.id,
                self.module_alias_scopes,
            )
            if module in {"builtins", "os", "subprocess"}:
                return f"{module}.{node.attr}"
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and not self._is_bound("getattr")
            and len(node.args) >= 2
            and isinstance(node.args[0], ast.Name)
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            module = self._resolve_alias(
                node.args[0].id,
                self.module_alias_scopes,
            )
            if module == "builtins" and node.args[1].value in {"eval", "exec"}:
                return f"builtins.{node.args[1].value}"
        return None

    def _visit_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        """按外层求值和内层函数体两个作用域访问函数定义。"""

        for decorator in node.decorator_list:
            self.visit(decorator)
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default is not None:
                self.visit(default)
        if node.returns is not None:
            self.visit(node.returns)
        self._shadow_name(node.name)
        self._push_scope(self._argument_names(node.args))
        for statement in node.body:
            self.visit(statement)
        self._pop_scope()

    @staticmethod
    def _argument_names(arguments: ast.arguments) -> Set[str]:
        """返回函数签名中会遮蔽外层名称的全部参数名。"""

        names = {
            argument.arg
            for argument in (
                *arguments.posonlyargs,
                *arguments.args,
                *arguments.kwonlyargs,
            )
        }
        if arguments.vararg is not None:
            names.add(arguments.vararg.arg)
        if arguments.kwarg is not None:
            names.add(arguments.kwarg.arg)
        return names

    def _push_scope(self, shadowed_names: Set[str]) -> None:
        """压入新的词法作用域和初始遮蔽名称。"""

        self.module_alias_scopes.append({})
        self.callable_alias_scopes.append({})
        self.shadowed_name_scopes.append(set(shadowed_names))

    def _pop_scope(self) -> None:
        """弹出当前词法作用域。"""

        self.module_alias_scopes.pop()
        self.callable_alias_scopes.pop()
        self.shadowed_name_scopes.pop()

    def _bind_module_alias(self, local_name: str, canonical: str) -> None:
        """在当前作用域绑定一个受支持模块别名。"""

        self.shadowed_name_scopes[-1].discard(local_name)
        self.callable_alias_scopes[-1].pop(local_name, None)
        self.module_alias_scopes[-1][local_name] = canonical

    def _bind_callable_alias(self, local_name: str, canonical: str) -> None:
        """在当前作用域绑定一个受支持危险函数别名。"""

        self.shadowed_name_scopes[-1].discard(local_name)
        self.module_alias_scopes[-1].pop(local_name, None)
        self.callable_alias_scopes[-1][local_name] = canonical

    def _shadow_target(self, target: ast.AST) -> None:
        """把赋值目标中的名称标为当前作用域重绑定。"""

        if isinstance(target, ast.Name):
            self._shadow_name(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for element in target.elts:
                self._shadow_target(element)

    def _shadow_name(self, name: str) -> None:
        """使当前作用域中的模块或函数别名失效。"""

        self.module_alias_scopes[-1].pop(name, None)
        self.callable_alias_scopes[-1].pop(name, None)
        self.shadowed_name_scopes[-1].add(name)

    def _resolve_alias(
        self,
        name: str,
        scopes: List[Dict[str, str]],
    ) -> str | None:
        """从内到外解析别名，并在遇到重绑定时停止。"""

        for index in range(len(scopes) - 1, -1, -1):
            if name in self.shadowed_name_scopes[index]:
                return None
            canonical = scopes[index].get(name)
            if canonical is not None:
                return canonical
        return None

    def _is_bound(self, name: str) -> bool:
        """判断名称是否被显式导入、赋值或参数绑定。"""

        return any(
            name in shadowed
            or name in self.module_alias_scopes[index]
            or name in self.callable_alias_scopes[index]
            for index, shadowed in enumerate(self.shadowed_name_scopes)
        )

    @staticmethod
    def _contains_sql_text(node: ast.AST) -> bool:
        """判断表达式是否包含带 SQL 动词的字符串常量。"""

        return any(
            isinstance(child, ast.Constant)
            and isinstance(child.value, str)
            and _SQL_KEYWORDS.search(child.value)
            for child in ast.walk(node)
        )

    @staticmethod
    def _contains_dynamic_value(node: ast.AST) -> bool:
        """判断字符串二元表达式是否混入了非常量值。"""

        return any(
            isinstance(child, (ast.Name, ast.Attribute, ast.Call, ast.Subscript))
            for child in ast.walk(node)
        )

    def _record(self, node: ast.AST, rule_id: str, severity: str, title: str) -> None:
        """将 AST 命中转换为带源码范围的内部安全候选项。"""

        start_line = getattr(node, "lineno", 0)
        end_line = getattr(node, "end_lineno", start_line) or start_line
        self.candidates.append(
            _ASTCandidate(
                rule_id=rule_id,
                severity=severity,
                title=title,
                start_line=start_line,
                end_line=end_line,
            )
        )


def _ast_candidates(full_text: str) -> Tuple[_ASTCandidate, ...]:
    """防御性解析一个完整文件，并返回稳定的安全候选集合。"""

    try:
        tree = ast.parse(full_text)
    except SyntaxError:
        # The parser normally prevents this path and records the warning there.
        return ()
    visitor = _SecurityVisitor()
    visitor.visit(tree)
    return tuple(visitor.candidates)


def _review_line(candidate: _ASTCandidate, review_scope: str, changed_lines: Tuple[int, ...]) -> int | None:
    """返回审查范围内的主定位，必要时锚定到真实变更行。"""

    if review_scope == "full_file":
        return candidate.start_line
    if review_scope != "changed_lines":
        return None
    for line in changed_lines:
        if candidate.start_line <= line <= candidate.end_line:
            return line
    return None


def _source_line(full_text: str, line_number: int) -> str:
    """安全返回一行源码，且不泄漏越界异常细节。"""

    lines = full_text.splitlines()
    return lines[line_number - 1] if 1 <= line_number <= len(lines) else ""


def _recommendation(rule_id: str) -> str:
    """返回一个受支持 AST 规则的确定性修复建议。"""

    recommendations = {
        "security.dynamic-eval": "Replace eval with a strict parser or allowlisted dispatch table.",
        "security.dynamic-exec": "Remove exec and use explicit, allowlisted program behavior.",
        "security.os-system": "Use subprocess with shell disabled and a validated argument list.",
        "security.os-popen": "Use subprocess with shell disabled and a validated argument list.",
        "security.subprocess-shell-command": (
            "Use subprocess with shell disabled, pass an argument list, and validate all command inputs."
        ),
        "security.subprocess-shell-true": "Pass an argument list with shell disabled and validate all command inputs.",
        "security.sql-fstring": "Use parameterized queries and bind user-controlled values separately.",
        "security.sql-interpolation": "Use parameterized queries and bind user-controlled values separately.",
    }
    return recommendations[rule_id]


@dataclass(frozen=True)
class ASTSecurityRule:
    """AST confirmation for A4 security rules when a complete Python file exists."""

    rule_id: str = "security.ast-confirmation"
    category: str = "security"
    severity: str = "high"
    confidence: float = 0.92
    requires_full_file: bool = True

    def match(self, change_set: ChangeSet) -> Tuple[RuleMatch, ...]:
        """对可完整解析的 Python 文件执行 AST 安全规则并限制报告范围。"""

        matches: List[RuleMatch] = []
        for file_change in change_set.files:
            if file_change.is_binary or not file_change.normalized_path.endswith(".py"):
                continue
            if file_change.review_scope not in {"changed_lines", "full_file"}:
                continue
            if file_change.full_text is None or file_change.analysis_mode != "ast_validated":
                continue
            for candidate in _ast_candidates(file_change.full_text):
                line = _review_line(
                    candidate,
                    file_change.review_scope,
                    file_change.new_changed_lines,
                )
                if line is None:
                    continue
                matches.append(
                    RuleMatch(
                        rule_id=candidate.rule_id,
                        category=self.category,
                        severity=candidate.severity,
                        confidence=self.confidence,
                        file=file_change.normalized_path,
                        line=line,
                        title=candidate.title,
                        evidence=redact_text(_source_line(file_change.full_text, line)),
                        recommendation=_recommendation(candidate.rule_id),
                        source="ast",
                    )
                )
        return tuple(matches)


def default_ast_rules() -> Tuple[ReviewRule, ...]:
    """返回确定性的完整文件 AST 安全规则包。"""

    return (ASTSecurityRule(),)
