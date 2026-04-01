# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""FastAPI server for TRPC Agent.

Exposes the agent as an HTTP service with both synchronous and
Server-Sent Events (SSE) streaming endpoints.

Public API::

    from trpc_agent_sdk.server.fastapi import create_app, RunnerManager

    manager = RunnerManager(app_name="my-app", model_key="sk-...", ...)
    app = create_app(manager)
"""

from ._app import create_app
from ._app import run_server
from ._runner_manager import RunnerManager
from ._schemas import ChatRequest
from ._schemas import ChatResponse
from ._schemas import HealthResponse
from ._schemas import StreamChunk

__all__ = [
    "create_app",
    "run_server",
    "RunnerManager",
    "ChatRequest",
    "ChatResponse",
    "StreamChunk",
    "HealthResponse",
]
