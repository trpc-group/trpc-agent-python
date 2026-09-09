# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Session service backed by Advanced Memory transcript storage."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any
from typing import Optional

from trpc_agent_sdk.abc import ListSessionsResponse
from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.advanced_memory import AdvancedMemoryConfig
from trpc_agent_sdk.advanced_memory import AdvancedMemoryRuntime
from trpc_agent_sdk.advanced_memory._coordination import CrossLoopLock
from trpc_agent_sdk.advanced_memory._transcript import build_event_transcript_record
from trpc_agent_sdk.advanced_memory._transcript import find_last_event_id

from ._base_session_service import BaseSessionService
from ._session import Session
from ._types import SessionServiceConfig
from ._utils import extract_state_delta
from ._utils import merge_state


class _AdvancedMemorySessionBackend(BaseSessionService):
    """Persist Session metadata while TranscriptSessionService persists events."""

    def __init__(self, runtime: AdvancedMemoryRuntime, session_config: SessionServiceConfig | None = None) -> None:
        super().__init__(session_config=session_config)
        self._runtime = runtime
        self._lock = CrossLoopLock()
        self._cleanup_task: asyncio.Task[None] | None = None
        self._cleanup_stop_event: asyncio.Event | None = None
        self._transcript_enabled = True
        self._start_cleanup_task()

    def set_transcript_enabled(self, enabled: bool) -> None:
        """Enable or disable transcript persistence for this backend."""
        self._transcript_enabled = enabled

    def _scoped_runtime(self, app_name: str, user_id: str) -> AdvancedMemoryRuntime:
        return self._runtime.for_scope(app_name, user_id)

    def _metadata_path(self, app_name: str, user_id: str, session_id: str) -> Path:
        return self._scoped_runtime(app_name, user_id).paths.session_dir(session_id) / "session.json"

    def _app_state_path(self, app_name: str, user_id: str) -> Path:
        """Return state shared by every user of one app."""
        return self._scoped_runtime(app_name, user_id).paths.tenant_root_dir.parent / "_state.json"

    def _user_state_path(self, app_name: str, user_id: str) -> Path:
        """Return state private to one application user."""
        return self._scoped_runtime(app_name, user_id).paths.tenant_root_dir / "_state.json"

    async def _write_session(self, session: Session) -> None:
        payload = session.model_dump(mode="json", by_alias=True, exclude={"events", "historical_events"})
        payload["state"] = extract_state_delta(session.state).session_state
        path = self._metadata_path(session.app_name, session.user_id, session.id)
        await asyncio.to_thread(self._write_json, path, payload, self._runtime.config.encoding)
        await asyncio.to_thread(path.parent.joinpath(".advanced-memory-activity").touch, exist_ok=True)

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any], encoding: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        file_descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(file_descriptor, "w", encoding=encoding) as temporary_file:
                temporary_file.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_name, path)
        except BaseException:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise

    async def _read_session(self, app_name: str, user_id: str, session_id: str) -> Session | None:
        path = self._metadata_path(app_name, user_id, session_id)
        if not path.exists():
            return None
        payload = await asyncio.to_thread(path.read_text, encoding=self._runtime.config.encoding)
        await asyncio.to_thread(path.touch)
        await asyncio.to_thread(path.parent.joinpath(".advanced-memory-activity").touch, exist_ok=True)
        return Session.model_validate(json.loads(payload))

    def _start_cleanup_task(self) -> None:
        """Start persistent session cleanup when TTL is enabled."""
        if not self.session_config.need_ttl_expire() or self._cleanup_task is not None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._cleanup_stop_event = asyncio.Event()
        self._cleanup_task = loop.create_task(self._cleanup_loop())

    async def _cleanup_loop(self) -> None:
        """Periodically remove expired session directories."""
        assert self._cleanup_stop_event is not None
        try:
            while not self._cleanup_stop_event.is_set():
                try:
                    await asyncio.wait_for(
                        self._cleanup_stop_event.wait(),
                        timeout=self.session_config.ttl.cleanup_interval_seconds,
                    )
                    break
                except asyncio.TimeoutError:
                    async with self._lock:
                        await asyncio.to_thread(self._cleanup_expired_sessions)
        except asyncio.CancelledError:
            raise

    def _cleanup_expired_sessions(self) -> None:
        """Delete session directories idle longer than the configured TTL."""
        cutoff = time.time() - self.session_config.ttl.ttl_seconds
        tenants_root = self._runtime.config.root_dir / "tenants"
        if not tenants_root.exists():
            return
        for metadata_path in tenants_root.glob(f"*/*/{self._runtime.config.session_dir_name}/*/session.json"):
            try:
                if metadata_path.stat().st_mtime < cutoff:
                    session_dir = metadata_path.parent
                    if self._runtime.config.session_ttl_delete_transcripts:
                        shutil.rmtree(session_dir, ignore_errors=True)
                    else:
                        transcript_path = session_dir / self._runtime.config.transcript_name
                        for child in session_dir.iterdir():
                            if child == transcript_path:
                                continue
                            if child.is_dir():
                                shutil.rmtree(child, ignore_errors=True)
                            else:
                                child.unlink(missing_ok=True)
            except FileNotFoundError:
                continue

    async def _stop_cleanup_task(self) -> None:
        """Stop the background TTL cleanup task."""
        task = self._cleanup_task
        self._cleanup_task = None
        if task is None:
            return
        if self._cleanup_stop_event is not None:
            self._cleanup_stop_event.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self._cleanup_stop_event = None

    async def _read_global_state(self, app_name: str, user_id: str) -> dict[str, dict[str, Any]]:

        async def read(path: Path) -> dict[str, Any]:
            if not path.exists():
                return {}
            payload = await asyncio.to_thread(path.read_text, encoding=self._runtime.config.encoding)
            return dict(json.loads(payload))

        return {
            "app": await read(self._app_state_path(app_name, user_id)),
            "user": await read(self._user_state_path(app_name, user_id)),
        }

    async def _write_global_state(self, app_name: str, user_id: str, state: dict[str, dict[str, Any]]) -> None:
        await asyncio.to_thread(
            self._write_json,
            self._app_state_path(app_name, user_id),
            state["app"],
            self._runtime.config.encoding,
        )
        await asyncio.to_thread(
            self._write_json,
            self._user_state_path(app_name, user_id),
            state["user"],
            self._runtime.config.encoding,
        )

    async def _restore_events(self, session: Session) -> Session:
        records = await self._scoped_runtime(session.app_name, session.user_id).transcripts.read_all(session.id)
        events: list[Event] = []
        for record in records:
            event_payload = record.get("event")
            if record.get("kind") != "event" or not isinstance(event_payload, dict):
                continue
            events.append(Event.model_validate(event_payload))
        session.events = events
        if events:
            session.last_update_time = events[-1].timestamp
        return session

    async def create_session(
        self,
        *,
        app_name: str,
        user_id: str,
        state: Optional[dict[str, Any]] = None,
        session_id: Optional[str] = None,
        agent_context: Optional[AgentContext] = None,
    ) -> Session:
        self._start_cleanup_task()
        resolved_id = session_id.strip() if session_id and session_id.strip() else str(uuid.uuid4())
        state_delta = extract_state_delta(state)
        session = Session(
            id=resolved_id,
            app_name=app_name,
            user_id=user_id,
            state=state_delta.session_state,
            save_key=f"{app_name}/{user_id}",
        )
        async with self._lock:
            await self._scoped_runtime(app_name, user_id).initialize()
            await self._read_session(app_name, user_id, resolved_id)
            global_state = await self._read_global_state(app_name, user_id)
            global_state["app"].update(state_delta.app_state_delta)
            global_state["user"].update(state_delta.user_state_delta)
            await self._write_global_state(app_name, user_id, global_state)
            await self._write_session(session)
        session.state = merge_state(
            extract_state_delta(session.state),
            need_copy=True,
        )
        session.state.update({f"app:{key}": value for key, value in global_state["app"].items()})
        session.state.update({f"user:{key}": value for key, value in global_state["user"].items()})
        return session

    async def get_session(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
        agent_context: Optional[AgentContext] = None,
    ) -> Session | None:
        self._start_cleanup_task()
        async with self._lock:
            session = await self._read_session(app_name, user_id, session_id)
            if session is None:
                return None
            global_state = await self._read_global_state(app_name, user_id)
            app_state = global_state["app"]
            user_state = global_state["user"]
            session.state = merge_state(
                extract_state_delta(session.state),
                need_copy=True,
            )
            session.state.update({f"app:{key}": value for key, value in app_state.items()})
            session.state.update({f"user:{key}": value for key, value in user_state.items()})
            return self.filter_events(await self._restore_events(session), need_copy=True)

    async def list_sessions(
        self,
        *,
        app_name: str,
        user_id: Optional[str] = None,
    ) -> ListSessionsResponse:
        self._start_cleanup_task()
        tenants_root = self._runtime.config.root_dir / "tenants"
        if not tenants_root.exists():
            return ListSessionsResponse()
        sessions: list[Session] = []
        if user_id is not None:
            root = self._scoped_runtime(app_name, user_id).paths.session_root_dir
            session_glob = "*/session.json"
        else:
            root = tenants_root
            session_glob = f"*/*/{self._runtime.config.session_dir_name}/*/session.json"
        for path in await asyncio.to_thread(lambda: list(root.glob(session_glob))):
            try:
                session = await asyncio.to_thread(lambda path=path: Session.model_validate(
                    json.loads(path.read_text(encoding=self._runtime.config.encoding))))
            except (OSError, ValueError, TypeError):
                continue
            if session.app_name == app_name and (user_id is None or session.user_id == user_id):
                session.events = []
                session.historical_events = []
                sessions.append(session)
        return ListSessionsResponse(sessions=sessions)

    async def delete_session(self, *, app_name: str, user_id: str, session_id: str) -> None:
        self._start_cleanup_task()
        session = await self.get_session(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
        )
        if session is not None:
            async with self._lock:
                await asyncio.to_thread(shutil.rmtree, self._metadata_path(app_name, user_id, session_id).parent, True)

    async def append_event(self, session: Session, event: Event) -> Event:
        self._start_cleanup_task()
        async with self._lock:
            persisted = await super().append_event(session, event)
            if not event.partial:
                state_delta = extract_state_delta(event.actions.state_delta if event.actions else None)
                if state_delta.app_state_delta or state_delta.user_state_delta:
                    global_state = await self._read_global_state(session.app_name, session.user_id)
                    global_state["app"].update(state_delta.app_state_delta)
                    global_state["user"].update(state_delta.user_state_delta)
                    await self._write_global_state(session.app_name, session.user_id, global_state)
                    session.state.update({f"app:{key}": value for key, value in state_delta.app_state_delta.items()})
                    session.state.update({f"user:{key}": value for key, value in state_delta.user_state_delta.items()})
                await self._write_session(session)
            if not event.partial and self._transcript_enabled:
                runtime = self._scoped_runtime(session.app_name, session.user_id)
                records = await runtime.transcripts.read_all(session.id)
                record = build_event_transcript_record(
                    session,
                    persisted,
                    parent_event_id=find_last_event_id(records),
                )
                await runtime.transcripts.append_unique(
                    session.id,
                    record,
                    unique_key="event_id",
                )
            return persisted

    async def update_session(self, session: Session) -> None:
        self._start_cleanup_task()
        async with self._lock:
            await self._write_session(session)

    async def create_session_summary(
        self,
        session: Session,
        ctx: InvocationContext | None = None,
    ) -> None:
        await super().create_session_summary(session, ctx=ctx)
        await self.update_session(session)

    async def get_session_summary(self, session: Session) -> str | None:
        return await super().get_session_summary(session)

    async def close(self) -> None:
        await self._stop_cleanup_task()
        await self._runtime.close()


