# Replay Consistency Playbook

回放一致性验证手册。解决一件事：**两套实现了同一接口的 Session /
Memory 后端（InMemory / SQL / Redis），在同一条输入下能不能产生出同一
份业务快照**。

这套 harness 把同一条轨迹在两个后端各跑一遍，逐字段对比。结果写到
`tests/sessions/replay_diff_report.json`，每条 `differences` 都是带
字段路径的真实偏差。

最常用的跑法：

```bash
pytest tests/sessions/test_replay_consistency.py -v
```

默认跑 InMemory ⇄ SQLite，约 24 秒。设环境变量就能接入 Redis /
MySQL，集成测试在没配 secret 时自动 skip。

## 心脏只有 5 段

```
JSONL cases  →  harness  →  normalizer  →  diff  →  report
1 个真相源          在 2 个后端跑      丢非业务字段    按 path 逐字段比   写到 JSON
```

整个 harness 在 `trpc_agent_sdk/replay/` 下分 5 个核心模块（外加
`__init__.py` 统一导出公开 API）。新增后端**只改** `_backends.py`
里的 `_build_backend` 一个分支。

## 10 条 case 都在一张 JSONL

[`tests/sessions/replay_cases/session_memory_summary.jsonl`](../../../tests/sessions/replay_cases/session_memory_summary.jsonl)
每行一个 case，行顺序和下表一致：

| # | Case | 压测点 | 不一致就说明后端…… |
|---|------|--------|--------------------|
| 1 | `single_turn` | 一对 user → agent | 事件列表为空、author 缺失 |
| 2 | `multi_turn` | 三轮交替 | 顺序交换 |
| 3 | `tool_call` | function_call + response | Part 序列化不一致 |
| 4 | `state_update` | 4 次 `state_delta` 覆盖 | 后写没覆盖先写 |
| 5 | `memory_rw` | store + search | 索引器没跑 |
| 6 | `summary_gen` | 22 轮 → 摘要 | anchor event 没写 |
| 7 | `summary_truncate` | 摘要 + 后追加 | 压缩分界漂移 |
| 8 | `exception_recovery` | 重复 append + 摘要失败 | 缺补偿逻辑 |
| 9 | `injected_event_order` | 注入事件顺序 | diff 引擎丢了顺序感知 |
| 10 | `injected_summary_session` | 注入摘要 session_id | 跨 session 摘要泄漏 |

> 9 / 10 是**人为注入**的失败，用来证明 diff 引擎能抓它宣称能抓的 bug。
> 改了引擎让它们开始通过 = 丢检测能力，先修引擎别动 case。

## 归一化与允许差异

`NORMALIZATION_RULES` 和 `ALLOWED_DIFF_RULES` 两个常量分别定义
**自动丢弃**和**显式允许**的字段。新增条目都必须在 commit message
里写明理由——前者压差异 = 隐瞒 bug，后者加规则 = 放开承诺。

```
自动丢弃:  timestamp·秒级化 · id·按内容重赋 · state_delta·key 排序 · is_final_response
允许差异:  后端生成的 invocation_id · save_key 格式差异 · 压缩后事件总数
```

## 摘要语义（3 层）

1. **元数据严格一致**：`summary_id` / `session_id` / `version` / `text` /
   `anchor_count` / `original_event_count` / `compressed_event_count`
   跨后端必须相等。跨后端摘要 bug **绝大多数** 落在这里。
2. **单后端独立不变量**：压缩真的发生了（`compressed < original`）、
   摘要文本非空、摘要后追加的事件仍可读。
3. **已知差异类**：`EXPECTATIONS` 打了 `known_summary_divergence` 的
   case 只允许 `events` / `summary` 域差异，且每条都要带 allowed_diff
   理由。出现 `state` 差异 = 真 bug。

## 集成模式与 CI

```bash
pytest tests/sessions/test_replay_consistency.py -v     # 默认 24s，纯本地
TRPC_REPLAY_REDIS_URL=redis://...  pytest -m integration # 启用 Redis
TRPC_REPLAY_SQL_URL=mysql+...      pytest -m integration # 启用 MySQL
```

`conftest.py` 的 `integration_runtime` fixture 在运行开头统一探测：
环境变量 + 可选依赖 **缺一就 skip**，从来不会硬失败。

- `ci.yml` — 每个 PR 跑轻量套件，30 秒预算内
- `.github/workflows/replay-integration.yml` — 每周 + 手动触发，用
  `redis:7-alpine` / `mysql:8.0` service container 跑集成测试，每个
  集成 job 用 `if: env.TRPC_REPLAY_*_URL != ''` 守卫，未配 secret 的
  fork 看到的是 "job skipped" 而不是 "job failed"。diff 报告每次都
  作为 workflow artifact 上传。

## 常见失败模式

| 报告里看到的 | 大概率是 |
|--------------|---------|
| `$.events[*].id` 跨后端不同 | 新事件没走 `_canonical_event` |
| `$.summary.current.session_id` 不一致 | 摘要被建到了别的 session 命名空间 |
| `$.summary.current.version` 不一致 | 后端漏了一个 revision |
| 一个后端上记忆结果数为 0 | 该 case 的 `search_memory` 之前没调 `store_session` |
| `duplicate_append` 没 recovery | 后端缺补偿去重路径；SQL 通常加 `(session_id, event_id)` 唯一约束 |
| 注入型 case 报告里 `differences` 为空 | diff 引擎丢了检测能力——先修引擎，别放宽 case |

## 加新 case / 加新后端

**新 case** 就是一行 JSON。`op` 字段当前支持：`append_event`、
`state_update`、`summarize`、`store_memory`、`search_memory`、
`duplicate_append`、`fail_summary`。详情看
[`_harness.py::_run_case`](../../../trpc_agent_sdk/replay/_harness.py)。
加完后到 `tests/sessions/test_replay_consistency.py` 的 `EXPECTATIONS`
登记，一条规则三选一：`normal` / `known_summary_divergence` /
`allowed_mechanism_only`。

**新后端** 5 步：实现 `SessionServiceABC` + `MemoryServiceABC` → 在
[`_backends.py::_build_backend`](../../../trpc_agent_sdk/replay/_backends.py)
加分支 → 在 `resolve_backend_names` 注册名字 → 重跑轻量套件（前几次
挂掉是预期，错误信息会告诉你它漏了哪个不变量）→ 如有必要给
`ALLOWED_DIFF_RULES` 加一条带理由的规则。
