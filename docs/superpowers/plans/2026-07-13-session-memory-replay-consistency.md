# Session/Memory/Summary 回放一致性框架 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: 用 superpowers:executing-plans 逐 task 执行。步骤用 `- [ ]` 跟踪。

**Goal:** 实现一个后端中立的 replay harness,用 10 条标准化轨迹驱动 InMemory/SQLite/Redis,产出可定位的差异报告,并通过快照层 + 端到端后端注入验证检出率/误报率。

**Architecture:** 四段管线 `load JSONL → replay_case → 后端中立快照 → compare → report`。详见 [设计文档](../specs/2026-07-13-session-memory-replay-consistency-design.md)。

**Tech Stack:** Python 3.10+、pytest、pydantic v2、SQLAlchemy(SQLite)、redis-py(mock)。

## Global Constraints

- **PR 干净**:只碰 `tests/` + 本计划/设计文档 + 仓库根 `session_memory_summary_diff_report.json`(运行产物)。**不改 `trpc_agent_sdk/` 生产代码**;发现的 SDK bug 只在报告/文档记录。
- **CI lint**:提交前本地 `PYTHONUTF8=1` 跑 `yapf -ri` + `flake8`([[ci-lint-yapf-flake8]])。
- **Windows**:`python-magic` 用 `python-magic-bin`([[python-magic-windows-cygwin-crash]])。
- **提交纪律**:`git add` 只加本计划列出的确切路径,禁用 `-A`/`.`([[subagent-git-add-scope]])。用户未要求不主动 commit/push。
- **轻量模式 ≤30s**:默认 InMemory vs SQLite(`:memory:`);Redis/MySQL 经 env 启用,不可用 `pytest.skip`。
- **确定性**:无 LLM、无真实网络;summary 用 `_DeterministicSummarizer`;时间用占位符归一化。

## File Structure

(完整职责见设计文档 §3.2)

| 文件 | 职责 |
|---|---|
| `tests/sessions/replay/__init__.py` | 包入口 + 150–300 字设计说明 |
| `tests/sessions/replay/harness.py` | 数据模型(ReplayOp/ReplayCase/ReplayBackend/ReplaySnapshot)+ `replay_case()` |
| `tests/sessions/replay/normalizer.py` | 占位符归一化 |
| `tests/sessions/replay/comparator.py` | 递归 `visit()` + DiffEntry |
| `tests/sessions/replay/allowed_diff.py` | JSONPath 匹配 + 覆盖率治理 |
| `tests/sessions/replay/summary_checks.py` | 三类专项 + 分词 Jaccard 语义比较 |
| `tests/sessions/replay/injectors.py` | 快照层 + 端到端后端注入 |
| `tests/sessions/replay/report.py` | schema_version=3 报告 |
| `tests/sessions/replay/backends.py` | 三后端实例化 + env 门控 |
| `tests/sessions/replay/replay_cases/*.jsonl` | 10 条 case |
| `tests/sessions/test_replay_consistency.py` | 主 E2E |
| `tests/sessions/test_replay_injections.py` | 两层注入检出 |
| `tests/sessions/test_replay_unit.py` | normalizer/comparator/allowed_diff/summary_checks 单测 |

---

## Task 1: 包骨架 + 设计说明

**Files:**
- Create: `tests/sessions/replay/__init__.py`

**Interfaces:** Produces `tests.sessions.replay` 包(后续模块的根)。

- [ ] **Step 1:** 创建 `tests/sessions/replay/__init__.py`,内含设计文档 §10 的 150–300 字设计说明作为模块 docstring,加 `__all__ = []`。
- [ ] **Step 2:** `PYTHONUTF8=1 python -c "import tests.sessions.replay"` 验证可导入。

---

## Task 2: harness 数据模型(纯 Pydantic,无逻辑)

**Files:**
- Create: `tests/sessions/replay/harness.py`

