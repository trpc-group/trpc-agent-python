# -*- coding: utf-8 -*-
#
# Copyright @ 2025 Tencent.com
"""HTTP/SSE service handlers for generated agent service."""

import random
import uuid

from aiohttp import web
from aiohttp.web_request import Request

from trpc.context import TrpcContext
from trpc.http import routes
from trpc.log import logger

from _agent_runner import AgentRunner
from _agent_runner import get_agent_runner


@routes.route("/sse", method="POST")
async def stream_api(ctx: TrpcContext, request: Request) -> web.StreamResponse:
    del ctx
    rsp = web.StreamResponse(
        status=200,
        reason="OK",
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
    await rsp.prepare(request)
    text = await request.text()

    user_id = _get_user_id(request.headers.get("userid", ""))
    session_id = _get_session_id(request.headers.get("sessionid", ""))
    logger.info("user_id: %s, session_id: %s", user_id, session_id)

    agent_runner: AgentRunner = get_agent_runner()

    async def send_event(msg: str):
        text = msg
        if isinstance(text, bytes):
            text = text.decode("utf-8", errors="replace")
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        payload = "".join(f"data: {line}\n" for line in normalized.split("\n")) + "\n"
        await rsp.write(payload.encode("utf-8"))

    agent_runner.send_func = send_event

    try:
        await agent_runner.conversation(text, user_id, session_id)
    finally:
        await rsp.write_eof()
    return rsp


def _get_user_id(user_id: str) -> str:
    if not user_id:
        charset = "abc123!@#"
        return "".join(random.choices(charset, k=10))
    return user_id


def _get_session_id(session_id: str) -> str:
    if not session_id:
        return str(uuid.uuid4())
    return session_id
