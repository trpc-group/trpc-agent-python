#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

"""真实模型运行时配置的确定性单元测试。"""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from code_review.model_runtime import build_real_model  # noqa: E402


def test_real_model_uses_bounded_deterministic_retry_and_request_timeout() -> None:
    """验证真实模型 API 失败时最多重试三次，按 5/10/20 秒退避且单次请求不会无限等待。"""

    model = build_real_model(
        {
            "TRPC_AGENT_API_KEY": "synthetic-key",
            "TRPC_AGENT_BASE_URL": "https://example.invalid/v1",
            "TRPC_AGENT_MODEL_NAME": "synthetic-model",
        }
    )

    retry = model.model_retry_config
    assert retry is not None
    assert retry.num_retries == 3
    assert retry.backoff.initial_backoff == 5.0
    assert retry.backoff.multiplier == 2.0
    assert retry.backoff.max_backoff == 20.0
    assert retry.backoff.jitter is False
    assert model.client_args["timeout"] == 30.0
