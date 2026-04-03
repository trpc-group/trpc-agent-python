# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Model configuration helpers for generated graph workflow."""

import os
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


def create_model_llmagent1() -> OpenAIModel:
    model_name = os.getenv('MODEL1_NAME')
    api_key = os.getenv('MODEL1_API_KEY')
    base_url = os.getenv('MODEL1_BASE_URL')
    return create_openai_model(
        model_name=model_name,
        api_key=api_key,
        base_url=base_url,
        headers={},
    )


def create_model_llmagent2() -> OpenAIModel:
    model_name = os.getenv('MODEL2_NAME')
    api_key = os.getenv('MODEL2_API_KEY')
    base_url = os.getenv('MODEL2_BASE_URL')
    return create_openai_model(
        model_name=model_name,
        api_key=api_key,
        base_url=base_url,
        headers={},
    )


def create_model_llmagent3() -> OpenAIModel:
    model_name = os.getenv('MODEL3_NAME')
    api_key = os.getenv('MODEL3_API_KEY')
    base_url = os.getenv('MODEL3_BASE_URL')
    return create_openai_model(
        model_name=model_name,
        api_key=api_key,
        base_url=base_url,
        headers={},
    )
