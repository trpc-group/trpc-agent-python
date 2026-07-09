# Code Review Agent

## 快速运行

```powershell
# 在仓库根目录执行
$env:PYTHONPATH = "examples/skills_code_review_agent"
python examples/skills_code_review_agent/run_agent.py --fixture security --dry-run --output-dir examples/skills_code_review_agent/out --db-path examples/skills_code_review_agent/cr_agent.db

# 使用 diff 文件
python examples/skills_code_review_agent/run_agent.py --diff-file path/to/change.diff --mode dry-run --output-dir out --db-path cr_agent.db

# 审查本地 git 工作区变更
python examples/skills_code_review_agent/run_agent.py --repo-path path/to/repo --mode dry-run --output-dir out --db-path cr_agent.db
```

## 常用参数

| 参数 | 说明 |
| --- | --- |
| `--diff-file PATH` | 读取 unified diff / PR patch 文件。 |
| `--repo-path PATH` | 在指定 git 仓库中读取工作区变更。 |
| `--fixture NAME` | 使用内置 fixture，便于 dry-run 和测试。 |
| `--skill-dir PATH` | 指定 code-review Skill 目录，默认 `skills/code-review`。 |
| `--mode dry-run\|real` | `dry-run` 使用 fake runner；`real` 走沙箱 runtime。 |
| `--dry-run` | `--mode dry-run` 的快捷写法。 |
| `--output-dir DIR` | 输出 `review_report.json` 和 `review_report.md`。 |
| `--db-path PATH` | SQLite 数据库路径。 |
| `--require-sandbox` | real 模式下强制使用声明的沙箱，不可用则失败。 |
| `--telemetry` | 输出 telemetry span，便于审计链路。 |
| `--enable-llm` | 可选开启真实 LLM 二次研判；无 Key 时默认不需要。 |

---
# CR Agent 架构设计

> 自动代码评审 Agent 原型 — 基于 tRPC-Agent Skill 体系
> 本文是**整体架构设计**,不含完整实现代码,但给出可落地的契约:目录结构、建表 DDL、SKILL.md frontmatter、规则清单、监控字段、去重算法与验收标准逐条对照。

---

## 1. 概述

目标不是"让 LLM 评论代码",而是把 **Skills、沙箱执行、数据库、Filter 治理、审查规则、结果结构化、监控审计、安全边界** 串成一个**可验证系统**。输入 git diff / PR patch / 本地变更目录,输出结构化 findings,并把审查任务、拦截记录、监控摘要、结果写入数据库,支持后续评测、监控、回放。

系统采用**六层流水线**架构,数据自上而下流动,其中 Filter 治理与沙箱执行构成安全治理层,监控审计贯穿全链路。

---

## 2. 目录结构

```
examples/skills_code_review_agent/
├── ARCHITECTURE.md              # 本文档
├── README.md                    # 使用说明
├── agent.py                     # Agent 入口(CLI 编排)
├── skills/
│   └── code-review/
│       ├── SKILL.md             # Skill 契约:入口/规则/沙箱策略
│       ├── rules/               # 6 类规则文档
│       │   ├── security.md
│       │   ├── async_errors.md
│       │   ├── resource_leak.md
│       │   ├── missing_tests.md
│       │   ├── sensitive_info.md
│       │   └── db_lifecycle.md
│       └── scripts/             # 沙箱内可执行脚本
│           ├── parse_diff.py    # unified diff 解析 → ChangeSet
│           ├── run_checks.py    # 规则匹配 → 原始诊断
│           ├── dedupe.py        # 去重 + 置信度分流
│           └── mask_secrets.py  # 敏感信息脱敏
├── db/
│   ├── schema.sql               # 建表 DDL
│   ├── storage.py               # 存储接口(SQLite 默认,可切 SQL 后端)
│   └── init_db.py               # 初始化/迁移脚本
├── sandbox/
│   ├── runtime.py               # runtime 抽象:local/container/cube
│   └── policy.py                # 安全边界:超时/输出/env/脱敏/失败
├── filters/
│   └── governance.py            # Filter 治理:四类检查 + 决策
├── tests/
│   ├── fixtures/                # 8 条 diff 样本
│   │   ├── 01_clean.diff
│   │   ├── 02_security.diff
│   │   ├── 03_async_leak.diff
│   │   ├── 04_db_lifecycle.diff
│   │   ├── 05_missing_tests.diff
│   │   ├── 06_duplicate.diff
│   │   ├── 07_sandbox_fail.diff
│   │   └── 08_sensitive_info.diff
│   └── test_cr_agent.py         # 链路测试
└── examples/
    └── review_report.json       # 示例报告输出
```

