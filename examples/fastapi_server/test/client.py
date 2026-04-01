#!/usr/bin/env python3

# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Interactive Python client for the TRPC Agent FastAPI server.

Usage::

    python3 client.py                          # connect to 127.0.0.1:8080
    python3 client.py --url http://host:8080   # custom server URL
    python3 client.py --user alice             # custom user ID

In-chat commands::

    /new      start a new session (clear conversation context)
    /sync     toggle between streaming and synchronous mode
    /help     show command help
    /quit     exit

Dependencies: httpx, httpx-sse  (both are in trpc-agent requirements)
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Optional

try:
    import httpx
    from httpx_sse import connect_sse
except ImportError:
    print(
        "Error: required packages not found.\n"
        "  pip install httpx httpx-sse",
        file=sys.stderr,
    )
    sys.exit(1)

# ---------------------------------------------------------------------------
# ANSI colour helpers
# ---------------------------------------------------------------------------

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
CYAN = "\033[36m"
YELLOW = "\033[33m"
MAGENTA = "\033[35m"
RED = "\033[31m"
GREY = "\033[90m"


def _c(text: str, *codes: str) -> str:
    """Wrap *text* with ANSI *codes* (no-op when stdout is not a tty)."""
    if not sys.stdout.isatty():
        return text
    return "".join(codes) + text + RESET


# ---------------------------------------------------------------------------
# Chat client
# ---------------------------------------------------------------------------


class AgentClient:
    """Stateful chat client that maintains a session across turns."""

    def __init__(self, base_url: str, user_id: str, stream: bool = True) -> None:
        self.base_url = base_url.rstrip("/")
        self.user_id = user_id
        self.stream = stream
        self.session_id: Optional[str] = None
        self._http = httpx.Client(timeout=120.0)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def new_session(self) -> None:
        """Discard the current session ID so the next message starts fresh."""
        self.session_id = None

    def send(self, message: str) -> None:
        """Send *message* and print the reply to stdout."""
        if self.stream:
            self._send_stream(message)
        else:
            self._send_sync(message)

    def close(self) -> None:
        self._http.close()

    # ------------------------------------------------------------------
    # Streaming mode
    # ------------------------------------------------------------------

    def _send_stream(self, message: str) -> None:
        payload = self._build_payload(message)
        url = f"{self.base_url}/v1/chat/stream"

        print(_c("Assistant: ", BOLD, GREEN), end="", flush=True)
        reply_started = False
        tool_lines: list[str] = []

        try:
            with connect_sse(self._http,
                             "POST",
                             url,
                             headers={"Content-Type": "application/json"},
                             content=json.dumps(payload)) as es:
                for event in es.iter_sse():
                    if not event.data:
                        continue
                    chunk = json.loads(event.data)
                    ctype = chunk.get("type", "")

                    if ctype == "text_delta":
                        text = chunk.get("data", "")
                        print(text, end="", flush=True)
                        reply_started = True

                    elif ctype == "tool_call":
                        data = chunk.get("data", {})
                        line = _c(
                            f"\n  ⚙  tool_call  {data.get('name')}  {data.get('args', {})}",
                            DIM,
                            YELLOW,
                        )
                        tool_lines.append(line)
                        print(line, end="", flush=True)

                    elif ctype == "tool_result":
                        data = chunk.get("data", {})
                        line = _c(
                            f"\n  ↩  tool_result {data.get('name')}  {data.get('response')}",
                            DIM,
                            MAGENTA,
                        )
                        tool_lines.append(line)
                        print(line, end="", flush=True)

                    elif ctype == "done":
                        # Capture session_id from the last chunk.
                        sid = chunk.get("session_id", "")
                        if sid:
                            self.session_id = sid

                    elif ctype == "error":
                        print(
                            _c(f"\n[error] {chunk.get('data')}", BOLD, RED),
                            flush=True,
                        )

        except httpx.RequestError as exc:
            print(_c(f"\n[connection error] {exc}", BOLD, RED))
            return

        if reply_started or tool_lines:
            print()  # trailing newline

    # ------------------------------------------------------------------
    # Synchronous mode
    # ------------------------------------------------------------------

    def _send_sync(self, message: str) -> None:
        payload = self._build_payload(message)
        url = f"{self.base_url}/v1/chat"

        try:
            resp = self._http.post(url, json=payload)
            resp.raise_for_status()
        except httpx.RequestError as exc:
            print(_c(f"[connection error] {exc}", BOLD, RED))
            return
        except httpx.HTTPStatusError as exc:
            print(_c(f"[http error {exc.response.status_code}] {exc.response.text}", BOLD, RED))
            return

        body = resp.json()
        self.session_id = body.get("session_id", self.session_id)

        print(_c("Assistant: ", BOLD, GREEN), end="")

        for ev in body.get("tool_events", []):
            if ev["type"] == "tool_call":
                print(_c(
                    f"\n  ⚙  tool_call  {ev['name']}  {ev['data']}",
                    DIM,
                    YELLOW,
                ))
            elif ev["type"] == "tool_result":
                print(_c(
                    f"\n  ↩  tool_result {ev['name']}  {ev['data']}",
                    DIM,
                    MAGENTA,
                ))

        print(body.get("reply", ""))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_payload(self, message: str) -> dict:
        payload: dict = {"message": message, "user_id": self.user_id}
        if self.session_id:
            payload["session_id"] = self.session_id
        return payload


