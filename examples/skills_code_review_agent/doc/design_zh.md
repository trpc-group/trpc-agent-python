# Skills Code Review Agent 方案设计说明

## 1. 概述

Skills Code Review Agent 是基于 tRPC-Agent-Python SDK 构建的自动化代码评审原型。通过 `code-review` Skill 加载评审规则和脚本，经 Filter 治理审批后在隔离工作空间中执行检查，输出结构化发现项，并将任务、沙箱运行记录、拦截事件、监控摘要和报告持久化到 SQL 数据库（默认 SQLite）。

## 2. 架构

```
   统一 diff 输入（文件 / 仓库 / 内置样例）
              │
              ▼
      +-- 解析 diff --+
      |  (parse_diff) |
      +--------------+
              │
              ▼
   +--- 治理过滤器 -------+
   | 脚本白名单、路径限制   |
   | 网络禁止、预算上限    |
   | 高风险 → 人工复核     |
   +----------------------+
              │
              ▼
   +-- 沙箱执行 (container / local / cube) --+
   | env -i (仅 PATH+HOME+LANG)              |
   | security | async_leak | db_lifecycle    |
   | tests_missing | secrets                  |
   | 超时 60s, 输出截断 256KB                  |
   +------------------------------------------+
              │
              ▼
   +--- LLM 富化（可选）---+
   | 置信度增强、误报抑制   |
   | 文字摘要              |
   +-----------------------+
              │
              ▼
   +--- 去重 + 置信度过滤 ---+
   | file:line:category      |
   | confidence >= 0.6       |
   +-------------------------+
              │
              ▼
   +--- SQLite 持久化 ---+
   | 6 张表, task-id 索引 |
   +----------------------+
              │
              ▼
   +-- 脱敏 + 报告生成 --+
   | json / markdown     |
   +---------------------+
```

流水线为确定性执行：解析 → 治理 → 沙箱 → 合并 → 去重 → 持久化 → 报告。`code-review` Skill 内部的规则脚本生成基线发现项（`source: "static"`），确保 ≥80% 检出率 / ≤15% 误报率的验收标准不依赖 LLM。可选的 `LlmAgent` 富化步骤用于增强置信度并抑制误报。`--dry-run` 模式下使用 `FakeReviewModel` 返回确定性响应，整个链路无需 API Key 即可运行。

## 3. Skill 设计

采用 SKILL.md 声明 + 规则文档 + 脚本的架构：

```
skills/code-review/
├── SKILL.md                    # 前端元数据 + 使用说明 + 规则索引
├── references/rules/           # 每类规则一篇文档
└── scripts/
    ├── diffparse.py            # unified diff 解析器（纯标准库）
    ├── parse_diff.py           # CLI: diff → JSON 摘要
    ├── check_security.py       # eval/exec, shell=True, pickle, yaml.load, SQL 注入
    ├── check_async_leak.py     # 无引用 task、未管理 session/file
    ├── check_db_lifecycle.py   # 连接/游标/事务生命周期
    ├── check_tests_missing.py  # 源文件变更缺少对应测试
    ├── check_secrets.py        # 硬编码密钥（证据已预脱敏）
    ├── secret_patterns.py      # 共享规则库（沙箱 + 宿主共用）
    └── checklib.py             # 共享工具：load_files, finding, emit
```

每个 checker 脚本以 JSON 契约输出 `{"findings": [...]}`，每个发现项包含 `severity, category, file, line, title, evidence, recommendation, confidence, source` 字段，确保静态分析与 LLM 富化结果统一格式。

## 4. 沙箱隔离策略

- **Container（生产默认）**：`create_container_workspace_runtime()`，使用 `python:3-slim` Docker 镜像。Skill 目录通过 `stage_directory` 阶段化到工作空间，diff 文件阶段化到 `work/inputs/changes.diff`。
- **Local（仅开发调试）**：`create_local_workspace_runtime()`，输出警告提示不可用于生产。
- **Cube/E2B**：通过环境变量配置凭证。

### 安全边界

| 边界 | 机制 |
|---|---|
| 环境隔离 | `env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/tmp LANG=C.UTF-8` — 仅三个环境变量进入沙箱进程，彻底清除宿主环境 |
| 超时控制 | 单脚本 60 秒超时（可配置），总预算 300 秒 / 20 次运行 |
| 输出上限 | 每路 stdout/stderr 截断至 256 KB，截断标记 |
| 故障容错 | 非零退出码、超时、运行时异常均记录为失败的 `cr_sandbox_runs` 行，带 `error_type`。评审继续执行剩余脚本，绝不因沙箱失败导致整体崩溃 |

## 5. Filter 治理策略

`GovernanceEngine` 实施多层策略：

| 策略 | 规则 | 决策 |
|---|---|---|
| 脚本白名单 | 仅允许 6 个已知 checker 脚本执行 | `deny` |
| 禁止路径 | 绝对路径、`..`、`~` 逃逸 | `deny` |
| 网络禁止 | `curl`, `wget`, `pip`, `git`, `ssh`, `apt` 等 | `deny` |
| 高风险标记 | `sudo`, `docker`, `chmod`, `rm`, `mkfs` 等 | `needs_human_review` |
| 预算限制 | 超过 20 次运行或 300 秒累计沙箱时间 | `deny` |

