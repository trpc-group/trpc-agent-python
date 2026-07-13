# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

"""Allowed-diff governance tests."""

from __future__ import annotations

import pytest

from .diff import AllowedDiffRule
from .diff import compare_snapshots
from .diff import unused_allowed_diff_rules
from .diff import validate_allowed_diff_rules


def test_allowed_diff_reports_unused_rules():
    reference = {"sessions": [{"session_id": "s1", "state": {"value": 1}}], "memory": [], "summaries": []}
    actual = {"sessions": [{"session_id": "s1", "state": {"value": 2}}], "memory": [], "summaries": []}
    rules = [
        AllowedDiffRule(
            backend_pair=("left", "right"),
            field_path="$.sessions[0].state.value",
            comparator="exact_path",
            reason="known backend-local representation difference",
            rule_id="used-rule",
        ),
        AllowedDiffRule(
            backend_pair=("left", "right"),
            field_path="$.sessions[0].state.other",
            comparator="exact_path",
            reason="stale example rule",
            rule_id="unused-rule",
        ),
    ]

    diffs = compare_snapshots(
        reference,
        actual,
        case_id="allowed",
        backend_pair=("left", "right"),
        allowed_diff_rules=rules,
    )

    assert diffs[0].allowed
    assert diffs[0].allowed_rule_id == "used-rule"
    assert unused_allowed_diff_rules(diffs, rules) == ["unused-rule"]


@pytest.mark.parametrize(
    "rule",
    [
        AllowedDiffRule(("left", "right"), "$.sessions[*].events[*]", "prefix", "too broad"),
        AllowedDiffRule(("left", "right"), "$.summaries", "prefix", "too broad"),
        AllowedDiffRule(("left", "right"), "$.sessions[0].state.value", "exact_path", ""),
        AllowedDiffRule(("left", "right"), "$.sessions[0].state.value", "regex", "unsupported"),
    ],
)
def test_allowed_diff_rejects_unsafe_rules(rule):
    with pytest.raises(ValueError):
        validate_allowed_diff_rules([rule])
