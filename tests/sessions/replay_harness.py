# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Reusable Session / Memory / Summary replay consistency harness.

可复用的 Session / Memory / Summary 跨后端回放一致性测试框架。
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import fnmatch
import json
import os
import re
import tempfile
import time
import unicodedata
import uuid
from dataclasses import asdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import AsyncGenerator
from typing import Iterable
from typing import Optional

from trpc_agent_sdk.abc import MemoryServiceConfig
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.memory import InMemoryMemoryService
from trpc_agent_sdk.memory import RedisMemoryService
from trpc_agent_sdk.memory import SqlMemoryService
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.models import LlmResponse
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.sessions import RedisSessionService
from trpc_agent_sdk.sessions import SESSION_SUMMARY_METADATA_KEY
from trpc_agent_sdk.sessions import Session
from trpc_agent_sdk.sessions import SessionServiceConfig
from trpc_agent_sdk.sessions import SessionSummarizer
from trpc_agent_sdk.sessions import SqlSessionService
from trpc_agent_sdk.sessions import SummarizerSessionManager
from trpc_agent_sdk.sessions import session_summary_from_event
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import EventActions
from trpc_agent_sdk.types import FunctionCall
from trpc_agent_sdk.types import FunctionResponse
from trpc_agent_sdk.types import Part

DEFAULT_CASES_PATH = Path(__file__).with_name("replay_cases") / "standard_cases.jsonl"
DEFAULT_REPORT_PATH = Path(__file__).parents[2] / "session_memory_summary_diff_report.json"


@dataclass(frozen=True)
class ReplayCase:
    """Describe one backend-independent replay trace.

    描述一条与具体存储后端无关的标准回放轨迹。
    """

    case_id: str
    operations: list[dict[str, Any]]
    expect: dict[str, Any]
    memory_queries: list[str]


@dataclass
class BackendBundle:
    """Group all services required to replay against one backend.

    组合在单个后端执行回放所需的 Session、Memory 与 Summary 服务。
    """

    name: str
    session_service: Any
    memory_service: Any
    summarizer_manager: SummarizerSessionManager
    summary_model: "DeterministicSummaryModel"

    async def close(self) -> None:
        """Close Session and Memory resources owned by this bundle.

        关闭当前后端组合持有的 Session 与 Memory 资源。
        """
        await self.session_service.close()
        await self.memory_service.close()


@dataclass(frozen=True)
class AllowedDiff:
    """Declare one narrowly scoped, explainable backend difference.

    声明一项范围明确且有原因说明的后端允许差异。
    """

    component: str
    path: str
    backends: tuple[str, ...]
    strategy: str
    reason: str


@dataclass
class DiffEntry:
    """Record one snapshot difference with a precise diagnostic location.

    记录一项快照差异，并携带可精确定位问题的诊断信息。
    """

    case_id: str
    session_id: str
    component: str
    field_path: str
    reference_backend: str
    reference_value: Any
    candidate_backend: str
    candidate_value: Any
    event_index: Optional[int] = None
    summary_id: Optional[str] = None
    allowed: bool = False
    reason: Optional[str] = None


# Allowed differences must name an exact field and strategy; business-value
# mismatches are never globally ignored.
# 允许差异必须明确字段与策略，绝不能全局忽略业务值的不一致。
ALLOWED_DIFFS = [
    AllowedDiff(
        component="session",
        path="/last_update_time",
        backends=("inmemory", "sqlite", "sql", "redis"),
        strategy="omit_backend_clock",
        reason="Session update time is assigned by either the process clock or the storage engine clock.",
    ),
    AllowedDiff(
        component="memory",
        path="/memory/$result_order",
        backends=("inmemory", "sqlite", "sql", "redis"),
        strategy="stable_multiset_sort",
        reason="MemoryServiceABC does not define result ordering; multiplicity and entry values remain strict.",
    ),
    AllowedDiff(
        component="events",
        path="/events/*/long_running_tool_ids",
        backends=("inmemory", "sqlite", "sql", "redis"),
        strategy="none_equals_empty_set",
        reason="SQL restores an absent long-running tool ID set as an empty set.",
    ),
]

# Keep normalization rules visible in the report so consumers can distinguish
# normalized non-business fields from fields compared strictly.
# 将归一化规则写入报告，便于区分非业务字段与严格比较字段。
NORMALIZATION_RULES = [
    {
        "path": "/events/*/timestamp",
        "strategy": "fixed_fixture_time_or_summary_version_token",
        "reason": (
            "Fixture event times remain strict; generated summary times are compared by version and monotonicity."
        ),
    },
    {
        "path": "/summary/summary_text",
        "strategy": "unicode_nfc_and_whitespace",
        "reason": "Summary presentation whitespace is non-semantic; ownership and version metadata remain strict.",
    },
    {
        "path": "/memory",
        "strategy": "stable_multiset_sort",
        "reason": "Search ordering is not part of MemoryServiceABC, but duplicate entries are retained and detected.",
    },
    {
        "path": "/**",
        "strategy": "recursive_key_sort",
        "reason": "Serialized dictionary insertion order is not business data.",
    },
]


