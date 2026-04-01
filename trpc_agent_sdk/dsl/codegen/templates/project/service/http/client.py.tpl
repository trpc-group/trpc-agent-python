# -*- coding: utf-8 -*-
"""HTTP/SSE client test for generated service."""

import asyncio
import logging
import os
import uuid

from trpc import context
from trpc.client.options import with_timeout
from trpc.config import config
from trpc.http.sse_client import SSEClient
from trpc.plugin import setup


async def test_http_agent():
    """Interactive generated HTTP/SSE client."""
    http_sse_client = SSEClient("trpc.py_trpc_agent.helloworld.Greeter")
    opts = [with_timeout(1000000)]
    ctx = context.Context()
    user_id = "demo_user"
    session_id = str(uuid.uuid4())

    print("Interactive mode. Type 'exit' to quit, 'new' for new session.")
    while True:
        try:
            user_text = input("You: ").strip()
        except EOFError:
            print("\nGoodbye!")
            break
        except KeyboardInterrupt:
            print("\nGoodbye!")
            break

        if not user_text:
            continue

        lowered = user_text.lower()
        if lowered in {"exit", "quit"}:
            print("Goodbye!")
            break

        if lowered == "new":
            session_id = str(uuid.uuid4())
            print(f"New session: {session_id}")
            continue

        print("Assistant:")
        async for event in http_sse_client.request_events(
                ctx,
                "POST",
                "http://127.0.0.1:8080/sse",
                options=opts,
                data=user_text,
                headers={
                    "userid": user_id,
                    "sessionid": session_id,
                },
        ):
            rsp = event.data
            if isinstance(rsp, bytes):
                rsp = rsp.decode("utf-8")
            print(rsp, end="", flush=True)


if __name__ == "__main__":
    conf_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "trpc_python.yaml"))
    config.load_global_config(conf_path, "utf-8")
    setup()
    asyncio.run(test_http_agent())
