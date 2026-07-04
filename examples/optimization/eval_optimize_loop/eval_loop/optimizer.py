"""Fake optimizer that proposes deterministic prompt candidates."""

from __future__ import annotations

from .schemas import CandidatePrompt


class FakeOptimizer:
    """Produce the two candidates required by the example issue."""

    def propose(self, baseline_prompt: str) -> list[CandidatePrompt]:
        return [
            CandidatePrompt(
                candidate_id="candidate_001_overfit",
                prompt=(
                    baseline_prompt.rstrip()
                    + "\n\n"
                    + "# Optimizer patch\n"
                    + "OPTIMIZER_MARKER: ALWAYS_OUTPUT_JSON\n"
                    + "Always force every final answer into JSON, even when the user asks for prose.\n"
                ),
                rationale=(
                    "The train failures are strict JSON/exact formatting failures, so this candidate "
                    "over-corrects by forcing JSON globally."
                ),
                prompt_diff=(
                    "+ OPTIMIZER_MARKER: ALWAYS_OUTPUT_JSON\n"
                    "+ Always force every final answer into JSON, even when the user asks for prose."
                ),
            ),
            CandidatePrompt(
                candidate_id="candidate_002_safe",
                prompt=(
                    baseline_prompt.rstrip()
                    + "\n\n"
                    + "# Optimizer patch\n"
                    + "OPTIMIZER_MARKER: STRICT_WHEN_REQUESTED\n"
                    + "Use strict JSON only when the user explicitly asks for JSON.\n"
                    + "Use exact answers only when the user explicitly asks for an exact answer.\n"
                    + "Otherwise answer naturally and honor rubric constraints.\n"
                ),
                rationale=(
                    "This candidate fixes observed strict-format failures without changing "
                    "natural-language behavior on validation cases."
                ),
                prompt_diff=(
                    "+ OPTIMIZER_MARKER: STRICT_WHEN_REQUESTED\n"
                    "+ Use strict JSON only when explicitly requested.\n"
                    "+ Preserve natural-language answers unless a strict format is requested."
                ),
            ),
        ]
