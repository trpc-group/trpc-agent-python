"""Failure attribution — clusters failed cases by root cause category.

Maps evaluation failures to structured categories for the optimizer's
reflection prompt. Goes beyond simple pass/fail to identify WHY each
case failed.
"""

from dataclasses import dataclass, field

# 单一来源：复用 comparator 定义的 FailureCategory，避免同名异类
# （comparator 的 str 子类 vs 本模块曾经的 str, Enum）长期混淆、类型不兼容
# （reviewer Suggestion）。
from .comparator import FailureCategory


@dataclass
class AttributionEntry:
    """Attribution for a single failed case."""
    case_id: str
    category: FailureCategory
    confidence: float         # How confident we are in this attribution
    detail: str               # Human-readable explanation
    evidence: str = ""        # What in the eval result led to this conclusion


@dataclass
class AttributionReport:
    """Aggregated failure attribution across all cases."""
    total_failures: int = 0
    by_category: dict[str, int] = field(default_factory=dict)
    entries: list[AttributionEntry] = field(default_factory=list)

    def get_summary(self) -> str:
        """Human-readable summary of failure attribution."""
        if self.total_failures == 0:
            return "No failures to attribute."

        lines = [f"Failure Attribution Report ({self.total_failures} failures):"]
        for cat, count in sorted(self.by_category.items(),
                                  key=lambda x: x[1], reverse=True):
            lines.append(f"  {cat}: {count} case(s)")
        return "\n".join(lines)


def attribute_failures(
    baseline_train: dict,
    baseline_val: dict,
) -> AttributionReport:
    """Analyze baseline evaluation results and attribute failures.

    Args:
        baseline_train: BaselineResult from training set evaluation.
        baseline_val: BaselineResult from validation set evaluation.

    Returns:
        AttributionReport with failure clustering.
    """
    report = AttributionReport()

    # Collect all failed cases
    all_failed = []
    if isinstance(baseline_train, dict):
        all_failed.extend(_extract_failures(baseline_train))
    elif hasattr(baseline_train, 'failed_case_ids'):
        all_failed.extend(
            _attribute_from_cases(baseline_train.per_case_results,
                                   baseline_train.failed_case_ids)
        )

    if isinstance(baseline_val, dict):
        all_failed.extend(_extract_failures(baseline_val))
    elif hasattr(baseline_val, 'failed_case_ids'):
        all_failed.extend(
            _attribute_from_cases(baseline_val.per_case_results,
                                   baseline_val.failed_case_ids)
        )

    report.total_failures = len(all_failed)
    report.entries = all_failed

    # Count by category
    for entry in all_failed:
        cat = getattr(entry.category, "value", str(entry.category))
        report.by_category[cat] = report.by_category.get(cat, 0) + 1

    return report


def _entry_from_case(case: dict) -> AttributionEntry:
    """把一个失败 case 转换为 AttributionEntry（category/evidence/confidence/detail）。

    两个失败提取路径共用，避免 per_case→AttributionEntry 逻辑重复漂移。
    """
    case_id = case.get("eval_id", "unknown")
    reason = case.get("reason", "")
    # 优先使用 comparator 提供的 category/evidence（更准确）
    raw_cat = case.get("category", "")
    if raw_cat and raw_cat in _CATEGORY_MAP:
        category = _CATEGORY_MAP[raw_cat]
        evidence = case.get("evidence", "") or reason
    else:
        category = _categorize_failure(reason)
        evidence = reason
    score = case.get("score", 0.7)
    confidence = max(0.5, min(0.95, score)) if isinstance(score, (int, float)) else 0.7
    return AttributionEntry(
        case_id=case_id,
        category=category,
        confidence=round(confidence, 2),
        detail=reason or "Failed without specific reason",
        evidence=evidence,
    )


def _extract_failures(result: dict) -> list[AttributionEntry]:
    """Extract failure attributions from a baseline result dict."""
    entries = []
    per_case = result.get("per_case_results", [])
    failed_ids = result.get("failed_case_ids", [])

    for case in per_case:
        case_id = case.get("eval_id", "unknown")
        if case_id in failed_ids or not case.get("pass", True):
            entries.append(_entry_from_case(case))

    return entries


def _attribute_from_cases(per_case: list, failed_ids: list[str]) -> list[AttributionEntry]:
    """Attribute failures from per-case result data."""
    entries = []
    for case in per_case:
        case_id = case.get("eval_id", "unknown")
        # 与 _extract_failures 判定一致：在 failed_ids 或 pass=False 都算失败，
        # 避免 "pass=False 但未进 failed_case_ids" 的 case 被漏归因。
        if case_id in failed_ids or not case.get("pass", True):
            entries.append(_entry_from_case(case))
    return entries


# 字符串类别名 → FailureCategory 类别常量映射（单一来源：comparator）
_CATEGORY_MAP = {
    "final_response_mismatch": FailureCategory.FINAL_RESPONSE_MISMATCH,
    "tool_call_error": FailureCategory.TOOL_CALL_ERROR,
    "wrong_tool_selected": FailureCategory.WRONG_TOOL_SELECTED,
    "tool_parameter_error": FailureCategory.TOOL_PARAMETER_ERROR,
    "llm_rubric_not_met": FailureCategory.LLM_RUBRIC_NOT_MET,
    "knowledge_recall_insufficient": FailureCategory.KNOWLEDGE_RECALL_INSUFFICIENT,
    "format_not_as_required": FailureCategory.FORMAT_NOT_AS_REQUIRED,
    "missing_expected_output": FailureCategory.MISSING_EXPECTED_OUTPUT,
    "unknown": FailureCategory.UNKNOWN,
}


def _categorize_failure(reason: str) -> FailureCategory:
    """Map a failure reason string to a FailureCategory.

    Uses keyword matching against the reason text.
    """
    reason_lower = reason.lower()

    # Tool-related failures
    if any(kw in reason_lower for kw in ["tool", "function", "api call"]):
        if "parameter" in reason_lower or "argument" in reason_lower:
            return FailureCategory.TOOL_PARAMETER_ERROR
        if "wrong" in reason_lower or "incorrect" in reason_lower:
            return FailureCategory.WRONG_TOOL_SELECTED
        return FailureCategory.TOOL_CALL_ERROR

    # Response quality failures
    if any(kw in reason_lower for kw in ["rubric", "llm judge", "quality"]):
        return FailureCategory.LLM_RUBRIC_NOT_MET

    # Knowledge recall
    if any(kw in reason_lower for kw in ["knowledge", "recall", "retrieval"]):
        return FailureCategory.KNOWLEDGE_RECALL_INSUFFICIENT

    # Format issues
    if any(kw in reason_lower for kw in ["format", "pattern", "regex", "schema"]):
        return FailureCategory.FORMAT_NOT_AS_REQUIRED

    # Missing reference (check BEFORE response mismatch to avoid false match)
    if any(kw in reason_lower for kw in ["missing", "no reference"]):
        return FailureCategory.MISSING_EXPECTED_OUTPUT

    # Response mismatch
    if any(kw in reason_lower for kw in ["response", "output", "answer", "match"]):
        return FailureCategory.FINAL_RESPONSE_MISMATCH

    return FailureCategory.UNKNOWN
