# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Message tool for sending messages to users.

Implemented as a :class:`~trpc_agent_sdk.tools.BaseTool` subclass.

Context passing
---------------
All per-invocation delivery state is read from ``tool_context.agent_context``
metadata.  Callers must write the required keys before the agent turn starts::

    agent_context.with_metadata(MESSAGE_CALLBACK_KEY, async_send_fn)
    agent_context.with_metadata(MESSAGE_CHANNEL_KEY, "telegram")
    agent_context.with_metadata(MESSAGE_CHAT_ID_KEY, "12345")

Optionally::

    agent_context.with_metadata(MESSAGE_ID_KEY, "reply-to-msg-id")

After each tool call the tool writes back a bool under
:data:`MESSAGE_SENT_IN_TURN_KEY` so callers can check whether a message was
dispatched during the current turn.
"""

from __future__ import annotations

from typing import Any
from typing import Awaitable
from typing import Callable
from typing import List
from typing import Optional

from nanobot.bus.events import OutboundMessage
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.tools import BaseTool
from trpc_agent_sdk.types import FunctionDeclaration
from trpc_agent_sdk.types import Schema
from trpc_agent_sdk.types import Type

# ---------------------------------------------------------------------------
# Metadata keys — import these in callers to avoid magic strings
# ---------------------------------------------------------------------------

MESSAGE_CALLBACK_KEY = "message_send_callback"
MESSAGE_CHANNEL_KEY = "message_channel"
MESSAGE_CHAT_ID_KEY = "message_chat_id"
MESSAGE_ID_KEY = "message_message_id"
MESSAGE_SENT_IN_TURN_KEY = "message_sent_in_turn"

_DESCRIPTION = "Send a message to the user. Use this when you want to communicate something."


class MessageTool(BaseTool):
    """trpc-claw tool to send messages to users on chat channels.

    All delivery context (callback, channel, chat_id, message_id) is read
    from ``tool_context.agent_context`` metadata at call time — see module-
    level key constants.

    Args:
        filters_name: Optional filter names forwarded to
                      :class:`~trpc_agent_sdk.tools.BaseTool`.
        filters:      Optional filter instances forwarded to
                      :class:`~trpc_agent_sdk.tools.BaseTool`.
    """

    def __init__(
        self,
        filters_name: Optional[List[str]] = None,
        filters: Optional[List[BaseFilter]] = None,
    ) -> None:
        super().__init__(
            name="message",
            description=_DESCRIPTION,
            filters_name=filters_name,
            filters=filters,
        )

    # ------------------------------------------------------------------
    # BaseTool interface
    # ------------------------------------------------------------------

    def _get_declaration(self) -> FunctionDeclaration:
        return FunctionDeclaration(
            name="message",
            description=_DESCRIPTION,
            parameters=Schema(
                type=Type.OBJECT,
                properties={
                    "content":
                    Schema(
                        type=Type.STRING,
                        description="The message content to send",
                    ),
                    "channel":
                    Schema(
                        type=Type.STRING,
                        description="Optional: override target channel (telegram, discord, etc.)",
                    ),
                    "chat_id":
                    Schema(
                        type=Type.STRING,
                        description="Optional: override target chat/user ID",
                    ),
                    "media":
                    Schema(
                        type=Type.ARRAY,
                        items=Schema(type=Type.STRING),
                        description="Optional: file paths to attach (images, audio, documents)",
                    ),
                },
                required=["content"],
            ),
        )

    async def _run_async_impl(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> Any:
        agent_ctx = tool_context.agent_context

        send_callback: Optional[Callable[[OutboundMessage],
                                         Awaitable[None]]] = (agent_ctx.get_metadata(MESSAGE_CALLBACK_KEY)
                                                              if agent_ctx else None)
        default_channel: str = agent_ctx.get_metadata(MESSAGE_CHANNEL_KEY, "") if agent_ctx else ""
        default_chat_id: str = agent_ctx.get_metadata(MESSAGE_CHAT_ID_KEY, "") if agent_ctx else ""
        message_id: Optional[str] = agent_ctx.get_metadata(MESSAGE_ID_KEY) if agent_ctx else None

        content: str = args.get("content", "")
        channel: str = args.get("channel") or default_channel
        chat_id: str = args.get("chat_id") or default_chat_id
        media: list[str] = args.get("media") or []

        if not channel or not chat_id:
            return (f"Error: no delivery context — set {MESSAGE_CHANNEL_KEY!r} and "
                    f"{MESSAGE_CHAT_ID_KEY!r} in agent_context metadata before calling this tool")
        if not send_callback:
            return (f"Error: send callback not configured — set {MESSAGE_CALLBACK_KEY!r} "
                    "in agent_context metadata before calling this tool")

        msg = OutboundMessage(
            channel=channel,
            chat_id=chat_id,
            content=content,
            media=media,
            metadata={"message_id": message_id},
        )

        try:
            await send_callback(msg)
            if agent_ctx and channel == default_channel and chat_id == default_chat_id:
                agent_ctx.with_metadata(MESSAGE_SENT_IN_TURN_KEY, True)
            media_info = f" with {len(media)} attachments" if media else ""
            return f"Message sent to {channel}:{chat_id}{media_info}"
        except Exception as e:  # pylint: disable=broad-except
            return f"Error sending message: {e}"