---

## 3. 分层架构

系统分为六层,数据流自上而下。数据处理层(蓝)负责把 diff 变成结构化结论,安全治理层(珊瑚)在风险点与执行边界上把关,底部监控审计带贯穿全链路。

| 层 | 名称 | 职责 | 关键产出 |
|----|------|------|----------|
| L1 | 输入解析 | 接收 `--diff-file` / `--repo-path` / fixture,解析 unified diff | `ChangeSet`(文件·hunk·行号) |
| L2 | Skill 加载 | `skill_load` 加载 SKILL.md + 6 类规则 + 脚本目录 | `RuleSet` + 脚本清单 |
| L3 | Filter 治理 | 脚本前置决策:高风险/禁止路径/非白名单网络/超预算 | `allow` / `deny` / `needs_human_review` |
| L4 | 沙箱执行 | Container/Cube·E2B(默认)或本地 fallback 跑检查 | 原始诊断(已脱敏) |
| L5 | 去重结构化 | 行级去重 + 置信度分流 + 9 字段组装 | `findings` / `warnings` / `needs_human_review` |
| L6 | 存储输出 | 写 SQLite,生成 report.json + report.md | 数据库记录 + 双格式报告 |

**监控审计(贯穿)**:总耗时、沙箱耗时、工具调用次数、拦截次数、finding 数量、severity 分布、异常类型分布。

---

## 4. 评审数据流

一次完整 review 的主链(9 步),含两条异常分支:

```
CLI 输入 → 解析 diff → skill_load → [Filter 决策] → 沙箱执行 → 去重降噪 → 组装 findings → 落库 → 生成报告
                                      │                  │
                          deny/review ─┘                  └ 沙箱失败 → 降级空诊断
                                      │                                    │
                                      └──────────→ 都汇入去重降噪层 ←──────┘
```

- **Filter 分支**:`deny` / `needs_human_review` 时记录拦截原因,跳过沙箱,直接进入去重层(无诊断)。
- **沙箱失败分支**:超时/崩溃时降级为空诊断,记录失败,继续评审——**异常不崩任务**。
- **dry-run 模式**:L4 替换为本地 fake runner(直接跑规则脚本,不调真实模型 API),其余链路完全一致,无 API Key 也可验证解析→落库→报告全链路。

---

## 5. 数据库 Schema

七张表围绕 `review_task` 聚合,`task_id` 为全局外键。SQLite 默认实现,`storage.py` 接口保留切换 SQL 后端的空间(只暴露 `ReviewStore` 抽象,底层连接可替换)。

### 5.1 表关系

| 主表 | 关系 | 子表 | 语义 |
|------|------|------|------|
| review_task | 1 — N | input_diff | 一个任务可含多个变更文件 |
| review_task | 1 — N | sandbox_run | 一个任务可跑多次沙箱 |
| review_task | 1 — N | finding | 一个任务产出多条 finding |
| review_task | 1 — N | filter_block | 一个任务可有多条拦截 |
| review_task | 1 — 1 | monitor_summary | 一个任务一条监控汇总 |
| review_task | 1 — 1 | review_report | 一个任务一份最终报告 |

### 5.2 建表 DDL(`db/schema.sql`)

