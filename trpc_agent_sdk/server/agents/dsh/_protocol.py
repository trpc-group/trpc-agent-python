# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Wire and internal protocol types for the DSH agent."""

from __future__ import annotations

import json
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Literal
from typing import TypedDict

from ._sdk import JsonObject

DSH_SESSION_FORMAT_VERSION = 4
DSH_SESSION_STATE_KEY = "_trpc_dsh_session"


class _DSHModelSpecRequired(TypedDict):
    provider: str
    model: str


class DSHModelSpec(_DSHModelSpecRequired, total=False):
    """Wire model selection for ``session/trpc-prompt``.

    ``provider`` and ``model`` are required. ``reasoningEffort`` and
    ``maxTokens`` are omitted when the caller does not set them. The object is
    plain JSON because it is also the shape accepted by the DSH runtime.
    """

    # DSH provider id, for example ``openai`` or ``anthropic``.
    provider: str

    # Model id, for example ``deepseek-chat`` or ``claude-sonnet-4``.
    model: str

    # Optional reasoning effort forwarded to the provider.
    reasoningEffort: str

    # Optional output-token limit.
    maxTokens: int


@dataclass
class DSHRunInput:
    """Everything one ``session/trpc-prompt`` call needs.

    ``content_blocks`` uses the same plain-JSON shape as
    ``deepseek_harness.HarnessClient.session_prompt``. The framework converts
    text and inline image/file parts before ``custom_dsh_input`` runs; the
    JS plugin later turns encoded blocks into durable attachment references.

    The object is mutable so ``custom_dsh_input`` can adjust it in place. A
    ``None`` value means "let the framework fill the default": ``jsonl`` is
    loaded from the tRPC session state and ``version`` follows that snapshot.

    An empty ``jsonl`` marks the first turn. The JS plugin then calls
    ``ctx.agents.create()`` with ``cwd`` and DSH builds the session header;
    the receipt returns the initial snapshot. Later turns send the stored
    snapshot back and the runtime resumes it.
    """

    # Stable DSH session id. The framework derives it from the tRPC
    # app/user/session identity, so one tRPC session maps to one DSH session.
    session_id: str

    # DSH content blocks (plain JSON). The framework converts text and inline
    # image/file parts before ``custom_dsh_input`` sees the object.
    content_blocks: list[JsonObject]

    # Only used when DSH creates the session on the first turn. Once ``jsonl``
    # carries a header, that header is authoritative.
    cwd: str | None = None

    # Full DSH session snapshot. ``None`` means the framework loads it from
    # ``Session.state[DSH_SESSION_STATE_KEY]``; ``""`` means a new session and
    # asks the runtime to create one.
    jsonl: str | None = None

    # DSH session format version. ``None`` is inferred from the JSONL header.
    version: int | None = None

    # Optional per-run model override. The default comes from
    # ``DSHConfig.model.to_spec()``; ``custom_dsh_input`` may replace it for a
    # single turn. The generated DSH route and gateway stay process-level.
    model: DSHModelSpec | None = None

    # Business metadata forwarded to ``session/trpc-prompt`` as ``meta``. User
    # JS plugins can read it through ``ctx.get('trpcRequestContext')``.
    meta: dict[str, Any] = field(default_factory=dict)

    def to_params(self) -> dict[str, object]:
        """Map this input onto the ``session/trpc-prompt`` parameters."""

        if not self.session_id:
            raise ValueError("DSHRunInput.session_id must not be empty")
        if not self.content_blocks:
            raise ValueError("DSHRunInput.content_blocks must not be empty")
        if self.jsonl is None or self.version is None:
            raise ValueError("DSHRunInput must be finalized before it is sent")

        params: dict[str, object] = {
            "sessionId": self.session_id,
            "contentBlocks": self.content_blocks,
            "jsonl": self.jsonl,
            "version": self.version,
            "meta": self.meta,
        }
        if self.cwd is not None:
            params["cwd"] = self.cwd
        if self.model is not None:
            params["model"] = self.model
        return params


@dataclass(frozen=True)
class Frame:
    """Normalized DSH notification consumed by the agent."""

    kind: Literal["delta", "event", "status", "snapshot"]
    data: dict[str, Any]


def jsonl_version(jsonl: str) -> int:
    """Return the format version from a DSH JSONL header.

    An empty snapshot is valid: the runtime creates the header at seed time,
    so the request still uses the current format version.
    """

    for line in jsonl.splitlines():
        if not line.strip():
            continue
        try:
            header = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError("DSH session JSONL first line is not valid JSON") from exc
        if not isinstance(header, dict) or header.get("type") != "session":
            raise ValueError("DSH session JSONL first line is not a session header")
        version = header.get("version")
        if not isinstance(version, int) or isinstance(version, bool):
            raise ValueError("DSH session JSONL header is missing an integer version")
        return version
    return DSH_SESSION_FORMAT_VERSION
