# 自动代码评审 Agent（Skills + 沙箱 + 数据库）

> English version: [README.en.md](README.en.md) · 方案设计说明: [DESIGN.zh_CN.md](DESIGN.zh_CN.md)

本示例基于 tRPC-Agent SDK 构建一个可验证的自动代码评审（CR）Agent 原型：
读取 git diff / PR patch / 本地变更，通过 **code-review Skill** 加载规则与脚本，
经 **Filter 策略**放行后在**沙箱**中执行静态检查，把结构化 findings、拦截记录、
沙箱日志和监控指标全部落入 **SQL 数据库**，最终输出 `review_report.json` 与
`review_report.md`。

## 关键特性

- **CR Skill**（`skills/code-review/`）：SKILL.md + 6 类规则文档 + 沙箱脚本。
  规则覆盖安全风险、异步错误、资源泄漏、测试缺失、敏感信息泄漏、数据库事务/连接
  生命周期（超出题目要求的 4 类）。
- **单一实现，双端复用**：diff 解析器、规则引擎、密钥正则表全部放在
  `skills/code-review/scripts/lib/`（纯标准库）。沙箱内直接执行，宿主端通过
  importlib 加载同一份代码 —— 检测与脱敏永不漂移。
- **沙箱执行**：`--sandbox` 默认 **auto**——检测到 Docker 即用 **container**
  （原生隔离，生产方案），否则记录告警并回退 **local**；另支持 **cube**
  （Cube/E2B 云沙箱）。**local** 仅作为开发 fallback（测试与 `--dry-run` 显式
  使用），并通过 `EnvWhitelistLocalProgramRunner` 强制环境变量白名单，
  宿主密钥不进沙箱。
- **安全边界**：每次运行有超时、stdout/stderr 大小上限、环境白名单；证据文本在
  沙箱内即完成脱敏；沙箱超时/失败/异常一律记录为数据（`cr_sandbox_run` 行），
  自动回退到宿主内规则引擎，**评审任务永不崩溃**。
- **Filter 治理**：`SandboxGovernanceFilter`（真实 `BaseFilter` + `run_filters`
  链）对高风险脚本、非白名单命令、禁止路径、网络访问、超预算执行做前置拦截；
  `deny` / `needs_human_review` 时终端 handler 不会被调用，拦截原因写入报告与
  `cr_filter_event` 表。
- **去重与降噪**：同一 `(file, line, category)` 只报一次（保最高严重级别，合并
  规则 ID）；置信度 < 0.7 的启发式结果进入 `needs_human_review`，绝不混入高置信
  findings。
- **数据库存储**：5 张表（task / sandbox_run / filter_event / finding / report），
  接口为 `ReviewStore` ABC —— 换 MySQL/PostgreSQL 只需换 SQLAlchemy URL。
- **监控审计**：总耗时、沙箱耗时、工具调用次数、拦截次数与分布、finding 数量、
  severity 分布、异常类型分布，全部入库可查，另附 OpenTelemetry tracer span。
- **离线可测**：`--dry-run`（fake model + local 沙箱）不需要任何 API Key，
  完整链路秒级完成（验收要求 ≤ 2 分钟）。

## 架构与数据流

```
--diff-file | --repo-path | --files | --fixture
      │ inputs.py → RawChangeSet(unified diff + 可选全文件内容)
      ▼
ReviewPipeline.run()                          [span code_review.total]
  1 建任务 cr_review_task(status=running)
  2 宿主解析 diff → 摘要(无内容, 安全入库)     [span code_review.parse]
  3 Filter 治理门 (run_filters)
      allow → 4        deny/needs_human_review → 记录拦截, 走宿主回退
  4 沙箱执行 run_checks.py                    [span code_review.sandbox]
      stage skill → 注入 diff.json → 白名单环境运行(超时/输出上限)
      失败/超时 → cr_sandbox_run 记录 + 宿主回退, 任务继续
  5 后处理: 去重 → 降噪分桶 → 二次脱敏        [span code_review.postprocess]
  6 LLM 摘要 (fake|real|off)                  [span code_review.llm]
  7 落库 findings/filter_events/report + 渲染 json/md 报告
```

## 关键文件

| 路径 | 说明 |
|---|---|
| `run_agent.py` | CLI 入口（review / show / list / init-db） |
| `skills/code-review/SKILL.md` | Skill 使用说明 |
| `skills/code-review/rules/*.md` | 6 类规则文档 |
| `skills/code-review/scripts/` | 沙箱入口脚本 + 纯标准库规则引擎 `lib/` |
| `codereview/pipeline.py` | 评审编排（任务生命周期、回退、落库） |
| `codereview/governance.py` | Filter 策略（BaseFilter + run_filters） |
| `codereview/sandbox.py` | 沙箱运行时工厂 + 环境白名单 + SandboxExecutor |
| `codereview/findings.py` | Finding 模型、去重、降噪 |
| `codereview/redaction.py` | 宿主端脱敏（复用 Skill 的正则表） |
| `codereview/store/` | ReviewStore ABC + SqlReviewStore + schema + init 脚本 |
| `fixtures/*.diff` | 8 条可运行测试样例 |
| `sample_output/` | 提交的示例报告（json + md） |
| `tests/` | 71 个 pytest 用例（全部离线） |

