# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""AG-UI Server for ReviewMind — 智能代码审查助手

Exposes the CodeReviewAgent as an AG-UI service, enabling real-time
streaming events and frontend interaction via CopilotKit or other
AG-UI compatible UIs.

Usage:
    python -m examples.skills_code_review_agent.server.agui_server

    # Or directly:
    # python server/agui_server.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure the parent package is importable
_parent = Path(__file__).resolve().parent.parent
if str(_parent) not in sys.path:
    sys.path.insert(0, str(_parent))

from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI
from pydantic import BaseModel

from trpc_agent_sdk.agents import BaseAgent
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.memory import SqlMemoryService
from trpc_agent_sdk.sessions import InMemorySessionService, SqliteSessionService
from trpc_agent_sdk.server.ag_ui import AgUiAgent
from trpc_agent_sdk.server.ag_ui import AgUiManager
from trpc_agent_sdk.server.ag_ui import AgUiService
from trpc_agent_sdk.server.ag_ui import AgUiUserFeedBack

from agent.agent import root_agent

HOST = os.getenv("AGUI_HOST", "127.0.0.1")
PORT = int(os.getenv("AGUI_PORT", "18080"))

# Database path for persistent storage
DB_PATH = os.getenv("REVIEWMIND_DB_PATH", os.path.join(os.path.dirname(__file__), "..", "reviewmind.db"))


class HealthResponse(BaseModel):
    """Response body for GET /health."""
    status: str = "ok"
    app_name: str
    version: str = "1.0.0"


class AguiRunner:
    """AG-UI runner: owns the AG-UI manager for the FastAPI server."""

    def __init__(self, app_name: str) -> None:
        self._app_name = app_name
        self._agui_manager = AgUiManager()
        self._app = self._create_app()

    @property
    def app(self) -> FastAPI:
        return self._app

    def register_service(self, service_name: str, service: AgUiService) -> None:
        self._agui_manager.register_service(service_name, service)

    def run(self, host: str, port: int, **kwargs: Any) -> None:
        self._app.get("/health", response_model=HealthResponse, tags=["meta"])(self.health)
        self._agui_manager.set_app(self._app)
        self._agui_manager.run(host, port, **kwargs)

    @asynccontextmanager
    async def _lifespan(self, app: FastAPI):
        logger.info("ReviewMind AG-UI Server starting up.")
        yield
        logger.info("ReviewMind AG-UI Server shutting down.")
        await self._agui_manager.close()

    def _create_app(self) -> FastAPI:
        app = FastAPI(
            title="ReviewMind AG-UI Server",
            description="AG-UI service for ReviewMind code review agent",
            version="1.0.0",
            lifespan=self._lifespan,
        )
        return app

    async def health(self) -> HealthResponse:
        return HealthResponse(app_name=self._app_name)


def _create_agui_agent(name: str, root_agent: BaseAgent, **kwargs) -> AgUiAgent:
    """Create AgUiAgent wrapping the CodeReviewAgent."""
    return AgUiAgent(
        trpc_agent=root_agent,
        app_name=name,
        **kwargs,
    )


def create_agui_runner(
    app_name: str,
    service_name: str,
    uri: str,
    **kwargs: Any,
) -> AguiRunner:
    """Create AgUiService and add agent to it."""
    agui_runner = AguiRunner(app_name)
    agui_service = AgUiService(service_name, app=agui_runner.app)
    agui_agent = _create_agui_agent(app_name, **kwargs)
    agui_service.add_agent(uri, agui_agent)
    agui_runner.register_service(service_name, agui_service)
    return agui_runner


def serve() -> None:
    """Start the AG-UI server."""
    load_dotenv()

    service_name = "reviewmind_code_review"
    uri = "/code_review_agent"
    session_service = SqliteSessionService(DB_PATH)
    memory_service = SqlMemoryService(
        db_url=f"sqlite:///{DB_PATH}",
        ttl=3600 * 24 * 30,
    )

    agui_runner = create_agui_runner(
        app_name="reviewmind",
        service_name=service_name,
        uri=uri,
        root_agent=root_agent,
        session_service=session_service,
        memory_service=memory_service,
    )

    print(f"🤖 ReviewMind AG-UI Server starting...")
    print(f"   Listening on: http://{HOST}:{PORT}")
    print(f"   Agent URI:    http://{HOST}:{PORT}{uri}")
    print(f"   Health:       http://{HOST}:{PORT}/health")
    print(f"   Compatible with CopilotKit and other AG-UI clients")
    print()

    agui_runner.run(HOST, PORT)


if __name__ == "__main__":
    serve()