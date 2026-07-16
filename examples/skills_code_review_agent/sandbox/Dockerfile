# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Dockerfile for Code Review Agent Sandbox Container.

这个 Dockerfile 创建一个用于代码审查 Agent 的沙箱容器镜像。
包含 Python 3.12 运行时和常用的代码检查工具（ruff、semgrep、bandit）。
"""

FROM python:3.12-slim

# 设置工作目录
WORKDIR /workspace

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

# 安装 Python 代码检查工具
RUN pip install --no-cache-dir \
    ruff \
    semgrep \
    bandit \
    safety

# 创建非 root 用户（与 ContainerSandbox 中的 --user 65532 对应）
RUN useradd -m -u 65532 sandbox

# 设置工作目录权限
RUN chown -R sandbox:sandbox /workspace

# 切换到非 root 用户
USER sandbox

# 默认命令
CMD ["python", "--version"]
