"""Gold-verdict 归因精度锁 — 锁定 comparator 在真实 evalset 上的判定与归因。

验收标准 #4：失败归因分类准确率 ≥ 75%，且每个失败 case 至少给出一个可解释原因。
本测试锁定的阈值更严：≥90%（见 test_attribution_accuracy）。

本测试维护一份 {eval_id: (passed, category)} 的黄金表，parametrize 遍历
train + large_train 的所有 case，断言 comparator 的判定与黄金表一致。
任何 comparator 改动若导致判定漂移，此测试会立即暴露。
"""

import json
import sys
from pathlib import Path

import pytest

_parent = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_parent))

from pipeline.comparator import compare_case


def _load_cases(rel_path: str) -> list[dict]:
    path = _parent / rel_path
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("eval_cases", [])


# 黄金表：eval_id → (passed, expected_category)
# 从数据归一化后的真实判定生成，作为回归锁。
GOLD: dict[str, tuple[bool, str]] = {
    # ── train.evalset.json (34 cases) ──
    "train_simple_math_001": (True, ""),
    "train_simple_math_002_fail": (False, "final_response_mismatch"),
    "train_simple_math_003": (True, ""),
    "train_simple_math_004_fail": (False, "final_response_mismatch"),
    "train_simple_math_005": (True, ""),
    "train_simple_math_006": (True, ""),
    "train_reasoning_001": (True, ""),
    "train_reasoning_002_fail": (False, "final_response_mismatch"),
    "train_reasoning_003": (True, ""),
    "train_reasoning_004_fail": (False, "final_response_mismatch"),
    "train_reasoning_005": (True, ""),
    "train_reasoning_006": (True, ""),
    "train_tool_001": (True, ""),
    "train_tool_002_fail": (False, "tool_call_error"),
    "train_tool_003": (True, ""),
    "train_tool_004": (True, ""),
    "train_tool_005": (True, ""),
    "train_tool_006_fail": (False, "final_response_mismatch"),
    "train_multiturn_001": (True, ""),
    "train_multiturn_002_fail": (False, "final_response_mismatch"),
    "train_multiturn_003": (True, ""),
    "train_multiturn_004": (True, ""),
    "train_chinese_001": (True, ""),
    "train_chinese_002": (True, ""),
    "train_chinese_003_fail": (False, "final_response_mismatch"),
    "train_chinese_004": (True, ""),
    "train_edge_001": (True, ""),
    "train_edge_002": (True, ""),
    "train_edge_003_fail": (False, "final_response_mismatch"),
    "train_edge_004": (True, ""),
    "train_format_001": (True, ""),
    "train_format_002": (True, ""),
    "train_format_003": (True, ""),
    "train_format_004_fail": (False, "format_not_as_required"),
    # ── large_train.evalset.json (50 cases) ──
    "large_simple_math_001": (True, ""),
    "large_simple_math_002": (True, ""),
    "large_simple_math_003": (True, ""),
    "large_simple_math_004": (True, ""),
    "large_simple_math_005": (True, ""),
    "large_simple_math_006": (True, ""),
    "large_simple_math_007": (True, ""),
    "large_simple_math_008": (True, ""),
    "large_simple_math_009_fail": (False, "final_response_mismatch"),
    "large_simple_math_010": (True, ""),
    "large_reasoning_001": (True, ""),
    "large_reasoning_002": (True, ""),
    "large_reasoning_003": (True, ""),
    "large_reasoning_004_fail": (False, "final_response_mismatch"),
    "large_reasoning_005": (True, ""),
    "large_reasoning_006": (True, ""),
    "large_reasoning_007_fail": (False, "final_response_mismatch"),
    "large_reasoning_008": (True, ""),
    "large_reasoning_009": (True, ""),
    "large_reasoning_010_fail": (False, "final_response_mismatch"),
    "large_tool_001": (True, ""),
    "large_tool_002": (True, ""),
    "large_tool_003": (True, ""),
    "large_tool_004": (True, ""),
    "large_tool_005_fail": (False, "final_response_mismatch"),
    "large_tool_006": (True, ""),
    "large_tool_007_fail": (False, "final_response_mismatch"),
    "large_tool_008": (True, ""),
    "large_multiturn_001": (True, ""),
    "large_multiturn_002_fail": (False, "final_response_mismatch"),
    "large_multiturn_003": (True, ""),
    "large_multiturn_004": (True, ""),
    "large_multiturn_005_fail": (False, "final_response_mismatch"),
    "large_multiturn_006": (True, ""),
    "large_chinese_001": (True, ""),
    "large_chinese_002": (True, ""),
    "large_chinese_003_fail": (False, "final_response_mismatch"),
    "large_chinese_004": (True, ""),
    "large_chinese_005": (True, ""),
    "large_chinese_006_fail": (False, "final_response_mismatch"),
    "large_edge_001": (True, ""),
    "large_edge_002": (True, ""),
    "large_edge_003_fail": (False, "final_response_mismatch"),
    "large_edge_004": (True, ""),
    "large_edge_005_fail": (False, "final_response_mismatch"),
    "large_format_001": (True, ""),
    "large_format_002": (True, ""),
    "large_format_003_fail": (False, "format_not_as_required"),
    "large_format_004": (True, ""),
    "large_format_005_fail": (False, "format_not_as_required"),
}


