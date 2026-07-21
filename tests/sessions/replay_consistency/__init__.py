# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Session/Memory/Summary 多后端回放一致性测试框架。

设计说明（150-300字）：

本框架用同一组标准化 Agent 轨迹驱动 InMemory / SQLite / Redis 三个后端，
经四段管线 load → replay → normalize → compare → report 比较事件、状态、
长期记忆与会话摘要的一致性。

归一化策略：对 timestamp、自动生成 id、invocation_id 等非业务字段用占位符
替换（保留字段存在性），剥离 temp: 临时状态，memory 结果按确定性键排序，
JSON 统一 sort_keys 消除字段顺序差异。

Summary 比较策略：采用确定性 Summarizer（覆写压缩方法，无 LLM 依赖）
生成稳定摘要，再做三分比较 —— 文本走分词集合 Jaccard 语义比较（纯标准库，
无 embedding），元数据（version/session_id/supersedes）严格相等，按 session_id
匹配后专项检测 loss/overwrite/affiliation 三类故障。

allowed_diff 用 JSONPath 精确匹配 + 必填 reason + 每 case 条数与占比上限治理，
支持 [*] 下标通配。检出验证分两层 —— 快照层 deepcopy 改字段（对齐其他方案）
和端到端后端数据注入（直接改 SQL 行 / Redis key 后重读），真正验证 harness
对后端数据漂移的感知能力。

后端接入：轻量模式默认 InMemory vs SQLite（≤30s），Redis/MySQL 经环境变量
启用，不可用时 pytest.skip。
"""

from .harness import ReplayCase
from .harness import ReplaySnapshot
from .harness import EventSpec
from .harness import MemoryQuerySpec
from .harness import SummaryPoint
from .harness import DiffEntry
from .harness import BackendStatus
from .harness import Report

__all__ = [
    "ReplayCase",
    "ReplaySnapshot",
    "EventSpec",
    "MemoryQuerySpec",
    "SummaryPoint",
    "DiffEntry",
    "BackendStatus",
    "Report",
]
