# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
#
# Directly reuse the types from adk-python
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
#
"""Base session service interface."""

from __future__ import annotations

from abc import ABC
from abc import abstractmethod
from typing import Any
from typing import Optional
from typing import TYPE_CHECKING

from pydantic import BaseModel
from pydantic import Field

from ._response import ResponseABC
from ._session import SessionABC

if TYPE_CHECKING:
    from trpc_agent_sdk.context import AgentContext
    from trpc_agent_sdk.context import InvocationContext


class ListSessionsResponse(BaseModel):
    """The response of listing sessions.

    The events and states are not set within each Session object.
    """

    sessions: list[SessionABC] = Field(default_factory=list)


class SessionServiceABC(ABC):
    """Abstract base class for session management services.

    The service provides a set of methods for managing sessions and events.
    """

    @abstractmethod
    async def create_session(
        self,
        *,
        app_name: str,
        user_id: str,
        state: Optional[dict[str, Any]] = None,
        session_id: Optional[str] = None,
        agent_context: Optional[AgentContext] = None,
    ) -> SessionABC:
        """Creates a new session.

        Args:
            app_name: the name of the app.
            user_id: the id of the user.
            state: the initial state of the session.
            session_id: the client-provided id of the session. If not provided, a
                generated ID will be used.

        Returns:
            session: The newly created session instance.
        """

    @abstractmethod
    async def get_session(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
        agent_context: Optional[AgentContext] = None,
    ) -> Optional[SessionABC]:
        """Gets a session."""

    @abstractmethod
    async def list_sessions(self, *, app_name: str, user_id: str) -> ListSessionsResponse:
        """Lists all the sessions."""

    @abstractmethod
    async def delete_session(self, *, app_name: str, user_id: str, session_id: str) -> None:
        """Deletes a session."""

    @abstractmethod
    async def append_event(self, session: SessionABC, event: ResponseABC) -> ResponseABC:
        """Appends an event to a session object."""

    @abstractmethod
    async def update_session(self, session: SessionABC) -> None:
        """Update a session in storage.

        This method should be implemented by concrete session services
        to persist session changes to their storage backend.

        Args:
            session: The session to update
        """

    @abstractmethod
    async def create_session_summary(self, session: SessionABC, ctx: "InvocationContext" = None) -> None:
        """Summarize a session."""

    @abstractmethod
    async def get_session_summary(self, session: SessionABC) -> Optional[str]:
        """Get a summary of a session."""

    @abstractmethod
    async def close(self):
        """Closes the session service."""
