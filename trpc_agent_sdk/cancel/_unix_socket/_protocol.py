# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Shared protocol helpers for the Unix socket control plane."""

from __future__ import annotations

import asyncio
from typing import Any
from typing import Dict
from typing import Optional

from .._types import SessionKey
from .._types import Status


def _key_to_data(key: SessionKey) -> Dict[str, str]:
    return {
        "app_name": key.app_name,
        "user_id": key.user_id,
        "session_id": key.session_id,
    }


def _key_from_data(data: Dict[str, Any]) -> SessionKey:
    return SessionKey(
        app_name=str(data["app_name"]),
        user_id=str(data["user_id"]),
        session_id=str(data["session_id"]),
    )


def _status_to_data(status: Status) -> Dict[str, Any]:
    return {"ok": status.ok, "error_msg": status.error_msg}


def _status_from_data(data: Dict[str, Any]) -> Status:
    return Status(
        ok=bool(data["ok"]),
        error_msg=str(data.get("error_msg", "")),
    )


async def _read_frame(reader: asyncio.StreamReader, timeout: Optional[float]) -> Optional[bytes]:
    """Read one length-prefixed frame, or ``None`` on EOF.

    Servers pass ``timeout=None`` because persistent worker connections are
    idle between requests.
    """
    try:
        if timeout is None:
            header = await reader.readexactly(4)
        else:
            header = await asyncio.wait_for(reader.readexactly(4), timeout=timeout)
    except asyncio.IncompleteReadError:
        return None
    length = int.from_bytes(header, "big")
    try:
        if timeout is None:
            payload = await reader.readexactly(length)
        else:
            payload = await asyncio.wait_for(reader.readexactly(length), timeout=timeout)
    except asyncio.IncompleteReadError:
        return None
    return payload


async def _write_frame(writer: asyncio.StreamWriter, payload: bytes, timeout: float) -> None:
    writer.write(len(payload).to_bytes(4, "big") + payload)
    await asyncio.wait_for(writer.drain(), timeout=timeout)


__all__ = [
    "_key_from_data",
    "_key_to_data",
    "_read_frame",
    "_status_from_data",
    "_status_to_data",
    "_write_frame",
]
