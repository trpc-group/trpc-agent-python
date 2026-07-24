# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Session / Memory / Summary 多后端回放一致性测试框架。

用同一组标准化 Agent 轨迹驱动 InMemory / SQLite / Redis 三个后端,经四段管线
``load → replay_case → 后端中立快照 → compare → report`` 比较事件、状态、长期记忆
与会话摘要的一致性。

归一化策略:对 timestamp、自动生成 id、invocation_id 等非业务字段用占位符替换
(保留字段存在性,优于直接删除),剥离 ``temp:`` 临时状态,memory 结果按确定性键
排序,JSON 统一 ``sort_keys`` 序列化以消除字段顺序差异。

summary 比较策略:采用 SDK 确定性模型(覆写 ``_compress_session_to_summary`` 换掉
LLM,跑真实压缩流程)生成确定性摘要,再做三分比较 —— 文本走分词集合 Jaccard 语义
比较(纯标准库,无 embedding 依赖),元数据(version / session_id / supersedes)
严格相等,并按 session_id 匹配后专项检测 loss / overwrite / affiliation 三类故障;
因 SDK 无持久 version 字段,形式化为「生成序号 + supersedes 链」可观测修订状态。

允许差异 allowed_diff:JSONPath 精确匹配 + 强制 reason,并设每 case 条数与占比上限
防滥用,绝不无脑忽略。

后端接入:轻量模式默认 InMemory vs SQLite(≤30s),Redis / MySQL 经环境变量启用,
不可用时 ``pytest.skip``,并提供 sqlite / mock 跳过策略。

创新点:在所有公开方案的快照层注入之外,新增端到端后端数据注入(直接改 SQL 行 /
Redis key 后重读),真正验证 harness 对后端数据漂移的感知能力,兑现「后端实现质量
基准」的立意。发现的 SDK 不一致只在报告中列出,不在本 PR 改生产代码。
"""

__all__: list[str] = []