class DeterministicSummaryModel(LLMModel):
    """Return operation-controlled summaries without using the network.

    返回由回放操作预设的摘要，不发起任何网络模型请求。
    """

    def __init__(self) -> None:
        """Initialize the fixed model identity and an empty next response.

        初始化固定模型标识及空的下一次摘要响应。
        """
        super().__init__(model_name="replay-summary-model")
        self._next_summary = ""

    @classmethod
    def supported_models(cls) -> list[str]:
        """Return the single synthetic model supported by the harness.

        返回回放框架唯一支持的合成模型名称。
        """
        return ["replay-summary-model"]

    def set_next_summary(self, summary_text: str) -> None:
        """Set the exact summary returned by the next generation call.

        设置下一次生成调用必须返回的确定性摘要文本。
        """
        self._next_summary = summary_text

    async def _generate_async_impl(
        self,
        request: LlmRequest,
        stream: bool = False,
        ctx: Any = None,
    ) -> AsyncGenerator[LlmResponse, None]:
        """Yield one deterministic model response for Summary generation.

        为 Summary 生成流程产出一个确定性的模型响应。
        """
        del request, stream, ctx
        yield LlmResponse(
            content=Content(
                role="model",
                parts=[Part.from_text(text=self._next_summary)],
            )
        )


def load_replay_cases(path: Path = DEFAULT_CASES_PATH) -> list[ReplayCase]:
    """Load consecutive JSON case objects from a readable JSONL-style file.

    从类 JSONL 文件加载连续 JSON 对象；兼容单行或跨行对象、空行及整行
    ``#`` 注释，并在格式错误时报告大致行号。
    """
    cases: list[ReplayCase] = []
    raw_lines = path.read_text(encoding="utf-8").splitlines(keepends=True)

    # Replace full-line comments with blank lines instead of deleting them, so
    # decoder character offsets still map to useful source line numbers.
    # 用空行替换整行注释而非直接删除，使解析偏移仍可映射到有用的源文件行号。
    source = "".join("\n" if line.lstrip().startswith("#") else line for line in raw_lines)
    decoder = json.JSONDecoder()
    cursor = 0
    source_length = len(source)

    while cursor < source_length:
        # raw_decode does not skip leading whitespace, so advance to the next
        # object explicitly; this also supports blank lines between cases.
        # raw_decode 不跳过前导空白，因此显式移动到下一个对象，并兼容用例间空行。
        while cursor < source_length and source[cursor].isspace():
            cursor += 1
        if cursor >= source_length:
            break

        object_line = source.count("\n", 0, cursor) + 1
        try:
            data, cursor = decoder.raw_decode(source, cursor)
        except json.JSONDecodeError as exc:
            error_line = source.count("\n", 0, exc.pos) + 1
            raise ValueError(f"Invalid replay case near {path}:{error_line}: {exc.msg}") from exc

        if not isinstance(data, dict):
            raise ValueError(f"Invalid replay case near {path}:{object_line}: expected a JSON object")
        try:
            cases.append(
                ReplayCase(
                    case_id=data["case_id"],
                    operations=data["operations"],
                    expect=data.get("expect", {}),
                    memory_queries=data.get("memory_queries", []),
                )
            )
        except (KeyError, TypeError) as exc:
            raise ValueError(f"Invalid replay case fields near {path}:{object_line}") from exc
    return cases


def _session_config() -> SessionServiceConfig:
    """Build identical Session retention settings for every backend.

    为所有后端构建完全一致的 Session 事件保留配置。
    """
    config = SessionServiceConfig(
        max_events=0,
        num_recent_events=0,
        store_historical_events=True,
    )
    config.clean_ttl_config()
    return config


def _memory_config() -> MemoryServiceConfig:
    """Build an enabled Memory configuration without TTL side effects.

    构建已启用且不受 TTL 影响的 Memory 配置。
    """
    config = MemoryServiceConfig(enabled=True)
    config.clean_ttl_config()
    return config


def _summary_components() -> tuple[DeterministicSummaryModel, SummarizerSessionManager]:
    """Create deterministic Summary components shared by one backend bundle.

    创建一套由单个后端组合使用的确定性 Summary 组件。
    """
    model = DeterministicSummaryModel()
    summarizer = SessionSummarizer(
        model=model,
        check_summarizer_functions=[lambda _session: False],
        keep_recent_count=2,
    )
    manager = SummarizerSessionManager(
        model=model,
        summarizer=summarizer,
        auto_summarize=False,
    )
    return model, manager


