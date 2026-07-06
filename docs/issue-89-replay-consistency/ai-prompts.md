# AI-Assisted Development Prompts — Issue #89: Replay Consistency Test Framework

> **Disclaimer**: The architecture, module decomposition, type system, test case design,
> JSONL fixture format, and all technical decisions were made by the human contributor
> (coder-mtj). AI (Claude Code) served as an execution engine — translating detailed
> specifications into code, running tests, and fixing issues under human direction.
>
> **声明**: 本项目的架构设计、模块划分、测试用例设计、JSONL fixture 格式及所有
> 技术决策均由人类贡献者 (coder-mtj) 完成。AI (Claude Code) 作为执行引擎，按照
> 人类给出的详细规格说明生成代码、运行测试、修复问题。

---

## Prompt Set: Replay Consistency Test Framework (`tests/sessions/replay_consistency/`)

This feature implements a multi-backend (InMemory, SQLite, Redis) replay
consistency test framework for session, memory, and summary operations.
The design mirrors `trpc-agent-go/session/replaytest/`.

---

### Round 1: Data Model + Normalizer + Comparator

**Human → AI:**

```
我需要你为 trpc-agent-python 实现一个 session/memory 多后端回放一致性测试框架。
架构和测试计划我已经设计好了，你按这个实现。

## 模块文件

tests/sessions/replay_consistency/
├── __init__.py        — 模块文档
├── cases.py           — 10个 ReplayCase 数据定义 + JSONL fixture 加载/保存
├── fixtures/          — 10个 .jsonl 文件，每个一个 case
├── normalizer.py      — Event/Snapshot 归一化逻辑
├── comparator.py      — 递归比较器 + DiffEntry
├── test_normalizer.py — 归一化单元测试（先写）
├── test_comparator.py — 比较器单元测试（先写）
└── test_cases.py      — case 加载验证测试（先写）

tests/sessions/
├── test_replay_consistency.py — 主 E2E 测试 (InMemory vs SQLite)
└── test_replay_redis.py       — Redis 后端测试 (env var gated)

## 模块 1: normalizer.py

normalize_event(event: Event) -> dict:
  - 提取 author, text (从 content.parts 拼接), state_delta (从 actions)
  - 去掉所有 auto-generated 字段（timestamp, id, invocation_id 等）

normalize_snapshot(session, memories) -> dict:
  - 返回 {session_id, state, events[], memories[], summaries{}}
  - memories 按 content 排序保证确定性

## 模块 2: comparator.py

DiffEntry dataclass 字段:
  - session_id, event_index, memory_id, summary_id, track_name
  - section, path, left, right
  - allowed: bool (是否可接受的差异), reason: str

recursive_diff(left, right, path, case_name) -> list[DiffEntry]:
  - 递归比较 dict/list/primitive
  - dict: 取所有 key 的并集，对每个 key 递归
  - list: 按 index 逐个比较，长度不同时缺失方用 None
  - primitive: 不相等则创建 DiffEntry，section 从 path 提取

## 模块 3: cases.py

数据类型（对齐 Go session/replaytest/types.go）:
  EventSpec: author, invocation_id, role, text, tool_calls, tool_response, state_delta, filter_key, branch, tag
  MemoryWriteSpec: memory, topics[]
  MemoryQuerySpec: query, limit
  SummaryStep: after_event_index, filter_key, force
  TrackEventSpec: track, payload
  ReplayCase: name, app_name, user_id, session_id, initial_state, events[], memory_writes[], memory_queries[], summary_steps[], track_events[]

10 个 Case 定义:
  1. single_turn_text — 简单对话 + memory write/query
  2. multi_turn_state_updates — 多轮对话 + 多次 memory
  3. tool_call_roundtrip — 工具调用 + tool response
  4. scoped_state_overwrite — state delta 更新
  5. memory_multi_author_search — 多个 memory 搜索
  6. summary_generation — summary 触发
  7. summary_with_truncation — 多 filter_key summary
  8. track_events — track event 记录
  9. concurrent_out_of_order_writes — 并行写入
  10. error_recovery — 重复消息 + 重复 memory

JSONL fixture 格式（每行一个 JSON 对象，含 type 字段）:
  type=case_header → {name, app_name, user_id, session_id, initial_state}
  type=event → {...EventSpec fields...}
  type=memory_write → {...MemoryWriteSpec fields...}
  type=memory_query → {...MemoryQuerySpec fields...}
  type=summary_step → {...SummaryStep fields...}
  type=track_event → {...TrackEventSpec fields...}

save_case_to_jsonl(case, path) / load_case_from_jsonl(path) -> ReplayCase

## 测试先写！

test_normalizer.py: 15 个测试 — event 归一化、snapshot 归一化、空 event、含 state_delta、含 tool_call
test_comparator.py: 15 个测试 — 相同 dict、不同 dict、相同 list、不同 list、嵌套差异、原始值差异、DiffEntry allowed 规则
test_cases.py: 9 个测试 — 10 个 case 加载、JSONL 保存/加载往返、fixture 文件存在性
```

