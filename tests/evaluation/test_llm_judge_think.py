# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for LLM judge `think` field."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

import trpc_agent_sdk.runners  # noqa: F401
from trpc_agent_sdk.evaluation._llm_criterion import JudgeModelOptions
from trpc_agent_sdk.evaluation._llm_judge import _JudgeAgent
from trpc_agent_sdk.evaluation._llm_judge import _judge_generation_config
from trpc_agent_sdk.evaluation._llm_judge import _merge_extra_body
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import HttpOptions
from trpc_agent_sdk.types import Part


class TestJudgeModelOptionsThinkField:

    def test_think_field_default_is_none(self):
        opts = JudgeModelOptions(model_name="m")
        assert opts.think is None

    def test_think_field_accepts_bool(self):
        assert JudgeModelOptions(model_name="m", think=True).think is True
        assert JudgeModelOptions(model_name="m", think=False).think is False

    def test_think_field_rejects_non_bool(self):
        # EvalBaseModel uses pydantic v2 default lax mode (no strict). Strings like "yes"
        # would be coerced to bool, so use an object() instance that cannot be coerced.
        with pytest.raises(Exception):
            JudgeModelOptions(model_name="m", think=object())


class TestMergeExtraBody:

    def test_none_http_options_creates_new_with_patch(self):
        result = _merge_extra_body(None, {"chat_template_kwargs": {"enable_thinking": False}})
        assert isinstance(result, HttpOptions)
        assert result.extra_body == {"chat_template_kwargs": {"enable_thinking": False}}

    def test_preserves_other_top_level_keys_in_extra_body(self):
        existing = HttpOptions(extra_body={"custom_user_field": "abc"})
        result = _merge_extra_body(existing, {"chat_template_kwargs": {"enable_thinking": False}})
        assert result.extra_body["custom_user_field"] == "abc"
        assert result.extra_body["chat_template_kwargs"] == {"enable_thinking": False}

    def test_preserves_sibling_keys_in_chat_template_kwargs(self):
        existing = HttpOptions(
            extra_body={
                "chat_template_kwargs": {"enable_thinking": True, "other_key": "x"},
                "custom_user_field": "abc",
            })
        result = _merge_extra_body(existing, {"chat_template_kwargs": {"enable_thinking": False}})
        assert result.extra_body["chat_template_kwargs"]["other_key"] == "x"
        assert result.extra_body["chat_template_kwargs"]["enable_thinking"] is False
        assert result.extra_body["custom_user_field"] == "abc"

    def test_patch_is_copied_not_shared(self):
        patch_dict = {"chat_template_kwargs": {"enable_thinking": False}}
        result = _merge_extra_body(None, patch_dict)
        patch_dict["chat_template_kwargs"]["enable_thinking"] = True
        assert result.extra_body["chat_template_kwargs"]["enable_thinking"] is False


class TestJudgeGenerationConfigThink:

    def test_think_none_returns_none_thinking_config_and_none_http_options(self):
        cfg, tc = _judge_generation_config(None, None)
        assert tc is None
        assert cfg.http_options is None
        assert cfg.thinking_config is None  # must stay empty; LlmAgent rejects otherwise

    def test_think_none_preserves_caller_http_options(self):
        gen = {"http_options": {"extra_body": {"my_key": 1}}}
        cfg, tc = _judge_generation_config(gen, None)
        assert tc is None
        assert cfg.http_options is not None
        assert cfg.http_options.extra_body == {"my_key": 1}

    def test_think_false_builds_disabled_thinking_config(self):
        cfg, tc = _judge_generation_config(None, False)
        assert tc is not None
        assert tc.include_thoughts is False
        assert tc.thinking_budget == 0
        assert cfg.thinking_config is None
        assert cfg.http_options is not None
        assert cfg.http_options.extra_body == {
            "chat_template_kwargs": {"enable_thinking": False},
        }

    def test_think_true_builds_enabled_thinking_config_auto_budget(self):
        cfg, tc = _judge_generation_config(None, True)
        assert tc is not None
        assert tc.include_thoughts is True
        assert tc.thinking_budget == -1
        assert cfg.http_options is not None
        assert cfg.http_options.extra_body == {
            "chat_template_kwargs": {"enable_thinking": True},
        }

    def test_think_false_overrides_generation_config_thinking_config(self):
        gen = {
            "max_tokens": 4096,
            "thinking_config": {"include_thoughts": True, "thinking_budget": 2048},
        }
        cfg, tc = _judge_generation_config(gen, False)
        assert cfg.max_output_tokens == 4096
        assert tc is not None
        assert tc.include_thoughts is False
        assert tc.thinking_budget == 0

    def test_think_false_deep_merges_extra_body_preserving_other_keys(self):
        gen = {
            "http_options": {
                "extra_body": {
                    "chat_template_kwargs": {"enable_thinking": True, "other_key": "x"},
                    "custom_user_field": "abc",
                },
            },
        }
        cfg, tc = _judge_generation_config(gen, False)
        assert tc is not None
        assert cfg.http_options.extra_body["custom_user_field"] == "abc"
        assert cfg.http_options.extra_body["chat_template_kwargs"]["other_key"] == "x"
        assert (
            cfg.http_options.extra_body["chat_template_kwargs"]["enable_thinking"] is False
        )

    def test_generation_config_thinking_config_used_when_think_is_none(self):
        gen = {"thinking_config": {"include_thoughts": True, "thinking_budget": 512}}
        cfg, tc = _judge_generation_config(gen, None)
        assert tc is not None
        assert tc.include_thoughts is True
        assert tc.thinking_budget == 512
        assert cfg.http_options is None


