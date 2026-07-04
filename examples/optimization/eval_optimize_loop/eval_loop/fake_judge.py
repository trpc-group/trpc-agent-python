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
    """Scores JSON, exact-answer, rubric, tool, and knowledge cases offline."""

    def score(self, case: EvalCase, output: str) -> JudgeOutcome:
        expectation_type = case.expectation.get("type")
        if expectation_type == "json":
            return self._score_json(case, output)
        if expectation_type == "exact":
            return self._score_exact(case, output)
        if expectation_type == "rubric":
            return self._score_rubric(case, output)
        if expectation_type == "tool":
            return self._score_tool(case, output)
        if expectation_type == "knowledge":
            return self._score_knowledge(case, output)
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

    def _score_tool(self, case: EvalCase, output: str) -> JudgeOutcome:
        try:
            parsed = json.loads(output)
        except json.JSONDecodeError as exc:
            return JudgeOutcome(
                score=0.0,
                passed=False,
                error_code="tool_call_error",
                evidence=f"tool output was not JSON at char {exc.pos}: {exc.msg}",
                trace={"expectation_type": "tool", "valid_json": False},
            )
        expected_tool = case.expectation.get("expected_tool")
        if parsed.get("tool") != expected_tool:
            return JudgeOutcome(
                score=0.0,
                passed=False,
                error_code="tool_call_error",
                evidence=f"expected tool {expected_tool!r}, got {parsed.get('tool')!r}",
                trace={"expectation_type": "tool", "expected_tool": expected_tool},
            )
        expected_args = dict(case.expectation.get("expected_args") or {})
        actual_args = parsed.get("args") or {}
        for key, expected_value in expected_args.items():
            if actual_args.get(key) != expected_value:
                return JudgeOutcome(
                    score=0.0,
                    passed=False,
                    error_code="parameter_error",
                    evidence=f"arg {key!r}: expected {expected_value!r}, got {actual_args.get(key)!r}",
                    trace={"expectation_type": "tool", "arg": key},
                )
        return JudgeOutcome(score=1.0, passed=True, trace={"expectation_type": "tool"})

    def _score_knowledge(self, case: EvalCase, output: str) -> JudgeOutcome:
        lowered = output.lower()
        required_sources = [str(item) for item in case.expectation.get("required_sources") or []]
        required_terms = [str(item) for item in case.expectation.get("must_include_knowledge_terms") or []]
        missing_sources = [source for source in required_sources if source.lower() not in lowered]
        missing_terms = [term for term in required_terms if term.lower() not in lowered]
        if missing_sources or missing_terms:
            return JudgeOutcome(
                score=0.0,
                passed=False,
                error_code="knowledge_recall_insufficient",
                evidence=f"missing sources={missing_sources!r}, terms={missing_terms!r}",
                trace={
                    "expectation_type": "knowledge",
                    "missing_sources": missing_sources,
                    "missing_terms": missing_terms,
                },
            )
        return JudgeOutcome(score=1.0, passed=True, trace={"expectation_type": "knowledge"})


def _normalize_exact(value: str) -> str:
    return " ".join(value.strip().lower().split())
