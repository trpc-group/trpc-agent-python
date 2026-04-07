# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""File-backed session service for trpc_claw agents.

"""

from __future__ import annotations

import copy
import shutil
import uuid
from pathlib import Path
from typing import Any
from typing import Optional
from typing_extensions import override

from nanobot.utils.helpers import ensure_dir
from nanobot.utils.helpers import safe_filename
from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.sessions import Session
from trpc_agent_sdk.sessions import SessionServiceConfig
from trpc_agent_sdk.sessions import extract_state_delta

from ..config import ClawConfig
from ..storage import get_memory_key
from ..storage import make_memory_key
from ..storage import set_agent_context
from ._claw_summarizer import ClawSummarizerSessionManager


class ClawSessionService(InMemorySessionService):
    """trpc_claw session service that stores :class:`Session` objects.

    Sessions are persisted as JSONL files under ``workspace/sessions/``.
    Args:
        workspace: Root directory; session files live in ``workspace/sessions/``.
        summarizer_manager: Optional summarizer manager (forwarded to parent).
        session_config: Optional session service configuration.
    """

    def __init__(
        self,
        config: ClawConfig,
        summarizer_manager: ClawSummarizerSessionManager,
        session_config: Optional[SessionServiceConfig] = None,
    ) -> None:
        """Initialize the claw session service.

        Args:
            config: The config.
            summarizer_manager: The summarizer manager.
            session_config: The session config.
        """
        super().__init__(
            summarizer_manager=summarizer_manager,
            session_config=session_config,
        )
        self.workspace = config.workspace
        self.sessions_dir = ensure_dir(self.workspace / "sessions")
        self.legacy_sessions_dir = Path(config.runtime.legacy_sessions_dir)
        if not self.legacy_sessions_dir.exists():
            self.legacy_sessions_dir.mkdir(parents=True, exist_ok=True)

        self._storage_manager = summarizer_manager.storage_manager

    # ------------------------------------------------------------------
    # Public API overrides
    # ------------------------------------------------------------------

    @override
    async def create_session(
        self,
        *,
        app_name: str,
        user_id: str,
        state: Optional[dict[str, Any]] = None,
        session_id: Optional[str] = None,
        agent_context: Optional[AgentContext] = None,
    ) -> Session:
        """Create (or restore) a :class:`Session`.

        If a JSONL file already exists for *session_id*, the session is
        restored from disk.  Otherwise the parent creates a fresh session which
        is immediately wrapped as a ``Session``.
        """
        resolved_id = (session_id.strip() if session_id and session_id.strip() else str(uuid.uuid4()))
        save_key = make_memory_key(app_name, user_id, resolved_id)
        session_path = self._get_session_path(save_key)

        set_agent_context(agent_context)
        # ── try to restore from disk ──────────────────────────────────────────
        self._maybe_migrate(save_key, session_path)
        loaded = await self._storage_manager.load_session(session_path, app_name, user_id, resolved_id)

        if loaded is not None:
            if state:
                state_deltas = extract_state_delta(state)
                loaded.state.update(state_deltas.session_state)
                self._update_app_state(app_name, state_deltas.app_state_delta)
                self._update_user_state(app_name, user_id, state_deltas.user_state_delta)
            # set the llm events for the session
            loaded.set_llm_events()
            self._set_session(app_name, user_id, resolved_id, loaded)
            logger.debug("Restored Session %s from disk", save_key)

            app_state = self._get_app_state(app_name)
            user_state = self._get_user_state(app_name, user_id)
            cloned = copy.deepcopy(loaded)
            return self._merge_state(app_state, user_state, cloned)

        # ── create a fresh session via parent, then wrap it ───────────────────
        session = await super().create_session(
            app_name=app_name,
            user_id=user_id,
            state=state,
            session_id=resolved_id,
            agent_context=agent_context,
        )

        session.save_key = save_key

        # Retrieve the internal (non-copied) session stored by the parent and
        # replace it with a ClawSession that carries the same data.
        self._set_session(app_name, user_id, session.id, session)

        return copy.deepcopy(session)

    @override
    async def get_session(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
        agent_context: Optional[AgentContext] = None,
    ) -> Optional[Session]:
        """Return a :class:`ClawSession`, loading from disk on a cache miss.

        Steps:
        1. Check the in-memory cache via the parent implementation.
        2. On miss, try loading the JSONL file.
        3. Register the loaded session in the in-memory cache.
        4. Return ``None`` only if neither source has the session.
        """
        # ── fast path: in-memory cache hit ────────────────────────────────────
        session = await super().get_session(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
            agent_context=agent_context,
        )

        if session is not None:
            return session

        # ── slow path: file load ──────────────────────────────────────────────
        save_key = make_memory_key(app_name, user_id, session_id)
        session_path = self._get_session_path(save_key)
        self._maybe_migrate(save_key, session_path)
        set_agent_context(agent_context)
        loaded = await self._storage_manager.load_session(session_path, app_name, user_id, session_id)
        if loaded is None:
            return None

        # Register in in-memory cache so subsequent calls hit the fast path.
        self._set_session(app_name, user_id, session_id, loaded)
        logger.debug("Loaded Session %s from disk on cache miss", save_key)

        # Delegate back to parent to apply app/user state merge + deep copy.
        return await super().get_session(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
            agent_context=agent_context,
        )

    @override
    async def update_session(self, session: Session) -> None:
        """Update the in-memory session and persist changes to disk."""
        await super().update_session(session)
        save_key = get_memory_key(session)
        await self._storage_manager.save_session(self._get_session_path(save_key), session)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_session_path(self, save_key: str) -> Path:
        safe_key = safe_filename(save_key.replace(":", "_"))
        return self.sessions_dir / f"{safe_key}.jsonl"

    def _get_legacy_session_path(self, save_key: str) -> Path:
        safe_key = safe_filename(save_key.replace(":", "_"))
        return self.legacy_sessions_dir / f"{safe_key}.jsonl"

    def _maybe_migrate(self, save_key: str, target: Path) -> None:
        """Move a legacy session file to *target* if one exists and target is absent."""
        if target.exists():
            return
        legacy = self._get_legacy_session_path(save_key)
        if not legacy.exists():
            return
        try:
            shutil.move(str(legacy), str(target))
            logger.info("Migrated session %s from legacy path", save_key)
        except Exception as exc:  # pylint: disable=broad-except
            logger.error("Failed to migrate session %s: %s", save_key, exc)
