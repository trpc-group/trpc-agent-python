"""Model mock: an OpenAI-compatible HTTP server driven by scripted turns.

The server runs in a separate process so its CPU/RSS do not pollute the
benchmark process. Targets reach it through their normal OpenAI client.
"""

from __future__ import annotations

import asyncio
import json
import multiprocessing
import time
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.responses import StreamingResponse

from ._config import ModelConf

_MOCK_HOST = "127.0.0.1"


@dataclass(frozen=True)
class TextBlock:
    """One text response block."""

    text: str = "ok"
    chunks: int = 1


@dataclass(frozen=True)
class FunctionBlock:
    """One model-requested function call."""

    name: str
    args: dict[str, Any] = field(default_factory=dict)


Reply = TextBlock | list[FunctionBlock]
Turn = list[Reply]


@dataclass
class ModelReply:
    text: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    chunks: int = 1
    finish_reason: str = "stop"
    error: str | None = None


def _reply_spec(reply: Reply) -> dict[str, Any]:
    if isinstance(reply, TextBlock):
        return {"kind": "text", "text": reply.text, "chunks": reply.chunks}
    if not reply:
        raise ValueError("a function reply must contain at least one block")
    return {
        "kind": "functions",
        "calls": [{
            "name": block.name,
            "args": block.args
        } for block in reply],
    }


def _reply_from_spec(spec: dict[str, Any]) -> Reply:
    kind = spec.get("kind")
    if kind == "text":
        return TextBlock(text=spec.get("text", "ok"), chunks=spec.get("chunks", 1))
    if kind == "functions":
        calls = spec.get("calls") or []
        if not calls:
            raise ValueError("a function reply must contain at least one call")
        return [FunctionBlock(name=call["name"], args=call.get("args") or {}) for call in calls]
    raise ValueError(f"unknown reply kind: {kind!r}")


def script_spec(turns: list[Turn]) -> dict[str, Any]:
    """Serialize a turn script for the mock server process."""

    return {
        "turns": [[_reply_spec(reply) for reply in turn] for turn in turns],
    }


def turns_from_spec(spec: dict[str, Any]) -> list[Turn]:
    return [[_reply_from_spec(reply) for reply in turn] for turn in spec.get("turns", [])]


def reply_from_script(
    reply: Reply,
    *,
    turn: int,
    step: int,
) -> ModelReply:
    """Convert one scripted reply into a model reply."""

    if isinstance(reply, TextBlock):
        return ModelReply(
            text=reply.text,
            chunks=max(1, reply.chunks),
        )
    if not reply:
        raise ValueError("a reply must contain at least one block")
    if not all(isinstance(block, FunctionBlock) for block in reply):
        raise TypeError("a function reply may only contain FunctionBlock entries")
    return ModelReply(
        tool_calls=[{
            "id": f"call_{turn}_{step}_{index}",
            "name": block.name,
            "args": block.args,
        } for index, block in enumerate(reply)],
        finish_reason="tool_calls",
    )


def request_position(messages: list[dict[str, Any]]) -> tuple[int, int]:
    """Return the zero-based turn and reply index for one model request."""

    user_indexes = [index for index, message in enumerate(messages) if message.get("role") == "user"]
    if not user_indexes:
        raise ValueError("model request has no user message")
    turn = len(user_indexes) - 1
    last_user = user_indexes[-1]
    step = sum(1 for message in messages[last_user + 1:] if message.get("role") == "assistant")
    return turn, step


def _split_text(text: str, chunks: int) -> list[str]:
    """Split text into exactly ``chunks`` parts without losing characters."""

    chunks = max(1, chunks)
    if chunks == 1:
        return [text]
    base, extra = divmod(len(text), chunks)
    parts = []
    start = 0
    for index in range(chunks):
        size = base + (1 if index < extra else 0)
        parts.append(text[start:start + size])
        start += size
    return parts


# ─────────────────────────────────────────────────────────────────────────────
# OpenAI-compatible HTTP server
# ─────────────────────────────────────────────────────────────────────────────


def _chunk_payload(delta: dict[str, Any], finish_reason: str | None = None) -> dict[str, Any]:
    return {
        "id": "chatcmpl-bench",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": "bench-model",
        "choices": [{
            "index": 0,
            "delta": delta,
            "finish_reason": finish_reason
        }],
    }


def _usage_payload() -> dict[str, Any]:
    return {
        "id": "chatcmpl-bench",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": "bench-model",
        "choices": [],
        "usage": {
            "prompt_tokens": 16,
            "completion_tokens": 1,
            "total_tokens": 17
        },
    }


def _completion_payload(reply: ModelReply) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": reply.text}
    if reply.tool_calls:
        message["content"] = None
        message["tool_calls"] = [{
            "id": call["id"],
            "type": "function",
            "function": {
                "name": call["name"],
                "arguments": json.dumps(call["args"]),
            },
        } for call in reply.tool_calls]
    return {
        "id": "chatcmpl-bench",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "bench-model",
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": reply.finish_reason,
            },
        ],
        "usage": {
            "prompt_tokens": 16,
            "completion_tokens": 1,
            "total_tokens": 17
        },
    }


