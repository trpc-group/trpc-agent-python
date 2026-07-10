"""Deterministic fake model used by the example pipeline."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .schemas import EvalCase

_ASSIGNMENT_PATTERN = re.compile(r"(?<![A-Za-z0-9_-])([A-Za-z_][A-Za-z0-9_]*)=([A-Za-z0-9_-]+)" r"(?![A-Za-z0-9_-])")
_RETURN_ONLY_PATTERN = re.compile(
    r"\breturn\s+only\s+([A-Za-z0-9_-]+)\b",
    re.IGNORECASE,
)
_STRICT_JSON_PATTERN = re.compile(r"\bstrict\s+json\b", re.IGNORECASE)

_OVERFIT_INSTRUCTION = "Always force every final answer into JSON"
_SAFE_INSTRUCTION = "Use strict JSON only when the user explicitly asks"


@dataclass(frozen=True)
class _ParsedRequest:
    assignments: dict[str, str]
    only_value: str | None
    strict_json: bool
    natural_answer: str


class FakeModel:
    """Render deterministic responses from prompt text and user input only."""

    COST_PER_CALL = 0.001

    def __init__(self, seed: int = 91) -> None:
        self.seed = seed

    def generate(
        self,
        prompt_id: str,
        prompt: str,
        case: EvalCase,
    ) -> tuple[str, dict[str, Any], float]:
        mode = self._mode(prompt)
        request = self._parse_request(case.input)
        output = self._render(request, mode=mode)
        trace = {
            "seed": self.seed,
            "prompt_id": prompt_id,
            "prompt_mode": mode,
        }
        return output, trace, self.COST_PER_CALL

    @staticmethod
    def _mode(prompt: str) -> str:
        if _OVERFIT_INSTRUCTION in prompt:
            return "overfit"
        if _SAFE_INSTRUCTION in prompt:
            return "safe"
        return "baseline"

    @staticmethod
    def _parse_request(user_input: str) -> _ParsedRequest:
        assignments = {match.group(1): match.group(2) for match in _ASSIGNMENT_PATTERN.finditer(user_input)}
        only_match = _RETURN_ONLY_PATTERN.search(user_input)
        return _ParsedRequest(
            assignments=assignments,
            only_value=only_match.group(1) if only_match else None,
            strict_json=bool(_STRICT_JSON_PATTERN.search(user_input)),
            natural_answer=FakeModel._natural_answer(user_input),
        )

    @staticmethod
    def _natural_answer(user_input: str) -> str:
        lowered = user_input.lower()
        if "latency" in lowered and "retries" in lowered:
            return "Latency can trigger retries."
        if "cache" in lowered and "stale data" in lowered:
            return "Cache invalidation refreshes stale data."
        return "Here is a natural response."

    @staticmethod
    def _render(request: _ParsedRequest, *, mode: str) -> str:
        if mode == "overfit":
            payload: dict[str, str]
            if request.assignments:
                payload = request.assignments
            else:
                payload = {
                    "answer": request.only_value or request.natural_answer,
                }
            return json.dumps(payload, sort_keys=True)

        if request.strict_json:
            payload_json = json.dumps(request.assignments, sort_keys=True)
            if mode == "safe":
                return payload_json
            return f"Here is the JSON you requested: {payload_json}"

        if request.only_value is not None:
            return request.only_value

        return request.natural_answer