async def create_backend(name: str, work_dir: Path) -> BackendBundle:
    """Create one uniformly configured replay backend and its resources.

    创建一套配置统一的回放后端及其 Session、Memory、Summary 资源。
    """
    model, manager = _summary_components()
    session_config = _session_config()
    memory_config = _memory_config()

    if name == "inmemory":
        session_service = InMemorySessionService(
            summarizer_manager=manager,
            session_config=session_config,
        )
        memory_service = InMemoryMemoryService(memory_service_config=memory_config)
    elif name == "sqlite":
        db_path = work_dir / "replay.sqlite3"
        db_url = f"sqlite:///{db_path}"
        session_service = SqlSessionService(
            db_url=db_url,
            summarizer_manager=manager,
            session_config=session_config,
            is_async=False,
        )
        memory_service = SqlMemoryService(
            db_url=db_url,
            memory_service_config=memory_config,
            is_async=False,
        )
        await session_service._sql_storage.create_sql_engine()  # pylint: disable=protected-access
        await memory_service._sql_storage.create_sql_engine()  # pylint: disable=protected-access
    elif name == "sql":
        db_url = os.environ["TRPC_REPLAY_SQL_URL"]
        session_service = SqlSessionService(
            db_url=db_url,
            summarizer_manager=manager,
            session_config=session_config,
            is_async=False,
        )
        memory_service = SqlMemoryService(
            db_url=db_url,
            memory_service_config=memory_config,
            is_async=False,
        )
        await session_service._sql_storage.create_sql_engine()  # pylint: disable=protected-access
        await memory_service._sql_storage.create_sql_engine()  # pylint: disable=protected-access
    elif name == "redis":
        redis_url = os.environ["TRPC_REPLAY_REDIS_URL"]
        session_service = RedisSessionService(
            db_url=redis_url,
            summarizer_manager=manager,
            session_config=session_config,
            is_async=False,
        )
        memory_service = RedisMemoryService(
            db_url=redis_url,
            memory_service_config=memory_config,
            is_async=False,
        )
    else:
        raise ValueError(f"Unsupported replay backend: {name}")

    return BackendBundle(
        name=name,
        session_service=session_service,
        memory_service=memory_service,
        summarizer_manager=manager,
        summary_model=model,
    )


def selected_backend_names() -> list[str]:
    """Resolve default and opt-in integration backends from environment variables.

    根据环境变量解析默认后端及需显式启用的集成后端。
    """
    configured = os.getenv("TRPC_REPLAY_BACKENDS")
    if configured:
        names = [name.strip().lower() for name in configured.split(",") if name.strip()]
    else:
        names = ["inmemory", "sqlite"]

    available: list[str] = []
    # Real Redis/SQL services are opt-in: silently omit them when their URL is
    # unavailable so lightweight local and CI runs remain self-contained.
    # 真实 Redis/SQL 服务采用显式启用策略；未提供 URL 时跳过，以保持轻量运行自包含。
    for name in names:
        if name == "redis" and not os.getenv("TRPC_REPLAY_REDIS_URL"):
            continue
        if name == "sql" and not os.getenv("TRPC_REPLAY_SQL_URL"):
            continue
        available.append(name)
    if not available:
        raise ValueError("No replay backends are enabled")
    return available


def _content_for_operation(operation: dict[str, Any]) -> Optional[Content]:
    """Translate a replay operation into the corresponding event Content.

    将回放操作转换为对应的文本、函数调用或函数响应 Content。
    """
    op_name = operation["op"]
    if op_name in {"append_text", "append_with_failure_retry"}:
        text = operation.get("text")
        if text is None:
            return None
        role = operation.get("role") or ("user" if operation.get("author") == "user" else "model")
        return Content(role=role, parts=[Part.from_text(text=text)])
    if op_name == "function_call":
        return Content(
            role="model",
            parts=[
                Part(
                    function_call=FunctionCall(
                        id=operation.get("call_id"),
                        name=operation["name"],
                        args=operation.get("args", {}),
                    )
                )
            ],
        )
    if op_name == "function_response":
        return Content(
            role="user",
            parts=[
                Part(
                    function_response=FunctionResponse(
                        id=operation.get("call_id"),
                        name=operation["name"],
                        response=operation.get("response", {}),
                    )
                )
            ],
        )
    if op_name == "state_update":
        text = operation.get("text")
        if text is not None:
            return Content(role="model", parts=[Part.from_text(text=text)])
        return None
    raise ValueError(f"Operation {op_name!r} does not create an event")


def _event_for_operation(operation: dict[str, Any]) -> Event:
    """Build a deterministic Event while keeping its storage time current.

    构造 ID 与逻辑顺序确定的 Event，同时使持久化时间接近当前时钟。
    """
    # JSON fixture timestamps are deterministic logical sequence numbers. Use a
    # value close to the backend clock to avoid triggering SQL stale-write guards.
    # JSON 用例时间戳只表示确定性逻辑顺序；使用接近后端时钟的值可避免误触 SQL 陈旧写保护。
    storage_timestamp = time.time() + float(operation["timestamp"]) / 1_000_000
    return Event(
        id=operation["event_id"],
        invocation_id=operation.get("invocation_id", f"inv-{operation['event_id']}"),
        author=operation.get("author", "assistant"),
        timestamp=storage_timestamp,
        content=_content_for_operation(operation),
        actions=EventActions(state_delta=operation.get("state_delta", {})),
        partial=False,
    )


