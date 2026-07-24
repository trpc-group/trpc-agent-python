#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Patch-free offline adapters for Agent, judge, reflection, and trace replay."""

from __future__ import annotations

import json
import re
import threading
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.evaluation import EvalCase
from trpc_agent_sdk.evaluation import EvalSet
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.models import LlmResponse
from trpc_agent_sdk.models import ModelRegistry
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import GenerateContentResponseUsageMetadata
from trpc_agent_sdk.types import Part

_VARIANT_RE = re.compile(r"\[variant:\s*([a-zA-Z0-9_-]+)\]", re.IGNORECASE)


def _content_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts = getattr(content, "parts", None) or []
    return "\n".join(str(getattr(part, "text", "") or "") for part in parts).strip()


def _invocation_response_text(payload: dict[str, Any]) -> str:
    response = payload.get("final_response") or {}
    return "\n".join(str(part.get("text", "")) for part in response.get("parts", [])).strip()


def _request_text(request: LlmRequest) -> str:
    return "\n".join(_content_text(content) for content in (request.contents or [])).strip()


def _system_text(request: LlmRequest) -> str:
    config = request.config
    if config is None:
        return ""
    return _content_text(getattr(config, "system_instruction", None))


def _tag(text: str, name: str) -> str:
    match = re.search(
        rf"<{re.escape(name)}>\s*(.*?)\s*</{re.escape(name)}>",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    return match.group(1).strip() if match else ""


class OfflineModel(LLMModel):
    """One deterministic provider with agent, judge, and reflector model names."""

    _catalog: dict[str, EvalCase] = {}
    _candidate_prompts: list[str] = []
    _configuration_lock = threading.Lock()

    @classmethod
    def supported_models(cls) -> list[str]:
        return [r"offline/.*"]

    @classmethod
    def configure(
        cls,
        *,
        eval_sets: list[EvalSet],
        candidate_prompts: list[str],
    ) -> None:
        """Replace immutable replay inputs before a pipeline run."""
        catalog: dict[str, EvalCase] = {}
        for eval_set in eval_sets:
            for case in eval_set.eval_cases:
                conversation = case.conversation or []
                if not conversation:
                    continue
                query = _content_text(conversation[0].user_content)
                if not query:
                    raise ValueError(f"case {case.eval_id!r} has an empty offline replay query")
                if query in catalog:
                    previous = catalog[query]
                    raise ValueError("offline replay queries must be unique; "
                                     f"cases {previous.eval_id!r} and {case.eval_id!r} share the same query")
                catalog[query] = case
        with cls._configuration_lock:
            cls._catalog = catalog
            cls._candidate_prompts = list(candidate_prompts)

    def __init__(self, model_name: str, **kwargs: Any) -> None:
        super().__init__(model_name=model_name, **kwargs)
        self._reflection_index = 0
        self._reflection_lock = threading.Lock()

    def validate_request(self, request: LlmRequest) -> None:
        if not request.contents:
            raise ValueError("offline model requires at least one content item")

    async def _generate_async_impl(
        self,
        request: LlmRequest,
        stream: bool = False,
        ctx: Any = None,
    ) -> AsyncGenerator[LlmResponse, None]:
        del stream, ctx
        if self.name == "offline/agent":
            text = self._agent_response(request)
        elif self.name == "offline/judge":
            text = self._judge_response(request)
        elif self.name == "offline/reflector":
            text = self._reflection_response()
        else:
            raise ValueError(f"unsupported offline model: {self.name}")

        prompt_text = "\n\n".join(item for item in (_system_text(request), _request_text(request)) if item)
        prompt_tokens = max(1, len(prompt_text) // 4)
        completion_tokens = max(1, len(text) // 4)
        usage = GenerateContentResponseUsageMetadata(
            prompt_token_count=prompt_tokens,
            candidates_token_count=completion_tokens,
            total_token_count=prompt_tokens + completion_tokens,
        )
        yield LlmResponse(
            content=Content(role="model", parts=[Part.from_text(text=text)]),
            usage_metadata=usage,
            partial=False,
        )

    def _agent_response(self, request: LlmRequest) -> str:
        query = _request_text(request)
        case = self._catalog.get(query)
        if case is None:
            raise ValueError(f"offline replay has no case for query: {query!r}")
        variant_match = _VARIANT_RE.search(_system_text(request))
        variant = variant_match.group(1).lower() if variant_match else "baseline"
        state = case.session_input.state if case.session_input else {}
        traces = state.get("variant_traces") or {}
        payload = traces.get(variant) or traces.get("baseline")
        if not isinstance(payload, dict):
            raise ValueError(f"case {case.eval_id!r} has no trace for variant {variant!r}")
        return _invocation_response_text(payload)

    def _reflection_response(self) -> str:
        with self._reflection_lock:
            if not self._candidate_prompts:
                raise ValueError("offline reflector has no configured candidate prompts")
            index = min(self._reflection_index, len(self._candidate_prompts) - 1)
            self._reflection_index += 1
            prompt = self._candidate_prompts[index]
        return f"```\n{prompt.rstrip()}\n```"

    def _judge_response(self, request: LlmRequest) -> str:
        prompt = _request_text(request)
        query = _tag(_tag(prompt, "user_prompt"), "main_prompt")
        case = self._catalog.get(query)
        rubrics_text = _tag(prompt, "rubric")
        rubric_lines = [line.strip() for line in rubrics_text.splitlines() if ":" in line]
        signals = self._resolve_signals(case, prompt)
        items: list[dict[str, str]] = []
        for line in rubric_lines:
            rubric_id, rubric = line.split(":", 1)
            normalized = rubric_id.strip().lower()
            if "format" in normalized:
                passed = signals.get("format_pass", False)
            elif "knowledge" in normalized or "recall" in normalized:
                passed = signals.get("knowledge_recall_pass", False)
            else:
                passed = signals.get("llm_rubric_pass", False)
            items.append({
                "id":
                rubric_id.strip(),
                "rubric":
                rubric.strip(),
                "evidence":
                "offline deterministic replay signal",
                "reason": ("replay evidence satisfies rubric" if passed else "replay evidence does not satisfy rubric"),
                "verdict":
                "yes" if passed else "no",
            })
        if not items:
            items.append({
                "id": "offline",
                "rubric": "offline fallback",
                "evidence": "no rubric line parsed",
                "reason": "offline fallback rejects malformed judge prompt",
                "verdict": "no",
            })
        return json.dumps({"items": items}, ensure_ascii=False)

    @classmethod
    def _resolve_signals(cls, case: EvalCase | None, judge_prompt: str) -> dict[str, bool]:
        if case is None or case.session_input is None:
            return {}
        state = case.session_input.state
        traces = state.get("variant_traces") or {}
        final_answer = _tag(judge_prompt, "final_answer")
        retrieved = _tag(judge_prompt, "retrieved_knowledge")
        for payload in traces.values():
            if not isinstance(payload, dict):
                continue
            response_matches = final_answer and _invocation_response_text(payload) == final_answer
            knowledge_matches = False
            intermediate = payload.get("intermediate_data") or {}
            for tool_response in intermediate.get("tool_responses") or []:
                encoded = json.dumps(tool_response.get("response"), ensure_ascii=False)
                if encoded and encoded in retrieved:
                    knowledge_matches = True
                    break
            if response_matches or knowledge_matches:
                raw = payload.get("signals") or {}
                return {
                    "format_pass": bool(raw.get("format_pass", True)),
                    "knowledge_recall_pass": bool(raw.get("knowledge_recall_pass", True)),
                    "llm_rubric_pass": bool(raw.get("llm_rubric_pass", True)),
                }
        expected_tools = []
        for invocation in case.conversation or []:
            intermediate = invocation.intermediate_data
            if intermediate is not None:
                expected_tools.extend(getattr(tool, "name", "") for tool in getattr(intermediate, "tool_uses", []))
        if _tag(judge_prompt, "retrieved_knowledge") and "search_policy" not in expected_tools:
            return {
                "format_pass": True,
                "knowledge_recall_pass": True,
                "llm_rubric_pass": True,
            }
        return {}


def configure_offline_models(
    *,
    eval_sets: list[EvalSet],
    candidate_prompts: list[str],
) -> None:
    """Register the provider and configure a fresh deterministic replay."""
    ModelRegistry.register(OfflineModel)
    ModelRegistry.resolve.cache_clear()
    OfflineModel.configure(
        eval_sets=eval_sets,
        candidate_prompts=candidate_prompts,
    )


def create_offline_call_agent(prompt_paths: dict[str, Path]):
    """Build the optimizer's async ``call_agent`` using a real :class:`LlmAgent`."""

    async def call_agent(query: str) -> str:
        instructions = []
        for name, path in prompt_paths.items():
            instructions.append(f"## {name}\n{path.read_text(encoding='utf-8')}")
        root_agent = LlmAgent(
            name="offline_support_agent",
            model=OfflineModel(model_name="offline/agent"),
            instruction="\n\n".join(instructions),
            add_name_to_instruction=False,
        )
        session_service = InMemorySessionService()
        runner = Runner(
            app_name="eval_optimize_loop",
            agent=root_agent,
            session_service=session_service,
        )
        session_id = str(uuid.uuid4())
        await session_service.create_session(
            app_name="eval_optimize_loop",
            user_id="offline",
            session_id=session_id,
            state={},
        )
        final_text = ""
        async for event in runner.run_async(
                user_id="offline",
                session_id=session_id,
                new_message=Content(role="user", parts=[Part.from_text(text=query)]),
        ):
            if not event.is_final_response() or not event.content:
                continue
            for part in event.content.parts or []:
                if not part.thought and part.text:
                    final_text += part.text
        return final_text.strip()

    return call_agent
