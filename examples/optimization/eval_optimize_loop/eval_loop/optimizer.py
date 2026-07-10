"""Fake optimizer that proposes deterministic prompt candidates."""

from __future__ import annotations

from collections import Counter

from .diffing import make_unified_diff
from .schemas import CandidatePrompt
from .schemas import EvalResult

_TARGET_FAILURE_CATEGORIES = {
    "format_violation",
    "final_response_mismatch",
}
_OVERFIT_INSTRUCTION = "Always force every final answer into JSON."
_SAFE_INSTRUCTION = "Use strict JSON only when the user explicitly asks."


class FakeOptimizer:
    """Propose candidates only for observed train formatting failures."""

    def propose(
        self,
        baseline_prompt: str,
        baseline_train: EvalResult,
        failure_summary: dict[str, object],
    ) -> list[CandidatePrompt]:
        if not isinstance(failure_summary, dict):
            raise TypeError("failure_summary must be a dict")

        if baseline_train.split != "train":
            raise ValueError(
                "baseline_train.split must be 'train'; "
                f"got {baseline_train.split!r}"
            )
        for case in baseline_train.cases:
            if case.split != "train":
                raise ValueError(
                    f"baseline_train case {case.case_id!r} split must be 'train'; "
                    f"got {case.split!r}"
                )

        failed_cases = [case for case in baseline_train.cases if not case.passed]
        observed_counts = Counter(
            case.failure_category
            for case in failed_cases
            if case.failure_category
        )
        _validate_failure_summary(failure_summary, observed_counts)

        targeted = sorted(observed_counts.keys() & _TARGET_FAILURE_CATEGORIES)
        if not targeted:
            return []

        overfit_prompt = f"{baseline_prompt.rstrip()}\n\n{_OVERFIT_INSTRUCTION}\n"
        safe_prompt = f"{baseline_prompt.rstrip()}\n\n{_SAFE_INSTRUCTION}\n"
        evidence = ", ".join(targeted)
        return [
            CandidatePrompt(
                candidate_id="candidate_001_overfit",
                prompt=overfit_prompt,
                rationale=(
                    f"Observed training failures ({evidence}); this candidate deliberately "
                    "tests the risky global-JSON correction."
                ),
                prompt_diff=make_unified_diff(
                    baseline_prompt,
                    overfit_prompt,
                    before_name="baseline_system_prompt.txt",
                    after_name="candidate_001_overfit/system_prompt.txt",
                ),
                prompt_fields={"system_prompt": overfit_prompt},
            ),
            CandidatePrompt(
                candidate_id="candidate_002_safe",
                prompt=safe_prompt,
                rationale=(
                    f"Observed training failures ({evidence}); this candidate limits strict "
                    "JSON behavior to explicit user requests."
                ),
                prompt_diff=make_unified_diff(
                    baseline_prompt,
                    safe_prompt,
                    before_name="baseline_system_prompt.txt",
                    after_name="candidate_002_safe/system_prompt.txt",
                ),
                prompt_fields={"system_prompt": safe_prompt},
            ),
        ]


def _validate_failure_summary(
    failure_summary: dict[str, object],
    observed_counts: Counter[str],
) -> None:
    if "by_category" not in failure_summary:
        return

    by_category = failure_summary["by_category"]
    if not isinstance(by_category, dict):
        raise ValueError("failure_summary['by_category'] must be a dict")

    summary_counts: dict[object, int] = {}
    for category, count in by_category.items():
        summary_counts[category] = _normalize_positive_count(category, count)

    if summary_counts != dict(observed_counts):
        raise ValueError(
            "failure_summary['by_category'] must match failed train cases exactly; "
            f"summary={summary_counts!r}, observed={dict(observed_counts)!r}"
        )


def _normalize_positive_count(category: object, value: object) -> int:
    normalized = value if type(value) is int and value > 0 else None

    if normalized is None:
        raise ValueError(
            "failure_summary['by_category'] count must be a positive integer; "
            f"category={category!r}, count={value!r}"
        )
    return normalized
