# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""This file is used to parse the origin of an inbound message."""

import mimetypes
from pathlib import Path
from typing import Optional

from nanobot.bus.events import InboundMessage
from nanobot.utils.helpers import detect_image_mime
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.types import Part


def parse_origin(msg: InboundMessage) -> tuple[str, str]:
    """Resolve delivery origin channel/chat from inbound message.

    nanobot subagent announcements are injected as system messages where
    `chat_id` encodes `<origin_channel>:<origin_chat_id>`.
    """
    if msg.channel != "system":
        return msg.channel, msg.chat_id

    if ":" in msg.chat_id:
        return msg.chat_id.split(":", 1)
    return "cli", msg.chat_id


# Channels that don't support stream progress
CHANNELS_WITHOUT_STREAM_PROGRESS: set[str] = {"cli", "telegram", "wecom"}


def is_channel_supports_stream_progress(channel: str) -> bool:
    """Check if the channel supports stream progress.

    Args:
        channel: The channel name.

    Returns:
        bool: True if the channel supports stream progress, False otherwise.
    """
    return channel not in CHANNELS_WITHOUT_STREAM_PROGRESS


def register_channel_without_stream_progress(channel: str) -> None:
    """Register the channel without stream progress.

    Args:
        channel: The channel name.
    """
    CHANNELS_WITHOUT_STREAM_PROGRESS.add(channel)


def merge_assistant_text(current: str, incoming: str) -> str:
    """Merge assistant text chunks while avoiding cumulative duplicates.
    
    Args:
        current: The current text.
        incoming: The incoming text.

    Returns:
        str: The merged text.
    """
    if not incoming:
        return current
    if not current:
        return incoming

    # Provider may send cumulative text (incoming starts with current).
    if incoming.startswith(current):
        return incoming
    # Exact / trailing duplicate.
    if current.endswith(incoming):
        return current
    # Containment fallback.
    if incoming in current:
        return current
    if current in incoming:
        return incoming
    return current + incoming


def build_user_parts(query: str, media: Optional[list[str]] = None) -> list[Part]:
    """Build user parts with optional image attachments.

    Keep behavior aligned with nanobot:
    - only image media is injected into model input
    - non-image media (for example video/audio/doc files) is ignored

    Args:
        query: The query text.
        media: The media paths.

    Returns:
        list[Part]: The user parts.
    """
    parts: list[Part] = []
    for media_path in media or []:
        try:
            file_path = Path(media_path)
            if not file_path.is_file():
                continue
            raw = file_path.read_bytes()
            mime = detect_image_mime(raw) or mimetypes.guess_type(str(file_path))[0]
            if not mime or not mime.startswith("image/"):
                continue
            parts.append(Part.from_bytes(data=raw, mime_type=mime))
        except Exception as ex:  # pylint: disable=broad-except
            logger.warning("Skip invalid media '%s': %s", media_path, ex)
            continue

    parts.append(Part.from_text(text=query))
    return parts


def merge_raw_events(existing: list[Event], recent: list[Event]) -> list[Event]:
    """Merge raw archive with recent events while avoiding duplicates.

    Args:
        existing: The existing events.
        recent: The recent events.

    Returns:
        list[Event]: The merged events.
    """
    merged: list[Event] = []
    seen_ids: set[str] = set()
    for event in [*(existing or []), *(recent or [])]:
        event_id = str(getattr(event, "id", "") or "")
        if event_id:
            if event_id in seen_ids:
                continue
            seen_ids.add(event_id)
        merged.append(event)
    return merged


def write_file(src: Path, dest: Path, force: bool = False):
    """Write file to destination.

    Args:
        src: The source file.
        dest: The destination file.
        force: Whether to force write the file.
    """
    if dest.exists() and not force:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(src.read_text(encoding="utf-8") if src else "", encoding="utf-8")
