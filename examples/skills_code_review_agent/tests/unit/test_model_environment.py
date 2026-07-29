#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

"""Unit tests for the narrowly scoped real-model environment loader."""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from code_review.model_environment import load_model_environment  # noqa: E402


def test_load_model_environment_only_reads_allowed_values_and_preserves_process_priority(
    tmp_path: Path,
) -> None:
    """验证 .env 仅向 real 模型提供白名单变量，且显式进程环境优先。"""

    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text(
        "TRPC_AGENT_API_KEY=file-key\n"
        "TRPC_AGENT_BASE_URL=https://example.invalid/v1\n"
        "TRPC_AGENT_MODEL_NAME=sample-model\n"
        "UNRELATED_SECRET=must-not-load\n",
        encoding="utf-8",
    )

    loaded = load_model_environment(
        dotenv_path,
        environ={"TRPC_AGENT_MODEL_NAME": "process-model", "PATH": "ignored"},
    )

    assert loaded == {
        "TRPC_AGENT_API_KEY": "file-key",
        "TRPC_AGENT_BASE_URL": "https://example.invalid/v1",
        "TRPC_AGENT_MODEL_NAME": "process-model",
    }
