# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Public exports for the a2a-sdk 0.3 adapter package."""

from __future__ import annotations

import pytest

from trpc_agent_sdk.server.a2a import TrpcA2aAgentService
from trpc_agent_sdk.server.a2a import TrpcRemoteA2aAgent


def test_public_service_export():
    assert TrpcA2aAgentService.__name__ == "TrpcA2aAgentService"
    assert TrpcRemoteA2aAgent.__name__ == "TrpcRemoteA2aAgent"


def test_create_a2a_application_not_exported():
    import trpc_agent_sdk.server.a2a as a2a_pkg

    assert not hasattr(a2a_pkg, "create_a2a_application")
    with pytest.raises(ImportError):
        from trpc_agent_sdk.server.a2a import create_a2a_application

        assert create_a2a_application  # pragma: no cover - import raises


def test_a2a_v1_package_requires_sdk_1x():
    with pytest.raises(ImportError, match="a2a_v1 requires a2a-sdk>=1.0"):
        from trpc_agent_sdk.server.a2a_v1 import TrpcA2aAgentService as _unused

        assert _unused  # pragma: no cover - import raises
