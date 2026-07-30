#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Normalize common Tool, Skill, and MCP Tool argument shapes."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ._models import ScriptLanguage
from ._models import ScriptPayload
from ._models import ScriptScanRequest
from ._models import ToolMetadata

_SCRIPT_KEYS = ("script", "code", "command", "cmd", "python_code", "bash_code")


def default_request_extractor(req: Any, *, tool_name: str) -> ScriptScanRequest | None:
    """Extract executable payloads; return None for non-script tool calls."""

    if not isinstance(req, Mapping):
        raise TypeError("tool safety input must be a mapping")

    payloads: list[ScriptPayload] = []
    raw_blocks = req.get("code_blocks")
    if raw_blocks is not None:
        if not isinstance(raw_blocks, list):
            raise TypeError("code_blocks must be a list")
        for index, block in enumerate(raw_blocks):
            if not isinstance(block, Mapping) or not isinstance(block.get("code"), str):
                raise TypeError(f"code_blocks[{index}] must contain string code")
            payloads.append(
                ScriptPayload(
                    language=_language(block.get("language"), tool_name, "code"),
                    content=block["code"],
                    source=f"code_blocks[{index}]",
                ))

    for key in _SCRIPT_KEYS:
        if key not in req:
            continue
        value = req[key]
        if not isinstance(value, str):
            raise TypeError(f"{key} must be a string")
        if value.strip():
            payloads.append(
                ScriptPayload(
                    language=_language(req.get("language"), tool_name, key),
                    content=value,
                    argv=_string_list(req.get("command_args", req.get("args", [])), "command_args"),
                    stdin=_optional_string(req.get("stdin"), "stdin"),
                    source=key,
                ))
    if not payloads:
        return None

    env = req.get("env", {})
    if env is None:
        env = {}
    if not isinstance(env, Mapping) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in env.items()):
        raise TypeError("env must map strings to strings")

    timeout = req.get("timeout", req.get("timeout_seconds"))
    if timeout is not None and not isinstance(timeout, (int, float)):
        raise TypeError("timeout must be numeric")
    output_limit = req.get("max_output_bytes")
    if output_limit is not None and not isinstance(output_limit, int):
        raise TypeError("max_output_bytes must be an integer")

    return ScriptScanRequest(
        payloads=payloads,
        cwd=_optional_string(req.get("cwd", req.get("working_directory")), "cwd"),
        env=dict(env),
        metadata=ToolMetadata(name=tool_name),
        requested_timeout=float(timeout) if timeout is not None else None,
        max_output_bytes=output_limit,
    )


def _language(value: Any, tool_name: str, key: str) -> ScriptLanguage:
    if value is None or value == "":
        hint = f"{tool_name} {key}".lower()
        return ScriptLanguage.PYTHON if "python" in hint or key == "python_code" else ScriptLanguage.BASH
    if not isinstance(value, str):
        raise TypeError("language must be a string")
    normalized = value.lower()
    if normalized in {"python", "py", "python3"}:
        return ScriptLanguage.PYTHON
    if normalized in {"bash", "sh", "shell"}:
        return ScriptLanguage.BASH
    raise ValueError(f"unsupported script language: {value}")


def _optional_string(value: Any, field: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    return value


def _string_list(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise TypeError(f"{field} must be a string or list of strings")
    return value