class TestJudgeAgentPlanner:

    @pytest.mark.asyncio
    async def test_judge_agent_collects_final_non_thought_text(self):
        class _FakeEvent:

            def __init__(self, *, final, content):
                self._final = final
                self.content = content

            def is_final_response(self):
                return self._final

        events = [
            _FakeEvent(
                final=False,
                content=Content(parts=[Part(text="ignored non-final")]),
            ),
            _FakeEvent(final=True, content=None),
            _FakeEvent(
                final=True,
                content=Content(parts=[
                    Part(text="hidden reasoning", thought=True),
                    Part(text=" first "),
                    Part(text="second"),
                ]),
            ),
        ]

        class _FakeLlmAgent:

            def __init__(self, **kwargs):
                pass

            def run_async(self, ctx):
                async def _run():
                    for event in events:
                        yield event

                return _run()

        class _FakeInvocationContext:

            def __init__(self, **kwargs):
                self.run_config = kwargs["run_config"]

        with patch("trpc_agent_sdk.evaluation._llm_judge.LlmAgent", _FakeLlmAgent), patch(
                "trpc_agent_sdk.evaluation._llm_judge.InvocationContext", _FakeInvocationContext):
            judge = _JudgeAgent(
                model=object(),
                config=None,
                system_prompt="sp",
            )
            response = await judge.get_response("evaluate this response")

        assert response == "first\nsecond"

    @pytest.mark.asyncio
    async def test_judge_agent_closes_its_agent_run(self):
        captured = {"closed": False}

        class _FakeAgentRun:

            def __aiter__(self):
                return self

            async def __anext__(self):
                raise StopAsyncIteration

            async def aclose(self):
                captured["closed"] = True

        agent_run = _FakeAgentRun()

        class _FakeLlmAgent:

            def __init__(self, **kwargs):
                pass

            def run_async(self, ctx):
                return agent_run

        class _FakeInvocationContext:

            def __init__(self, **kwargs):
                pass

        with patch("trpc_agent_sdk.evaluation._llm_judge.LlmAgent", _FakeLlmAgent), patch(
                "trpc_agent_sdk.evaluation._llm_judge.InvocationContext", _FakeInvocationContext):
            judge = _JudgeAgent(
                model=object(),
                config=None,
                system_prompt="sp",
            )
            await judge.get_response("evaluate this response")

        assert captured["closed"] is True

    @pytest.mark.asyncio
    async def test_judge_agent_disables_model_streaming(self):
        captured: dict[str, Any] = {}

        class _FakeLlmAgent:

            def __init__(self, **kwargs):
                captured.update(kwargs)

            async def run_async(self, ctx):
                captured["run_config"] = ctx.run_config
                if False:  # pragma: no cover - makes this an async generator
                    yield None

        class _FakeInvocationContext:

            def __init__(self, **kwargs):
                self.run_config = kwargs["run_config"]

        with patch("trpc_agent_sdk.evaluation._llm_judge.LlmAgent", _FakeLlmAgent), patch(
                "trpc_agent_sdk.evaluation._llm_judge.InvocationContext", _FakeInvocationContext):
            judge = _JudgeAgent(
                model=object(),
                config=None,
                system_prompt="sp",
            )
            await judge.get_response("evaluate this response")

        assert captured["run_config"].streaming is False

    def test_judge_agent_accepts_planner_and_forwards_to_llm_agent(self):
        captured: dict[str, Any] = {}

        class _FakeLlmAgent:

            def __init__(self, **kwargs):
                captured.update(kwargs)

        fake_planner = object()
        with patch("trpc_agent_sdk.evaluation._llm_judge.LlmAgent", _FakeLlmAgent):
            _JudgeAgent(
                model=object(),
                config=None,
                system_prompt="sp",
                output_schema=None,
                tools=None,
                planner=fake_planner,
            )
        assert captured.get("planner") is fake_planner

    def test_judge_agent_planner_defaults_to_none(self):
        captured: dict[str, Any] = {}

        class _FakeLlmAgent:

            def __init__(self, **kwargs):
                captured.update(kwargs)

        with patch("trpc_agent_sdk.evaluation._llm_judge.LlmAgent", _FakeLlmAgent):
            _JudgeAgent(
                model=object(),
                config=None,
                system_prompt="sp",
            )
        assert captured.get("planner") is None


