"""Issue-level deliverable contract tests for the public example."""

from __future__ import annotations

import json
import re
from pathlib import Path

EXAMPLE_ROOT = (Path(__file__).resolve().parents[3] / "examples" / "optimization" / "eval_optimize_loop")


def _dataset(name: str) -> dict:
    return json.loads((EXAMPLE_ROOT / name).read_text(encoding="utf-8"))


def test_public_datasets_cover_required_case_matrix() -> None:
    train = _dataset("train.evalset.json")["evalCases"]
    validation = _dataset("val.evalset.json")["evalCases"]
    assert len(train) == 3
    assert len(validation) == 3
    assert {case["evalId"] for case in train}.isdisjoint(case["evalId"] for case in validation)
    queries = [case["conversation"][0]["userContent"]["parts"][0]["text"] for case in (*train, *validation)]
    assert all(
        any(f"BEHAVIOR:{behavior}" in query for query in queries) for behavior in ("improve", "stable", "regress"))


def test_solution_is_300_to_500_chinese_characters_and_covers_required_topics() -> None:
    solution = (EXAMPLE_ROOT / "SOLUTION.md").read_text(encoding="utf-8")
    chinese_characters = re.findall(r"[\u4e00-\u9fff]", solution)
    assert 300 <= len(chinese_characters) <= 500
    for topic in ("失败归因", "接受门禁", "过拟合", "审计"):
        assert topic in solution
