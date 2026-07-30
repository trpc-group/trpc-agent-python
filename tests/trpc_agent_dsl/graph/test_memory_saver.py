# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Public-API tests for graph memory saver."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langgraph.checkpoint.base import empty_checkpoint
from trpc_agent_sdk.dsl.graph._constants import STATE_KEY_CHECKPOINTS
from trpc_agent_sdk.dsl.graph._constants import STATE_KEY_CHECKPOINT_BLOBS
from trpc_agent_sdk.dsl.graph._constants import STATE_KEY_CHECKPOINT_WRITES
from trpc_agent_sdk.dsl.graph._memory_saver import MemorySaver
from trpc_agent_sdk.dsl.graph._memory_saver import has_graph_internal_checkpoint_state
from trpc_agent_sdk.dsl.graph._memory_saver import strip_graph_internal_checkpoint_state


class TestMemorySaverHelpers:
    """Tests for exported checkpoint-state helper functions."""

    def test_has_and_strip_graph_internal_checkpoint_state(self):
        """Strip helper should remove only graph internal checkpoint keys."""
        state = {
            "visible": "keep",
            STATE_KEY_CHECKPOINTS: {
                "t": {}
            },
            STATE_KEY_CHECKPOINT_BLOBS: {
                "t": {}
            },
        }

        stripped = strip_graph_internal_checkpoint_state(state)

        assert has_graph_internal_checkpoint_state(state) is True
        assert stripped == {"visible": "keep"}
        assert STATE_KEY_CHECKPOINTS in state


