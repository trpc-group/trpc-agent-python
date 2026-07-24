# Session / Memory / Summary 多后端回放一致性测试框架 — 设计文档

| 项 | 值 |
|---|---|
| Issue | [#89 构建 Session / Memory 多后端回放一致性测试框架](https://github.com/trpc-group/trpc-agent-python/issues/89) |
| 分支 | `feat/session-memory-replay-consistency-89` |
| 日期 | 2026-07-13 |
| 状态 | 设计(待 review) |
| 范围抉择 | 折中整合创新 · summary 走 SDK 确定性模型 · 快照层+端到端后端注入 · 发现的 SDK bug 只报告不改 |

---

## 1. 背景与目标

项目支持 InMemory / SQL / Redis 三类 Session / Memory 后端,以及多轮对话、state 读写、事件追加、长期记忆、Session Summary 等能力。生产中常见"先用 InMemory 开发再切 SQL/Redis",若不同后端在同一条 Agent 轨迹下保存的事件顺序、state、memory 或 summary 不一致,会导致回放错乱、上下文丢失、长期记忆污染、摘要覆盖错误。

**目标**:构建一个可复用的回放一致性框架 —— 用同一组标准化轨迹驱动多个后端,经规范化比较自动产出可定位的差异报告。它既是测试工具,也是后端实现质量的基准。

**非目标(YAGNI)**:
- 不引入 embedding/向量依赖做 summary 语义比较(用分词集合即可,见 §5.6)。
- 不在本 PR 修复发现的 SDK 生产代码 bug(只报告,另开 issue/PR,吸取 #117 PR 不干净的教训)。
- 不做 Web/可视化报告界面(JSON 报告即可)。

---

## 2. 已有方案研究结论(10 个 PR)

研究 issue #89 下全部 10 个公开 PR(#100/114/115/117/120/125/152/153/158/163),**均未 merged、均无真人 review**。

**共识(已是行业最终范式)**:
1. 四段管线:`load JSONL → replay_case(backend, case) → 后端中立快照 → compare → report`。
2. 比较器:dict 按 sorted keys、list 按下标对齐、叶子严格相等。
3. 用 fake/deterministic summary 规避 LLM 不确定性。
4. `allowed_diff` 必须带 reason,反对无脑忽略。
5. summary 做"内容语义 vs 存储元数据"分层比较。
6. 报告 `session_memory_summary_diff_report.json`,每条 diff 内联定位字段。

**最大留白(本设计的创新切入点)**:
- **检出验证全部停留在快照层**(deepcopy 改快照),**无人做端到端后端数据注入** —— 直接违背 issue"后端实现质量基准"立意。
- **summary 内容比较最多到 `compact+casefold`**,无真正语义比较(issue 却要"语义")。
- **`allowed_diff` 缺治理**(规则引擎灵活但无上限,易被滥用塞入真不一致)。
- **Redis 三后端从未真跑**(全 env opt-in、CI 跳过)。
- **轻量模式诚实性**:#163 单后端也报 `match`(假绿灯),#153 用 `not_applicable`。

---

## 3. 整体架构

### 3.1 四段管线

```
replay_cases/*.jsonl ──load──▶ ReplayCase
                                  │
                  ┌───────────────┼───────────────┐
                  ▼               ▼               ▼
            InMemory          SQLite           Redis        ← ReplayBackend
                  │               │               │
                  └─────── replay_case(backend, case) ──────┐
                                  │                          │
                          后端中立快照 ReplaySnapshot         │
                                  │                          │
                          normalize(占位符)                   │
                                  │                          │
              compare_snapshots(reference, candidate)         │
                  │                                           │
          ┌───────┴────────┐                                  │
          ▼                ▼                                  │
     DiffEntry[]    summary_checks(三类专项)                  │
                  │                                           │
          build_diff_report ◀────────────────────────────────┘
                  │
          session_memory_summary_diff_report.json
```

### 3.2 目录结构(严守干净 — 全部落在 `tests/sessions/` 下,文档随测试代码同置)

```
tests/sessions/replay/
├── README.md              # 测试运行说明与注意事项
├── IMPLEMENTATION_PLAN.md          # 实施计划(随测试同置,无日期前缀)
├── 2026-07-13-session-memory-replay-consistency-design.md   # 设计文档(本文档)
├── __init__.py            # 含 150–300 字设计说明(issue 交付物)
├── harness.py             # ReplayCase/ReplayBackend/ReplaySnapshot + replay_case()
├── normalizer.py          # 占位符归一化(保留字段存在性)
├── comparator.py          # 递归 visit() + DiffEntry(内联定位)
├── allowed_diff.py        # JSONPath 精确匹配 + reason + 覆盖率上限治理
├── summary_checks.py      # session_id 匹配 + loss/overwrite/affiliation 三类专项
├── injectors.py           # 快照层注入 + 端到端后端注入(SQL 行 / Redis key)
├── report.py              # 统一 schema_version=3 报告
├── backends.py            # 三后端实例化 + env 门控
└── replay_cases/
    ├── 01_single_turn.jsonl
    ├── 02_multi_turn.jsonl
    ├── 03_tool_round_trip.jsonl
    ├── 04_state_overwrite.jsonl
    ├── 05_memory_preference.jsonl
    ├── 06_memory_fact_update.jsonl
    ├── 07_summary_create.jsonl
    ├── 08_summary_update.jsonl
    ├── 09_summary_truncation.jsonl
    └── 10_retry_recovery.jsonl
tests/sessions/test_replay_consistency.py        # 主 E2E
tests/sessions/test_replay_injections.py         # 快照层 + 端到端注入检出
tests/sessions/test_allowed_diff_governance.py   # 精确匹配 + 覆盖率上限
tests/sessions/test_summary_checks.py            # 三类 summary 故障
tests/sessions/session_memory_summary_diff_report.json   # 报告产物(运行时生成)
```

---

## 4. 核心模块设计

### 4.1 harness.py — 数据模型与回放驱动

```python
class ReplayOp(BaseModel):
    op: Literal[
        "create_session", "append_event", "function_call", "function_response",
        "update_state", "memory_store", "memory_search",
        "create_summary", "update_summary",
        "fail_before_commit", "retry_event",
    ]
    # 各 op 的确定性 payload:显式 event_id / invocation_id / timestamp /
    # state_delta / memory_key / memory_query / session_ref(跨 session 引用)
    ...

class AllowedDiffRule(BaseModel):
    path: str            # JSONPath,如 "events[0].timestamp"
    reason: str          # 必填,解释为何允许
    backend_pair: tuple[str, str] | None = None  # 可选,限定后端对

class ReplayCase(BaseModel):
    case_id: str
    description: str
    operations: list[ReplayOp]
    allowed_diff: list[AllowedDiffRule] = []
# 注:10 条 jsonl 均为**正常一致性轨迹**;人为不一致由 injectors.py 在运行时
# 程序化派生(快照层 deepcopy 改字段 / 端到端改后端数据),不写进 case 文件。
# 因此验收 2(注入检出)与验收 3(FPR)用的是**同一组 10 条 case**:
#   - 不注入 → 应 100% match(测 FPR)
#   - 注入   → 应 100% 检出(测检出率)

class ReplayBackend:
    name: str
    session_service: SessionServiceABC
    memory_service: MemoryServiceABC | None

class ReplaySnapshot(BaseModel):
    session_id: str
    events: list[dict]          # 归一化前的事件
    historical_events: list[dict]
    state: dict                 # 已合并 app:/user:/session,已剥离 temp:
    memory: dict                # per query 的检索结果
    summary: dict               # {current: {...} | None, history: [...]}

async def replay_case(backend: ReplayBackend, case: ReplayCase) -> ReplaySnapshot:
    """顺序执行 operations,采集后端中立快照。每 case 独立 app_name 命名空间,避免失败重跑污染。"""
```

### 4.2 comparator.py — 递归比较器(内联定位)

```python
class DiffEntry(BaseModel):
    session_id: str | None
    event_index: int | None     # event 在 events[] 中的下标
    summary_id: str | None
    field_path: str             # 如 "events[0].content.parts[0].text"
    reference_backend: str
    candidate_backend: str
    reference_value: Any
    candidate_value: Any
    allowed: bool
    reason: str | None

def compare_snapshots(reference: ReplaySnapshot, candidate: ReplaySnapshot,
                      *, reference_backend: str, candidate_backend: str,
                      allowed_diff: list[AllowedDiffRule]) -> list[DiffEntry]:
    """单一递归 visit(left, right, path):
       - dict → 按 sorted(keys) 对齐
       - list → 按下标对齐,长度差补 <missing>
       - 叶子 → left == right
    比较时直接内联写入 session_id/event_index/summary_id(取 #163 而非 #153 事后反查)。"""
```

### 4.3 normalizer.py — 占位符归一化

```python
NORMALIZED = "<normalized>"

def normalize_event(e: dict) -> dict:
    """timestamp / id / invocation_id → NORMALIZED(保留字段存在性,优于 #100 的 pop 删除)。"""

def normalize_snapshot(s: ReplaySnapshot) -> ReplaySnapshot:
    """- 事件/记忆归一化
       - 剥离 temp: state
       - memory 结果按 json.dumps(sort_keys=True) 排序
       - JSON 序列化统一 sort_keys,消除序列化字段顺序差"""
```

### 4.4 allowed_diff.py — JSONPath 精确 + 覆盖率治理(创新)

```python
def is_allowed(field_path: str, backends: tuple[str, str],
               rules: list[AllowedDiffRule]) -> tuple[bool, str | None]:
    """精确匹配:events[0].timestamp;[N]→[*] 通配。规避 #117 的 *.id 过宽(会误放业务 id)。"""

# —— 治理创新 ——
MAX_ALLOWED_PER_CASE = 8          # 每 case allowed 条数上限
MAX_ALLOWED_RATIO = 0.10          # allowed 占该 case 总比较字段的比例上限

def check_governance(case: ReplayCase, total_fields: int,
                     used_allowed: int) -> None:
    """超限 → fail。防'用 allowed_diff 塞进真不一致'。test_allowed_diff_governance.py 强制。"""
```

### 4.5 summary_checks.py — SDK 确定性模型 + 三分比较 + 三类专项

**确定性模型**(跑 SDK 真实压缩流程,只换 LLM;覆写点已确认存在 [`_compress_session_to_summary`](../../../trpc_agent_sdk/sessions/_session_summarizer.py) L197):

```python
class _DeterministicSummarizer(SessionSummarizer):
    async def _compress_session_to_summary(self, ...) -> str:
        return f"{session_id} summary rev v{n}: {covered_events}"  # 确定性,无 LLM

# SummarizerSessionManager(auto_summarize=True) 挂到 service
```

**三分比较**:
- `text`:分词集合语义比较(§4.6)。
- `metadata`:`version` / `session_id` / `supersedes` 严格相等。
- `coverage`:summary 覆盖的事件集合。

**`summary.version` 形式化**:SDK 无持久 version 字段(对齐 #158 诚实做法)→ 用「生成序号 + supersedes 链」表达可观测修订状态。

**三类专项检测**(对应验收第 4 条):
```python
class SummaryIssue(BaseModel):
    type: Literal["loss", "overwrite", "affiliation"]
    session_id: str
    summary_id: str | None
    detail: dict

# loss:          current is None
# overwrite:     version 倒退(旧版覆盖新版)
# affiliation:   summary.session_id 与所属 session 不符
```

### 4.6 summary 内容语义比较(创新,纯 stdlib)

```python
def summary_text_similarity(a: str, b: str) -> float:
    """分词(去标点/小写)→ 集合 → Jaccard 相似度。"""

SUMMARY_SIM_THRESHOLD = 0.8   # 之上判一致,之下落差异并附相似度分
```
10 个 PR 最多到 `compact+casefold`;本设计用分词集合兑现 issue"语义比较"要求,无外部依赖、确定性(embedding 引入依赖+不确定性,YAGNI 不取)。

### 4.7 injectors.py — 检出验证(核心创新:快照层 + 端到端)

| 层 | 机制 | 验证目标 |
|---|---|---|
| **快照层**(对齐 10 PR) | `deepcopy` 快照改字段 → `compare` → 断言 `DiffEntry` 出现 | 比较器检出率 |
| **端到端后端注入**(留白填补) | 跑完 case 后**直接改后端数据**再重读 → 断言 harness 检出 | harness 对**真实后端漂移**的感知 |

**端到端注入实现**(key/表/列均已确认,见 §7):
```python
# SQL:用真实 SQLAlchemy session 改行
UPDATE events SET author=:bad WHERE session_id=:sid AND index=:i;   # 改事件
UPDATE app_states SET value=:bad WHERE ...;                         # 改 state
# → service.get_session() 重读 → compare → 断言检出

# Redis:用相同 key 构造函数定位 key,改值
session_key(app, user, sid) → SET 改 session JSON 某 field          # session_key/app_state_key/user_state_key
app_state_key(app) → HSET 改 hash 某 field
# → 重读 → compare → 断言检出
```
SQLite 走 `tmp_path` 文件 DB(非 `:memory:`)以支持外部改写。

### 4.8 report.py — 统一报告 schema(schema_version=3)

```jsonc
{
  "schema_version": 3,
  "reference_backend": "in_memory",
  "compared_backends": ["sqlite", "redis"],
  "backend_statuses": [
    {"name": "redis", "status": "skipped", "reason": "TRPC_REPLAY_REDIS_URL unset"}
  ],
  "totals": {"cases": 10, "matched": 9, "mismatched": 1, "not_applicable": 0},
  "false_positive_rate": 0.0,   // 仅正常 case 计入分母
  "cases": [{
    "case_id": "summary_update",
    "session_id": "replay-summary-update",
    "status": "match",          // match | mismatch | not_applicable | skipped
    "differences": [{
      "session_id": "replay-...",
      "event_index": 0,
      "summary_id": null,
      "field_path": "events[0].author",
      "reference_backend": "in_memory",
      "candidate_backend": "sqlite",
      "reference_value": "user",
      "candidate_value": "assistant",
      "allowed": false,
      "reason": null
    }],
    "summary_issues": [{"type": "overwrite", "session_id": "...", "summary_id": "...", "detail": {...}}]
  }]
}
```
**不嵌全量 snapshot**(吸取 #115 报告 10427 行不可审的教训)。

### 4.9 replay_cases — 10 条 case(覆盖 issue 全 8 类)

operations 数组风格(取 #115 扩展性最强),显式 `event_id/invocation_id/state_delta`,跨 session 用 `session_ref`。

| # | case_id | 覆盖 issue case |
|---|---|---|
| 01 | single_turn | 1 单轮对话 |
| 02 | multi_turn | 2 多轮对话 |
| 03 | tool_round_trip | 3 工具调用(function_call/response) |
| 04 | state_overwrite | 4 state 多次写入覆盖 |
| 05 | memory_preference | 5 memory 写入读取 |
| 06 | memory_fact_update | 5 memory 事实更新 |
| 07 | summary_create | 6 summary 生成 |
| 08 | summary_update | 6 summary 更新(version/supersedes/updated_at) |
| 09 | summary_truncation | 7 summary 与事件截断(保留+summary+新事件还原上下文) |
| 10 | retry_recovery | 8 异常恢复(重复写入/脏状态/错误 summary) |

---

## 5. 后端接入与运行模式

```python
# backends.py
def _in_memory_backend() -> ReplayBackend: ...
def _sqlite_backend(tmp_path) -> ReplayBackend:
    # SessionServiceConfig(store_historical_events=True).clean_ttl_config()
    # SQLite 默认 :memory:;端到端注入用 tmp_path 文件 DB
def _redis_backend(url: str) -> ReplayBackend: ...
```

| env | 作用 | 默认 |
|---|---|---|
| `TRPC_REPLAY_LIGHTWEIGHT` | =1 只跑 InMemory vs SQLite(轻量模式,≤30s) | 1 |
| `TRPC_REPLAY_REDIS_URL` | 设置则启用 Redis 集成模式 | 未设置→skip |
| `TRPC_REPLAY_SQL_URL` | 自定义 SQL 连接串 | sqlite 默认 |

Redis/MySQL 不可用时 `pytest.skip`(满足 issue"不要求本地装真 Redis/MySQL")。

---

## 6. 验收标准映射(可检测性写硬)

| 验收 | 落点 | 如何证明 |
|---|---|---|
| 1 InMemory + 持久化 | `_sqlite_backend` + 主 E2E | 轻量模式默认 InMemory vs SQLite |
| 2 10 case 100% 检出注入 | `test_replay_injections.py` | 10 条 case 各由 injectors 程序化注入一种不一致(快照层 + 端到端),断言全部检出(`detected == [True]*10`) |
| 3 误报率 ≤5% | `false_positive_rate` 字段 | **FPR 定义**:10 条 case 在**不注入**状态下被误判 mismatch 的比例。正常应 100% match,FPR=0。注入测试单独在 `test_replay_injections.py`,不计入分母 |
| 4 summary 三类 100% | `test_summary_checks.py` | loss/overwrite/affiliation 各注入一个,断言 `SummaryIssue` 出现 |
| 5 报告定位 | DiffEntry schema | 每条 diff 含 session_id/event_index/summary_id/field_path/双后端值 |
| 6 轻量 ≤30s + 集成 env | env 门控 + CI | 轻量默认跑;Redis/SQL env opt-in,不可用 skip |

---

## 6.1 正向与负向双向验证(正确场景 + 错误场景均覆盖)

> 回应 review:本框架**同时**验证「正确场景」(各后端一致时应判 match)与「错误场景」(后端真不一致时必须检出),二者共用同一组 10 条标准化 case,形成「不误报 + 不漏报」的双向闭环。

| 方向 | 场景 | 机制 | 期望 | 落点 |
|---|---|---|---|---|
| **正向(正确场景)** | 各后端在正常一致性轨迹下应判定一致 | 10 条 case **不注入**,直接 `replay_case` + `compare` | 100% `match`,误报率 `false_positive_rate == 0.0` | `tests/sessions/test_replay_consistency.py`(验收 1/3) |
| **负向(错误场景)** | 后端真不一致时必须被检出 | 同一组 10 条 case,经 `injectors.py` 程序化注入不一致 | 100% 检出(**0 漏报**) | `tests/sessions/test_replay_injections.py`(验收 2/4) |

**负向(错误场景)注入两层覆盖**:

- **快照层注入**(对齐 10 个公开 PR):`deepcopy` 快照改字段,覆盖 event / state / memory / summary 四类共 8 种 kind —— `event_author` / `event_text` / `extra_event` / `state_value` / `memory_content` / `summary_loss` / `summary_overwrite` / `summary_affiliation`,断言比较器与 `summary_checks` 全部检出(`TestSnapshotInjection::test_all_eight_kinds_detected`),且 10 条 case 各注入一种必须 100% 检出(`test_each_case_detects_injection`)。
- **端到端后端注入**(本设计创新,填补 10 PR 留白):跑完 case 后**直接改 SQL 行 / Redis key** 再用全新 service 重读,验证 harness 对**真实后端数据漂移**的感知(`TestEndToEndSqlInjection` / `TestEndToEndRedisInjection`)。

**结论**:同一组 10 条 case 既验证「一致时不误报」(FPR),又验证「不一致时不漏报」(检出率),双向均有断言,杜绝「只测绿灯、漏测红灯」。错误场景验证完整清单见 `tests/sessions/test_replay_injections.py`。

---

## 7. 可行性确认(硬点已验证)

| 硬点 | 验证 | 结论 |
|---|---|---|
| SDK 确定性模型覆写点 | `_compress_session_to_summary` 存在于 [`_session_summarizer.py:197`](../../../trpc_agent_sdk/sessions/_session_summarizer.py) | ✅ 可覆写 |
| Redis 端到端注入 key | `session_key`/`app_state_key`/`user_state_key` + `SET`/`HSET`([`_redis_session_service.py:339`](../../../trpc_agent_sdk/sessions/_redis_session_service.py)) | ✅ 可定位 |
| SQL 端到端注入表 | `sessions`/`events`/`app_states`/`user_states` + `from_event/to_event`([`_sql_session_service.py:142`](../../../trpc_agent_sdk/sessions/_sql_session_service.py)) | ✅ 可 UPDATE 重读 |

---

## 8. 风险与缓解

| 风险 | 缓解 |
|---|---|
| Redis `HGETALL` 返回 bytes(#163 发现的 SDK bug)在未修主干上触发 | 端到端 Redis 注入若触发,在报告"已知 SDK 不一致"节记录;不在本 PR 改生产代码(只报告不改) |
| `_compress_session_to_summary` 是 SDK 内部方法,SDK 重构可能失效 | 覆写点集中在一处;设计说明标注此依赖 |
| SDK 无持久 `summary.version` 字段 | 形式化为「生成序号 + supersedes 链」可观测修订状态(对齐 #158) |
| 端到端注入依赖具体表/key 结构 | 已确认(§7);注入代码集中 `injectors.py`,结构变化只改一处 |
| FPR/检出率标准各 PR 不统一 | 本设计明确定义(§6),并 `test_*` 强制 |
| **SQLite summary 持久化漂移(实测发现)** | `create_session_summary` 后 SQLite `get_session` 读回的 events 顺序 / historical_events / summary 与 InMemory 不一致(类 issue #163 的 summarizer 锚点 timestamp 问题);框架在 `summary_update` / `summary_truncation` case 检出,标 `KNOWN_DRIFT` 不计入 FPR 分母,**只报告不改**,修 bug 另开 issue/PR |

---

## 9. 交付物

1. `tests/sessions/test_replay_consistency.py` + `tests/sessions/replay/` harness 包
2. `tests/sessions/replay/replay_cases/*.jsonl`(10 条)
3. `tests/sessions/session_memory_summary_diff_report.json`(运行时生成)
4. 150–300 字设计说明(本文档 §10 + 测试包 `__init__.py` docstring)
5. 本设计文档 + 实施计划(均置于 `tests/sessions/replay/`,随测试代码同置)

---

## 10. 设计说明(150–300 字,同步至测试包 `__init__.py`)

本框架用同一组标准化 Agent 轨迹驱动 InMemory / SQLite / Redis 三个后端,经四段管线 `load → replay_case → 后端中立快照 → compare → report` 比较事件、状态、长期记忆与会话摘要的一致性。**归一化策略**:对 timestamp、自动生成 id、invocation_id 等非业务字段用占位符替换(保留字段存在性,优于直接删除),剥离 temp: 临时状态,memory 结果按确定性键排序,JSON 统一 sort_keys 序列化以消除字段顺序差异。**summary 比较策略**:采用 SDK 确定性模型(覆写 `_compress_session_to_summary` 换掉 LLM,跑真实压缩流程)生成确定性摘要,再做三分比较 —— 文本走分词集合 Jaccard 语义比较(纯标准库,无 embedding 依赖),元数据(version/session_id/supersedes)严格相等,并按 session_id 匹配后专项检测 loss/overwrite/affiliation 三类故障;因 SDK 无持久 version 字段,形式化为「生成序号 + supersedes 链」可观测修订状态。**允许差异(allowed_diff)**:用 JSONPath 精确匹配 + 强制 reason,并设每 case 条数与占比上限防滥用,绝不无脑忽略。**后端接入**:轻量模式默认 InMemory vs SQLite(≤30s),Redis/MySQL 经环境变量启用,不可用时 skip,并提供 mock/sqlite 跳过策略。**创新点**:在所有公开方案的快照层注入之外,新增端到端后端数据注入(直接改 SQL 行 / Redis key 后重读),真正验证 harness 对后端数据漂移的感知能力,兑现"后端实现质量基准"的立意。发现的 SDK 不一致只在报告中列出,不在本 PR 改生产代码。

---

## 11. 创新点小结(vs 10 个 PR)

| 创新点 | 来源 |
|---|---|
| 端到端后端数据注入(SQL 行 / Redis key 改写重读) | 原创(10 PR 全缺) |
| summary 分词集合 Jaccard 语义比较 | 原创(10 PR 最多 compact+casefold) |
| allowed_diff 覆盖率上限治理(条数 + 占比) | 原创 |
| 诚实 `not_applicable` 标记单后端 | 吸收 #153 |
| 占位符归一化(保留字段存在性) | 吸收 #115(优于 #100 pop) |
| JSONPath 精确 allowed_diff + 强制 reason | 吸收 #115 + #163 |
| summary 按 session_id 匹配 + 三类专项 | 吸收 #152 + #117 |
| SDK 确定性模型驱动 summary | 吸收 #158 |
| PR 严守干净(只碰 tests/ + 文档) | 吸取 #117 教训 |
