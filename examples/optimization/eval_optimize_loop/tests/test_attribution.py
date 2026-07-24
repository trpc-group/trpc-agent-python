# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""失败归因单测：六类失败类型 × 各 ≥2 条合成样本（12/12 全对 → 准确率 100% ≥ 75%），
以及「真实 baseline 上每个失败 case 至少给出一个可解释原因」（验收标准 4）。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_EXAMPLE_ROOT = _HERE.parent
_REPO_ROOT = _EXAMPLE_ROOT.parents[2]
for _p in (str(_REPO_ROOT), str(_EXAMPLE_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from loop_pipeline.attribution import attribute_case, cluster, primary_type  # noqa: E402
from loop_pipeline.evaluate import CaseEvalRecord, run_eval  # noqa: E402


def _record(
    eval_id: str = "case",
    *,
    failed_metrics: dict[str, float],
    actual_calls=(),
    expected_calls=(),
    actual_response: str = "",
    expected_response: str = "",
    rubric_verdicts=None,
) -> CaseEvalRecord:
    """合成一条失败 case：failed_metrics 里的 metric 记 FAILED，其余 PASSED。"""
    all_metrics = [
        "tool_trajectory_avg_score",
        "final_response_avg_score",
        "llm_rubric_response",
        "llm_rubric_knowledge_recall",
    ]
    scores = {m: (failed_metrics.get(m, 1.0)) for m in all_metrics}
    status = {m: ("FAILED" if m in failed_metrics else "PASSED") for m in all_metrics}
    return CaseEvalRecord(
        eval_id=eval_id,
        passed=False,
        final_status="FAILED",
        case_score=sum(scores.values()) / len(scores),
        metric_scores=scores,
        metric_status=status,
        metric_reasons={m: "synthetic"
                        for m in failed_metrics},
        rubric_verdicts=rubric_verdicts or {},
        actual_tool_calls=[{
            "name": n,
            "args": a
        } for n, a in actual_calls],
        expected_tool_calls=[{
            "name": n,
            "args": a
        } for n, a in expected_calls],
        actual_response=actual_response,
        expected_response=expected_response,
    )


# 表驱动：12 条合成 case，每条标注期望的（主要归因, 必须包含的类型集合）
SYNTHETIC_CASES = [
    # --- wrong_tool_call ×2：漏调（非知识工具）/ 调错工具 ---
    (_record("wtc_missing",
             failed_metrics={"tool_trajectory_avg_score": 0.0},
             expected_calls=[("convert_distance", {
                 "value": 3,
                 "unit": "km"
             })]), "wrong_tool_call", {"wrong_tool_call"}),
    (_record("wtc_wrong_tool",
             failed_metrics={"tool_trajectory_avg_score": 0.0},
             actual_calls=[("get_weather", {
                 "city": "上海"
             })],
             expected_calls=[("convert_distance", {
                 "value": 3,
                 "unit": "km"
             })]), "wrong_tool_call", {"wrong_tool_call"}),
    # --- wrong_tool_args ×2：名字对、参数错 ---
    (_record("wta_unit",
             failed_metrics={"tool_trajectory_avg_score": 0.0},
             actual_calls=[("convert_distance", {
                 "value": 3,
                 "unit": "公里"
             })],
             expected_calls=[("convert_distance", {
                 "value": 3,
                 "unit": "km"
             })]), "wrong_tool_args", {"wrong_tool_args"}),
    (_record("wta_value",
             failed_metrics={"tool_trajectory_avg_score": 0.0},
             actual_calls=[("knowledge_search", {
                 "query": "北京天气"
             })],
             expected_calls=[("knowledge_search", {
                 "query": "北京"
             })]), "wrong_tool_args", {"wrong_tool_args"}),
    # --- knowledge_recall_miss ×2：召回 rubric 失败 / 漏调知识工具 ---
    (_record("krm_rubric",
             failed_metrics={"llm_rubric_knowledge_recall": 0.0},
             rubric_verdicts={"llm_rubric_knowledge_recall": [{
                 "id": "k_guide",
                 "score": 0.0,
                 "reason": "无检索结果"
             }]}), "knowledge_recall_miss", {"knowledge_recall_miss"}),
    (_record("krm_missing_tool",
             failed_metrics={"tool_trajectory_avg_score": 0.0},
             expected_calls=[("knowledge_search", {
                 "query": "深圳"
             })]), "wrong_tool_call", {"wrong_tool_call", "knowledge_recall_miss"}),  # 漏调知识工具 → 两类并报，主因是调用缺失
    # --- format_violation ×2：期望 JSON、实际自由文本 ---
    (_record("fv_plain",
             failed_metrics={"final_response_avg_score": 0.0},
             actual_response="3 公里等于 3000 米",
             expected_response='{"result": 3000, "unit": "m"}'), "format_violation", {"format_violation"}),
    (_record("fv_partial",
             failed_metrics={"final_response_avg_score": 0.0},
             actual_response="结果是 {result: 5000",
             expected_response='{"result": 5000, "unit": "m"}'), "format_violation", {"format_violation"}),
    # --- llm_rubric_fail ×2 ---
    (_record("lrf_json",
             failed_metrics={"llm_rubric_response": 0.5},
             rubric_verdicts={
                 "llm_rubric_response": [{
                     "id": "r_json",
                     "score": 0.0,
                     "reason": "缺少 result 字段"
                 }, {
                     "id": "r_cite",
                     "score": 1.0,
                     "reason": "ok"
                 }]
             }), "llm_rubric_fail", {"llm_rubric_fail"}),
    (_record("lrf_cite",
             failed_metrics={"llm_rubric_response": 0.0},
             rubric_verdicts={"llm_rubric_response": [{
                 "id": "r_cite",
                 "score": 0.0,
                 "reason": "缺少来源标注"
             }]}), "llm_rubric_fail", {"llm_rubric_fail"}),
    # --- final_answer_mismatch ×2：两侧都是自由文本 ---
    (_record("fam_text",
             failed_metrics={"final_response_avg_score": 0.0},
             actual_response="深圳是一座很不错的城市。",
             expected_response="深圳是一座以科技创新闻名的现代化滨海城市。 [source: city-guide]"), "final_answer_mismatch",
     {"final_answer_mismatch"}),
    (_record("fam_identity",
             failed_metrics={"final_response_avg_score": 0.0},
             actual_response="根据以往训练经验，答案与训练样本一致。",
             expected_response="我是城市信息助手 CityInfo。"), "final_answer_mismatch", {"final_answer_mismatch"}),
]


@pytest.mark.parametrize(
    "record,expected_primary,expected_types",
    SYNTHETIC_CASES,
    ids=[case[0].eval_id for case in SYNTHETIC_CASES],
)
def test_six_failure_types_classified(record, expected_primary, expected_types):
    """12/12 合成样本全部归类正确 → 分类准确率 100%（验收线 75%）。"""
    findings = attribute_case(record)
    assert findings, "失败 case 必须产出归因"
    types = {f.type for f in findings}
    assert expected_types <= types, f"{record.eval_id}: 期望包含 {expected_types}，实际 {types}"
    assert primary_type(findings) == expected_primary
    for finding in findings:
        assert finding.evidence, "每条归因必须带证据"
        assert finding.explanation, "每条归因必须带中文可读解释"


def test_bare_scalar_expected_answer_is_not_format_violation():
    """期望答案是 JSON 裸标量（'42'/'true' 等）→ final_answer_mismatch，而非格式违规。"""
    for expected in ("42", "true", "3.14", '"plain"'):
        record = _record("scalar_expected",
                         failed_metrics={"final_response_avg_score": 0.0},
                         actual_response="回答内容不对",
                         expected_response=expected)
        types = {f.type for f in attribute_case(record)}
        assert "format_violation" not in types, f"expected={expected!r} 被误判为格式违规"
        assert "final_answer_mismatch" in types, f"expected={expected!r}"
    # JSON 对象/数组仍按结构化输出判定格式违规
    record = _record("array_expected",
                     failed_metrics={"final_response_avg_score": 0.0},
                     actual_response="一、二、三",
                     expected_response="[1, 2, 3]")
    assert "format_violation" in {f.type for f in attribute_case(record)}


def test_passed_case_yields_no_findings():
    record = _record("ok", failed_metrics={})
    record.passed = True
    record.final_status = "PASSED"
    assert attribute_case(record) == []


def test_fallback_guarantees_explanation_for_unknown_metric():
    """规则未覆盖的 metric 失败也必须有兜底归因（隐藏样本适配性）。"""
    record = CaseEvalRecord(
        eval_id="custom_metric_case",
        passed=False,
        final_status="FAILED",
        case_score=0.0,
        metric_scores={"custom_business_metric": 0.0},
        metric_status={"custom_business_metric": "FAILED"},
        metric_reasons={"custom_business_metric": "业务指标未达标"},
    )
    findings = attribute_case(record)
    assert len(findings) == 1
    assert findings[0].evidence == "业务指标未达标"


def test_cluster_counts_and_primary():
    records = {case[0].eval_id: case[0] for case in SYNTHETIC_CASES}
    summary = cluster(records)
    assert summary.counts["wrong_tool_call"] == 3  # wtc×2 + krm_missing_tool
    assert summary.counts["knowledge_recall_miss"] == 2
    assert summary.counts["format_violation"] == 2
    assert set(summary.primary) == set(records)
    assert all(summary.per_case[eid] for eid in records)


def test_every_failed_case_on_real_baseline_has_reason():
    """真实 baseline 跑一遍：所有 FAILED case 的归因均非空（验收标准 4 后半句）。"""
    records = asyncio.run(
        run_eval(str(_EXAMPLE_ROOT / "data" / "train.evalset.json"), str(_EXAMPLE_ROOT / "data" / "eval_config.json")))
    failed = [r for r in records.values() if not r.passed]
    assert failed, "baseline 训练集应存在失败 case"
    for record in failed:
        findings = attribute_case(record)
        assert findings, f"{record.eval_id} 缺少归因"
        assert all(f.explanation and f.evidence for f in findings)
    # 与 §2.2 设计矩阵对齐的抽查：train_convert_3km 的主因是工具参数错误
    convert_findings = attribute_case(records["train_convert_3km"])
    assert primary_type(convert_findings) == "wrong_tool_args"
    types = {f.type for f in convert_findings}
    assert {"wrong_tool_args", "format_violation", "llm_rubric_fail"} <= types
    intro_findings = attribute_case(records["train_intro_shenzhen"])
    assert primary_type(intro_findings) == "wrong_tool_call"
    assert "knowledge_recall_miss" in {f.type for f in intro_findings}