```sql
CREATE TABLE IF NOT EXISTS review_task (
  id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  status TEXT NOT NULL,           -- pending|running|done|failed
  input_type TEXT NOT NULL,       -- diff|repo|fixture
  input_ref TEXT,
  mode TEXT NOT NULL,             -- dry-run|real
  total_duration_ms INTEGER
);

CREATE TABLE IF NOT EXISTS input_diff (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES review_task(id),
  file_path TEXT,
  sha256 TEXT,
  hunk_count INTEGER,
  line_count INTEGER,
  summary TEXT
);

CREATE TABLE IF NOT EXISTS sandbox_run (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES review_task(id),
  runtime TEXT,                   -- local|container|cube
  script TEXT,
  status TEXT,                    -- ok|timeout|failed|truncated
  duration_ms INTEGER,
  exit_code INTEGER,
  output_bytes INTEGER,
  timed_out INTEGER,              -- 0|1
  masked_count INTEGER
);

CREATE TABLE IF NOT EXISTS finding (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES review_task(id),
  severity TEXT,                  -- critical|high|medium|low
  category TEXT,
  file TEXT,
  line INTEGER,
  title TEXT,
  evidence TEXT,
  recommendation TEXT,
  confidence REAL,
  source TEXT,                    -- rule|sandbox|llm
  bucket TEXT                     -- findings|warnings|needs_human_review
);
CREATE INDEX IF NOT EXISTS idx_finding_dedup ON finding(task_id, file, line, category);
CREATE INDEX IF NOT EXISTS idx_finding_task ON finding(task_id);

CREATE TABLE IF NOT EXISTS filter_block (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES review_task(id),
  reason TEXT,                    -- high-risk|forbidden-path|network|budget
  target TEXT,
  decision TEXT,                  -- deny|needs_human_review
  detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_filter_task ON filter_block(task_id);

CREATE TABLE IF NOT EXISTS monitor_summary (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES review_task(id),
  total_duration_ms INTEGER,
  sandbox_duration_ms INTEGER,
  tool_calls INTEGER,
  blocks INTEGER,
  finding_count INTEGER,
  sev_critical INTEGER,
  sev_high INTEGER,
  sev_medium INTEGER,
  sev_low INTEGER,
  exception_types TEXT            -- JSON,如 {"timeout":2,"oom":1}
);

CREATE TABLE IF NOT EXISTS review_report (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES review_task(id),
  report_json_path TEXT,
  report_md_path TEXT,
  summary TEXT,
  created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_report_task ON review_report(task_id);
```

### 5.3 关键设计

- **复合索引 `idx_finding_dedup (task_id, file, line, category)`**——直接服务去重查询,O(1) 命中"同文件同行同类"。
- **`finding.bucket` 三档**——从 schema 层强制低置信度不混入高置信结论。
- **`sandbox_run.timed_out` / `output_bytes` / `masked_count`**——把安全边界执行证据持久化,可回放可审计。
- **`monitor_summary` severity 平铺成列**——避免运行时反序列化 JSON,监控查询直接走索引。
- **按 task_id 查询路径**:`SELECT * FROM review_task WHERE id=?` → 关联 `sandbox_run` / `finding` / `filter_block` / `monitor_summary` / `review_report`,一次 join 取回完整审查记录。

---

## 6. 安全治理:Filter + 沙箱

### 6.1 Filter 门禁(L3,沙箱前置)

脚本清单在进入沙箱前必须经过四类检查,任一命中即按策略决策:

| 检查类 | 命中条件 | 默认决策 |
|--------|----------|----------|
| 高风险脚本特征 | 脚本含 `rm -rf` / `sudo` / 网络下载执行 / `eval` 等危险模式 | `deny` |
| 禁止路径 | 访问 `/etc` / `~/.ssh` / 系统目录 / 工作区外路径 | `deny` |
| 非白名单网络 | 脚本发起非白名单域名/端口的网络请求 | `needs_human_review` |
| 超预算执行 | 预估耗时/内存超出预算阈值 | `needs_human_review` |

- `allow` → 进入沙箱。
- `deny` / `needs_human_review` → **不进沙箱**,拦截原因写入 `filter_block` 表 + 报告。
- 拦截记录字段:`reason` / `target` / `decision` / `detail`,可按 `task_id` 查询回放。

### 6.2 沙箱 runtime(L4)

| runtime | 用途 | 说明 |
|---------|------|------|
| Container | 生产默认 | Docker 隔离,资源限额 |
| Cube · E2B | 生产可选 | 远程沙箱,更强隔离 |
| 本地 fallback | 仅 dry-run / 开发 | **不能作为生产方案** |

`runtime.py` 暴露统一 `SandboxRuntime.run(script, input, policy)` 接口,三种实现可切换。

### 6.3 沙箱安全边界(强制五项)

`policy.py` 在每次沙箱执行时强制套用,不可绕过:

| 约束 | 默认值 | 失败行为 |
|------|--------|----------|
| 超时控制 | 30s | 中断进程,`timed_out=1`,降级空诊断 |
| 输出大小限制 | 1MB | 截断,`status=truncated` |
| env 白名单 | `PATH`/`HOME`/`LANG` | 非白名单变量不透传 |
| 敏感信息脱敏 | 正则 + 熵值双检 | 输出落地前脱敏,`masked_count` 记录 |
| 失败记录 | — | 异常写 `sandbox_run`,任务不崩 |

脱敏规则覆盖:明文 API Key、token、password、私钥、连接串。脱敏检出率目标 ≥ 95%,报告和数据库中不得出现明文。

---

## 7. CR Skill 设计

### 7.1 SKILL.md 契约

```yaml
---
name: code-review
description: 自动代码评审 Skill,加载 6 类规则与脚本,在沙箱中执行检查并产出结构化 findings
version: 1.0.0
entry: scripts/run_checks.py
rules:
  - rules/security.md
  - rules/async_errors.md
  - rules/resource_leak.md
  - rules/missing_tests.md
  - rules/sensitive_info.md
  - rules/db_lifecycle.md
sandbox:
  default_runtime: container
  fallback: local
  timeout_s: 30
  max_output_bytes: 1048576
  env_whitelist: [PATH, HOME, LANG]
---
```

`skill_load` 读取 frontmatter,产出 `RuleSet`(规则文档列表)+ 脚本清单,送入 Filter 门禁。

### 7.2 六类规则(覆盖要求的 4 类以上)

| 规则文档 | 覆盖问题 | 检测方式 | 默认 severity |
|----------|----------|----------|---------------|
| `security.md` | SQL 注入、命令注入、硬编码密钥、不安全反序列化 | 静态模式 + 沙箱 semgrep | critical / high |
| `async_errors.md` | 未 await 的协程、未处理 rejection、async 资源泄漏 | AST 解析 | high / medium |
| `resource_leak.md` | 未关闭文件/连接、try 无 finally | AST + 控制流分析 | high / medium |
| `missing_tests.md` | 新增公开函数无对应测试 | diff 关联分析 | low |
| `sensitive_info.md` | 明文 API key / token / password | 正则 + 熵值 | critical |
| `db_lifecycle.md` | 连接未关闭、事务未提交/回滚、连接池泄漏 | AST | high / medium |

### 7.3 脚本目录职责

| 脚本 | 输入 | 输出 | 运行位置 |
|------|------|------|----------|
| `parse_diff.py` | diff 文本 | `ChangeSet` | 本地(L1) |
| `run_checks.py` | `ChangeSet` + `RuleSet` | 原始诊断列表 | 沙箱(L4) |
| `dedupe.py` | 原始诊断列表 | 分流后 findings | 本地(L5) |
| `mask_secrets.py` | 任意文本 | 脱敏后文本 + count | 沙箱输出落地前 |

---

## 8. 监控审计字段

每次 review 写入 `monitor_summary` 一条记录:

| 字段 | 含义 | 来源层 |
|------|------|--------|
| `total_duration_ms` | 评审总耗时 | 全链路 |
| `sandbox_duration_ms` | 沙箱执行总耗时(所有 run 累加) | L4 |
| `tool_calls` | 工具/skill 调用次数 | L2 / L4 |
| `blocks` | Filter 拦截次数 | L3 |
| `finding_count` | finding 总数(三 bucket 合计) | L5 |
| `sev_critical` | critical 级 finding 数 | L5 |
| `sev_high` | high 级 finding 数 | L5 |
| `sev_medium` | medium 级 finding 数 | L5 |
| `sev_low` | low 级 finding 数 | L5 |
| `exception_types` | 异常类型分布(JSON) | 全链路 |

---

## 9. 去重降噪策略

`dedupe.py` 处理流程:

1. 收集所有原始诊断(来自规则匹配 + 沙箱结果)。
2. 按 `(file, line, category)` 三元组分组。
3. **组内取 `confidence` 最高的一条**,其余丢弃——消除"同文件同行同类重复报"。
4. 按置信度分流 `bucket`:
   - `confidence < 0.6` → `needs_human_review`
   - `0.6 ≤ confidence < 0.8` → `warnings`
   - `confidence ≥ 0.8` → `findings`
