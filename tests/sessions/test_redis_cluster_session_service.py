# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Construction tests for RedisClusterSessionService."""

from unittest.mock import MagicMock
from unittest.mock import patch

from trpc_agent_sdk.sessions import RedisClusterSessionService


class TestRedisClusterSessionService:
    @patch("trpc_agent_sdk.sessions._redis_cluster_session_service.RedisClusterStorage")
    def test_uses_cluster_storage(self, storage_cls):
        storage = MagicMock()
        storage_cls.return_value = storage

        service = RedisClusterSessionService(
            db_url="redis://seed:6379/0", is_async=True, max_connections=20)

        assert service._redis_storage is storage
        storage_cls.assert_called_once_with(
            is_async=True, redis_url="redis://seed:6379/0", max_connections=20)