**Interfaces:**
- Consumes: `trpc_agent_sdk.sessions.SessionServiceABC`、`trpc_agent_sdk.memory.MemoryServiceABC`
- Produces: `ReplayOp`、`AllowedDiffRule`、`ReplayCase`、`ReplayBackend`、`ReplaySnapshot`(签名见设计文档 §4.1)
- **关键决策**:`ReplayOp` 用 `type: Literal[...]` + `model_dump()` 兼容异构 payload(不用 discriminated union 的复杂标签,保持 jsonl 可读)。

- [ ] **Step 1:** 写 `test_replay_unit.py::test_replay_case_roundtrip`:构造 `ReplayCase(case_id="x", operations=[ReplayOp(op="create_session", app_name="a", user_id="u", session_id="s")])`,`model_dump_json()` 后 `model_validate_json()` 应相等。
- [ ] **Step 2:** 跑测试,确认 FAIL(模块未建)。
- [ ] **Step 3:** 在 `harness.py` 实现五个 Pydantic 模型(签名照设计文档 §4.1,`ReplayBackend` 用 `BaseModel` + `model_config = ConfigDict(arbitrary_types_allowed=True)` 以容纳 service 实例)。
- [ ] **Step 4:** 跑测试,确认 PASS。

---

## Task 3: normalizer(占位符归一化)

**Files:**
- Create: `tests/sessions/replay/normalizer.py`
- Test: `tests/sessions/test_replay_unit.py`

**Interfaces:**
- Produces: `normalize_event(e: dict) -> dict`、`normalize_snapshot(s: ReplaySnapshot) -> ReplaySnapshot`、常量 `NORMALIZED = "<normalized>"`

- [ ] **Step 1:** 写测试:
  - `test_normalize_event_replaces_volatile_fields`:event 含 `id/timestamp/invocation_id` → 归一化后这三键值为 `NORMALIZED`,**键仍存在**。
  - `test_normalize_strips_temp_state`:state 含 `temp:k` → 归一化后删除该键,保留 `app:`/`user:`/普通键。
  - `test_normalize_sorts_memory`:memory 两个查询结果顺序乱 → 归一化后按 `json.dumps(sort_keys=True)` 确定性排序。
- [ ] **Step 2:** 跑测试,确认 FAIL。
- [ ] **Step 3:** 实现:递归把 `id`/`timestamp`/`invocation_id` 顶层键值替换为 `NORMALIZED`;剥离 `state` 中 `temp:` 前缀键;memory 各 list 按 `json.dumps(i, sort_keys=True, ensure_ascii=True)` 排序。
- [ ] **Step 4:** 跑测试,确认 PASS。

---

## Task 4: comparator(递归比较 + 内联定位)

**Files:**
- Create: `tests/sessions/replay/comparator.py`
- Test: `tests/sessions/test_replay_unit.py`

**Interfaces:**
- Consumes: `ReplaySnapshot`、`list[AllowedDiffRule]`(from Task 2)、`is_allowed`(from Task 5,先用 stub)
- Produces: `DiffEntry`、`compare_snapshots(reference, candidate, *, reference_backend, candidate_backend, allowed_diff) -> list[DiffEntry]`
- **关键决策**:DiffEntry 在递归到叶子时**内联写入** `session_id`(snapshot 顶层)、`event_index`(events 列表的下标)、`summary_id`(若 path 在 summary 下)。

- [ ] **Step 1:** 写测试:
  - `test_diff_detects_leaf_mismatch`:`events[0].author` 左 `"user"` 右 `"assistant"` → 产 1 条 DiffEntry,`field_path="events[0].author"`、`event_index=0`、`allowed=False`。
  - `test_diff_aligns_dict_sorted_keys`:左 `{"b":1,"a":2}` 右 `{"a":2,"b":1}` → 无 diff。
  - `test_diff_list_length_diff`:左 events 3 条右 2 条 → 产 1 条 `<missing>` diff,`event_index=2`。
