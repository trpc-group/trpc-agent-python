# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""沙箱工厂函数。

提供 build_runtime 工厂函数，根据 backend 类型创建对应的 SandboxProvider。
支持环境变量 CODE_REVIEW_SANDBOX_BACKEND 覆盖默认后端。
"""

import os

from sandbox.base import SandboxProvider
from sandbox.fake import FakeSandbox


def build_runtime(backend: str = None) -> SandboxProvider:
    """创建沙箱运行时实例。

    Args:
        backend: 沙箱后端类型（fake/local/container/cube）
                如果为 None，从环境变量 CODE_REVIEW_SANDBOX_BACKEND 读取
                如果环境变量也不存在，默认使用 fake

    Returns:
        SandboxProvider: 对应的沙箱实例

    Raises:
        ValueError: 如果 backend 类型未知

    后端类型：
    - fake：默认，无依赖，通过 trigger 关键字模拟边界情况
    - local：开发 fallback，标注不隔离，直接执行脚本
    - container：生产真后端，基于 Docker 容器隔离
    - cube：远端真后端，基于 Cube/E2B 沙箱
    """
    # 优先使用参数，其次环境变量，最后默认 fake
    if backend is None:
        backend = os.getenv("CODE_REVIEW_SANDBOX_BACKEND", "fake")

    # 标准化 backend 名称（转小写）
    backend = backend.lower()

    # 根据 backend 类型创建对应的实例
    if backend == "fake":
        return FakeSandbox()

    if backend == "local":
        # 延迟 import，避免不需要时导入
        from sandbox.local import LocalSandbox

        return LocalSandbox()

    if backend == "container":
        # 延迟 import，避免不需要时导入
        from sandbox.container import ContainerSandbox

        return ContainerSandbox()

    if backend == "cube":
        # 延迟 import，避免不需要时导入
        from sandbox.cube import CubeSandbox

        return CubeSandbox()

    # 未知 backend 类型
    raise ValueError(f"未知的沙箱后端: {backend}. 支持: fake, local, container, cube")
