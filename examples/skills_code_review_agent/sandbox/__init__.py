# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""沙箱执行后端模块（Fake/Local/Container/Cube）。

提供四种沙箱实现：
- Fake：默认，无依赖，通过 trigger 关键字模拟边界情况
- Local：开发 fallback，标注不隔离，直接执行脚本
- Container：生产真后端，基于 Docker 容器隔离
- Cube：远端真后端，基于 Cube/E2B 沙箱

工厂函数：build_runtime(backend) -> SandboxProvider
"""

from .base import SandboxProvider
from .factory import build_runtime

__all__ = [
    "SandboxProvider",
    "build_runtime",
]