def _all_cases() -> list[dict]:
    """加载 train + large_train 的所有 case。"""
    cases = _load_cases("data/train.evalset.json")
    cases += _load_cases("data/large_train.evalset.json")
    return cases


def _all_ids() -> list[str]:
    return [c.get("eval_id", "") for c in _all_cases()]


@pytest.mark.parametrize("eval_id", _all_ids())
def test_gold_verdict(eval_id: str):
    """每个 case 的判定必须与黄金表一致。"""
    case = next(c for c in _all_cases() if c.get("eval_id") == eval_id)
    assert eval_id in GOLD, f"eval_id {eval_id} 不在 GOLD 表中"
    gold_passed, gold_cat = GOLD[eval_id]

    v = compare_case(case)
    assert v.passed == gold_passed, (
        f"[{eval_id}] 判定 {v.passed} != 黄金 {gold_passed}; "
        f"detail={v.detail}; expected={v.expected_final!r}; actual={v.actual_final!r}"
    )
    if not gold_passed:
        assert str(v.category) == gold_cat, (
            f"[{eval_id}] 归因 {v.category} != 黄金 {gold_cat}; detail={v.detail}"
        )
        # 每个失败 case 必须有可解释原因
        assert v.detail, f"[{eval_id}] 失败但无 detail"
        assert v.evidence, f"[{eval_id}] 失败但无 evidence"


def test_gold_covers_all_cases():
    """GOLD 表必须覆盖 train + large_train 的所有 case。"""
    ids = set(_all_ids())
    gold_ids = set(GOLD.keys())
    missing = ids - gold_ids
    extra = gold_ids - ids
    assert not missing, f"GOLD 表缺少 case: {missing}"
    assert not extra, f"GOLD 表包含不存在的 case: {extra}"


def test_attribution_accuracy():
    """归因准确率锁定 ≥ 90%（比验收标准 #4 的 ≥75% 更严）。"""
    correct = 0
    total = 0
    for c in _all_cases():
        cid = c.get("eval_id", "")
        if cid not in GOLD:
            continue
        gold_passed, gold_cat = GOLD[cid]
        v = compare_case(c)
        total += 1
        if v.passed == gold_passed and (gold_passed or str(v.category) == gold_cat):
            correct += 1
    assert total > 0
    accuracy = correct / total
    assert accuracy >= 0.90, f"归因准确率 {accuracy:.1%} < 90%"


def test_every_failure_has_explainable_reason():
    """每个失败 case 的归因都带 detail + evidence（可解释性）。"""
    unexplained = []
    for c in _all_cases():
        cid = c.get("eval_id", "")
        if cid not in GOLD or GOLD[cid][0]:
            continue
        v = compare_case(c)
        if not v.detail or not v.evidence:
            unexplained.append(cid)
    assert not unexplained, f"以下失败 case 缺少可解释原因: {unexplained}"
