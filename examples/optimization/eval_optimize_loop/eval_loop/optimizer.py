"""Fake optimizer that proposes deterministic prompt candidates."""

from __future__ import annotations

from .diffing import make_unified_diff
from .schemas import CandidatePrompt


class FakeOptimizer:
    """Produce the two candidates required by the example issue."""

    def propose(self, baseline_prompt: str) -> list[CandidatePrompt]:
        overfit_prompt = (
            baseline_prompt.rstrip()
            + "\n\n"
            + "# Optimizer patch\n"
            + "OPTIMIZER_MARKER: ALWAYS_OUTPUT_JSON\n"
            + "Always force every final answer into JSON, even when the user asks for prose.\n"
        )
        safe_prompt = (
            baseline_prompt.rstrip()
            + "\n\n"
            + "# Optimizer patch\n"
            + "OPTIMIZER_MARKER: STRICT_WHEN_REQUESTED\n"
            + "Use strict JSON only when the user explicitly asks for JSON.\n"
            + "Use exact answers only when the user explicitly asks for an exact answer.\n"
            + "Otherwise answer naturally and honor rubric constraints.\n"
        )
        return [
            CandidatePrompt(
                candidate_id="candidate_001_overfit",
                prompt=overfit_prompt,
                rationale=(
                    "The train failures are strict JSON/exact formatting failures, so this candidate "
                    "over-corrects by forcing JSON globally."
                ),
                prompt_diff=make_unified_diff(
                    baseline_prompt,
                    overfit_prompt,
                    before_name="baseline_system_prompt.txt",
                    after_name="candidate_001_overfit/system_prompt.txt",
                ),
            ),
            CandidatePrompt(
                candidate_id="candidate_002_safe",
                prompt=safe_prompt,
                rationale=(
                    "This candidate fixes observed strict-format failures without changing "
                    "natural-language behavior on validation cases."
                ),
                prompt_diff=make_unified_diff(
                    baseline_prompt,
                    safe_prompt,
                    before_name="baseline_system_prompt.txt",
                    after_name="candidate_002_safe/system_prompt.txt",
                ),
            ),
        ]
