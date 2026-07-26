# Skills Code Review Agent 终版设计

## 1. 目标与范围

在 `examples/skills_code_review_agent/` 内新增可独立运行的代码审查 Agent，不修改生产源码或公开 API。主链复用仓库已有 `LlmAgent`、`SkillToolSet`、Container workspace runtime、Filter 和 `SqlStorage`。首版只实现 Issue #92 要求：输入解析、Skill 驱动审查、执行前过滤、隔离运行、结构化 Finding、SQL 持久化、JSON/Markdown 报告、fake model 和验收测试；不扩展为通用 CI 平台。

## 2. 交付设计说明（300–500 字）

本方案以确定性规则 Skill 作为基线，Agent 通过 `skill_load` 和受控 `review_skill_run` 发起真实工具调用；fake model 按固定轨迹运行同一链路。输入统一解析 unified diff、file list、git workspace 和 fixture，保留文件、hunk 与候选行号，拒绝越界及超限内容，并跳过二进制。执行前将 argv、工作目录、环境白名单、网络策略、输入输出路径和预算固化为不可变计划；Filter 审批计划摘要并先落库，`DENY` 或 `NEEDS_HUMAN_REVIEW` 绝不调用 sandbox。Container 默认禁网，local 仅显式开发启用。Finding 严格使用九字段，按任务、文件、行号和类别去重，低置信结果转 warning 和人工复核。原始 diff 不入库，异常、沙箱输出和报告统一脱敏。SQLite 为默认后端，窄存储接口支持替换 SQL URL。JSON 与 Markdown 报告记录拦截、执行、异常、耗时和发现分布，确保任务可查询、定位和复验。

## 3. 核心合同

### 3.1 CLI

使用子命令避免运行和查询参数混用：

```text
run_agent.py run
  (--diff-file PATH | --file-list PATH | --repo-path PATH | --fixture NAME)
  [--staged | --worktree | --base REF --head REF]
  [--runtime container | local]
  [--fake-model] [--dry-run] [--db-url URL] [--output-dir PATH]

run_agent.py show --task-id ID [--db-url URL]
```

- `run` 的四种输入互斥；`--repo-path` 默认读取 worktree 与 staged diff，不包含 untracked 文件，显式参数可改变范围。
- rename 保留新旧路径；binary 形成跳过 warning，submodule 与超限文件形成受控 failure，不读取文件正文。
- `--dry-run` 执行解析、计划生成、Filter 和审计落库，但不调用 sandbox。
- `--fake-model` 仍执行 `skill_load`、`review_skill_run` 和报告主链，只把模型决策替换为固定工具轨迹。
- 默认 `container`。`local` 必须同时设置 `TRPC_CODE_REVIEW_ALLOW_UNSAFE_LOCAL=1`，只供开发与测试，README 明示禁止生产使用。

### 3.2 Finding

业务 Finding 固定为 Issue 指定的 9 个字段：

```text
severity: critical | high | medium | low | warning
category: security | async_error | resource_leak | missing_test |
          secret_leak | db_lifecycle
file: POSIX 风格仓库相对路径
line: 1-based 新文件行号；仅删除行使用负的旧文件行号；无法定位为 null
title: 简短标题
evidence: 已脱敏证据
recommendation: 可执行修复建议
confidence: [0.0, 1.0]
source: skill 规则、工具或模型来源
```

`task_id`、`finding_id`、`fingerprint` 和状态属于存储元数据，不改变 9 字段合同。按 `(task_id, file, line, category)` 唯一；冲突时保留更高 severity/confidence，合并去重后的 evidence、recommendation 和 source。低于命名阈值 `MIN_ACTIONABLE_CONFIDENCE` 的结果降为 `warning`，并标记 `needs_human_review`。

### 3.3 不可变执行计划与 Filter

解析后生成冻结的 `ExecutionPlan`：固定 argv、cwd、规范化输入/输出路径、环境白名单、runtime、网络策略、超时及资源预算。计划使用 SHA-256 摘要；Filter 审批与 sandbox 接收同一对象，审批后禁止拼接 shell 字符串。首版只允许调用内置 `scan_rules.py` 的固定 argv，不接受用户命令。

Filter 顺序：

1. 路径：拒绝绝对路径、`..`、NUL、symlink/junction 外逸、`.git` 和敏感系统路径。
2. 命令：拒绝 shell 元字符、非白名单解释器、脚本或参数。
3. 环境：只保留命名白名单；值先脱敏，危险变量直接拒绝。
4. 网络：Container 必须 `network_mode=none`；无法证明禁网则 fail closed。
5. 预算：校验单步与全局剩余时间、输入和输出限额。

