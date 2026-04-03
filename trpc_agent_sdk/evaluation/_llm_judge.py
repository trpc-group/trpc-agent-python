# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""LLM Judge: build a judge agent from eval_metric and run evaluation via the agent."""

from __future__ import annotations

import json
import os
import uuid
from typing import Any
from typing import Optional
from typing import Protocol

from pydantic import BaseModel as PydanticBaseModel

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.context import create_agent_context
from trpc_agent_sdk.context import new_invocation_context_id
from trpc_agent_sdk.models import ModelRegistry
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import GenerateContentConfig
from trpc_agent_sdk.types import Part

from ._eval_case import IntermediateData
from ._eval_case import Invocation
from ._eval_case import get_all_tool_calls
from ._eval_case import get_all_tool_responses
from ._eval_metrics import EvalMetric
from ._eval_metrics import EvalStatus
from ._eval_result import EvaluationResult
from ._eval_result import PerInvocationResult
from ._llm_criterion import LLMJudgeCriterion
from ._llm_criterion import Rubric
from ._llm_criterion import RubricScore
from ._llm_criterion import ScoreResult
from ._llm_criterion import get_llm_criterion_from_metric


class FinalResponseOutput(PydanticBaseModel):
    """Pydantic schema for llm_final_response judge output (reasoning + valid/invalid)."""
    reasoning: str
    is_the_agent_response_valid: str  # Must be "valid" or "invalid"


class RubricItemOutput(PydanticBaseModel):
    """Schema for a single rubric item in judge output (id, rubric, evidence, reason, verdict)."""
    id: str
    rubric: str
    evidence: str
    reason: str
    verdict: str  # "yes" or "no"


class RubricJudgeOutput(PydanticBaseModel):
    """Pydantic schema for llm_rubric_response and llm_rubric_knowledge_recall judge output."""
    items: list[RubricItemOutput]


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


class DefaultResponseScorer:
    """Parses judge JSON into ScoreResult; dispatches by metric (final_response vs rubric)."""

    def parse_response(self, response_text: str, metric_name: str) -> ScoreResult:
        if metric_name == "llm_final_response":
            return self._parse_final_response(response_text)
        if metric_name in ("llm_rubric_response", "llm_rubric_knowledge_recall"):
            return self._parse_rubric_response(response_text)
        raise ValueError(f"unknown metric_name: {metric_name!r}")

    @staticmethod
    def _extract_json(text: str) -> str:
        """Extract the first JSON object or array from text, stripping any surrounding non-JSON content.

        Example: when the judge model returns Markdown-wrapped JSON, we strip the fence
        so that model_validate_json receives valid JSON instead of failing on the prefix:

            input:  '```json\\n{"items": [{"id": "1", "verdict": "yes"}]}\\n```'
            output: '{"items": [{"id": "1", "verdict": "yes"}]}'

        Without this, Pydantic would raise: Invalid JSON: expected value at line 1 column 1
        (because the first character is '`' rather than '{' or '[').
        """
        text = (text or "").strip()
        # Strip markdown code fence (e.g. ```json ... ```) so model output wrapped in code block still parses
        if text.startswith("```"):
            first_line, _, rest = text.partition("\n")
            if first_line in ("```json", "```"):
                text = rest
            text = text.strip()
        if text.endswith("```"):
            text = text[:-3].strip()
        for start_char, end_char in [("{", "}"), ("[", "]")]:
            start = text.find(start_char)
            if start == -1:
                continue
            end = text.rfind(end_char)
            if end > start:
                return text[start:end + 1]
        return text

    def _parse_final_response(self, response_text: str) -> ScoreResult:
        try:
            obj = FinalResponseOutput.model_validate_json(self._extract_json(response_text))
        except Exception as e:
            raise ValueError(f"failed to parse final response JSON: {e}" +
                             (f"; got: {(response_text or '')[:200]!r}" if response_text else "")) from e
        label = obj.is_the_agent_response_valid.strip().lower()
        score = 1.0 if label == "valid" else 0.0
        return ScoreResult(score=score, reason=obj.reasoning.strip())

    def _parse_rubric_response(self, response_text: str) -> ScoreResult:
        try:
            obj = RubricJudgeOutput.model_validate_json(self._extract_json(response_text))
        except Exception as e:
            raise ValueError(f"failed to parse rubric response JSON: {e}" +
                             (f"; got: {(response_text or '')[:500]!r}" if response_text else "")) from e
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


# Default judge generation params when not specified in criterion.
DEFAULT_JUDGE_MAX_TOKENS = 4096
DEFAULT_JUDGE_TEMPERATURE = 0.8


