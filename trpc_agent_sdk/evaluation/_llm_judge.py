# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""LLM Judge: build a judge agent from eval_metric and run evaluation via the agent."""

from __future__ import annotations

import asyncio
import copy
import json
import os
import re
import uuid
from typing import Any
from typing import Optional
from typing import Protocol

import json_repair
from pydantic import BaseModel as PydanticBaseModel
from pydantic import field_validator

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.context import create_agent_context
from trpc_agent_sdk.context import new_invocation_context_id
from trpc_agent_sdk.models import ModelRegistry
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.planners import BuiltInPlanner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import GenerateContentConfig
from trpc_agent_sdk.types import HttpOptions
from trpc_agent_sdk.types import Part
from trpc_agent_sdk.types import ThinkingConfig

from ._eval_case import IntermediateData
from ._eval_case import Invocation
from ._eval_case import get_all_tool_calls
from ._eval_case import get_all_tool_responses
from ._eval_metrics import EvalMetric
from ._eval_metrics import EvalStatus
from ._eval_result import EvaluationResult
from ._eval_result import NamedScoreResult
from ._eval_result import PerInvocationResult
from ._llm_criterion import LLMJudgeCriterion
from ._llm_criterion import JudgeModelOptions
from ._llm_criterion import Rubric
from ._llm_criterion import RubricScore
from ._llm_criterion import ScoreResult
from ._llm_criterion import get_llm_criterion_from_metric


