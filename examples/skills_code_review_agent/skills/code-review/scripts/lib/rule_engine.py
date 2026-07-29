#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

"""Standard-library-only deterministic review engine."""

from __future__ import annotations

import io
import tokenize
from dataclasses import dataclass
from typing import List, Protocol, Sequence, Tuple

from .diff_parser import ChangeSet, Hunk
from .secret_rules import detect_change_set_secrets


_SEVERITIES = frozenset({"critical", "high", "medium", "low", "info"})
_SOURCES = frozenset({"rule-engine", "ast", "heuristic"})


def mask_non_code_line(line: str) -> Tuple[str, Tuple[str, ...]]:
    """屏蔽注释和普通字符串，同时保留 f-string 的插值标记。"""

    masked = list(line)
    f_strings: List[str] = []
    try:
        tokens = tokenize.generate_tokens(io.StringIO(f"{line}\n").readline)
        for token in tokens:
            if token.type == tokenize.COMMENT:
                start, end = token.start[1], token.end[1]
                masked[start:end] = " " * (end - start)
            elif token.type == tokenize.STRING:
                start, end = token.start[1], token.end[1]
                masked[start:end] = " " * (end - start)
                if token.string.lower().lstrip("rub").startswith("f"):
                    f_strings.append(token.string)
    except (tokenize.TokenError, IndentationError):
        comment_index = line.find("#")
        if comment_index >= 0:
            masked[comment_index:] = " " * (len(line) - comment_index)
    return "".join(masked), tuple(f_strings)


def advance_triple_quote_state(
    line: str,
    active_delimiter: str | None,
) -> Tuple[str | None, bool]:
    """推进三引号字符串状态，并屏蔽属于 docstring 的整行。"""

    if active_delimiter is not None:
        return (
            (None if active_delimiter in line else active_delimiter),
            True,
        )
    if line.lstrip().startswith("#"):
        return None, False
    for delimiter in ('"""', "'''"):
        start = line.find(delimiter)
        if start < 0:
            continue
        end = line.find(delimiter, start + len(delimiter))
        return (None if end >= 0 else delimiter), True
    return None, False


def hunk_new_side_lines(hunk: Hunk) -> Tuple[Tuple[int, str, bool], ...]:
    """按行号返回 hunk 新侧行及其是否为新增行的稳定序列。"""

    lines = {
        line_number: (line_text, False)
        for line_number, line_text in hunk.context_lines.items()
    }
    lines.update(
        {
            line_number: (line_text, True)
            for line_number, line_text in hunk.added_lines.items()
        }
    )
    return tuple(
        (line_number, line_text, is_added)
        for line_number, (line_text, is_added) in sorted(lines.items())
    )


@dataclass(frozen=True)
class RuleMatch:
    """A pre-deduplication, already-redacted deterministic rule result."""

    rule_id: str
    category: str
    severity: str
    confidence: float
    file: str
    line: int
    title: str
    evidence: str
    recommendation: str
    source: str = "rule-engine"
    line_side: str = "new"

    def __post_init__(self) -> None:
        """校验规则元数据的标识、严重级别和置信度范围。"""

        if not self.rule_id or not self.category:
            raise ValueError("rule_id and category must be non-empty")
        if self.severity not in _SEVERITIES:
            raise ValueError("severity is invalid")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if not self.file or self.line < 1:
            raise ValueError("file and line must identify a real source line")
        if self.source not in _SOURCES:
            raise ValueError("source is invalid")
        if self.line_side not in {"new", "old"}:
            raise ValueError("line_side is invalid")


class ReviewRule(Protocol):
    """The plug-in contract shared by all deterministic review rules."""

    rule_id: str
    category: str
    severity: str
    confidence: float
    requires_full_file: bool

    def match(self, change_set: ChangeSet) -> Tuple[RuleMatch, ...]:
        """针对一个已解析审查输入返回已脱敏的规则匹配结果。"""


@dataclass(frozen=True)
class SecretRule:
    """Adapt the A3 detector to the common deterministic rule protocol."""

    rule_id: str = "secrets.detect"
    category: str = "secrets"
    severity: str = "high"
    confidence: float = 0.95
    requires_full_file: bool = False

    def match(self, change_set: ChangeSet) -> Tuple[RuleMatch, ...]:
        """从变更集检测敏感信息，并保留新旧侧真实坐标。"""

        matches = []
        for location in detect_change_set_secrets(change_set):
            recommendation = (
                "Revoke and rotate the exposed credential, then load it from "
                "a secret manager or protected environment variable."
            )
            if location.line_side == "old":
                recommendation = (
                    "Treat the deleted credential as exposed in history; revoke "
                    "and rotate it, then verify the replacement is managed safely."
                )
            matches.append(
                RuleMatch(
                    rule_id=f"secrets.{location.secret_type}",
                    category=self.category,
                    severity=self.severity,
                    confidence=location.confidence,
                    file=location.file,
                    line=location.line,
                    title="Potential hard-coded secret",
                    evidence=location.evidence,
                    recommendation=recommendation,
                    source="rule-engine",
                    line_side=location.line_side,
                )
            )
        return tuple(matches)


class RuleEngine:
    """Run a stable sequence of rules through one dispatching boundary."""

    def __init__(self, rules: Sequence[ReviewRule]) -> None:
        """保存按固定顺序执行的不可变规则集合。"""

        self._rules = tuple(rules)

    @property
    def rules(self) -> Tuple[ReviewRule, ...]:
        """暴露供 manifest 和诊断使用的不可变规则元数据。"""

        return self._rules

    def match(self, change_set: ChangeSet) -> Tuple[RuleMatch, ...]:
        """分发全部规则并在去重前对匹配结果进行稳定排序。"""

        matches = []
        for rule in self._rules:
            matches.extend(rule.match(change_set))
        return tuple(
            sorted(
                matches,
                key=lambda item: (
                    item.file,
                    item.line_side != "new",
                    item.line,
                    item.category,
                    item.rule_id,
                ),
            )
        )
