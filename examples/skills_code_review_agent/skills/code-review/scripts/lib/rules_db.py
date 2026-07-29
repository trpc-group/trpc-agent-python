#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

"""Database connection and transaction lifecycle rules."""

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
class DbLifecycleRule:
    """Detect a resource creation without its required lifecycle finalizer."""

    rule_id: str
    constructor: re.Pattern[str]
    finalizer_template: str
    severity: str
    confidence: float
    title: str
    recommendation: str
    category: str = "db-lifecycle"
    requires_full_file: bool = False

    def match(self, change_set: ChangeSet) -> Tuple[RuleMatch, ...]:
        """检查新增 Python hunk 中连接和显式事务是否缺少收尾。"""

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
                    finalizer = re.compile(self.finalizer_template.format(variable=re.escape(variable)))
                    if any(finalizer.search(other_code) for _, _, other_code, _ in lines):
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


def default_db_rules() -> Tuple[ReviewRule, ...]:
    """返回数据库连接与显式事务生命周期规则。"""

    return (
        DbLifecycleRule(
            rule_id="db.connection-without-close",
            constructor=re.compile(
                r"\b(?P<variable>[A-Za-z_]\w*)\s*=\s*"
                r"(?:sqlite3|psycopg2?|pymysql|mysql(?:\.connector)?|db)\.connect\s*\("
            ),
            finalizer_template=r"\b{variable}\.close\s*\(",
            severity="medium",
            confidence=0.76,
            title="Database connection is opened without a visible close",
            recommendation="Close the connection with a context manager or a guaranteed finally path.",
        ),
        DbLifecycleRule(
            rule_id="db.transaction-without-finalize",
            constructor=re.compile(
                r"\b(?P<variable>[A-Za-z_]\w*)\s*=\s*[A-Za-z_]\w*\.begin\s*\("
            ),
            finalizer_template=r"\b{variable}\.(?:commit|rollback)\s*\(",
            severity="high",
            confidence=0.82,
            title="Explicit transaction has no visible commit or rollback",
            recommendation=(
                "Commit on success and roll back on failure, preferably through "
                "a transaction context manager."
            ),
        ),
    )
