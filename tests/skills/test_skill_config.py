# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

from unittest.mock import MagicMock

from trpc_agent_sdk.skills._constants import SKILL_CONFIG_KEY
from trpc_agent_sdk.skills._constants import SkillLoadModeNames
from trpc_agent_sdk.skills._skill_config import DEFAULT_SKILL_CONFIG
from trpc_agent_sdk.skills._skill_config import get_skill_config
from trpc_agent_sdk.skills._skill_config import get_skill_load_mode
from trpc_agent_sdk.skills._skill_config import is_exist_skill_config
from trpc_agent_sdk.skills._skill_config import set_skill_config


def test_get_skill_config_uses_metadata_default():
    agent_ctx = MagicMock()
    agent_ctx.get_metadata = MagicMock(return_value=DEFAULT_SKILL_CONFIG)
    assert get_skill_config(agent_ctx) == DEFAULT_SKILL_CONFIG


def test_set_skill_config_writes_metadata():
    agent_ctx = MagicMock()
    config = {"skill_processor": {"load_mode": "session"}}
    set_skill_config(agent_ctx, config)
    agent_ctx.with_metadata.assert_called_once_with(SKILL_CONFIG_KEY, config)


def test_get_skill_load_mode_fallback_turn_on_invalid():
    agent_ctx = MagicMock()
    agent_ctx.get_metadata = MagicMock(return_value={"skill_processor": {"load_mode": "bad"}})
    ctx = MagicMock()
    ctx.agent_context = agent_ctx
    assert get_skill_load_mode(ctx) == SkillLoadModeNames.TURN.value


def test_is_exist_skill_config_checks_key():
    agent_ctx = MagicMock()
    agent_ctx.metadata = {SKILL_CONFIG_KEY: {}}
    assert is_exist_skill_config(agent_ctx) is True
