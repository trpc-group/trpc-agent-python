# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Deterministic fake model so the full pipeline runs without any API key."""
import json
from typing import List

from trpc_agent_sdk.models import LLMModel, LlmResponse
from trpc_agent_sdk.types import Content, Part

_FAKE_PAYLOAD = {
    "summary": "Dry-run review complete. Static findings are authoritative.",
    "findings": [],
}


class FakeReviewModel(LLMModel):
    """LLMModel returning one canned JSON review response."""

    def __init__(self, model_name: str = "fake-review-model", **kwargs):
        super().__init__(model_name=model_name, **kwargs)

    @classmethod
    def supported_models(cls) -> List[str]:
        return [r"fake-review-.*"]

    def validate_request(self, request) -> None:
        return None

    async def _generate_async_impl(self, request, stream=False, ctx=None):
        text = json.dumps(_FAKE_PAYLOAD)
        yield LlmResponse(content=Content(role="model", parts=[Part.from_text(text=text)]))
