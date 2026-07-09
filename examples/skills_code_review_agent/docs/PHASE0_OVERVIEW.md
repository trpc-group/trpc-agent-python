# Phase 0 — 基础设施层交付概览

> 自动代码评审 Agent · Phase 0 (Foundation)
> 基于 tRPC-Agent Skill 体系 · 完成日期 2026-07-07

## 完成内容

按 `docs/skills_code_review_agent/specs/phase-0-foundation.md` 的契约，落地了 CR Agent 的持久化基础设施层，为 P1–P6 全部后续阶段提供统一的存储接口。

### 交付物

| 文件 | 职责 | 行数 |
|------|------|------|
| `examples/skills_code_review_agent/db/__init__.py` | 包导出 `ReviewStore` / `SQLiteStore` | — |
| `examples/skills_code_review_agent/db/schema.sql` | 七表 DDL + 7 索引，全部 `IF NOT EXISTS` 可重复执行 | ~95 |
| `examples/skills_code_review_agent/db/storage.py` | `ReviewStore` Protocol + `SQLiteStore` 实现 | ~430 |
| `examples/skills_code_review_agent/db/init_db.py` | 初始化脚本（CLI 可调用，幂等） | ~115 |
| `examples/skills_code_review_agent/tests/test_phase0_foundation.py` | Phase 0 验收测试（20 用例） | ~330 |

### 架构落点对照

- **七表 schema**：`review_task` / `input_diff` / `sandbox_run` / `finding` / `filter_block` / `monitor_summary` / `review_report`，围绕 `review_task` 聚合，`task_id` 为全局外键。
- **复合索引 `idx_finding_dedup (task_id, file, line, category)`**：直接服务 P4 去重查询，O(1) 命中"同文件同行同类"。
- **`finding.bucket` 三档**：从 schema 层强制低置信度不混入高置信结论。
- **`sandbox_run.timed_out`**：INTEGER 0/1（SQLite 无原生 bool）。
- **`finding.confidence`**：REAL（浮点）。
- **`monitor_summary.exception_types`**：JSON 字符串（如 `{"timeout":2,"oom":1}`）。
- **外键约束**：每次连接 `PRAGMA foreign_keys = ON`，孤儿 finding 写入被 `IntegrityError` 拦截。

## 关键设计决策

1. **原生 `sqlite3` 而非 SDK 的 SQLAlchemy 抽象**：SDK `storage` 模块是面向 session/memory 的通用重型抽象；CR Agent 的七张业务表用标准库 `sqlite3` + `row_factory = sqlite3.Row` 实现更轻量、更贴合 phase-0 契约，且 `ReviewStore` Protocol 保留了切换 Postgres 的空间。
2. **`ReviewStore` 为 `runtime_checkable` Protocol**：结构性子类型，`SQLiteStore` 及任何新后端（如 `PostgresStore`）只要实现同方法即满足，上层无感。
3. **批量 finding 用 `executemany`**：单次评审可能产数百条 finding，提供 `add_findings(task_id, list[dict])` 批量方法，一次 round-trip 写入；同时保留单条 `add_finding` 满足契约。
4. **`set_monitor_summary` / `set_report` 为 upsert**：与 task 1:1 关系，重复调用替换而非堆叠。
5. **`get_task` 用分表查询而非巨型 LEFT JOIN**：各子表基数不同，分表查询 + 组装成嵌套 dict，输出更干净稳定。
6. **`_now_iso` 用 timezone-aware**：`datetime.now(timezone.utc)`，规避 3.12+ `utcnow()` 弃用警告，兼容 python>=3.10。

## 验收结果（DoD 全部达成）

| # | 验收标准 | 状态 | 证据 |
|---|----------|------|------|
| 1 | `init_db.py` 能建库建表，重复执行不报错 | ✅ | `test_init_is_idempotent_repeated_runs` + CLI 二次执行 exit=0 |
| 2 | `create_task` 返回 task_id，`update_task_status` 可改状态 | ✅ | `TestTaskLifecycle` 2 用例 |
| 3 | 七个 `add_*` / `set_*` 方法可写入对应表 | ✅ | `TestChildWrites` 8 用例（含批量 executemany） |
| 4 | `get_task(task_id)` 能 join 返回完整记录 | ✅ | `test_full_join_record_shape` 验证七段式结构 |
| 5 | `SQLiteStore` 实现同一 Protocol，可替换后端 | ✅ | `TestProtocolConformance` + `TestInMemoryStore`（FakeStore 通过 isinstance 校验） |

**测试统计**：20 用例全部通过（0.78s），含外键约束拦截验证。

## 运行方式

```bash
# 建库（幂等）
python examples/skills_code_review_agent/db/init_db.py --db-path cr_agent.db

# 跑验收测试
python examples/skills_code_review_agent/tests/test_phase0_foundation.py
```

## 下游影响

P1–P6 全部阶段通过 `from db import ReviewStore, SQLiteStore` 获取存储句柄，不直接触碰 SQLite API。后续阶段只需关注业务逻辑（diff 解析、规则引擎、沙箱执行、去重、编排、报告），持久化层已就绪。
