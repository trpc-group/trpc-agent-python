"""Fake optimizer that proposes deterministic prompt candidates."""

from __future__ import annotations

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
        failed_cases = [case for case in baseline_train.cases if not case.passed]
        if not failed_cases:
            return []

        observed_categories = {case.failure_category for case in failed_cases if case.failure_category}
        by_category = failure_summary.get("by_category")
        if isinstance(by_category, dict):
            observed_categories.update(
                str(category) for category, count in by_category.items() if _is_positive_count(count)
            )

        targeted = sorted(observed_categories & _TARGET_FAILURE_CATEGORIES)
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


def _is_positive_count(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and value > 0
