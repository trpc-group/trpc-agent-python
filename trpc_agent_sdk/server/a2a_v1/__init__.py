# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""A2A adapter for a2a-sdk 1.x (extra ``trpc-agent-py[a2a-v1]``).

For a2a-sdk 0.3.x use ``trpc_agent_sdk.server.a2a`` (extra ``trpc-agent-py[a2a]``).
The two extras cannot be installed together.
"""

from .._a2a_detect import require_a2a_sdk_major

require_a2a_sdk_major(1)

from ._agent_card_builder import AgentCardBuilder  # noqa: E402
from ._agent_service import TrpcA2aAgentService  # noqa: E402
from ._application import create_a2a_application  # noqa: E402
from ._remote_a2a_agent import TrpcRemoteA2aAgent  # noqa: E402
from ._utils import get_metadata  # noqa: E402
from ._utils import metadata_is_true  # noqa: E402
from ._utils import set_metadata  # noqa: E402
from .executor import TrpcA2aAgentExecutor  # noqa: E402
from .executor import TrpcA2aAgentExecutorConfig  # noqa: E402

__all__ = [
    "AgentCardBuilder",
    "TrpcA2aAgentService",
    "TrpcRemoteA2aAgent",
    "create_a2a_application",
    "get_metadata",
    "metadata_is_true",
    "set_metadata",
    "TrpcA2aAgentExecutor",
    "TrpcA2aAgentExecutorConfig",
]
