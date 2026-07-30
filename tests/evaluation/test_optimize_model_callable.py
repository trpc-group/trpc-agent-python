# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for _OptimizeModelCallable (gepa-compatible LanguageModel wrapper)."""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock

import pytest

from trpc_agent_sdk.evaluation._optimize_model_callable import _OptimizeModelCallable
from trpc_agent_sdk.evaluation._optimize_model_callable import _build_optimize_generation_config
from trpc_agent_sdk.evaluation._optimize_model_callable import _create_optimize_model
from trpc_agent_sdk.evaluation._optimize_model_callable import _extract_final_text
from trpc_agent_sdk.evaluation._optimize_model_callable import _flatten_messages
from trpc_agent_sdk.evaluation._optimize_model_options import OptimizeModelOptions


def _make_opts(**overrides) -> OptimizeModelOptions:
    defaults = {
        "provider_name": "openai",
        "model_name": "gpt-4o",
        "api_key": "test-key",
        "base_url": "https://api.example.com",
        "generation_config": {"temperature": 0.2, "max_tokens": 100},
    }
    defaults.update(overrides)
    return OptimizeModelOptions(**defaults)


def _stub_event(text: str):
    event = MagicMock()
    event.is_final_response.return_value = True
    part = MagicMock()
    part.text = text
    part.thought = False
    event.content = MagicMock()
    event.content.parts = [part]
    return event


def _install_fake_run_async(instance: _OptimizeModelCallable, return_text: str) -> list[str]:
    """Replace ``_run_async`` and record the flattened user_text it received.

    The bound method swap isolates tests from LlmAgent / InvocationContext setup
    while still exercising ``_flatten_messages`` via the public ``__call__`` path.
    """
    seen: list[str] = []

    async def fake_run_async(user_text: str) -> str:
        seen.append(user_text)
        return return_text

    instance._run_async = fake_run_async  # type: ignore[method-assign]
    return seen


def test_flatten_messages_passes_through_string():
    assert _flatten_messages("hello") == "hello"


def test_flatten_messages_concatenates_dict_list():
    out = _flatten_messages(
        [
            {"role": "system", "content": "you are helpful"},
            {"role": "user", "content": "say hi"},
        ]
    )
    assert "you are helpful" in out
    assert "say hi" in out
    assert "[system]" in out
    assert "[user]" in out


def test_flatten_messages_handles_content_list_parts():
    out = _flatten_messages(
        [{"role": "user", "content": [{"text": "first"}, {"text": "second"}]}]
    )
    assert "first" in out
    assert "second" in out


def test_create_optimize_model_with_openai_provider():
    model = _create_optimize_model(_make_opts(provider_name="openai"))
    assert model is not None
    assert type(model).__name__ == "OpenAIModel"


def test_create_optimize_model_with_empty_provider_uses_openai():
    model = _create_optimize_model(_make_opts(provider_name=""))
    assert type(model).__name__ == "OpenAIModel"


def test_build_generation_config_returns_tuple_with_thinking_none():
    cfg, thinking_config = _build_optimize_generation_config(_make_opts())
    assert cfg is not None
    assert cfg.temperature == 0.2
    assert cfg.max_output_tokens == 100
    assert thinking_config is None


def test_build_generation_config_with_think_true_returns_thinking_config():
    opts = _make_opts(think=True)
    cfg, thinking_config = _build_optimize_generation_config(opts)
    assert thinking_config is not None
    assert thinking_config.include_thoughts is True


def test_build_generation_config_with_think_false_returns_disabled_thinking():
    opts = _make_opts(think=False)
    cfg, thinking_config = _build_optimize_generation_config(opts)
    assert thinking_config is not None
    assert thinking_config.include_thoughts is False
    assert thinking_config.thinking_budget == 0


def test_build_generation_config_uses_defaults_when_generation_config_missing():
    opts = OptimizeModelOptions(model_name="m", api_key="k")
    cfg, _ = _build_optimize_generation_config(opts)
    assert cfg.max_output_tokens == 4096
    assert cfg.temperature == 0.8


def test_callable_constructor_initialises_total_cost_to_zero():
    instance = _OptimizeModelCallable(_make_opts())
    assert instance.total_cost == 0.0


