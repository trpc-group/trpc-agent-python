"""分层失败归因：规则快通道 + 反事实深归因（本地确定性 metric 验证）。

见 DESIGN.md §4.1 / §7。两层按 case 信号明确度自适应：
- 规则层：从 actual/expected 的工具轨迹 + response 差异直接归因（confidence 0.85）
- 反事实层：单变量替换验证（只换 response / 只换 tools）。与规则一致 → 提升至 0.95；
  规则未命中 → 反事实兜底（0.7 / 0.6）；都失败 → unknown（0.3, fallback）

反事实用本地纯 Python 实现 metric（contains + trajectory exact），不调 SDK、不调 LLM，
所以即使触发也零成本——这是「归因准确率 ≥75%」与「≤3 分钟」兼容的关键。
"""
from __future__ import annotations

from typing import Any

from .models import FailureAttribution, FailureAttributionSummary, SplitResult

# 视为「格式结构标记」的 expected 关键词（用于区分 format_violation 与 final_response_mismatch）
_FORMAT_MARKERS = {"route", "answer", "category", "json", "{", "}"}


def _tools_equal(a: list[dict[str, Any]], b: list[dict[str, Any]]) -> bool:
    if len(a) != len(b):
        return False
    for x, y in zip(a, b):
        if x.get("name") != y.get("name") or x.get("args") != y.get("args"):
            return False
    return True


def _resp_ok(actual_resp: str, expected_resp: str) -> bool:
    """对应 SDK final_response_avg_score 的 contains criterion（本地复刻）。"""
    if not expected_resp:
        return True
    return expected_resp.lower() in (actual_resp or "").lower()


def _rule_attribute(case_spec: dict[str, Any], variant: str) -> tuple[str, str, float]:
    """规则归因 → (category, evidence, confidence)。判定优先级见 DESIGN.md §7。"""
    expected_tools = case_spec.get("expected_tool_uses", [])
    actual_tools = case_spec["variants"][variant]["tool_uses"]
    expected_resp = case_spec.get("expected_response", "")
    actual_resp = case_spec["variants"][variant]["response"]

    # 工具维度（优先于 response）
    if expected_tools:
        exp_names = [t["name"] for t in expected_tools]
        act_names = [t["name"] for t in actual_tools]
        if not actual_tools:
            return (
                "knowledge_recall_insufficient",
                f"expected tool call(s) {exp_names} but agent called none (guessed without query)",
                0.85,
            )
        if exp_names != act_names:
            return (
                "tool_selection_error",
                f"expected tool names {exp_names}, actual {act_names}",
                0.85,
            )
        for et, at in zip(expected_tools, actual_tools):
            if et.get("args") != at.get("args"):
                return (
                    "tool_parameter_error",
                    f"tool '{et['name']}' expected args {et['args']}, got {at['args']}",
                    0.85,
                )

    # response 维度
    if expected_resp and not _resp_ok(actual_resp, expected_resp):
        if expected_resp.lower() in _FORMAT_MARKERS:
            return (
                "format_violation",
                f"actual response missing required structure marker '{expected_resp}'",
                0.85,
            )
        return (
            "final_response_mismatch",
            f"actual response missing expected key content '{expected_resp}'",
            0.85,
        )

    return ("unknown", "no decisive rule signal", 0.3)


def _counterfactual(case_spec: dict[str, Any], variant: str) -> str | None:
    """单变量替换反事实：返回失败侧 'response' / 'tool' / 'compound' / None。

    只换 response（用 expected）→ 整体 pass？说明失败在 response 侧。
    只换 tools（用 expected）→ 整体 pass？说明失败在 tool 侧。
    """
    expected_tools = case_spec.get("expected_tool_uses", [])
    expected_resp = case_spec.get("expected_response", "")
    actual = case_spec["variants"][variant]

    base_resp_ok = _resp_ok(actual["response"], expected_resp)
    base_tool_ok = _tools_equal(actual["tool_uses"], expected_tools)

    # 只换 response（expected 一定含自身 → resp metric 必过），tool 维持
    cf_resp_repairs = base_tool_ok
    # 只换 tools（expected == expected → tool metric 必过），response 维持
    cf_tool_repairs = base_resp_ok

    if not base_resp_ok and base_tool_ok and cf_resp_repairs:
        return "response"
    if not base_tool_ok and base_resp_ok and cf_tool_repairs:
        return "tool"
    if not base_resp_ok and not base_tool_ok:
        return "compound"
    return None


def attribute_failure(case_spec: dict[str, Any], variant: str) -> FailureAttribution:
    """对一个失败 case 做分层归因。"""
    cat, evidence, conf = _rule_attribute(case_spec, variant)
    if cat != "unknown":
        side = _counterfactual(case_spec, variant)
        response_cats = {"format_violation", "final_response_mismatch"}
        tool_cats = {
            "tool_selection_error",
            "tool_parameter_error",
            "knowledge_recall_insufficient",
            "tool_call_error",
        }
        consistent = (cat in response_cats and side == "response") or (cat in tool_cats and side == "tool")
        if consistent:
            conf = 0.95
        return FailureAttribution(category=cat, confidence=conf, evidence=evidence, source="rule")

    # 规则未命中 → 反事实兜底
    side = _counterfactual(case_spec, variant)
    if side == "response":
        return FailureAttribution(
            category="final_response_mismatch",
            confidence=0.7,
            evidence="counterfactual: replacing response alone repairs metrics",
            source="counterfactual",
        )
    if side == "tool":
        return FailureAttribution(
            category="tool_call_error",
            confidence=0.7,
            evidence="counterfactual: replacing tools alone repairs metrics",
            source="counterfactual",
        )
    if side == "compound":
        return FailureAttribution(
            category="tool_call_error",
            confidence=0.6,
            evidence="counterfactual: only combined repair fixes metrics (compound failure)",
            source="counterfactual",
        )
    return FailureAttribution(
        category="unknown",
        confidence=0.3,
        evidence="no rule or counterfactual signal",
        source="fallback",
    )


def attribute_failures(
    baseline_train: SplitResult,
    baseline_val: SplitResult,
    cases: list[dict[str, Any]],
    variant: str,
) -> FailureAttributionSummary:
    """对 baseline 的所有失败 case 归因（baseline 失败是优化目标，驱动 §5.2 归因阶段）。"""
    case_map = {c["eval_id"]: c for c in cases}
    by_case: dict[str, FailureAttribution] = {}
    total_failed = 0
    category_counts: dict[str, int] = {}

    for split_result in (baseline_train, baseline_val):
        for snap in split_result.cases:
            if snap.passed:
                continue
            total_failed += 1
            spec = case_map.get(snap.eval_id)
            if not spec:
                continue
            attr = attribute_failure(spec, variant)
            by_case[snap.eval_id] = attr
            category_counts[attr.category] = category_counts.get(attr.category, 0) + 1

    explained = sum(1 for a in by_case.values() if a.category != "unknown")
    coverage = explained / total_failed if total_failed else 1.0
    return FailureAttributionSummary(
        total_failed_cases=total_failed,
        explained_failed_cases=explained,
        coverage_rate=coverage,
        category_counts=category_counts,
        by_case=by_case,
    )