# --- Integration tests: end-to-end LLMJudge wiring ---


class _SpyModel:

    def __init__(self, name: str) -> None:
        self._stub_name = name


def _spy_create_judge_model(opts):
    return _SpyModel(opts.model_name or "")


class _SpyJudgeAgent:
    """Captures constructor kwargs for every judge model built by LLMJudge."""

    instances: list[dict[str, Any]] = []

    def __init__(self, model, config, system_prompt, output_schema=None, tools=None, planner=None):
        _SpyJudgeAgent.instances.append({
            "model_name": getattr(model, "_stub_name", ""),
            "config": config,
            "planner": planner,
        })

    async def get_response(self, user_message: str) -> str:  # pragma: no cover - not invoked here
        return '{"reasoning":"stub","is_the_agent_response_valid":"valid"}'


def _make_metric(judge_models: list[dict[str, Any]]):
    from trpc_agent_sdk.evaluation import EvalMetric
    return EvalMetric(
        metric_name="llm_final_response",
        threshold=1.0,
        criterion={
            "llm_judge": {
                "judge_models": judge_models,
                "models_aggregator": "all_pass",
            },
        },
    )


@pytest.fixture(autouse=True)
def _reset_spy():
    _SpyJudgeAgent.instances.clear()
    yield
    _SpyJudgeAgent.instances.clear()


def _build_judge(judge_models: list[dict[str, Any]]):
    from trpc_agent_sdk.evaluation._llm_judge import LLMJudge
    metric = _make_metric(judge_models)
    patchers = [
        patch("trpc_agent_sdk.evaluation._llm_judge._create_judge_model",
              side_effect=_spy_create_judge_model),
        patch("trpc_agent_sdk.evaluation._llm_judge._JudgeAgent", _SpyJudgeAgent),
    ]
    for p in patchers:
        p.start()
    try:
        return LLMJudge(metric)
    finally:
        for p in patchers:
            p.stop()


