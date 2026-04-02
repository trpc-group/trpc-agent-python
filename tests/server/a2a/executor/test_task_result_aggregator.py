# -*- coding: utf-8 -*-
"""Unit tests for trpc_agent_sdk.server.a2a.executor._task_result_aggregator."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from a2a.types import Message, Role, TaskState, TaskStatus, TaskStatusUpdateEvent, TextPart

from trpc_agent_sdk.server.a2a.executor._task_result_aggregator import TaskResultAggregator


def _make_status_event(state: TaskState, text: str = "msg") -> TaskStatusUpdateEvent:
    return TaskStatusUpdateEvent(
        task_id="t1",
        context_id="ctx1",
        final=False,
        status=TaskStatus(
            state=state,
            message=Message(
                message_id="m1",
                role=Role.agent,
                parts=[TextPart(text=text)],
            ),
        ),
    )


class TestTaskResultAggregatorInit:
    def test_initial_state_is_working(self):
        agg = TaskResultAggregator()
        assert agg.task_state == TaskState.working

    def test_initial_message_is_none(self):
        agg = TaskResultAggregator()
        assert agg.task_status_message is None


class TestProcessEventWorking:
    def test_working_event_updates_message(self):
        agg = TaskResultAggregator()
        evt = _make_status_event(TaskState.working, "working msg")
        agg.process_event(evt)
        assert agg.task_state == TaskState.working
        assert agg.task_status_message.parts[0].root.text == "working msg"

    def test_working_event_state_is_rewritten(self):
        agg = TaskResultAggregator()
        evt = _make_status_event(TaskState.working)
        agg.process_event(evt)
        assert evt.status.state == TaskState.working


class TestProcessEventFailed:
    def test_failed_sets_state(self):
        agg = TaskResultAggregator()
        evt = _make_status_event(TaskState.failed, "error")
        agg.process_event(evt)
        assert agg.task_state == TaskState.failed
        assert agg.task_status_message.parts[0].root.text == "error"

    def test_failed_is_highest_priority(self):
        agg = TaskResultAggregator()
        agg.process_event(_make_status_event(TaskState.auth_required, "auth"))
        agg.process_event(_make_status_event(TaskState.failed, "fail"))
        assert agg.task_state == TaskState.failed
        assert agg.task_status_message.parts[0].root.text == "fail"

    def test_failed_not_overwritten_by_auth_required(self):
        agg = TaskResultAggregator()
        agg.process_event(_make_status_event(TaskState.failed, "fail"))
        agg.process_event(_make_status_event(TaskState.auth_required, "auth"))
        assert agg.task_state == TaskState.failed
        assert agg.task_status_message.parts[0].root.text == "fail"

    def test_failed_not_overwritten_by_input_required(self):
        agg = TaskResultAggregator()
        agg.process_event(_make_status_event(TaskState.failed, "fail"))
        agg.process_event(_make_status_event(TaskState.input_required, "input"))
        assert agg.task_state == TaskState.failed

    def test_failed_not_overwritten_by_working(self):
        agg = TaskResultAggregator()
        agg.process_event(_make_status_event(TaskState.failed, "fail"))
        agg.process_event(_make_status_event(TaskState.working, "work"))
        assert agg.task_state == TaskState.failed
        assert agg.task_status_message.parts[0].root.text == "fail"

    def test_event_state_rewritten_to_working(self):
        agg = TaskResultAggregator()
        evt = _make_status_event(TaskState.failed)
        agg.process_event(evt)
        assert evt.status.state == TaskState.working


class TestProcessEventAuthRequired:
    def test_auth_required_sets_state(self):
        agg = TaskResultAggregator()
        agg.process_event(_make_status_event(TaskState.auth_required, "auth"))
        assert agg.task_state == TaskState.auth_required

    def test_auth_required_not_overwritten_by_input_required(self):
        agg = TaskResultAggregator()
        agg.process_event(_make_status_event(TaskState.auth_required, "auth"))
        agg.process_event(_make_status_event(TaskState.input_required, "input"))
        assert agg.task_state == TaskState.auth_required


class TestProcessEventInputRequired:
    def test_input_required_sets_state(self):
        agg = TaskResultAggregator()
        agg.process_event(_make_status_event(TaskState.input_required, "input"))
        assert agg.task_state == TaskState.input_required

    def test_input_required_overridden_by_failed(self):
        agg = TaskResultAggregator()
        agg.process_event(_make_status_event(TaskState.input_required, "input"))
        agg.process_event(_make_status_event(TaskState.failed, "fail"))
        assert agg.task_state == TaskState.failed


class TestProcessEventNonStatusUpdate:
    def test_non_status_event_is_ignored(self):
        agg = TaskResultAggregator()
        agg.process_event(MagicMock())
        assert agg.task_state == TaskState.working
        assert agg.task_status_message is None


class TestProcessEventSequence:
    def test_multiple_working_events_keep_last_message(self):
        agg = TaskResultAggregator()
        agg.process_event(_make_status_event(TaskState.working, "first"))
        agg.process_event(_make_status_event(TaskState.working, "second"))
        assert agg.task_status_message.parts[0].root.text == "second"

    def test_working_after_failed_does_not_update_message(self):
        agg = TaskResultAggregator()
        agg.process_event(_make_status_event(TaskState.failed, "error"))
        agg.process_event(_make_status_event(TaskState.working, "work"))
        assert agg.task_status_message.parts[0].root.text == "error"
