# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
#
# Below code are copy and modified from https://github.com/google/adk-python.git
#
# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

from a2a.server.events import Event as A2AEvent
from a2a.types import Message
from a2a.types import TaskState
from a2a.types import TaskStatusUpdateEvent


class TaskResultAggregator:
    """Aggregates the task status updates and provides the final task state.

    In a2a-sdk 1.x the events are shared protobuf messages, so this aggregator
    only *observes* them and tracks the highest-priority state internally; it
    never mutates an event in place (unlike the 0.3 implementation, which
    rewrote ``event.status.state``).
    """

    def __init__(self):
        self._task_state = TaskState.TASK_STATE_WORKING
        self._task_status_message = None

    def process_event(self, event: A2AEvent):
        """Process an event from the agent run and detect signals about the task status.
        Priority of task state:
        - failed
        - auth_required
        - input_required
        - working
        """
        if isinstance(event, TaskStatusUpdateEvent):
            if event.status.state == TaskState.TASK_STATE_FAILED:
                self._task_state = TaskState.TASK_STATE_FAILED
                self._task_status_message = event.status.message
            elif (event.status.state == TaskState.TASK_STATE_AUTH_REQUIRED
                  and self._task_state != TaskState.TASK_STATE_FAILED):
                self._task_state = TaskState.TASK_STATE_AUTH_REQUIRED
                self._task_status_message = event.status.message
            elif (event.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
                  and self._task_state not in (TaskState.TASK_STATE_FAILED, TaskState.TASK_STATE_AUTH_REQUIRED)):
                self._task_state = TaskState.TASK_STATE_INPUT_REQUIRED
                self._task_status_message = event.status.message
            elif self._task_state == TaskState.TASK_STATE_WORKING:
                self._task_status_message = event.status.message

    @property
    def task_state(self) -> TaskState:
        """Get the current task state."""
        return self._task_state

    @property
    def task_status_message(self) -> Message | None:
        """Get the current task status message."""
        return self._task_status_message