def create_app(spec: dict[str, Any], model_conf: dict[str, Any] | None = None):
    """Build the FastAPI app used by the mock server and its tests."""

    turns = turns_from_spec(spec)
    conf = ModelConf(**(model_conf or {}))
    app = FastAPI(title="trpc-agent-bench-mock")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/models")
    async def models() -> dict[str, Any]:
        return {
            "object": "list",
            "data": [{
                "id": "bench-model",
                "object": "model",
                "created": 0,
                "owned_by": "bench"
            }],
        }

    @app.post("/v1/chat/completions")
    async def chat(request: Request):
        body = await request.json()
        messages = body.get("messages") or []
        turn, step = request_position(messages)
        try:
            reply = turns[turn][step]
        except IndexError as exc:
            raise HTTPException(
                status_code=500,
                detail=f"no scripted reply for turn={turn + 1} step={step}",
            ) from exc
        if conf.request_delay_ms > 0:
            await asyncio.sleep(conf.request_delay_ms / 1000.0)
        model_reply = reply_from_script(reply, turn=turn + 1, step=step)
        if model_reply.error:
            raise HTTPException(status_code=500, detail=model_reply.error)
        if body.get("stream"):
            include_usage = bool((body.get("stream_options") or {}).get("include_usage"))
            return StreamingResponse(
                _sse_stream(model_reply, conf, include_usage=include_usage),
                media_type="text/event-stream",
            )
        return JSONResponse(_completion_payload(model_reply))

    return app


async def _sse_stream(
    reply: ModelReply,
    conf: ModelConf,
    *,
    include_usage: bool,
) -> AsyncIterator[str]:

    def emit(payload: dict[str, Any]) -> str:
        return f"data: {json.dumps(payload)}\n\n"

    yield emit(_chunk_payload({"role": "assistant", "content": ""}))

    if reply.tool_calls:
        for index, call in enumerate(reply.tool_calls):
            if index and conf.chunk_delay_ms:
                await asyncio.sleep(conf.chunk_delay_ms / 1000.0)
            yield emit(
                _chunk_payload(
                    {
                        "tool_calls": [
                            {
                                "index": index,
                                "id": call["id"],
                                "type": "function",
                                "function": {
                                    "name": call["name"],
                                    "arguments": json.dumps(call["args"]),
                                },
                            },
                        ],
                    }, ), )
        yield emit(_chunk_payload({}, "tool_calls"))
    else:
        text = reply.text or ""
        for index, chunk in enumerate(_split_text(text, reply.chunks)):
            if index and conf.chunk_delay_ms:
                await asyncio.sleep(conf.chunk_delay_ms / 1000.0)
            yield emit(_chunk_payload({"content": chunk}))
        yield emit(_chunk_payload({}, "stop"))

    if include_usage:
        yield emit(_usage_payload())
    yield "data: [DONE]\n\n"


def _serve(spec: dict[str, Any], model_conf: dict[str, Any], connection: Any) -> None:
    import uvicorn

    app = create_app(spec, model_conf)
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=0,
        log_level="error",
        access_log=False,
    )
    server = uvicorn.Server(config)

    async def run() -> None:
        task = asyncio.create_task(server.serve())
        while not server.started:
            await asyncio.sleep(0.01)
        port = server.servers[0].sockets[0].getsockname()[1]
        connection.send(port)
        await task

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


class MockOpenAIServer:
    """A separately hosted OpenAI-compatible endpoint."""

    def __init__(
        self,
        turns: list[Turn],
        conf: ModelConf | None = None,
    ) -> None:
        self.spec = script_spec(turns)
        self.model_conf = conf or ModelConf()
        self.base_url: str | None = None
        self._process: multiprocessing.Process | None = None
        self._connection: Any = None

    def start(self) -> str:
        context = multiprocessing.get_context("fork")
        parent_connection, child_connection = context.Pipe()
        process = context.Process(
            target=_serve,
            args=(self.spec, asdict(self.model_conf), child_connection),
            daemon=True,
        )
        process.start()
        try:
            if not parent_connection.poll(20.0):
                raise RuntimeError("mock OpenAI server did not report a port")
            port = parent_connection.recv()
        except Exception:
            process.terminate()
            process.join(timeout=5.0)
            raise
        self._process = process
        self._connection = parent_connection
        self.base_url = f"http://{_MOCK_HOST}:{port}/v1"
        return self.base_url

    def stop(self) -> None:
        process = self._process
        self._process = None
        if process is not None and process.is_alive():
            process.terminate()
            process.join(timeout=5.0)
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def __enter__(self) -> "MockOpenAIServer":
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.stop()
