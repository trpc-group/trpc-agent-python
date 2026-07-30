# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""AG-UI Server with Cancel Support

This example starts an AG-UI protocol server that supports automatic
cancellation when the client closes the SSE connection. The agent will
stop at the next checkpoint and partial results are saved to the session.
"""

from dotenv import load_dotenv

from trpc_agent_sdk.sessions import InMemorySessionService

from _agui_runner import create_agui_runner

load_dotenv()

HOST = "127.0.0.1"
PORT = 18080

app_name = "agui_cancel_demo"


def serve():
    """Start the AG-UI server with cancel support."""
    service_name = "weather_agent_cancel_service"
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