5. 同一行不同 `category` 的问题**保留**(不误合并不同类问题)。
6. 低置信度问题不混入高置信 `findings`,保证误报率可控。

---

## 10. 方案设计说明

本方案把自动代码评审拆成六层流水线:输入解析、Skill 加载、Filter 治理、沙箱执行、去重结构化、存储输出,监控审计贯穿全链路。Skill 设计上,`code-review` Skill 以 `SKILL.md` frontmatter 声明入口、六类规则清单与沙箱策略,通过 `skill_load` 产出 `RuleSet` 与脚本清单,实现规则与执行解耦。沙箱默认 Container/Cube·E2B 隔离执行,本地仅作 dry-run 与开发 fallback,绝不作为生产方案;沙箱内强制套用超时(30s)、输出限制(1MB)、env 白名单、敏感信息脱敏、失败记录五项安全边界,异常降级为空诊断而不崩任务。Filter 治理在沙箱前置,对高风险脚本、禁止路径、非白名单网络、超预算执行做 `allow`/`deny`/`needs_human_review` 决策,后两者不进沙箱,拦截原因写入报告与数据库。数据库采用七张表围绕 `review_task` 聚合的最小 schema,`task_id` 为全局外键,复合索引 `(task_id,file,line,category)` 直接服务去重查询,支持按 task_id 一次 join 取回完整审查记录;`finding.bucket` 三档从 schema 层强制低置信度不混入高置信结论。去重按 `(file,line,category)` 取最高置信,置信度低于阈值进 warnings 或 needs_human_review,保证误报率 ≤ 15%。dry-run 模式用 fake runner 跑规则脚本,无 API Key 可验证解析→沙箱→落库全链路,耗时 ≤ 2 分钟。

---

## 11. 验收标准对照

| # | 验收标准 | 设计落点 |
|---|----------|----------|
| 1 | 8 条 diff 样本全部可运行并生成报告 | L1-L6 完整链路 + `tests/fixtures/` 8 个样本 |
| 2 | 高危检出率 ≥ 80%,误报率 ≤ 15% | 6 类规则覆盖 + 置信度分流(`confidence` 阈值) |
| 3 | DB 完整记录 task/run/finding/report,按 task_id 查询 | 七表 schema + `idx_*_task` 索引 + join 查询路径 |
| 4 | 沙箱超时+输出限制,失败不崩任务 | `policy.py` 五项约束 + 沙箱失败降级分支 |
| 5 | 脱敏检出率 ≥ 95%,无明文密钥 | `mask_secrets.py` 正则+熵值双检 + `masked_count` 审计 |
| 6 | dry-run ≤ 2 分钟 | fake runner 跳过模型 API,本地跑规则脚本 |
| 7 | 高风险先过 Filter,deny/review 不进沙箱 | `governance.py` 前置决策 + `filter_block` 落库 |
| 8 | 报告含 findings/统计/复核/拦截/监控/沙箱/修复 | `review_report` 八段式模板(见下) |

### 11.1 报告八段式模板(`review_report.json` / `.md`)

```
1. findings 摘要          — 高置信 finding 列表(9 字段)
2. 严重级别统计           — critical/high/medium/low 计数
3. 人工复核项             — needs_human_review 列表
4. Filter 拦截摘要        — 拦截原因/目标/决策
5. 监控指标               — 耗时/调用/拦截/finding 分布/异常分布
6. 沙箱执行摘要           — runtime/状态/耗时/截断/脱敏数
7. 可执行修复建议         — 每条 finding 附 recommendation
8. warnings               — 低置信度问题(不混入 findings)
```

---

## 12. finding 字段契约

每条 finding 至少包含 9 字段:

| 字段 | 类型 | 说明 |
|------|------|------|
| `severity` | string | critical / high / medium / low |
| `category` | string | security / async / resource / tests / sensitive / db |
| `file` | string | 文件路径 |
| `line` | int | 行号 |
| `title` | string | 问题标题 |
| `evidence` | text | 代码证据(已脱敏) |
| `recommendation` | text | 修复建议 |
| `confidence` | float | 0.0-1.0 |
| `source` | string | rule / sandbox / llm |

---
