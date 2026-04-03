# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""FastAPI application factory and server entry point.

Usage (script)::

    # In examples/fastapi_server/
    python3 run_server.py --model_key sk-... --model_url https://api.openai.com/v1 --port 8080

Usage (programmatic)::

    from _app import RunnerManager, create_app
    import uvicorn

    manager = RunnerManager(app_name="my-app", model_key="sk-...",
                            model_url="https://api.openai.com/v1",
                            model_name="gpt-4o-mini")
    app = create_app(manager)
    uvicorn.run(app, host="0.0.0.0", port=8080)

Endpoints
---------
GET  /health            - liveness check.
POST /v1/chat           - synchronous, returns full reply in one response.
POST /v1/chat/stream    - SSE streaming, yields chunks as they arrive.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator
from typing import Optional

import uvicorn
from _runner_manager import RunnerManager
from _schemas import ChatRequest
from _schemas import ChatResponse
from _schemas import HealthResponse
from _schemas import StreamChunk
from _schemas import ToolEvent
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part


def create_app(manager: RunnerManager) -> FastAPI:
    """Build and return a configured FastAPI application.

    Args:
        manager: A fully initialized :class:`RunnerManager` that will be
            shared across all requests for the lifetime of the server.

    Returns:
        A :class:`fastapi.FastAPI` instance ready to be served by uvicorn.
    """

    @asynccontextmanager
    async def _lifespan(app: FastAPI):  # noqa: ARG001
        """Startup / shutdown hook: close the runner on exit."""
        logger.info("TRPC Agent FastAPI server starting up.")
        yield
        logger.info("TRPC Agent FastAPI server shutting down.")
        await manager.close()

    app = FastAPI(
        title="TRPC Agent Server",
        description="HTTP API for TRPC Agent",
        version="1.0.0",
        lifespan=_lifespan,
    )

    # ------------------------------------------------------------------
    # GET /health
    # ------------------------------------------------------------------

    @app.get("/health", response_model=HealthResponse, tags=["meta"])
    async def health() -> HealthResponse:
        """Liveness check - always returns 200 while the server is up."""
        return HealthResponse(app_name=manager.app_name)

    # ------------------------------------------------------------------
    # POST /v1/chat  (synchronous, full response)
    # ------------------------------------------------------------------

    @app.post("/v1/chat", response_model=ChatResponse, tags=["chat"])
    async def chat(req: ChatRequest) -> ChatResponse:  # pylint: disable=unused-variable
        """Send a message to the agent and receive the complete reply.

        If ``session_id`` is omitted, a new session is created automatically.
        Pass the returned ``session_id`` in follow-up requests to continue the
        same conversation.
        """
        session_id = req.session_id or manager.new_session_id()
        user_content = Content(parts=[Part.from_text(text=req.message)])

        reply_parts: list[str] = []
        tool_events: list[ToolEvent] = []

        try:
            async for event in manager.runner.run_async(
                    user_id=req.user_id,
                    session_id=session_id,
                    new_message=user_content,
            ):
                if not event.content or not event.content.parts:
                    continue

                for part in event.content.parts:
                    if part.thought:
                        # Internal reasoning steps - not surfaced to the caller.
                        continue

                    if part.text:
                        reply_parts.append(part.text)

                    elif part.function_call:
                        tool_events.append(
                            ToolEvent(
                                type="tool_call",
                                name=part.function_call.name,
                                data=dict(part.function_call.args or {}),
                            ))

                    elif part.function_response:
                        tool_events.append(
                            ToolEvent(
                                type="tool_result",
                                name=part.function_response.name,
                                data=part.function_response.response,
                            ))

        except Exception as exc:
            logger.exception("Error during agent run (session=%s)", session_id)
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        return ChatResponse(
            session_id=session_id,
            user_id=req.user_id,
            reply="".join(reply_parts),
            tool_events=tool_events,
        )

    # ------------------------------------------------------------------
    # POST /v1/chat/stream  (SSE streaming)
    # ------------------------------------------------------------------

    @app.post("/v1/chat/stream", tags=["chat"])
    async def chat_stream(req: ChatRequest) -> StreamingResponse:  # pylint: disable=unused-variable
        """Send a message and receive the agent reply as a Server-Sent Events stream.

        Each SSE event carries a JSON-serialized :class:`StreamChunk`.
        The stream is terminated by a ``done`` chunk (or an ``error`` chunk on failure).

        Example SSE payload::

            data: {"type":"text_delta","data":"Hello","session_id":"abc"}

            data: {"type":"done","data":null,"session_id":"abc"}
        """
        session_id = req.session_id or manager.new_session_id()
        user_content = Content(parts=[Part.from_text(text=req.message)])

        async def _event_generator() -> AsyncGenerator[str, None]:
            try:
                async for event in manager.runner.run_async(
                        user_id=req.user_id,
                        session_id=session_id,
                        new_message=user_content,
                ):
                    if not event.content or not event.content.parts:
                        continue

                    for part in event.content.parts:
                        if part.thought:
                            continue

                        if part.text:
                            yield _sse(StreamChunk(
                                type="text_delta",
                                data=part.text,
                                session_id=session_id,
                            ))

                        elif part.function_call:
                            yield _sse(
                                StreamChunk(
                                    type="tool_call",
                                    data={
                                        "name": part.function_call.name,
                                        "args": dict(part.function_call.args or {}),
                                    },
                                    session_id=session_id,
                                ))

                        elif part.function_response:
                            yield _sse(
                                StreamChunk(
                                    type="tool_result",
                                    data={
                                        "name": part.function_response.name,
                                        "response": part.function_response.response,
                                    },
                                    session_id=session_id,
                                ))

                # Signal normal completion.
                yield _sse(StreamChunk(type="done", session_id=session_id))

            except Exception as exc:
                logger.exception("Error during streaming run (session=%s)", session_id)
                yield _sse(StreamChunk(type="error", data=str(exc), session_id=session_id))

        return StreamingResponse(
            _event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                # Disable nginx/proxy buffering so chunks arrive in real time.
                "X-Accel-Buffering": "no",
            },
        )

    return app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sse(chunk: StreamChunk) -> str:
    """Serialize *chunk* as a single SSE ``data:`` line."""
    return f"data: {chunk.model_dump_json()}\n\n"


# ---------------------------------------------------------------------------
# Server entry point (called by the CLI)
# ---------------------------------------------------------------------------


def run_server(
    app_name: str,
    model_key: str,
    model_url: Optional[str],
    model_name: str,
    host: str,
    port: int,
    agent_module: Optional[str] = None,
    instruction: Optional[str] = None,
) -> None:
    """Build the RunnerManager, create the FastAPI app, and start uvicorn.

    Args:
        app_name:     Logical name of this agent application.
        model_key:    API key for the LLM provider.
        model_url:    Base URL of the LLM API endpoint.
        model_name:   Model identifier (e.g. ``gpt-4o-mini``).
        host:         Network interface to bind (e.g. ``0.0.0.0``).
        port:         TCP port to listen on.
        agent_module: Optional Python module path that exports ``root_agent``
                      or ``create_agent()``.  When ``None``, a default assistant
                      agent is created from the provided model credentials.
        instruction:  Optional system instruction override for the default agent.
    """
    manager = RunnerManager(
        app_name=app_name,
        model_key=model_key,
        model_url=model_url or "",
        model_name=model_name,
        agent_module=agent_module,
        instruction=instruction,
    )
    app = create_app(manager)

    logger.info("Starting TRPC Agent FastAPI server on %s:%d", host, port)
    uvicorn.run(app, host=host, port=port)