每个决定先提交 `filter_decisions`，再执行 handler。`DENY` 和 `NEEDS_HUMAN_REVIEW` 的 handler 调用次数必须为 0。Filter 自身异常按拒绝处理并记录原因。

### 3.4 Sandbox、错误与超时

- 复用 Container workspace runtime；只暂存固定 Skill 资产和规范化输入，输出写入 task 独立目录，结束后清理。
- 使用命名常量限制 git、解析、sandbox、模型、清理和总耗时。
- 复用 workspace runtime 的进程执行和输出截断能力；额外限制 JSONL 总字节、行数、单行长度、嵌套深度和字符串长度。
- timeout、非零退出、非法 JSONL、输出超限、清理失败均生成 `sandbox_runs`/failure 记录；不以空结果冒充成功。
- fake runtime 只用于单元测试；Container 集成测试由环境变量开启，无 Docker 时明确 skip。

### 3.5 Agent、Skill 与规则

真实模式由 `LlmAgent` 挂载仅暴露 `skill_load` 的 `SkillToolSet`，再通过 `review_skill_run` 执行唯一允许的扫描计划。该受控工具复用 Skill 脚本，但不开放内置 `skill_run` 的通用命令面。模型超时或未调用工具时，pipeline 记录失败；仅在模型未调用执行工具时，通过同一受控工具完成确定性基线并标记 `PARTIAL`。fake model 生成相同工具调用序列，不伪造 sandbox 结果。

Skill 规则覆盖：

- 注入、危险反序列化、命令执行等安全问题；
- 未 await、在 async 路径调用阻塞 API 等异步错误；
- 文件、进程、锁等资源泄漏；
- 生产行为变更但缺少对应测试；
- API key、token、私钥、URL credential 等 secret 泄漏；
- DB 事务未回滚、连接/游标生命周期错误。

规则只检查 diff 中的变更行及有限上下文，证据必须可定位；启发式结果默认低置信，避免扩大误报。

### 3.6 存储、状态与恢复

`ReviewStore` 保持窄接口并复用 `SqlStorage`、SQLAlchemy metadata 和事务管理。默认同步 SQLite URL；其他同步或受支持的 async SQL URL 通过同一接口接入，驱动缺失或方言不受支持时启动即失败，不降级。

最小表：

- `review_tasks`：输入摘要、状态、开始/结束时间、最终结论；
- `filter_decisions`：计划摘要、动作、规则、脱敏原因；
- `sandbox_runs`：状态、退出码、timeout、耗时、截断输出、异常类型；
- `findings`：9 字段、fingerprint、人工复核状态；
- `review_reports`：JSON、Markdown、聚合指标和生成时间。

状态只允许：

```text
CREATED -> FILTERED -> RUNNING -> COMPLETE | PARTIAL | FAILED
                    \-> DENIED
```

Filter 决定单独事务先提交；运行结果、Finding 和报告按阶段提交。每个 task 使用独立 async session。SQLite 设置 WAL 与 busy timeout；唯一冲突重读并合并，异常回滚后写入失败状态。

### 3.7 脱敏、报告与监控

统一 `SecretRedactor` 覆盖 DB 文本、JSON/Markdown、stdout/stderr、异常、Filter 决定和 artifact。原始 diff 只写入 task 临时 workspace，不持久化。报告原子写入同目录临时文件，再用 `os.replace` 替换。

报告固定章节：任务摘要、Finding、拦截记录、sandbox 执行、异常、指标、最终结论。指标包含总耗时、sandbox 耗时、tool 调用数、Filter 动作数、Finding 总数、severity/category 分布和异常类型分布。

## 4. 分阶段实现与审查节点

### 阶段 A：合同与骨架

实现数据模型、常量、输入解析、SQL schema、Skill 文档与基础 fixture。

审查节点 A：subagent 检查输入语义、Finding 9 字段、路径边界、schema 和原始数据保留策略。输出 checklist；存在 Blocking 不进入阶段 B。

### 阶段 B：安全执行链

实现不可变执行计划、Filter、脱敏器、Container/local adapter、timeout/output cap 和 failure record。

审查节点 B：独立 subagent 验证“审计提交早于执行”、计划摘要无 TOCTOU、拒绝项 handler 调用为 0、禁网、env 白名单、超时与清理。Blocking 清零后进入阶段 C。

