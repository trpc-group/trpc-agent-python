#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

"""Load only explicit real-model configuration from the project dotenv file."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path


_MODEL_ENVIRONMENT_KEYS = (
    "TRPC_AGENT_API_KEY",
    "TRPC_AGENT_BASE_URL",
    "TRPC_AGENT_MODEL_NAME",
)


def _dotenv_values(path: Path) -> dict[str, str]:
    """解析受控 .env 中的模型白名单变量；格式错误或缺失文件均不会泄漏内容。"""

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return {}

    values: dict[str, str] = {}
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", maxsplit=1)
        key = key.strip()
        if key not in _MODEL_ENVIRONMENT_KEYS:
            continue
        normalized_value = value.strip()
        if (
            len(normalized_value) >= 2
            and normalized_value[0] == normalized_value[-1]
            and normalized_value[0] in {"'", '"'}
        ):
            normalized_value = normalized_value[1:-1]
        if normalized_value:
            values[key] = normalized_value
    return values


def load_model_environment(
    dotenv_path: Path,
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """返回 real 模型所需白名单配置，显式进程环境优先且不修改宿主环境。"""

    process_values = os.environ if environ is None else environ
    dotenv_values = _dotenv_values(dotenv_path)
    resolved: dict[str, str] = {}
    for key in _MODEL_ENVIRONMENT_KEYS:
        value = process_values.get(key) or dotenv_values.get(key)
        if isinstance(value, str) and value:
            resolved[key] = value
    return resolved


__all__ = ["load_model_environment"]
