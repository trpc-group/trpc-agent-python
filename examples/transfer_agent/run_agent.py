#!/usr/bin/env python3

# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Run the Transfer Agent demo to demonstrate TransferAgent with remote A2A agent."""

import asyncio
import os
import socket
import threading
import time
from pathlib import Path
import sys
from urllib.parse import urlparse
import uuid

from dotenv import load_dotenv
import uvicorn

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore

from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.server.a2a import TrpcA2aAgentExecutorConfig
from trpc_agent_sdk.server.a2a import TrpcA2aAgentService
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv()


class _EmbeddedA2AServer:
    """Run an A2A server in a background thread for local demos."""

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self._startup_error: Exception | None = None

    @staticmethod
    def _is_port_open(host: str, port: int) -> bool:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            return False

    def _build_uvicorn_server(self) -> uvicorn.Server:
        try:
            from examples.a2a.agent.agent import root_agent as a2a_root_agent
        except ModuleNotFoundError:
            # Allow running from examples/transfer_agent by appending repo root.
            repo_root = Path(__file__).resolve().parents[2]
            if str(repo_root) not in sys.path:
                sys.path.insert(0, str(repo_root))
            from examples.a2a.agent.agent import root_agent as a2a_root_agent

        a2a_svc = TrpcA2aAgentService(
            service_name="embedded_weather_agent_service",
            agent=a2a_root_agent,
            executor_config=TrpcA2aAgentExecutorConfig(),
        )
        a2a_svc.initialize()

        request_handler = DefaultRequestHandler(
            agent_executor=a2a_svc,
            task_store=InMemoryTaskStore(),
        )
        app = A2AStarletteApplication(
            agent_card=a2a_svc.agent_card,
            http_handler=request_handler,
        ).build()
        config = uvicorn.Config(app=app, host=self.host, port=self.port, log_level="warning")
        return uvicorn.Server(config)

    def _serve_forever(self) -> None:
        try:
            self._server = self._build_uvicorn_server()
            self._server.run()
        except Exception as exc:  # pylint: disable=broad-except
            self._startup_error = exc

    def start_if_needed(self) -> bool:
        """Start embedded server only when local target is not already running.

        Returns:
            True if this helper started a new server, False otherwise.
        """
        if self._is_port_open(self.host, self.port):
            print(f"ℹ️ Reusing existing remote A2A server at http://{self.host}:{self.port}")
            return False

        self._thread = threading.Thread(target=self._serve_forever, daemon=True)
        self._thread.start()

        deadline = time.time() + 8.0
        while time.time() < deadline:
            if self._startup_error is not None:
                raise RuntimeError(f"Embedded remote A2A server failed to start: {self._startup_error}")
            if self._is_port_open(self.host, self.port):
                print(f"✅ Embedded remote A2A server started at http://{self.host}:{self.port}")
                return True
            time.sleep(0.1)
        raise RuntimeError(f"Failed to start embedded remote A2A server on {self.host}:{self.port}")

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=3.0)


def _parse_local_base_url(base_url: str) -> tuple[str, int] | None:
    parsed = urlparse(base_url)
    if parsed.scheme not in ("http", "https"):
        return None
    host = parsed.hostname or ""
    if host not in {"127.0.0.1", "localhost"}:
        return None
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return host, port


async def run_transfer_agent():
    """Run the Transfer Agent demo to demonstrate TransferAgent with remote A2A agent."""

    app_name = "transfer_agent_demo"

    from agent.agent import root_agent
    embedded_server: _EmbeddedA2AServer | None = None

    auto_start = os.getenv("TRPC_TRANSFER_AUTO_START_REMOTE_A2A", "1").strip().lower() not in {"0", "false", "no"}
    target_base_url = getattr(getattr(root_agent, "target_agent", None), "agent_base_url", None) or ""
    local_target = _parse_local_base_url(target_base_url) if target_base_url else None

    if auto_start and local_target:
        host, port = local_target
        embedded_server = _EmbeddedA2AServer(host, port)
        embedded_server.start_if_needed()

    if hasattr(root_agent, "target_agent") and hasattr(root_agent.target_agent, "initialize"):
        # TrpcRemoteA2aAgent must be initialized before the first remote call.
        await root_agent.target_agent.initialize()

    session_service = InMemorySessionService()
    runner = Runner(app_name=app_name, agent=root_agent, session_service=session_service)

    user_id = "demo_user"

    demo_queries = [
        "What is the weather in Shenzhen today?",
    ]

    try:
        for query in demo_queries:
            current_session_id = str(uuid.uuid4())

            await session_service.create_session(
                app_name=app_name,
                user_id=user_id,
                session_id=current_session_id,
            )

            print(f"🆔 Session ID: {current_session_id[:8]}...")
            print(f"📝 User: {query}")

            user_content = Content(parts=[Part.from_text(text=query)])

            last_agent = None

            print("🤖 Assistant: ", end="", flush=True)
            async for event in runner.run_async(user_id=user_id, session_id=current_session_id, new_message=user_content):
                if not event.content or not event.content.parts:
                    continue

                if last_agent != event.author:
                    print(f"\n\n ============ [{event.author}] ============\n")
                    last_agent = event.author

                if event.partial:
                    for part in event.content.parts:
                        if part.text and not part.thought:
                            print(part.text, end="", flush=True)
                    continue

                for part in event.content.parts:
                    if part.thought:
                        continue
                    if part.function_call:
                        print(f"\n🔧 [Invoke Tool:: {part.function_call.name}({part.function_call.args})]")
                    elif part.function_response:
                        print(f"📊 [Tool Result: {part.function_response.response}]")
                    # elif part.text:
                    #     print(f"\n✅ {part.text}")
    finally:
        if embedded_server is not None:
            embedded_server.stop()


if __name__ == "__main__":
    asyncio.run(run_transfer_agent())
