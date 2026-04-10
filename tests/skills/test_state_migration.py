# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for legacy skill state migration."""

from unittest.mock import Mock

from trpc_agent_sdk.skills._constants import SKILL_DOCS_STATE_KEY_PREFIX
from trpc_agent_sdk.skills._constants import SKILL_LOADED_STATE_KEY_PREFIX
from trpc_agent_sdk.skills._state_keys import docs_key
from trpc_agent_sdk.skills._state_keys import loaded_key
from trpc_agent_sdk.skills._state_migration import SKILLS_LEGACY_MIGRATION_STATE_KEY
from trpc_agent_sdk.skills._state_migration import maybe_migrate_legacy_skill_state


def _build_ctx(*, state=None, delta=None, agent_name: str = "agent-a") -> Mock:
    ctx = Mock()
    ctx.session = Mock()
    ctx.session.state = dict(state or {})
    ctx.session.events = []
    ctx.actions = Mock()
    ctx.actions.state_delta = dict(delta or {})
    ctx.agent = Mock()
    ctx.agent.name = agent_name
    return ctx


class TestMaybeMigrateLegacySkillState:
    def test_migrates_loaded_legacy_key_to_temp(self):
        legacy_key = f"{SKILL_LOADED_STATE_KEY_PREFIX}demo-skill"
        temp_key = loaded_key("agent-a", "demo-skill")
        ctx = _build_ctx(state={legacy_key: "1"})

        maybe_migrate_legacy_skill_state(ctx)

        assert ctx.actions.state_delta[SKILLS_LEGACY_MIGRATION_STATE_KEY] is True
        assert ctx.actions.state_delta[temp_key] == "1"
        assert ctx.actions.state_delta[legacy_key] is None

    def test_migrates_docs_legacy_key_to_temp(self):
        legacy_key = f"{SKILL_DOCS_STATE_KEY_PREFIX}demo-skill"
        temp_key = docs_key("agent-a", "demo-skill")
        value = '["README.md"]'
        ctx = _build_ctx(state={legacy_key: value})

        maybe_migrate_legacy_skill_state(ctx)

        assert ctx.actions.state_delta[SKILLS_LEGACY_MIGRATION_STATE_KEY] is True
        assert ctx.actions.state_delta[temp_key] == value
        assert ctx.actions.state_delta[legacy_key] is None

    def test_existing_scoped_key_skips_copy_and_only_clears_legacy(self):
        legacy_key = f"{SKILL_LOADED_STATE_KEY_PREFIX}demo-skill"
        temp_key = loaded_key("agent-a", "demo-skill")
        ctx = _build_ctx(state={legacy_key: "legacy", temp_key: "existing"})

        maybe_migrate_legacy_skill_state(ctx)

        assert ctx.actions.state_delta[SKILLS_LEGACY_MIGRATION_STATE_KEY] is True
        assert ctx.actions.state_delta[legacy_key] is None
        assert temp_key not in ctx.actions.state_delta

    def test_migration_is_idempotent_when_marker_exists(self):
        legacy_key = f"{SKILL_LOADED_STATE_KEY_PREFIX}demo-skill"
        ctx = _build_ctx(state={legacy_key: "1", SKILLS_LEGACY_MIGRATION_STATE_KEY: True})

        maybe_migrate_legacy_skill_state(ctx)

        assert ctx.actions.state_delta == {}