class ReplayExecutor:
    """Interpret standard operations and replay them against one backend.

    解释标准操作，并在单个后端上执行完整回放。
    """

    def __init__(self, namespace: str) -> None:
        """Store a run namespace that isolates generated application keys.

        保存本次运行命名空间，用于隔离生成的应用存储键。
        """
        self._namespace = namespace

    async def execute(self, case: ReplayCase, backend: BackendBundle) -> dict[str, Any]:
        """Execute one case, reread persisted data, and return its snapshot.

        执行单个用例，从持久化后端重新读取数据，并返回规范化快照。
        """
        app_name = f"replay-{self._namespace}-{case.case_id}"
        user_id = f"user-{case.case_id}"
        session_id = case.expect.get("session_id", case.case_id)
        session: Optional[Session] = None
        event_by_id: dict[str, Event] = {}
        observed_errors: list[dict[str, str]] = []

        for operation in case.operations:
            op_name = operation["op"]
            if op_name == "create_session":
                session = await backend.session_service.create_session(
                    app_name=app_name,
                    user_id=user_id,
                    session_id=session_id,
                    state=copy.deepcopy(operation.get("state", {})),
                )
                continue

            if session is None:
                raise ValueError(f"Case {case.case_id} must create a session before {op_name}")

            if op_name in {
                "append_text",
                "function_call",
                "function_response",
                "state_update",
            }:
                event = _event_for_operation(operation)
                event_by_id[event.id] = event
                await backend.session_service.append_event(session, event)
            elif op_name == "create_summary":
                backend.summary_model.set_next_summary(operation["summary_text"])
                await backend.summarizer_manager.create_session_summary(session, force=True)
                if operation.get("store_memory", True):
                    await backend.memory_service.store_session(session)
            elif op_name == "store_memory":
                await backend.memory_service.store_session(session)
            elif op_name == "reload_session":
                reloaded = await backend.session_service.get_session(
                    app_name=app_name,
                    user_id=user_id,
                    session_id=session_id,
                )
                if reloaded is None:
                    raise AssertionError(f"Session {session_id} disappeared during {case.case_id}")
                session = reloaded
            elif op_name == "clear_summary_cache":
                backend.summarizer_manager._summarizer_cache.clear()  # pylint: disable=protected-access
            elif op_name == "repeat_event":
                original = event_by_id[operation["event_id"]]
                await backend.session_service.append_event(session, original.model_copy(deep=True))
            elif op_name == "append_with_failure_retry":
                # Simulate failure after local Session mutation but before the
                # storage write, then retry twice to verify backend idempotency.
                # 模拟本地 Session 已变更但存储尚未写入时失败，再重试两次以验证后端幂等性。
                event = _event_for_operation(operation)
                event_by_id[event.id] = event
                try:
                    _, _, appended = (
                        backend.session_service._append_event_to_session(  # pylint: disable=protected-access
                            session,
                            event,
                        )
                    )
                    if not appended:
                        raise AssertionError("Fault injection event was unexpectedly a duplicate")
                    raise RuntimeError("simulated failure after local mutation and before storage")
                except RuntimeError as exc:
                    observed_errors.append({"operation": op_name, "error": str(exc)})
                await backend.session_service.append_event(session, event.model_copy(deep=True))
                await backend.session_service.append_event(session, event.model_copy(deep=True))
                if operation.get("store_memory", True):
                    await backend.memory_service.store_session(session)
                    await backend.memory_service.store_session(session)
            else:
                raise ValueError(f"Unknown replay operation: {op_name}")

        if session is None:
            raise AssertionError(f"Case {case.case_id} did not create a session")

        # Always reread the final Session from storage; comparing the caller's
        # mutable object would hide serialization or persistence defects.
        # 最终 Session 必须从存储重读；直接比较调用方对象会掩盖序列化或持久化缺陷。
        stored = await backend.session_service.get_session(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
        )
        if stored is None:
            raise AssertionError(f"Session {session_id} was not readable after replay")

        memory_results: dict[str, list[Any]] = {}
        for query in case.memory_queries:
            result = await backend.memory_service.search_memory(stored.save_key, query, limit=100)
            memory_results[query] = result.memories

        # Force Summary recovery through persisted Event metadata rather than
        # the manager's process-local cache.
        # 清空进程内缓存，强制通过持久化 Event 元数据恢复 Summary。
        backend.summarizer_manager._summarizer_cache.clear()  # pylint: disable=protected-access
        recovered_summary = await backend.summarizer_manager.get_session_summary(stored)
        return snapshot_from_backend(
            backend=backend.name,
            session=stored,
            memory_results=memory_results,
            recovered_summary_text=recovered_summary.summary_text if recovered_summary else None,
            observed_errors=observed_errors,
        )


def _normalize_text(value: str) -> str:
    """Normalize Unicode and whitespace without changing text semantics.

    统一 Unicode 与空白表现形式，但不改变文本语义。
    """
    return " ".join(unicodedata.normalize("NFC", value).replace("\r\n", "\n").split())


