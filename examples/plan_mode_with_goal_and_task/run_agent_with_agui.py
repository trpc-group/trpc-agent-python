# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
"""AG-UI server for Plan Mode + Goal + Task combined example.

Exposes the orchestrator (see agent/agent.py) over the AG-UI protocol and
serves a dependency-free static HTML page (static/index.html) from the same
FastAPI app. The page shows plan, session goal, and task board side panels.

Run:
    cd examples/plan_mode_with_goal_and_task
    python3 run_agent_with_agui.py

Then open http://127.0.0.1:18091/ in a browser.

Prerequisites:
    pip install -e '.[ag-ui]'
    Set TRPC_AGENT_API_KEY (and optionally TRPC_AGENT_BASE_URL / TRPC_AGENT_MODEL_NAME)
    in examples/plan_mode_with_goal_and_task/.env
"""

from __future__ import annotations

import argparse
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

from trpc_agent_sdk.log import logger
from trpc_agent_sdk.server.ag_ui import AgUiAgent
from trpc_agent_sdk.server.ag_ui import AgUiManager
from trpc_agent_sdk.server.ag_ui import AgUiService

_EXAMPLE_DIR = Path(__file__).resolve().parent
if str(_EXAMPLE_DIR) not in sys.path:
    sys.path.insert(0, str(_EXAMPLE_DIR))

load_dotenv(_EXAMPLE_DIR / ".env")

HOST = "127.0.0.1"
PORT = 18093
APP_NAME = "plan_mode_goal_task_agui_demo"
AGENT_URI = "/plan_agent"
STATIC_DIR = _EXAMPLE_DIR / "static"
INDEX_HTML = STATIC_DIR / "index.html"

agui_manager = AgUiManager()


class HealthResponse(BaseModel):
    """Response body for GET /health."""

    status: str = "ok"
    app_name: str
    agent_uri: str


def _create_agui_agent() -> AgUiAgent:
    """Lazy factory so each worker process gets its own agent instance."""
    from agent.agent import create_plan_goal_task_agent

    return AgUiAgent(trpc_agent=create_plan_goal_task_agent(), app_name=APP_NAME)


@asynccontextmanager
async def _lifespan(app: FastAPI):  # noqa: ARG001
    if not os.environ.get("TRPC_AGENT_API_KEY"):
        logger.warning(
            "TRPC_AGENT_API_KEY is not set — copy .env values into %s/.env",
            _EXAMPLE_DIR,
        )
    if not INDEX_HTML.is_file():
        raise FileNotFoundError(f"Demo page not found: {INDEX_HTML}")
    logger.info("Plan + Goal + Task AG-UI demo starting up.")
    yield
    logger.info("Plan + Goal + Task AG-UI demo shutting down.")
    await agui_manager.close()


def create_app() -> FastAPI:
    """Build the FastAPI app: AG-UI agent endpoint + static demo page."""
    app = FastAPI(title="Plan + Goal + Task AG-UI Demo", lifespan=_lifespan)

    @app.get("/", response_class=FileResponse)
    async def index() -> FileResponse:
        return FileResponse(INDEX_HTML)

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(app_name=APP_NAME, agent_uri=AGENT_URI)

    service = AgUiService(APP_NAME, app=app)
    service.add_agent(AGENT_URI, _create_agui_agent)
    agui_manager.register_service(APP_NAME, service)
    agui_manager.set_app(app)
    return app


app = create_app()


def serve(host: str = HOST, port: int = PORT) -> None:
    """Start the FastAPI + AG-UI server."""
    print(f"Plan + Goal + Task AG-UI demo:  http://{host}:{port}/")
    print(f"Agent endpoint:                 http://{host}:{port}{AGENT_URI}")
    print(f"Health check:                   http://{host}:{port}/health")
    agui_manager.run(host, port)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plan + Goal + Task AG-UI browser demo")
    parser.add_argument("--host", default=HOST, help=f"bind address (default: {HOST})")
    parser.add_argument("--port", type=int, default=PORT, help=f"listen port (default: {PORT})")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    serve(host=args.host, port=args.port)
