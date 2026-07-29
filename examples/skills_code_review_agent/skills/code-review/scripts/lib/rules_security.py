#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

"""Heuristic security rules that operate only on executable Python code."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Tuple

from .diff_parser import ChangeSet
from .rule_engine import (
    ReviewRule,
    RuleMatch,
    SecretRule,
    advance_triple_quote_state,
    hunk_new_side_lines,
    mask_non_code_line,
)
from .secret_rules import redact_text


_SQL_KEYWORDS = re.compile(r"\b(?:select|insert|update|delete)\b", re.IGNORECASE)
_INTERPOLATION = re.compile(r"\{[^{}]+\}")
_SHELL_TRUE = re.compile(
    r"\bsubprocess\.(?:run|call|check_call|check_output|Popen)\s*\([^\n]*\bshell\s*=\s*True\b"
)
_DYNAMIC_EVAL = re.compile(
    r"(?:\bbuiltins\.eval|(?<![.\w])eval)\s*\("
)
_DYNAMIC_EXEC = re.compile(r"(?<![.\w])exec\s*\(")
_OS_SYSTEM = re.compile(r"\bos\.system\s*\(")
_OS_POPEN = re.compile(r"\bos\.popen\s*\(")
_SUBPROCESS_SHELL_COMMAND = re.compile(
    r"\bsubprocess\.(?:getoutput|getstatusoutput)\s*\("
)


@dataclass(frozen=True)
class SecurityRule:
    """One line-oriented security rule with explicit public metadata."""

    rule_id: str
    severity: str
    confidence: float
    title: str
    recommendation: str
    detector: Callable[[str, Tuple[str, ...]], bool]
    category: str = "security"
    requires_full_file: bool = False

    def match(self, change_set: ChangeSet) -> Tuple[RuleMatch, ...]:
        """扫描新增 Python 行，返回命中的确定性安全风险候选项。"""

        matches = []
        for file_change in change_set.files:
            if file_change.is_binary or not file_change.normalized_path.endswith(".py"):
                continue
            if file_change.review_scope == "deleted_lines":
                continue
            for hunk in file_change.hunks:
                triple_quote = None
                for line_number, line_text, is_added in hunk_new_side_lines(hunk):
                    triple_quote, is_triple_quoted = advance_triple_quote_state(
                        line_text,
                        triple_quote,
                    )
                    if not is_added or is_triple_quoted:
                        continue
                    code, f_strings = mask_non_code_line(line_text)
                    if not self.detector(code, f_strings):
                        continue
                    matches.append(
                        RuleMatch(
                            rule_id=self.rule_id,
                            category=self.category,
                            severity=self.severity,
                            confidence=self.confidence,
                            file=file_change.normalized_path,
                            line=line_number,
                            title=self.title,
                            evidence=redact_text(line_text),
                            recommendation=self.recommendation,
                            source="heuristic",
                        )
                    )
        return tuple(matches)


def _sql_fstring(_code: str, f_strings: Tuple[str, ...]) -> bool:
    """判断 f-string 中是否含有插值构造的 SQL 语句。"""

    if any(_SQL_KEYWORDS.search(value) and _INTERPOLATION.search(value) for value in f_strings):
        return True
    return bool(
        re.search(
            r"\bf(?:r|u|b)?(?:'|\").*\b(?:select|insert|update|delete)\b.*\{[^{}]+\}",
            _code,
            re.IGNORECASE,
        )
    )


def _shell_true(code: str, _f_strings: Tuple[str, ...]) -> bool:
    """判断 subprocess 调用是否显式启用 ``shell=True``。"""

    return bool(_SHELL_TRUE.search(code))


def _dynamic_eval(code: str, _f_strings: Tuple[str, ...]) -> bool:
    """判断代码行是否直接调用内置或限定名 eval。"""

    return bool(_DYNAMIC_EVAL.search(code))


def _dynamic_exec(code: str, _f_strings: Tuple[str, ...]) -> bool:
    """判断代码行是否直接调用内置或限定名 exec。"""

    return bool(_DYNAMIC_EXEC.search(code))


def _os_system(code: str, _f_strings: Tuple[str, ...]) -> bool:
    """判断代码行是否调用隐式 shell 的 ``os.system``。"""

    return bool(_OS_SYSTEM.search(code))


def _os_popen(code: str, _f_strings: Tuple[str, ...]) -> bool:
    """判断代码行是否调用隐式经过 shell 的 os.popen。"""

    return bool(_OS_POPEN.search(code))


def _subprocess_shell_command(
    code: str,
    _f_strings: Tuple[str, ...],
) -> bool:
    """判断代码行是否调用 subprocess 的隐式 shell 辅助函数。"""

    return bool(_SUBPROCESS_SHELL_COMMAND.search(code))


def default_security_rules() -> Tuple[ReviewRule, ...]:
    """按确定性顺序返回 A4 安全规则包。"""

    return (
        SecurityRule(
            rule_id="security.sql-fstring",
            severity="high",
            confidence=0.82,
            title="SQL is built with an interpolated f-string",
            recommendation="Use parameterized queries and bind user-controlled values separately.",
            detector=_sql_fstring,
        ),
        SecurityRule(
            rule_id="security.subprocess-shell-true",
            severity="high",
            confidence=0.85,
            title="subprocess executes with shell=True",
            recommendation="Pass an argument list with shell disabled and validate all command inputs.",
            detector=_shell_true,
        ),
        SecurityRule(
            rule_id="security.dynamic-eval",
            severity="critical",
            confidence=0.85,
            title="Dynamic eval can execute untrusted input",
            recommendation="Replace eval with a strict parser or allowlisted dispatch table.",
            detector=_dynamic_eval,
        ),
        SecurityRule(
            rule_id="security.dynamic-exec",
            severity="critical",
            confidence=0.85,
            title="Dynamic exec can execute untrusted input",
            recommendation="Remove exec and use explicit, allowlisted program behavior.",
            detector=_dynamic_exec,
        ),
        SecurityRule(
            rule_id="security.os-system",
            severity="high",
            confidence=0.85,
            title="os.system invokes a shell command",
            recommendation="Use subprocess with shell disabled and a validated argument list.",
            detector=_os_system,
        ),
        SecurityRule(
            rule_id="security.os-popen",
            severity="high",
            confidence=0.85,
            title="os.popen invokes a shell command",
            recommendation="Use subprocess with shell disabled and a validated argument list.",
            detector=_os_popen,
        ),
        SecurityRule(
            rule_id="security.subprocess-shell-command",
            severity="high",
            confidence=0.85,
            title="subprocess helper executes through a shell",
            recommendation=(
                "Use subprocess with shell disabled, pass an argument list, "
                "and validate all command inputs."
            ),
            detector=_subprocess_shell_command,
        ),
        SecretRule(),
    )
