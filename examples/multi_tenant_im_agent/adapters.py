"""Inbound normalization, callback verification, and outbound delivery."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from .config import ConfigurationError, require_secret
from .domain import (
    AgentReply,
    ChannelBinding,
    ChatType,
    DeliveryRequest,
    InboundMessage,
    TenantConfig,
)


class InvalidCallbackError(ValueError):
    pass


class UnsupportedMessageError(ValueError):
    pass


def _headers_lower(headers: Mapping[str, str]) -> dict[str, str]:
    return {key.lower(): value for key, value in headers.items()}


class ChannelAdapter(ABC):
    name: str

    @abstractmethod
    def verify(
        self,
        *,
        binding: ChannelBinding,
        headers: Mapping[str, str],
        query: Mapping[str, str],
        raw_body: bytes,
    ) -> None:
        """Raise InvalidCallbackError when callback authenticity is invalid."""

    @abstractmethod
    def parse(
        self, *, tenant: TenantConfig, binding: ChannelBinding, raw_body: bytes
    ) -> InboundMessage:
        """Normalize one provider callback into the internal message envelope."""

    @abstractmethod
    def delivery(
        self, *, binding: ChannelBinding, message: InboundMessage, reply: AgentReply
    ) -> DeliveryRequest:
        """Create a provider-independent delivery request."""


class TelegramAdapter(ChannelAdapter):
    name = "telegram"

    def verify(self, *, binding, headers, query, raw_body) -> None:
        expected = require_secret(binding.webhook_secret_env)
        actual = _headers_lower(headers).get("x-telegram-bot-api-secret-token", "")
        if not hmac.compare_digest(actual, expected):
            raise InvalidCallbackError("invalid Telegram webhook secret")

    def parse(
        self, *, tenant: TenantConfig, binding: ChannelBinding, raw_body: bytes
    ) -> InboundMessage:
        try:
            update = json.loads(raw_body)
            message = update.get("message") or update.get("edited_message")
            if not isinstance(message, dict):
                raise UnsupportedMessageError(
                    "Telegram update has no supported message"
                )
            sender = message.get("from") or {}
            chat = message.get("chat") or {}
            text = message.get("text") or message.get("caption") or ""
            if not text:
                raise UnsupportedMessageError(
                    "only Telegram text/caption messages are supported"
                )
            external_id = f"{update['update_id']}:{message.get('message_id', '')}"
            chat_type = (
                ChatType.DIRECT if chat.get("type") == "private" else ChatType.GROUP
            )
            return InboundMessage(
                tenant_id=tenant.tenant_id,
                channel=self.name,
                account_id=binding.account_id,
                external_message_id=external_id,
                user_id=str(sender["id"]),
                conversation_id=str(chat["id"]),
                chat_type=chat_type,
                text=str(text),
                metadata={"thread_id": str(message.get("message_thread_id", ""))},
            )
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise InvalidCallbackError("malformed Telegram update") from exc

    def delivery(
        self, *, binding: ChannelBinding, message: InboundMessage, reply: AgentReply
    ) -> DeliveryRequest:
        return DeliveryRequest(
            channel=self.name,
            account_id=binding.account_id,
            conversation_id=message.conversation_id,
            text=reply.text,
            credentials_env={"bot_token": binding.bot_token_env},
            metadata={"thread_id": str(message.metadata.get("thread_id", ""))},
        )


class WeComAdapter(ChannelAdapter):
    """Enterprise WeChat callback adapter.

    It verifies both plaintext ``signature`` callbacks and encrypted
    ``msg_signature`` callbacks. Encrypted payload decryption is intentionally
    delegated to an ingress/KMS plugin; plaintext JSON and XML are normalized
    here so the core routing logic stays provider independent.
    """

    name = "wecom"

    def verify(self, *, binding, headers, query, raw_body) -> None:
        token = require_secret(binding.webhook_secret_env)
        timestamp = query.get("timestamp", "")
        nonce = query.get("nonce", "")
        signature = query.get("msg_signature") or query.get("signature") or ""
        if not timestamp or not nonce or not signature:
            raise InvalidCallbackError("missing WeCom signature parameters")
        try:
            if abs(int(time.time()) - int(timestamp)) > 300:
                raise InvalidCallbackError("expired WeCom callback")
        except ValueError as exc:
            raise InvalidCallbackError("invalid WeCom timestamp") from exc

        encrypted = ""
        if query.get("msg_signature"):
            encrypted = self._extract_encrypt(raw_body)
            if not encrypted:
                raise InvalidCallbackError(
                    "encrypted WeCom callback has no Encrypt field"
                )
        pieces = [token, timestamp, nonce]
        if encrypted:
            pieces.append(encrypted)
        expected = hashlib.sha1("".join(sorted(pieces)).encode("utf-8")).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise InvalidCallbackError("invalid WeCom callback signature")

    @staticmethod
    def _extract_encrypt(raw_body: bytes) -> str:
        try:
            payload = json.loads(raw_body)
            return str(payload.get("Encrypt") or payload.get("encrypt") or "")
        except json.JSONDecodeError:
            try:
                root = ET.fromstring(raw_body)
                return root.findtext("Encrypt", default="")
            except ET.ParseError:
                return ""

    def parse(
        self, *, tenant: TenantConfig, binding: ChannelBinding, raw_body: bytes
    ) -> InboundMessage:
        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError:
            try:
                root = ET.fromstring(raw_body)
            except ET.ParseError as exc:
                raise InvalidCallbackError("malformed WeCom callback") from exc
            payload = {child.tag: child.text or "" for child in root}

        if payload.get("Encrypt") or payload.get("encrypt"):
            raise UnsupportedMessageError(
                "encrypted WeCom body must be decrypted by the configured ingress plugin"
            )
        text_value = (
            payload.get("Content")
            or payload.get("content")
            or payload.get("text")
            or ""
        )
        if isinstance(text_value, dict):
            text_value = text_value.get("content", "")
        if not text_value:
            raise UnsupportedMessageError("only WeCom text messages are supported")
        user_id = (
            payload.get("FromUserName")
            or payload.get("from_user")
            or payload.get("userid")
        )
        conversation_id = payload.get("ChatId") or payload.get("chatid") or user_id
        message_id = payload.get("MsgId") or payload.get("msgid")
        if not user_id or not conversation_id or not message_id:
            raise InvalidCallbackError("WeCom callback lacks message identity fields")
        raw_chat_type = str(
            payload.get("ChatType") or payload.get("chattype") or "single"
        ).lower()
        chat_type = (
            ChatType.GROUP
            if raw_chat_type in {"group", "groupchat"}
            else ChatType.DIRECT
        )
        return InboundMessage(
            tenant_id=tenant.tenant_id,
            channel=self.name,
            account_id=binding.account_id,
            external_message_id=str(message_id),
            user_id=str(user_id),
            conversation_id=str(conversation_id),
            chat_type=chat_type,
            text=str(text_value),
            metadata={"to_user": str(payload.get("ToUserName") or "")},
        )

    def delivery(
        self, *, binding: ChannelBinding, message: InboundMessage, reply: AgentReply
    ) -> DeliveryRequest:
        return DeliveryRequest(
            channel=self.name,
            account_id=binding.account_id,
            conversation_id=message.conversation_id,
            text=reply.text,
            credentials_env={"outbound_webhook": binding.outbound_webhook_env},
            metadata={"user_id": message.user_id},
        )


class ChannelSender(Protocol):
    async def send(self, request: DeliveryRequest) -> Mapping[str, Any]: ...


@dataclass
class NoopChannelSender:
    """Offline/demo sender. It records no secret and performs no network I/O."""

    async def send(self, request: DeliveryRequest) -> Mapping[str, Any]:
        return {"accepted": True, "channel": request.channel}


class HttpChannelSender:
    """Production delivery client with bounded timeouts and no secret logging."""

    def __init__(self, timeout_seconds: float = 10.0):
        self.timeout_seconds = timeout_seconds

    async def send(self, request: DeliveryRequest) -> Mapping[str, Any]:
        import httpx

        if request.channel == "telegram":
            token = require_secret(request.credentials_env.get("bot_token", ""))
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            payload: dict[str, Any] = {
                "chat_id": request.conversation_id,
                "text": request.text[:4096],
            }
            if request.metadata.get("thread_id"):
                payload["message_thread_id"] = request.metadata["thread_id"]
        elif request.channel == "wecom":
            url = require_secret(request.credentials_env.get("outbound_webhook", ""))
            payload = {"msgtype": "text", "text": {"content": request.text[:2048]}}
        else:
            raise ConfigurationError(
                f"no HTTP sender configured for channel: {request.channel}"
            )

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            result = response.json()
        return result if isinstance(result, dict) else {"accepted": True}


def default_adapters() -> dict[str, ChannelAdapter]:
    adapters: tuple[ChannelAdapter, ...] = (TelegramAdapter(), WeComAdapter())
    return {adapter.name: adapter for adapter in adapters}
