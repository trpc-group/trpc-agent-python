# -*- coding: utf-8 -*-
#
# Copyright @ 2025 Tencent.com
"""A2A Server Example

This example uses the standard A2A SDK server (A2AStarletteApplication) to serve
a trpc-agent as an A2A service over plain HTTP, with the standard protocol
(artifact-first streaming and unprefixed metadata keys).
"""

from dotenv import load_dotenv

from trpc_agent_sdk.sessions import InMemorySessionService

from _agui_runner import create_agui_runner

load_dotenv()

HOST = "127.0.0.1"
PORT = 18080

app_name = "agui_demo"


def serve():
    """Start the A2A server using standard HTTP (uvicorn + Starlette)."""
    service_name = "weather_agent_standard_service"
    uri = "/weather_agent"
    from agent.agent import root_agent
    session_service = InMemorySessionService()
    agui_runner = create_agui_runner(app_name,
                                     service_name,
                                     uri,
                                     root_agent=root_agent,
                                     session_service=session_service)
    agui_runner.run(HOST, PORT)


if __name__ == "__main__":
    serve()
