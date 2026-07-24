# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

"""Backend adapters that replay standard operations through public SDK APIs."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from trpc_agent_sdk.abc import MemoryServiceConfig
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.memory import InMemoryMemoryService
from trpc_agent_sdk.memory import RedisMemoryService
from trpc_agent_sdk.memory import SqlMemoryService
from trpc_agent_sdk.models import LlmResponse
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.sessions import RedisSessionService
from trpc_agent_sdk.sessions import Session
from trpc_agent_sdk.sessions import SessionServiceConfig
from trpc_agent_sdk.sessions import SessionSummarizer
from trpc_agent_sdk.sessions import SqlSessionService
from trpc_agent_sdk.sessions import SummarizerSessionManager
from trpc_agent_sdk.sessions import find_events_for_summary
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import FunctionCall
from trpc_agent_sdk.types import FunctionResponse
from trpc_agent_sdk.types import Part

from .cases import ReplayCase
from .cases import ReplayOp
from .snapshot import MemoryProbe
from .snapshot import Snapshot
from .snapshot import SummaryRecord
from .snapshot import read_snapshot


class FakeSummaryModel:
    """Deterministic model used by SessionSummarizer in replay tests."""

    name = "fake-replay-summary-model"

    async def generate_async(self, request, stream: bool = False, ctx=None):
        text = "deterministic-summary:stable"
        yield LlmResponse(content=Content(parts=[Part.from_text(text=text)]))


class ReplayBackendAdapter:
    """Common adapter state and operation mapping."""

    name = "base"

    def __init__(self, *, case: ReplayCase, workdir: Path | None = None) -> None:
        self.case = case
        self.workdir = workdir
        self.session_service = None
        self.memory_service = None
        self.sessions: dict[tuple[str, str, str], Session] = {}
        self.event_id_map: dict[str, str] = {}
        self.actual_to_client_event_id: dict[str, str] = {}
        self.summary_records: dict[str, list[SummaryRecord]] = defaultdict(list)
        self.memory_probes: list[MemoryProbe] = []
        self._timestamp = 1_700_000_000.0

    async def setup(self) -> None:
        raise NotImplementedError

    async def close(self) -> None:
        if self.memory_service:
            await self.memory_service.close()
        if self.session_service:
            await self.session_service.close()

    async def replay(self) -> Snapshot:
        for op in self.case.operations:
            await self.apply(op)
        return await self.snapshot()

    async def apply(self, op: ReplayOp) -> None:
        if op.kind == "create_session":
            await self.create_session(op)
        elif op.kind == "append_text":
            await self.append_event(op, [Part.from_text(text=op.text)])
        elif op.kind == "append_tool_call":
            function_call = FunctionCall(name=op.function_name, args=op.function_args)
            function_call.id = op.function_call_id
            await self.append_event(op, [Part(function_call=function_call)])
        elif op.kind == "append_tool_response":
            function_response = FunctionResponse(name=op.function_name, response=op.function_response)
            function_response.id = op.function_call_id
            await self.append_event(op, [Part(function_response=function_response)])
        elif op.kind == "store_memory":
            await self.store_memory(op)
        elif op.kind == "search_memory":
            self.memory_probes.append(
                MemoryProbe(
                    probe_id=op.probe_id or op.query,
                    session_key=self._session_key(op),
                    query=op.query,
                )
            )
        elif op.kind == "summarize":
            await self.summarize(op)
        else:
            raise ValueError(f"Unsupported replay op: {op.kind}")

    async def create_session(self, op: ReplayOp) -> None:
        session = await self.session_service.create_session(
            app_name=op.app_name,
            user_id=op.user_id,
            session_id=op.session_id,
            state=op.initial_state,
        )
        self.sessions[(op.app_name, op.user_id, op.session_id)] = session

    async def append_event(self, op: ReplayOp, parts: list[Part]) -> None:
        session = self._get_session(op)
        content_role = "user" if op.author == "user" else "model"
        event = Event(
            invocation_id=f"{self.case.case_id}:{op.client_event_id}",
            author=op.author,
            content=Content(parts=parts, role=content_role),
            timestamp=self._next_timestamp(),
        )
        if op.state_delta:
            event.actions.state_delta.update(op.state_delta)
        await self.session_service.append_event(session, event)
        if op.client_event_id:
            self.event_id_map[op.client_event_id] = event.id
            self.actual_to_client_event_id[event.id] = op.client_event_id
        await self._refresh_session(op)

    async def store_memory(self, op: ReplayOp) -> None:
        session = await self.session_service.get_session(
            app_name=op.app_name,
            user_id=op.user_id,
            session_id=op.session_id,
        )
        await self.memory_service.store_session(session)

    async def summarize(self, op: ReplayOp) -> None:
        session = self._get_session(op)
        events_for_summary, _ = find_events_for_summary(session.events, keep_recent_count=2)
        covered_ids = [self.actual_to_client_event_id.get(event.id, event.id) for event in events_for_summary]
        manager = self.session_service.summarizer_manager
        await manager.create_session_summary(session, force=True)
        active_summary = next((event for event in session.events if event.is_summary_event()), None)
        if active_summary is None:
            return
        if len(session.events) > 1:
            active_summary.timestamp = session.events[1].timestamp - 0.1
            await self.session_service.update_session(session)
        client_summary_id = op.client_summary_id or f"summary-{len(self.summary_records[self._session_key(op)]) + 1}"
        self.actual_to_client_event_id[active_summary.id] = client_summary_id
        session_key = self._session_key(op)
        records = self.summary_records[session_key]
        for record in records:
            record.active = False
        records.append(
            SummaryRecord(
                client_summary_id=client_summary_id,
                session_id=op.session_id,
                user_id=op.user_id,
                app_name=op.app_name,
                event_id=client_summary_id,
                text=active_summary.get_text(),
                version=len(records) + 1,
                active=True,
                covered_event_ids=covered_ids,
                timestamp=active_summary.timestamp,
            )
        )
        await self._refresh_session(op)

    async def snapshot(self) -> Snapshot:
        return await read_snapshot(
            backend=self.name,
            case_id=self.case.case_id,
            session_service=self.session_service,
            memory_service=self.memory_service,
            sessions=list(self.sessions.values()),
            actual_to_client_event_id=self.actual_to_client_event_id,
            memory_probes=self.memory_probes,
            summary_records=[record for records in self.summary_records.values() for record in records],
        )

    def _next_timestamp(self) -> float:
        self._timestamp += 1.0
        return self._timestamp

    def _get_session(self, op: ReplayOp) -> Session:
        return self.sessions[(op.app_name, op.user_id, op.session_id)]

    async def _refresh_session(self, op: ReplayOp) -> None:
        session = await self.session_service.get_session(
            app_name=op.app_name,
            user_id=op.user_id,
            session_id=op.session_id,
        )
        self.sessions[(op.app_name, op.user_id, op.session_id)] = session

    @staticmethod
    def _session_key(op: ReplayOp) -> str:
        return f"{op.app_name}/{op.user_id}"


class InMemoryReplayAdapter(ReplayBackendAdapter):
    """Replay adapter backed by in-memory session and memory services."""

    name = "in_memory"

    async def setup(self) -> None:
        config = SessionServiceConfig(store_historical_events=True)
        config.clean_ttl_config()
        summarizer = SessionSummarizer(model=FakeSummaryModel(), keep_recent_count=2)
        manager = SummarizerSessionManager(model=FakeSummaryModel(), summarizer=summarizer, auto_summarize=False)
        self.session_service = InMemorySessionService(summarizer_manager=manager, session_config=config)
        memory_config = MemoryServiceConfig(enabled=True)
        memory_config.clean_ttl_config()
        self.memory_service = InMemoryMemoryService(memory_service_config=memory_config)


class SQLiteReplayAdapter(ReplayBackendAdapter):
    """Replay adapter backed by SQLite SQL session and memory services."""

    name = "sqlite"

    async def setup(self) -> None:
        assert self.workdir is not None
        db_path = self.workdir / f"{self.case.case_id}_{self.name}.db"
        db_url = f"sqlite:///{db_path.as_posix()}"
        config = SessionServiceConfig(store_historical_events=True)
        config.clean_ttl_config()
        summarizer = SessionSummarizer(model=FakeSummaryModel(), keep_recent_count=2)
        manager = SummarizerSessionManager(model=FakeSummaryModel(), summarizer=summarizer, auto_summarize=False)
        self.session_service = SqlSessionService(
            db_url=db_url,
            summarizer_manager=manager,
            session_config=config,
            is_async=False,
        )
        await self.session_service._sql_storage.create_sql_engine()
        memory_config = MemoryServiceConfig(enabled=True)
        memory_config.clean_ttl_config()
        self.memory_service = SqlMemoryService(
            db_url=db_url,
            memory_service_config=memory_config,
            enabled=True,
            is_async=False,
        )
        await self.memory_service._sql_storage.create_sql_engine()


class SQLUrlReplayAdapter(ReplayBackendAdapter):
    """Replay adapter backed by an externally configured SQL URL."""

    name = "sql"

    def __init__(self, *, case: ReplayCase, db_url: str) -> None:
        super().__init__(case=case)
        self.db_url = db_url

    async def setup(self) -> None:
        config = SessionServiceConfig(store_historical_events=True)
        config.clean_ttl_config()
        summarizer = SessionSummarizer(model=FakeSummaryModel(), keep_recent_count=2)
        manager = SummarizerSessionManager(model=FakeSummaryModel(), summarizer=summarizer, auto_summarize=False)
        self.session_service = SqlSessionService(
            db_url=self.db_url,
            summarizer_manager=manager,
            session_config=config,
            is_async=False,
        )
        await self.session_service._sql_storage.create_sql_engine()
        memory_config = MemoryServiceConfig(enabled=True)
        memory_config.clean_ttl_config()
        self.memory_service = SqlMemoryService(
            db_url=self.db_url,
            memory_service_config=memory_config,
            enabled=True,
            is_async=False,
        )
        await self.memory_service._sql_storage.create_sql_engine()


class RedisReplayAdapter(ReplayBackendAdapter):
    """Replay adapter backed by an externally configured Redis URL."""

    name = "redis"

    def __init__(self, *, case: ReplayCase, redis_url: str) -> None:
        super().__init__(case=case)
        self.redis_url = redis_url

    async def setup(self) -> None:
        config = SessionServiceConfig(store_historical_events=True)
        config.clean_ttl_config()
        summarizer = SessionSummarizer(model=FakeSummaryModel(), keep_recent_count=2)
        manager = SummarizerSessionManager(model=FakeSummaryModel(), summarizer=summarizer, auto_summarize=False)
        self.session_service = RedisSessionService(
            db_url=self.redis_url,
            summarizer_manager=manager,
            session_config=config,
            is_async=False,
        )
        memory_config = MemoryServiceConfig(enabled=True)
        memory_config.clean_ttl_config()
        self.memory_service = RedisMemoryService(
            db_url=self.redis_url,
            memory_service_config=memory_config,
            enabled=True,
            is_async=False,
        )


class FaultInjectingAdapter:
    """Minimal proxy for operation-level fault injection tests."""

    def __init__(self, adapter: ReplayBackendAdapter, *, fail_after_op_kind: str) -> None:
        self.adapter = adapter
        self.fail_after_op_kind = fail_after_op_kind
        self.triggered = False

    async def apply(self, op: ReplayOp) -> None:
        await self.adapter.apply(op)
        if not self.triggered and op.kind == self.fail_after_op_kind:
            self.triggered = True
            raise ReplayInjectedFault(f"Injected fault after {op.kind}")


class ReplayInjectedFault(RuntimeError):
    """Raised by FaultInjectingAdapter after a configured operation commits."""