双重执行点：
1. `pipeline.py` 中的确定性编排器在每次沙箱执行前咨询引擎
2. `GovernanceToolFilter`（`BaseFilter` 子类，`FilterType.TOOL`）守护 LLM 发起的 `skill_run` 工具调用

`deny` 和 `needs_human_review` 决策绝不会进入沙箱。每次决策均写入 `cr_filter_events` 并在报告中总结。

## 6. 去重降噪

- **去重**：键 = `(file, line, category)`。重复项合并：保留最高 severity 和最高 confidence，来源合并为 `"static+llm"`。被丢弃的行记录为 `deduped` 状态。
- **置信度过滤**：`confidence < 0.6` 的发现从主报告排除，进入 `needs_human_review` 人工确认区域。

## 7. 数据库 Schema

六表设计，基于独立的 SQLAlchemy `DeclarativeBase`，通过 SDK 的 `SqlStorage(is_async=False, db_url=..., metadata=CrBase.metadata)` 管理。默认 `sqlite:///code_review.db`，可切换任意 SQLAlchemy 支持的 `db_url`。

| 表 | 关键字段 |
|---|---|
| `cr_review_tasks` | `id` (uuid), `created_at`, `finished_at`, `status`, `input_type`, `input_ref`, `runtime`, `dry_run`, `diff_summary` (JSON) |
| `cr_sandbox_runs` | `id`, `task_id` (FK), `script`, `category`, `status`, `exit_code`, `duration_ms`, `timed_out`, `stdout_summary` (脱敏), `stderr_summary`, `error_type` |
| `cr_findings` | `id`, `task_id` (FK), `severity`, `category`, `file`, `line`, `title`, `evidence` (脱敏), `recommendation`, `confidence`, `source`, `status`, `dedup_key` |
| `cr_filter_events` | `id`, `task_id` (FK), `target`, `decision`, `rule`, `reason` |
| `cr_metrics` | `id`, `task_id` (FK), `total_duration_ms`, `sandbox_duration_ms`, `tool_calls`, `intercepts`, `findings_total`, `severity_distribution` (JSON), `error_distribution` (JSON) |
| `cr_reports` | `id`, `task_id` (FK), `report_json` (JSON), `report_md` |

`ReviewStore.get_task_bundle(task_id)` 按任务 ID 返回全部六表关联数据。

## 8. 监控字段

`cr_metrics` 记录：

- `total_duration_ms`：整个评审的时钟耗时
- `sandbox_duration_ms`：所有脚本的累计沙箱执行时间
- `tool_calls`：沙箱运行总次数（含 LLM 调用）
- `intercepts`：非 `allow` 决策的拦截计数
- `findings_total`：已报告（已去重、高置信度）发现项总数
- `severity_distribution`：JSON，如 `{"critical": 2, "high": 5}`
- `error_distribution`：JSON，如 `{"timeout": 1, "ValueError": 2}`

SDK 的 OTel 链路追踪（`invocation`, `agent_run`, `execute_tool`）保持活跃；自定义指标表提供不依赖 OTel 后端的可查询聚合。

## 9. 安全边界（脱敏）

共享的 `secret_patterns.py` 模块（纯标准库）作为规则唯一来源，`check_secrets.py`（沙箱侧）和 `review/redaction.py`（宿主侧）均导入使用。

检测范围：OpenAI/Anthropic/AWS/GitHub/Slack API 密钥、Bearer 令牌、JWT、PEM 私钥、URL 基本认证、敏感变量赋值（`password`, `secret`, `token`, `api_key` 等）。

替换格式：`***REDACTED-<sha256:8>***`——按密钥值稳定计算指纹，可追溯但不可还原。

全链路脱敏：
1. checker 脚本证据（沙箱内预脱敏）
2. 沙箱 stdout/stderr 摘要（`ReviewStore.add_sandbox_run` 内）
3. 发现项证据（`ReviewStore.add_findings` 内）
4. Filter 事件目标（`ReviewStore.add_filter_event` 内）
5. 报告 JSON 和 Markdown（`write_reports` 和 `ReviewStore.add_report` 内）
6. 返回调用方的报告字典（`run_review` 内）

目标：≥95% 检出率；报告文件与数据库行中绝无明文密钥泄露。

## 10. Fake Model / Dry-Run

`FakeReviewModel(LLMModel)` 返回确定性响应：`{"summary": "Dry-run review complete. Static findings are authoritative.", "findings": []}`。`supported_models()` 返回 `[r"fake-review-.*"]`。

Dry-run 模式下完整流水线（解析、治理、沙箱、去重、持久化、报告）正常执行，仅模型层替换。目标：≤2 分钟（实测本地约 5 秒）。`TRPC_AGENT_API_KEY` 未设置时自动启用 dry-run。

## 11. 报告内容

`review_report.json` 和 `review_report.md` 均包含：

1. 发现项摘要 + 严重级别分布
2. `needs_human_review` 待人工复核项
3. Filter 拦截摘要
4. 监控指标
5. 沙箱执行摘要
6. 可执行修复建议
7. 最终结论：`pass` / `needs_attention` / `blocked`
