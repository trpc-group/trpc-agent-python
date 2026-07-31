# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Explicit request extractors for script-bearing execution boundaries."""

from __future__ import annotations

import shlex
from collections.abc import Callable
from typing import Any
from typing import Optional
from typing import Protocol

from trpc_agent_sdk.context import InvocationContext

from ._models import SafetyScanRequest


class ToolRequestExtractor(Protocol):
    """Build safety requests only from fields with known script semantics."""

    def __call__(
        self,
        tool: Any,
        args: dict[str, Any],
        invocation_context: Optional[InvocationContext],
    ) -> SafetyScanRequest | tuple[SafetyScanRequest, ...] | None:
        ...


class ToolArgumentExtractor:
    """Extract one explicitly named script/command field from Tool args."""

    def __init__(
        self,
        *,
        script_field: str,
        language: str = "shell",
        language_field: Optional[str] = None,
        argv_field: Optional[str] = None,
        cwd_field: Optional[str] = None,
        env_field: Optional[str] = None,
    ):
        self._script_field = script_field
        self._language = language
        self._language_field = language_field
        self._argv_field = argv_field
        self._cwd_field = cwd_field
        self._env_field = env_field

    def __call__(
        self,
        tool: Any,
        args: dict[str, Any],
        invocation_context: Optional[InvocationContext],
    ) -> Optional[SafetyScanRequest]:
        raw = args.get(self._script_field)
        if not isinstance(raw, str):
            return None
        language = args.get(self._language_field, self._language) if self._language_field else self._language
        if not isinstance(language, str):
            language = self._language
        argv_value = args.get(self._argv_field, ()) if self._argv_field else ()
        argv = tuple(str(item) for item in argv_value) if isinstance(argv_value, (list, tuple)) else ()
        env_value = args.get(self._env_field, {}) if self._env_field else {}
        env = env_value if isinstance(env_value, dict) else {}
        cwd_value = args.get(self._cwd_field) if self._cwd_field else None
        cwd = cwd_value if isinstance(cwd_value, str) else None
        return SafetyScanRequest(
            script=raw,
            language=language,
            command=raw if language in {"shell", "bash", "sh"} else None,
            argv=argv,
            cwd=cwd,
            env=env,
            tool_name=getattr(tool, "name", None),
            source_type="tool",
            invocation_id=getattr(invocation_context, "invocation_id", None),
            session_id=getattr(invocation_context, "session_id", None) if invocation_context is not None else None,
        )


def workspace_request(
    command: str,
    argv: tuple[str, ...] | list[str],
    *,
    cwd: str = "",
    env: Optional[dict[str, str]] = None,
    invocation_context: Optional[InvocationContext] = None,
) -> SafetyScanRequest:
    """Represent structured argv without losing argument boundaries."""
    values = (command, *tuple(argv))
    return SafetyScanRequest(
        script=shlex.join(values),
        language="argv",
        command=command,
        argv=tuple(argv),
        cwd=cwd or None,
        env=env or {},
        source_type="workspace",
        invocation_id=getattr(invocation_context, "invocation_id", None),
        session_id=getattr(invocation_context, "session_id", None) if invocation_context is not None else None,
    )


CallableRequestFactory = Callable[[tuple[Any, ...], dict[str, Any]], SafetyScanRequest | None]
MCPRequestExtractor = Callable[[str, dict[str, Any]], SafetyScanRequest | None]
