# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

from trpc_agent_sdk.skills._constants import SKILL_FILE
from trpc_agent_sdk.skills._constants import SKILL_LOAD_MODE_VALUES
from trpc_agent_sdk.skills._constants import SKILL_TOOLS_NAMES
from trpc_agent_sdk.skills._constants import SkillLoadModeNames
from trpc_agent_sdk.skills._constants import SkillToolsNames


def test_skill_file_constant():
    assert SKILL_FILE == "SKILL.md"


def test_skill_tools_names_matches_enum():
    assert SKILL_TOOLS_NAMES == [item.value for item in SkillToolsNames]


def test_skill_load_mode_values_matches_enum():
    assert SKILL_LOAD_MODE_VALUES == [item.value for item in SkillLoadModeNames]
