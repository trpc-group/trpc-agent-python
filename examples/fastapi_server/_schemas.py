# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Pydantic schemas for the TRPC Agent FastAPI server."""

from typing import Any
from typing import List
from typing import Optional

from pydantic import BaseModel


class ChatRequest(BaseModel):
    """Body for POST /v1/chat and POST /v1/chat/stream."""

    message: str
    session_id: Optional[str] = None
    user_id: str = "default"


class ToolEvent(BaseModel):
    """A single tool invocation or result captured during agent execution."""

    type: str  # "tool_call" or "tool_result"
    name: str
    data: Any = None


class ChatResponse(BaseModel):
    """Response body for POST /v1/chat (non-streaming)."""

    session_id: str
    user_id: str
    reply: str
    tool_events: List[ToolEvent] = []


class StreamChunk(BaseModel):
    """Single Server-Sent Event payload for POST /v1/chat/stream.

    type values:
      text_delta  - incremental text from the agent.
      tool_call   - agent invoking a tool; data = {"name": str, "args": dict}.
      tool_result - tool returned; data = {"name": str, "response": any}.
      done        - stream finished successfully; data is null.
      error       - an error occurred; data contains the error message string.
    """

    type: str
    data: Any = None
    session_id: str = ""


class HealthResponse(BaseModel):
    """Response body for GET /health."""

    status: str = "ok"
    app_name: str
    version: str = "1.0.0"
