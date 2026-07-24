# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""A2A Server for ReviewMind — 智能代码审查助手

Exposes the CodeReviewAgent as an A2A service over HTTP,
enabling multi-turn interactive code review sessions.

Usage:
    python -m examples.skills_code_review_agent.server.a2a_server

    # Or directly:
    # python server/a2a_server.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure the parent package is importable
_parent = Path(__file__).resolve().parent.parent
if str(_parent) not in sys.path:
    sys.path.insert(0, str(_parent))

import uvicorn
from dotenv import load_dotenv

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore

from trpc_agent_sdk.memory import SqlMemoryService
from trpc_agent_sdk.sessions import SqliteSessionService
from trpc_agent_sdk.server.a2a import TrpcA2aAgentExecutorConfig
from trpc_agent_sdk.server.a2a import TrpcA2aAgentService

from agent.agent import root_agent

HOST = os.getenv("A2A_HOST", "127.0.0.1")
PORT = int(os.getenv("A2A_PORT", "18081"))

# Database path for persistent storage
DB_PATH = os.getenv("REVIEWMIND_DB_PATH", os.path.join(os.path.dirname(__file__), "..", "reviewmind.db"))


def create_a2a_service() -> TrpcA2aAgentService:
    """Create the A2A service wrapping the CodeReviewAgent.

    The service exposes the code review agent via the A2A protocol,
    supporting multi-turn conversations and artifact-first streaming.
    The agent uses SQLite-backed session and memory services for
    persistent conversation history and long-term memory.
    """
    executor_config = TrpcA2aAgentExecutorConfig()
    session_service = SqliteSessionService(DB_PATH)
    memory_service = SqlMemoryService(
        db_url=f"sqlite:///{DB_PATH}",
        ttl=3600 * 24 * 30,  # 30-day TTL for long-term memory
    )

    a2a_svc = TrpcA2aAgentService(
        service_name="reviewmind_code_review",
        agent=root_agent,
        session_service=session_service,
        memory_service=memory_service,
        executor_config=executor_config,
    )
    a2a_svc.initialize()

    return a2a_svc


def serve() -> None:
    """Start the A2A server."""
    load_dotenv()

    a2a_svc = create_a2a_service()

    request_handler = DefaultRequestHandler(
        agent_executor=a2a_svc,
        task_store=InMemoryTaskStore(),
    )

    server = A2AStarletteApplication(
        agent_card=a2a_svc.agent_card,
        http_handler=request_handler,
    )

    print(f"🤖 ReviewMind A2A Server starting...")
    print(f"   Listening on: http://{HOST}:{PORT}")
    print(f"   Agent card:   http://{HOST}:{PORT}/.well-known/agent.json")
    print(f"   Send a diff to: POST http://{HOST}:{PORT}/sendTask")
    print()

    uvicorn.run(server.build(), host=HOST, port=PORT)


if __name__ == "__main__":
    serve()