- [ ] **Step 2:** 跑测试,确认 FAIL。
- [ ] **Step 3:** 实现 `visit(left, right, path)` 递归(dict sorted keys / list 下标+补 `<missing>` / 叶子 `!=` 产 DiffEntry),顶层包装填 `session_id`、按 path 前缀推断 `event_index`/`summary_id`;`allowed_diff` 参数暂传 `[]`。
- [ ] **Step 4:** 跑测试,确认 PASS。

---

## Task 5: allowed_diff(JSONPath 精确 + 治理)

**Files:**
- Create: `tests/sessions/replay/allowed_diff.py`
- Test: `tests/sessions/test_replay_unit.py` + `tests/sessions/test_allowed_diff_governance.py`

**Interfaces:**
- Produces: `is_allowed(field_path, backend_pair, rules) -> tuple[bool, str|None]`、`check_governance(case, total_fields, used_allowed) -> None`、常量 `MAX_ALLOWED_PER_CASE=8`、`MAX_ALLOWED_RATIO=0.10`
- **接线**:回 `comparator.compare_snapshots` 用真实 `is_allowed` 替换 Task 4 stub。

- [ ] **Step 1:** 写测试:
  - `test_allowed_exact_path_match`:规则 `path="events[0].timestamp"` → 字段 `"events[0].timestamp"` 匹配 allowed,`"events[0].author"` 不匹配。防 `*.id` 过宽。
  - `test_allowed_index_wildcard`:规则 `"events[*].timestamp"` → 匹配 `events[0].timestamp`/`events[5].timestamp`。
  - `test_governance_rejects_too_many`:`check_governance(case, total_fields=20, used_allowed=10)`(超 `MAX_ALLOWED_RATIO=0.10`)→ 抛 `ValueError`。
  - `test_governance_rejects_no_reason`:`AllowedDiffRule(path="x", reason="")` → 构造时或 governance 校验抛错。
- [ ] **Step 2:** 跑测试,确认 FAIL。
- [ ] **Step 3:** 实现 `is_allowed`:`[N]→[*]` 归一化后 `fnmatch.fnmatchcase`,reason 空即拒;`check_governance`:条数 > `MAX_ALLOWED_PER_CASE` 或 `used/total > MAX_ALLOWED_RATIO` 抛错。
- [ ] **Step 4:** 跑测试 + 回归 Task 4 测试,确认 PASS。

---

## Task 6: summary_checks(三类专项 + 语义比较)

**Files:**
- Create: `tests/sessions/replay/summary_checks.py`
- Test: `tests/sessions/test_replay_unit.py` + `tests/sessions/test_summary_checks.py`

**Interfaces:**
- Produces: `SummaryIssue`、`check_summary_issues(reference_summary, candidate_summary, *, candidate_backend) -> list[SummaryIssue]`、`summary_text_similarity(a, b) -> float`、常量 `SUMMARY_SIM_THRESHOLD=0.8`

- [ ] **Step 1:** 写测试:
  - `test_detects_loss`:candidate `summary.current=None`(ref 非 None)→ 产 `SummaryIssue(type="loss")`。
  - `test_detects_overwrite`:candidate `current.version < ref.current.version`(旧覆盖新)→ 产 `type="overwrite"`。
  - `test_detects_affiliation`:candidate `current.session_id != ref` → 产 `type="affiliation"`。
  - `test_semantic_similarity`:相同词不同序 → Jaccard=1.0;完全不同 → 0.0。
- [ ] **Step 2:** 跑测试,确认 FAIL。
- [ ] **Step 3:** 实现:三类 if 判断 + `summary_text_similarity`(去标点、小写、`set(tokens)` Jaccard)。
- [ ] **Step 4:** 跑测试,确认 PASS。

---

## Task 7: _DeterministicSummarizer + replay_case 驱动

**Files:**
- Modify: `tests/sessions/replay/harness.py`(加 `replay_case()`、`_DeterministicSummarizer`)