class FinalResponseOutput(PydanticBaseModel):
    """Pydantic schema for llm_final_response judge output (reasoning + valid/invalid)."""
    reasoning: str
    is_the_agent_response_valid: str  # "valid" or "invalid"

    # Coerce non-string reasoning (e.g. nested object) to JSON repr.
    @field_validator("reasoning", mode="before")
    @classmethod
    def _stringify_reasoning(cls, v: Any) -> str:
        if v is None:
            return ""
        if isinstance(v, str):
            return v
        try:
            return json.dumps(v, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(v)


class RubricItemOutput(PydanticBaseModel):
    """Schema for a single rubric item in judge output (id, rubric, evidence, reason, verdict)."""
    id: str
    rubric: str
    evidence: str
    reason: str
    verdict: str  # "yes" or "no"

    # Coerce scalar types (int/float/bool) into strings before validation.
    @field_validator("id", "rubric", "evidence", "reason", "verdict", mode="before")
    @classmethod
    def _stringify(cls, v: Any) -> str:
        if v is None:
            return ""
        if isinstance(v, str):
            return v
        if isinstance(v, bool):
            # bool must precede int (bool is a subclass of int).
            return "yes" if v else "no"
        if isinstance(v, (int, float)):
            return str(v)
        try:
            return json.dumps(v, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(v)


class RubricJudgeOutput(PydanticBaseModel):
    """Pydantic schema for llm_rubric_response and llm_rubric_knowledge_recall judge output."""
    items: list[RubricItemOutput]

    # Unpack items that were double-serialized as a JSON-encoded string.
    @field_validator("items", mode="before")
    @classmethod
    def _unpack_items(cls, v: Any) -> Any:
        if isinstance(v, str):
            try:
                return json.loads(v)
            except (TypeError, ValueError, json.JSONDecodeError):
                return v
        return v


class MessagesConstructor(Protocol):
    """Builds the user message sent to the judge model from invocations and criterion."""

    def format_user_message(
        self,
        actuals: list[Invocation],
        expecteds: Optional[list[Invocation]],
        criterion: LLMJudgeCriterion,
        metric_name: str,
    ) -> str:
        ...


class ResponseScorer(Protocol):
    """Parses the judge model's raw response text into a ScoreResult by metric type."""

    def parse_response(self, response_text: str, metric_name: str) -> ScoreResult:
        ...


class SamplesAggregator(Protocol):
    """Aggregates multiple judge samples (e.g. multiple runs) into a single ScoreResult."""

    def aggregate_samples(
        self,
        samples: list[ScoreResult],
        threshold: float,
    ) -> ScoreResult:
        ...


class InvocationsAggregator(Protocol):
    """Aggregates per-invocation results into an overall score and EvalStatus."""

    def aggregate_invocations(
        self,
        results: list[PerInvocationResult],
        threshold: float,
    ) -> tuple[Optional[float], EvalStatus]:
        ...


class ModelsAggregator(Protocol):
    """Aggregates per-model judge ScoreResults (single invocation, multiple judge models) into one ScoreResult."""

    def aggregate_models(
        self,
        per_model: list[ScoreResult],
        threshold: float,
        weights: list[float],
    ) -> ScoreResult:
        ...


class MajorityVoteSamplesAggregator:
    """Selects one sample by majority vote on pass/fail; on tie, prefers a failed sample if any."""

    def aggregate_samples(
        self,
        samples: list[ScoreResult],
        threshold: float,
    ) -> ScoreResult:
        if not samples:
            raise ValueError("samples must not be empty")
        passed = [s for s in samples if (s.score or 0) >= threshold]
        failed = [s for s in samples if (s.score or 0) < threshold]
        if len(samples) == 1:
            return samples[0]
        if len(passed) > len(failed):
            return passed[0]
        if len(failed) > 0:
            return failed[0]
        return samples[0]


class AverageInvocationsAggregator:
    """Averages per-invocation scores; overall pass iff average >= threshold."""

    def aggregate_invocations(
        self,
        results: list[PerInvocationResult],
        threshold: float,
    ) -> tuple[Optional[float], EvalStatus]:
        scores = [r.score for r in results if r.eval_status != EvalStatus.NOT_EVALUATED and r.score is not None]
        if not scores:
            return (None, EvalStatus.NOT_EVALUATED)
        overall = sum(scores) / len(scores)
        status = EvalStatus.PASSED if overall >= threshold else EvalStatus.FAILED
        return (overall, status)


def _format_per_model_reason(per_model: list[ScoreResult], threshold: float) -> str:
    """Build a multi-line per-model breakdown string for ScoreResult.reason."""
    lines: list[str] = []
    for i, s in enumerate(per_model):
        passed = (s.score or 0.0) >= threshold
        snippet = (s.reason or "").replace("\n", " ").strip()
        if len(snippet) > 200:
            snippet = snippet[:200] + "..."
        lines.append(f"  model#{i}: score={s.score:.4f} passed={passed} reason={snippet}")
    return "\n".join(lines)


class AllPassModelsAggregator:
    """All models must pass (AND); returned score = min(scores)."""

    def aggregate_models(
        self,
        per_model: list[ScoreResult],
        threshold: float,
        weights: list[float],
    ) -> ScoreResult:
        if not per_model:
            raise ValueError("per_model must not be empty")
        scores = [s.score or 0.0 for s in per_model]
        overall = min(scores)
        passed_all = all(s >= threshold for s in scores)
        base_reason = _format_per_model_reason(per_model, threshold)
        reason = f"{base_reason}\naggregator=all_pass -> {'PASSED' if passed_all else 'FAILED'}"
        return ScoreResult(score=overall, reason=reason)


class AnyPassModelsAggregator:
    """Any model passing is enough (OR); returned score = max(scores)."""

    def aggregate_models(
        self,
        per_model: list[ScoreResult],
        threshold: float,
        weights: list[float],
    ) -> ScoreResult:
        if not per_model:
            raise ValueError("per_model must not be empty")
        scores = [s.score or 0.0 for s in per_model]
        overall = max(scores)
        passed_any = any(s >= threshold for s in scores)
        base_reason = _format_per_model_reason(per_model, threshold)
        reason = f"{base_reason}\naggregator=any_pass -> {'PASSED' if passed_any else 'FAILED'}"
        return ScoreResult(score=overall, reason=reason)


class MajorityPassModelsAggregator:
    """Strict majority must pass (passed*2 > total). Score = passed_count/total."""

    def aggregate_models(
        self,
        per_model: list[ScoreResult],
        threshold: float,
        weights: list[float],
    ) -> ScoreResult:
        if not per_model:
            raise ValueError("per_model must not be empty")
        passed_count = sum(1 for s in per_model if (s.score or 0.0) >= threshold)
        total = len(per_model)
        overall = passed_count / total if total else 0.0
        passed_majority = passed_count * 2 > total
        reason = (_format_per_model_reason(per_model, threshold) + f"\naggregator=majority_pass -> "
                  f"{'PASSED' if passed_majority else 'FAILED'} ({passed_count}/{total})")
        return ScoreResult(score=overall, reason=reason)


class AverageModelsAggregator:
    """Mean of scores."""

    def aggregate_models(
        self,
        per_model: list[ScoreResult],
        threshold: float,
        weights: list[float],
    ) -> ScoreResult:
        if not per_model:
            raise ValueError("per_model must not be empty")
        scores = [s.score or 0.0 for s in per_model]
        overall = sum(scores) / len(scores)
        reason = (_format_per_model_reason(per_model, threshold) + f"\naggregator=avg -> mean={overall:.4f}")
        return ScoreResult(score=overall, reason=reason)


class WeightedAverageModelsAggregator:
    """Weighted mean: sum(w*s)/sum(w). Zero total -> 0.0."""

    def aggregate_models(
        self,
        per_model: list[ScoreResult],
        threshold: float,
        weights: list[float],
    ) -> ScoreResult:
        if not per_model:
            raise ValueError("per_model must not be empty")
        if len(weights) != len(per_model):
            raise ValueError(f"weights length {len(weights)} must equal per_model length {len(per_model)}")
        total_w = sum(weights)
        if total_w <= 0:
            overall = 0.0
        else:
            overall = sum(w * (s.score or 0.0) for w, s in zip(weights, per_model)) / total_w
        base_reason = _format_per_model_reason(per_model, threshold)
        reason = f"{base_reason}\naggregator=weighted_avg -> weighted_mean={overall:.4f} (total_w={total_w})"
        return ScoreResult(score=overall, reason=reason)


class WeightedMajorityModelsAggregator:
    """passed_weight*2 > total_weight (strict). Score = passed_weight/total_weight."""

    def aggregate_models(
        self,
        per_model: list[ScoreResult],
        threshold: float,
        weights: list[float],
    ) -> ScoreResult:
        if not per_model:
            raise ValueError("per_model must not be empty")
        if len(weights) != len(per_model):
            raise ValueError(f"weights length {len(weights)} must equal per_model length {len(per_model)}")
        total_w = sum(weights)
        passed_w = sum(w for w, s in zip(weights, per_model) if (s.score or 0.0) >= threshold)
        if total_w <= 0:
            overall = 0.0
            passed_majority = False
        else:
            overall = passed_w / total_w
            passed_majority = passed_w * 2 > total_w
        reason = (_format_per_model_reason(per_model, threshold) + f"\naggregator=weighted_majority -> "
                  f"{'PASSED' if passed_majority else 'FAILED'} "
                  f"(passed_w={passed_w}, total_w={total_w})")
        return ScoreResult(score=overall, reason=reason)


_BUILTIN_MODELS_AGGREGATORS: dict[str, type] = {
    "all_pass": AllPassModelsAggregator,
    "any_pass": AnyPassModelsAggregator,
    "majority_pass": MajorityPassModelsAggregator,
    "avg": AverageModelsAggregator,
    "weighted_avg": WeightedAverageModelsAggregator,
    "weighted_majority": WeightedMajorityModelsAggregator,
}


def get_builtin_models_aggregator(name: str) -> Optional[ModelsAggregator]:
    """Return a built-in ModelsAggregator instance by name, or None if unknown."""
    cls = _BUILTIN_MODELS_AGGREGATORS.get(name)
    if cls is None:
        return None
    return cls()


def _extract_text_from_content(content: Any) -> str:
    """Extract plain text from Content parts (concatenate part texts)."""
    if content is None:
        return ""
    parts = content.parts
    if not parts:
        return ""
    return "\n".join((p.text or "") for p in parts).strip()


def _extract_rubrics_text(rubrics: list[Rubric]) -> str:
    """Format rubrics as lines of the form 'id: content.text'."""
    out = []
    for r in rubrics or []:
        if not r or not r.content:
            continue
        out.append(f"{r.id}: {r.content.text}")
    return "\n".join(out)


def _extract_retrieved_knowledge(
    invocation: Invocation,
    knowledge_tool_names: list[str],
) -> str:
    """Extract tool responses from the invocation for tools in knowledge_tool_names, for judge input."""
    if not knowledge_tool_names:
        return "No knowledge search results were found."
    intermediate_data = invocation.intermediate_data
    if not intermediate_data or not isinstance(intermediate_data, IntermediateData):
        return "No knowledge search results were found."
    tool_calls = get_all_tool_calls(intermediate_data)
    tool_responses = get_all_tool_responses(intermediate_data)
    allow = frozenset(knowledge_tool_names)
    parts = []
    for call, resp in zip(tool_calls, tool_responses):
        if not call or call.name not in allow:
            continue
        if not resp:
            continue
        payload = resp.response
        if payload is None:
            continue
        try:
            parts.append(json.dumps(payload, ensure_ascii=False) if isinstance(payload, (dict, list)) else str(payload))
        except (TypeError, ValueError):
            parts.append(str(payload))
    if not parts:
        return "No knowledge search results were found."
    return "\n".join(parts)


class DefaultMessagesConstructor:
    """Formats the judge user message from invocations and criterion.
    Supports llm_final_response, llm_rubric_response, llm_rubric_knowledge_recall.
    """

    def __init__(self, user_template: str) -> None:
        self._user_template = user_template

    def format_user_message(
        self,
        actuals: list[Invocation],
        expecteds: Optional[list[Invocation]],
        criterion: LLMJudgeCriterion,
        metric_name: str,
    ) -> str:
        if not actuals:
            raise ValueError("actuals is empty")
        actual = actuals[-1]
        if metric_name == "llm_final_response":
            if not expecteds:
                raise ValueError("expecteds is required for llm_final_response")
            expected = expecteds[-1]
            return self._user_template.format(
                user_prompt=_extract_text_from_content(actual.user_content),
                actual_response=_extract_text_from_content(actual.final_response),
                expected_response=_extract_text_from_content(expected.final_response),
            )
        if metric_name == "llm_rubric_response":
            if not criterion.rubrics:
                raise ValueError("llm_rubric_response requires criterion.rubrics")
            return self._user_template.format(
                user_input=_extract_text_from_content(actual.user_content),
                final_response=_extract_text_from_content(actual.final_response),
                rubrics=_extract_rubrics_text(criterion.rubrics),
            )
        if metric_name == "llm_rubric_knowledge_recall":
            if not criterion.rubrics:
                raise ValueError("llm_rubric_knowledge_recall requires criterion.rubrics")
            knowledge_tool_names = criterion.get_knowledge_tool_names()
            retrieved = _extract_retrieved_knowledge(actual, knowledge_tool_names)
            return self._user_template.format(
                user_input=_extract_text_from_content(actual.user_content),
                retrieved_knowledge=retrieved,
                rubrics=_extract_rubrics_text(criterion.rubrics),
            )
        raise ValueError(f"Unknown metric_name: {metric_name!r}")


_SALVAGE_MARK = "[salvaged]"

# Verdict tokens recognized by salvage regex; canonicalized to yes/no.
_VERDICT_YES = {"yes", "true", "pass", "passed", "valid"}
_VERDICT_NO = {"no", "false", "fail", "failed", "invalid"}


class DefaultResponseScorer:
    """Parses judge LLM output into a ScoreResult.

    Two-layer pipeline:
      1. json_repair.loads + Pydantic.model_validate (handles markdown fences,
         single quotes, trailing commas, scalar-type coercion via field
         validators on the schema classes).
      2. Regex salvage on the raw text when Pydantic rejects the dict. Extracts
         only verdict tokens; does not fabricate rubric/reason/evidence. If no
         verdict token is found, the caller raises (no silent zero-score).
    """

    def parse_response(self, response_text: str, metric_name: str) -> ScoreResult:
        if metric_name == "llm_final_response":
            return self._parse_final_response(response_text)
        if metric_name in ("llm_rubric_response", "llm_rubric_knowledge_recall"):
            return self._parse_rubric_response(response_text)
        raise ValueError(f"unknown metric_name: {metric_name!r}")

    @staticmethod
    def _load_json(text: str) -> Any:
        """Lenient JSON load via json_repair (falls back to repair parser on failure)."""
        return json_repair.loads(text or "")

    @staticmethod
    def _salvage_final_response(text: str) -> Optional[ScoreResult]:
        """Regex-extract the valid/invalid verdict; return None if not found."""
        if not text:
            return None
        m = re.search(
            r'is_the_agent_response_valid["\s:=]+["\']?(valid|invalid)\b',
            text,
            re.IGNORECASE,
        )
        if not m:
            return None
        label = m.group(1).strip().lower()
        score = 1.0 if label == "valid" else 0.0
        return ScoreResult(
            score=score,
            reason=f"{_SALVAGE_MARK} verdict={label!r}; reasoning omitted",
        )

    @staticmethod
    def _salvage_rubric_response(text: str) -> Optional[ScoreResult]:
        """Regex-extract verdict tokens; return None if none found.

        Only verdict values are scraped — id/rubric/evidence/reason are not,
        because positional alignment across free-form fields is unreliable.
        """
        if not text:
            return None
        # `\\?` tolerates escaped quotes from double-serialized JSON strings.
        matches = re.findall(
            r'\\?["\']verdict\\?["\']\s*:\s*\\?["\']([A-Za-z]+)\\?["\']',
            text,
        )
        scores: list[float] = []
        for raw in matches:
            tok = raw.strip().lower()
            if tok in _VERDICT_YES:
                scores.append(1.0)
            elif tok in _VERDICT_NO:
                scores.append(0.0)
            # Unknown tokens dropped; never mapped to a guessed score.
        if not scores:
            return None
        rubric_scores = [
            RubricScore(
                id=f"salvaged_{i}",
                reason=f"{_SALVAGE_MARK} original rubric text omitted",
                score=s,
            ) for i, s in enumerate(scores)
        ]
        avg = sum(scores) / len(scores)
        return ScoreResult(
            score=avg,
            reason=f"{_SALVAGE_MARK} extracted {len(scores)} verdict(s); rubric/evidence/reason omitted",
            rubric_scores=rubric_scores,
        )

    def _parse_final_response(self, response_text: str) -> ScoreResult:
        try:
            data = self._load_json(response_text)
            obj = FinalResponseOutput.model_validate(data)
        except Exception as e:
            salvaged = self._salvage_final_response(response_text)
            if salvaged is not None:
                return salvaged
            preview = f"; got: {(response_text or '')[:200]!r}" if response_text else ""
            raise ValueError(f"failed to parse final response JSON: {e}{preview}") from e
        label = obj.is_the_agent_response_valid.strip().lower()
        score = 1.0 if label == "valid" else 0.0
        return ScoreResult(score=score, reason=obj.reasoning.strip())

    def _parse_rubric_response(self, response_text: str) -> ScoreResult:
        try:
            data = self._load_json(response_text)
            obj = RubricJudgeOutput.model_validate(data)
        except Exception as e:
            salvaged = self._salvage_rubric_response(response_text)
            if salvaged is not None:
                return salvaged
            preview = f"; got: {(response_text or '')[:500]!r}" if response_text else ""
            raise ValueError(f"failed to parse rubric response JSON: {e}{preview}") from e
        if not obj.items:
            raise ValueError("rubric response JSON contains empty items array")
        rubric_scores: list[RubricScore] = []
        reasons: list[str] = []
        for item in obj.items:
            verdict = item.verdict.strip().lower()
            score = 1.0 if verdict == "yes" else 0.0
            rubric_scores.append(RubricScore(id=item.id, reason=item.reason.strip(), score=score), )
            reasons.append(item.reason.strip())
        avg = sum(r.score for r in rubric_scores) / len(rubric_scores)
        return ScoreResult(
            score=avg,
            reason="\n".join(reasons),
            rubric_scores=rubric_scores,
        )


FINAL_RESPONSE_PROMPT = """
You are an expert evaluator for an AI agent (Agent: a model that executes tasks).
Your job is to **only** judge whether the agent's **final answer** matches the
reference answer, and to output a fixed-format plain-text report.

### Core scoring rules

1. **The reference answer is the only Ground Truth (Ground Truth: the official
   "correct" answer used for evaluation).**
   No matter whether you personally think the reference answer might be wrong,
   outdated, or unreasonable, you **must** treat it as absolutely correct.

* Your job is not to fact-check or correct the reference answer, but to judge
  whether the agent's answer is aligned with it.
* If the agent's answer does not match the reference answer, then even if you
  think the agent is "more correct," you must mark it **invalid**.

2. **Clarification questions are never allowed.**
   If the agent asks the user for more information, requests clarification,
   asks follow-up questions, or tells the user to provide missing conditions,
   it is considered **not completing the task**, and must be marked **invalid**.
   (Examples: "Please provide more details / what exactly do you want / can you
   share the date and location?")

3. **No independent verification or calculation.**

* If the user prompt includes CSV (Comma-Separated Values, a table-like text
  format where values are separated by commas) or other tabular data: do
  **not** parse or calculate it yourself. Always follow the reference answer.
* If math, date arithmetic, or unit conversion is needed: do **not** compute it
  yourself. Always follow the reference answer.

### Input

You will receive three items wrapped in XML tags:

* <user_prompt>: the user's question
* <agent_response>: the agent's answer
* <reference_response>: the reference answer (the only Ground Truth)

### Matching rules

As long as the meaning does not change, the following differences are allowed
and can still be considered a match (**valid**):

* **Formatting differences**: list vs. paragraph; line breaks, punctuation, or
  slightly different ordering (as long as the key information is unchanged).
* **Equivalent writing**: different number formatting (e.g., 1000000 vs
  1,000,000), different capitalization.
* **Paraphrases**: as long as the key entities (Key Entities: the critical
  items required by the answer) and main components clearly align with the
  reference answer.

Must mark **invalid** in typical cases:

* **Missing key information**: the agent does not include all key entities /
  core fields required by the reference answer.
* **Key information mismatch**: numbers, conclusions, objects, units, etc.
  differ from the reference answer.

  * Pay special attention to units: for example, if the reference answer is
    100 miles but the agent writes 100 km, it must be **invalid**.
* **Clarification / deflection / refusal**: any response that asks for more
  input, turns into a question, or fails to directly provide the required
  result must be **invalid**.

### Output requirements

Your output must be a JSON object with exactly two fields:

* "reasoning": string. Briefly explain why you judged valid/invalid, pointing to
  the key aligned or misaligned points.
* "is_the_agent_response_valid": string, must be exactly "valid" or "invalid".

Example output:
{"reasoning": "The agent answered Paris which matches the reference.", "is_the_agent_response_valid": "valid"}

Requirement: be assertive and unambiguous; do not hedge. Output ONLY the JSON
object, no other text.
"""

RUBRIC_RESPONSE_PROMPT = """
# Mission

Your mission is to evaluate the quality of an AI agent's final answer. You will
be shown a user prompt (<user_prompt>), the agent's response (<response>, which
contains <final_answer>), and a rubric (<rubric>). You must use the rubric to
objectively assess whether the agent's final answer satisfies each rubric item.
Only respond to the rubric items provided. Do not invent new rubric items.

# Rubric

"yes": The final answer fulfills the rubric item, OR the rubric item's
condition was not applicable to the response.
"no": The rubric item is applicable but the final answer fails to fulfill it,
OR the rubric item requires a fact/conclusion that cannot be unambiguously
verified from <user_prompt> and <final_answer> (i.e., it is ambiguous or lacks
checkable information).

# Key Evaluation Principles

1. **Evaluate final answer content only**
   You must evaluate only whether <final_answer> satisfies each rubric item in
   <rubric>. Do not evaluate tool usage, intermediate steps, chain-of-thought,
   or any process artifacts.

2. **Restricted evidence sources**
   Your judgment may only be based on:

* the original text of <user_prompt> (the user's requirements and any given
  information), and
* the text of <final_answer> (the agent's final output).
  Do not use external knowledge, common-sense guessing, or additional
  background to "fill in" missing information.

3. **Allow semantic equivalence**
   As long as the rubric item is still satisfied, accept different wording,
   formatting, and paraphrases.
   For numbers, accept numerically equivalent expressions (different
   representations), and allow minor rounding/precision differences as long as
   they do not change the final conclusion.

4. **Conditional rubric items (not applicable => yes)**
   If a rubric item is conditional (e.g., "If … then …"), you may mark it as
   not applicable and return "yes" only if you can clearly determine from
   <user_prompt> and <final_answer> that the condition is not met.
   If you cannot determine whether the condition is met, you may not mark it as
   "probably not applicable." Treat it as not fulfilled (typically "no").

# Output Format

Your output must be a JSON object with a single key "items", whose value is an
array of objects. Each object corresponds to one rubric item and has exactly
these five fields:

* "id": The ID of the rubric item (must match the rubric numbering).
* "rubric": Repeat the rubric item word-for-word without changes.
* "evidence": Evidence text snippets from <user_prompt> and/or <final_answer>.
  If no evidence is required, explain why. If it cannot be verified, explain why.
* "reason": Your reasoning: how evidence supports/contradicts the final answer,
  or why the rubric item is not applicable.
* "verdict": exactly "yes" or "no".

Example output:
{"items": [{"id": "1", "rubric": "...", "evidence": "...", "reason": "...", "verdict": "yes"}]}

REMEMBER: Your answer will help improve the AI agent. It is important to
determine whether rubric items are fulfilled correctly. Even answering "no" can
improve the agent! Output ONLY the JSON object, no other text.

# Your Turn

## Input

<user_prompt>
<main_prompt>
{{user_input}}
</main_prompt>
</user_prompt>

<response>
  <final_answer>
  {{final_response}}
  </final_answer>
</response>

<rubric>
{{rubrics}}
</rubric>

## Output
"""

RUBRIC_KNOWLEDGE_RECALL_PROMPT = """
# Mission

Your mission is to evaluate whether the retrieved knowledge (<retrieved_knowledge>)
is relevant to the user question (<user_prompt>), and whether it is sufficient to
support each rubric item in the rubric (<rubric>).
You will be given: the user question (<user_prompt>), the retrieved documents
(<retrieved_knowledge>), and the rubric (<rubric>).
Only respond to the rubric items provided. Do not invent new rubric items.

# Rubric

"yes": The retrieved knowledge **directly supports** the key information required
by the rubric item, OR the rubric item's condition is clearly not applicable to
this user question.
"no": The rubric item is applicable, but the retrieved knowledge is missing,
insufficient, only broadly on-topic, or clearly irrelevant, and therefore cannot
support the rubric item.

# Key Evaluation Principles

1. **Trusted evidence comes from retrieved documents only**
   You may only use content from <retrieved_knowledge> as trusted evidence. Do
   not use any final answer text, model reasoning, external knowledge, or
   common-sense guessing to fill in missing information.

2. **Relevance first, and it must be answerable**
   Even if the retrieved knowledge contains correct facts, it must be relevant
   to the user's intent and usable for satisfying the corresponding rubric item.
   If it is merely "same topic / loosely related / generic background" but does
   not support the required information for the rubric item, answer "no".

3. **Sufficiency must be judged with an operational test**
   For each rubric item, use the following test to determine whether the
   retrieval is "sufficient":

* Imagine an answerer who can only see <retrieved_knowledge> (no external
  knowledge, no guessing).
* If they could complete the rubric item using only that retrieved knowledge
  (i.e., produce the key conclusion/elements required), then the item may be
  "yes".
* If they could not (missing key entities, steps, numbers, conditions,
  definitions, etc.), then it must be "no".

4. **Evidence must be close to the original text; no abstract fabrication**
   Evidence must come from <retrieved_knowledge>, with these requirements:

* Prefer **verbatim excerpts** (you may truncate, but must not change meaning).
* If you must paraphrase, it must be a **near-verbatim** paraphrase that can be
  directly located in the documents.
* If <retrieved_knowledge> contains document IDs/titles/sectioning, you must
  cite the source location in Evidence (e.g., "Doc 2 / Paragraph 3"). If there
  is no numbering, include enough raw text to make the source identifiable.

5. **Conditional rubric items (not applicable => yes)**
   If a rubric item is conditional (e.g., "If … then …"):

* You may return "yes" as not applicable only if you can **clearly determine
  from <user_prompt>** that the condition is not met.
* If you cannot determine whether the condition is met, treat the item as
  applicable; if retrieval is insufficient, return "no".
* When you return "yes" due to not-applicable, your Reason must explicitly
  state what part of <user_prompt> makes it not applicable.

6. **No extra output**
   You must output only the per-item evaluations in the format below. Do not add
   any overall summary or additional commentary.

# Internal steps for each rubric item (for internal analysis only)

1. Understand the rubric item and the key evaluation principles.
2. Collect relevant excerpts from <retrieved_knowledge> as evidence.
3. Judge whether the evidence is relevant and sufficient (using the sufficiency test).
4. Output the verdict in the required format.
   Note: These steps are for your internal analysis only and must not be output.

# Output Format

Your output must be a JSON object with a single key "items", whose value is an
array of objects. Each object corresponds to one rubric item and has exactly
these five fields:

* "id": The ID of the rubric item (must match the rubric numbering).
* "rubric": Repeat the rubric item word-for-word without changes.
* "evidence": Relevant verbatim excerpts (or near-verbatim paraphrases) from
  <retrieved_knowledge>, with source/location where possible. If there is no
  relevant evidence, write "none".
* "reason": Explain why the evidence directly supports the rubric item, or why
  evidence is missing/insufficient/irrelevant; or explain why the item is not
  applicable, and cite which part of <user_prompt> establishes that.
* "verdict": exactly "yes" or "no".

Example output:
{"items": [{"id": "1", "rubric": "...", "evidence": "...", "reason": "...", "verdict": "yes"}]}

Output ONLY the JSON object, no other text.

# Your Turn

## Input

<user_prompt>
<main_prompt>
{{user_input}}
</main_prompt>
</user_prompt>

<retrieved_knowledge>
{{retrieved_knowledge}}
</retrieved_knowledge>

<rubric>
{{rubrics}}
</rubric>

## Output
"""


def _rubric_system(prompt: str) -> str:
    """Return the system part of a rubric prompt: content before '# Your Turn' plus '## Output'."""
    return prompt.split("# Your Turn")[0].strip() + "\n\n## Output"


def _expand_env(s: str) -> str:
    """Expand environment variables in a string (e.g. $VAR or ${VAR})."""
    if not s or not isinstance(s, str):
        return s or ""
    return os.path.expandvars(s)


def _create_judge_model(opts: JudgeModelOptions) -> Any:
    """Build the underlying LLM model for one judge option.

    Provider routing:
      - provider_name empty or "openai" -> OpenAIModel(...) directly. This
        matches the framework's standard pattern for OpenAI-compatible
        endpoints (see examples/llmagent/) and ensures http_options.extra_body
        (e.g. chat_template_kwargs.enable_thinking used by judge `think` field)
        is forwarded to the backend. Routing via "openai/<name>" through
        ModelRegistry lands on LiteLLMModel whose current implementation
        drops extra_body.
      - Any other provider_name -> ModelRegistry.create_model("{provider}/{model}")
        which routes to LiteLLMModel for multi-provider support.
    """
    provider_name = _expand_env(opts.provider_name or "")
    model_name = _expand_env(opts.model_name or "")
    base_url = _expand_env(opts.base_url or "")
    api_key = _expand_env(opts.api_key or "")
    extra = dict(opts.extra_fields or {})

    if not provider_name or provider_name.lower() == "openai":
        # Direct OpenAIModel instantiation bypasses ModelRegistry regex routing,
        # so any model_name (e.g. "glm-5.1-w4afp8") works against any
        # OpenAI-compatible endpoint.
        return OpenAIModel(
            model_name=model_name,
            api_key=api_key,
            base_url=base_url or None,
            **extra,
        )

    model_str = f"{provider_name}/{model_name}"
    return ModelRegistry.create_model(
        model_str,
        api_key=api_key,
        base_url=base_url or "",
        **extra,
    )


# Default judge generation params when not specified in criterion.
DEFAULT_JUDGE_MAX_TOKENS = 4096
DEFAULT_JUDGE_TEMPERATURE = 0.8


def _merge_extra_body(
    http_options: Optional[HttpOptions],
    patch: dict[str, Any],
) -> HttpOptions:
    """Deep-merge patch into http_options.extra_body at nested-dict granularity.

    - None http_options -> returns new HttpOptions(extra_body=deepcopy(patch)).
    - For top-level keys in patch: if both sides have dict, merge recursively (deep-copying
      patch values); otherwise patch value wins.
    - Other existing top-level keys in http_options.extra_body are preserved.
    """
    base = (http_options.extra_body or {}) if http_options is not None else {}
    merged: dict[str, Any] = dict(base)
    for key, patch_val in patch.items():
        base_val = merged.get(key)
        if isinstance(base_val, dict) and isinstance(patch_val, dict):
            new_child = dict(base_val)
            for subkey, subval in patch_val.items():
                new_child[subkey] = copy.deepcopy(subval)
            merged[key] = new_child
        else:
            merged[key] = copy.deepcopy(patch_val)
    if http_options is None:
        return HttpOptions(extra_body=merged)
    return http_options.model_copy(update={"extra_body": merged})


def _judge_generation_config(
    gen: dict[str, Any] | None,
    think: Optional[bool],
) -> tuple[GenerateContentConfig, Optional[ThinkingConfig]]:
    """Build GenerateContentConfig from criterion generation_config and resolve thinking config.

    Returns (cfg, effective_thinking_config):
      - cfg: GenerateContentConfig WITHOUT thinking_config set (LlmAgent rejects it;
        thinking_config must be applied via BuiltInPlanner).
      - effective_thinking_config: None means caller should not build a planner;
        otherwise caller wraps it in BuiltInPlanner.

    Resolution order:
      1. Parse gen for base fields (max_tokens/temperature/top_p/stop/...).
      2. Parse gen["thinking_config"] dict into a candidate ThinkingConfig (not written to cfg).
      3. Parse gen["http_options"] dict into cfg.http_options (if present).
      4. If `think` is not None, override the candidate ThinkingConfig and deep-merge
         chat_template_kwargs.enable_thinking into cfg.http_options (preserving siblings).
    """
    gen = gen or {}
    cfg = GenerateContentConfig()
    cfg.max_output_tokens = (gen.get("max_tokens") or gen.get("max_output_tokens") or DEFAULT_JUDGE_MAX_TOKENS)
    cfg.temperature = gen.get("temperature", DEFAULT_JUDGE_TEMPERATURE)
    if "top_p" in gen and gen["top_p"] is not None:
        cfg.top_p = gen["top_p"]
    if "stop" in gen and gen["stop"] is not None:
        cfg.stop_sequences = gen["stop"] if isinstance(gen["stop"], list) else [gen["stop"]]
    elif "stop_sequences" in gen and gen["stop_sequences"] is not None:
        cfg.stop_sequences = gen["stop_sequences"]
    if "presence_penalty" in gen and gen["presence_penalty"] is not None:
        setattr(cfg, "presence_penalty", gen["presence_penalty"])
    if "frequency_penalty" in gen and gen["frequency_penalty"] is not None:
        setattr(cfg, "frequency_penalty", gen["frequency_penalty"])

    # Parse thinking_config dict from generation_config (candidate; may be overridden by `think`).
    effective_thinking_config: Optional[ThinkingConfig] = None
    tc_dict = gen.get("thinking_config")
    if isinstance(tc_dict, dict):
        effective_thinking_config = ThinkingConfig(**tc_dict)

    # Parse http_options dict from generation_config, if any.
    http_opts_dict = gen.get("http_options")
    if isinstance(http_opts_dict, dict):
        cfg.http_options = HttpOptions(**http_opts_dict)

    # `think` field overrides both paths when set.
    if think is True:
        effective_thinking_config = ThinkingConfig(
            include_thoughts=True,
            thinking_budget=-1,
        )
        cfg.http_options = _merge_extra_body(
            cfg.http_options,
            {"chat_template_kwargs": {
                "enable_thinking": True
            }},
        )
    elif think is False:
        effective_thinking_config = ThinkingConfig(
            include_thoughts=False,
            thinking_budget=0,
        )
        cfg.http_options = _merge_extra_body(
            cfg.http_options,
            {"chat_template_kwargs": {
                "enable_thinking": False
            }},
        )

    return cfg, effective_thinking_config


class _JudgeAgent:
    """Runs the judge via LlmAgent: system as instruction, single turn, optional output_schema."""

    def __init__(
        self,
        model: Any,
        config: GenerateContentConfig,
        system_prompt: str,
        output_schema: Optional[type[PydanticBaseModel]] = None,
        tools: Optional[list] = None,
        planner: Optional[Any] = None,
    ) -> None:
        self._agent = LlmAgent(
            name="judge",
            model=model,
            instruction=system_prompt,
            generate_content_config=config,
            add_name_to_instruction=False,
            output_schema=output_schema,
            tools=tools or [],
            planner=planner,
        )
        self._session_service = InMemorySessionService()

    async def get_response(self, user_message: str) -> str:
        user_content = Content(role="user", parts=[Part.from_text(text=user_message)])
        agent_context = create_agent_context()
        session = await self._session_service.create_session(
            app_name="eval",
            user_id="judge",
            session_id=str(uuid.uuid4()),
            agent_context=agent_context,
        )
        ctx = InvocationContext(
            session_service=self._session_service,
            invocation_id=new_invocation_context_id(),
            agent=self._agent,
            session=session,
            agent_context=agent_context,
            user_content=user_content,
            override_messages=[user_content],
        )
        last_text = ""
        async for event in self._agent.run_async(ctx):
            if not event.is_final_response():
                continue
            if not event.content or not event.content.parts:
                continue
            part_text = "\n".join((p.text or "").strip() for p in event.content.parts if p.thought is not True).strip()
            if part_text:
                last_text += part_text
        return last_text.strip()


class LLMJudge:
    """Builds judge agent(s) from eval_metric. Supports 1..N judge models with cross-model aggregation.

    Pluggable: messages_constructor, response_scorer, samples_aggregator, invocations_aggregator,
    models_aggregator, judge_tools.

    models_aggregator resolution order:
      1) explicit constructor argument (if any)
      2) registry-registered ModelsAggregator for metric_name (resolved by caller, e.g. _judge_for_metric)
      3) criterion.models_aggregator string -> built-in 6 names
      4) fallback: all_pass
    """

    def __init__(
        self,
        eval_metric: EvalMetric,
        *,
        messages_constructor: Optional[MessagesConstructor] = None,
        response_scorer: Optional[ResponseScorer] = None,
        samples_aggregator: Optional[SamplesAggregator] = None,
        invocations_aggregator: Optional[InvocationsAggregator] = None,
        models_aggregator: Optional[ModelsAggregator] = None,
        judge_tools: Optional[list] = None,
    ) -> None:
        if not eval_metric:
            raise ValueError("LLMJudge requires eval_metric")
        self._eval_metric = eval_metric
        criterion = get_llm_criterion_from_metric(eval_metric)
        if not criterion:
            raise ValueError("eval_metric.criterion.llmJudge is required")
        judge_models_list = criterion.get_judge_models()
        if not judge_models_list:
            raise ValueError("eval_metric.criterion.llmJudge requires either judge_model or judge_models")
        self._criterion = criterion
        self._metric_name = eval_metric.metric_name or ""
        self._judge_models: list[JudgeModelOptions] = judge_models_list
        self._parallel: bool = bool(criterion.parallel)

        # Resolve models_aggregator: explicit > built-in name lookup > error.
        resolved_models_agg = models_aggregator
        if resolved_models_agg is None:
            agg_name = criterion.models_aggregator or "all_pass"
            built = get_builtin_models_aggregator(agg_name)
            if built is None:
                raise ValueError(f"models_aggregator {agg_name!r} is not a built-in name; "
                                 f"register it via LLM_EVALUATOR_REGISTRY.register_models_aggregator "
                                 f"before constructing LLMJudge")
            resolved_models_agg = built
        self._models_aggregator: ModelsAggregator = resolved_models_agg

        # Pick metric-specific system prompt + user template + output schema (unchanged from before).
        if self._metric_name == "llm_final_response":
            system_prompt = FINAL_RESPONSE_PROMPT
            user_template = ("<user_prompt>\n"
                             "{user_prompt}\n"
                             "</user_prompt>\n"
                             "\n"
                             "<agent_response>\n"
                             "{actual_response}\n"
                             "</agent_response>\n"
                             "\n"
                             "<reference_response>\n"
                             "{expected_response}\n"
                             "</reference_response>")
            output_schema: Optional[type[PydanticBaseModel]] = FinalResponseOutput
        elif self._metric_name == "llm_rubric_response":
            system_prompt = _rubric_system(RUBRIC_RESPONSE_PROMPT)
            user_template = ("<user_prompt>\n"
                             "<main_prompt>\n"
                             "{user_input}\n"
                             "</main_prompt>\n"
                             "</user_prompt>\n"
                             "\n"
                             "<response>\n"
                             "  <final_answer>\n"
                             "  {final_response}\n"
                             "  </final_answer>\n"
                             "</response>\n"
                             "\n"
                             "<rubric>\n"
                             "{rubrics}\n"
                             "</rubric>")
            output_schema = RubricJudgeOutput
        elif self._metric_name == "llm_rubric_knowledge_recall":
            system_prompt = _rubric_system(RUBRIC_KNOWLEDGE_RECALL_PROMPT)
            user_template = ("<user_prompt>\n"
                             "<main_prompt>\n"
                             "{user_input}\n"
                             "</main_prompt>\n"
                             "</user_prompt>\n"
                             "\n"
                             "<retrieved_knowledge>\n"
                             "{retrieved_knowledge}\n"
                             "</retrieved_knowledge>\n"
                             "\n"
                             "<rubric>\n"
                             "{rubrics}\n"
                             "</rubric>")
            output_schema = RubricJudgeOutput
        else:
            raise ValueError(f"Unsupported metric_name for LLMJudge: {self._metric_name!r}")

        # Build one _JudgeAgent per judge model option, in order.
        self._judge_agents: list[_JudgeAgent] = []
        for opts in judge_models_list:
            model = _create_judge_model(opts)
            cfg, effective_tc = _judge_generation_config(opts.generation_config, opts.think)
            planner = (BuiltInPlanner(thinking_config=effective_tc) if effective_tc is not None else None)
            self._judge_agents.append(
                _JudgeAgent(
                    model,
                    cfg,
                    system_prompt,
                    output_schema=output_schema,
                    tools=judge_tools,
                    planner=planner,
                ))

        self._messages_constructor = messages_constructor or DefaultMessagesConstructor(user_template)
        self._response_scorer = response_scorer or DefaultResponseScorer()
        self._samples_aggregator = samples_aggregator or MajorityVoteSamplesAggregator()
        self._invocations_aggregator = invocations_aggregator or AverageInvocationsAggregator()

    def get_num_samples(self) -> int:
        """Return num_samples for the *first* judge model (legacy single-model API).

        Multi-model judges may use different num_samples per model; callers that need
        per-model sample counts should iterate criterion.get_judge_models() directly.
        """
        return self._criterion.get_num_samples()

    async def _run_one_judge(
        self,
        agent_index: int,
        opts: JudgeModelOptions,
        user_message: str,
        threshold: float,
    ) -> "tuple[NamedScoreResult, ScoreResult, bool]":
        """Run num_samples calls for one judge model, then SamplesAggregator.

        Returns (named_score, raw_score_result, had_exception). On exception, returns
        a soft-failure NamedScoreResult with passed=False, score=0.0, reason=str(exc),
        and had_exception=True.
        """
        agent = self._judge_agents[agent_index]
        n = opts.get_num_samples()
        try:
            samples: list[ScoreResult] = []
            for _ in range(n):
                response_text = await agent.get_response(user_message)
                samples.append(self._response_scorer.parse_response(response_text, self._metric_name))
            chosen = self._samples_aggregator.aggregate_samples(samples, threshold)
        except Exception as exc:
            named = NamedScoreResult(
                model_name=opts.model_name or "",
                provider_name=opts.provider_name or "",
                score=0.0,
                reason=str(exc),
                rubric_scores=[],
                passed=False,
            )
            return named, ScoreResult(score=0.0, reason=str(exc)), True
        passed = (chosen.score or 0.0) >= threshold
        named = NamedScoreResult(
            model_name=opts.model_name or "",
            provider_name=opts.provider_name or "",
            score=chosen.score or 0.0,
            reason=chosen.reason or "",
            rubric_scores=list(chosen.rubric_scores or []),
            passed=passed,
        )
        return named, chosen, False

    async def evaluate(
        self,
        actual_invocations: list[Invocation],
        expected_invocations: Optional[list[Invocation]],
    ) -> EvaluationResult:
        """Run multi-model judge per invocation, aggregate per-model + per-invocation."""
        if expected_invocations is None:
            expected_invocations = []
        if len(actual_invocations) != len(expected_invocations):
            raise ValueError(f"actual_invocations ({len(actual_invocations)}) and "
                             f"expected_invocations ({len(expected_invocations)}) length mismatch")

        threshold = self._eval_metric.threshold
        weights = [m.weight for m in self._judge_models]
        per_invocation_results: list[PerInvocationResult] = []

        for i in range(len(actual_invocations)):
            actual = actual_invocations[i]
            expected = expected_invocations[i] if i < len(expected_invocations) else None

            user_message = self._messages_constructor.format_user_message(
                actual_invocations[:i + 1],
                expected_invocations[:i + 1] if expected_invocations else None,
                self._criterion,
                self._metric_name,
            )

            # Step 1: each model runs its own samples + SamplesAggregator -> (named, raw, had_exception)
            if self._parallel and len(self._judge_models) > 1:
                tasks = [
                    self._run_one_judge(idx, opts, user_message, threshold)
                    for idx, opts in enumerate(self._judge_models)
                ]
                triples = await asyncio.gather(*tasks)
            else:
                triples = []
                for idx, opts in enumerate(self._judge_models):
                    triples.append(await self._run_one_judge(idx, opts, user_message, threshold))

            named_results: list[NamedScoreResult] = [t[0] for t in triples]
            score_results: list[ScoreResult] = [t[1] for t in triples]
            exceptions: list[bool] = [t[2] for t in triples]

            # Step 2: if every model raised, mark NOT_EVALUATED.
            all_exception = all(exceptions) and len(exceptions) > 0

            if all_exception:
                per_invocation_results.append(
                    PerInvocationResult(
                        actual_invocation=actual,
                        expected_invocation=expected,
                        score=None,
                        eval_status=EvalStatus.NOT_EVALUATED,
                        reason="all judge models failed: " + "; ".join(f"{n.model_name}={n.reason}"
                                                                       for n in named_results),
                        rubric_scores=None,
                        per_model_scores=named_results,
                    ))
                continue

            # Step 3: cross-model aggregation -> single ScoreResult
            invocation_score = self._models_aggregator.aggregate_models(
                score_results,
                threshold,
                weights,
            )
            status = (EvalStatus.PASSED if (invocation_score.score or 0.0) >= threshold else EvalStatus.FAILED)
            rubric_scores = (list(invocation_score.rubric_scores) if invocation_score.rubric_scores else None)
            per_invocation_results.append(
                PerInvocationResult(
                    actual_invocation=actual,
                    expected_invocation=expected,
                    score=invocation_score.score,
                    eval_status=status,
                    reason=invocation_score.reason or None,
                    rubric_scores=rubric_scores,
                    per_model_scores=named_results,
                ))

        overall_score, overall_status = self._invocations_aggregator.aggregate_invocations(
            per_invocation_results,
            threshold,
        )
        return EvaluationResult(
            overall_score=overall_score,
            overall_eval_status=overall_status,
            per_invocation_results=per_invocation_results,
        )
