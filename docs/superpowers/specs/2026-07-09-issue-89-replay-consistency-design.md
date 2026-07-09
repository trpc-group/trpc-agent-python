# Issue #89 Session / Memory 多后端回放一致性框架设计

## 1. 目标与边界

本设计为 tRPC-Agent-Python 建立可重复、可诊断、可扩展的 Session / Memory / Summary 回放一致性测试框架，回答三个相互独立的问题：

1. 相同操作和显式配置下，不同后端的业务行为是否一致；
2. SQLite 关闭并重新创建服务后，持久化投影是否保持不变；
3. 重试、响应丢失或持久化失败后，SDK 当前表现为何，比较框架能否准确识别。

框架不新增生产 API，也不把测试框架观测字段伪装成 SDK 契约。当前 SDK 没有持久化的 Summary version，因此 `observed_generation_ordinal` 只能作为 harness observation；`summary.persisted_version` 在维护者确认前必须报告为 unsupported。

## 2. 测试层次

### 2.1 Replay Consistency

使用相同轨迹和相同 `SessionServiceConfig` 驱动 InMemory 与 SQLite，并在集成模式下选择性加入 Redis。严格比较 Session、事件、State、Memory 和 Summary 的当前公开契约。

### 2.2 Persistence Recovery

在同一组 SQLite Session/Memory 数据库文件上执行：

```text
replay -> warm snapshot -> close -> reopen -> cold snapshot
```

Warm/Cold 只严格比较持久化投影。重新打开的 SQLite 必须使用新的 `SummarizerSessionManager`，其运行时 cache 为空。

### 2.3 Mutation / Capability Detection

- Snapshot mutation 只验证比较器对人为差异的检出能力；
- 操作级故障注入用于识别当前幂等性和原子恢复能力；
- 未由 SDK 保证的理想行为不作为强制契约；
- 无法唯一分类的状态必须使测试失败。

## 3. 运行模式

统一使用 `REPLAY_MODE`：

| 模式 | 必需后端 | 内容 |
| --- | --- | --- |
| `inmemory` | InMemory | 10 条 replay、业务不变量、10 条 mutation、运行时 Summary、性能记录 |
| `contract` | InMemory + SQLite | 跨后端比较、SQLite Warm/Cold、Default Profile、恢复能力分类 |
| `integration` | InMemory + SQLite；Redis 可选 | Contract 全部内容及 Redis 比较 |

默认模式为 `contract`。Contract/Integration 中 SQLite 构造失败必须失败，不能 skip 或退化为单后端。只有 Redis 可以因未配置、缺少可选依赖或外部服务不可达而明确 skip。

InMemory 轻量模式不创建 SQLite Engine。30 秒预算默认记录，通过 `REPLAY_ENFORCE_BUDGET=1` 在专用验收中强制。

## 4. 工程结构

```text
tests/sessions/
├── replay_consistency/
│   ├── __init__.py
│   ├── __main__.py
│   ├── model.py
│   ├── backends.py
│   ├── replay.py
│   ├── snapshot.py
│   ├── compare.py
│   ├── replay_cases.jsonl
│   ├── report.schema.json
│   ├── example_report.json
│   └── README.md
├── test_replay_consistency.py
└── test_replay_recovery.py
```

职责如下：

- `model.py`：case、operation、required observation、diff 和 evaluation 模型，JSONL loader；
- `backends.py`：运行模式、后端构造、生命周期及 SQLite reopen；
- `replay.py`：操作执行、真实 Event factory、确定性 Summary、故障 wrapper；
- `snapshot.py`：Session、Memory、Summary 分层快照及 canonical 表示；
- `compare.py`：字段策略、path-aware diff、allowed diff、mutation、报告组装；
- `__main__.py`：显式报告生成 CLI；
- 两个测试入口分别负责一致性与恢复能力。

## 5. 后端与 Summary Stack

后端使用当前真实构造参数：

```python
InMemorySessionService(
    summarizer_manager=manager,
    session_config=session_config,
)

SqlSessionService(
    db_url=f"sqlite:///{session_db_path}",
    summarizer_manager=manager,
    session_config=session_config,
    is_async=False,
)

SqlMemoryService(
    db_url=f"sqlite:///{memory_db_path}",
    is_async=False,
)
```

每个后端、并行 case 和 SQLite reopen 实例必须拥有独立的 Summary stack。不得共享 Manager 或运行时 cache。

