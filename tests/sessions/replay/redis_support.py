# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Redis availability helpers shared by replay integration tests.

回放集成测试共用的 Redis 可用性检查工具。
"""

from __future__ import annotations

import os
from typing import Optional

import pytest
import redis


def redis_unavailable_reason(redis_url: str) -> Optional[str]:
    """Return an actionable reason when Redis cannot answer ``PING``.

    当 Redis 无法响应 ``PING`` 时返回可操作的原因；可用时返回 ``None``。
    The URL is deliberately omitted from the message because it may contain
    credentials.

    错误消息不回显 URL，避免泄露其中可能包含的认证信息。
    """
    client = None
    try:
        # Keep the probe short so an invalid opt-in URL does not stall the
        # lightweight test suite.
        # 使用短超时，避免错误的可选 URL 阻塞轻量测试流程。
        client = redis.Redis.from_url(
            redis_url,
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
        )
        if client.ping():
            return None
        return "configured Redis did not return PONG"
    except Exception as exc:  # noqa: BLE001 - availability probe must report every client failure
        return f"configured Redis is unavailable: {type(exc).__name__}: {exc}"
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:  # noqa: BLE001 - cleanup must not hide the probe result
                pass


def require_replay_redis() -> str:
    """Return the configured reachable Redis URL or skip the integration test.

    返回已配置且可连接的 Redis URL；否则跳过可选集成测试。
    """
    redis_url = os.getenv("TRPC_REPLAY_REDIS_URL")
    if not redis_url:
        pytest.skip("TRPC_REPLAY_REDIS_URL is not configured")

    reason = redis_unavailable_reason(redis_url)
    if reason:
        pytest.skip(f"{reason}; start Redis or unset TRPC_REPLAY_REDIS_URL")
    return redis_url
