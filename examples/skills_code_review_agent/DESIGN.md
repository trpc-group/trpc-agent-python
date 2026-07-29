# 自动代码评审 Agent 方案设计

## 方案设计说明

本 Agent 把 `code-review` Skill 作为规则与脚本的可复用边界：SKILL.md 说明输入、输出和安全边界，manifest 固定允许执行的脚本及其哈希，规则只对 diff 的新增侧或显式 snapshot 生效。Agent 入口由 SDK `LlmAgent + SkillToolSet` 真实发出 `skill_load → skill_run`；`skill_run` 只接收宿主签发的一次性 request id，并在同一 workspace 中复验已加载脚本的摘要。Pipeline 是唯一检测链路，CLI、fixture、评测和 Agent 入口共享同一份解析、治理、沙箱、存储和报告对象，因此不会出现“测试走假实现、生产走另一套逻辑”的分叉。

沙箱默认使用 Container，并要求本次运行可验证 `network_mode=none`；当前示例的 Cube/E2B 仅保留 SDK 适配接口，CLI 尚未注入 `cube_runtime_factory`，因此 `--sandbox cube` 不是可用后端，会以配置错误退出。即使未来接入工厂，Cube 缺少可证明的受控网络时仍必须拒绝；local 只是用户显式调试 fallback，必定留下隔离不可验证告警。Filter 在 stage 前校验 manifest、路径、结构化参数、网络策略和预算，DENY 与 NEEDS_HUMAN_REVIEW 不创建 sandbox。环境只按白名单重建，模型 Key、token 和 password 不会传给脚本。

数据库采用可替换的 SQL 接口，默认 SQLite 的五张 `cr_*` 表分别保存任务、sandbox run、Filter 事件、finding 与最终报告；不持久化原始 diff。finding 以文件、行号、类别去重，并依置信度分为 findings、needs_human_review、suppressed，运行故障只进入 warnings。报告 JSON 是 canonical 来源，Markdown、数据库摘要和 severity 统计均由它派生，确保可回放且排序稳定。

安全链路先在受控内存检测，再对 sandbox 输出、宿主字段和全部出口三次脱敏；发现明文会阻止写入。监控记录总耗时、沙箱与 LLM 耗时、调用次数、拦截次数、严重级别和异常分布。LLM 只可增强建议、摘要和人工复核提示，real 模式必须显式开启并从 `.env` 白名单读取，不能参与确定性检出或改变 finding 身份。

## 规格与交付范围

同目录的 [`DEV_SPEC.md`](DEV_SPEC.md) 是本项目的规范源，完整定义 issue #92 的输入语义、
字段契约、沙箱预算、失败语义、AC1–AC8 和测试方法。本文直接展示设计与验收映射，README
提供运行入口和可执行证据；两者都不取代 `DEV_SPEC.md`。第 7 章未来规划不属于当前交付。

当前实现已完成仓库公开 fixture、代理语料、Container 和真实模型的对应验证。AC2 只能
表述为“公开代理门禁通过；官方隐藏样本待官方验收”，不得把公开数据结果外推为官方隐藏集结论。

## 交付物与架构映射

| 交付物 | 实现位置 | 设计责任 |
|---|---|---|
| Agent 与唯一编排链路 | [`run_agent.py`](run_agent.py)、[`agent/agent.py`](agent/agent.py)、[`agent/tools.py`](agent/tools.py)、[`pipeline.py`](code_review/pipeline.py) | `review` 直连 pipeline；`user-query` 真实调用 `skill_load → skill_run` 后仍委托同一检测链路 |
| code-review Skill | [`SKILL.md`](skills/code-review/SKILL.md)、[`rules/`](skills/code-review/rules/)、[`scripts/`](skills/code-review/scripts/) | 规则、diff 解析和执行 manifest 的可复用边界 |
| Filter 与沙箱 | [`governance.py`](code_review/governance.py)、[`sandbox.py`](code_review/sandbox.py) | 执行前治理、Container/Cube/local runtime、超时和输出限制 |
| 数据库 | [`models.py`](code_review/store/models.py)、[`review_store.py`](code_review/store/review_store.py)、[`init_db.py`](code_review/store/init_db.py) | SQLite 默认五表、可替换 SQL URL、task bundle 回放 |
| 报告与监控 | [`report.py`](code_review/report.py)、[`metrics.py`](code_review/metrics.py)、[`review_report.schema.json`](schemas/review_report.schema.json) | canonical JSON、Markdown 派生、指标快照与 Telemetry 白名单 |
| fixture 与示例输出 | [`tests/fixtures/diffs/`](tests/fixtures/diffs/)、[`test_fixtures_e2e.py`](tests/e2e/test_fixtures_e2e.py)、[`sample_output/`](sample_output/) | 8 simple + 8 complex 的分桶、落库、失败和脱敏证据 |
| 维护者验收入口 | [`OPERATIONS.md`](OPERATIONS.md)、[`.env.example`](.env.example)、[`run_agent.py`](run_agent.py) | 可审计的四输入、direct/Agent 触发、Docker/模型前置、16 fixture、质量门禁与产物定位 |