class AdvancedMemorySessionService(BaseSessionService):
    """Persist sessions and raw events in the Advanced Memory directory."""

    def __init__(
        self,
        runtime: AdvancedMemoryRuntime | None = None,
        *,
        config: AdvancedMemoryConfig | None = None,
        session_config: SessionServiceConfig | None = None,
        preload_memory_model: Any | None = None,
    ) -> None:
        if runtime is not None and config is not None and runtime.config != config:
            raise ValueError("runtime and config must describe the same Advanced Memory configuration")
        self._runtime = runtime or AdvancedMemoryRuntime.create(config)
        if self._runtime.config.storage_backend == "redis":
            raise ValueError("AdvancedMemorySessionService is file-backed; use RedisSessionService with "
                             "AdvancedMemoryService when AdvancedMemoryConfig.storage_backend='redis'")
        self._preload_memory_model = preload_memory_model
        self._backend = _AdvancedMemorySessionBackend(self._runtime, session_config=session_config)
        self._integration: Any | None = None
        self._bound_agent: Any | None = None
        super().__init__(session_config=session_config)

    @property
    def runtime(self) -> AdvancedMemoryRuntime:
        """Return the Advanced Memory runtime used by this service."""
        return self._runtime

    @property
    def integration(self) -> Any | None:
        """Return the Advanced Memory binding, when attached to a Runner."""
        return self._integration

    @property
    def backend(self) -> BaseSessionService:
        """Return the persistent backend used by the transcript decorator."""
        return self._backend

    def bind(self, agent: Any) -> BaseSessionService:
        """Install Advanced Memory callbacks and return the wrapped service."""
        from trpc_agent_sdk.advanced_memory import setup_advanced_memory

        if self._integration is not None:
            if agent is not self._bound_agent:
                raise ValueError("AdvancedMemorySessionService is already bound to another agent")
            return self._integration.session_service
        integration = setup_advanced_memory(
            agent,
            self,
            self._runtime,
            preload_memory_model=self._preload_memory_model,
        )
        self._backend.set_transcript_enabled(False)
        self._integration = integration
        self._bound_agent = agent
        return self._integration.session_service

    async def create_session(self, **kwargs: Any) -> Session:
        return await self._backend.create_session(**kwargs)

    async def get_session(self, **kwargs: Any) -> Session | None:
        return await self._backend.get_session(**kwargs)

    async def list_sessions(self, **kwargs: Any) -> ListSessionsResponse:
        return await self._backend.list_sessions(**kwargs)

    async def delete_session(self, **kwargs: Any) -> None:
        await self._backend.delete_session(**kwargs)

    async def append_event(self, session: Session, event: Event) -> Event:
        return await self._backend.append_event(session, event)

    async def update_session(self, session: Session) -> None:
        await self._backend.update_session(session)

    async def create_session_summary(
        self,
        session: Session,
        ctx: InvocationContext | None = None,
    ) -> None:
        await self._backend.create_session_summary(session, ctx=ctx)

    async def get_session_summary(self, session: Session) -> str | None:
        return await self._backend.get_session_summary(session)

    async def close(self) -> None:
        await self._backend.close()
