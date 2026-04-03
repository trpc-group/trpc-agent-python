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
"""Convert A2A request contexts to TrpcAgent run arguments."""

from __future__ import annotations

import inspect
from typing import Any
from typing import Awaitable
from typing import Callable
from typing import Optional
from typing import Union

from a2a.server.agent_execution import RequestContext
from google.genai import types as genai_types
from trpc_agent_sdk.configs import RunConfig

from ._part_converter import convert_a2a_part_to_genai_part

UserIdExtractor = Callable[[RequestContext], Union[str, Awaitable[str]]]


def _get_user_id_default(request: RequestContext) -> str:
    """Default user-ID extraction: prefer call_context.user, fall back to context_id."""
    if request.call_context and request.call_context.user and request.call_context.user.user_name:
        return request.call_context.user.user_name
    return f"A2A_USER_{request.context_id}"


async def _resolve_user_id(
    request: RequestContext,
    user_id_extractor: Optional[UserIdExtractor] = None,
) -> str:
    if user_id_extractor is None:
        return _get_user_id_default(request)
    result = user_id_extractor(request)
    if inspect.iscoroutine(result):
        return await result
    return result


async def get_user_session_id(
    request: RequestContext,
    user_id_extractor: Optional[UserIdExtractor] = None,
) -> tuple[str, str]:
    """Extract (user_id, session_id) from an A2A request context."""
    user_id = await _resolve_user_id(request, user_id_extractor)
    return user_id, request.context_id


async def convert_a2a_request_to_trpc_agent_run_args(
    request: RequestContext,
    user_id_extractor: Optional[UserIdExtractor] = None,
) -> dict[str, Any]:
    """Convert an A2A request to TrpcAgent ``run`` keyword arguments.

    Raises:
        ValueError: If request.message is None.
    """
    if not request.message:
        raise ValueError("Request message cannot be None")

    user_id = await _resolve_user_id(request, user_id_extractor)

    raw_meta = getattr(request.message, "metadata", None)
    request_metadata = dict(raw_meta) if isinstance(raw_meta, dict) else {}

    return {
        "user_id":
        user_id,
        "session_id":
        request.context_id,
        "new_message":
        genai_types.Content(
            role="user",
            parts=[convert_a2a_part_to_genai_part(part) for part in request.message.parts],
        ),
        "run_config":
        RunConfig(agent_run_config={"metadata": request_metadata}),
    }


async def convert_a2a_cancel_request_to_run_args(
    request: RequestContext,
    user_id_extractor: Optional[UserIdExtractor] = None,
) -> dict[str, str]:
    """Extract (user_id, session_id) needed for cancellation."""
    user_id = await _resolve_user_id(request, user_id_extractor)
    return {
        "user_id": user_id,
        "session_id": request.context_id,
    }
