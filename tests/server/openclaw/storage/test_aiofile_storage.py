"""Tests for trpc_agent_sdk.server.openclaw.storage._aiofile_storage."""

import json
from pathlib import Path

import pytest

from trpc_agent_sdk.server.openclaw.config import FileStorageConfig
from trpc_agent_sdk.server.openclaw.storage._aiofile_storage import (
    AioFileStorage,
    FileAsyncContextManager,
    FileCondition,
    FileData,
    FileSession,
)
from trpc_agent_sdk.server.openclaw.storage._constants import (
    HISTORY_FILENAME,
    MEMORY_FILENAME,
)
from trpc_agent_sdk.storage import DEFAULT_MAX_KEY_LENGTH


class TestAioFileStorageInit:
    """Tests for AioFileStorage.__init__."""

    def test_base_dir_resolved(self, tmp_path):
        config = FileStorageConfig(base_dir=str(tmp_path / "store"))
        storage = AioFileStorage(config)
        assert storage._base_dir == (tmp_path / "store").resolve()

    def test_closed_initially_false(self, tmp_path):
        config = FileStorageConfig(base_dir=str(tmp_path))
        storage = AioFileStorage(config)
        assert storage._closed is False

    def test_max_key_length(self, tmp_path):
        config = FileStorageConfig(base_dir=str(tmp_path), max_key_length=512)
        storage = AioFileStorage(config)
        assert storage._max_key_length == 512