确定性 Summarizer 覆盖真实调用点 `_compress_session_to_summary()`，根据固定 Event ID、author 和 canonical content 生成 SHA-256 摘要，不调用外部模型。它配置始终返回真的 checker 和 `auto_summarize=True`，但 replay 必须通过公开 API 触发：

```python
await session_service.create_session_summary(session)
```

## 6. Profile

### 6.1 Contract Profile

所有参与比较的后端显式使用：

```python
SessionServiceConfig(store_historical_events=True)
```

只有该 Profile 计算跨后端一致率和误报率。

### 6.2 Default Profile

分别使用后端默认构造，并断言：

1. SQL 默认保存 historical events；
2. InMemory 行为与其默认配置一致。

Default Profile 不参与跨后端一致率。

## 7. Replay Case

公开 JSONL 至少包含以下 10 条轨迹：

1. 单轮 user/assistant 文本；
2. 多轮连续对话；
3. 真实 `FunctionCall` 与 `FunctionResponse`；
4. State 多次写入、覆盖和临时状态持久化边界；
5. `store_session()` / `search_memory()` Memory 存取；
6. 首次 Summary；
7. Summary 更新；
8. Summary 与事件截断；
9. Memory 重复 `store_session()`；
10. 异常或重复操作轨迹。

每条 case 声明 required observations，包括最少 active/historical events、Memory 数量、Summary anchor、FunctionCall 和 FunctionResponse 数量。缺少必需观察结果必须失败，防止后端、Memory 或 Summary 空跑。

Event ID、invocation ID、request ID 和业务输入固定。Event timestamp 使用固定基准加严格递增偏移，避免同时间戳导致 SQL 排序不稳定。

## 8. Snapshot 契约

### 8.1 Session 与 Event

严格比较 Session 作用域、事件数量和顺序、author、文本、工具调用参数和响应、State、active/historical 划分及业务可注入 ID。

### 8.2 Memory

Memory 使用真实 `store_session()` 和 `search_memory()`。内容与数量通过 canonical multiset（`Counter`）比较，不能使用会吞掉重复项的 set。原始顺序保留在 diagnostics；只有 SDK 明确承诺排序时才作为严格契约。

### 8.3 Summary

Summary 快照分为三层：

- `runtime_contract`：`session_id`、文本、原始/压缩事件数、`summary_timestamp`、metadata，仅在 Manager 存活时存在；
- `persisted_projection`：Summary anchor、文本、Session 归属、active/historical 覆盖关系和摘要后新事件，Warm/Cold 严格比较；
- `harness_observations`：生成序号、操作 ID 和 lane，不属于 SDK 契约。

Cold reopen 不要求恢复运行时 Summary cache、计数或 runtime timestamp。

## 9. Summary ID 与时间策略

不同后端独立生成的 Summary anchor ID 只验证 UUID 结构、非空、唯一性、位置和 Summary flag；SQLite Warm/Cold 指向同一持久化事件，anchor ID 必须严格相等。

Runtime Summary timestamp 使用每个测试独立安装的无限确定性时钟：

```python
ticks = count(start=1_700_001_000, step=10)
```

每个后端内部必须满足：

```text
v1 timestamp 是有限正数
v2 timestamp > v1 timestamp
v2 summary text != v1 summary text
```

不同后端的 runtime timestamp 不要求绝对值相等。

Summary anchor timestamp 是持久化 Event 字段：

- InMemory vs SQLite：验证有限正数、位置和结构，不比较绝对值；
- SQLite Warm vs Cold：必须严格相等。

## 10. Normalizer、Comparator 与 Allowed Diff

Normalizer 只统一 datetime、Pydantic/dataclass、字典键序、tuple/list 和浮点表现形式，不负责忽略差异。

Comparator 必须严格检查类型，并按 `ComparisonContext`（backend pair、profile、lane）选择字段策略。禁止 `.*id.*`、`.*timestamp.*` 等宽泛规则。

Allowed diff 必须包含受控路径、比较模式、原因、容差及适用 backend pair。报告区分：

```text
actual_diff
allowed_diff
unsupported_contract
harness_observation
diagnostic
```

误报率采用 case-level 公式：

```text
存在 unexpected actual diff 的正常 Contract case 数
÷ 正常 Contract case 总数
```

同一 case 的跨后端和 Warm/Cold 比较最多计一次。InMemory-only 模式误报率为不适用。

## 11. Mutation

对真实 baseline snapshot 的副本执行：

