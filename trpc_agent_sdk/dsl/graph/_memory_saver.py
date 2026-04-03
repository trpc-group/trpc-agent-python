# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""LangGraph checkpointer backed by trpc-agent SessionService state.

This module provides a BaseCheckpointSaver implementation that stores
LangGraph checkpoints inside trpc-agent Session.state. Because Session.state is
persisted by SessionService backends (in-memory/redis/sql), checkpoint data can
survive process restarts when the selected backend persists session state.
"""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator
from collections.abc import Iterator
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from typing import Optional

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.base import ChannelVersions
from langgraph.checkpoint.base import Checkpoint
from langgraph.checkpoint.base import CheckpointMetadata
from langgraph.checkpoint.base import CheckpointTuple
from langgraph.checkpoint.base import WRITES_IDX_MAP
from langgraph.checkpoint.base import get_checkpoint_id
from langgraph.checkpoint.base import get_checkpoint_metadata

from ._constants import STATE_KEY_CHECKPOINTS
from ._constants import STATE_KEY_CHECKPOINT_BLOBS
from ._constants import STATE_KEY_CHECKPOINT_WRITES

_INTERNAL_CHECKPOINT_KEYS = frozenset({
    STATE_KEY_CHECKPOINTS,
    STATE_KEY_CHECKPOINT_WRITES,
    STATE_KEY_CHECKPOINT_BLOBS,
})


@dataclass(frozen=True)
class MemorySaverOption:
    """Configuration options for graph MemorySaver."""

    # Standalone mode option. In runner mode, persistence is via state_delta.
    auto_persist: bool = False
    # Persist intermediate writes immediately in standalone mode.
    persist_writes: bool = False


def has_graph_internal_checkpoint_state(state: dict[str, Any]) -> bool:
    """Check whether state already contains graph checkpoint storage."""
    if not state:
        return False
    return any(k in state for k in _INTERNAL_CHECKPOINT_KEYS)


def strip_graph_internal_checkpoint_state(state: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow copy of state without internal checkpoint storage keys."""
    if not state:
        return {}
    if not has_graph_internal_checkpoint_state(state):
        return dict(state)
    filtered = dict(state)
    for key in _INTERNAL_CHECKPOINT_KEYS:
        filtered.pop(key, None)
    return filtered


@dataclass
class _StorageContext:
    state: dict[str, Any]
    invocation_context: Optional[Any] = None
    session: Optional[Any] = None
    session_service: Optional[Any] = None