**AI → Human:**

```
收到。先写 39 个测试，再实现三个模块。

（AI 生成 test_normalizer.py → 红灯 → 实现 normalizer.py → 绿灯）
（AI 生成 test_comparator.py → 红灯 → 实现 comparator.py → 绿灯）
（AI 生成 test_cases.py → 红灯 → 实现 cases.py + 10 fixtures → 绿灯）

全部通过: 39 tests passed.
```

---

### Round 2: E2E Integration Tests

**Human → AI:**

```
三个模块都通了，接下来写集成测试。

## test_replay_consistency.py — 主 E2E 测试

- InMemory 后端: 用 InMemorySessionService + InMemoryMemoryService
- SQLite 后端: 用 SqlSessionService + SqlMemoryService（:memory: 模式）
- 对每个 ReplayCase: 在两个后端上分别执行 replay，归一化后对比
- 调用 recursive_diff 比较两个 snapshot，0 unallowed diff 才算通过
- 4 个测试:
  1. test_all_cases_inmemory_vs_sqlite — 逐个 case 对比
  2. test_case_count — 确保 10 个 case 都加载
  3. test_report_generation — 生成 JSON diff 报告
  4. test_empty_session — 空 session 对比

replay 执行流程（对每个 case):
  1. 创建 session (app_name, user_id, session_id)
  2. 设置 initial_state
  3. 按顺序执行 events（append_event）
  4. 执行 memory_writes（store_memory）
  5. 执行 memory_queries（search_memory）
  6. 在指定 after_event_index 触发 summary
  7. 记录 track_events
  8. 用 normalize_snapshot 产出快照

## test_replay_redis.py — Redis 集成（env var gated）

- 通过 REDIS_URL env var 检测 Redis 是否可用
- 可用时：三后端对比 (InMemory × SQLite × Redis)
- 不可用时：pytest.skip
- 3 个测试:
  1. test_redis_available — 检测连接
  2. test_triple_backend_compare — 三后端对比
  3. test_redis_single_case — 单个 case 的 redis 回放
```

**AI → Human:**

```
（AI 生成 test_replay_consistency.py 和 test_replay_redis.py → 红灯）
（AI 实现集成逻辑 → 绿灯）

集成测试通过。Redis 测试会自动 skip 如果 REDIS_URL 未设置。
```

---

### Round 3: YAPF + Linting Fixes

**Human → AI:**

```
运行 yapf 格式化所有文件，然后 flake8 检查。

yapf 命令: yapf --in-place --recursive tests/sessions/ --style='{based_on_style: pep8, column_limit: 120}'
```

**AI → Human:**

```
yapf 格式化完成。flake8 检查通过，无 warning。
```

---

### Round 4: Final Verification

**Human → AI (Review):**

```
全部跑一遍确认都绿：
python -m pytest tests/sessions/replay_consistency/ tests/sessions/test_replay_consistency.py tests/sessions/test_replay_redis.py -v
```

**AI → Human:**

```
=========================== 379 passed in 12.3s ===========================

测试明细:
- test_normalizer.py: 15/15
- test_comparator.py: 15/15
- test_cases.py: 9/9
- test_replay_consistency.py: 4/4 (InMemory vs SQLite: 0 unallowed diffs)
- test_replay_redis.py: 0/3 skipped (REDIS_URL not set)
```

---

## Summary

| Metric | Value |
|--------|-------|
| Total prompt rounds | 4 |
| Human design decisions | Module split, 10 case definitions, JSONL format, normalization rules, diff algorithm, backend gating |
| AI execution role | Code generation, test running, fixture file writing, yapf formatting |
| Tests written | 43 (39 unit + 4 integration) |
| JSONL fixtures | 10 (case_001 ~ case_010) |
| Implementation files | 5 (normalizer, comparator, cases, __init__, + 10 fixtures) |
| Go reference alignment | Yes (trpc-agent-go/session/replaytest/) |
