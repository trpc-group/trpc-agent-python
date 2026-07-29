#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

"""Change-set-level missing-test heuristic."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from .diff_parser import ChangeSet
from .rule_engine import ReviewRule, RuleMatch


def _is_test_path(path: str) -> bool:
    """依据约定目录和文件名判断路径是否为 Python 测试文件。"""

    normalized = path.replace("\\", "/").lower()
    file_name = normalized.rsplit("/", 1)[-1]
    return "/tests/" in f"/{normalized}" or file_name.startswith("test_") or file_name.endswith("_test.py")


@dataclass(frozen=True)
class MissingTestsRule:
    """Suggest human review when changed production Python lacks changed tests."""

    rule_id: str = "tests.missing-coverage"
    category: str = "missing-tests"
    severity: str = "low"
    confidence: float = 0.65
    requires_full_file: bool = False

    def match(self, change_set: ChangeSet) -> Tuple[RuleMatch, ...]:
        """在生产代码变更没有同次测试变更时生成低置信度候选项。"""

        changed_tests = any(
            file_change.normalized_path.endswith(".py")
            and _is_test_path(file_change.normalized_path)
            and bool(file_change.new_changed_lines)
            for file_change in change_set.files
        )
        if changed_tests:
            return ()

        candidates = []
        for file_change in change_set.files:
            if file_change.is_binary or not file_change.normalized_path.endswith(".py"):
                continue
            if file_change.review_scope == "deleted_lines" or _is_test_path(file_change.normalized_path):
                continue
            if not file_change.new_changed_lines:
                continue
            candidates.append((file_change.normalized_path, min(file_change.new_changed_lines)))
        if not candidates:
            return ()

        file_path, line_number = sorted(candidates)[0]
        return (
            RuleMatch(
                rule_id=self.rule_id,
                category=self.category,
                severity=self.severity,
                confidence=self.confidence,
                file=file_path,
                line=line_number,
                title="Production Python changed without a changed test file",
                evidence="Changed production Python file has no changed test-file companion in this review.",
                recommendation="Add or update focused tests, or record why existing coverage is sufficient.",
                source="heuristic",
            ),
        )


def default_test_rules() -> Tuple[ReviewRule, ...]:
    """返回基于变更集的测试缺失启发式规则。"""

    return (MissingTestsRule(),)
