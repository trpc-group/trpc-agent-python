"""Tests for trpc_agent_sdk.server.openclaw.storage._manager."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import quote

import pytest

from trpc_agent_sdk.events import Event
from trpc_agent_sdk.server.openclaw.storage._aiofile_storage import (
    AioFileStorage,
    FileData,
)
from trpc_agent_sdk.server.openclaw.storage._manager import (
    StorageManager,
    build_session_memory_kv_model,
)
from trpc_agent_sdk.storage import (
    BaseStorage,
    RedisCommand,
    RedisStorage,
    SqlKey,
    SqlStorage,
)


def _mock_storage_with_session():
    """Create a mock storage with a working create_db_session context manager."""
    storage = MagicMock(spec=BaseStorage)
    storage.create_db_session = MagicMock()
    mock_db = MagicMock()
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_db)
    ctx.__aexit__ = AsyncMock(return_value=False)
    storage.create_db_session.return_value = ctx
    storage.add = AsyncMock()
    storage.get = AsyncMock(return_value=None)
    storage.commit = AsyncMock()
    return storage, mock_db


class TestStorageManagerInit:
    """Tests for StorageManager.__init__."""

    def test_requires_create_db_session(self):
        storage = MagicMock(spec=[])
        with pytest.raises(TypeError, match="create_db_session"):
            StorageManager(storage)

    def test_generic_storage(self):
        storage, _ = _mock_storage_with_session()
        mgr = StorageManager(storage)
        assert mgr.storage is storage
        assert mgr._sql_kv_cls is None

    def test_sql_storage_builds_kv_model(self):
        from sqlalchemy import MetaData
        storage = MagicMock(spec=SqlStorage)
        storage.create_db_session = MagicMock()
        md = MetaData()
        storage._SqlStorage__metadata = md
        mgr = StorageManager(storage)
        assert mgr._sql_kv_cls is not None
        assert mgr._sql_kv_cls.__tablename__ == "session_memory_kv"

    def test_sql_storage_missing_metadata_raises(self):
        storage = MagicMock(spec=SqlStorage)
        storage.create_db_session = MagicMock()
        # Delete the private attribute so getattr returns None
        if hasattr(storage, "_SqlStorage__metadata"):
            delattr(storage, "_SqlStorage__metadata")
        with pytest.raises(ValueError, match="metadata"):
            StorageManager(storage)


class TestSerializeValue:
    """Tests for StorageManager._serialize_value."""

    def test_string_passthrough(self):
        assert StorageManager._serialize_value("hello") == "hello"

    def test_dict_to_json(self):
        result = StorageManager._serialize_value({"key": "val"})
        assert json.loads(result) == {"key": "val"}

    def test_list_to_json(self):
        result = StorageManager._serialize_value([1, 2, 3])
        assert json.loads(result) == [1, 2, 3]

    def test_int_to_json(self):
        result = StorageManager._serialize_value(42)
        assert json.loads(result) == 42


class TestDeserializeValue:
    """Tests for StorageManager._deserialize_value."""

    def test_none_returns_none(self):
        assert StorageManager._deserialize_value(None) is None

    def test_bytes_decoded(self):
        raw = json.dumps({"a": 1}).encode("utf-8")
        result = StorageManager._deserialize_value(raw)
        assert result == {"a": 1}

    def test_str_json(self):
        result = StorageManager._deserialize_value('{"key": "val"}')
        assert result == {"key": "val"}

    def test_str_non_json(self):
        result = StorageManager._deserialize_value("just a string")
        assert result == "just a string"

    def test_non_string_passthrough(self):
        result = StorageManager._deserialize_value(12345)
        assert result == 12345


class TestKeyFormatters:
    """Tests for _memory_content_key, _history_content_key, _session_storage_key."""

    def test_memory_content_key(self):
        result = StorageManager._memory_content_key("app/user/sess")
        assert result.startswith("memory:")
        assert quote("app/user/sess", safe="") in result

    def test_history_content_key(self):
        result = StorageManager._history_content_key("app/user/sess")
        assert result.startswith("history:")

    def test_session_storage_key(self):
        result = StorageManager._session_storage_key(Path("/tmp/session.jsonl"))
        assert result.startswith("session:")


class TestIsTextStorageKey:
    """Tests for _is_text_storage_key."""

    def test_memory(self):
        assert StorageManager._is_text_storage_key("memory:x") is True

    def test_history(self):
        assert StorageManager._is_text_storage_key("history:x") is True

    def test_session(self):
        assert StorageManager._is_text_storage_key("session:x") is True

    def test_plain(self):
        assert StorageManager._is_text_storage_key("plainkey") is False


class TestExtractRecentEvents:
    """Tests for _extract_recent_events."""

    def _make_event(self, event_id="e1"):
        ev = Event(author="user", id=event_id)
        return ev

    def test_empty_raw_events(self):
        assert StorageManager._extract_recent_events([], {}) == []

    def test_by_ids(self):
        e1 = self._make_event("id-1")
        e2 = self._make_event("id-2")
        e3 = self._make_event("id-3")
        meta = {"recent_event_ids": ["id-1", "id-3"]}
        result = StorageManager._extract_recent_events([e1, e2, e3], meta)
        assert len(result) == 2
        ids = [e.id for e in result]
        assert "id-1" in ids
        assert "id-3" in ids

    def test_by_count(self):
        events = [self._make_event(f"e{i}") for i in range(5)]
        meta = {"recent_event_count": 2}
        result = StorageManager._extract_recent_events(events, meta)
        assert len(result) == 2
        assert result[0].id == "e3"
        assert result[1].id == "e4"

    def test_ids_empty_falls_to_count(self):
        events = [self._make_event(f"e{i}") for i in range(3)]
        meta = {"recent_event_ids": [], "recent_event_count": 1}
        result = StorageManager._extract_recent_events(events, meta)
        assert len(result) == 1
        assert result[0].id == "e2"

    def test_no_ids_no_count_returns_empty(self):
        events = [self._make_event("e1")]
        result = StorageManager._extract_recent_events(events, {})
        assert result == []

    def test_ids_not_found_falls_to_count(self):
        events = [self._make_event(f"e{i}") for i in range(3)]
        meta = {"recent_event_ids": ["nonexistent"], "recent_event_count": 2}
        result = StorageManager._extract_recent_events(events, meta)
        assert len(result) == 2


class TestSanitizeEventForStorage:
    """Tests for _sanitize_event_for_storage."""

    def test_non_user_event_passthrough(self):
        data = {"author": "assistant", "content": {"parts": [{"text": "hi"}]}}
        assert StorageManager._sanitize_event_for_storage(data) is data

    def test_user_without_dict_content(self):
        data = {"author": "user", "content": "plain text"}
        assert StorageManager._sanitize_event_for_storage(data) is data

    def test_user_without_parts(self):
        data = {"author": "user", "content": {"text": "hello"}}
        assert StorageManager._sanitize_event_for_storage(data) is data

    def test_user_with_non_list_parts(self):
        data = {"author": "user", "content": {"parts": "not a list"}}
        assert StorageManager._sanitize_event_for_storage(data) is data

    def test_user_with_image_parts(self):
        data = {
            "author": "user",
            "content": {
                "parts": [
                    {"text": "look at this"},
                    {"inline_data": {"mime_type": "image/png", "data": "base64data..."}},
                ]
            },
        }
        result = StorageManager._sanitize_event_for_storage(data)
        parts = result["content"]["parts"]
        assert parts[0] == {"text": "look at this"}
        assert parts[1] == {"text": "[image]"}

    def test_user_with_camel_case_image(self):
        data = {
            "author": "user",
            "content": {
                "parts": [
                    {"inlineData": {"mimeType": "image/jpeg", "data": "abc"}},
                ]
            },
        }
        result = StorageManager._sanitize_event_for_storage(data)
        assert result["content"]["parts"][0] == {"text": "[image]"}

    def test_user_no_images_unchanged(self):
        data = {
            "author": "user",
            "content": {
                "parts": [{"text": "just text"}]
            },
        }
        result = StorageManager._sanitize_event_for_storage(data)
        assert result is data

    def test_non_dict_parts_preserved(self):
        data = {
            "author": "user",
            "content": {
                "parts": ["raw_string", {"text": "ok"}]
            },
        }
        result = StorageManager._sanitize_event_for_storage(data)
        assert result is data

    def test_inline_data_non_image_mime(self):
        data = {
            "author": "user",
            "content": {
                "parts": [
                    {"inline_data": {"mime_type": "application/pdf", "data": "..."}},
                ]
            },
        }
        result = StorageManager._sanitize_event_for_storage(data)
        assert result is data


class TestDumpEvent:
    """Tests for _dump_event."""

    def test_dump_event(self):
        ev = Event(author="user", id="test-id")
        result = StorageManager._dump_event(ev)
        assert isinstance(result, dict)
        assert result["author"] == "user"

    def test_dump_event_sanitizes(self):
        ev = Event(author="user", id="test-id")
        data = ev.model_dump(mode="json", exclude_none=True)
        data["content"] = {
            "parts": [{"inline_data": {"mime_type": "image/png", "data": "x"}}]
        }
        with patch.object(Event, "model_dump", return_value=data):
            result = StorageManager._dump_event(ev)
        assert result["content"]["parts"][0] == {"text": "[image]"}


class TestLoadEvent:
    """Tests for _load_event."""

    def test_valid_event(self):
        ev = Event(author="assistant", id="ev1")
        data = ev.model_dump(mode="json", exclude_none=True)
        result = StorageManager._load_event(data)
        assert result is not None
        assert result.author == "assistant"

    def test_invalid_event_returns_none(self):
        result = StorageManager._load_event({"invalid_field_xyz": True})
        assert result is None


class TestWriteLongTerm:
    """Tests for write_long_term."""

    async def test_write_long_term(self):
        storage, mock_db = _mock_storage_with_session()
        mgr = StorageManager(storage)
        await mgr.write_long_term("app/user/sess", "memory content")
        storage.add.assert_awaited_once()
        storage.commit.assert_awaited_once()


class TestReadLongTerm:
    """Tests for read_long_term."""

    async def test_read_long_term_exists(self):
        storage, mock_db = _mock_storage_with_session()
        storage.get = AsyncMock(return_value="stored memory")
        mgr = StorageManager(storage)
        result = await mgr.read_long_term("app/user/sess")
        assert result == "stored memory"

    async def test_read_long_term_missing(self):
        storage, mock_db = _mock_storage_with_session()
        storage.get = AsyncMock(return_value=None)
        mgr = StorageManager(storage)
        result = await mgr.read_long_term("app/user/sess")
        assert result == ""


class TestAppendHistory:
    """Tests for append_history."""

    async def test_append_history(self):
        storage, mock_db = _mock_storage_with_session()
        storage.get = AsyncMock(return_value="old\n")
        mgr = StorageManager(storage)
        await mgr.append_history("app/user/sess", "new entry")
        storage.add.assert_awaited_once()
        storage.commit.assert_awaited()

    async def test_append_history_empty_entry_skipped(self):
        storage, mock_db = _mock_storage_with_session()
        mgr = StorageManager(storage)
        await mgr.append_history("key", "   ")
        storage.add.assert_not_awaited()

    async def test_append_history_none_previous(self):
        storage, mock_db = _mock_storage_with_session()
        storage.get = AsyncMock(return_value=None)
        mgr = StorageManager(storage)
        await mgr.append_history("key", "entry")
        storage.add.assert_awaited()


class TestBuildAddPayload:
    """Tests for _build_add_payload."""

    def test_aiofile_storage(self, tmp_path):
        from trpc_agent_sdk.server.openclaw.config import FileStorageConfig
        config = FileStorageConfig(base_dir=str(tmp_path))
        storage = AioFileStorage(config)
        mgr = StorageManager(storage)
        payload = mgr._build_add_payload("mykey", "myval")
        assert isinstance(payload, FileData)
        assert payload.key == "mykey"
        assert payload.value == "myval"

    def test_redis_storage(self):
        storage = MagicMock(spec=RedisStorage)
        storage.create_db_session = MagicMock()
        mgr = StorageManager(storage)
        payload = mgr._build_add_payload("rkey", {"data": 1})
        assert isinstance(payload, RedisCommand)
        assert payload.method == "set"
        assert payload.args[0] == "rkey"

    def test_default_storage(self):
        storage, _ = _mock_storage_with_session()
        mgr = StorageManager(storage)
        payload = mgr._build_add_payload("k", "v")
        assert isinstance(payload, dict)
        assert payload == {"key": "k", "value": "v"}


class TestBuildGetKey:
    """Tests for _build_get_key."""

    def test_redis_storage(self):
        storage = MagicMock(spec=RedisStorage)
        storage.create_db_session = MagicMock()
        mgr = StorageManager(storage)
        result = mgr._build_get_key("thekey")
        assert isinstance(result, RedisCommand)
        assert result.method == "get"

    def test_sql_storage(self):
        from sqlalchemy import MetaData
        storage = MagicMock(spec=SqlStorage)
        storage.create_db_session = MagicMock()
        md = MetaData()
        storage._SqlStorage__metadata = md
        mgr = StorageManager(storage)
        result = mgr._build_get_key("sqlkey")
        assert isinstance(result, SqlKey)

    def test_sql_storage_no_kv_cls_raises(self):
        storage = MagicMock(spec=SqlStorage)
        storage.create_db_session = MagicMock()
        md = MagicMock()
        storage._SqlStorage__metadata = md
        mgr = StorageManager.__new__(StorageManager)
        mgr.storage = storage
        mgr._sql_kv_cls = None
        with pytest.raises(ValueError, match="Sql KV model"):
            mgr._build_get_key("k")

    def test_default_storage(self):
        storage, _ = _mock_storage_with_session()
        mgr = StorageManager(storage)
        result = mgr._build_get_key("plain")
        assert result == "plain"


class TestExtractGetValue:
    """Tests for _extract_get_value."""

    def test_sql_storage_none(self):
        storage = MagicMock(spec=SqlStorage)
        storage.create_db_session = MagicMock()
        md = MagicMock()
        storage._SqlStorage__metadata = md
        mgr = StorageManager.__new__(StorageManager)
        mgr.storage = storage
        mgr._sql_kv_cls = None
        result = mgr._extract_get_value("memory:x", None)
        assert result is None

    def test_sql_storage_text_key(self):
        storage = MagicMock(spec=SqlStorage)
        storage.create_db_session = MagicMock()
        md = MagicMock()
        storage._SqlStorage__metadata = md
        mgr = StorageManager.__new__(StorageManager)
        mgr.storage = storage
        mgr._sql_kv_cls = None
        raw = MagicMock()
        raw.value = "text content"
        result = mgr._extract_get_value("memory:x", raw)
        assert result == "text content"

    def test_sql_storage_non_text_key(self):
        storage = MagicMock(spec=SqlStorage)
        storage.create_db_session = MagicMock()
        md = MagicMock()
        storage._SqlStorage__metadata = md
        mgr = StorageManager.__new__(StorageManager)
        mgr.storage = storage
        mgr._sql_kv_cls = None
        raw = MagicMock()
        raw.value = '{"a": 1}'
        result = mgr._extract_get_value("plainkey", raw)
        assert result == {"a": 1}

    def test_redis_storage_text_key_none(self):
        storage = MagicMock(spec=RedisStorage)
        storage.create_db_session = MagicMock()
        mgr = StorageManager.__new__(StorageManager)
        mgr.storage = storage
        mgr._sql_kv_cls = None
        result = mgr._extract_get_value("memory:x", None)
        assert result is None

    def test_redis_storage_text_key_bytes(self):
        storage = MagicMock(spec=RedisStorage)
        storage.create_db_session = MagicMock()
        mgr = StorageManager.__new__(StorageManager)
        mgr.storage = storage
        mgr._sql_kv_cls = None
        result = mgr._extract_get_value("memory:x", b"hello bytes")
        assert result == "hello bytes"

    def test_redis_storage_text_key_str(self):
        storage = MagicMock(spec=RedisStorage)
        storage.create_db_session = MagicMock()
        mgr = StorageManager.__new__(StorageManager)
        mgr.storage = storage
        mgr._sql_kv_cls = None
        result = mgr._extract_get_value("session:x", "text value")
        assert result == "text value"

    def test_redis_storage_non_text_key(self):
        storage = MagicMock(spec=RedisStorage)
        storage.create_db_session = MagicMock()
        mgr = StorageManager.__new__(StorageManager)
        mgr.storage = storage
        mgr._sql_kv_cls = None
        result = mgr._extract_get_value("plainkey", '{"k": "v"}')
        assert result == {"k": "v"}

    def test_default_storage_passthrough(self):
        storage, _ = _mock_storage_with_session()
        mgr = StorageManager(storage)
        assert mgr._extract_get_value("k", "raw") == "raw"
        assert mgr._extract_get_value("k", None) is None
        assert mgr._extract_get_value("k", 42) == 42


class TestSaveSession:
    """Tests for save_session."""

    async def test_save_session_with_invocation_ctx(self):
        storage, mock_db = _mock_storage_with_session()
        mgr = StorageManager(storage)

        session = MagicMock()
        session.app_name = "app"
        session.user_id = "user"
        session.id = "sess"
        session.state = {}
        session.conversation_count = 1
        session.last_update_time = 1000.0
        session.save_key = "app/user/sess"
        ev = Event(author="user", id="ev1")
        session.events = [ev]

        mock_agent_ctx = MagicMock()
        mock_agent_ctx.get_metadata.return_value = [ev]

        mock_inv_ctx = MagicMock()
        mock_inv_ctx.agent_context = mock_agent_ctx

        with patch("trpc_agent_sdk.server.openclaw.storage._manager.get_invocation_ctx", return_value=mock_inv_ctx):
            await mgr.save_session(Path("/tmp/sess.jsonl"), session)

        storage.add.assert_awaited_once()
        storage.commit.assert_awaited_once()

    async def test_save_session_without_invocation_ctx(self):
        storage, mock_db = _mock_storage_with_session()
        mgr = StorageManager(storage)

        session = MagicMock()
        session.app_name = "app"
        session.user_id = "user"
        session.id = "sess"
        session.state = {}
        session.conversation_count = 0
        session.last_update_time = 1000.0
        session.save_key = "key"
        session.events = []

        mock_agent_ctx = MagicMock()
        mock_agent_ctx.get_metadata.return_value = []

        with (
            patch("trpc_agent_sdk.server.openclaw.storage._manager.get_invocation_ctx", return_value=None),
            patch("trpc_agent_sdk.server.openclaw.storage._manager.get_agent_context", return_value=mock_agent_ctx),
        ):
            await mgr.save_session(Path("/tmp/sess.jsonl"), session)

        storage.add.assert_awaited_once()

    async def test_save_session_no_agent_context(self):
        storage, mock_db = _mock_storage_with_session()
        mgr = StorageManager(storage)

        session = MagicMock()
        session.app_name = "app"
        session.user_id = "user"
        session.id = "s"
        session.state = {}
        session.conversation_count = 0
        session.last_update_time = 1000.0
        session.save_key = "k"
        session.events = []

        with (
            patch("trpc_agent_sdk.server.openclaw.storage._manager.get_invocation_ctx", return_value=None),
            patch("trpc_agent_sdk.server.openclaw.storage._manager.get_agent_context", return_value=None),
        ):
            await mgr.save_session(Path("/tmp/s.jsonl"), session)

        storage.add.assert_awaited_once()


class TestLoadSession:
    """Tests for load_session."""

    async def test_load_session_not_found(self):
        storage, mock_db = _mock_storage_with_session()
        storage.get = AsyncMock(return_value=None)
        mgr = StorageManager(storage)
        result = await mgr.load_session(Path("/tmp/s.jsonl"), "app", "user", "sess")
        assert result is None

    async def test_load_session_success(self):
        ev = Event(author="user", id="ev-1")
        meta = {
            "_type": "metadata",
            "app_name": "app",
            "user_id": "user",
            "id": "sess",
            "state": {"key": "val"},
            "conversation_count": 5,
            "last_update_time": 1000.0,
            "save_key": "app/user/sess",
            "recent_event_ids": ["ev-1"],
            "recent_event_count": 1,
        }
        ev_data = ev.model_dump(mode="json", exclude_none=True)
        raw_event_line = json.dumps({"_type": "raw_event", "data": ev_data})
        meta_line = json.dumps(meta)
        raw_content = meta_line + "\n" + raw_event_line + "\n"

        storage, mock_db = _mock_storage_with_session()
        storage.get = AsyncMock(return_value=raw_content)
        mgr = StorageManager(storage)

        mock_agent_ctx = MagicMock()
        with patch("trpc_agent_sdk.server.openclaw.storage._manager.get_agent_context", return_value=mock_agent_ctx):
            session = await mgr.load_session(Path("/tmp/s.jsonl"), "app", "user", "sess")

        assert session is not None
        assert session.id == "sess"
        assert session.app_name == "app"
        assert session.state == {"key": "val"}
        assert session.conversation_count == 5

    async def test_load_session_exception_returns_none(self):
        storage, mock_db = _mock_storage_with_session()
        storage.get = AsyncMock(return_value="not valid json{{{")
        mgr = StorageManager(storage)

        mock_agent_ctx = MagicMock()
        with patch("trpc_agent_sdk.server.openclaw.storage._manager.get_agent_context", return_value=mock_agent_ctx):
            result = await mgr.load_session(Path("/tmp/s.jsonl"), "app", "user", "sess")
        assert result is None

    async def test_load_session_empty_lines_skipped(self):
        meta = {
            "_type": "metadata",
            "app_name": "app",
            "user_id": "user",
            "id": "sess",
            "state": {},
            "conversation_count": 0,
            "last_update_time": 1000.0,
            "save_key": "app/user/sess",
            "recent_event_ids": [],
            "recent_event_count": 0,
        }
        raw_content = "\n" + json.dumps(meta) + "\n\n"

        storage, mock_db = _mock_storage_with_session()
        storage.get = AsyncMock(return_value=raw_content)
        mgr = StorageManager(storage)

        mock_agent_ctx = MagicMock()
        with patch("trpc_agent_sdk.server.openclaw.storage._manager.get_agent_context", return_value=mock_agent_ctx):
            session = await mgr.load_session(Path("/tmp/s.jsonl"), "app", "user", "sess")
        assert session is not None

    async def test_load_session_uses_save_key_from_metadata(self):
        meta = {
            "_type": "metadata",
            "state": {},
            "conversation_count": 2,
            "last_update_time": 500.0,
            "save_key": "custom/save/key",
            "recent_event_ids": [],
            "recent_event_count": 0,
        }
        raw_content = json.dumps(meta) + "\n"

        storage, mock_db = _mock_storage_with_session()
        storage.get = AsyncMock(return_value=raw_content)
        mgr = StorageManager(storage)

        mock_agent_ctx = MagicMock()
        with patch("trpc_agent_sdk.server.openclaw.storage._manager.get_agent_context", return_value=mock_agent_ctx):
            session = await mgr.load_session(Path("/tmp/s.jsonl"), "a", "u", "s")
        assert session.save_key == "custom/save/key"

    async def test_load_session_fallback_save_key(self):
        meta = {
            "_type": "metadata",
            "state": {},
            "conversation_count": 0,
            "last_update_time": 500.0,
            "recent_event_ids": [],
            "recent_event_count": 0,
        }
        raw_content = json.dumps(meta) + "\n"

        storage, mock_db = _mock_storage_with_session()
        storage.get = AsyncMock(return_value=raw_content)
        mgr = StorageManager(storage)

        mock_agent_ctx = MagicMock()
        with patch("trpc_agent_sdk.server.openclaw.storage._manager.get_agent_context", return_value=mock_agent_ctx):
            session = await mgr.load_session(Path("/tmp/s.jsonl"), "myapp", "myuser", "mysess")
        assert session.save_key == "myapp/myuser/mysess"


class TestSetValueSqlPath:
    """Tests for _set_value with SqlStorage path."""

    async def test_set_value_sql_existing(self):
        from sqlalchemy import MetaData
        storage = MagicMock(spec=SqlStorage)
        md = MetaData()
        storage._SqlStorage__metadata = md
        mock_db = MagicMock()
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_db)
        ctx.__aexit__ = AsyncMock(return_value=False)
        storage.create_db_session.return_value = ctx
        existing_row = MagicMock()
        existing_row.value = "old"
        storage.get = AsyncMock(return_value=existing_row)
        storage.commit = AsyncMock()

        mgr = StorageManager(storage)
        await mgr._set_value("testkey", "new_value")

        assert existing_row.value == "new_value"
        storage.commit.assert_awaited_once()

    async def test_set_value_sql_new(self):
        from sqlalchemy import MetaData
        storage = MagicMock(spec=SqlStorage)
        md = MetaData()
        storage._SqlStorage__metadata = md
        mock_db = MagicMock()
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_db)
        ctx.__aexit__ = AsyncMock(return_value=False)
        storage.create_db_session.return_value = ctx
        storage.get = AsyncMock(return_value=None)
        storage.add = AsyncMock()
        storage.commit = AsyncMock()

        mgr = StorageManager(storage)
        await mgr._set_value("newkey", "val")

        storage.add.assert_awaited_once()
        storage.commit.assert_awaited_once()

    async def test_set_value_sql_no_kv_cls_raises(self):
        storage = MagicMock(spec=SqlStorage)
        mock_db = MagicMock()
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_db)
        ctx.__aexit__ = AsyncMock(return_value=False)
        storage.create_db_session.return_value = ctx

        mgr = StorageManager.__new__(StorageManager)
        mgr.storage = storage
        mgr._sql_kv_cls = None

        with pytest.raises(ValueError, match="Sql KV model"):
            await mgr._set_value("k", "v")


class TestBuildSessionMemoryKvModel:
    """Tests for build_session_memory_kv_model."""

    def test_creates_model(self):
        from sqlalchemy import MetaData
        md = MetaData()
        model = build_session_memory_kv_model(md)
        assert model.__tablename__ == "session_memory_kv"
        assert hasattr(model, "key")
        assert hasattr(model, "value")
