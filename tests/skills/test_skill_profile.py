# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

import pytest

from trpc_agent_sdk.skills._constants import SkillProfileNames
from trpc_agent_sdk.skills._skill_profile import SkillProfileFlags


def test_normalize_profile():
    assert SkillProfileFlags.normalize_profile("knowledge_only") == SkillProfileNames.KNOWLEDGE_ONLY.value
    assert SkillProfileFlags.normalize_profile("unknown") == SkillProfileNames.FULL.value


def test_preset_flags_knowledge_only():
    flags = SkillProfileFlags.preset_flags("knowledge_only")
    assert flags.has_knowledge_tools() is True
    assert flags.requires_execution_tools() is False


def test_resolve_flags_with_forbidden_tool():
    flags = SkillProfileFlags.resolve_flags("full", forbidden_tools=["skill_write_stdin"])
    assert flags.exec is True
    assert flags.write_stdin is False


def test_validate_dependency_error():
    flags = SkillProfileFlags(run=False, exec=True)
    with pytest.raises(ValueError, match="requires"):
        flags.validate()


def test_without_interactive_execution():
    flags = SkillProfileFlags.resolve_flags("full")
    narrowed = flags.without_interactive_execution()
    assert narrowed.run is True
    assert narrowed.exec is False
    assert narrowed.poll_session is False