class MemorySaver(BaseCheckpointSaver[str]):
    """LangGraph checkpoint saver that stores data in Session.state."""

    def __init__(
        self,
        *,
        auto_persist: bool = False,
        persist_writes: bool = False,
        serde=None,
    ) -> None:
        super().__init__(serde=serde)
        self._auto_persist = auto_persist
        self._persist_writes = persist_writes
        # Fallback for direct usage without invocation_context/session_state.
        self._fallback_state: dict[str, Any] = {}
        self._thread_contexts: dict[str, _StorageContext] = {}

    def _get_storage_context(self, config: RunnableConfig | None) -> _StorageContext:
        configurable = (config or {}).get("configurable", {})

        # Preferred path: InvocationContext passed by GraphAgent.
        invocation_context = configurable.get("invocation_context")
        if invocation_context is not None:
            session = getattr(invocation_context, "session", None)
            session_service = getattr(invocation_context, "session_service", None)
            if session is not None and isinstance(getattr(session, "state", None), dict):
                storage = _StorageContext(
                    state=session.state,
                    invocation_context=invocation_context,
                    session=session,
                    session_service=session_service,
                )
                thread_id = configurable.get("thread_id")
                if isinstance(thread_id, str) and thread_id:
                    self._thread_contexts[thread_id] = storage
                return storage

        # Secondary path: direct config injection for testing/advanced usage.
        session_state = configurable.get("session_state")
        if isinstance(session_state, dict):
            storage = _StorageContext(
                state=session_state,
                invocation_context=None,
                session=configurable.get("session"),
                session_service=configurable.get("session_service"),
            )
            thread_id = configurable.get("thread_id")
            if isinstance(thread_id, str) and thread_id:
                self._thread_contexts[thread_id] = storage
            return storage

        return _StorageContext(state=self._fallback_state)

    async def _persist_if_needed(self, ctx: _StorageContext) -> None:
        # Primary persistence path is runner SessionService via event.state_delta.
        # Only use direct update_session for standalone scenarios without ctx.
        if ctx.invocation_context is not None:
            return
        if not self._auto_persist:
            return
        if ctx.session is None or ctx.session_service is None:
            return
        await ctx.session_service.update_session(ctx.session)

    @staticmethod
    def _mark_state_delta(ctx: _StorageContext, *root_keys: str) -> None:
        invocation_context = ctx.invocation_context
        if invocation_context is None:
            return
        state_proxy = getattr(invocation_context, "state", None)
        if state_proxy is None:
            return
        for key in root_keys:
            if key in ctx.state:
                state_proxy[key] = ctx.state[key]

    @staticmethod
    def _ensure_nested(root: dict[str, Any], *keys: str) -> dict[str, Any]:
        node = root
        for key in keys:
            child = node.get(key)
            if not isinstance(child, dict):
                child = {}
                node[key] = child
            node = child
        return node

    def _checkpoints_root(self, state: dict[str, Any]) -> dict[str, Any]:
        return self._ensure_nested(state, STATE_KEY_CHECKPOINTS)

    def _writes_root(self, state: dict[str, Any]) -> dict[str, Any]:
        return self._ensure_nested(state, STATE_KEY_CHECKPOINT_WRITES)

    def _blobs_root(self, state: dict[str, Any]) -> dict[str, Any]:
        return self._ensure_nested(state, STATE_KEY_CHECKPOINT_BLOBS)

    @staticmethod
    def _encode_typed(value: tuple[str, bytes]) -> dict[str, str]:
        return {
            "type": value[0],
            "data": base64.b64encode(value[1]).decode("ascii"),
        }

    @staticmethod
    def _decode_typed(value: Any) -> tuple[str, bytes]:
        if isinstance(value, tuple) and len(value) == 2 and isinstance(value[0], str) and isinstance(value[1], bytes):
            return value
        if not isinstance(value, dict):
            raise ValueError(f"Invalid typed payload: {type(value).__name__}")
        value_type = value.get("type")
        value_data = value.get("data")
        if not isinstance(value_type, str) or not isinstance(value_data, str):
            raise ValueError("Invalid typed payload fields")
        return (value_type, base64.b64decode(value_data.encode("ascii")))

    def _load_blobs(
        self,
        state: dict[str, Any],
        thread_id: str,
        checkpoint_ns: str,
        versions: ChannelVersions,
    ) -> dict[str, Any]:
        channel_values: dict[str, Any] = {}
        blobs_by_ns = self._blobs_root(state).get(thread_id, {}).get(checkpoint_ns, {})
        for channel, version in versions.items():
            encoded_value = blobs_by_ns.get(channel, {}).get(str(version))
            if not encoded_value:
                continue
            typed = self._decode_typed(encoded_value)
            if typed[0] == "empty":
                continue
            channel_values[channel] = self.serde.loads_typed(typed)
        return channel_values

    def _get_pending_writes(
        self,
        state: dict[str, Any],
        thread_id: str,
        checkpoint_ns: str,
        checkpoint_id: str,
    ) -> list[tuple[str, str, Any]]:
        writes_by_task = self._writes_root(state).get(thread_id, {}).get(checkpoint_ns, {}).get(checkpoint_id, {})
        pending: list[tuple[str, str, Any]] = []
        for task_id, writes_for_task in writes_by_task.items():
            if not isinstance(writes_for_task, dict):
                continue
            for _, write_item in writes_for_task.items():
                if not isinstance(write_item, dict):
                    continue
                channel = write_item.get("channel")
                encoded_value = write_item.get("value")
                if not isinstance(channel, str) or encoded_value is None:
                    continue
                pending.append((task_id, channel, self.serde.loads_typed(self._decode_typed(encoded_value))))
        return pending

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        storage_ctx = self._get_storage_context(config)
        state = storage_ctx.state
        configurable = config["configurable"]
        thread_id: str = configurable["thread_id"]
        checkpoint_ns: str = configurable.get("checkpoint_ns", "")

        checkpoints_by_ns = self._checkpoints_root(state).get(thread_id, {}).get(checkpoint_ns, {})
        if not checkpoints_by_ns:
            return None

        checkpoint_id = get_checkpoint_id(config)
        checkpoint_entry = None
        resolved_checkpoint_id = checkpoint_id
        if checkpoint_id:
            checkpoint_entry = checkpoints_by_ns.get(checkpoint_id)
        else:
            resolved_checkpoint_id = max(checkpoints_by_ns.keys())
            checkpoint_entry = checkpoints_by_ns.get(resolved_checkpoint_id)

        if not checkpoint_entry or resolved_checkpoint_id is None:
            return None

        checkpoint_base: Checkpoint = self.serde.loads_typed(self._decode_typed(checkpoint_entry["checkpoint"]))
        checkpoint: Checkpoint = {
            **checkpoint_base,
            "channel_values":
            self._load_blobs(
                state,
                thread_id,
                checkpoint_ns,
                checkpoint_base["channel_versions"],
            ),
        }
        metadata: CheckpointMetadata = self.serde.loads_typed(self._decode_typed(checkpoint_entry["metadata"]))
        parent_checkpoint_id = checkpoint_entry.get("parent_checkpoint_id")
        parent_config = None
        if parent_checkpoint_id:
            parent_config = {
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": parent_checkpoint_id,
                }
            }

        result_config = config
        if not checkpoint_id:
            result_config = {
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": resolved_checkpoint_id,
                }
            }

        return CheckpointTuple(
            config=result_config,
            checkpoint=checkpoint,
            metadata=metadata,
            parent_config=parent_config,
            pending_writes=self._get_pending_writes(state, thread_id, checkpoint_ns, resolved_checkpoint_id),
        )

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        storage_ctx = self._get_storage_context(config)
        state = storage_ctx.state
        checkpoints_root = self._checkpoints_root(state)

        config_thread_id = config["configurable"]["thread_id"] if config else None
        config_checkpoint_ns = config["configurable"].get("checkpoint_ns") if config else None
        config_checkpoint_id = get_checkpoint_id(config) if config else None
        before_checkpoint_id = get_checkpoint_id(before) if before else None

        thread_ids = [config_thread_id] if config_thread_id else list(checkpoints_root.keys())
        remaining = limit

        for thread_id in thread_ids:
            checkpoints_by_thread = checkpoints_root.get(thread_id, {})
            for checkpoint_ns, checkpoints_by_id in checkpoints_by_thread.items():
                if config_checkpoint_ns is not None and checkpoint_ns != config_checkpoint_ns:
                    continue

                for checkpoint_id in sorted(checkpoints_by_id.keys(), reverse=True):
                    if config_checkpoint_id and checkpoint_id != config_checkpoint_id:
                        continue
                    if before_checkpoint_id and checkpoint_id >= before_checkpoint_id:
                        continue

                    checkpoint_entry = checkpoints_by_id[checkpoint_id]
                    metadata: CheckpointMetadata = self.serde.loads_typed(
                        self._decode_typed(checkpoint_entry["metadata"]))
                    if filter and not all(metadata.get(k) == v for k, v in filter.items()):
                        continue

                    if remaining is not None:
                        if remaining <= 0:
                            return
                        remaining -= 1

                    checkpoint_base: Checkpoint = self.serde.loads_typed(
                        self._decode_typed(checkpoint_entry["checkpoint"]))
                    checkpoint: Checkpoint = {
                        **checkpoint_base,
                        "channel_values":
                        self._load_blobs(
                            state,
                            thread_id,
                            checkpoint_ns,
                            checkpoint_base["channel_versions"],
                        ),
                    }

                    parent_checkpoint_id = checkpoint_entry.get("parent_checkpoint_id")
                    parent_config = None
                    if parent_checkpoint_id:
                        parent_config = {
                            "configurable": {
                                "thread_id": thread_id,
                                "checkpoint_ns": checkpoint_ns,
                                "checkpoint_id": parent_checkpoint_id,
                            }
                        }

                    yield CheckpointTuple(
                        config={
                            "configurable": {
                                "thread_id": thread_id,
                                "checkpoint_ns": checkpoint_ns,
                                "checkpoint_id": checkpoint_id,
                            }
                        },
                        checkpoint=checkpoint,
                        metadata=metadata,
                        parent_config=parent_config,
                        pending_writes=self._get_pending_writes(state, thread_id, checkpoint_ns, checkpoint_id),
                    )

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        storage_ctx = self._get_storage_context(config)
        state = storage_ctx.state
        configurable = config["configurable"]
        thread_id: str = configurable["thread_id"]
        checkpoint_ns: str = configurable.get("checkpoint_ns", "")

        checkpoint_copy = checkpoint.copy()
        channel_values = checkpoint_copy.pop("channel_values", {})

        blobs_by_ns = self._ensure_nested(self._blobs_root(state), thread_id, checkpoint_ns)
        for channel, version in new_versions.items():
            encoded = (self._encode_typed(self.serde.dumps_typed(channel_values[channel]))
                       if channel in channel_values else self._encode_typed(("empty", b"")))
            self._ensure_nested(blobs_by_ns, channel)[str(version)] = encoded

        checkpoints_by_ns = self._ensure_nested(self._checkpoints_root(state), thread_id, checkpoint_ns)
        checkpoints_by_ns[checkpoint["id"]] = {
            "checkpoint": self._encode_typed(self.serde.dumps_typed(checkpoint_copy)),
            "metadata": self._encode_typed(self.serde.dumps_typed(get_checkpoint_metadata(config, metadata))),
            "parent_checkpoint_id": configurable.get("checkpoint_id"),
        }
        self._mark_state_delta(
            storage_ctx,
            STATE_KEY_CHECKPOINTS,
            STATE_KEY_CHECKPOINT_BLOBS,
        )

        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint["id"],
            }
        }

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        storage_ctx = self._get_storage_context(config)
        state = storage_ctx.state
        configurable = config["configurable"]
        thread_id: str = configurable["thread_id"]
        checkpoint_ns: str = configurable.get("checkpoint_ns", "")
        checkpoint_id: str = configurable["checkpoint_id"]

        writes_by_checkpoint = self._ensure_nested(self._writes_root(state), thread_id, checkpoint_ns, checkpoint_id)
        writes_by_task = writes_by_checkpoint.get(task_id)
        if not isinstance(writes_by_task, dict):
            writes_by_task = {}
            writes_by_checkpoint[task_id] = writes_by_task

        for idx, (channel, value) in enumerate(writes):
            write_idx = WRITES_IDX_MAP.get(channel, idx)
            write_idx_key = str(write_idx)
            if write_idx >= 0 and write_idx_key in writes_by_task:
                continue
            writes_by_task[write_idx_key] = {
                "channel": channel,
                "value": self._encode_typed(self.serde.dumps_typed(value)),
                "task_path": task_path,
            }
        self._mark_state_delta(storage_ctx, STATE_KEY_CHECKPOINT_WRITES)

    def delete_thread(self, thread_id: str) -> None:
        storage_ctx = self._thread_contexts.pop(thread_id, None)
        state = storage_ctx.state if storage_ctx is not None else self._fallback_state
        checkpoints_root = self._checkpoints_root(state)
        writes_root = self._writes_root(state)
        blobs_root = self._blobs_root(state)
        checkpoints_root.pop(thread_id, None)
        writes_root.pop(thread_id, None)
        blobs_root.pop(thread_id, None)
        if storage_ctx is not None:
            self._mark_state_delta(
                storage_ctx,
                STATE_KEY_CHECKPOINTS,
                STATE_KEY_CHECKPOINT_WRITES,
                STATE_KEY_CHECKPOINT_BLOBS,
            )

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        return self.get_tuple(config)

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        for item in self.list(config, filter=filter, before=before, limit=limit):
            yield item

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        next_config = self.put(config, checkpoint, metadata, new_versions)
        await self._persist_if_needed(self._get_storage_context(config))
        return next_config

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        self.put_writes(config, writes, task_id, task_path)
        if self._persist_writes:
            await self._persist_if_needed(self._get_storage_context(config))

    async def adelete_thread(self, thread_id: str) -> None:
        storage_ctx = self._thread_contexts.get(thread_id)
        self.delete_thread(thread_id)
        if storage_ctx is not None:
            await self._persist_if_needed(storage_ctx)
