"""Deterministic fake model used by the example pipeline."""

from __future__ import annotations

import json
from typing import Any

from .schemas import EvalCase


class FakeModel:
    """Prompt-sensitive deterministic model.

    Markers injected by ``FakeOptimizer`` select behavior:
    - no marker: baseline; adds prose around strict JSON/exact outputs.
    - ALWAYS_OUTPUT_JSON: overfits to train formatting and forces JSON on
      validation natural-language/exact cases.
    - STRICT_WHEN_REQUESTED: applies JSON/exact constraints only when the case
      explicitly asks for them.
    """

    COST_PER_CALL = 0.001

    def __init__(self, seed: int = 91) -> None:
        self.seed = seed

    def generate(self, prompt_id: str, prompt: str, case: EvalCase) -> tuple[str, dict[str, Any], float]:
        mode = self._mode(prompt)
        if mode == "overfit":
            output = self._overfit_output(case)
        elif mode == "safe":
            output = self._safe_output(case)
        else:
            output = self._baseline_output(case)
        trace = {
            "seed": self.seed,
            "prompt_id": prompt_id,
            "prompt_mode": mode,
            "case_id": case.case_id,
            "expectation_type": case.expectation.get("type"),
        }
        return output, trace, self.COST_PER_CALL

    def _mode(self, prompt: str) -> str:
        if "ALWAYS_OUTPUT_JSON" in prompt:
            return "overfit"
        if "STRICT_WHEN_REQUESTED" in prompt:
            return "safe"
        return "baseline"

    def _baseline_output(self, case: EvalCase) -> str:
        expectation_type = case.expectation.get("type")
        if expectation_type == "json":
            return f"Here is the JSON you requested: {self._expected_json(case)}"
        if expectation_type == "exact":
            expected = str(case.expectation.get("expected", ""))
            if case.case_id == "val_protected_yes_no":
                return expected
            return f"{expected} - confirmed."
        if case.case_id == "train_rubric_retry_summary":
            return "Latency improved because retries handle transient failures."
        if case.case_id == "val_explain_cache":
            return "A cache keeps recent data fast, but stale data can appear after updates."
        return "I need more information."

    def _overfit_output(self, case: EvalCase) -> str:
        expectation_type = case.expectation.get("type")
        if case.split == "train":
            return self._ideal_output(case)
        if expectation_type == "json":
            return self._expected_json(case)
        if expectation_type == "exact":
            return json.dumps({"answer": str(case.expectation.get("expected", ""))}, sort_keys=True)
        return json.dumps({"answer": "Cache invalidation prevents stale data."}, sort_keys=True)

    def _safe_output(self, case: EvalCase) -> str:
        user_asked = case.input.lower()
        expectation_type = case.expectation.get("type")
        if expectation_type == "json" or "json" in user_asked:
            return self._expected_json(case)
        if expectation_type == "exact" or "exactly" in user_asked:
            return str(case.expectation.get("expected", ""))
        return self._ideal_output(case)

    def _ideal_output(self, case: EvalCase) -> str:
        expectation_type = case.expectation.get("type")
        if expectation_type == "json":
            return self._expected_json(case)
        if expectation_type == "exact":
            return str(case.expectation.get("expected", ""))
        if case.case_id == "train_rubric_retry_summary":
            return "Latency improved because retries handle transient failures."
        if case.case_id == "val_explain_cache":
            return "A cache keeps recent data fast, but stale data can appear after updates."
        return "Meets the rubric."

    def _expected_json(self, case: EvalCase) -> str:
        values = dict(case.expectation.get("expected_values") or {})
        return json.dumps(values, sort_keys=True)
