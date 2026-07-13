# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

"""Backend capability declarations for replay consistency reports."""

from __future__ import annotations


BACKEND_CAPABILITIES = {
    "in_memory": {
        "session": True,
        "event": True,
        "state": True,
        "memory": True,
        "summary": True,
        "persistent": False,
        "external_service": False,
    },
    "sqlite": {
        "session": True,
        "event": True,
        "state": True,
        "memory": True,
        "summary": True,
        "persistent": True,
        "external_service": False,
    },
    "sql": {
        "session": True,
        "event": True,
        "state": True,
        "memory": True,
        "summary": True,
        "persistent": True,
        "external_service": True,
    },
    "redis": {
        "session": True,
        "event": True,
        "state": True,
        "memory": True,
        "summary": True,
        "persistent": True,
        "external_service": True,
    },
}


def capabilities_for(*backend_names: str) -> dict[str, dict[str, bool]]:
    """Return report-friendly capabilities for the requested backends."""

    return {name: BACKEND_CAPABILITIES[name] for name in backend_names}
