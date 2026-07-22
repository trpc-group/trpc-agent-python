# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Redis Cluster-backed session service."""

from __future__ import annotations

from typing import Any

from trpc_agent_sdk.storage import RedisClusterStorage

from ._redis_session_service import RedisSessionService


class RedisClusterSessionService(RedisSessionService):
    """Persist sessions in a Redis Cluster.

    The service preserves :class:`RedisSessionService` semantics while routing
    single-key operations through redis-py's cluster client.  Session listing
    scans every primary node, so sessions are not missed when their keys occupy
    different hash slots.

    ``db_url`` supplies one cluster seed, for example
    ``redis://user:password@cluster-node-1:6379/0``.  Redis Cluster only
    supports database 0; additional redis-py cluster options, including
    ``startup_nodes`` and ``address_remap``, may be provided as keyword args.
    """

    def _create_storage(self, db_url: str, is_async: bool, **kwargs: Any) -> RedisClusterStorage:
        return RedisClusterStorage(is_async=is_async, redis_url=db_url, **kwargs)