**Interfaces:**
- Consumes: `SessionSummarizer`(覆写 `_compress_session_to_summary`,已确认存在于 [`_session_summarizer.py:197`](../../trpc_agent_sdk/sessions/_session_summarizer.py))、`SessionServiceABC.append_event/get_session/update_session`、`MemoryServiceABC.store_session/search_memory`
- Produces: `async replay_case(backend, case) -> ReplaySnapshot`

- [ ] **Step 1:** 写 `test_replay_unit.py::test_replay_case_single_turn`(用 InMemory backend):case 含 create_session + 1 append_event → snapshot.events 长度 1、state 含写入值。
- [ ] **Step 2:** 跑测试,确认 FAIL。
- [ ] **Step 3:** 实现 `_DeterministicSummarizer._compress_session_to_summary` 返回 `f"{session_id} summary rev v{n}: {covered}"`;`replay_case` 按 `operations` 顺序调 service API,末尾 `get_session` + memory 查询组装 `ReplaySnapshot`;每 case 用独立 `app_name`。
- [ ] **Step 4:** 跑测试,确认 PASS。

---

## Task 8: backends(三后端实例化 + env 门控)

**Files:**
- Create: `tests/sessions/replay/backends.py`

**Interfaces:**
- Produces: `in_memory_backend() -> ReplayBackend`、`sqlite_backend(tmp_path=None) -> ReplayBackend`、`redis_backend(url) -> ReplayBackend`、`enabled_backends(tmp_path) -> list[ReplayBackend]`(读 env)

- [ ] **Step 1:** 写测试:
  - `test_in_memory_backend_runs`:create+append+get 一轮不报错。
  - `test_sqlite_backend_persists`:用 tmp_path 文件 DB,写后重开 service 仍能读到。
  - `test_enabled_backends_env`:无 env → [in_memory, sqlite];设 `TRPC_REPLAY_REDIS_URL` 但不可达 → redis 标 skipped(不 crash)。
- [ ] **Step 2:** 跑测试,确认 FAIL。
- [ ] **Step 3:** 实现:`SessionServiceConfig(store_historical_events=True)` + `MemoryServiceConfig(enabled=True)`,均 `clean_ttl_config()`;SQLite 默认 `sqlite:///:memory:`,端到端用 `tmp_path` 文件;Redis 不可达 `pytest.skip`。
- [ ] **Step 4:** 跑测试,确认 PASS。

---

## Task 9: report(schema_version=3)

**Files:**
- Create: `tests/sessions/replay/report.py`

**Interfaces:**
- Consumes: `list[DiffEntry]`、`list[SummaryIssue]`、per-case 状态
- Produces: `build_diff_report(reference_backend, cases_results) -> dict`、`write_report(report, path)`

- [ ] **Step 1:** 写测试:
  - `test_report_schema`:构造 1 match + 1 mismatch + 1 skipped → report 含 `schema_version=3`、`backend_statuses`(skipped 含 reason)、`totals`、`false_positive_rate`、cases[]。
  - `test_report_locates_diff`:mismatch 的 diff 含全部 7 个定位字段。
- [ ] **Step 2:** 跑测试,确认 FAIL。
- [ ] **Step 3:** 实现按设计文档 §4.8 schema 组装;FPR = 正常 case mismatch 数 / 正常 case 总数。
- [ ] **Step 4:** 跑测试,确认 PASS。

---

## Task 10: injectors(快照层 + 端到端)

**Files:**
- Create: `tests/sessions/replay/injectors.py`
- Test: `tests/sessions/test_replay_injections.py`

**Interfaces:**
- Produces: `inject_snapshot_diff(snapshot, kind) -> ReplaySnapshot`(deepcopy 改字段)、`inject_sql_diff(sqlalchemy_session, session_id, kind) -> None`、`inject_redis_diff(redis_client, app, user, sid, kind) -> None`
- **kind** 枚举:`event_author` / `state_value` / `summary_loss` / `summary_overwrite` / `summary_affiliation` / `memory_content` 等(覆盖 10 case 各一种)

