# 回放一致性测试框架

tRPC-Agent 支持 InMemory、SQL、Redis 三种 Session/Memory 后端。生产环境常先用 InMemory 开发，再切换到 SQL 或 Redis。不同后端在同一条 Agent 轨迹下保存的事件顺序、state、memory 或 summary 不一致时，会导致回放错乱、上下文丢失、长期记忆污染、摘要覆盖错误等问题。

该框架提供一组标准化输入轨迹驱动多个后端，自动生成差异报告，定位不一致的字段路径和具体值。它既是测试工具，也是后端实现质量的基准。

## 架构

核心组件：

- **ReplayCase / ReplayStep**：JSONL 文件定义标准输入轨迹
- **ReplayHarness**：解析 JSONL 步骤，并行驱动两个后端执行，收集原始结果
- **DiffEngine**：四维度比较（events / state / memory / summary），产出 DiffReport
- **Normalizer**：时间戳截断到秒级、ID 按内容重赋、`is_final_response` 排除

基于 [tests/sessions/conftest.py](../../../tests/sessions/conftest.py) 和 [tests/sessions/test_replay_consistency.py](../../../tests/sessions/test_replay_consistency.py) 实现。

## Replay Case

| # | Case 名称 | 类型 | 说明 |
|---|---|---|---|
| 1 | `single_turn` | 正常 | 单轮 user → agent 对话 |
| 2 | `multi_turn` | 正常 | 3 轮交替对话 |
| 3 | `tool_call` | 正常 | function_call + function_response |
| 4 | `state_update` | 正常 | 多次 state_delta 写入覆盖 |
| 5 | `memory_rw` | 正常 | store_session + search_memory |
| 6 | `summary_gen` | 正常 | 22 轮对话触发摘要 |
| 7 | `summary_truncate` | 已知不一致 | 两层验证：元数据严格 + 单端语义 |
| 8 | `exception_recovery` | 注入 | inject_skip_append 模拟写入失败 |
| 9 | `injected_event_order` | 注入 | inject_reorder_events 交换事件 |
| 10 | `injected_summary_session` | 注入 | inject_summary_session_id 篡改归属 |

## 归一化策略

跨后端比较前需去除非业务差异：

| 字段 | 处理方式 |
|------|---------|
| event.timestamp | 截断到秒级精度（int） |
| event.id | 按内容排序后重赋稳定 ID |
| state_delta | 统一 JSON key 排序 |
| is_final_response | 排除（computed property，序列化路径不同） |

三类差异明确允许，写入 allowed_diff：

1. 后端自动生成的 `invocation_id`
2. 不同后端的 `save_key` 格式差异
3. Summary 压缩后事件总数的差异（InMemory 在内存中压缩事件列表，SQL 的 get_session 从事件表重新读取全部原始事件）

## Summary 比较策略

分两层：

1. **摘要元数据**：`session_id`、`summary_text`、`original_event_count`、`compressed_event_count` 跨后端严格一致——这是回放正确性的核心
2. **单后端独立验证**：摘要文本非空、压缩已生效（compressed < original）、压缩后追加的新事件已保留

摘要文本与事件列表的精确分界允许因后端存储模型不同而异。

## 后端接入

| 模式 | 后端 A | 后端 B | 触发条件 |
|------|--------|--------|----------|
| 轻量模式（默认） | InMemorySessionService | SqlSessionService(SQLite) | 无条件 |
| SQL 集成模式 | InMemorySessionService | SqlSessionService(MySQL) | TEST_MYSQL_URL（规划中，尚未实现） |
| Redis 集成模式 | InMemorySessionService | RedisSessionService | TEST_REDIS_URL（规划中，尚未实现） |

三个后端的 `SessionServiceABC` 接口一致，新增后端只需实现该接口即可接入框架。