### 阶段 C：Agent、持久化与报告

实现真实/fake model 工具链、规则扫描、去重、置信路由、状态机、查询和 JSON/Markdown 输出。

审查节点 C：独立 subagent 检查 Agent 确实经 Skill 调用 sandbox、事务恢复、跨 task 隔离、全出口脱敏及 fake/real 合同。Blocking 清零后进入阶段 D。

### 阶段 D：验收与收敛

补齐 8 组带 ground truth 的 fixture、示例报告、README、覆盖率、并发交错和可选 Container 集成测试。

审查节点 D：独立 subagent 对照 Issue #92、代码规模门禁和全部验收公式终审；修复后再复审，Blocking/Warning 均给出处理结论。

## 5. 测试与验收

- 8 类 fixture 每类包含 `*.diff` 与 `*.expected.json` ground truth，按 `(category, file, line±1)` 匹配；同时统计 micro recall 与 false-positive rate。
- 公开 fixture 和隐藏变体的高风险检出率均要求 `>=80%`，正常样例误报率 `<=15%`。
- secret 标注语料覆盖普通 token、URL credential、私钥、Unicode、编码及跨行变体；召回率 `>=95%`，并检查存储与报告等持久化出口无测试明文。
- spy runtime 断言所有 `DENY/NEEDS_HUMAN_REVIEW` 的执行次数为 0；timeout 与 output cap 分别有确定性测试。
- 用 thread barrier 和 `asyncio.to_thread` 触发相同 Finding 的并发写入，验证 SQLite 唯一冲突后的重读合并，替代 Python 不存在的 Go `-race`。
- fake model 端到端目标 `<30s`，硬性 `<120s`；Container 集成模式通过环境变量开启或 skip。
- 覆盖率硬门槛 `>=85%`，目标 `>=90%`。执行项目采用的 YAPF、Flake8、目标 pytest+coverage 和全量相关测试。
- 使用 Ruff/McCabe 与自定义 AST 检查门禁：函数体 `<=80` 行、`<=60` 语句、圈复杂度 `<=15`、参数 `<=4`、单文件 `<=1000` 行；所有阈值使用命名常量。

## 6. 预计文件

均为新增文件，预计不修改生产源码。

```text
examples/skills_code_review_agent/
├── README.md
├── DESIGN.md
├── run_agent.py
├── agent/
│   ├── __init__.py
│   ├── constants.py
│   ├── models.py
│   ├── input_parser.py
│   ├── policy.py
│   ├── sandbox.py
│   ├── storage.py
│   ├── pipeline.py
│   └── reporting.py
├── scripts/
│   └── init_db.py
├── skills/code-review/
│   ├── SKILL.md
│   ├── references/rules.md
│   └── scripts/scan_rules.py
├── fixtures/
│   ├── clean.diff
│   ├── clean.expected.json
│   ├── security.diff
│   ├── security.expected.json
│   ├── async_resource.diff
│   ├── async_resource.expected.json
│   ├── db_lifecycle.diff
│   ├── db_lifecycle.expected.json
│   ├── missing_tests.diff
│   ├── missing_tests.expected.json
│   ├── duplicate.diff
│   ├── duplicate.expected.json
│   ├── sandbox_failure.diff
│   ├── sandbox_failure.expected.json
│   ├── secrets.diff
│   └── secrets.expected.json
└── reports/
    ├── review_report.json
    └── review_report.md

tests/examples/skills_code_review_agent/
├── test_input_parser.py
├── test_policy_sandbox.py
├── test_storage.py
├── test_reporting.py
└── test_acceptance.py
```

预计新增 40 个文件：Python 实现约 1,500–1,900 行，测试约 1,000–1,300 行，文档、fixture 与示例报告约 900–1,200 行。任何单文件不超过 1,000 行。若实现中出现已有公共能力可直接复用，优先删减本地封装。

## 7. 初版审查吸收结果

已吸收 subagent 的关键意见：补全 file-list 与 git 范围、拆分 `run/show`、锁定 Finding 字段、引入不可变执行计划和先审计后执行、明确 SQL 事务边界、量化脱敏与检测公式、机器化代码规模门禁，并把 B/C/D 设为阻断式独立审查。

未采用三项与任务范围冲突的建议：Finding 字段不改为 reviewer 自定义字段；保留 Issue 允许的显式 local 开发 fallback；保留真实 Agent/LLM 主链与 DB 初始化脚本。首版暂不接入 Cube/E2B，Container 已满足“至少一种隔离 runtime”要求。
