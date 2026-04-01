# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Model configuration helpers for generated graph workflow."""

from typing import Any

from trpc_agent_sdk.models import OpenAIModel


def create_openai_model(
    model_name: str | None,
    api_key: str | None,
    base_url: str | None,
    headers: dict[str, str] | None = None,
) -> OpenAIModel:
    kwargs: dict[str, Any] = {}
    if headers:
        kwargs["client_args"] = {"default_headers": headers}
    return OpenAIModel(
        model_name=model_name,
        api_key=api_key,
        base_url=base_url,
        **kwargs,
    )


# No llmagent model config found in workflow.
