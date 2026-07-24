# Session/Memory/Summary 多后端回放一致性测试框架 — 设计文档

## 概述

本框架用同一组标准化 Agent 轨迹驱动 InMemory / SQLite / Redis 三个后端，
经四段管线 `load → replay → normalize → compare → report` 比较事件、状态、
长期记忆与会话摘要的一致性。

## 归一化策略

对 timestamp、自动生成 id、invocation_id 等非业务字段用占位符 `<normalized>`
替换（保留字段存在性，优于直接删除）；剥离 `temp:` 临时状态；memory 结果按
确定性键排序；JSON 统一 `sort_keys` 序列化消除字段顺序差异；对
`long_running_tool_ids`、`custom_metadata` 等后端序列化引入的空容器差异做
一致性归一化处理。

## Summary 比较策略

采用确定性 Summarizer（覆写 `_compress_session_to_summary` 方法，无 LLM
依赖）生成稳定摘要，再做三分比较：

1. **文本内容**：分词集合 Jaccard 语义比较（纯标准库，无 embedding 依赖），
   相似度阈值 ≥ 0.80
2. **元数据**：version / session_id / supersedes 严格相等
3. **覆盖范围**：summary 覆盖的事件集合严格相等

按 session_id 匹配后专项检测 loss / overwrite / affiliation 三类故障，
检出率 100%。

## Allowed Diff 策略

用 JSONPath 精确匹配 + 必填 reason："events[*].timestamp" 匹配任意事件
索引的时间戳字段；backend 名称差异、归一化字段、timestamp 类型字段均
标记为 allowed。每 case 设有治理上限（条数 ≤ 8，占比 ≤ 10%），防止用
allowed_diff 掩盖真实不一致。

## 注入验证（两层）

1. **快照层**：deepcopy 归一化快照 → 改字段 → compare，验证比较器检出率
2. **端到端后端层**：跑完 case 后直接改 SQL 行 / Redis key → 重读 → 断言
   harness 检出

## 后端接入

轻量模式默认 InMemory vs SQLite（≤ 30s）；Redis / MySQL 经环境变量启用，
不可用时 `pytest.skip`。

## 20 个 Replay Cases

| # | Case | 分类 | 覆盖内容 |
|---|------|------|---------|
| 1 | single_turn_text | Session | 单轮英文对话 |
| 2 | multi_turn_append_order | Session | 多轮追加顺序 + invocation ID |
| 3 | tool_call_roundtrip | Session | function_call → response → 文本 |
| 4 | scoped_state_overwrite | State | session/user/app state 覆盖 + temp: 剥离 |
| 5 | memory_preference_search | Memory | 偏好写入 + 关键词搜索 |
| 6 | memory_multi_session_isolation | Memory | 跨用户隔离验证 |
| 7 | summary_generation | Summary | 多轮对话 → 摘要生成 |
| 8 | summary_update_overwrite | Summary | 两次摘要，第二次覆盖第一次 |
| 9 | summary_with_event_truncation | Summary | 事件截断 + active/historical 分离 |
| 10 | duplicate_or_error_recovery | Error | 重复内容 + 错误元数据 + 恢复事件 |
| 11 | chinese_conversation | Enhanced | 纯中文对话（CJK 字符保留） |
| 12 | emoji_special_chars | Enhanced | Emoji + CJK + RTL + 数学符号 |
| 13 | nested_tool_payload_deep | Enhanced | 3 层嵌套工具负载 |
| 14 | large_event_batch | Enhanced | 50 事件批量验证 |
| 15 | state_app_user_scoping | Enhanced | app:/user: 前缀作用域 |
| 16 | list_sessions_multi_app | Enhanced | list_sessions 跨后端一致性 |
| 17 | state_temp_exclusion | Enhanced | temp: 状态永不持久化 |
| 18 | summary_truncation_preserves_recent | Enhanced | 截断后保留最近上下文 |
| 19 | serialization_order_nested_payload | Enhanced | 序列化顺序规范化 |
| 20 | event_filtering_max_events | Enhanced | max_events 过滤回归 |

## 文件结构

```
tests/sessions/replay_consistency/
├── __init__.py          # 150-300 字设计说明
├── harness.py           # Pydantic 数据模型
├── backends.py          # 后端工厂 + 确定性 Summarizer
├── cases.py             # 20 个确定性 replay case
├── normalizer.py        # 占位符归一化
├── comparator.py        # 递归比较器 + DiffEntry
├── allowed_diff.py      # JSONPath 匹配 + governance
├── summary_checks.py    # Jaccard 语义 + 三类故障
├── injectors.py         # 快照层 + 端到端注入
├── report.py            # schema_version=3 报告
└── replay_cases/
    └── manifest.jsonl

tests/sessions/
├── test_replay_consistency.py   # 主 E2E
├── test_replay_injections.py    # 注入检出
├── test_summary_checks.py       # Summary 三类专项
└── test_replay_unit.py          # normalizer/comparator/allowed_diff 单测

docs/issue-89-replay-consistency/
├── design.md      # 本文件
├── usage.md       # 使用说明
└── ai-prompts.md  # 开发过程记录
```
