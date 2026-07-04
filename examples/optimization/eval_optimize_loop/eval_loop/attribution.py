"""Rule-based failure attribution for the example evaluator."""

from __future__ import annotations

from collections import Counter
from typing import Iterable

from .schemas import EvalResult


_ERROR_TO_ATTRIBUTION = {
    "json_parse_failure": ("format_violation", "output is not valid JSON"),
    "required_key_missing": ("final_answer_mismatch", "required JSON key is missing"),
    "json_value_mismatch": ("final_answer_mismatch", "JSON value does not match expected value"),
    "exact_answer_mismatch": ("final_answer_mismatch", "normalized exact answer mismatch"),
    "forbidden_pattern": ("format_violation", "output contains a forbidden pattern"),
    "missing_rubric_terms": ("llm_rubric_failed", "required rubric terms are missing"),
    "max_chars_exceeded": ("length_violation", "output exceeds max_chars"),
}


def attribute_failure(error_code: str, evidence: str) -> tuple[str, str, str]:
    """Return failure_category, failure_reason, evidence for a judge error."""

    category, reason = _ERROR_TO_ATTRIBUTION.get(
        error_code,
        ("unknown_failure", f"unmapped judge error: {error_code}"),
    )
    return category, reason, evidence


def summarize_failures(results: Iterable[EvalResult]) -> dict[str, object]:
    """Summarize failures by category and by prompt/split for reporting."""

    by_category: Counter[str] = Counter()
    by_prompt: dict[str, dict[str, int]] = {}
    examples: list[dict[str, str]] = []
    total_failed = 0

    for result in results:
        prompt_key = f"{result.prompt_id}:{result.split}"
        by_prompt.setdefault(prompt_key, {})
        for case in result.cases:
            if case.passed:
                continue
            total_failed += 1
            category = case.failure_category or "unknown_failure"
            by_category[category] += 1
            by_prompt[prompt_key][category] = by_prompt[prompt_key].get(category, 0) + 1
            examples.append({
                "prompt_id": result.prompt_id,
                "split": result.split,
                "case_id": case.case_id,
                "failure_category": category,
                "failure_reason": case.failure_reason or "",
                "evidence": case.evidence or "",
            })

    return {
        "total_failed_cases": total_failed,
        "by_category": dict(sorted(by_category.items())),
        "by_prompt_split": {key: dict(sorted(value.items())) for key, value in sorted(by_prompt.items())},
        "examples": examples,
    }
