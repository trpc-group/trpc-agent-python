# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.server.a2a_v1.executor._task_result_aggregator."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from a2a.types import Message, Part, Role, TaskState, TaskStatus, TaskStatusUpdateEvent

from trpc_agent_sdk.server.a2a_v1.executor._task_result_aggregator import TaskResultAggregator


def _make_status_event(state: TaskState, text: str = "msg") -> TaskStatusUpdateEvent:
    return TaskStatusUpdateEvent(
        task_id="t1",
        context_id="ctx1",
        status=TaskStatus(
            state=state,
            message=Message(
                message_id="m1",
                role=Role.ROLE_AGENT,
                parts=[Part(text=text)],
            ),
        ),
    )


class TestTaskResultAggregatorInit:
    def test_initial_state_is_working(self):
        agg = TaskResultAggregator()
        assert agg.task_state == TaskState.TASK_STATE_WORKING

    def test_initial_message_is_none(self):
        agg = TaskResultAggregator()
        assert agg.task_status_message is None


class TestProcessEventWorking:
    def test_working_event_updates_message(self):
        agg = TaskResultAggregator()
        evt = _make_status_event(TaskState.TASK_STATE_WORKING, "working msg")
        agg.process_event(evt)
        assert agg.task_state == TaskState.TASK_STATE_WORKING
        assert agg.task_status_message.parts[0].text == "working msg"

    def test_working_event_state_not_rewritten(self):
        # 1.x events are shared protobuf messages; the aggregator observes but
        # does not mutate the event state.
        agg = TaskResultAggregator()
        evt = _make_status_event(TaskState.TASK_STATE_WORKING)
        agg.process_event(evt)
        assert evt.status.state == TaskState.TASK_STATE_WORKING


class TestProcessEventFailed:
    def test_failed_sets_state(self):
        agg = TaskResultAggregator()
        evt = _make_status_event(TaskState.TASK_STATE_FAILED, "error")
        agg.process_event(evt)
        assert agg.task_state == TaskState.TASK_STATE_FAILED
        assert agg.task_status_message.parts[0].text == "error"

    def test_failed_is_highest_priority(self):
        agg = TaskResultAggregator()
        agg.process_event(_make_status_event(TaskState.TASK_STATE_AUTH_REQUIRED, "auth"))
        agg.process_event(_make_status_event(TaskState.TASK_STATE_FAILED, "fail"))
        assert agg.task_state == TaskState.TASK_STATE_FAILED
        assert agg.task_status_message.parts[0].text == "fail"

    def test_failed_not_overwritten_by_auth_required(self):
        agg = TaskResultAggregator()
        agg.process_event(_make_status_event(TaskState.TASK_STATE_FAILED, "fail"))
        agg.process_event(_make_status_event(TaskState.TASK_STATE_AUTH_REQUIRED, "auth"))
        assert agg.task_state == TaskState.TASK_STATE_FAILED
        assert agg.task_status_message.parts[0].text == "fail"

    def test_failed_not_overwritten_by_input_required(self):
        agg = TaskResultAggregator()
        agg.process_event(_make_status_event(TaskState.TASK_STATE_FAILED, "fail"))
        agg.process_event(_make_status_event(TaskState.TASK_STATE_INPUT_REQUIRED, "input"))
        assert agg.task_state == TaskState.TASK_STATE_FAILED

    def test_failed_not_overwritten_by_working(self):
        agg = TaskResultAggregator()
        agg.process_event(_make_status_event(TaskState.TASK_STATE_FAILED, "fail"))
        agg.process_event(_make_status_event(TaskState.TASK_STATE_WORKING, "work"))
        assert agg.task_state == TaskState.TASK_STATE_FAILED
        assert agg.task_status_message.parts[0].text == "fail"

    def test_event_state_not_rewritten(self):
        # 1.x events are shared protobuf messages; the aggregator does not
        # rewrite the event's state.
        agg = TaskResultAggregator()
        evt = _make_status_event(TaskState.TASK_STATE_FAILED)
        agg.process_event(evt)
        assert evt.status.state == TaskState.TASK_STATE_FAILED


class TestProcessEventAuthRequired:
    def test_auth_required_sets_state(self):
        agg = TaskResultAggregator()
        agg.process_event(_make_status_event(TaskState.TASK_STATE_AUTH_REQUIRED, "auth"))
        assert agg.task_state == TaskState.TASK_STATE_AUTH_REQUIRED

    def test_auth_required_not_overwritten_by_input_required(self):
        agg = TaskResultAggregator()
        agg.process_event(_make_status_event(TaskState.TASK_STATE_AUTH_REQUIRED, "auth"))
        agg.process_event(_make_status_event(TaskState.TASK_STATE_INPUT_REQUIRED, "input"))
        assert agg.task_state == TaskState.TASK_STATE_AUTH_REQUIRED


class TestProcessEventInputRequired:
    def test_input_required_sets_state(self):
        agg = TaskResultAggregator()
        agg.process_event(_make_status_event(TaskState.TASK_STATE_INPUT_REQUIRED, "input"))
        assert agg.task_state == TaskState.TASK_STATE_INPUT_REQUIRED

    def test_input_required_overridden_by_failed(self):
        agg = TaskResultAggregator()
        agg.process_event(_make_status_event(TaskState.TASK_STATE_INPUT_REQUIRED, "input"))
        agg.process_event(_make_status_event(TaskState.TASK_STATE_FAILED, "fail"))
        assert agg.task_state == TaskState.TASK_STATE_FAILED


class TestProcessEventNonStatusUpdate:
    def test_non_status_event_is_ignored(self):
        agg = TaskResultAggregator()
        agg.process_event(MagicMock())
        assert agg.task_state == TaskState.TASK_STATE_WORKING
        assert agg.task_status_message is None


class TestProcessEventSequence:
    def test_multiple_working_events_keep_last_message(self):
        agg = TaskResultAggregator()
        agg.process_event(_make_status_event(TaskState.TASK_STATE_WORKING, "first"))
        agg.process_event(_make_status_event(TaskState.TASK_STATE_WORKING, "second"))
        assert agg.task_status_message.parts[0].text == "second"

    def test_working_after_failed_does_not_update_message(self):
        agg = TaskResultAggregator()
        agg.process_event(_make_status_event(TaskState.TASK_STATE_FAILED, "error"))
        agg.process_event(_make_status_event(TaskState.TASK_STATE_WORKING, "work"))
        assert agg.task_status_message.parts[0].text == "error"
