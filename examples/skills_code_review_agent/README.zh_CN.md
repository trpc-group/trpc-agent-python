# Skills 代码评审 Agent

[English](README.md) | [设计说明](DESIGN.md)

本示例实现 [Issue #92](https://github.com/trpc-group/trpc-agent-python/issues/92)：把可复用 Skill、隔离 workspace、确定性规则、Filter 治理、SQLite 持久化、OpenTelemetry、脱敏与可审计报告组合成自动代码评审 Agent。验收主链路不依赖真实模型 API Key。

## 实测验收结果

在仓库根目录运行以下命令，可复现当前标注集结果：

```bash
python examples/skills_code_review_agent/evaluate.py \
  --markdown --fail-under \
  --out tmp/code-review-eval.json
```

| 指标 | Issue #92 门槛 | 当前实测 |
| --- | ---: | ---: |
| 高危问题检出率 | >= 80% | **100.0%（15/15）** |
| 误报率 | <= 15% | **0.0%（0/18）** |
| 敏感信息脱敏召回率 | >= 95% | **100.0%（30/30）** |
| 错误脱敏率 | 信息项 | **0.0%（0/12）** |

holdout 共 18 条 diff：12 条正例、6 条负对照。检出率和误报率只统计高置信 `findings`；评测器同时公开“包含 warnings/人工复核”的召回率，避免把结果下沉到人工复核来刷分。匹配条件为文件、类别相同且行号误差不超过 2。脱敏语料包含 30 条密钥正例和 12 条正常文本。

这些数字只代表仓库内确定性标注集，不代表任意代码库。CI 应始终带 `--fail-under`，防止规则改动悄悄跌破验收线。

## 架构

```text
unified diff / PR patch / Git 工作区 / fixture
                         |
                         v
                   输入解析与摘要哈希
                    /            \
          仅内存中的原文          只向沙箱传脱敏 diff
              |                         |
              v                         v
         确定性规则扫描           Filter 执行前决策
              |                         |
        AST / hunk 上下文          SDK workspace runtime
          可审计抑制          container / cube / local fallback
              |                         |
              +--------- 沙箱脚本 findings
                         |
                  去重、分桶、再次脱敏
                         |
                  JSON / Markdown / SQLite

             OpenTelemetry span 覆盖全链路
```

沙箱永远拿不到含明文密钥的输入。进程内规则只在内存中检查原文，构造 finding 时立刻脱敏，报告和数据库写入前再对整个对象树做一次脱敏。

## 目录

```text
examples/skills_code_review_agent/
├── README.md / README.zh_CN.md
├── DESIGN.md
├── .env.example
├── Dockerfile
├── run_agent.py
├── evaluate.py
├── schema.sql
├── agent/                       # 编排、策略、runtime、存储
├── evalset/
│   ├── holdout/                 # 18 条标注评测 diff
│   ├── labels.json
│   └── secrets_corpus.json
├── fixtures/                    # 8 条公开验收 fixture
├── scripts/                     # 数据库初始化与查询脚本
└── skills/code-review/
    ├── SKILL.md
    ├── rules/                    # 6 类规则文档
    └── scripts/
        ├── parse_diff.py
        └── static_rules.py
```

## 快速开始

在仓库根目录安装开发依赖：

```bash
pip install -e ".[dev]"
```

无需模型凭据或 Docker，运行一条公开样本：

```bash
python examples/skills_code_review_agent/run_agent.py \
  --fixture security_issue \
  --dry-run \
  --output-dir tmp/code-review-security \
  --db-path tmp/code-review-security/review.sqlite3
```

命令会输出 `review_report.json`、`review_report.md`，把 task、沙箱执行、Filter 拦截、findings、指标和最终报告写入 SQLite，并在标准输出中返回 `task_id`。

按 task id 查询完整审计包：

```bash
python examples/skills_code_review_agent/run_agent.py \
  --db-path tmp/code-review-security/review.sqlite3 \
  --query-task-id <task_id>
```

输入方式互斥：

- `--diff-file`：unified diff 或 PR patch；
- `--repo-path`：本地 Git 工作区的暂存/未暂存改动；
- `--path-list-file` 配合 `--repo-path`：只评审清单内路径；
- `--fixture`：`fixtures/` 下不带 `.diff` 的样本名。

## Runtime 模式

| 模式 | 用途 | 隔离与依赖 |
| --- | --- | --- |
| `container` | 默认生产路径 | tRPC-Agent `BaseWorkspaceRuntime`、Docker、禁网、Skill 只读挂载 |
| `cube` | 远程生产沙箱 | 创建时关闭网络的 Cube/E2B sandbox；客户端无法强制禁网时 fail-closed |
| `local` | 显式开发兜底 | 在宿主机执行，绝不作为默认生产路径 |
| `dry-run-local` | 确定性 CI | `--dry-run` / `--fake-model` 选择，无需模型 Key |

各 SDK runtime 共享 `skills/`、`work/inputs/`、`runs/`、`out/` 目录契约。每条命令都有超时、输出字节预算和环境变量白名单；有界采集进程在 stdout 或 stderr 达到预算时立即终止子进程，SDK 只接收经过大小校验和脱敏的 envelope。启动、超时、执行、输出超限、产物收集失败会落为 `sandbox_run` 与人工复核项，不会让整次评审崩溃。Container 与 Cube 必须在创建 workspace 前证明由后端强制禁网。

构建固定工具链镜像：

```bash
docker build \
  -t trpc-agent-code-review:local \
  examples/skills_code_review_agent
export CODE_REVIEW_IMAGE=trpc-agent-code-review:local
```

PowerShell 使用 `$env:CODE_REVIEW_IMAGE = "trpc-agent-code-review:local"`。Dockerfile 不复制源码树、`.env`、凭据或构建上下文内容。仅在明确接受宿主机风险的开发环境使用 `--allow-local-fallback`，实际 runtime 会记录到报告和数据库。

## Filter 治理

确定性 CLI 在每个沙箱请求前调用 `ReviewExecutionFilter`；可选 `LlmAgent` 路径则把 `CodeReviewSandboxPolicyFilter` 注册到 `SkillRunTool`，模型驱动的调用会在 handler 执行前被截断。同一个 Filter 也挂到更底层的 `workspace_exec` 及相关 runtime 工具，模型不能绕过 `skill_run` 改走直接 workspace 执行。

策略同时扫描真实 argv 的每个元素、拼接后的 argv 与调用方提供的展示文本；递归检查声明式输入/输出，拒绝 `host://` 和宿主机绝对路径，并约束网络、超时和输出预算。因此良性展示文本或嵌套输入对象都无法隐藏恶意请求。

确定性评审流水线会把每个 `deny` / `needs_human_review` 决策写入报告与 SQLite task bundle。可选 `LlmAgent` helper 本身不拥有报告或 task 生命周期；嵌入服务如需同样持久化，应向 `create_agent(...)` 传入 task 级 `intercept_sink`（例如调用 `ReviewStore.add_filter_intercept`）。未配置 sink 时 Filter 仍返回结构化拒绝。每个 tool set 独占 Filter 实例，并发评审不会共用进程级事件 sink。

每次调用 `create_agent(...)` 都会独占 Container/Cube runtime。请将返回的 `CodeReviewAgent` 作为异步上下文管理器使用，或在 `finally` 中调用 `await agent.close()`；该操作幂等，并会立即停止底层容器或远程沙箱。使用模块级延迟初始化 `root_agent` 的服务可在退出时调用 `await close_root_agent()`。

在已经运行的事件循环中，请用 `await create_agent_async("cube")` 创建 Cube Agent。同步延迟初始化的 `root_agent` 只有在事件循环启动前完成初始化时才能选择 Cube；若在循环内访问会 fail-closed 并提示异步入口，不会留下半初始化的远程沙箱。

如 CLI 提供演示拦截，必须显式使用 `--demo-filter-intercept`；正常评审不得注入假拦截污染指标。

## Telemetry

各阶段通过 `trpc_agent_sdk.telemetry.tracer` 发出 span。未配置 provider 时安全空转；配置 OpenTelemetry 后可导出根 span `code_review.review` 及输入加载、diff 解析、沙箱、规则、上下文抑制、持久化、报告等子 span。属性包括 task/输入标识、runtime、状态、耗时、超时、finding 数和异常类型，绝不附带密钥证据。

本地查看 console span：

```bash
python examples/skills_code_review_agent/run_agent.py \
  --fixture security_issue --dry-run --telemetry-console \
  --output-dir tmp/code-review-telemetry \
  --db-path tmp/code-review-telemetry/review.sqlite3
```

## 存储与回放

默认 SQLite 后端位于可替换的 review-store 接口后。`schema.sql` 是唯一 DDL 来源，包含 `schema_version`、`review_task`、`sandbox_run`、`finding`、`filter_intercept`、`review_metric`、`review_report`。

初始化并按 task id 查询：

```bash
python examples/skills_code_review_agent/scripts/init_db.py \
  --db-path tmp/code-review.sqlite3

python examples/skills_code_review_agent/scripts/query_review.py \
  --db-path tmp/code-review.sqlite3 \
  query <task_id> \
  --format table
```

机器读取可用 `--format json`。稳定 task id 不得静默删除历史审计行；只有明确的 overwrite / 新 attempt 机制才能替换。

## 评测与测试

运行原验收、阈值与 Filter 测试：

```bash
python -m pytest \
  tests/examples/test_skills_code_review_agent.py \
  tests/examples/test_skills_code_review_agent_eval.py \
  tests/examples/test_skills_code_review_agent_filter.py \
  -q -s
```

`-s` 可绕过 Windows 上 pytest stdin capture 导致的子进程句柄问题；其他平台可省略。

单独评分 8 条公开 fixture：

```bash
python examples/skills_code_review_agent/evaluate.py \
  --labels examples/skills_code_review_agent/fixtures/labels.json \
  --diffs examples/skills_code_review_agent/fixtures \
  --skip-redaction --markdown --fail-under
```

公开样本覆盖：无问题、安全问题、异步资源泄漏、数据库生命周期、测试缺失、去重、沙箱失败、敏感信息脱敏。

## 安全说明

- 不要把真实凭据写进 `.env.example`、fixture、Docker build arg 或文档；运行时从密钥管理器或进程环境注入。
- 容器默认 `network_mode=none`，放开网络必须有显式策略决策。
- 只有白名单环境变量可进入沙箱。
- stdout、stderr 与产物都有字节上限，截断行为会记录。
- diff 摘要、脚本输出、产物、findings、报告、Telemetry 安全属性与数据库值均经过脱敏。
- local 模式没有隔离能力，只能用于开发兜底。

## 规则文档

规则索引见 [`skills/code-review/rules/README.md`](skills/code-review/rules/README.md)。每类文档都列出 rule id、检测模式、严重级别、置信度、修复方式和已知误报；上下文抑制会写入报告，不会静默丢弃。
