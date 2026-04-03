# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""This file is used to repair channels."""

from typing import Callable
from typing import Dict

from nanobot.channels.manager import ChannelManager
from trpc_agent_sdk.log import logger

_channels_to_repair: Dict[str, Callable[[str, ChannelManager], None]] = {}


def register_channel_repair(channel_name: str, repair_func: Callable[[str, ChannelManager], None]) -> None:
    """Register a channel to be repaired."""
    _channels_to_repair[channel_name] = repair_func


def repair_channels(channel_manager: ChannelManager) -> None:
    """Repair all channels."""
    for name, repair_func in _channels_to_repair.items():
        try:
            logger.debug(f"Repairing channel {name}...")
            repair_func(name, channel_manager)
            logger.debug(f"Channel {name} repaired successfully.")
        except Exception as e:
            logger.error(f"Failed to repair channel {name}: {e}")
