# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""A2A adapter for a2a-sdk 0.3.x (extra ``trpc-agent-py[a2a]``).

For a2a-sdk 1.x use ``trpc_agent_sdk.server.a2a_v1``
(extra ``trpc-agent-py[a2a-v1]``). The two extras cannot be installed together.
"""

from .._a2a_detect import require_a2a_sdk_major

require_a2a_sdk_major(0)

from ._agent_card_builder import AgentCardBuilder
from ._agent_service import TrpcA2aAgentService
from ._remote_a2a_agent import TrpcRemoteA2aAgent
from ._utils import get_metadata
from ._utils import metadata_is_true
from ._utils import set_metadata
from .executor import TrpcA2aAgentExecutor
from .executor import TrpcA2aAgentExecutorConfig

__all__ = [
    "AgentCardBuilder",
    "TrpcA2aAgentService",
    "TrpcRemoteA2aAgent",
    "get_metadata",
    "metadata_is_true",
    "set_metadata",
    "TrpcA2aAgentExecutor",
    "TrpcA2aAgentExecutorConfig",
]
