#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2025 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Shared pytest setup and safe Champion prompt restoration."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import pytest_asyncio

_HERE = Path(__file__).resolve().parent
_LOOP_ROOT = _HERE.parent
_REPO_ROOT = _LOOP_ROOT.parents[2]

for path in (str(_REPO_ROOT), str(_LOOP_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)


_BASELINE_CHAMPION = """# System Prompt (Champion)

你是一个数学问答助手。请用中文回答用户的算术问题。

## 输出格式

请直接给出最终数字答案，无需展示步骤。

## 工具使用

当前未配置工具。

<!-- FAKE_CONTROLS
ADD_STEPS=false
MEMORIZE_TRAIN=false
-->
"""


@pytest.fixture(scope="session")
def loop_root() -> Path:
    return _LOOP_ROOT


@pytest_asyncio.fixture(autouse=True)
async def _restore_champion_prompt():
    """Restore through the production TargetPrompt abstraction."""

    from trpc_agent_sdk.evaluation import TargetPrompt

    champion = _LOOP_ROOT / "prompts" / "system.md"
    target = TargetPrompt().add_path("system", str(champion))
    await target.write_all({"system": _BASELINE_CHAMPION})
    yield
    await target.write_all({"system": _BASELINE_CHAMPION})
