"""Deterministic fake judge for offline scoring."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .schemas import EvalCase


@dataclass(frozen=True)
class JudgeOutcome:
    score: float
    passed: bool
    error_code: str | None = None
    evidence: str | None = None
    trace: dict[str, Any] | None = None


class FakeJudge:
    """Scores JSON, exact-answer, and rubric cases without calling an LLM."""

    def score(self, case: EvalCase, output: str) -> JudgeOutcome:
        expectation_type = case.expectation.get("type")
        if expectation_type == "json":
            return self._score_json(case, output)
        if expectation_type == "exact":
            return self._score_exact(case, output)
        if expectation_type == "rubric":
            return self._score_rubric(case, output)
        return JudgeOutcome(
            score=0.0,
            passed=False,
            error_code="unknown_expectation",
            evidence=f"unsupported expectation type: {expectation_type!r}",
            trace={"expectation_type": expectation_type},
        )

    def _score_json(self, case: EvalCase, output: str) -> JudgeOutcome:
        try:
            parsed = json.loads(output)
        except json.JSONDecodeError as exc:
            return JudgeOutcome(
                score=0.0,
                passed=False,
                error_code="json_parse_failure",
                evidence=f"json parser failed at char {exc.pos}: {exc.msg}",
                trace={"expectation_type": "json", "valid_json": False},
            )
        if not isinstance(parsed, dict):
            return JudgeOutcome(
                score=0.0,
                passed=False,
                error_code="json_value_mismatch",
                evidence=f"expected JSON object, got {type(parsed).__name__}",
                trace={"expectation_type": "json", "valid_json": True, "object": False},
            )
        required_keys = list(case.expectation.get("required_keys") or [])
        for key in required_keys:
            if key not in parsed:
                return JudgeOutcome(
                    score=0.0,
                    passed=False,
                    error_code="required_key_missing",
                    evidence=f"missing key {key!r}; got keys {sorted(parsed.keys())!r}",
                    trace={"expectation_type": "json", "valid_json": True, "missing_key": key},
                )
        expected_values = dict(case.expectation.get("expected_values") or {})
        for key, expected_value in expected_values.items():
            actual_value = parsed.get(key)
            if actual_value != expected_value:
                return JudgeOutcome(
                    score=0.0,
                    passed=False,
                    error_code="json_value_mismatch",
                    evidence=f"{key!r}: expected {expected_value!r}, got {actual_value!r}",
                    trace={"expectation_type": "json", "valid_json": True, "mismatch_key": key},
                )
        return JudgeOutcome(score=1.0, passed=True, trace={"expectation_type": "json", "valid_json": True})

    def _score_exact(self, case: EvalCase, output: str) -> JudgeOutcome:
        expected = str(case.expectation.get("expected", ""))
        if _normalize_exact(output) != _normalize_exact(expected):
            return JudgeOutcome(
                score=0.0,
                passed=False,
                error_code="exact_answer_mismatch",
                evidence=f"expected normalized {expected!r}, got {output!r}",
                trace={"expectation_type": "exact", "expected": expected},
            )
        return JudgeOutcome(score=1.0, passed=True, trace={"expectation_type": "exact", "expected": expected})

    def _score_rubric(self, case: EvalCase, output: str) -> JudgeOutcome:
        lowered = output.lower()
        forbidden = [str(item) for item in case.expectation.get("forbidden") or []]
        for pattern in forbidden:
            if pattern.lower() in lowered:
                return JudgeOutcome(
                    score=0.0,
                    passed=False,
                    error_code="forbidden_pattern",
                    evidence=f"forbidden pattern {pattern!r} was present",
                    trace={"expectation_type": "rubric", "forbidden_pattern": pattern},
                )

        must_include = [str(item) for item in case.expectation.get("must_include") or []]
        missing = [term for term in must_include if term.lower() not in lowered]
        if missing:
            return JudgeOutcome(
                score=0.0,
                passed=False,
                error_code="missing_rubric_terms",
                evidence=f"missing terms: {missing!r}",
                trace={"expectation_type": "rubric", "missing_terms": missing},
            )

        max_chars = case.expectation.get("max_chars")
        if max_chars is not None and len(output) > int(max_chars):
            return JudgeOutcome(
                score=0.0,
                passed=False,
                error_code="max_chars_exceeded",
                evidence=f"length {len(output)} exceeded max_chars {max_chars}",
                trace={"expectation_type": "rubric", "length": len(output), "max_chars": int(max_chars)},
            )

        return JudgeOutcome(score=1.0, passed=True, trace={"expectation_type": "rubric"})


def _normalize_exact(value: str) -> str:
    return " ".join(value.strip().lower().split())
