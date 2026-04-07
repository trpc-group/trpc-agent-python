"""Unit tests for trpc_agent_sdk.server.openclaw.session_memory._claw_session_service."""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trpc_agent_sdk.server.openclaw.session_memory._claw_session_service import ClawSessionService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_service(tmp_path: Path) -> ClawSessionService:
    """Instantiate ClawSessionService with mocked __init__."""
    svc = object.__new__(ClawSessionService)

    svc.workspace = tmp_path / "workspace"
    svc.workspace.mkdir(parents=True, exist_ok=True)
    svc.sessions_dir = svc.workspace / "sessions"
    svc.sessions_dir.mkdir(parents=True, exist_ok=True)
    svc.legacy_sessions_dir = tmp_path / "legacy_sessions"
    svc.legacy_sessions_dir.mkdir(parents=True, exist_ok=True)

    svc._storage_manager = MagicMock()
    svc._storage_manager.load_session = AsyncMock(return_value=None)
    svc._storage_manager.save_session = AsyncMock()

    # In-memory caches from parent
    svc._sessions = {}
    svc._app_states = {}
    svc._user_states = {}
    svc._lock = MagicMock()

    return svc


# ---------------------------------------------------------------------------
# _get_session_path
# ---------------------------------------------------------------------------

class TestGetSessionPath:

    def test_constructs_correct_path(self, tmp_path):
        svc = _make_service(tmp_path)
        path = svc._get_session_path("app:user:session")
        assert path.parent == svc.sessions_dir
        assert path.suffix == ".jsonl"
        assert "app_user_session" in path.stem or "app" in path.stem

    def test_replaces_colons(self, tmp_path):
        svc = _make_service(tmp_path)
        path = svc._get_session_path("a:b:c")
        assert ":" not in path.name


# ---------------------------------------------------------------------------
# _get_legacy_session_path
# ---------------------------------------------------------------------------

class TestGetLegacySessionPath:

    def test_constructs_correct_path(self, tmp_path):
        svc = _make_service(tmp_path)
        path = svc._get_legacy_session_path("app:user:session")
        assert path.parent == svc.legacy_sessions_dir
        assert path.suffix == ".jsonl"

    def test_replaces_colons(self, tmp_path):
        svc = _make_service(tmp_path)
        path = svc._get_legacy_session_path("a:b:c")
        assert ":" not in path.name


# ---------------------------------------------------------------------------
# _maybe_migrate
# ---------------------------------------------------------------------------

class TestMaybeMigrate:

    def test_target_exists_noop(self, tmp_path):
        svc = _make_service(tmp_path)
        target = svc.sessions_dir / "test.jsonl"
        target.write_text("existing")
        legacy = svc.legacy_sessions_dir / "test.jsonl"
        legacy.write_text("legacy data")
        svc._maybe_migrate("key", target)
        assert target.read_text() == "existing"
        assert legacy.exists()

    def test_legacy_not_exists_noop(self, tmp_path):
        svc = _make_service(tmp_path)
        target = svc.sessions_dir / "test.jsonl"
        svc._maybe_migrate("key", target)
        assert not target.exists()

    def test_migration_success(self, tmp_path):
        svc = _make_service(tmp_path)
        save_key = "app_user_session"
        target = svc._get_session_path(save_key)
        legacy = svc._get_legacy_session_path(save_key)
        legacy.write_text("legacy data")

        svc._maybe_migrate(save_key, target)
        assert target.exists()
        assert target.read_text() == "legacy data"
        assert not legacy.exists()

    @patch("trpc_agent_sdk.server.openclaw.session_memory._claw_session_service.shutil.move",
           side_effect=OSError("permission denied"))
    def test_migration_failure_logs_error(self, mock_move, tmp_path):
        svc = _make_service(tmp_path)
        save_key = "app_user_session"
        target = svc.sessions_dir / "target.jsonl"
        legacy = svc._get_legacy_session_path(save_key)
        legacy.write_text("legacy data")

        svc._maybe_migrate(save_key, target)
        assert not target.exists()


# ---------------------------------------------------------------------------
# create_session
# ---------------------------------------------------------------------------

