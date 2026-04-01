# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
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
