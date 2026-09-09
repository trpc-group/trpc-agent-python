# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""A2A Server Example

This example uses the SDK's ``create_a2a_application`` (which wraps the a2a-sdk
1.x route factories) to serve a trpc-agent as an A2A service over plain HTTP,
with the standard protocol (artifact-first streaming and unprefixed metadata keys).

Set ``A2A_V03_COMPAT=1`` to also accept legacy v0.3 clients on the same endpoint:

    A2A_V03_COMPAT=1 python3 run_server.py
"""

import os

import uvicorn
from dotenv import load_dotenv

from trpc_agent_sdk.server.a2a_v1 import create_a2a_application
from trpc_agent_sdk.server.a2a_v1 import TrpcA2aAgentExecutorConfig
from trpc_agent_sdk.server.a2a_v1 import TrpcA2aAgentService

load_dotenv()

HOST = "127.0.0.1"
PORT = 18081


def create_a2a_service() -> TrpcA2aAgentService:
    """Create A2A service with LlmAgent (standard protocol).

    This service wraps a weather query agent and exposes it via A2A protocol
    using artifact-first streaming and unprefixed metadata keys.
    """
    from agent.agent import root_agent

    executor_config = TrpcA2aAgentExecutorConfig()

    a2a_svc = TrpcA2aAgentService(
        service_name="weather_agent_standard_service",
        agent=root_agent,
        # Public address advertised in the agent card; clients call this url.
        rpc_url=f"http://{HOST}:{PORT}",
        executor_config=executor_config,
    )
    a2a_svc.initialize()

    return a2a_svc


def serve():
    """Start the A2A server using standard HTTP (uvicorn + Starlette)."""
    a2a_svc = create_a2a_service()

    # A2A_V03_COMPAT=1 also accepts legacy v0.3 clients on the same endpoint.
    enable_v0_3_compat = os.getenv("A2A_V03_COMPAT", "").strip().lower() in ("1", "true", "yes")
    app = create_a2a_application(
        a2a_svc,
        enable_v0_3_compat=enable_v0_3_compat,
    )

    print("Starting A2A server (standard protocol over HTTP)...")
    print(f"Listening on: http://{HOST}:{PORT}")
    print(f"Agent card: http://{HOST}:{PORT}/.well-known/agent-card.json")
    print(f"v0.3 compatibility: {'ENABLED' if enable_v0_3_compat else 'disabled'}")

    uvicorn.run(app, host=HOST, port=PORT)


if __name__ == "__main__":
    serve()