class TestCreateSession:

    @patch("trpc_agent_sdk.server.openclaw.session_memory._claw_session_service.set_agent_context")
    @patch("trpc_agent_sdk.server.openclaw.session_memory._claw_session_service.make_memory_key",
           return_value="app:user:sess1")
    async def test_restore_from_disk(self, mock_key, mock_ctx, tmp_path):
        svc = _make_service(tmp_path)
        mock_session = MagicMock()
        mock_session.state = {}
        mock_session.events = []
        svc._storage_manager.load_session = AsyncMock(return_value=mock_session)

        with (
            patch.object(svc, "_maybe_migrate"),
            patch.object(svc, "_set_session"),
            patch.object(svc, "_get_app_state", return_value={}),
            patch.object(svc, "_get_user_state", return_value={}),
            patch.object(svc, "_merge_state", return_value=mock_session),
        ):
            result = await svc.create_session(app_name="app", user_id="user", session_id="sess1")
        assert result is mock_session

    @patch("trpc_agent_sdk.server.openclaw.session_memory._claw_session_service.set_agent_context")
    @patch("trpc_agent_sdk.server.openclaw.session_memory._claw_session_service.make_memory_key",
           return_value="app:user:sess1")
    async def test_create_fresh(self, mock_key, mock_ctx, tmp_path):
        svc = _make_service(tmp_path)
        svc._storage_manager.load_session = AsyncMock(return_value=None)
        fresh = MagicMock()
        fresh.id = "sess1"

        with (
            patch.object(svc, "_maybe_migrate"),
            patch.object(svc, "_set_session"),
            patch(
                "trpc_agent_sdk.server.openclaw.session_memory._claw_session_service.InMemorySessionService.create_session",
                new_callable=AsyncMock,
                return_value=fresh,
            ),
        ):
            result = await svc.create_session(app_name="app", user_id="user", session_id="sess1")
        assert result is not None

    @patch("trpc_agent_sdk.server.openclaw.session_memory._claw_session_service.set_agent_context")
    @patch("trpc_agent_sdk.server.openclaw.session_memory._claw_session_service.make_memory_key",
           return_value="app:user:sess1")
    async def test_with_state_applied_to_loaded(self, mock_key, mock_ctx, tmp_path):
        svc = _make_service(tmp_path)
        mock_session = MagicMock()
        mock_session.state = {"old": "val"}
        mock_session.events = []
        svc._storage_manager.load_session = AsyncMock(return_value=mock_session)

        with (
            patch.object(svc, "_maybe_migrate"),
            patch.object(svc, "_set_session"),
            patch.object(svc, "_get_app_state", return_value={}),
            patch.object(svc, "_get_user_state", return_value={}),
            patch.object(svc, "_update_app_state"),
            patch.object(svc, "_update_user_state"),
            patch.object(svc, "_merge_state", return_value=mock_session),
            patch(
                "trpc_agent_sdk.server.openclaw.session_memory._claw_session_service.extract_state_delta",
                return_value=MagicMock(session_state={"new": "val"}, app_state_delta={}, user_state_delta={}),
            ),
        ):
            result = await svc.create_session(
                app_name="app", user_id="user", session_id="sess1", state={"new": "val"})
        assert result is mock_session


# ---------------------------------------------------------------------------
# get_session
# ---------------------------------------------------------------------------

class TestGetSession:

    async def test_cache_hit(self, tmp_path):
        svc = _make_service(tmp_path)
        cached = MagicMock()

        with patch(
            "trpc_agent_sdk.server.openclaw.session_memory._claw_session_service.InMemorySessionService.get_session",
            new_callable=AsyncMock,
            return_value=cached,
        ):
            result = await svc.get_session(app_name="app", user_id="user", session_id="sess1")
        assert result is cached

    @patch("trpc_agent_sdk.server.openclaw.session_memory._claw_session_service.set_agent_context")
    @patch("trpc_agent_sdk.server.openclaw.session_memory._claw_session_service.make_memory_key",
           return_value="app:user:sess1")
    async def test_cache_miss_load_from_disk(self, mock_key, mock_ctx, tmp_path):
        svc = _make_service(tmp_path)
        loaded = MagicMock()
        svc._storage_manager.load_session = AsyncMock(return_value=loaded)
        call_count = 0

        async def _side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return None
            return loaded

        with (
            patch(
                "trpc_agent_sdk.server.openclaw.session_memory._claw_session_service.InMemorySessionService.get_session",
                new_callable=AsyncMock,
                side_effect=_side_effect,
            ),
            patch.object(svc, "_maybe_migrate"),
            patch.object(svc, "_set_session"),
        ):
            result = await svc.get_session(app_name="app", user_id="user", session_id="sess1")
        assert result is loaded

    @patch("trpc_agent_sdk.server.openclaw.session_memory._claw_session_service.set_agent_context")
    @patch("trpc_agent_sdk.server.openclaw.session_memory._claw_session_service.make_memory_key",
           return_value="app:user:sess1")
    async def test_not_found(self, mock_key, mock_ctx, tmp_path):
        svc = _make_service(tmp_path)
        svc._storage_manager.load_session = AsyncMock(return_value=None)

        with (
            patch(
                "trpc_agent_sdk.server.openclaw.session_memory._claw_session_service.InMemorySessionService.get_session",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch.object(svc, "_maybe_migrate"),
        ):
            result = await svc.get_session(app_name="app", user_id="user", session_id="sess1")
        assert result is None


# ---------------------------------------------------------------------------
# update_session
# ---------------------------------------------------------------------------

class TestUpdateSession:

    @patch("trpc_agent_sdk.server.openclaw.session_memory._claw_session_service.get_memory_key",
           return_value="app:user:sess1")
    async def test_persists_to_disk(self, mock_key, tmp_path):
        svc = _make_service(tmp_path)
        session = MagicMock()

        with patch(
            "trpc_agent_sdk.server.openclaw.session_memory._claw_session_service.InMemorySessionService.update_session",
            new_callable=AsyncMock,
        ):
            await svc.update_session(session)
        svc._storage_manager.save_session.assert_called_once()

    @patch("trpc_agent_sdk.server.openclaw.session_memory._claw_session_service.get_memory_key",
           return_value="app:user:sess1")
    async def test_calls_super_update(self, mock_key, tmp_path):
        svc = _make_service(tmp_path)
        session = MagicMock()

        with patch(
            "trpc_agent_sdk.server.openclaw.session_memory._claw_session_service.InMemorySessionService.update_session",
            new_callable=AsyncMock,
        ) as mock_super:
            await svc.update_session(session)
        mock_super.assert_called_once_with(session)