def _canonicalize(value: Any) -> Any:
    """Recursively canonicalize serialization-only representation differences.

    递归消除字典顺序、集合表示及浮点精度等纯序列化差异。
    """
    if isinstance(value, dict):
        return {key: _canonicalize(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_canonicalize(item) for item in value]
    if isinstance(value, set):
        return sorted(_canonicalize(item) for item in value)
    if isinstance(value, float):
        return round(value, 6)
    return value


def _summary_id_map(events: Iterable[Event]) -> dict[str, str]:
    """Map generated Summary IDs to stable version-based logical IDs.

    将自动生成的 Summary ID 映射为基于版本的稳定逻辑 ID。
    """
    mapping: dict[str, str] = {}
    for event in events:
        if event.is_summary_event():
            mapping[event.id] = f"summary:v{event.version or 1}"
    return mapping


def _replace_summary_ids(value: Any, summary_ids: dict[str, str]) -> Any:
    """Recursively replace Summary IDs, including replacement references.

    递归替换 Summary ID，包括摘要覆盖关系中的引用。
    """
    if isinstance(value, dict):
        return {key: _replace_summary_ids(item, summary_ids) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_summary_ids(item, summary_ids) for item in value]
    if isinstance(value, str):
        return summary_ids.get(value, value)
    return value


def _event_snapshot(event: Event, summary_ids: dict[str, str]) -> dict[str, Any]:
    """Normalize one Event while preserving all business-relevant fields.

    规范化单个 Event，同时保留所有业务相关字段供严格比较。
    """
    data = event.model_dump(mode="json", exclude_none=True)
    data["id"] = summary_ids.get(event.id, event.id)
    data["long_running_tool_ids"] = sorted(event.long_running_tool_ids or [])
    if event.is_summary_event():
        # Generated Summary IDs and clocks are backend-dependent, so compare
        # stable versions while keeping text, ownership and metadata strict.
        # Summary 自动 ID 与时钟依赖后端，因此按稳定版本比较，但文本、归属和元数据仍严格校验。
        version = event.version or 1
        data["timestamp"] = f"summary:v{version}:time"
        content = data.get("content", {})
        for part in content.get("parts", []):
            if isinstance(part.get("text"), str):
                part["text"] = _normalize_text(part["text"])
        metadata = data.get("custom_metadata", {}).get(SESSION_SUMMARY_METADATA_KEY, {})
        if metadata:
            metadata["summary_timestamp"] = f"summary:v{version}:time"
            if isinstance(metadata.get("summary_text"), str):
                metadata["summary_text"] = _normalize_text(metadata["summary_text"])
    else:
        data["timestamp"] = f"event:{event.id}:time"
    return _canonicalize(_replace_summary_ids(data, summary_ids))


def _memory_entry_snapshot(entry: Any) -> dict[str, Any]:
    """Normalize one Memory entry without discarding duplicates or content.

    规范化单条 Memory，但不去重，也不忽略作者和内容。
    """
    content = entry.content.model_dump(mode="json", exclude_none=True)
    for part in content.get("parts", []):
        if isinstance(part.get("text"), str):
            part["text"] = _normalize_text(part["text"])
    return _canonicalize(
        {
            "author": entry.author,
            "content": content,
            "timestamp": "<memory-time>" if entry.timestamp is not None else None,
        }
    )


def snapshot_from_backend(
    *,
    backend: str,
    session: Session,
    memory_results: dict[str, list[Any]],
    recovered_summary_text: Optional[str],
    observed_errors: list[dict[str, str]],
) -> dict[str, Any]:
    """Build a normalized snapshot suitable for strict cross-backend comparison.

    构建适合跨后端严格比较的规范化 Session、Memory 与 Summary 快照。
    """
    all_events = [*session.historical_events, *session.events]
    summary_ids = _summary_id_map(all_events)
    summary_events = [event for event in session.events if event.is_summary_event()]
    summary_event = max(summary_events, key=lambda item: (item.version, item.timestamp)) if summary_events else None
    summary = session_summary_from_event(summary_event, session.id) if summary_event else None
    decoded_summaries = [
        decoded
        for event in all_events
        if event.is_summary_event()
        for decoded in [session_summary_from_event(event, session.id)]
        if decoded is not None
    ]
    # Summary text receives only presentation normalization. Ownership, version,
    # replacement chain, active count and update ordering remain strict fields.
    # Summary 文本仅做展示层归一化；归属、版本、覆盖链、活跃数量和更新时间顺序均严格比较。
    ordered_summaries = sorted(decoded_summaries, key=lambda item: item.version)
    update_time_monotonic = all(
        current.summary_timestamp <= following.summary_timestamp
        for current, following in zip(ordered_summaries, ordered_summaries[1:])
    )

    summary_data: Optional[dict[str, Any]]
    if summary is None:
        summary_data = None
    else:
        summary_data = summary.model_dump(mode="json")
        summary_data["summary_id"] = summary_ids.get(summary.summary_id, summary.summary_id)
        if summary_data.get("replaces_summary_id"):
            summary_data["replaces_summary_id"] = summary_ids.get(
                summary_data["replaces_summary_id"],
                summary_data["replaces_summary_id"],
            )
        summary_data["summary_timestamp"] = f"summary:v{summary.version}:time"
        metadata = summary_data.get("metadata")
        if isinstance(metadata, dict) and "summary_timestamp" in metadata:
            metadata["summary_timestamp"] = f"summary:v{summary.version}:time"
        summary_data["summary_text"] = _normalize_text(summary.summary_text)
        summary_data["recovered_text"] = (
            _normalize_text(recovered_summary_text) if recovered_summary_text is not None else None
        )
        summary_data["active_summary_count"] = len(summary_events)
        summary_data["update_time_monotonic"] = update_time_monotonic
        summary_data = _replace_summary_ids(summary_data, summary_ids)

    normalized_memory: dict[str, list[dict[str, Any]]] = {}
    # The Memory API does not guarantee search order. Stable multiset sorting
    # removes only ordering noise and deliberately retains duplicate entries.
    # Memory API 不保证搜索顺序；稳定的多重集排序仅消除顺序噪声，并刻意保留重复项。
    for query, entries in memory_results.items():
        normalized_entries = [_memory_entry_snapshot(entry) for entry in entries]
        normalized_memory[query] = sorted(
            normalized_entries,
            key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True),
        )

    snapshot = {
        "backend": backend,
        "session_id": session.id,
        "events": [_event_snapshot(event, summary_ids) for event in session.events],
        "historical_events": [_event_snapshot(event, summary_ids) for event in session.historical_events],
        "state": _canonicalize(session.state),
        "memory": normalized_memory,
        "summary": _canonicalize(summary_data),
        "observed_errors": observed_errors,
    }
    return snapshot


