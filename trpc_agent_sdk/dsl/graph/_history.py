# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Agent-node history inheritance policies."""

from typing import Literal

HistoryScope = Literal["none", "branch", "all"]

_HISTORY_SCOPES = frozenset({"none", "branch", "all"})


def resolve_history_scope(
    history_scope: HistoryScope | None,
    *,
    isolated_messages: bool,
) -> HistoryScope:
    """Resolve the public policy while preserving the legacy boolean default."""
    if history_scope is None:
        return "none" if isolated_messages else "all"
    if history_scope not in _HISTORY_SCOPES:
        raise ValueError("history_scope must be one of: none, branch, all")
    return history_scope
