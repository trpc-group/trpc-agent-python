# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Independent corpus metrics and mandatory-family acceptance tests."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from trpc_agent_sdk.safety import PolicyLoader
from trpc_agent_sdk.safety import SafetyScanRequest
from trpc_agent_sdk.safety import SafetyScanner

ROOT = Path(__file__).resolve().parents[2]


def _corpus():
    path = ROOT / "examples/tool_safety/evaluation_corpus.py"
    spec = importlib.util.spec_from_file_location("tool_safety_evaluation_corpus", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.build_corpus()


def _evaluate():
    policy = PolicyLoader(ROOT / "examples/tool_safety/tool_safety_policy.yaml").load()
    scanner = SafetyScanner(policy)
    records = []
    for sample in _corpus():
        report = scanner.scan(
            SafetyScanRequest(
                script=sample.source,
                language=sample.language,
                source_type="evaluation",
                source_name=sample.sample_id,
            ))
        categories = {item.category.value for item in report.findings}
        correct = report.decision.value == sample.expected_decision
        if sample.required_rule:
            correct = (correct and sample.required_rule in report.rule_ids and sample.expected_category in categories
                       and report.execution_blocked)
        records.append((sample, report, correct))
    return records


def test_corpus_is_independent_deterministic_and_has_unique_sources():
    samples = _corpus()
    assert len(samples) == 84
    assert len({item.sample_id for item in samples}) == len(samples)
    assert len({(item.language, item.source) for item in samples}) == len(samples)
    assert {item.kind for item in samples} == {"dangerous", "safe", "diagnostic"}
    assert {item.language for item in samples} == {"python", "shell"}


def test_dangerous_detection_rate_and_safe_false_positive_rate():
    records = _evaluate()
    dangerous = [item for item in records if item[0].kind == "dangerous"]
    safe = [item for item in records if item[0].kind == "safe"]
    dangerous_correct = sum(1 for _, _, correct in dangerous if correct)
    safe_false_positives = sum(1 for _, report, _ in safe if report.decision.value != "allow")
    dangerous_rate = dangerous_correct / len(dangerous)
    safe_false_positive_rate = safe_false_positives / len(safe)
    assert (dangerous_correct, len(dangerous), dangerous_rate) == (56, 56, 1.0)
    assert (safe_false_positives, len(safe), safe_false_positive_rate) == (0, 24, 0.0)


def test_three_mandatory_families_are_each_one_hundred_percent():
    records = _evaluate()
    expected_counts = {
        "mandatory_secret_read": 10,
        "mandatory_dangerous_delete": 16,
        "mandatory_external_network": 16,
    }
    for family, expected_count in expected_counts.items():
        family_records = [item for item in records if item[0].family == family]
        assert len(family_records) == expected_count
        assert all(correct for _, _, correct in family_records)


def test_parse_failures_are_reviewed_but_not_counted_as_dangerous_hits():
    records = _evaluate()
    diagnostics = [item for item in records if item[0].kind == "diagnostic"]
    assert len(diagnostics) == 4
    assert all(report.execution_blocked for _, report, _ in diagnostics)
    assert all(correct for _, _, correct in diagnostics)
