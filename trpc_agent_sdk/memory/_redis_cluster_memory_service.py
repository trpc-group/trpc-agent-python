# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Redis Cluster-backed long-term memory service."""

from __future__ import annotations

from typing import Any

from trpc_agent_sdk.storage import RedisClusterStorage

from ._redis_memory_service import RedisMemoryService


class RedisClusterMemoryService(RedisMemoryService):
    """Store and search cross-session memories in a Redis Cluster.

    Events retain the List-based format of :class:`RedisMemoryService`, but
    cluster-wide memory lookups use ``SCAN`` across all primary nodes.  This
    avoids the incomplete results produced by a node-local ``KEYS`` command in
    a sharded deployment.

    ``db_url`` identifies one cluster seed.  Redis Cluster only supports
    database 0; pass redis-py cluster options such as ``startup_nodes`` or
    ``address_remap`` through keyword args when required by the deployment.
    """

    def _create_storage(self, db_url: str, is_async: bool, **kwargs: Any) -> RedisClusterStorage:
        return RedisClusterStorage(is_async=is_async, redis_url=db_url, **kwargs)
