# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass
from dataclasses import field
from typing import Awaitable
from typing import Callable
from typing import TypeAlias

from nanobot.bus.events import InboundMessage
from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.sessions import BaseSessionService

from .._utils import parse_origin
from ..config import ClawConfig
from ..config import DEFAULT_USER_ID


@dataclass
class TrpcClawCommandHandlerParams:
    """Command handler for trpc_claw."""
    config: ClawConfig
    bus: MessageBus
    session_service: BaseSessionService
    active_tasks: dict[str, list[asyncio.Task]] = field(default_factory=dict)
    background_tasks: dict[str, list[asyncio.Task]] = field(default_factory=dict)


TrpcClawCommandHandlerCallback: TypeAlias = Callable[[InboundMessage, TrpcClawCommandHandlerParams], Awaitable[None]]


class TrpcClawCommandHandler:
    """Command handler for trpc_claw."""

    def __init__(self,
                 params: TrpcClawCommandHandlerParams,
                 callback: dict[str, TrpcClawCommandHandlerCallback] | None = None):
        self.params = params
        self.callbacks = {
            "/stop": self._handle_stop,
            "/new": self._handle_new,
            "/help": self._handle_help,
            "/restart": self._handle_restart,
            "/quit": self._handle_quit,
            "/exit": self._handle_quit,
            "quit": self._handle_quit,
            "exit": self._handle_quit,
        }
        if callback:
            self.callbacks.update(callback)
        self.restarting = False

    async def handle(self, msg: InboundMessage) -> bool:
        """Handle the command.
        Args:
            msg: The inbound message.
        Returns:
            bool: True if the command is handled, False otherwise.
        """
        if msg.content.strip().lower() in self.callbacks:
            await self.callbacks[msg.content.strip().lower()](msg, self.params)
            return True

        return False

    async def _handle_stop(self, msg: InboundMessage, params: TrpcClawCommandHandlerParams) -> None:
        """Cancel active tasks for the current session key."""
        tasks = params.active_tasks.pop(msg.session_key, [])
        bg_tasks = params.background_tasks.pop(msg.session_key, [])
        cancelled = 0
        for t in tasks:
            if not t.done():
                t.cancel()
                cancelled += 1
        for t in bg_tasks:
            if not t.done():
                t.cancel()
                cancelled += 1
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if bg_tasks:
            await asyncio.gather(*bg_tasks, return_exceptions=True)
        content = f"Stopped {cancelled} task(s)." if cancelled else "No active task to stop."
        await params.bus.publish_outbound(OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=content,
        ))

    async def _handle_new(self, msg: InboundMessage, params: TrpcClawCommandHandlerParams) -> None:
        """Start a new conversation by clearing current session state."""
        # Cancel any in-flight work for this session first.
        await self._handle_stop(msg, params)

        channel, _ = parse_origin(msg)
        user_id = msg.sender_id or f"{channel}_{DEFAULT_USER_ID}"
        app_name = params.config.runtime.app_name

        try:
            await params.session_service.delete_session(
                app_name=app_name,
                user_id=user_id,
                session_id=msg.session_key,
            )
        except Exception:
            logger.error("Failed to clear session for /new: %s", msg.session_key)
            await params.bus.publish_outbound(
                OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content="Failed to start a new session. Please try again.",
                ))
            return

        await params.bus.publish_outbound(
            OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="New session started.",
            ))

    async def _handle_help(self, msg: InboundMessage, params: TrpcClawCommandHandlerParams) -> None:
        """Show available runtime commands."""
        await params.bus.publish_outbound(
            OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=("OpenClaw commands:\n"
                         "/new — Start a new conversation\n"
                         "/stop — Stop active tasks in current session\n"
                         "/restart — Restart worker process and reload config\n"
                         "/quit|/exit (or quit|exit) — End current interaction\n"
                         "/help — Show available commands"),
            ))

    async def _handle_quit(self, msg: InboundMessage, params: TrpcClawCommandHandlerParams) -> None:
        """End current interaction for this channel/session."""
        await params.bus.publish_outbound(OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content="Bye.",
        ))

    async def _handle_restart(self, msg: InboundMessage, params: TrpcClawCommandHandlerParams) -> None:
        """Restart worker process (WeCom command only)."""
        if self.restarting:
            await params.bus.publish_outbound(
                OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content="Restart already in progress...",
                ))
            return
        self.restarting = True
        await params.bus.publish_outbound(
            OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="Restarting worker process and reloading config...",
            ))
        asyncio.create_task(self._restart_process())

    async def _restart_process(self, *, delay_s: float = 0.5) -> None:
        """Replace current process to reload config/runtime state."""
        await asyncio.sleep(delay_s)
        logger.warning("Restarting process to reload config")
        try:
            argv = [sys.executable, *sys.argv]
            os.execv(sys.executable, argv)
        except Exception as ex:  # pylint: disable=broad-except
            logger.error("Failed to restart process: %s", ex)
