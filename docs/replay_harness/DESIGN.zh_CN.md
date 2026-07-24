# Session / Memory / Summary 回放一致性测试框架 — 设计说明

## 目标

验证 InMemory、SQL、Redis 三种后端的 Session、Memory 和 Summary 服务在相同操作序列下是否产出一致的结果。该框架使用标准化输入轨迹驱动多个后端，并生成结构化差异报告。

## 架构

```
replay_cases/*.json   →  ReplayEngine  →  BackendResult
                             │
                    ┌─────────┴─────────┐
              后端 A              后端 B
              (InMemory)          (SQL / Redis)
                    │                    │
              BackendResult        BackendResult
                    │                    │
                    └────────┬───────────┘
                             ▼
                      _normalizer.py
                      （去除时间戳、ID，排序键）
                             │
                    NormalizedResult A + B
                             │
                             ▼
                      _comparator.py
                      （逐对比较事件、状态、记忆、摘要）
                             │
                             ▼
                      DiffReport（JSON 差异报告）
```

每个回放用例是一个 JSON 文件，描述一系列操作：
`create_session`、`append_event`、`update_state`、`inject_summary`、
`store_memory`、`search_memory`、`read_back`。

ReplayEngine 将相同的操作序列在两个后端上并行执行，收集原始结果后进行归一化和比较。

## 归一化策略

对后端间必然不同的字段，在比较前进行归一化：

| 字段                      | 策略                                          |
|---------------------------|-----------------------------------------------|
| `event.id`                | 去除（各后端自动生成 UUID）                     |
| `event.timestamp`         | 替换为顺序索引（0, 1, 2, ...）                  |
| `session.last_update_time` | 替换为哨兵值 `0.0`                             |
| `summary_timestamp`       | 替换为哨兵值 `0.0`                             |
| `memory_entry.timestamp`  | 去除                                          |
| dict 键顺序               | 使用 `sort_keys=True` 重新序列化                |
| `invocation_id`           | 去除（调用范围标识，非后端相关）                  |
| `branch`、`request_id`    | 去除（运行时元数据，非统一持久化字段）             |

## 摘要比较策略

框架区分两层摘要正确性：

1. **内容语义一致性** — `summary_text` 在空白符归一化后进行比较。不影响含义的微小格式差异按后端对允许规则处理。

2. **元数据完整性** — `session_id`、`original_event_count`、`compressed_event_count` 必须在各后端间完全匹配。任何偏差均视为无条件失败。框架明确检测三类摘要缺陷：
   - **摘要丢失** — 摘要存在于后端 A，但后端 B 中缺失。
   - **摘要覆盖错误** — 摘要存在但 `session_id` 不正确。
   - **摘要归属错误** — 摘要在底层缓存或存储中被错误地关联到不正确的 session 键。

摘要通过直接注入 `SummarizerSessionManager._summarizer_cache` 的方式生成，绕过 LLM，因为框架测试的是存储一致性而非摘要模型质量。

## 允许差异

并非所有字段级差异都表示缺陷。已知且有文档记录的差异通过 `AllowedDiff` 规则管理：

```
allowed_diffs = {
    "inmem_vs_sql": [
        {"field": "events[*].function_calls[*].args",
         "reason": "SQL 对 JSON 参数的序列化与 InMemory dict 往返不同"},
    ],
    "inmem_vs_redis": [
        {"field": "events[*].timestamp_precision",
         "reason": "Redis 将时间戳存储为精度有限的浮点字符串"},
    ],
}
```

每条规则包含后端对、字段路径和原因说明。匹配允许规则的差异不计入失败统计，但仍会在报告中以 `allowed: true` 标记呈现。

## 后端接入方式

| 后端     | 可用性             | 激活方式                                      |
|----------|--------------------|----------------------------------------------|
| InMemory | 始终可用           | 直接实例化                                    |
| SQL      | 始终可用（sqlite）  | `sqlite:///:memory:` — 无外部依赖              |
| Redis    | 按需启用           | 设置 `TRPC_REDIS_URL` 环境变量；否则跳过        |

轻量模式（InMemory + SQL）无需外部服务即可在 CI 中运行。Redis 集成模式需要设置相应的环境变量，未设置时跳过 Redis 测试对并给出明确提示。

## 差异报告

报告（`session_memory_summary_diff_report.json`）包含：

- **运行元数据**：运行 ID、时间戳、已测试的后端列表。
- **逐用例结果**：状态（pass/fail/error）、`DiffEntry` 列表。
- **汇总**：总计/通过/失败数量和误报率。

每个 `DiffEntry` 精确定位：后端对、session ID、事件索引（或摘要 ID）、类别（events/state/memory/summary）、完整的点分隔字段路径以及两个冲突值。

## 框架自身的测试

- `test_replay_normalizer.py` — 每个归一化函数的单元测试。
- `test_replay_comparator.py` — 差异逻辑的单元测试，包含针对摘要丢失、覆盖错误和归属错误检测的专项测试。
- `test_replay_report.py` — 报告生成和序列化的单元测试。
- `test_replay_consistency.py` — 端到端测试，通过后端对运行全部 10 个回放用例，验证正常用例误报率 ≤ 5%，异常用例 100% 检出。