## 运行方式

```bash
cd examples/skills_code_review_agent

# 1. 离线体检（无需任何 API Key）
python run_agent.py review --fixture security_issue --dry-run

# 2. 评审一个 diff / 一个 git 工作区 / 一组文件
python run_agent.py review --diff-file my.patch
python run_agent.py review --repo-path /path/to/repo
python run_agent.py review --files a.py b.py

# 3. 查询数据库（按 task id 取回全部记录）
python run_agent.py show --task-id <ID>
python run_agent.py list
python run_agent.py init-db          # 初始化/迁移 schema（幂等）

# 4. 生产形态：容器沙箱 + 真实模型
python run_agent.py review --diff-file my.patch --sandbox container --model-mode real
```

- `--sandbox` 默认 **auto**：检测到 Docker 时使用 container
  （`python:3.12-slim` 镜像），否则告警并回退 local；`--sandbox cube` 使用
  Cube/E2B（需要 E2B Key）。**local 只是开发 fallback，不是生产方案**——
  生产请使用 container。
- `--model-mode real` 需要环境变量 `TRPC_AGENT_API_KEY` / `TRPC_AGENT_BASE_URL`
  / `TRPC_AGENT_MODEL_NAME`（键名参考 `.env.example`；本示例**不会**自动加载
  `.env`，请先 `set -a; source .env; set +a` 或逐个 `export`）。
- `--inject-sandbox-failure` 演示沙箱失败被安全吸收（任务状态
  `completed_with_errors`，报告照常生成）。

## 运行测试

```bash
python -m pytest examples/skills_code_review_agent/tests -q      # 71 passed
```

## 数据库 Schema（SQLite 默认，URL 可换 MySQL/PostgreSQL）

| 表 | 内容 | 关键字段 |
|---|---|---|
| `cr_review_task` | 任务与状态机 | id, status, input_type/ref, diff_summary(JSON), config(JSON), error_* |
| `cr_sandbox_run` | 每次沙箱尝试（含被拦截/失败） | task_id, status(ok/failed/timeout/blocked/error), exit_code, timed_out, filter_action, stdout/stderr 摘录(已脱敏+截断), error_type |
| `cr_filter_event` | 每个治理决策 | task_id, stage, target, action, rule, reasons(JSON) |
| `cr_finding` | 结构化 finding | task_id, severity, category, file, line, title, evidence(已脱敏), recommendation, confidence, source, rule_id, bucket, dedup_key |
| `cr_report` | 最终报告 + 监控摘要 | task_id(unique), summary, severity_stats, filter_summary, sandbox_summary, metrics, report(完整 JSON) |

初始化/迁移：`python run_agent.py init-db`（`SqlStorage.create_sql_engine` 执行
`create_all` + 前向列迁移，幂等可重复执行）。

## 验收标准对照

| # | 验收标准 | 实现/验证 |
|---|---|---|
| 1 | 8 条 diff 样本全部可运行并生成报告 | `fixtures/` 8 条；`tests/test_fixtures_e2e.py` 参数化全跑 |
| 2 | 高危检出 ≥80%，误报 ≤15% | `tests/test_rules.py`：19 条带标注正样本全检出（公开集召回 100%）；10 条干净样本高置信 FP 为 0。隐藏集指标以此带标注语料为代理 |
| 3 | DB 完整记录并按 task id 查询 | 5 张表 + `get_task_bundle(task_id)`；`tests/test_store.py` |
| 4 | 沙箱超时/输出上限；失败不崩溃 | `tests/test_sandbox_safety.py`（超时、截断、强制失败、异常包裹） |
| 5 | 脱敏检出 ≥95%，报告与 DB 无明文 | `tests/test_redaction.py`（48 条样本 ≥95%）；e2e 断言报告与 sqlite 文件字节中无种子明文 |
| 6 | dry-run ≤2 分钟 | `test_dry_run_speed_and_no_api_key`（实测 < 5 秒，且删除全部 Key 环境变量） |
| 7 | 高风险脚本先经 Filter，deny/needs_human_review 不进沙箱 | `tests/test_governance_filter.py`（handler 哨兵证明未执行） |
| 8 | 报告含 7 个规定部分 | `tests/test_cli_and_report.py::test_report_sections_complete` |
