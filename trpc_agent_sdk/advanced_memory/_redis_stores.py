"""Redis implementations of the Advanced Memory storage contracts."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from trpc_agent_sdk.storage import RedisCommand, RedisExpire, RedisStorage
from trpc_agent_sdk.types import Ttl

from ._config import AdvancedMemoryConfig
from ._formats import MemoryDocument, MemoryIndexEntry, SessionMemoryDocument
from ._paths import AdvancedMemoryPaths

_APPEND_UNIQUE_SCRIPT = """
if redis.call('SADD', KEYS[2], ARGV[1]) == 0 then return 0 end
redis.call('XADD', KEYS[1], '*', 'data', ARGV[2])
return 1
"""

_RELEASE_LOCK_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""


class _RedisStore:

    def __init__(self, config: AdvancedMemoryConfig, paths: AdvancedMemoryPaths, storage: RedisStorage) -> None:
        if paths.scope is None:
            raise ValueError("Redis Advanced Memory storage requires a tenant scope")
        self._config, self._paths, self._storage = config, paths, storage
        app_component = paths.tenant_root_dir.parent.name
        user_component = paths.tenant_root_dir.name
        self._user_base = f"{config.redis_key_prefix}:{{{app_component}:{user_component}}}"
        self._app_base = f"{config.redis_key_prefix}:{{{app_component}}}"

    async def _command(self, method: str, *args: Any, **kwargs: Any) -> Any:
        command_expire = kwargs.pop("_command_expire", None)
        async with self._storage.create_db_session() as connection:
            return await self._storage.execute_command(
                connection,
                RedisCommand(method=method, args=args, kwargs=kwargs, expire=command_expire or RedisExpire()),
            )

    def _session_base(self, session_id: str) -> str:
        safe_session_id = self._paths.session_dir(session_id).name
        tenant = f"{self._paths.tenant_root_dir.parent.name}:{self._paths.tenant_root_dir.name}"
        return f"{self._config.redis_key_prefix}:{{{tenant}:{safe_session_id}}}"

    def _session_registry(self, session_id: str) -> str:
        return f"{self._session_base(session_id)}:keys"

    def _memory_registry(self) -> str:
        return f"{self._user_base}:memory:keys"

    def _memory_lock_key(self) -> str:
        """Return the distributed lock key for this app/user memory scope."""
        return f"{self._user_base}:memory:lock"

    @asynccontextmanager
    async def _memory_write_lock(self):
        """Serialize long-term memory writes across processes and nodes."""
        token = uuid4().hex
        key = self._memory_lock_key()
        deadline = asyncio.get_running_loop().time() + self._config.memory_lock_acquire_timeout_seconds
        acquired = False
        while asyncio.get_running_loop().time() < deadline:
            result = await self._command(
                "set",
                key,
                token,
                nx=True,
                ex=self._config.memory_lock_ttl_seconds,
                _command_expire=RedisExpire(
                    key=key,
                    ttl=Ttl(ttl_seconds=self._config.memory_lock_ttl_seconds),
                ),
            )
            if result is True or result in (b"OK", "OK"):
                acquired = True
                break
            await asyncio.sleep(min(0.05, max(0.0, deadline - asyncio.get_running_loop().time())))
        if not acquired:
            raise TimeoutError(f"Timed out acquiring Advanced Memory lock for {self._paths.scope.storage_key}")
        try:
            yield
        finally:
            await self._command(
                "eval",
                _RELEASE_LOCK_SCRIPT,
                1,
                key,
                token,
            )

    async def _refresh_ttl_group(
            self,
            registry: str,
            keys: list[str],
            ttl: int | None,
            skip_prefixes: tuple[str, ...] = (),
    ) -> None:
        """Track and refresh every key in one logical memory group."""
        if ttl is None:
            return
        if keys:
            await self._command("sadd", registry, *keys)
        tracked = await self._command("smembers", registry) or []
        tracked_keys = {self._text(value) for value in tracked}
        tracked_keys.update(keys)
        for key in tracked_keys:
            if key and not key.startswith(skip_prefixes):
                await self._command("expire", key, ttl)
        await self._command("expire", registry, ttl)

    async def _refresh_session_ttl(self, session_id: str, *keys: str) -> None:
        skip_prefixes: tuple[str, ...] = ()
        if not self._config.session_ttl_delete_transcripts:
            skip_prefixes = (f"{self._session_base(session_id)}:transcript", )
        await self._refresh_ttl_group(
            self._session_registry(session_id),
            list(keys),
            self._config.session_ttl_seconds,
            skip_prefixes=skip_prefixes,
        )

    async def _refresh_memory_ttl(self, *keys: str) -> None:
        await self._refresh_ttl_group(
            self._memory_registry(),
            list(keys),
            self._config.memory_ttl_seconds,
        )

    async def delete_session(self, session_id: str) -> None:
        """Delete all Advanced Memory keys for one session."""
        session_base = self._session_base(session_id)
        registry = self._session_registry(session_id)
        keys: set[str] = {registry}
        tracked = await self._command("smembers", registry) or []
        keys.update(value for value in (self._text(item) for item in tracked) if value)

        cursor: Any = 0
        pattern = f"{session_base}:*"
        while True:
            cursor, scanned = await self._command(
                "scan",
                cursor,
                match=pattern,
                count=100,
            )
            keys.update(value for value in (self._text(item) for item in scanned) if value)
            if int(cursor) == 0:
                break
        if keys:
            await self._command("delete", *keys)

    @staticmethod
    def _text(value: Any) -> str | None:
        if value is None:
            return None
        return value.decode("utf-8") if isinstance(value, bytes) else str(value)


class RedisLongTermMemoryStore(_RedisStore):

    async def initialize(self) -> None:
        key = f"{self._user_base}:memory:index"
        await self._command("setnx", key, "")
        await self._refresh_memory_ttl(key)

    async def read_index(self) -> str:
        key = f"{self._user_base}:memory:index"
        value = self._text(await self._command("get", key)) or ""
        await self._refresh_memory_ttl()
        lines, used_bytes = [], 0
        for line in value.splitlines(keepends=True)[:self._config.memory_index_max_lines]:
            size = len(line.encode(self._config.encoding))
            if used_bytes + size > self._config.memory_index_max_bytes:
                break
            lines.append(line)
            used_bytes += size
        return "".join(lines)

    async def write_index(self, entries: list[MemoryIndexEntry]) -> None:
        content = "\n".join(entry.to_markdown() for entry in entries)
        key = f"{self._user_base}:memory:index"
        async with self._memory_write_lock():
            await self._command("set", key, f"{content}\n" if content else "")
            await self._refresh_memory_ttl(key)

    def _topic_name(self, topic_name: str) -> str:
        return self._paths.memory_topic_path(topic_name).name

    async def read_topic(self, topic_name: str) -> str | None:
        key = f"{self._user_base}:memory:topic:{self._topic_name(topic_name)}"
        value = await self._command("get", key)
        await self._refresh_memory_ttl()
        return self._text(value)

    async def read_topic_frontmatter(self, topic_name: str) -> str | None:
        content = await self.read_topic(topic_name)
        if content is None:
            return None
        end = content.find("\n---", 4) if content.startswith("---\n") else -1
        return content[:end + 4] if end >= 0 else content

    async def write_topic(self, topic_name: str, document: MemoryDocument) -> Path:
        name = self._topic_name(topic_name)
        document = replace(document, updated_at=datetime.now(timezone.utc))
        topic_key = f"{self._user_base}:memory:topic:{name}"
        topics_key = f"{self._user_base}:memory:topics"
        async with self._memory_write_lock():
            await self._command("set", topic_key, document.to_markdown())
            await self._command("zadd", topics_key, {name: document.updated_at.timestamp()})
            await self._refresh_memory_ttl(topic_key, topics_key)
        return Path(name)

    async def list_topics(self) -> list[Path]:
        key = f"{self._user_base}:memory:topics"
        values = await self._command("zrange", key, 0, -1)
        await self._refresh_memory_ttl()
        return [Path(self._text(value) or "") for value in values]


class RedisSessionMemoryStore(_RedisStore):

    async def read(self, session_id: str) -> str | None:
        key = f"{self._session_base(session_id)}:summary"
        value = await self._command("get", key)
        await self._refresh_session_ttl(session_id, key)
        return self._text(value)

    async def write(self, session_id: str, document: SessionMemoryDocument) -> Path:
        key = f"{self._session_base(session_id)}:summary"
        await self._command("set", key, document.to_markdown())
        await self._refresh_session_ttl(session_id, key)
        return Path(f"advanced-memory://{key}")


class RedisToolResultStore(_RedisStore):

    async def write(self, session_id: str, result_id: str, serialized_result: str) -> Path:
        key = f"{self._session_base(session_id)}:tool:{result_id}"
        await self._command("set", key, serialized_result)
        await self._refresh_session_ttl(session_id, key)
        return Path(f"advanced-memory://{key}")

    async def read(self, session_id: str, result_id: str) -> str | None:
        key = f"{self._session_base(session_id)}:tool:{result_id}"
        value = await self._command("get", key)
        await self._refresh_session_ttl(session_id, key)
        return self._text(value)


class RedisTranscriptStore(_RedisStore):

    async def append(self, session_id: str, record: Mapping[str, Any]) -> Path:
        payload = dict(record)
        payload.setdefault("recorded_at", datetime.now(timezone.utc).isoformat())
        stream = f"{self._session_base(session_id)}:transcript"
        await self._command("xadd", stream, {"data": json.dumps(payload)})
        await self._refresh_session_ttl(session_id, stream)
        return Path(f"advanced-memory://{stream}")

    async def append_unique(self, session_id: str, record: Mapping[str, Any], *, unique_key: str) -> tuple[Path, bool]:
        payload = dict(record)
        value = payload.get(unique_key)
        if not isinstance(value, str) or not value:
            raise ValueError(f"Transcript unique key {unique_key!r} must be a non-empty string")
        payload.setdefault("recorded_at", datetime.now(timezone.utc).isoformat())
        stream = f"{self._session_base(session_id)}:transcript"
        seen = f"{stream}:seen:{unique_key}"
        async with self._storage.create_db_session() as connection:
            added = await self._storage.execute_command(
                connection,
                RedisCommand(
                    method="eval",
                    args=(_APPEND_UNIQUE_SCRIPT, 2, stream, seen, value, json.dumps(payload, ensure_ascii=False)),
                ))
        await self._refresh_session_ttl(session_id, stream, seen)
        return Path(f"advanced-memory://{stream}"), bool(added)

    async def read_all(self, session_id: str) -> list[dict[str, Any]]:
        stream = f"{self._session_base(session_id)}:transcript"
        entries = await self._command("xrange", stream, "-", "+")
        await self._refresh_session_ttl(session_id, stream)
        records: list[dict[str, Any]] = []
        for _, fields in entries:
            value = fields.get(b"data") if isinstance(fields, dict) else None
            value = value or fields.get("data")
            text = self._text(value)
            if text:
                records.append(json.loads(text))
        return records
