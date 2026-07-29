# Session 回放一致性

回放框架将同一条 Session、Memory 和 Summary 轨迹运行在多个存储后端上，并报告业务语义差异。它既可以用规范预期校验单个后端，也可以选择一个基准后端进行多后端比较。

## 设计摘要

ReplayHarness 将同一操作流写入各后端，并把快照与 case 预期和 InMemory 基准比较。归一化只处理表示差异：文本执行 NFKC 和空白折叠，自动 ID 使用稳定别名，时间戳转为位置标记，字典键顺序不比较；时间缺失、事件乱序和业务值变化仍会报告。

Summary 文本规范化后精确比较；所属 Session、ID、版本、更新时间和覆盖链均严格比较。allowed_diff
必须限定后端与字段路径，只允许 Memory 排序差异，并记录原值和策略。

InMemory 可独立运行，轻量模式与 SQLite 对比；也可接入 SQL、Redis 或注入已有服务。连接地址、数据清理及服务生命周期由调用方控制。

## 核心 API

所有 Replay 类型都可以从 `trpc_agent_sdk.sessions` 导入。

| 类型 | 职责 |
|------|------|
| `ReplayCase` | 加载并校验一条 JSONL 轨迹及其规范预期 |
| `ReplayBackend` | 组合 Session、Memory、Summary、清理和生命周期行为 |
| `ReplaySummaryModel` | 为正常摘要流程提供确定性 Summary 文本 |
| `ReplayHarness` | 跨后端执行 case 并组织比较 |
| `ReplayNormalizer` | 归一化非业务存储表示 |
| `ReplayComparator` | 递归生成字段级差异 |
| `BackendReplayResult` | 保存单个后端的原始快照、规范化快照和诊断信息 |
| `ReplayRunResult` | 返回报告和各后端回放快照 |
| `ReplayReport` | 将报告稳定序列化为 UTF-8 JSON |

## 基本用法

```python
from pathlib import Path

from trpc_agent_sdk.sessions import ReplayHarness
from trpc_agent_sdk.sessions import ReplayReport


async def run_replay() -> None:
    cases = ReplayHarness.load_cases(Path("replay_cases"))
    harness = ReplayHarness.create_lightweight(work_dir=Path(".replay"))
    result = await harness.run(cases)
    ReplayReport.write(result.report, Path("replay_report.json"))
```

`create_in_memory()` 创建只包含 InMemory 的执行器。`create_lightweight()` 使用 InMemory 作为基准，并加入
一个文件型 SQLite 后端。

## 后端组合

已有连接地址时，可以使用 `create_integration()`：

```python
from trpc_agent_sdk.sessions import ReplayHarness


async def compare_remote_backends(sql_url: str, redis_url: str, cases) -> dict:
    harness = ReplayHarness.create_integration(
        sql_url=sql_url,
        redis_url=redis_url,
    )
    result = await harness.run(cases)
    return result.report
```

自定义组合可以先创建 `ReplayBackend`，再传给 `ReplayHarness`：

```python
from trpc_agent_sdk.sessions import ReplayBackend
from trpc_agent_sdk.sessions import ReplayHarness


def create_replay_harness(sql_url: str) -> ReplayHarness:
    return ReplayHarness(
        backends=[
            ReplayBackend.in_memory(),
            ReplayBackend.sql(sql_url, name="primary_sql"),
        ],
        reference_backend="in_memory",
    )
```

`ReplayBackend` 也可以直接接收已经配置好的 Session Service、Memory Service 和 Summary model。
调用方自己管理持久化数据时可设置 `cleanup_data=False`，自己管理服务生命周期时可设置
`close_services=False`。默认情况下，`run()` 会初始化所有后端，并在 `finally` 中清理已触达的数据、
关闭服务。清理范围只包括该后端记录的应用、用户、Session、state、Memory 数据和 Redis key。

## Replay DSL

每个 JSONL 文件以 `case` 记录开头，后续每行是一项操作：

| 操作 | 行为 |
|------|------|
| `create_session` | 创建指定 Session，并可写入初始 state |
| `append_event` | 追加完整 SDK `Event`，包括工具和 state 数据 |
| `store_memory` | 通过 Memory Service 保存当前 Session |
| `search_memory` | 保存一次具名 Memory 查询结果 |
| `summarize` | 执行 Session 摘要并持久化回放元数据 |
| `inject_failure` | 模拟声明的部分写入或重复提交 |
| `checkpoint` | 在结果快照中记录逻辑边界 |

首行提供 `case_id` 和 `expected`。操作使用唯一 `operation_id`，并且只能引用轨迹中已经创建的 Session。
加载器会校验必填字段、事件结构、标识唯一性、Summary 版本、覆盖关系和递增的更新时间。

## 快照与归一化

快照包含：

- Session、当前事件和历史事件；
- 合并后的 Session、User 和 App state；
- 具名 Memory 查询结果；
- Summary 文本和回放元数据；
- checkpoint 和操作异常。

文本归一化会递归处理消息、工具载荷、Memory 和 Summary 内容。自动事件 ID 会映射为稳定别名。时间戳
标记保留字段是否存在及其在列表中的相对位置；缺失、格式错误或倒序的时间戳仍然属于差异。字典采用递归
比较，不受序列化键顺序影响。

不同 Memory 实现可能以不同顺序返回同等相关的结果。如果规范排序改变了原始顺序，后端结果会写入一条
`allowed_diff`，记录后端名称、JSON Pointer、原始顺序和归一化策略。框架不提供通配字段忽略。

## Summary 语义

确定性模型提供回放操作声明的 Summary 文本，`SessionSummarizer` 和 `SummarizerSessionManager` 仍负责
正常的压缩与持久化流程。回放元数据包括：

- `summary_id`；
- 所属 `session_id`；
- `version`；
- `updated_at`；
- `replaces_summary_id`。

文本经过 NFKC 和空白归一化后比较，元数据保持精确比较。Summary 更新必须递增版本并指向被替换的
Summary，从而让比较器区分合法覆盖和旧版本误写。

## 报告与差异

每个后端首先使用子集语义与 case 中的规范预期比较。非基准后端还会与完整的基准快照比较。每条差异包含：

- 当前后端和基准后端；
- 路径属于 Session 时的 Session ID；
- JSON Pointer 字段路径；
- 基准值和后端值；
- 适用时的 event index、Memory query/index 或 Summary ID。

`ReplayRunResult.report` 可以直接稳定序列化为 JSON。`ReplayRunResult.backend_results` 保留原始快照、
规范化快照、归一化记录、允许差异和操作异常，供程序继续分析。