维护者执行 `review` 时直接进入唯一 pipeline；`user-query` 会增加 SDK
`LlmAgent + SkillToolSet` 的受控工具回合。模型只看到一次性 request id、输入类型及规范化意图，
看不到原始 diff、宿主路径、命令或环境值；固定执行计划仍由 manifest、Filter 和宿主构造。
CLI 默认使用 `--log-level INFO` 在 stderr 输出安全阶段信息与实际 container ID，SDK 原始日志不透传；
`DEBUG` 只增加仓库相对路径、script_id 和已脱敏摘要，任何等级均不输出 diff、evidence、环境变量或凭据。
成功工具回合随后委托同一 pipeline，不能形成第二套规则、沙箱或存储实现。CLI 成功 JSON 在当前
终端返回 `entrypoint`、实际 `skill_tools` 和 `report_files` 的完整位置，便于
人工验收与 CI 收集；这些路径不写入 canonical report、数据库、Telemetry 或日志。实现使用
`pathlib`、SQLite URL 和 SDK runtime，Windows PowerShell 与 Linux/macOS Bash 只在解释器路径和 shell
调用语法上不同；两套完整维护命令由 [`OPERATIONS.md`](OPERATIONS.md) 统一维护。

## 风险表

| 风险 | 控制措施 | 审计证据 |
|---|---|---|
| 任意命令执行 | manifest 加哈希、结构化参数和 Filter 前置拦截 | Filter 事件、零 sandbox run |
| 网络或宿主逃逸 | Container `network_mode=none`，Cube 默认拒绝，local 明示告警 | runtime 类型、网络策略摘要 |
| 密钥泄漏 | 同源 detect/redact、全出口扫描、模型环境白名单 | `plaintext_hits=0`、失败阻断 |
| 资源耗尽 | 次数、超时、单次/总输出和 deadline 预算 | sandbox run、warning、metrics |
| LLM 越权 | 仅合并文本字段，冻结 finding identity 与分桶 | fake/real identity 对照测试 |
| 误报与遗漏 | 确定性规则、公开语料、人工复核桶 | evaluation 指标、needs_human_review |
| 数据回放泄密 | 仅保存摘要和 canonical 脱敏报告，不保存原始 diff | SQLite bundle 查询 |

## AC1–AC8 核对

| 项目 | 设计保证 | 当前结论 | 直接证据 |
|---|---|---|---|
| AC1 | 相同 pipeline 执行公开输入并冻结 canonical report | 已验证 8 simple + 8 complex | `test_fixtures_e2e.py` 校验报告、分桶和 task bundle |
| AC2 | 确定性规则与固定语料保证可复现指标 | **公开代理门禁通过；官方隐藏样本待官方验收** | `evaluate.py` 输出 Recall、finding-level FP、P/R/F1 |
| AC3 | `ReviewStore` 抽象隔离 SQL 后端，五表保存完整业务链路 | 已验证 | `test_store.py` 验证初始化、CRUD、索引和按 task id 聚合 |
| AC4 | timeout、nonzero、truncated 和 blocked 统一转为运行数据 | 已验证 | `test_sandbox_safety.py`、`test_pipeline.py` 验证 warning 与报告续行 |
| AC5 | 检/脱同源加三层出口扫描，发现明文时阻止持久化 | 已验证公开语料与出口 | `test_redaction.py` 和 secret fixtures 验证检出率及 `plaintext_hits=0` |
| AC6 | fake model + 显式 local sandbox 构成固定离线测量路径 | 8 条独立 Agent 审查均验证 ≤120 秒 | `evaluate.py --sandbox local` 与 `test_evaluate.py` |
| AC7 | Filter 在 workspace 创建和脚本执行前短路非 ALLOW 决策 | 已验证零 sandbox 副作用 | `test_governance.py` 验证事件落库和 `sandbox_runs=0` |
| AC8 | schema 固定八段内容，Markdown 和数据库从 canonical JSON 派生 | 已验证 | `test_report.py` 与 fixture E2E 验证结构和统计一致 |

更细的字段、阈值和逐项测试命令以 [`DEV_SPEC.md`](DEV_SPEC.md) 为准；面向验收官的
完整交付物导航和复现命令见 [`README.md`](README.md)。
