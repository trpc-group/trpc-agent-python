# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Telegram channel for trpc-claw."""

from nanobot.channels.manager import ChannelManager
from nanobot.channels.telegram import TelegramChannel as NanobotTelegramChannel
from telegram import Update
from telegram.ext import ContextTypes

from ..config import BOT_NAME
from ._repair import register_channel_repair


class TelegramChannel(NanobotTelegramChannel):
    """Telegram channel for trpc-claw."""

    async def _on_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /start command."""
        if not update.message or not update.effective_user:
            return

        user = update.effective_user
        await update.message.reply_text(f"👋 Hi {user.first_name}! I'm {BOT_NAME}.\n\n"
                                        "Send me a message and I'll respond!\n"
                                        "Type /help to see available commands.")

    async def _on_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /help command, bypassing ACL so all users can access it."""
        if not update.message:
            return
        await update.message.reply_text(f"{BOT_NAME} commands:\n"
                                        "/new — Start a new conversation\n"
                                        "/stop — Stop the current task\n"
                                        "/help — Show available commands")


def repair_telegram_channel(name: str, channel_manager: ChannelManager) -> None:
    """Repair the telegram channel.

    Args:
        channel_manager: ChannelManager instance.
    """
    section = getattr(channel_manager.config.channels, name, None)
    if not section:
        return
    enabled = (section.get("enabled", False) if isinstance(section, dict) else getattr(section, "enabled", False))
    if not enabled:
        return
    channel_manager.channels[name] = TelegramChannel(
        channel_manager.config.channels,
        channel_manager.bus,
        groq_api_key=channel_manager.config.providers.groq.api_key,
    )


register_channel_repair("telegram", repair_telegram_channel)
