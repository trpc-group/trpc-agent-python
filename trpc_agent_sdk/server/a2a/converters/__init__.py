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
"""Converters for the A2A <-> TrpcAgent boundary.

Unprefixed metadata keys and artifact-first streaming.
"""

from ._event_converter import build_request_message_metadata
from ._event_converter import convert_a2a_message_to_event
from ._event_converter import convert_a2a_task_to_event
from ._event_converter import convert_content_to_a2a_message
from ._event_converter import convert_event_to_a2a_events
from ._event_converter import convert_event_to_a2a_message
from ._event_converter import create_cancellation_event
from ._event_converter import create_completed_status_event
from ._event_converter import create_exception_status_event
from ._event_converter import create_final_status_event
from ._event_converter import create_submitted_status_event
from ._event_converter import create_working_status_event
from ._part_converter import convert_a2a_part_to_genai_part
from ._part_converter import convert_genai_part_to_a2a_part
from ._request_converter import UserIdExtractor
from ._request_converter import convert_a2a_cancel_request_to_run_args
from ._request_converter import convert_a2a_request_to_trpc_agent_run_args
from ._request_converter import get_user_session_id

__all__ = [
    "build_request_message_metadata",
    "convert_a2a_message_to_event",
    "convert_a2a_task_to_event",
    "convert_content_to_a2a_message",
    "convert_event_to_a2a_events",
    "convert_event_to_a2a_message",
    "create_cancellation_event",
    "create_completed_status_event",
    "create_exception_status_event",
    "create_final_status_event",
    "create_submitted_status_event",
    "create_working_status_event",
    "convert_a2a_part_to_genai_part",
    "convert_genai_part_to_a2a_part",
    "UserIdExtractor",
    "convert_a2a_cancel_request_to_run_args",
    "convert_a2a_request_to_trpc_agent_run_args",
    "get_user_session_id",
]
