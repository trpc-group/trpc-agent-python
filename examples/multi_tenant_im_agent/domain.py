"""Domain types shared by the gateway, adapters, storage, and runtime."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class ChatType(str, Enum):
    DIRECT = "direct"
    GROUP = "group"


class StorageBackend(str, Enum):
    MEMORY = "memory"
    REDIS = "redis"
    SQL = "sql"


@dataclass(frozen=True)
class ChannelBinding:
    """Binds one public IM account/bot to exactly one tenant."""

    channel: str
    account_id: str
    webhook_secret_env: str
    bot_token_env: str = ""
    outbound_webhook_env: str = ""
    enabled: bool = True


@dataclass(frozen=True)
class TenantConfig:
    tenant_id: str
    display_name: str
    agent_app_id: str
    agent_name: str = "assistant"
    model_name: str = "gpt-4o-mini"
    model_base_url: str = ""
    model_api_key_env: str = "OPENAI_API_KEY"
    session_backend: StorageBackend = StorageBackend.REDIS
    session_dsn_env: str = "REDIS_URL"
    bindings: tuple[ChannelBinding, ...] = ()
    tool_allowlist: tuple[str, ...] = ()
    allowed_user_ids: tuple[str, ...] = ()
    max_input_chars: int = 8_000
    request_token_budget: int = 4_096
    monthly_token_budget: int = 1_000_000
    model_timeout_seconds: int = 90
    session_lease_seconds: int = 120

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> TenantConfig:
        data = dict(value)
        data["session_backend"] = StorageBackend(data.get("session_backend", "redis"))
        data["bindings"] = tuple(
            ChannelBinding(**item) for item in data.get("bindings", ())
        )
        for key in ("tool_allowlist", "allowed_user_ids"):
            data[key] = tuple(data.get(key, ()))
        return cls(**data)


@dataclass(frozen=True)
class InboundMessage:
    tenant_id: str
    channel: str
    account_id: str
    external_message_id: str
    user_id: str
    conversation_id: str
    chat_type: ChatType
    text: str
    received_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def payload_hash(self) -> str:
        canonical = json.dumps(
            {
                "tenant_id": self.tenant_id,
                "channel": self.channel,
                "account_id": self.account_id,
                "external_message_id": self.external_message_id,
                "user_id": self.user_id,
                "conversation_id": self.conversation_id,
                "chat_type": self.chat_type.value,
                "text": self.text,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AgentReply:
    text: str
    # ``None`` means that the provider omitted usage metadata.  A real zero is
    # kept distinct so callers never mistake it for an unknown value.
    token_count: int | None = None
    cost: float = 0.0
    tool_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class DeliveryRequest:
    channel: str
    account_id: str
    conversation_id: str
    text: str
    credentials_env: Mapping[str, str] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ChannelResponse:
    status_code: int = 200
    body: Mapping[str, Any] = field(default_factory=lambda: {"ok": True})
    headers: Mapping[str, str] = field(default_factory=dict)


def stable_subject_id(namespace_secret: str, *parts: str, prefix: str) -> str:
    """Create a non-reversible, stable identifier safe for logs and storage."""

    material = "\x1f".join(parts).encode("utf-8")
    digest = hmac.new(
        namespace_secret.encode("utf-8"), material, hashlib.sha256
    ).hexdigest()[:32]
    return f"{prefix}_{digest}"


def derive_session_id(message: InboundMessage, namespace_secret: str) -> str:
    """Derive tenant/channel-scoped sessions without sticky worker affinity.

    Direct chats are isolated per user. Group chats deliberately share one
    session per group so members see the same context. A channel thread id, if
    supplied, becomes another isolation component.
    """

    thread_id = str(message.metadata.get("thread_id", ""))
    identity = (
        message.user_id
        if message.chat_type is ChatType.DIRECT
        else message.conversation_id
    )
    return stable_subject_id(
        namespace_secret,
        message.tenant_id,
        message.channel,
        message.account_id,
        message.chat_type.value,
        identity,
        thread_id,
        prefix="ses",
    )


def derive_user_id(message: InboundMessage, namespace_secret: str) -> str:
    """Hash the actual IM actor for authorization and audit."""

    return stable_subject_id(
        namespace_secret,
        message.tenant_id,
        message.channel,
        message.account_id,
        message.user_id,
        prefix="usr",
    )


def derive_session_user_id(message: InboundMessage, namespace_secret: str) -> str:
    """Return the tRPC-Agent user key that owns the Session namespace.

    tRPC-Agent indexes sessions by app_name + user_id + session_id. Group
    members therefore need one shared group user key as well as a shared
    session_id; the actor-specific hash remains available for audit.
    """

    identity = (
        message.user_id
        if message.chat_type is ChatType.DIRECT
        else message.conversation_id
    )
    return stable_subject_id(
        namespace_secret,
        message.tenant_id,
        message.channel,
        message.account_id,
        message.chat_type.value,
        identity,
        prefix="owner",
    )
