# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Advanced utils for compact session manager."""

import hashlib
import json
from contextlib import contextmanager
from typing import Any
from typing import Iterator

from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.types import Content

INTERNAL_COMPACTION_METADATA_KEY = "_trpc_agent_internal_compaction"
_MISSING = object()


@contextmanager
def internal_compaction_call(agent_context: AgentContext | None) -> Iterator[None]:
    """Prevent the compact filter from recursively handling its own model call."""
    if agent_context is None:
        yield
        return
    previous = agent_context.get_metadata(INTERNAL_COMPACTION_METADATA_KEY, _MISSING)
    agent_context.with_metadata(INTERNAL_COMPACTION_METADATA_KEY, True)
    try:
        yield
    finally:
        if previous is _MISSING:
            agent_context.metadata.pop(INTERNAL_COMPACTION_METADATA_KEY, None)
        else:
            agent_context.with_metadata(INTERNAL_COMPACTION_METADATA_KEY, previous)


def content_signature(content: Content) -> str:
    """Generate a stable signature that preserves message identity."""
    parts: list[dict[str, Any]] = []
    for part in content.parts or []:
        if part.text is not None:
            parts.append({
                "type": "text",
                "sha256": hashlib.sha256(part.text.encode("utf-8")).hexdigest(),
            })
        elif part.function_call is not None:
            parts.append({
                "type": "function_call",
                "id": getattr(part.function_call, "id", None),
                "name": part.function_call.name,
            })
        elif part.function_response is not None:
            parts.append({
                "type": "function_response",
                "id": getattr(part.function_response, "id", None),
                "name": part.function_response.name,
            })
        elif part.executable_code is not None:
            parts.append({"type": "executable_code"})
        elif part.code_execution_result is not None:
            parts.append({"type": "code_execution_result"})
        else:
            parts.append({"type": "other"})
    serialized = json.dumps(
        {
            "role": content.role,
            "parts": parts
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