- [ ] **Step 1:** 写测试:
  - `test_snapshot_inject_detected`:每种 kind → `compare` 产 ≥1 DiffEntry(快照层)。
  - `test_sql_inject_detected`:SQLite 文件 DB 注入 `event_author`(UPDATE events)→ 重读 → harness 检出。
  - `test_redis_inject_detected`:用真实 `redis.Redis`(有 `TRPC_REPLAY_REDIS_URL`)或 fakeredis 注入 → 检出;无 Redis 则 skip。
- [ ] **Step 2:** 跑测试,确认 FAIL。
- [ ] **Step 3:** 实现:快照层 `copy.deepcopy` + 改字段;SQL 用 `sqlalchemy.text("UPDATE events SET ...")`;Redis 用 `session_key`/`app_state_key` 定位 + `SET`/`HSET`。
- [ ] **Step 4:** 跑测试(无 Redis 时 SQL+快照层必过),确认 PASS。

---

## Task 11: 10 条 replay_cases/*.jsonl

**Files:**
- Create: `tests/sessions/replay/replay_cases/01..10.jsonl`

- [ ] **Step 1:** 按 design §4.9 表写 10 条 case。每条用 operations 数组,显式 `event_id/invocation_id/timestamp/state_delta`;跨 session 用 `session_ref`。case 09 `summary_truncation` 必须含历史事件压缩 + 保留事件 + 新事件;case 10 `retry_recovery` 含 `fail_before_commit` + `retry_event`。
- [ ] **Step 2:** `PYTHONUTF8=1 python -c "import json; [json.loads(l) for f in __import__('glob').glob('tests/sessions/replay/replay_cases/*.jsonl') for l in open(f, encoding='utf-8')]"` 验证全部可解析,且每条 `ReplayCase.model_validate_json` 通过。

---

## Task 12: 主 E2E + 跑全套验收

**Files:**
- Create: `tests/sessions/test_replay_consistency.py`

- [ ] **Step 1:** 写 `test_replay_consistency_lightweight`:跑全部 10 case × `enabled_backends()`(轻量=in_memory+sqlite),断言正常 case 全 `match`、`false_positive_rate==0.0`,生成 `session_memory_summary_diff_report.json`。
- [ ] **Step 2:** 写 `test_injection_detection_100pct`:`test_replay_injections.py` 里 10 case 各注入一种 → `detected == [True]*10`。
- [ ] **Step 3:** 写 `test_summary_three_classes_100pct`:loss/overwrite/affiliation 各注入 → 全检出。
- [ ] **Step 4:** 跑 `PYTHONUTF8=1 pytest tests/sessions/test_replay_*.py tests/sessions/test_allowed_diff_governance.py tests/sessions/test_summary_checks.py -v`,确认全绿、轻量 ≤30s、报告产物生成且可定位。
- [ ] **Step 5:** lint:`PYTHONUTF8=1 yapf -ri tests/sessions/replay tests/sessions/test_replay_*.py tests/sessions/test_allowed_diff_governance.py tests/sessions/test_summary_checks.py && PYTHONUTF8=1 flake8 <同上文件>`。

---

## Self-Review

- **Spec 覆盖**:设计文档 §4 各模块 → Task 2–10;§4.9 case → Task 11;6 条验收 → Task 12 + Task 5(governance)/Task 6(summary 三类)/Task 10(注入);4 交付物 → Task 1(说明)/Task 11(jsonl)/Task 9(报告)/全部(py)。✅
- **类型一致**:`is_allowed`/`compare_snapshots`/`check_summary_issues`/`build_diff_report`/`replay_case` 在各 task 间签名一致。✅
- **占位符**:无 TBD;实现体在 TDD 循环产生(inline 执行约定,非占位)。
- **依赖顺序**:2→3/4/5/6(纯函数)→7(驱动,依赖模型)→8(后端)→9(报告)→10(注入,依赖 comparator+backends)→11(case,依赖模型)→12(E2E)。✅

---

## Execution

Inline 执行(同一 session,设计文档+代码地图在 context 内,无需 subagent 零上下文重载)。逐 task TDD,每 task 跑测试 + lint。
