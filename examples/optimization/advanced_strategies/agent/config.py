# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""模型凭据读取 —— 从环境变量加载 OpenAI 兼容 LLM 的连接信息。

需要的环境变量
--------------
  TRPC_AGENT_API_KEY     LLM 后端的 API key
  TRPC_AGENT_BASE_URL    LLM 后端的 endpoint
  TRPC_AGENT_MODEL_NAME  模型名

缺任意一个就立即抛 ValueError，避免运行到一半才撞到 LLM 后端的 401 错误，
那时报错信息会很有迷惑性（看起来像 prompt 写错了，实际是凭据没配）。
"""

from __future__ import annotations

import os


def get_model_config() -> tuple[str, str, str]:
    """返回 (api_key, base_url, model_name)；任一缺失立刻报错。"""
    api_key = os.getenv("TRPC_AGENT_API_KEY", "")
    base_url = os.getenv("TRPC_AGENT_BASE_URL", "")
    model_name = os.getenv("TRPC_AGENT_MODEL_NAME", "")
    if not api_key or not base_url or not model_name:
        raise ValueError(
            "运行优化器前必须配置环境变量 TRPC_AGENT_API_KEY / "
            "TRPC_AGENT_BASE_URL / TRPC_AGENT_MODEL_NAME。"
        )
    return api_key, base_url, model_name
