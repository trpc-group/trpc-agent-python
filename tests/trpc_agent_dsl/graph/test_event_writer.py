# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for graph event writers."""

from google.genai.types import Content
from google.genai.types import Part
from trpc_agent_sdk.dsl.graph._constants import STREAM_KEY_ACK
from trpc_agent_sdk.dsl.graph._constants import STREAM_KEY_EVENT
from trpc_agent_sdk.dsl.graph._event_writer import AsyncEventWriter
from trpc_agent_sdk.dsl.graph._event_writer import EventWriter
from trpc_agent_sdk.dsl.graph._events import EventUtils
from trpc_agent_sdk.dsl.graph._events import ExecutionPhase
from trpc_agent_sdk.events import Event


class _AckingStreamWriter:
    """Captures stream payloads and resolves ack futures immediately."""

    def __init__(self):
        self.payloads: list[dict] = []

    def __call__(self, payload: dict) -> None:
        self.payloads.append(payload)
        ack = payload.get(STREAM_KEY_ACK)
        if ack is not None and not ack.done():
            ack.set_result(True)


class TestEventWriter:
    """Tests for synchronous event writer."""

    def test_write_text_applies_optional_context_fields(self):
        """Text events should carry request/parent/filter context."""
        payloads: list[dict] = []
        writer = EventWriter(
            writer=payloads.append,
            invocation_id="inv-1",
            author="node-a",
            branch="root.node-a",
            request_id="req-1",
            parent_invocation_id="parent-1",
            filter_key="root.node-a",
        )

        writer.write_text("hello", partial=False)

        assert len(payloads) == 1
        event = payloads[0][STREAM_KEY_EVENT]
        assert event.get_text() == "hello"
        assert event.partial is False
        assert event.request_id == "req-1"
        assert event.parent_invocation_id == "parent-1"
        assert event.filter_key == "root.node-a"

    def test_write_event_forwards_existing_event_instance(self):
        """Direct write_event should emit the exact event object."""
        payloads: list[dict] = []
        writer = EventWriter(
            writer=payloads.append,
            invocation_id="inv-1",
            author="node-a",
            branch="root.node-a",
        )
        event = Event(
            invocation_id="inv-1",
            author="node-a",
            branch="root.node-a",
            content=Content(role="model", parts=[Part.from_text(text="raw")]),
        )

        writer.write_event(event)

        assert payloads[0][STREAM_KEY_EVENT] is event

    def test_write_content_and_properties_expose_base_context(self):
        """write_content should emit content as-is and properties should be readable."""
        payloads: list[dict] = []
        writer = EventWriter(
            writer=payloads.append,
            invocation_id="inv-1",
            author="node-a",
            branch="root.node-a",
        )
        content = Content(role="model", parts=[Part.from_text(text="payload")])

        writer.write_content(content, partial=True)

        emitted = payloads[0][STREAM_KEY_EVENT]
        assert emitted.content == content
        assert emitted.partial is True
        assert writer.invocation_id == "inv-1"
        assert writer.author == "node-a"
        assert writer.branch == "root.node-a"
        assert writer.builder is not None


class TestAsyncEventWriter:
    """Tests for asynchronous event writer lifecycle behavior."""

    async def test_write_text_flushes_with_ack_future(self):
        """Async writes should include an ack future and wait for completion."""
        stream_writer = _AckingStreamWriter()
        writer = AsyncEventWriter(
            writer=stream_writer,
            invocation_id="inv-1",
            author="node-a",
            branch="root.node-a",
        )

        await writer.write_text("chunk", partial=True)

        assert len(stream_writer.payloads) == 1
        assert STREAM_KEY_ACK in stream_writer.payloads[0]
        assert stream_writer.payloads[0][STREAM_KEY_EVENT].partial is True

    async def test_node_lifecycle_events_reuse_cached_start_time(self):
        """Node complete events should include timing from prior node start."""
        stream_writer = _AckingStreamWriter()
        writer = AsyncEventWriter(
            writer=stream_writer,
            invocation_id="inv-1",
            author="node-a",
            branch="root.node-a",
        )

        await writer.write_node_start("node-a", step_number=3, input_keys=["messages"])
        await writer.write_node_complete("node-a", step_number=3, output_keys=["last_response"])

        complete_event = stream_writer.payloads[-1][STREAM_KEY_EVENT]
        metadata = EventUtils.get_node_metadata(complete_event)

        assert metadata is not None
        assert metadata.phase == ExecutionPhase.COMPLETE.value
        assert metadata.step_number == 3
        assert metadata.start_time is not None

    async def test_tool_complete_clears_cached_tool_start_time(self):
        """Tool completion should remove the tool start timestamp cache entry."""
        stream_writer = _AckingStreamWriter()
        writer = AsyncEventWriter(
            writer=stream_writer,
            invocation_id="inv-1",
            author="node-a",
            branch="root.node-a",
        )

        await writer.write_tool_start("calculator", "tool-1", input_args='{"x": 1}')
        assert "tool-1" in writer._tool_start_times

        await writer.write_tool_complete("calculator", "tool-1", output_result="42")

        assert "tool-1" not in writer._tool_start_times
        complete_event = stream_writer.payloads[-1][STREAM_KEY_EVENT]
        metadata = EventUtils.get_tool_metadata(complete_event)

        assert metadata is not None
        assert metadata.phase == ExecutionPhase.COMPLETE.value
        assert metadata.output_result == "42"

    async def test_write_node_error_and_model_events_emit_expected_metadata(self):
        """Error/model lifecycle APIs should emit graph execution events."""
        stream_writer = _AckingStreamWriter()
        writer = AsyncEventWriter(
            writer=stream_writer,
            invocation_id="inv-1",
            author="node-a",
            branch="root.node-a",
        )

        await writer.write_node_start("node-a")
        await writer.write_node_error("node-a", "failure")
        await writer.write_model_start("model-a", input_text="input text")
        await writer.write_model_complete("model-a", output_text="output text")

        node_error = stream_writer.payloads[1][STREAM_KEY_EVENT]
        model_start = stream_writer.payloads[2][STREAM_KEY_EVENT]
        model_complete = stream_writer.payloads[3][STREAM_KEY_EVENT]

        assert EventUtils.get_event_type(node_error) == "graph.node.error"
        assert EventUtils.get_model_metadata(model_start).phase == "start"
        assert EventUtils.get_model_metadata(model_complete).phase == "complete"
