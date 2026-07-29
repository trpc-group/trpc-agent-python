#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

"""Line-oriented asynchronous-code rules for the deterministic rule pack."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Set, Tuple

from .diff_parser import ChangeSet
from .rule_engine import (
    ReviewRule,
    RuleMatch,
    advance_triple_quote_state,
    hunk_new_side_lines,
    mask_non_code_line,
)
from .secret_rules import redact_text


_ASYNC_DEF = re.compile(r"^(?P<indent>\s*)async\s+def\s+(?P<name>[A-Za-z_]\w*)\s*\(")
_TIME_SLEEP = re.compile(r"\btime\.sleep\s*\(")
_ASYNCIO_SLEEP = re.compile(r"\basyncio\.sleep\s*\(")
_AWAIT = re.compile(r"\bawait\b")
_SCHEDULED = re.compile(r"\b(?:create_task|ensure_future|gather)\s*\(")


def _async_function_names(change_set: ChangeSet) -> Set[str]:
    """收集变更中可见的异步函数名，供未 await 检测使用。"""

    names = set()
    for file_change in change_set.files:
        if file_change.is_binary or not file_change.normalized_path.endswith(".py"):
            continue
        for hunk in file_change.hunks:
            for _, line_text, _ in hunk_new_side_lines(hunk):
                match = _ASYNC_DEF.match(line_text)
                if match is not None:
                    names.add(match.group("name"))
    return names


def _is_async_scope_line(
    line_text: str,
    active_indent: int | None,
) -> Tuple[int | None, bool]:
    """推进基于缩进的异步作用域，并返回当前行是否在该作用域内。"""

    definition = _ASYNC_DEF.match(line_text)
    if definition is not None:
        return len(definition.group("indent")), False
    stripped = line_text.strip()
    if active_indent is None or not stripped or stripped.startswith("#"):
        return active_indent, active_indent is not None
    indent = len(line_text) - len(line_text.lstrip())
    if indent <= active_indent:
        return None, False
    return active_indent, True


def _blocking_sleep(code: str, _async_names: Set[str]) -> bool:
    """判断异步作用域代码是否调用阻塞的 ``time.sleep``。"""

    return bool(_TIME_SLEEP.search(code))


def _unawaited_coroutine(code: str, async_names: Set[str]) -> bool:
    """判断异步调用是否既未 await 也未被已知调度 API 接管。"""

    if _AWAIT.search(code) or _SCHEDULED.search(code):
        return False
    if _ASYNCIO_SLEEP.search(code):
        return True
    return any(re.search(rf"(?<![.\w]){re.escape(name)}\s*\(", code) for name in async_names)


@dataclass(frozen=True)
class AsyncRule:
    """One async rule with a detector evaluated inside async function scope."""

    rule_id: str
    severity: str
    confidence: float
    title: str
    recommendation: str
    detector: Callable[[str, Set[str]], bool]
    category: str = "async-errors"
    requires_full_file: bool = False

    def match(self, change_set: ChangeSet) -> Tuple[RuleMatch, ...]:
        """扫描新增 Python 行，生成异步阻塞与未等待协程候选项。"""

        matches = []
        async_names = _async_function_names(change_set)
        for file_change in change_set.files:
            if file_change.is_binary or not file_change.normalized_path.endswith(".py"):
                continue
            if file_change.review_scope == "deleted_lines":
                continue
            for hunk in file_change.hunks:
                async_indent = None
                triple_quote = None
                for line_number, line_text, is_added in hunk_new_side_lines(hunk):
                    triple_quote, is_triple_quoted = advance_triple_quote_state(
                        line_text,
                        triple_quote,
                    )
                    async_indent, in_async_scope = _is_async_scope_line(line_text, async_indent)
                    if not is_added or is_triple_quoted or not in_async_scope:
                        continue
                    code, _ = mask_non_code_line(line_text)
                    if not self.detector(code, async_names):
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


def default_async_rules() -> Tuple[ReviewRule, ...]:
    """按稳定执行顺序返回异步错误规则包。"""

    return (
        AsyncRule(
            rule_id="async.blocking-time-sleep",
            severity="high",
            confidence=0.84,
            title="time.sleep blocks an async function",
            recommendation="Use await asyncio.sleep or move blocking work to a controlled executor.",
            detector=_blocking_sleep,
        ),
        AsyncRule(
            rule_id="async.unawaited-coroutine",
            severity="medium",
            confidence=0.76,
            title="Coroutine call is not awaited or scheduled",
            recommendation="Await the coroutine or schedule it explicitly with a tracked task.",
            detector=_unawaited_coroutine,
        ),
    )