def test_callable_constructor_initialises_total_calls_to_zero():
    instance = _OptimizeModelCallable(_make_opts())
    assert instance.total_calls == 0
    assert instance.total_token_usage == {"prompt": 0, "completion": 0, "total": 0}


def test_callable_increments_total_calls_on_each_invocation():
    instance = _OptimizeModelCallable(_make_opts())
    _install_fake_run_async(instance, "reply")
    instance("p1")
    instance("p2")
    instance("p3")
    assert instance.total_calls == 3


def test_callable_accumulate_usage_handles_google_style_attrs():
    instance = _OptimizeModelCallable(_make_opts())

    class _U:
        prompt_token_count = 100
        candidates_token_count = 50
        total_token_count = 150

    instance._accumulate_usage(_U())
    assert instance.total_token_usage == {"prompt": 100, "completion": 50, "total": 150}


def test_callable_accumulate_usage_handles_openai_style_dict():
    instance = _OptimizeModelCallable(_make_opts())
    instance._accumulate_usage({"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30})
    instance._accumulate_usage({"prompt_tokens": 5, "completion_tokens": 4, "total_tokens": 9})
    assert instance.total_token_usage == {"prompt": 25, "completion": 14, "total": 39}


def test_callable_accumulate_usage_computes_total_when_missing():
    instance = _OptimizeModelCallable(_make_opts())
    instance._accumulate_usage({"prompt_tokens": 7, "completion_tokens": 3})
    assert instance.total_token_usage == {"prompt": 7, "completion": 3, "total": 10}


def test_callable_exposes_languagemodel_protocol_surface():
    instance = _OptimizeModelCallable(_make_opts())
    assert callable(instance)
    assert hasattr(instance, "total_cost")
    assert isinstance(instance.total_cost, float)


def test_callable_invokes_agent_with_string_prompt():
    instance = _OptimizeModelCallable(_make_opts())
    seen = _install_fake_run_async(instance, "reply text")
    result = instance("any prompt")
    assert result == "reply text"
    assert seen == ["any prompt"]


def test_callable_handles_messages_list_prompt():
    instance = _OptimizeModelCallable(_make_opts())
    seen = _install_fake_run_async(instance, "ok")
    result = instance(
        [
            {"role": "system", "content": "be helpful"},
            {"role": "user", "content": "hi"},
        ]
    )
    assert result == "ok"
    assert len(seen) == 1
    flattened = seen[0]
    assert "be helpful" in flattened
    assert "hi" in flattened
    assert "[system]" in flattened
    assert "[user]" in flattened


def test_callable_run_async_is_coroutine_function():
    instance = _OptimizeModelCallable(_make_opts())
    assert inspect.iscoroutinefunction(instance._run_async)


def test_extract_final_text_returns_empty_for_non_final_event():
    event = MagicMock()
    event.is_final_response.return_value = False
    assert _extract_final_text(event) == ""


def test_extract_final_text_returns_empty_when_no_content():
    event = MagicMock()
    event.is_final_response.return_value = True
    event.content = None
    assert _extract_final_text(event) == ""


def test_extract_final_text_returns_empty_when_parts_missing():
    event = MagicMock()
    event.is_final_response.return_value = True
    event.content = MagicMock()
    event.content.parts = []
    assert _extract_final_text(event) == ""


def test_extract_final_text_skips_thought_parts():
    event = MagicMock()
    event.is_final_response.return_value = True
    thought = MagicMock()
    thought.text = "internal monologue"
    thought.thought = True
    actual = MagicMock()
    actual.text = "user-visible"
    actual.thought = False
    event.content = MagicMock()
    event.content.parts = [thought, actual]
    result = _extract_final_text(event)
    assert "internal monologue" not in result
    assert "user-visible" in result


def test_extract_final_text_joins_multiple_non_thought_parts():
    event = MagicMock()
    event.is_final_response.return_value = True
    a = MagicMock()
    a.text = "first"
    a.thought = False
    b = MagicMock()
    b.text = "second"
    b.thought = False
    event.content = MagicMock()
    event.content.parts = [a, b]
    result = _extract_final_text(event)
    assert "first" in result
    assert "second" in result