class TestLLMJudgeThinkIntegration:

    def test_legacy_single_judge_model_supports_think(self):
        from trpc_agent_sdk.evaluation import EvalMetric
        from trpc_agent_sdk.evaluation._llm_judge import LLMJudge
        from trpc_agent_sdk.planners import BuiltInPlanner
        metric = EvalMetric(
            metric_name="llm_final_response",
            threshold=1.0,
            criterion={
                "llm_judge": {
                    "judge_model": {"model_name": "glm-4.7", "think": False},
                },
            },
        )
        patchers = [
            patch("trpc_agent_sdk.evaluation._llm_judge._create_judge_model",
                  side_effect=_spy_create_judge_model),
            patch("trpc_agent_sdk.evaluation._llm_judge._JudgeAgent", _SpyJudgeAgent),
        ]
        for p in patchers:
            p.start()
        try:
            LLMJudge(metric)
        finally:
            for p in patchers:
                p.stop()
        assert len(_SpyJudgeAgent.instances) == 1
        inst = _SpyJudgeAgent.instances[0]
        assert isinstance(inst["planner"], BuiltInPlanner)
        assert inst["planner"].thinking_config.include_thoughts is False
        assert inst["planner"].thinking_config.thinking_budget == 0

    def test_per_judge_independent_think(self):
        from trpc_agent_sdk.planners import BuiltInPlanner
        _build_judge([
            {"model_name": "glm-4.7", "think": False},
            {"model_name": "gpt-4o", "think": True},
            {"model_name": "qwen2.5"},  # think None -> no planner
        ])
        assert len(_SpyJudgeAgent.instances) == 3
        by_name = {i["model_name"]: i for i in _SpyJudgeAgent.instances}

        glm = by_name["glm-4.7"]
        assert isinstance(glm["planner"], BuiltInPlanner)
        assert glm["planner"].thinking_config.include_thoughts is False
        assert glm["planner"].thinking_config.thinking_budget == 0
        assert glm["config"].http_options.extra_body == {
            "chat_template_kwargs": {"enable_thinking": False},
        }

        gpt = by_name["gpt-4o"]
        assert isinstance(gpt["planner"], BuiltInPlanner)
        assert gpt["planner"].thinking_config.include_thoughts is True
        assert gpt["planner"].thinking_config.thinking_budget == -1
        assert gpt["config"].http_options.extra_body == {
            "chat_template_kwargs": {"enable_thinking": True},
        }

        qwen = by_name["qwen2.5"]
        assert qwen["planner"] is None
        assert qwen["config"].http_options is None

    def test_think_none_with_caller_http_options_preserves_it(self):
        _build_judge([{
            "model_name": "m",
            "generation_config": {"http_options": {"extra_body": {"preserved": 1}}},
        }])
        assert len(_SpyJudgeAgent.instances) == 1
        inst = _SpyJudgeAgent.instances[0]
        assert inst["planner"] is None
        assert inst["config"].http_options.extra_body == {"preserved": 1}


class TestCreateJudgeModelRouting:
    """Verify _create_judge_model picks OpenAIModel directly when provider is
    empty/openai (so http_options.extra_body actually reaches the backend),
    and falls back to ModelRegistry.create_model for other providers (LiteLLM)."""

    def test_empty_provider_uses_openaimodel_directly(self):
        from trpc_agent_sdk.evaluation._llm_judge import _create_judge_model
        from trpc_agent_sdk.models import OpenAIModel
        opts = JudgeModelOptions(
            provider_name="",
            model_name="glm-5.1-w4afp8",
            api_key="k",
            base_url="http://host/v1",
        )
        model = _create_judge_model(opts)
        assert isinstance(model, OpenAIModel)

    def test_openai_provider_uses_openaimodel_directly(self):
        from trpc_agent_sdk.evaluation._llm_judge import _create_judge_model
        from trpc_agent_sdk.models import OpenAIModel
        opts = JudgeModelOptions(
            provider_name="openai",
            model_name="gpt-4o",
            api_key="k",
        )
        model = _create_judge_model(opts)
        assert isinstance(model, OpenAIModel)

    def test_openai_provider_case_insensitive(self):
        from trpc_agent_sdk.evaluation._llm_judge import _create_judge_model
        from trpc_agent_sdk.models import OpenAIModel
        opts = JudgeModelOptions(
            provider_name="OpenAI",
            model_name="gpt-4o",
            api_key="k",
        )
        model = _create_judge_model(opts)
        assert isinstance(model, OpenAIModel)

    def test_non_openai_provider_uses_registry(self):
        from trpc_agent_sdk.evaluation import _llm_judge as llm_judge_mod
        from trpc_agent_sdk.evaluation._llm_judge import _create_judge_model
        opts = JudgeModelOptions(
            provider_name="anthropic",
            model_name="claude-3-5-sonnet",
            api_key="k",
        )
        sentinel = object()
        with patch.object(
                llm_judge_mod.ModelRegistry,
                "create_model",
                return_value=sentinel,
        ) as mock_reg:
            model = _create_judge_model(opts)
        assert model is sentinel
        args, kwargs = mock_reg.call_args
        assert args[0] == "anthropic/claude-3-5-sonnet"
        assert kwargs.get("api_key") == "k"

    def test_openaimodel_receives_model_name_and_base_url(self):
        from trpc_agent_sdk.evaluation._llm_judge import _create_judge_model
        opts = JudgeModelOptions(
            provider_name="",
            model_name="glm-5.1-w4afp8",
            api_key="sk-x",
            base_url="http://example/v1",
        )
        model = _create_judge_model(opts)
        assert getattr(model, "_model_name", None) == "glm-5.1-w4afp8"
        assert getattr(model, "_base_url", None) == "http://example/v1"
