# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""A2A Server with Cancel Support

This example demonstrates how to run an A2A service with cancel support.
The server configures cancel_wait_timeout so that when the client sends
a cancel_task request, the server waits for the agent to finish its
cancellation cleanup before responding.
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
PORT = 18082
CANCEL_WAIT_TIMEOUT = 3.0


def create_a2a_service() -> TrpcA2aAgentService:
    """Create A2A service with cancel support.

    The cancel_wait_timeout controls how long the server waits for the
    backend agent to complete its cancellation before responding to the
    client's cancel_task request.
    """
    from agent.agent import root_agent

    executor_config = TrpcA2aAgentExecutorConfig(
        cancel_wait_timeout=CANCEL_WAIT_TIMEOUT,
    )

    a2a_svc = TrpcA2aAgentService(
        service_name="weather_agent_cancel_service",
        agent=root_agent,
        executor_config=executor_config,
    )
    a2a_svc.initialize()

    return a2a_svc


def serve():
    """Start the A2A server with cancel support."""
    a2a_svc = create_a2a_service()

    request_handler = DefaultRequestHandler(
        agent_executor=a2a_svc,
        task_store=InMemoryTaskStore(),
    )

    server = A2AStarletteApplication(
        agent_card=a2a_svc.agent_card,
        http_handler=request_handler,
    )

    print("Starting A2A server with cancel support...")
    print(f"Listening on: http://{HOST}:{PORT}")
    print(f"Agent card: http://{HOST}:{PORT}/.well-known/agent.json")
    print(f"Cancel wait timeout: {CANCEL_WAIT_TIMEOUT}s")

    uvicorn.run(server.build(), host=HOST, port=PORT)


if __name__ == "__main__":
    serve()
