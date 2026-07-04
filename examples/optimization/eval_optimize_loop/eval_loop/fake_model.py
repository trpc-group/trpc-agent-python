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
        output_override = self._simulated_output(case, mode)
        if output_override is not None:
            output = output_override
        elif mode == "overfit":
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
            if case.protected or "baseline_pass" in case.tags:
                return expected
            return f"{expected} - confirmed."
        if expectation_type == "rubric":
            return self._rubric_sentence(case)
        if expectation_type == "tool":
            return self._tool_json(case)
        if expectation_type == "knowledge":
            return self._knowledge_sentence(case)
        return self._rubric_sentence(case)

    def _overfit_output(self, case: EvalCase) -> str:
        expectation_type = case.expectation.get("type")
        if case.split == "train":
            return self._ideal_output(case)
        if expectation_type == "json":
            return self._expected_json(case)
        if expectation_type == "exact":
            return json.dumps({"answer": str(case.expectation.get("expected", ""))}, sort_keys=True)
        if expectation_type == "rubric" or "prose" in case.tags:
            return json.dumps({"answer": self._rubric_sentence(case)}, sort_keys=True)
        if expectation_type == "tool":
            return self._tool_json(case)
        if expectation_type == "knowledge":
            return self._knowledge_sentence(case)
        return json.dumps({"answer": self._rubric_sentence(case)}, sort_keys=True)

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
        if expectation_type == "rubric":
            return self._rubric_sentence(case)
        if expectation_type == "tool":
            return self._tool_json(case)
        if expectation_type == "knowledge":
            return self._knowledge_sentence(case)
        return self._rubric_sentence(case)

    def _expected_json(self, case: EvalCase) -> str:
        values = dict(case.expectation.get("expected_values") or {})
        return json.dumps(values, sort_keys=True)

    def _simulated_output(self, case: EvalCase, mode: str) -> str | None:
        return case.simulated_outputs.get(mode)

    def _rubric_sentence(self, case: EvalCase) -> str:
        must_include = [str(item) for item in case.expectation.get("must_include") or []]
        if must_include:
            sentence = " ".join(must_include)
        else:
            sentence = "The answer satisfies the rubric"
        max_chars = case.expectation.get("max_chars")
        if max_chars is not None and len(sentence) > int(max_chars):
            sentence = sentence[:int(max_chars)].rstrip()
        return sentence

    def _tool_json(self, case: EvalCase) -> str:
        tool_name = str(case.expectation.get("expected_tool", "lookup"))
        args = dict(case.expectation.get("expected_args") or {})
        return json.dumps({"tool": tool_name, "args": args}, sort_keys=True)

    def _knowledge_sentence(self, case: EvalCase) -> str:
        terms = [str(item) for item in case.expectation.get("must_include_knowledge_terms") or []]
        sources = [str(item) for item in case.expectation.get("required_sources") or []]
        parts = terms + sources
        return " ".join(parts) if parts else "knowledge source recalled"
