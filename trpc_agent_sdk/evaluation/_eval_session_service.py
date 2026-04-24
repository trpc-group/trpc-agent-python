# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Eval-only SessionService wrapper: injects context_messages into session.events at create_session."""

from __future__ import annotations

from typing import Any
from typing import Optional
from typing_extensions import override

from trpc_agent_sdk.events import Event
from trpc_agent_sdk.sessions import BaseSessionService
from trpc_agent_sdk.sessions import Session


class EvalSessionService(BaseSessionService):
    """Wraps a SessionService: on create_session, if context_messages were passed in,
    prepends them to session.events."""

    def __init__(self, inner: BaseSessionService, context_messages: Optional[list] = None):
        super().__init__(summarizer_manager=getattr(inner, "summarizer_manager", None))
        self._inner = inner
        self._context_messages = context_messages

    @override
    async def create_session(
        self,
        *,
        app_name: str,
        user_id: str,
        state: Optional[dict[str, Any]] = None,
        session_id: Optional[str] = None,
        agent_context: Optional[Any] = None,
    ) -> Session:
        context_messages, self._context_messages = self._context_messages, None
        session = await self._inner.create_session(
            app_name=app_name,
            user_id=user_id,
            state=state or {},
            session_id=session_id,
            agent_context=agent_context,
        )
        if context_messages:
            user_messages = []
            for content in reversed(context_messages):
                author = content.role or "user"
                user_messages.append(Event(author=author, content=content))
            if user_messages:
                user_messages.reverse()
                session.insert_events(user_messages)
            await self._inner.update_session(session)
        return session

    @override
    async def get_session(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
        agent_context: Optional[Any] = None,
    ):
        return await self._inner.get_session(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
            agent_context=agent_context,
        )

    @override
    async def list_sessions(self, *, app_name: str, user_id: str):
        return await self._inner.list_sessions(app_name=app_name, user_id=user_id)

    @override
    async def delete_session(self, *, app_name: str, user_id: str, session_id: str) -> None:
        return await self._inner.delete_session(app_name=app_name, user_id=user_id, session_id=session_id)

    @override
    async def append_event(self, session: Session, event: Event) -> Event:
        return await self._inner.append_event(session=session, event=event)

    @override
    async def update_session(self, session: Session) -> None:
        return await self._inner.update_session(session=session)

    @override
    async def create_session_summary(self, session: Session, ctx: Any = None) -> None:
        return await self._inner.create_session_summary(session=session, ctx=ctx)

    @override
    async def get_session_summary(self, session: Session):
        return await self._inner.get_session_summary(session=session)

    @override
    async def close(self) -> None:
        return await self._inner.close()
