#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

"""真实模型调用的统一超时与重试策略。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from trpc_agent_sdk.configs import ExponentialBackoffConfig, ModelRetryConfig


_REQUEST_TIMEOUT_SECONDS = 30.0
_REAL_MODEL_RETRY_CONFIG = ModelRetryConfig(
    num_retries=3,
    backoff=ExponentialBackoffConfig(
        initial_backoff=5.0,
        multiplier=2.0,
        max_backoff=20.0,
        jitter=False,
    ),
)


def build_real_model(environment: Mapping[str, str]) -> Any:
    """按固定超时和 5/10/20 秒退避构造真实模型，避免网络瞬时失败或无限请求阻断评审。

    调用方负责在调用前验证三项模型配置均非空；本函数不会记录环境变量值，也不会把它们传入沙箱。
    """

    from trpc_agent_sdk.models import OpenAIModel

    return OpenAIModel(
        environment["TRPC_AGENT_MODEL_NAME"],
        api_key=environment["TRPC_AGENT_API_KEY"],
        base_url=environment["TRPC_AGENT_BASE_URL"],
        model_retry_config=_REAL_MODEL_RETRY_CONFIG,
        client_args={"timeout": _REQUEST_TIMEOUT_SECONDS},
    )


__all__ = ["build_real_model"]