class TestValidateKey:
    """Tests for AioFileStorage._validate_key."""

    def test_empty_key_raises(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            AioFileStorage._validate_key("")

    def test_too_long_key_raises(self):
        long_key = "a" * (DEFAULT_MAX_KEY_LENGTH + 1)
        with pytest.raises(ValueError, match="too long"):
            AioFileStorage._validate_key(long_key)

    def test_forward_slash_raises(self):
        with pytest.raises(ValueError, match="path separators"):
            AioFileStorage._validate_key("a/b")

    def test_backslash_raises(self):
        with pytest.raises(ValueError, match="path separators"):
            AioFileStorage._validate_key("a\\b")

    def test_valid_key(self):
        AioFileStorage._validate_key("valid-key_123")

    def test_max_length_key_ok(self):
        AioFileStorage._validate_key("a" * DEFAULT_MAX_KEY_LENGTH)


class TestKeyToPathAndPathToKey:
    """Tests for _key_to_path and _path_to_key."""

    def test_round_trip(self, tmp_path):
        key = "hello world"
        path = AioFileStorage._key_to_path(tmp_path, key)
        recovered = AioFileStorage._path_to_key(path)
        assert recovered == key

    def test_special_chars_encoded(self, tmp_path):
        key = "key:with@special#chars"
        path = AioFileStorage._key_to_path(tmp_path, key)
        assert path.suffix == ".json"
        assert "key" not in path.stem or "%" in path.stem

    def test_path_to_key_unquotes(self, tmp_path):
        path = tmp_path / "hello%20world.json"
        assert AioFileStorage._path_to_key(path) == "hello world"


class TestIsTextFileKey:
    """Tests for _is_text_file_key."""

    def test_memory_prefix(self):
        assert AioFileStorage._is_text_file_key("memory:something") is True

    def test_history_prefix(self):
        assert AioFileStorage._is_text_file_key("history:something") is True

    def test_session_prefix(self):
        assert AioFileStorage._is_text_file_key("session:something") is True

    def test_plain_key(self):
        assert AioFileStorage._is_text_file_key("regular-key") is False

    def test_empty_key(self):
        assert AioFileStorage._is_text_file_key("") is False


def _make_storage(tmp_path: Path) -> AioFileStorage:
    config = FileStorageConfig(base_dir=str(tmp_path))
    return AioFileStorage(config)


class TestResolveKeyPath:
    """Tests for _resolve_key_path."""

    async def test_memory_key(self, tmp_path):
        storage = _make_storage(tmp_path)
        path = await storage._resolve_key_path(tmp_path, "memory:abc")
        assert path.name == MEMORY_FILENAME

    async def test_history_key(self, tmp_path):
        storage = _make_storage(tmp_path)
        path = await storage._resolve_key_path(tmp_path, "history:abc")
        assert path.name == HISTORY_FILENAME

    async def test_session_key(self, tmp_path):
        storage = _make_storage(tmp_path)
        raw_path = str(tmp_path / "session_file.jsonl")
        from urllib.parse import quote
        key = "session:" + quote(raw_path, safe="")
        path = await storage._resolve_key_path(tmp_path, key)
        assert str(path) == raw_path

    async def test_plain_key(self, tmp_path):
        storage = _make_storage(tmp_path)
        path = await storage._resolve_key_path(tmp_path, "mykey")
        expected = AioFileStorage._key_to_path(tmp_path, "mykey")
        assert path == expected


class TestAdd:
    """Tests for AioFileStorage.add."""

    async def test_add_dict_json(self, tmp_path):
        storage = _make_storage(tmp_path)
        db = FileSession(base_dir=tmp_path)
        await storage.add(db, {"key": "testkey", "value": {"foo": "bar"}})
        path = AioFileStorage._key_to_path(tmp_path, "testkey")
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data == {"foo": "bar"}

    async def test_add_filedata(self, tmp_path):
        storage = _make_storage(tmp_path)
        db = FileSession(base_dir=tmp_path)
        await storage.add(db, FileData(key="fdkey", value={"x": 1}))
        path = AioFileStorage._key_to_path(tmp_path, "fdkey")
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data == {"x": 1}

    async def test_add_text_file(self, tmp_path):
        storage = _make_storage(tmp_path)
        db = FileSession(base_dir=tmp_path)
        await storage.add(db, FileData(key="memory:abc", value="some text"))
        path = tmp_path / MEMORY_FILENAME
        assert path.exists()
        assert path.read_text(encoding="utf-8") == "some text"

    async def test_add_history_appends(self, tmp_path):
        storage = _make_storage(tmp_path)
        db = FileSession(base_dir=tmp_path)
        await storage.add(db, FileData(key="history:log", value="line1"))
        await storage.add(db, FileData(key="history:log", value="line2"))
        path = tmp_path / HISTORY_FILENAME
        content = path.read_text(encoding="utf-8")
        assert "line1" in content
        assert "line2" in content
        assert content == "line1line2"

    async def test_add_non_text_value_to_text_key(self, tmp_path):
        storage = _make_storage(tmp_path)
        db = FileSession(base_dir=tmp_path)
        await storage.add(db, FileData(key="memory:x", value={"key": "val"}))
        path = tmp_path / MEMORY_FILENAME
        content = path.read_text(encoding="utf-8")
        assert json.loads(content) == {"key": "val"}

    async def test_add_validates_key(self, tmp_path):
        storage = _make_storage(tmp_path)
        db = FileSession(base_dir=tmp_path)
        with pytest.raises(ValueError):
            await storage.add(db, {"key": "", "value": "data"})


class TestGet:
    """Tests for AioFileStorage.get."""

    async def test_get_existing_json(self, tmp_path):
        storage = _make_storage(tmp_path)
        db = FileSession(base_dir=tmp_path)
        await storage.add(db, FileData(key="gkey", value={"a": 1}))
        result = await storage.get(db, "gkey")
        assert result == {"a": 1}

    async def test_get_nonexistent(self, tmp_path):
        storage = _make_storage(tmp_path)
        db = FileSession(base_dir=tmp_path)
        result = await storage.get(db, "nope")
        assert result is None

    async def test_get_text_file(self, tmp_path):
        storage = _make_storage(tmp_path)
        db = FileSession(base_dir=tmp_path)
        await storage.add(db, FileData(key="memory:m", value="text content"))
        result = await storage.get(db, "memory:m")
        assert result == "text content"

    async def test_get_json_file_returns_parsed(self, tmp_path):
        storage = _make_storage(tmp_path)
        db = FileSession(base_dir=tmp_path)
        await storage.add(db, FileData(key="jsonkey", value=[1, 2, 3]))
        result = await storage.get(db, "jsonkey")
        assert result == [1, 2, 3]


class TestDelete:
    """Tests for AioFileStorage.delete."""

    async def test_delete_existing(self, tmp_path):
        storage = _make_storage(tmp_path)
        db = FileSession(base_dir=tmp_path)
        await storage.add(db, FileData(key="delkey", value="data"))
        path = AioFileStorage._key_to_path(tmp_path, "delkey")
        assert path.exists()
        await storage.delete(db, "delkey")
        assert not path.exists()

    async def test_delete_nonexistent_no_error(self, tmp_path):
        storage = _make_storage(tmp_path)
        db = FileSession(base_dir=tmp_path)
        await storage.delete(db, "nosuchkey")


class TestQuery:
    """Tests for AioFileStorage.query."""

    async def test_query_all(self, tmp_path):
        storage = _make_storage(tmp_path)
        db = FileSession(base_dir=tmp_path)
        await storage.add(db, FileData(key="alpha", value="a"))
        await storage.add(db, FileData(key="beta", value="b"))
        results = await storage.query(db, "*")
        keys = [k for k, _ in results]
        assert "alpha" in keys
        assert "beta" in keys

    async def test_query_pattern(self, tmp_path):
        storage = _make_storage(tmp_path)
        db = FileSession(base_dir=tmp_path)
        await storage.add(db, FileData(key="item-1", value=1))
        await storage.add(db, FileData(key="item-2", value=2))
        await storage.add(db, FileData(key="other", value=3))
        results = await storage.query(db, "item-*")
        keys = [k for k, _ in results]
        assert "item-1" in keys
        assert "item-2" in keys
        assert "other" not in keys

    async def test_query_with_limit(self, tmp_path):
        storage = _make_storage(tmp_path)
        db = FileSession(base_dir=tmp_path)
        for i in range(5):
            await storage.add(db, FileData(key=f"k{i}", value=i))
        results = await storage.query(db, "*", conditions=FileCondition(limit=2))
        assert len(results) == 2

    async def test_query_empty_dir(self, tmp_path):
        storage = _make_storage(tmp_path)
        db = FileSession(base_dir=tmp_path)
        results = await storage.query(db, "*")
        assert results == []

    async def test_query_empty_key_matches_all(self, tmp_path):
        storage = _make_storage(tmp_path)
        db = FileSession(base_dir=tmp_path)
        await storage.add(db, FileData(key="x", value=1))
        results = await storage.query(db, "")
        assert len(results) == 1


class TestClose:
    """Tests for AioFileStorage.close."""

    async def test_close_sets_closed(self, tmp_path):
        storage = _make_storage(tmp_path)
        assert storage._closed is False
        await storage.close()
        assert storage._closed is True


class TestFileAsyncContextManager:
    """Tests for FileAsyncContextManager."""

    async def test_context_manager(self, tmp_path):
        storage = _make_storage(tmp_path)
        cm = FileAsyncContextManager(storage)
        async with cm as session:
            assert isinstance(session, FileSession)
            assert session.base_dir == storage._base_dir
        assert cm._file_session is None

    async def test_create_db_session(self, tmp_path):
        storage = _make_storage(tmp_path)
        cm = storage.create_db_session()
        assert isinstance(cm, FileAsyncContextManager)
        async with cm as session:
            assert isinstance(session, FileSession)


class TestCommitAndRefresh:
    """Tests for no-op commit and refresh."""

    async def test_commit_noop(self, tmp_path):
        storage = _make_storage(tmp_path)
        db = FileSession(base_dir=tmp_path)
        await storage.commit(db)

    async def test_refresh_noop(self, tmp_path):
        storage = _make_storage(tmp_path)
        db = FileSession(base_dir=tmp_path)
        await storage.refresh(db, FileData(key="k", value="v"))


class TestReadValue:
    """Tests for _read_value."""

    async def test_read_json_value(self, tmp_path):
        storage = _make_storage(tmp_path)
        path = tmp_path / "test.json"
        path.write_text(json.dumps({"k": "v"}), encoding="utf-8")
        result = await storage._read_value(path)
        assert result == {"k": "v"}

    async def test_read_non_json_returns_raw(self, tmp_path):
        storage = _make_storage(tmp_path)
        path = tmp_path / "test.txt"
        path.write_text("not json content", encoding="utf-8")
        result = await storage._read_value(path)
        assert result == "not json content"