class TestMemorySaverStorage:
    """Tests for MemorySaver public put/get/list/write/delete behavior."""

    def test_put_and_get_tuple_round_trip_channel_values(self):
        """Checkpoint put/get should preserve serialized channel values."""
        saver = MemorySaver()
        config = {
            "configurable": {
                "thread_id": "thread-1",
                "checkpoint_ns": "graph-a",
            }
        }
        checkpoint = empty_checkpoint()
        checkpoint["channel_values"] = {"answer": "42"}
        checkpoint["channel_versions"] = {"answer": 1}

        next_config = saver.put(
            config,
            checkpoint,
            metadata={
                "source": "loop",
                "step": 1
            },
            new_versions={"answer": 1},
        )
        restored = saver.get_tuple(next_config)

        assert restored is not None
        assert restored.config["configurable"]["checkpoint_id"] == checkpoint["id"]
        assert restored.checkpoint["channel_values"]["answer"] == "42"
        assert restored.metadata["step"] == 1

    def test_put_writes_skips_duplicate_write_index_for_same_task(self):
        """Repeated writes with same computed index should keep first value only."""
        saver = MemorySaver()
        base_config = {
            "configurable": {
                "thread_id": "thread-1",
                "checkpoint_ns": "graph-a",
            }
        }
        checkpoint = empty_checkpoint()
        checkpoint["channel_values"] = {"answer": "42"}
        checkpoint["channel_versions"] = {"answer": 1}
        checkpoint_config = saver.put(
            base_config,
            checkpoint,
            metadata={
                "source": "loop",
                "step": 1
            },
            new_versions={"answer": 1},
        )

        saver.put_writes(checkpoint_config, [("alpha", 1)], task_id="task-1")
        saver.put_writes(checkpoint_config, [("alpha", 2)], task_id="task-1")
        restored = saver.get_tuple(checkpoint_config)

        assert restored is not None
        assert restored.pending_writes == [("task-1", "alpha", 1)]

    def test_put_uses_invocation_context_session_state_storage(self):
        """InvocationContext session.state should be used as the storage backend."""
        saver = MemorySaver()
        invocation_context = SimpleNamespace(
            session=SimpleNamespace(state={}),
            session_service=object(),
            state={},
        )
        config = {
            "configurable": {
                "thread_id": "thread-ctx",
                "checkpoint_ns": "graph-a",
                "invocation_context": invocation_context,
            }
        }
        checkpoint = empty_checkpoint()
        checkpoint["channel_values"] = {"answer": "42"}
        checkpoint["channel_versions"] = {"answer": 1}

        saver.put(config, checkpoint, metadata={"step": 1}, new_versions={"answer": 1})

        assert has_graph_internal_checkpoint_state(invocation_context.session.state) is True

    def test_put_supports_direct_session_state_injection(self):
        """session_state config should be accepted as an alternate storage backend."""
        saver = MemorySaver()
        session_state: dict[str, object] = {}
        config = {
            "configurable": {
                "thread_id": "thread-injected",
                "checkpoint_ns": "graph-a",
                "session_state": session_state,
                "session": "session-obj",
                "session_service": "service-obj",
            }
        }
        checkpoint = empty_checkpoint()
        checkpoint["channel_values"] = {"answer": "42"}
        checkpoint["channel_versions"] = {"answer": 1}

        saver.put(config, checkpoint, metadata={"step": 1}, new_versions={"answer": 1})

        assert has_graph_internal_checkpoint_state(session_state) is True

    def test_get_tuple_returns_none_for_missing_or_unknown_checkpoint(self):
        """Tuple lookup should fail cleanly when no matching checkpoint exists."""
        saver = MemorySaver()
        config = {"configurable": {"thread_id": "t1", "checkpoint_ns": "ns"}}

        assert saver.get_tuple(config) is None

        checkpoint = empty_checkpoint()
        checkpoint["id"] = "cp-known"
        saver.put(config, checkpoint, metadata={"step": 1}, new_versions={})

        missing_config = {"configurable": {"thread_id": "t1", "checkpoint_ns": "ns", "checkpoint_id": "cp-missing"}}
        assert saver.get_tuple(missing_config) is None

    def test_list_supports_filter_before_and_limit(self):
        """Checkpoint listing should honor metadata filter, before, and limit options."""
        saver = MemorySaver()
        config = {"configurable": {"thread_id": "t1", "checkpoint_ns": "ns"}}

        cp1 = empty_checkpoint()
        cp1["id"] = "001"
        cp2 = empty_checkpoint()
        cp2["id"] = "002"
        cp3 = empty_checkpoint()
        cp3["id"] = "003"

        config1 = saver.put(config, cp1, metadata={"step": 1, "kind": "a"}, new_versions={})
        saver.put(config1, cp2, metadata={"step": 2, "kind": "b"}, new_versions={})
        saver.put(config1, cp3, metadata={"step": 3, "kind": "b"}, new_versions={})

        filtered = list(saver.list(config, filter={"kind": "b"}))
        assert [item.metadata["step"] for item in filtered] == [3, 2]

        before = {"configurable": {"thread_id": "t1", "checkpoint_ns": "ns", "checkpoint_id": "003"}}
        before_items = list(saver.list(config, before=before))
        assert all(item.config["configurable"]["checkpoint_id"] != "003" for item in before_items)

        limited = list(saver.list(config, limit=1))
        assert len(limited) == 1

    def test_delete_thread_removes_storage_for_known_and_unknown_contexts(self):
        """Deleting thread should clear stored data regardless of context source."""
        saver = MemorySaver()
        config = {"configurable": {"thread_id": "thread-delete", "checkpoint_ns": "ns"}}
        checkpoint = empty_checkpoint()
        checkpoint["id"] = "cp-1"
        saver.put(config, checkpoint, metadata={"step": 1}, new_versions={})

        saver.delete_thread("thread-delete")
        assert saver.get_tuple({"configurable": {"thread_id": "thread-delete", "checkpoint_ns": "ns"}}) is None

        # Unknown thread id should not raise.
        saver.delete_thread("unknown-thread")

    def test_put_handles_empty_channel_blob_without_materializing_value(self):
        """Channels persisted as empty markers should not materialize during get_tuple."""
        saver = MemorySaver()
        config = {"configurable": {"thread_id": "t-empty", "checkpoint_ns": "ns"}}
        checkpoint = empty_checkpoint()
        checkpoint["id"] = "cp-empty"
        checkpoint["channel_values"] = {}
        checkpoint["channel_versions"] = {"missing": 1}

        next_config = saver.put(
            config,
            checkpoint,
            metadata={"step": 1},
            new_versions={"missing": 1},
        )
        restored = saver.get_tuple(next_config)

        assert restored is not None
        assert restored.checkpoint["channel_values"] == {}


