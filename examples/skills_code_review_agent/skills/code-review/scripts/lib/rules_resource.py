#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

"""Resource-lifecycle rules for handles created in changed Python hunks."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Tuple

from .diff_parser import ChangeSet
from .rule_engine import (
    ReviewRule,
    RuleMatch,
    advance_triple_quote_state,
    hunk_new_side_lines,
    mask_non_code_line,
)
from .secret_rules import redact_text


@dataclass(frozen=True)
class ResourceRule:
    """Find a created resource whose hunk does not show a lifecycle close."""

    rule_id: str
    constructor: re.Pattern[str]
    severity: str
    confidence: float
    title: str
    recommendation: str
    category: str = "resource-leak"
    requires_full_file: bool = False

    def match(self, change_set: ChangeSet) -> Tuple[RuleMatch, ...]:
        """检查新增 Python hunk 中打开资源是否在可见范围内关闭。"""

        matches = []
        for file_change in change_set.files:
            if file_change.is_binary or not file_change.normalized_path.endswith(".py"):
                continue
            if file_change.review_scope == "deleted_lines":
                continue
            for hunk in file_change.hunks:
                triple_quote = None
                lines = []
                for line_number, line_text, is_added in hunk_new_side_lines(hunk):
                    triple_quote, is_triple_quoted = advance_triple_quote_state(
                        line_text,
                        triple_quote,
                    )
                    if is_triple_quoted:
                        continue
                    code, _ = mask_non_code_line(line_text)
                    lines.append((line_number, line_text, code, is_added))
                for line_number, line_text, code, is_added in lines:
                    if not is_added:
                        continue
                    candidate = self.constructor.search(code)
                    if candidate is None:
                        continue
                    variable = candidate.group("variable")
                    close_pattern = re.compile(rf"\b{re.escape(variable)}\.close\s*\(")
                    if any(close_pattern.search(other_code) for _, _, other_code, _ in lines):
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


def default_resource_rules() -> Tuple[ReviewRule, ...]:
    """按稳定执行顺序返回资源生命周期规则。"""

    return (
        ResourceRule(
            rule_id="resource.open-without-close",
            constructor=re.compile(r"\b(?P<variable>[A-Za-z_]\w*)\s*=\s*open\s*\("),
            severity="medium",
            confidence=0.74,
            title="File handle opened without a visible close",
            recommendation="Use a with block or close the handle on every execution path.",
        ),
        ResourceRule(
            rule_id="resource.client-session-without-close",
            constructor=re.compile(
                r"\b(?P<variable>[A-Za-z_]\w*)\s*=\s*(?:aiohttp\.)?ClientSession\s*\("
            ),
            severity="medium",
            confidence=0.78,
            title="ClientSession is created without a visible close",
            recommendation="Use async with or await session.close on every execution path.",
        ),
    )
