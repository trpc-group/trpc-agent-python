# -*- coding: utf-8 -*-
#
# Copyright @ 2025 Tencent.com
"""A2A Server Example

This example uses the standard A2A SDK server (A2AStarletteApplication) to serve
a trpc-agent as an A2A service over plain HTTP, with the standard protocol
(artifact-first streaming and unprefixed metadata keys).
"""

import uvicorn
from dotenv import load_dotenv

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore

from trpc_agent_sdk.server.a2a import TrpcA2aAgentExecutorConfig
from trpc_agent_sdk.server.a2a import TrpcA2aAgentService

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
        executor_config=executor_config,
    )
    a2a_svc.initialize()

    return a2a_svc


def serve():
    """Start the A2A server using standard HTTP (uvicorn + Starlette)."""
    a2a_svc = create_a2a_service()

    request_handler = DefaultRequestHandler(
        agent_executor=a2a_svc,
        task_store=InMemoryTaskStore(),
    )

    server = A2AStarletteApplication(
        agent_card=a2a_svc.agent_card,
        http_handler=request_handler,
    )

    print("Starting A2A server (standard protocol over HTTP)...")
    print(f"Listening on: http://{HOST}:{PORT}")
    print(f"Agent card: http://{HOST}:{PORT}/.well-known/agent.json")

    uvicorn.run(server.build(), host=HOST, port=PORT)


if __name__ == "__main__":
    serve()