class TestMemorySaverAsync:
    """Tests for asynchronous public APIs."""

    async def test_async_methods_persist_when_auto_persist_and_persist_writes_enabled(self):
        """aput/aput_writes/adelete_thread should persist in standalone mode when enabled."""
        saver = MemorySaver(auto_persist=True, persist_writes=True)
        session_service = SimpleNamespace(update_session=AsyncMock())
        session = SimpleNamespace(state={}, id="session-1")
        config = {
            "configurable": {
                "thread_id": "thread-async",
                "checkpoint_ns": "ns",
                "session_state": session.state,
                "session": session,
                "session_service": session_service,
            }
        }
        checkpoint = empty_checkpoint()
        checkpoint["id"] = "cp-async"

        next_config = await saver.aput(config, checkpoint, metadata={"step": 1}, new_versions={})
        writes_config = {
            "configurable": {
                **config["configurable"],
                "checkpoint_id": next_config["configurable"]["checkpoint_id"],
            }
        }
        await saver.aput_writes(writes_config, writes=[("x", 1)], task_id="task-1")
        await saver.adelete_thread("thread-async")

        assert session_service.update_session.await_count == 3

    async def test_async_methods_skip_persist_when_auto_persist_disabled(self):
        """auto_persist=False should disable persistence even for async write APIs."""
        saver = MemorySaver(auto_persist=False, persist_writes=True)
        session_service = SimpleNamespace(update_session=AsyncMock())
        session = SimpleNamespace(state={}, id="session-1")
        config = {
            "configurable": {
                "thread_id": "thread-disabled",
                "checkpoint_ns": "ns",
                "session_state": session.state,
                "session": session,
                "session_service": session_service,
            }
        }
        checkpoint = empty_checkpoint()
        checkpoint["id"] = "cp-disabled"

        next_config = await saver.aput(config, checkpoint, metadata={"step": 1}, new_versions={})
        writes_config = {
            "configurable": {
                **config["configurable"],
                "checkpoint_id": next_config["configurable"]["checkpoint_id"],
            }
        }
        await saver.aput_writes(writes_config, writes=[("x", 1)], task_id="task-1")
        await saver.adelete_thread("thread-disabled")

        assert session_service.update_session.await_count == 0

    async def test_async_methods_skip_standalone_persist_with_invocation_context(self):
        """When invocation_context is present, persistence should be skipped by MemorySaver."""
        saver = MemorySaver(auto_persist=True, persist_writes=True)
        session_service = SimpleNamespace(update_session=AsyncMock())
        invocation_context = SimpleNamespace(
            session=SimpleNamespace(state={}, id="session-1"),
            session_service=session_service,
            state={},
        )
        config = {
            "configurable": {
                "thread_id": "thread-ctx",
                "checkpoint_ns": "ns",
                "invocation_context": invocation_context,
            }
        }
        checkpoint = empty_checkpoint()
        checkpoint["id"] = "cp-ctx"

        next_config = await saver.aput(config, checkpoint, metadata={"step": 1}, new_versions={})
        writes_config = {
            "configurable": {
                **config["configurable"],
                "checkpoint_id": next_config["configurable"]["checkpoint_id"],
            }
        }
        await saver.aput_writes(writes_config, writes=[("x", 1)], task_id="task-1")
        await saver.adelete_thread("thread-ctx")

        assert session_service.update_session.await_count == 0