def _judge_generation_config(gen: dict[str, Any] | None) -> GenerateContentConfig:
    """Build GenerateContentConfig from criterion generation_config; use defaults for missing fields."""
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
    return cfg


class _JudgeAgent:
    """Runs the judge via LlmAgent: system as instruction, single turn, optional output_schema."""

    def __init__(
        self,
        model: Any,
        config: GenerateContentConfig,
        system_prompt: str,
        output_schema: Optional[type[PydanticBaseModel]] = None,
        tools: Optional[list] = None,
    ) -> None:
        self._agent = LlmAgent(
            name="judge",
            model=model,
            instruction=system_prompt,
            generate_content_config=config,
            add_name_to_instruction=False,
            output_schema=output_schema,
            tools=tools or [],
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
    """Builds a judge agent from eval_metric.
    Pluggable: messages constructor, response scorer, samples/invocations aggregators.
    Defaults used when not provided.
    """

    def __init__(
        self,
        eval_metric: EvalMetric,
        *,
        messages_constructor: Optional[MessagesConstructor] = None,
        response_scorer: Optional[ResponseScorer] = None,
        samples_aggregator: Optional[SamplesAggregator] = None,
        invocations_aggregator: Optional[InvocationsAggregator] = None,
        judge_tools: Optional[list] = None,
    ) -> None:
        if not eval_metric:
            raise ValueError("LLMJudge requires eval_metric")
        self._eval_metric = eval_metric
        criterion = get_llm_criterion_from_metric(eval_metric)
        if not criterion or not criterion.judge_model:
            raise ValueError("eval_metric.criterion.llmJudge with judge_model is required")
        self._criterion = criterion
        self._metric_name = eval_metric.metric_name or ""

        opts = criterion.judge_model
        provider_name = _expand_env(opts.provider_name or "")
        model_name = _expand_env(opts.model_name or "")
        base_url = _expand_env(opts.base_url or "")
        api_key = _expand_env(opts.api_key or "")
        model_str = f"{provider_name or 'openai'}/{model_name}"
        extra = dict(opts.extra_fields or {})
        model = ModelRegistry.create_model(
            model_str,
            api_key=api_key,
            base_url=base_url or "",
            **extra,
        )
        cfg = _judge_generation_config(opts.generation_config)

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
        else:
            raise ValueError(f"Unsupported metric_name for LLMJudge: {self._metric_name!r}")

        if self._metric_name == "llm_final_response":
            output_schema: Optional[type[PydanticBaseModel]] = FinalResponseOutput
        else:
            output_schema = RubricJudgeOutput

        self._agent = _JudgeAgent(model, cfg, system_prompt, output_schema=output_schema, tools=judge_tools)

        self._messages_constructor = messages_constructor or DefaultMessagesConstructor(user_template)
        self._response_scorer = response_scorer or DefaultResponseScorer()
        self._samples_aggregator = samples_aggregator or MajorityVoteSamplesAggregator()
        self._invocations_aggregator = invocations_aggregator or AverageInvocationsAggregator()

    def get_num_samples(self) -> int:
        """Return the number of judge samples to run per invocation (e.g. for majority vote)."""
        return self._criterion.get_num_samples()

    async def evaluate(
        self,
        actual_invocations: list[Invocation],
        expected_invocations: Optional[list[Invocation]],
    ) -> EvaluationResult:
        """Run the judge for each invocation, aggregate samples then invocations, and return EvaluationResult."""
        if expected_invocations is None:
            expected_invocations = []
        if len(actual_invocations) != len(expected_invocations):
            raise ValueError(f"actual_invocations ({len(actual_invocations)}) and "
                             f"expected_invocations ({len(expected_invocations)}) length mismatch")
        num_samples = self.get_num_samples()
        if num_samples <= 0:
            raise ValueError("num_samples must be greater than 0")

        threshold = self._eval_metric.threshold
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

            samples: list[ScoreResult] = []
            for _ in range(num_samples):
                response_text = await self._agent.get_response(user_message)
                samples.append(self._response_scorer.parse_response(response_text, self._metric_name))

            chosen = self._samples_aggregator.aggregate_samples(samples, threshold)
            status = EvalStatus.PASSED if (chosen.score or 0) >= threshold else EvalStatus.FAILED
            rubric_scores = (list(chosen.rubric_scores) if chosen.rubric_scores else None)
            per_invocation_results.append(
                PerInvocationResult(
                    actual_invocation=actual,
                    expected_invocation=expected,
                    score=chosen.score,
                    eval_status=status,
                    reason=chosen.reason or None,
                    rubric_scores=rubric_scores,
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
