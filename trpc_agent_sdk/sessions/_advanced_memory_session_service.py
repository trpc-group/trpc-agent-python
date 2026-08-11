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
from trpc_agent_sdk.advanced_memory.coordination import CrossLoopLock
from trpc_agent_sdk.advanced_memory.transcript import build_event_transcript_record
from trpc_agent_sdk.advanced_memory.transcript import find_last_event_id

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
        self._start_cleanup_task()

    def _metadata_path(self, session_id: str) -> Path:
        return self._runtime.paths.session_dir(session_id) / "session.json"

    @property
    def _state_path(self) -> Path:
        return self._runtime.paths.session_root_dir / "_state.json"

    async def _write_session(self, session: Session) -> None:
        payload = session.model_dump(mode="json", by_alias=True, exclude={"events", "historical_events"})
        path = self._metadata_path(session.id)
        await asyncio.to_thread(self._write_json, path, payload, self._runtime.config.encoding)

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

    async def _read_session(self, session_id: str) -> Session | None:
        path = self._metadata_path(session_id)
        if not path.exists():
            return None
        payload = await asyncio.to_thread(path.read_text, encoding=self._runtime.config.encoding)
        await asyncio.to_thread(path.touch)
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
        root = self._runtime.paths.session_root_dir
        if not root.exists():
            return
        for metadata_path in root.glob("*/session.json"):
            try:
                if metadata_path.stat().st_mtime < cutoff:
                    shutil.rmtree(metadata_path.parent, ignore_errors=True)
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

    async def _read_global_state(self) -> dict[str, dict[str, Any]]:
        if not self._state_path.exists():
            return {"app": {}, "user": {}}
        payload = await asyncio.to_thread(
            self._state_path.read_text,
            encoding=self._runtime.config.encoding,
        )
        parsed = json.loads(payload)
        return {
            "app": dict(parsed.get("app", {})),
            "user": dict(parsed.get("user", {})),
        }

    async def _write_global_state(self, state: dict[str, dict[str, Any]]) -> None:
        await asyncio.to_thread(self._write_json, self._state_path, state, self._runtime.config.encoding)

    async def _restore_events(self, session: Session) -> Session:
        records = await self._runtime.transcripts.read_all(session.id)
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
            await self._runtime.initialize()
            existing = await self._read_session(resolved_id)
            if existing is not None and (existing.app_name != app_name or existing.user_id != user_id):
                raise ValueError(f"Session ID {resolved_id!r} is already used by another app or user")
            global_state = await self._read_global_state()
            global_state["app"].setdefault(app_name, {}).update(state_delta.app_state_delta)
            global_state["user"].setdefault(f"{app_name}/{user_id}", {}).update(state_delta.user_state_delta)
            await self._write_global_state(global_state)
            await self._write_session(session)
        session.state = merge_state(
            extract_state_delta(session.state),
            need_copy=True,
        )
        session.state.update({f"app:{key}": value for key, value in global_state["app"].get(app_name, {}).items()})
        session.state.update({
            f"user:{key}": value
            for key, value in global_state["user"].get(f"{app_name}/{user_id}", {}).items()
        })
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
            session = await self._read_session(session_id)
            if session is None or session.app_name != app_name or session.user_id != user_id:
                return None
            global_state = await self._read_global_state()
            app_state = global_state["app"].get(app_name, {})
            user_state = global_state["user"].get(f"{app_name}/{user_id}", {})
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
        if not self._runtime.paths.session_root_dir.exists():
            return ListSessionsResponse()
        sessions: list[Session] = []
        for path in await asyncio.to_thread(lambda: list(self._runtime.paths.session_root_dir.glob("*/session.json"))):
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
                await asyncio.to_thread(shutil.rmtree, self._runtime.paths.session_dir(session_id), True)

    async def append_event(self, session: Session, event: Event) -> Event:
        self._start_cleanup_task()
        async with self._lock:
            persisted = await super().append_event(session, event)
            if not event.partial:
                state_delta = extract_state_delta(event.actions.state_delta if event.actions else None)
                if state_delta.app_state_delta or state_delta.user_state_delta:
                    global_state = await self._read_global_state()
                    global_state["app"].setdefault(session.app_name, {}).update(state_delta.app_state_delta)
                    global_state["user"].setdefault(f"{session.app_name}/{session.user_id}",
                                                    {}).update(state_delta.user_state_delta)
                    await self._write_global_state(global_state)
                    session.state.update({f"app:{key}": value for key, value in state_delta.app_state_delta.items()})
                    session.state.update({f"user:{key}": value for key, value in state_delta.user_state_delta.items()})
                await self._write_session(session)
                records = await self._runtime.transcripts.read_all(session.id)
                record = build_event_transcript_record(
                    session,
                    persisted,
                    parent_event_id=find_last_event_id(records),
                )
                await self._runtime.transcripts.append_unique(
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
        self._integration = setup_advanced_memory(
            agent,
            self,
            self._runtime,
            preload_memory_model=self._preload_memory_model,
        )
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
