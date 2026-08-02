"""归因准确率测试 — 验收标准 #4（≥75%，我们目标 ≥90%）。

通过端到端跑 baseline → attribution，验证：
1. 归因分类准确率 ≥ 90%（对照 gold-verdict）
2. 每个失败 case 都有可解释的 detail + evidence
"""

import json
import sys
from pathlib import Path

import pytest

_parent = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_parent))

from pipeline.attribution import attribute_failures
from pipeline.baseline import run_baseline_fake
from pipeline.config import load_pipeline_config

# 从 test_gold_verdicts 复用黄金表（避免重复维护）
from tests.test_gold_verdicts import GOLD


def _run_attribution(data_dir, evalset_name: str):
    """对指定 evalset 跑 baseline → attribution。"""
    cfg = load_pipeline_config(
        mode="fake",
        train_evalset=str(data_dir / "train.evalset.json"),
        val_evalset=str(data_dir / "val.evalset.json"),
    )
    baseline = run_baseline_fake(str(data_dir / evalset_name), cfg)
    attribution = attribute_failures(baseline.__dict__, baseline.__dict__)
    return attribution


def test_attribution_accuracy_on_train(data_dir):
    """train 集归因准确率 ≥ 90%（对照黄金表）。"""
    cfg = load_pipeline_config(mode="fake")
    baseline = run_baseline_fake(str(data_dir / "train.evalset.json"), cfg)
    attribution = attribute_failures(baseline.__dict__, baseline.__dict__)

    correct = 0
    total = 0
    for entry in attribution.entries:
        case_id = entry.case_id
        if case_id not in GOLD:
            continue
        gold_passed, gold_cat = GOLD[case_id]
        if not gold_passed:
            total += 1
            if str(entry.category) == gold_cat or entry.category == gold_cat:
                correct += 1

    assert total > 0, "train 集应存在失败 case"
    accuracy = correct / total
    assert accuracy >= 0.90, f"归因准确率 {accuracy:.1%} < 90%"


def test_attribution_accuracy_on_large_train(data_dir):
    """large_train 集归因准确率 ≥ 90%。"""
    cfg = load_pipeline_config(mode="fake")
    baseline = run_baseline_fake(str(data_dir / "large_train.evalset.json"), cfg)
    attribution = attribute_failures(baseline.__dict__, baseline.__dict__)

    correct = 0
    total = 0
    for entry in attribution.entries:
        case_id = entry.case_id
        if case_id not in GOLD:
            continue
        gold_passed, gold_cat = GOLD[case_id]
        if not gold_passed:
            total += 1
            if str(entry.category) == gold_cat or entry.category == gold_cat:
                correct += 1

    assert total > 0, "large_train 集应存在失败 case"
    accuracy = correct / total
    assert accuracy >= 0.90, f"归因准确率 {accuracy:.1%} < 90%"


def test_every_failure_has_detail_and_evidence(data_dir):
    """每个失败 case 的归因都带 detail + evidence（可解释性）。"""
    cfg = load_pipeline_config(mode="fake")
    baseline = run_baseline_fake(str(data_dir / "train.evalset.json"), cfg)
    attribution = attribute_failures(baseline.__dict__, baseline.__dict__)

    for entry in attribution.entries:
        assert entry.detail, f"case {entry.case_id} 缺少 detail"
        assert entry.evidence, f"case {entry.case_id} 缺少 evidence"
        assert entry.confidence > 0, f"case {entry.case_id} 置信度应为正"


def test_failure_categories_covered(data_dir):
    """train 集归因应覆盖多个失败类别（非全 unknown）。"""
    cfg = load_pipeline_config(mode="fake")
    baseline = run_baseline_fake(str(data_dir / "train.evalset.json"), cfg)
    attribution = attribute_failures(baseline.__dict__, baseline.__dict__)

    categories = set(str(e.category) for e in attribution.entries)
    assert len(categories) >= 2, f"归因类别过少: {categories}"
    assert "unknown" not in categories or len(categories) > 1