# ---------------------------------------------------------------------------
# REPL
# ---------------------------------------------------------------------------

_HELP = """\
Commands:
  /new     start a new session (clears conversation context)
  /sync    toggle streaming ↔ synchronous mode
  /help    show this message
  /quit    exit
"""


def _print_banner(client: AgentClient) -> None:
    mode = "stream" if client.stream else "sync"
    print(_c("━" * 52, DIM))
    print(_c(" TRPC Agent Chat Client", BOLD))
    print(_c(f" server  : {client.base_url}", DIM))
    print(_c(f" user    : {client.user_id}", DIM))
    print(_c(f" mode    : {mode}", DIM))
    print(_c(" type /help for commands", DIM))
    print(_c("━" * 52, DIM))


def _print_status(client: AgentClient) -> None:
    mode = _c("streaming", CYAN) if client.stream else _c("synchronous", YELLOW)
    sid = _c(client.session_id[:8] + "…", GREY) if client.session_id else _c("(new)", GREY)
    print(_c(f"  mode={mode}  session={sid}", DIM))


def run_repl(client: AgentClient) -> None:
    _print_banner(client)

    while True:
        try:
            raw = input(_c("\nYou: ", BOLD, CYAN)).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not raw:
            continue

        # --- built-in commands ---
        if raw.startswith("/"):
            cmd = raw.lower()
            if cmd in ("/quit", "/exit", "/q"):
                break
            elif cmd == "/new":
                client.new_session()
                print(_c("  ✓ new session started", DIM, GREEN))
            elif cmd == "/sync":
                client.stream = not client.stream
                mode = "streaming" if client.stream else "synchronous"
                print(_c(f"  ✓ switched to {mode} mode", DIM, GREEN))
            elif cmd == "/help":
                print(_c(_HELP, DIM))
            else:
                print(_c(f"  unknown command: {raw}  (try /help)", DIM, YELLOW))
            continue

        # --- send to agent ---
        client.send(raw)
        _print_status(client)

    print(_c("\nGoodbye!", BOLD))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="client",
        description="Interactive chat client for the TRPC Agent FastAPI server.",
    )
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:8080",
        help="Server base URL (default: http://127.0.0.1:8080).",
    )
    parser.add_argument(
        "--user",
        default="user_001",
        help="User ID sent with every request (default: user_001).",
    )
    parser.add_argument(
        "--sync",
        action="store_true",
        help="Use synchronous mode instead of SSE streaming.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    client = AgentClient(
        base_url=args.url,
        user_id=args.user,
        stream=not args.sync,
    )
    try:
        run_repl(client)
    finally:
        client.close()
