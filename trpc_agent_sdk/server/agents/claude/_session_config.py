# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
#
"""Claude session configuration for TRPC Agent framework."""

from dataclasses import dataclass


@dataclass
class SessionConfig:
    """Configuration for SessionManager/Session behavior.

    Attributes:
        ttl: Time-to-live for idle sessions in seconds (default: 600s/10min)
             Sessions that have been idle for longer than this will be automatically cleaned up.
             Set to 0 or negative to disable TTL-based cleanup.
    """

    ttl: int = 600
