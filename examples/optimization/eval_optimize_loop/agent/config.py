# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""真实模式的模型配置：从环境变量读取，供 orchestrator 与 LLM judge 共用。"""

from __future__ import annotations

import os


def get_model_config() -> tuple[str, str, str]:
    """返回 (api_key, base_url, model_name)，缺失时抛出清晰错误。

    仅在 real 模式下被调用；fake 模式不会触碰模型配置，因此无 key 也能跑。
    """
    api_key = os.getenv("TRPC_AGENT_API_KEY", "")
    base_url = os.getenv("TRPC_AGENT_BASE_URL", "")
    model_name = os.getenv("TRPC_AGENT_MODEL_NAME", "")
    missing = [
        name
        for name, val in (
            ("TRPC_AGENT_API_KEY", api_key),
            ("TRPC_AGENT_BASE_URL", base_url),
            ("TRPC_AGENT_MODEL_NAME", model_name),
        )
        if not val
    ]
    if missing:
        raise RuntimeError(
            "real 模式需要以下环境变量: "
            + ", ".join(missing)
            + "。若无 API Key，请改用 fake 模式：python run_pipeline.py --mode fake"
        )
    return api_key, base_url, model_name
