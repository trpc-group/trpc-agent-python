# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Public exports for the a2a-sdk 1.x adapter package."""

from __future__ import annotations

import pytest

from trpc_agent_sdk.server.a2a_v1 import TrpcA2aAgentService
from trpc_agent_sdk.server.a2a_v1 import TrpcRemoteA2aAgent
from trpc_agent_sdk.server.a2a_v1 import create_a2a_application


def test_public_service_export():
    assert TrpcA2aAgentService.__name__ == "TrpcA2aAgentService"
    assert TrpcRemoteA2aAgent.__name__ == "TrpcRemoteA2aAgent"
    assert callable(create_a2a_application)


def test_a2a_package_requires_sdk_03():
    with pytest.raises(ImportError, match="a2a requires a2a-sdk 0.3"):
        from trpc_agent_sdk.server.a2a import TrpcA2aAgentService as _unused

        assert _unused  # pragma: no cover - import raises