1. `drop_event`
2. `duplicate_event`
3. `swap_event_order`
4. `change_tool_argument`
5. `change_state_value`
6. `drop_memory`
7. `duplicate_memory`
8. `drop_summary`
9. `stale_summary_overwrite`
10. `wrong_summary_session`

每个 mutation 必须产生符合预期路径的 diff，mutation score 必须为 10/10。后三条分别验证 Summary 丢失、覆盖错误和串 Session 的 100% 检出。

## 12. 操作级恢复能力

### 12.1 Append 响应丢失后重试

实际完成 append 后 wrapper 抛出模拟响应丢失，再使用同一 Event ID 重试。结果按证据互斥分类为：

- `IDEMPOTENT`：无重试异常，目标 Event 恰好一个；
- `DUPLICATE_EVENT`：目标 ID 至少两个，并产生准确 duplicate diff；
- `RETRY_REJECTED`：重试异常存在，首次写入仍存在且目标 Event 恰好一个。

其他状态必须失败。

### 12.2 Memory 重复保存

重复调用 `store_session()`，通过 multiset 验证内容数量；已明确保证的幂等行为作为严格契约，否则按证据分类。

### 12.3 Summary 持久化失败

生成 v1，追加事件并生成 v2，在 `update_session()` 注入失败，分别捕获 runtime 与重新加载的 persisted projection。

先计算 `mixed`：

```text
同时包含 v1/v2 投影
或多个不同 anchor
或事件覆盖部分更新
或 active/historical 重叠
```

再进行互斥分类：

- `OLD_SUMMARY_PRESERVED`：`not mixed`、runtime=v1、persisted=v1、anchor=1；
- `RUNTIME_PERSISTED_DIVERGENCE`：`not mixed`、runtime=v2、persisted=v1、anchor=1；
- `PARTIAL_PERSISTENCE`：`mixed`。

命中数量不是 1 时必须失败，不能使用兜底枚举掩盖未知状态。

## 13. 报告与 CLI

普通 pytest 只向 `tmp_path` 写完整报告。仓库提交稳定的 JSON Schema 与去除时间、主机名、绝对路径、动态耗时和随机标识的示例报告。

Diff 至少包含：

```text
case_id
session_id
event_index 或 summary_anchor_event_id
field_path
left/right backend
left/right value
kind
allowed diff reason
```

CLI：

```bash
python -m tests.sessions.replay_consistency \
  --mode contract \
  --output replay-report.json
```

稳定示例只能通过显式 `--write-example-report` 更新。

## 14. 验收映射

- 默认支持 InMemory 与 SQLite，Redis 环境变量开启；
- InMemory-only 模式不依赖 SQLite、Redis 或外部模型；
- 10 条公开 replay case；
- 10 条真实 snapshot mutation 100% 检出；
- Summary 丢失、覆盖错误、串 Session 100% 检出；
- 正常 Contract case 误报率不超过 5%；
- Diff 定位到 Session、事件/Summary 和字段路径；
- SQLite Warm/Cold 使用同一数据库文件；
- 轻量模式目标耗时不超过 30 秒；
- `summary.persisted_version` 在维护者确认前显式标为 unsupported。

## 15. 150–300 字设计说明

本框架使用统一 replay case 驱动 Session、Memory 与 Summary 后端，支持 InMemory 轻量模式、InMemory/SQLite 契约模式及可选 Redis 集成模式。业务 ID 与事件时间采用确定性注入；时间精度、后端生成标识等差异通过字段级 allowed diff 说明，禁止通配忽略。Memory 使用 canonical multiset 比较，既消除无契约顺序差异，也保留重复项检测。Summary 分为运行时契约、持久化投影和框架观测三层；SQLite 同库重启严格比较摘要锚点、文本、Session 归属及事件覆盖关系。Snapshot mutation 验证差异检测率，操作级故障注入则基于互斥证据分类记录当前 SDK 的幂等和原子恢复能力。

## 16. 外部决策点

编码不受阻塞，但提交 PR 前应在 Issue #89 中向维护者确认：

> 当前 SDK 的 `SessionSummary` 未持久化 version。是否接受使用 harness 的 `observed_generation_ordinal` 验证摘要更新顺序，并通过 Summary anchor 与事件覆盖关系验证替换语义，同时将 `summary.persisted_version` 标记为当前 SDK 不支持？

未得到确认前，PR 不得声称完整验证了持久化 Summary version。
