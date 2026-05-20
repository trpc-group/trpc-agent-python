# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Synchronous LLM callable for optimizer prompt-rewrite operations.

Conforms to gepa's ``LanguageModel`` Protocol so the same instance
serves as ``reflection_lm`` for ``gepa.optimize``. Internally drives a
framework :class:`LlmAgent` so optimize-model configuration honours
the framework's provider routing, env-variable expansion, and
``extra_fields`` pass-through.
"""

from __future__ import annotations

import asyncio
import copy
import os
import uuid
from typing import Any
from typing import Optional
from typing import Union

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

from ._optimize_model_options import OptimizeModelOptions

DEFAULT_OPTIMIZE_MAX_TOKENS = 4096
DEFAULT_OPTIMIZE_TEMPERATURE = 0.8


def _expand_env(s: str) -> str:
    """Expand environment variables in a string (e.g. $VAR or ${VAR})."""
    if not s or not isinstance(s, str):
        return s or ""
    return os.path.expandvars(s)


def _merge_extra_body(
    http_options: Optional[HttpOptions],
    patch: dict[str, Any],
) -> HttpOptions:
    """Deep-merge patch into http_options.extra_body at nested-dict granularity."""
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


def _create_optimize_model(opts: OptimizeModelOptions) -> Any:
    """Build the underlying LLM model for an optimizer's LLM-driven operations.

    Provider routing:
      - provider_name empty or "openai" -> OpenAIModel(...) directly. This
        matches the framework's standard pattern for OpenAI-compatible
        endpoints and forwards http_options.extra_body to the backend.
      - Any other provider_name -> ModelRegistry.create_model("{provider}/{model}")
        which routes to LiteLLMModel for multi-provider support.
    """
    provider_name = _expand_env(opts.provider_name or "")
    model_name = _expand_env(opts.model_name or "")
    base_url = _expand_env(opts.base_url or "")
    api_key = _expand_env(opts.api_key or "")
    extra = dict(opts.extra_fields or {})

    if not provider_name or provider_name.lower() == "openai":
        return OpenAIModel(
            model_name=model_name,
            api_key=api_key,
            base_url=base_url or None,
            **extra,
        )

    return ModelRegistry.create_model(
        f"{provider_name}/{model_name}",
        api_key=api_key,
        base_url=base_url or "",
        **extra,
    )


# yapf: disable
def _build_optimize_generation_config(
    opts: OptimizeModelOptions,
) -> tuple[GenerateContentConfig, Optional[ThinkingConfig]]:
    # yapf: enable
    """Build (GenerateContentConfig, ThinkingConfig | None) from OptimizeModelOptions.

    Returns thinking_config separately because LlmAgent rejects it on
    GenerateContentConfig and requires it via BuiltInPlanner.

    Resolution order:
      1. Base fields (max_tokens/temperature/top_p/stop/...) from generation_config.
      2. thinking_config dict -> candidate ThinkingConfig (not written to cfg).
      3. http_options dict -> cfg.http_options (if present).
      4. opts.think overrides both paths when set.
    """
    gen = opts.generation_config or {}
    cfg = GenerateContentConfig()
    cfg.max_output_tokens = (gen.get("max_tokens") or gen.get("max_output_tokens") or DEFAULT_OPTIMIZE_MAX_TOKENS)
    cfg.temperature = gen.get("temperature", DEFAULT_OPTIMIZE_TEMPERATURE)
    if "top_p" in gen and gen["top_p"] is not None:
        cfg.top_p = gen["top_p"]
    if "stop" in gen and gen["stop"] is not None:
        cfg.stop_sequences = (gen["stop"] if isinstance(gen["stop"], list) else [gen["stop"]])
    elif "stop_sequences" in gen and gen["stop_sequences"] is not None:
        cfg.stop_sequences = gen["stop_sequences"]
    if "presence_penalty" in gen and gen["presence_penalty"] is not None:
        setattr(cfg, "presence_penalty", gen["presence_penalty"])
    if "frequency_penalty" in gen and gen["frequency_penalty"] is not None:
        setattr(cfg, "frequency_penalty", gen["frequency_penalty"])

    effective_thinking_config: Optional[ThinkingConfig] = None
    tc_dict = gen.get("thinking_config")
    if isinstance(tc_dict, dict):
        effective_thinking_config = ThinkingConfig(**tc_dict)

    http_opts_dict = gen.get("http_options")
    if isinstance(http_opts_dict, dict):
        cfg.http_options = HttpOptions(**http_opts_dict)

    if opts.think is True:
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
    elif opts.think is False:
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


def _extract_final_text(event: Any) -> str:
    """Collect non-thought text from a single LlmAgent final-response event.

    Returns empty string when the event is not a final response, lacks content,
    or contains only thought parts.
    """
    if not event.is_final_response():
        return ""
    if not event.content or not event.content.parts:
        return ""
    return "\n".join((p.text or "").strip() for p in event.content.parts if p.thought is not True).strip()


def _flatten_messages(prompt: Union[str, list[dict[str, Any]]]) -> str:
    """Flatten gepa's prompt forms into a single user-text string.

    Accepts:
      - str: returned verbatim
      - list[dict]: messages with role/content; joined with role tags so the
        downstream LlmAgent receives a single user turn that preserves the
        original conversation structure
    """
    if isinstance(prompt, str):
        return prompt
    if not isinstance(prompt, list):
        return str(prompt)
    parts: list[str] = []
    for msg in prompt:
        if not isinstance(msg, dict):
            parts.append(str(msg))
            continue
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if isinstance(content, list):
            content = "".join(c.get("text", str(c)) for c in content if isinstance(c, dict))
        parts.append(f"[{role}]\n{content}")
    return "\n\n".join(parts)


class _OptimizeModelCallable:
    """Synchronous LLM callable wrapping a framework `LlmAgent`.

    Conforms to gepa's `LanguageModel` Protocol:
      - `__call__(prompt: str | list[dict]) -> str`
      - `total_cost: float` attribute (used by gepa's MaxReflectionCostStopper)

    LlmAgent topology: instruction = "" (callers embed their own system text
    inside the prompt), single user turn, no tools, no planner unless
    `think` requests one, output_schema = None.
    """

    def __init__(self, opts: OptimizeModelOptions) -> None:
        model = _create_optimize_model(opts)
        cfg, thinking_config = _build_optimize_generation_config(opts)
        planner = (BuiltInPlanner(thinking_config=thinking_config) if thinking_config is not None else None)
        self._agent = LlmAgent(
            name="optimize_model",
            model=model,
            instruction="",
            generate_content_config=cfg,
            add_name_to_instruction=False,
            output_schema=None,
            tools=[],
            planner=planner,
        )
        self._session_service = InMemorySessionService()
        self.total_cost: float = 0.0
        self.total_calls: int = 0
        self.total_token_usage: dict[str, int] = {
            "prompt": 0,
            "completion": 0,
            "total": 0,
        }

    def __call__(self, prompt: Union[str, list[dict[str, Any]]]) -> str:
        user_text = _flatten_messages(prompt)
        self.total_calls += 1
        return asyncio.run(self._run_async(user_text))

    async def _run_async(self, user_text: str) -> str:
        user_content = Content(role="user", parts=[Part.from_text(text=user_text)])
        agent_context = create_agent_context()
        session = await self._session_service.create_session(
            app_name="optimizer",
            user_id="optimize_model",
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
            part_text = _extract_final_text(event)
            if part_text:
                last_text += part_text
            usage = getattr(event, "usage_metadata", None)
            if usage is not None:
                self._accumulate_usage(usage)
        return last_text.strip()

    def _accumulate_usage(self, usage: Any) -> None:
        """Add a single ``usage_metadata`` snapshot into ``total_token_usage``.

        Tolerant to Pydantic models, dict, or arbitrary attribute-bearing
        objects so it works across model providers.
        """
        prompt = self._read_count(usage, ("prompt_token_count", "input_tokens", "prompt_tokens"))
        completion = self._read_count(
            usage,
            ("candidates_token_count", "output_tokens", "completion_tokens"),
        )
        total = self._read_count(usage, ("total_token_count", "total_tokens"))
        if total <= 0 and (prompt > 0 or completion > 0):
            total = prompt + completion
        self.total_token_usage["prompt"] += prompt
        self.total_token_usage["completion"] += completion
        self.total_token_usage["total"] += total

    @staticmethod
    def _read_count(usage: Any, names: tuple[str, ...]) -> int:
        """Return the first non-None int among the candidate attribute / key names."""
        for name in names:
            value = None
            if isinstance(usage, dict):
                value = usage.get(name)
            else:
                value = getattr(usage, name, None)
            if isinstance(value, int):
                return value
            if isinstance(value, float):
                return int(value)
        return 0
