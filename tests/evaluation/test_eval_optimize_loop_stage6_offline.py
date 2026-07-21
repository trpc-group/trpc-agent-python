# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under the Apache License, Version 2.0.
"""Stage 6 offline-mode tests for the SDK-backed deterministic model."""

from __future__ import annotations

from pathlib import Path

import pytest

from examples.optimization.eval_optimize_loop.business_agent import BusinessAgent
from examples.optimization.eval_optimize_loop.fake.model import (
    DeterministicFakeModel,
)
from trpc_agent_sdk.evaluation import TargetPrompt
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import GenerateContentConfig
from trpc_agent_sdk.types import Part


def _request(instruction: str, user_text: str) -> LlmRequest:
    return LlmRequest(
        contents=[
            Content(role="user", parts=[Part.from_text(text=user_text)]),
        ],
        config=GenerateContentConfig(system_instruction=instruction),
    )


async def _text(model: DeterministicFakeModel, request: LlmRequest) -> str:
    responses = [response async for response in model.generate_async(request)]
    assert len(responses) == 1
    assert responses[0].content is not None
    return "".join(part.text or "" for part in responses[0].content.parts or [])


def test_deterministic_fake_model_registers_a_stable_model_pattern():
    assert DeterministicFakeModel.supported_models() == [
        "deterministic-fake-model"
    ]


@pytest.mark.asyncio
async def test_deterministic_fake_model_uses_system_instruction():
    model = DeterministicFakeModel()
    query = "Check the status of order A100."

    baseline = await _text(model, _request("baseline", query))
    improved = await _text(
        model,
        _request(
            "<!-- deterministic-fake-rule order_lookup=true -->",
            query,
        ),
    )

    assert '"route":"general_support"' in baseline
    assert improved == (
        '{"route":"order_lookup","message":"Checking order A100."}'
    )
    assert await _text(model, _request("baseline", query)) == baseline


@pytest.mark.asyncio
async def test_deterministic_fake_model_uses_last_user_message():
    request = _request(
        "<!-- deterministic-fake-rule shipping_policy=true -->",
        "ignored",
    )
    request.contents.extend(
        [
            Content(role="model", parts=[Part.from_text(text="intermediate")]),
            Content(
                role="user",
                parts=[Part.from_text(text="How long is standard shipping?")],
            ),
        ]
    )

    assert await _text(DeterministicFakeModel(), request) == (
        '{"route":"shipping_policy","message":'
        '"Standard shipping normally takes 3-5 business days."}'
    )


@pytest.mark.asyncio
async def test_deterministic_fake_model_rejects_missing_user_text():
    request = LlmRequest(
        contents=[Content(role="model", parts=[Part.from_text(text="only model")])],
        config=GenerateContentConfig(system_instruction="baseline"),
    )

    responses = [
        response
        async for response in DeterministicFakeModel().generate_async(request)
    ]

    assert len(responses) == 1
    assert responses[0].content is None
    assert responses[0].error_code == "API_ERROR"
    assert "user text" in (responses[0].error_message or "")


@pytest.mark.asyncio
async def test_business_agent_rereads_prompt_through_llm_agent(tmp_path: Path):
    prompt_path = tmp_path / "system.md"
    prompt_path.write_text("baseline", encoding="utf-8")
    target = TargetPrompt().add_path("system_prompt", str(prompt_path))
    created_models: list[DeterministicFakeModel] = []

    def model_factory() -> DeterministicFakeModel:
        model = DeterministicFakeModel()
        created_models.append(model)
        return model

    agent = BusinessAgent(
        target,
        model_factory,
        agent_name="stage6_offline_agent",
        app_name="stage6_offline_test",
        user_id="stage6-test",
    )
    query = "Check the status of order A100."

    baseline = await agent.call_agent(query)
    await target.write_all(
        {
            "system_prompt": (
                "<!-- deterministic-fake-rule order_lookup=true -->"
            )
        }
    )
    candidate = await agent.call_agent(query)

    assert '"route":"general_support"' in baseline
    assert candidate == (
        '{"route":"order_lookup","message":"Checking order A100."}'
    )
    assert len(created_models) == 2