def _snapshot_value(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Remove the diagnostic backend label before value comparison.

    比较业务值前移除仅用于诊断的后端名称。
    """
    value = copy.deepcopy(snapshot)
    value.pop("backend", None)
    return value


def _allowed_rule(path: str, reference_backend: str, candidate_backend: str) -> Optional[AllowedDiff]:
    """Find an explicit allowed-difference rule for one path and backend pair.

    按字段路径和后端组合查找显式声明的允许差异规则。
    """
    for rule in ALLOWED_DIFFS:
        if not fnmatch.fnmatch(path, rule.path):
            continue
        if reference_backend in rule.backends and candidate_backend in rule.backends:
            return rule
    return None


def compare_snapshots(
    *,
    case_id: str,
    reference: dict[str, Any],
    candidate: dict[str, Any],
) -> list[DiffEntry]:
    """Recursively compare snapshots and return field-level diagnostics.

    递归比较两个快照，并返回字段级、可定位的差异诊断。
    """
    differences: list[DiffEntry] = []
    reference_backend = reference["backend"]
    candidate_backend = candidate["backend"]
    session_id = reference["session_id"]
    reference_value = _snapshot_value(reference)
    candidate_value = _snapshot_value(candidate)
    summary_value = reference_value.get("summary") or candidate_value.get("summary") or {}
    summary_id = summary_value.get("summary_id") if isinstance(summary_value, dict) else None

    def add_difference(path: str, expected: Any, actual: Any) -> None:
        """Attach session, event/Summary location and allowed-diff metadata.

        为差异附加 Session、Event/Summary 定位信息及允许差异元数据。
        """
        segments = [segment for segment in path.split("/") if segment]
        component = segments[0] if segments else "snapshot"
        event_index = None
        # Derive event_index from the JSON-pointer-like path so the report can
        # locate an ordering/content defect without manually inspecting snapshots.
        # 从类 JSON Pointer 路径提取 event_index，使报告可直接定位事件顺序或内容缺陷。
        match = re.match(r"^/(?:events|historical_events)/(\d+)", path)
        if match:
            event_index = int(match.group(1))
        rule = _allowed_rule(path, reference_backend, candidate_backend)
        differences.append(
            DiffEntry(
                case_id=case_id,
                session_id=session_id,
                component=component,
                field_path=path or "/",
                reference_backend=reference_backend,
                reference_value=expected,
                candidate_backend=candidate_backend,
                candidate_value=actual,
                event_index=event_index,
                summary_id=summary_id if component == "summary" else None,
                allowed=rule is not None,
                reason=rule.reason if rule else None,
            )
        )

    def walk(expected: Any, actual: Any, path: str) -> None:
        """Walk dictionaries and lists without hiding missing or extra values.

        递归遍历字典和列表，不掩盖缺失值或额外值。
        """
        if isinstance(expected, dict) and isinstance(actual, dict):
            for key in sorted(set(expected) | set(actual)):
                child_path = f"{path}/{key}"
                if key not in expected:
                    add_difference(child_path, "<missing>", actual[key])
                elif key not in actual:
                    add_difference(child_path, expected[key], "<missing>")
                else:
                    walk(expected[key], actual[key], child_path)
            return
        if isinstance(expected, list) and isinstance(actual, list):
            common_length = min(len(expected), len(actual))
            for index in range(common_length):
                walk(expected[index], actual[index], f"{path}/{index}")
            for index in range(common_length, len(expected)):
                add_difference(f"{path}/{index}", expected[index], "<missing>")
            for index in range(common_length, len(actual)):
                add_difference(f"{path}/{index}", "<missing>", actual[index])
            return
        if expected != actual:
            add_difference(path, expected, actual)

    walk(reference_value, candidate_value, "")
    return differences


def _event_ids(snapshot: dict[str, Any], key: str) -> list[str]:
    """Extract ordered Event IDs from an active or historical snapshot list.

    从活跃或历史快照列表中按顺序提取 Event ID。
    """
    return [event["id"] for event in snapshot[key]]


def _memory_texts(snapshot: dict[str, Any], query: str) -> list[str]:
    """Extract normalized Memory texts for fixture expectation checks.

    提取规范化后的 Memory 文本，用于对照 fixture 预期。
    """
    texts: list[str] = []
    for entry in snapshot["memory"].get(query, []):
        text = "".join(part.get("text", "") for part in entry["content"].get("parts", []))
        texts.append(_normalize_text(text))
    return sorted(texts)


def validate_expectations(case: ReplayCase, snapshot: dict[str, Any]) -> list[DiffEntry]:
    """Validate one snapshot against fixtures, including InMemory-only mode.

    将单后端快照与 fixture 预期比较，确保 InMemory 轻量模式也有检测能力。
    """
    expected = case.expect
    differences: list[DiffEntry] = []

    def check(path: str, expected_value: Any, actual_value: Any) -> None:
        """Append a fixture-to-backend difference at an exact field path.

        在精确字段路径记录一项 fixture 与后端结果的差异。
        """
        if expected_value == actual_value:
            return
        differences.append(
            DiffEntry(
                case_id=case.case_id,
                session_id=snapshot["session_id"],
                component=path.strip("/").split("/", maxsplit=1)[0],
                field_path=path,
                reference_backend="fixture",
                reference_value=expected_value,
                candidate_backend=snapshot["backend"],
                candidate_value=actual_value,
            )
        )

    if "event_ids" in expected:
        check("/events/ids", expected["event_ids"], _event_ids(snapshot, "events"))
    if "historical_event_ids" in expected:
        check(
            "/historical_events/ids",
            expected["historical_event_ids"],
            _event_ids(snapshot, "historical_events"),
        )
    if "state" in expected:
        check("/state", _canonicalize(expected["state"]), snapshot["state"])
    if "summary" in expected:
        expected_summary = expected["summary"]
        if expected_summary is None:
            check("/summary", None, snapshot["summary"])
        else:
            actual_summary = snapshot["summary"] or {}
            # Besides expected content, require exactly one active Summary and
            # monotonic update times; these storage semantics cannot be normalized away.
            # 除预期内容外，还严格要求唯一活跃 Summary 和单调更新时间，不能以归一化跳过。
            for key, expected_value in expected_summary.items():
                check(f"/summary/{key}", expected_value, actual_summary.get(key, "<missing>"))
            check("/summary/active_summary_count", 1, actual_summary.get("active_summary_count", "<missing>"))
            check("/summary/update_time_monotonic", True, actual_summary.get("update_time_monotonic", "<missing>"))
    for query, expected_texts in expected.get("memory", {}).items():
        check(f"/memory/{query}", sorted(expected_texts), _memory_texts(snapshot, query))

    all_ids = _event_ids(snapshot, "events") + _event_ids(snapshot, "historical_events")
    check("/events/unique_ids", len(all_ids), len(set(all_ids)))
    return differences


def mutate_snapshot(snapshot: dict[str, Any], mutation: str) -> dict[str, Any]:
    """Inject one deterministic defect to measure comparator detection coverage.

    注入一项确定性缺陷，用于衡量比较器的异常检出能力。
    """
    # Mutate a deep copy so fault injection never contaminates the valid
    # backend snapshot subsequently written into the report.
    # 在深拷贝上注入故障，避免污染随后写入报告的正常后端快照。
    mutated = copy.deepcopy(snapshot)
    mutated["backend"] = f"{snapshot['backend']}-mutant-{mutation}"

    if mutation == "drop_event":
        mutated["events"].pop()
    elif mutation == "reorder_events":
        mutated["events"][0], mutated["events"][1] = mutated["events"][1], mutated["events"][0]
    elif mutation == "corrupt_tool_response":
        for event in mutated["events"]:
            for part in event.get("content", {}).get("parts", []):
                if "function_response" in part:
                    part["function_response"]["response"] = {"temperature": -999}
                    return mutated
        raise ValueError("No function response available to corrupt")
    elif mutation == "stale_state":
        first_key = next(iter(mutated["state"]))
        mutated["state"][first_key] = "<stale>"
    elif mutation == "leak_temp_state":
        mutated["state"]["temp:leaked"] = "secret"
    elif mutation == "drop_memory":
        query = next(iter(mutated["memory"]))
        mutated["memory"][query] = []
    elif mutation == "drop_summary":
        mutated["summary"] = None
    elif mutation == "wrong_summary_session":
        mutated["summary"]["session_id"] = "another-session"
    elif mutation == "stale_summary_version":
        mutated["summary"]["version"] = max(0, mutated["summary"]["version"] - 1)
    elif mutation == "wrong_summary_replacement":
        mutated["summary"]["replaces_summary_id"] = "summary:wrong"
    elif mutation == "drop_retained_event":
        mutated["events"].pop()
    elif mutation == "duplicate_event":
        mutated["events"].append(copy.deepcopy(mutated["events"][-1]))
    else:
        raise ValueError(f"Unknown replay mutation: {mutation}")
    return mutated


# Give every public case one targeted mutation, proving the comparator detects
# each required defect category rather than merely passing valid snapshots.
# 每条公开用例对应一项定向故障，证明比较器确实能检出各类异常而非只验证正常快照。
FAULT_BY_CASE = {
    "single_turn_text": "drop_event",
    "multi_turn_text": "reorder_events",
    "tool_call_response": "corrupt_tool_response",
    "session_state_overwrite": "stale_state",
    "scoped_state_update": "leak_temp_state",
    "memory_store_search": "drop_memory",
    "summary_create": "drop_summary",
    "summary_update_replace": "stale_summary_version",
    "summary_truncate_continue": "drop_retained_event",
    "failure_retry_duplicate": "duplicate_event",
}


async def run_replay_suite(
    *,
    cases_path: Path = DEFAULT_CASES_PATH,
    report_path: Optional[Path] = None,
    work_dir: Optional[Path] = None,
    backend_names: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Replay all cases, compare results, inject faults, and build a JSON report.

    回放全部用例、比较后端结果、注入故障，并构建可选落盘的 JSON 报告。
    """
    started_at = time.monotonic()
    cases = load_replay_cases(cases_path)
    names = backend_names or selected_backend_names()
    owned_temp_dir: Optional[tempfile.TemporaryDirectory[str]] = None
    if work_dir is None:
        owned_temp_dir = tempfile.TemporaryDirectory(prefix="trpc-replay-")
        work_dir = Path(owned_temp_dir.name)
    work_dir.mkdir(parents=True, exist_ok=True)

    namespace = uuid.uuid4().hex[:12]
    bundles: list[BackendBundle] = []
    case_reports: list[dict[str, Any]] = []
    total_differences = 0
    total_unallowed = 0

    try:
        # A fresh bundle per backend ensures every implementation receives the
        # same configuration while retaining its native persistence behavior.
        # 每个后端使用独立资源组合，在统一配置下保留各自原生持久化行为。
        for name in names:
            backend_dir = work_dir / name
            backend_dir.mkdir(parents=True, exist_ok=True)
            bundles.append(await create_backend(name, backend_dir))

        executor = ReplayExecutor(namespace)
        for case in cases:
            snapshots: dict[str, dict[str, Any]] = {}
            differences: list[DiffEntry] = []
            for bundle in bundles:
                snapshot = await executor.execute(case, bundle)
                snapshots[bundle.name] = snapshot
                differences.extend(validate_expectations(case, snapshot))

            # Fixture checks make single-backend mode meaningful; pairwise
            # checks then compare every additional backend with the reference.
            # fixture 校验保证单后端模式仍有效；随后将其余后端逐一与基准后端比较。
            reference = snapshots[bundles[0].name]
            for bundle in bundles[1:]:
                differences.extend(
                    compare_snapshots(
                        case_id=case.case_id,
                        reference=reference,
                        candidate=snapshots[bundle.name],
                    )
                )

            # Inject a known defect after valid replay. A missing diff here is
            # a comparator false negative and is surfaced in the final report.
            # 正常回放后注入已知缺陷；若未产生差异，即为比较器漏检并会写入最终报告。
            fault = FAULT_BY_CASE[case.case_id]
            mutated = mutate_snapshot(reference, fault)
            injected_differences = compare_snapshots(
                case_id=case.case_id,
                reference=reference,
                candidate=mutated,
            )

            unallowed = [difference for difference in differences if not difference.allowed]
            total_differences += len(differences)
            total_unallowed += len(unallowed)
            case_reports.append(
                {
                    "case_id": case.case_id,
                    "status": "consistent" if not unallowed else "different",
                    "snapshots": snapshots,
                    "differences": [asdict(difference) for difference in differences],
                    "fault_injection": {
                        "mutation": fault,
                        "detected": bool(injected_differences),
                        "differences": [asdict(difference) for difference in injected_differences],
                    },
                }
            )
    finally:
        for bundle in bundles:
            await bundle.close()
        if owned_temp_dir is not None:
            owned_temp_dir.cleanup()

    elapsed_seconds = round(time.monotonic() - started_at, 6)
    report = {
        "schema_version": 1,
        "mode": "lightweight" if all(name in {"inmemory", "sqlite"} for name in names) else "integration",
        "backends": names,
        "case_count": len(cases),
        "elapsed_seconds": elapsed_seconds,
        "normalization_rules": NORMALIZATION_RULES,
        "allowed_diff": [asdict(rule) for rule in ALLOWED_DIFFS],
        "summary": {
            "consistent_cases": sum(case["status"] == "consistent" for case in case_reports),
            "different_cases": sum(case["status"] == "different" for case in case_reports),
            "differences": total_differences,
            "unallowed_differences": total_unallowed,
            "faults_detected": sum(case["fault_injection"]["detected"] for case in case_reports),
        },
        "cases": case_reports,
    }

    if report_path is not None:
        # Persist complete snapshots and field-level diagnostics so failures can
        # be located by session ID, event index or Summary ID without rerunning.
        # 持久化完整快照及字段级诊断，使问题可按 Session ID、事件索引或 Summary ID 直接定位。
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return report


def _main() -> int:
    """Run the command-line harness and return a CI-friendly exit status.

    执行命令行回放框架，并返回适合 CI 判断的一致性退出码。
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--backends", default=None, help="Comma-separated backend names")
    args = parser.parse_args()
    backend_names = args.backends.split(",") if args.backends else None
    report = asyncio.run(
        run_replay_suite(
            cases_path=args.cases,
            report_path=args.report,
            backend_names=backend_names,
        )
    )
    print(
        json.dumps(
            {
                "report": str(args.report),
                **report["summary"],
                "elapsed_seconds": report["elapsed_seconds"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 1 if report["summary"]["unallowed_differences"] else 0


if __name__ == "__main__":
    raise SystemExit(_main())
