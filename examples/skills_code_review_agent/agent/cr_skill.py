# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""CR Skill integration for the code review agent.

Wraps the code-review Skill as a SkillToolSet, allowing the Agent to
load rules on demand and run scripts in an isolated sandbox environment.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

from trpc_agent_sdk.code_executors import ContainerCodeExecutor, UnsafeLocalCodeExecutor
from trpc_agent_sdk.skills import SkillToolSet


def get_skill_path() -> str:
    """Get the absolute path to the code-review skill directory."""
    return str(Path(__file__).resolve().parent.parent / "skills" / "code-review")


def create_skill_toolset(
    sandbox_type: str = "local",
    timeout: int = 30,
    max_output: int = 1_048_576,
) -> SkillToolSet:
    """Create a SkillToolSet for the code-review skill.

    Args:
        sandbox_type: Sandbox executor type ("local", "container", "cube").
        timeout: Max execution time in seconds for each script.
        max_output: Max output size in bytes.

    Returns:
        A configured SkillToolSet instance.
    """
    skill_path = get_skill_path()

    # Select sandbox executor
    if sandbox_type == "container":
        code_executor = ContainerCodeExecutor(
            timeout=timeout,
            max_output_size=max_output,
            env_whitelist=["PATH", "HOME", "PYTHONPATH", "WORKSPACE_DIR"],
        )
    else:
        code_executor = UnsafeLocalCodeExecutor(
            timeout=timeout,
            max_output_size=max_output,
        )

    return SkillToolSet(
        skill_dir=skill_path,
        code_executor=code_executor,
    )


# Global singleton for easy import
_default_skill_set: Optional[SkillToolSet] = None


def get_skill_toolset() -> SkillToolSet:
    """Get or create the default skill toolset (local sandbox)."""
    global _default_skill_set
    if _default_skill_set is None:
        _default_skill_set = create_skill_toolset()
    return _default_skill_set