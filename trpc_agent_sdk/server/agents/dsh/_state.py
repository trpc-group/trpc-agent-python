# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Per-turn DSH session state and JSONL operations."""

from __future__ import annotations

import json
from dataclasses import dataclass
from dataclasses import field
from typing import Any

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.events import Event

from ._protocol import DSH_SESSION_FORMAT_VERSION
from ._protocol import DSH_SESSION_STATE_KEY
from ._protocol import DSHRunInput
from ._protocol import jsonl_version


def _compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _event_count(jsonl: str) -> int:
    """Count events after the header line; an empty snapshot has no events."""

    return max(0, len([line for line in jsonl.splitlines() if line.strip()]) - 1)


@dataclass
class DSHState:
    """One turn's cursor over a DSH JSONL snapshot.

    Create one object per turn with :meth:`from_session` or
    :meth:`from_jsonl`, mutate it through :meth:`sync_run_input` /
    :meth:`append_event` / :meth:`replace`, then persist :meth:`to_session`.
    The object is intentionally not shared across turns; the cross-turn
    snapshot lives in the tRPC ``Session.state``.
    """

    # Full DSH JSONL snapshot; empty for a session that has not been created.
    jsonl: str = ""

    # DSH session format version taken from the JSONL header.
    version: int = DSH_SESSION_FORMAT_VERSION

    # Number of events after the header. Derived cursor, not persisted.
    event_count: int = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.event_count = _event_count(self.jsonl)

    @classmethod
    def from_session(cls, session: Any) -> "DSHState":
        """Load the snapshot stored in ``Session.state``, or start empty."""

        raw = session.state.get(DSH_SESSION_STATE_KEY)
        if not isinstance(raw, dict) or not isinstance(raw.get("jsonl"), str):
            return cls()
        return cls(
            jsonl=raw["jsonl"],
            version=int(raw.get("version", DSH_SESSION_FORMAT_VERSION)),
        )

    @classmethod
    def from_jsonl(cls, jsonl: str, version: int) -> "DSHState":
        """Create a state cursor from a caller-provided snapshot."""

        return cls(jsonl=jsonl, version=version)

    def sync_run_input(self, run_input: DSHRunInput) -> None:
        """Make the run input and this state point at the same snapshot.

        The callback may replace ``run_input.jsonl``. When it does, the new
        JSONL header is authoritative and the state follows it; when it leaves
        ``jsonl`` as ``None``, the state fills the default snapshot.
        """

        if run_input.jsonl is None:
            run_input.jsonl = self.jsonl
            run_input.version = self.version
            return

        if run_input.jsonl != self.jsonl:
            self.replace(run_input.jsonl, jsonl_version(run_input.jsonl))
        run_input.version = self.version

    def append_event(self, event: dict[str, Any]) -> None:
        """Append one ``session.event``; its ``seq`` must be contiguous."""

        if not self.jsonl.strip():
            raise ValueError(
                "DSH session snapshot is empty; the runtime must return the created "
                "session snapshot before events are appended")
        if not isinstance(event, dict):
            raise TypeError("DSH session event must be a JSON object")
        seq = event.get("seq")
        if not isinstance(seq, int):
            raise ValueError("DSH session event is missing an integer seq")
        if seq < self.event_count:
            return
        if seq != self.event_count:
            raise ValueError(
                f"DSH session event seq {seq} is not contiguous; expected {self.event_count}")
        self.jsonl += _compact(event) + "\n"
        self.event_count += 1

    def replace(self, jsonl: str, version: int) -> None:
        """Replace the local snapshot with the runtime's authoritative JSONL."""

        self.jsonl = jsonl
        self.version = version
        self.event_count = _event_count(jsonl)

    def to_session(self, ctx: InvocationContext, *, author: str) -> Event:
        """Persist the snapshot and return the invisible state-commit event."""

        ctx.state[DSH_SESSION_STATE_KEY] = {
            "jsonl": self.jsonl,
            "version": self.version,
        }
        return Event(
            invocation_id=ctx.invocation_id,
            author=author,
            branch=ctx.branch,
            actions=ctx.actions,
            visible=False,
            partial=False,
        )
