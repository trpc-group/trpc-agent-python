# Session / Memory / Summary 多后端回放一致性测试

用同一组标准化 Agent 轨迹驱动 InMemory / SQLite / Redis 三个后端,经四段管线
`load → replay_case → 后端中立快照 → compare → report` 比较**事件 / state / memory / summary** 的一致性。
既是测试工具,也是后端实现质量的基准。完整设计见同目录
[`2026-07-13-session-memory-replay-consistency-design.md`](2026-07-13-session-memory-replay-consistency-design.md),
实施步骤见 [`2026-07-13-session-memory-replay-consistency.md`](2026-07-13-session-memory-replay-consistency.md)。

## 快速运行

```bash
# 轻量模式(默认,无需任何外部依赖,≤30s):InMemory vs SQLite
PYTHONUTF8=1 pytest tests/sessions/test_replay_consistency.py \
                    tests/sessions/test_replay_injections.py \
                    tests/sessions/test_replay_unit.py -v

# 启用 Redis 集成模式(需要本机/CI 有可达 Redis)
TRPC_REPLAY_REDIS_URL=redis://localhost:6379/0 PYTHONUTF8=1 pytest tests/sessions/ -v
```

## 测试需要注意的地方

### 1. 完全确定性,无需 API Key / 网络
- **无 LLM、无真实网络**:summary 用 `DeterministicSummarizer`(覆写 `_compress_session_to_summary`,
  返回确定性文本),时间戳 / 自动 id / invocation_id 经占位符归一化。**不需要任何模型 API Key**,CI 可离线跑。
- 因此结果可复现:同一组 case 在同一后端组合下,差异报告逐字节稳定。

### 2. 后端启用由环境变量门控(不可用自动 skip,不会 fail)
| 环境变量 | 作用 | 默认 |
|---|---|---|
| `TRPC_REPLAY_SQL_URL` | 自定义 SQL 连接串 | `sqlite:///:memory:`(轻量) |
| `TRPC_REPLAY_REDIS_URL` | 设置即启用 Redis 集成模式 | **未设置 → Redis 用例 `pytest.skip`** |

- **轻量模式默认就跑**(InMemory + SQLite `:memory:`),**不要求**本地装 Redis/MySQL。
- Redis 不可用时测试**自动 skip**,不会报错失败 —— 不要为了让 CI 变绿而注释掉 Redis 用例。

### 3. 正向(正确场景)+ 负向(错误场景)双向验证
同一组 10 条标准化 case 同时承担两个方向的验证,**不要只看绿灯**:
- **正向**(`test_replay_consistency.py`):case **不注入** → 各后端应 100% `match`,
  断言 `false_positive_rate == 0.0`(不误报)。
- **负向**(`test_replay_injections.py`):case 经 `injectors.py` **程序化注入不一致**
  (快照层 8 种 kind + 端到端改 SQL 行 / Redis key)→ 必须 100% 检出(不漏报)。

### 4. 已知 drift 是「框架价值」,不是测试 bug
- `summary_update` / `summary_truncation` 两个 case **会**检出 SQLite summary 持久化漂移
  (`create_session_summary` 后 SQLite `get_session` 读回的 events 顺序 / historical_events / summary
  与 InMemory 不一致,类 issue #163 的 summarizer 锚点问题)。
- 这是框架**正确发现**的 SDK 真问题,以 `KNOWN_DRIFT` 标记、**不计入误报率分母**,
  遵循设计 §8「只报告不改」—— 修 bug 另开 issue/PR,**不要在本测试里用 `allowed_diff` 把它掩盖掉**。

### 5. `allowed_diff` 治理:严禁滥用
- 每条 `allowed_diff` 必须带 `reason`,且有**条数上限(8/ case)与占比上限(10%)**。
- 用 JSONPath **精确匹配**(`events[0].timestamp`),禁止 `*.id` 这类过宽规则(会误放业务 id)。
- 任何「为了让用例过」而塞进 `allowed_diff` 的真不一致,都会被 governance 测试拒绝。

### 6. Windows 运行注意
- 必须 `PYTHONUTF8=1`(否则中文 case / 报告 JSON 序列化乱码)。
- `python-magic` 在 Windows 上会致 SDK 导入崩溃,需改用 `python-magic-bin`(venv 内替换)。

### 7. 报告产物位置
- 运行 `test_replay_consistency.py` 会生成 / 覆盖 `tests/sessions/session_memory_summary_diff_report.json`
  (schema_version=3,每条 diff 内联 `session_id` / `event_index` / `summary_id` / `field_path` + 双后端值,
  不嵌全量 snapshot)。该文件作为测试基线产物**已纳入版本管理**,review/排障时可直接查看。

## 目录结构

```
tests/sessions/replay/
├── README.md                                          # 本文件
├── 2026-07-13-session-memory-replay-consistency-design.md     # 设计文档
├── 2026-07-13-session-memory-replay-consistency.md           # 实施计划
├── __init__.py            # 包入口 + 设计说明
├── harness.py             # 数据模型 + replay_case() 驱动
├── normalizer.py          # 占位符归一化
├── comparator.py          # 递归比较 + DiffEntry(内联定位)
├── allowed_diff.py        # JSONPath 精确匹配 + 覆盖率治理
├── summary_checks.py      # summary 三类专项(loss/overwrite/affiliation)
├── injectors.py           # 快照层 + 端到端后端注入(错误场景)
├── report.py              # schema_version=3 差异报告
├── backends.py            # 三后端实例化 + env 门控 + 确定性 summarizer
└── replay_cases/cases.jsonl   # 10 条标准化轨迹
tests/sessions/
├── test_replay_consistency.py    # 主 E2E(正向:一致性 + FPR)
├── test_replay_injections.py     # 注入检出(负向:错误场景)
├── test_replay_unit.py           # 模块单测
└── session_memory_summary_diff_report.json   # 报告产物(运行时生成)
```
