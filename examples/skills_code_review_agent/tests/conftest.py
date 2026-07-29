#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

"""Pytest configuration shared by code-review Agent test layers."""

from __future__ import annotations

from _pytest.config import Config


def pytest_configure(config: Config) -> None:
    """注册可选 container 与 real_llm 标记，避免环境门禁测试产生未知标记告警。"""

    config.addinivalue_line("markers", "container: requires a running Docker daemon")
    config.addinivalue_line("markers", "real_llm: requires an explicitly configured real model key")
