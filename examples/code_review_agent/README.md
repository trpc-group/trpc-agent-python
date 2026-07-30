# 自动代码评审 Agent（阶段 1～6）

本示例提供一个可运行的代码评审闭环：从本地 Git 仓库提取
`base...head` 变更，构造有大小边界的 Diff 上下文，运行
Ruff/Bandit/Pytest 静态分析并加载项目 Skills，再由单个 `LlmAgent`
返回结构化 Finding，最后执行确定性的路径/行号校验、去重、排序并生成
JSON 和 Markdown 报告，并将完整运行记录持久化到 SQL 数据库。

当前版本同时支持本地 Git Diff 和 GitHub App `pull_request` Webhook。

## 已实现能力

- 安全调用 Git：使用参数数组执行，不把 revision 或路径拼入 shell。
- PR 风格比较：默认使用 `merge-base(base, head)` 到 `head` 的变更。
- 解析新增、修改、删除、重命名文件及 unified diff hunk 行号。
- 跳过二进制、依赖目录、构建产物、lock 和压缩文件。
- 设置文件数量、单文件 Patch 和总上下文字符预算。
- 使用 Pydantic Schema 约束模型的 `ReviewOutput` 和 `Finding`。
- 将 Ruff/Bandit JSON 转换成与 LLM 相同的 Finding Schema。
- 可选执行 Pytest，并在报告中保留命令、状态、耗时和截断日志。
- 支持本地分析以及禁网、只读、资源受限的 Docker 静态分析。
- 提供 correctness/security/maintainability/test-coverage 四个评审 Skills。
- LLM 只能加载 Skill 知识，不能通过 Skill 获得 shell/exec 工具。
- 丢弃不属于本次变更文件的 Finding。
- 只有命中新增行的 Finding 才标记为 `publishable`。
- 按规则、文件和行号去重，并保留优先级更高的结果。
- 支持完全不调用模型的 `--no-llm` 模式。
- 默认使用 SQLite 保存运行、变更文件、Finding 和分析器执行记录。
- 基于实际 commit、完整配置和模型生成幂等键，重复运行不会重复入库。
- 支持按 ID 查看，以及按仓库、状态查询最近的评审记录。
- 使用 HMAC-SHA256 验证 GitHub Webhook 原始请求体。
- 按 `X-GitHub-Delivery` 原子去重并记录处理生命周期。
- Webhook 与持久化任务在同一事务内写入，接收进程重启不会丢任务。
- 独立 Worker 使用租约领取任务，支持心跳、崩溃恢复、退避重试和并发限制。
- 超过最大次数的任务进入 dead 状态，可由运维 CLI 显式重放。
- 使用 GitHub App 安装令牌检出 PR 的精确 base/head commit。
- 发布 Check Run annotations，并可选发布新增行 Review Comments。

## 目录

```text
code_review_agent/
├── run_review.py
├── inspect_reviews.py     # 查询已持久化的评审记录
├── run_github_webhook.py  # FastAPI/Uvicorn Webhook 服务
├── run_github_worker.py   # 持久化队列 Worker
├── agent/
│   ├── agent.py          # 单个结构化输出 LlmAgent
│   ├── config.py
│   ├── prompts.py
│   ├── reviewer.py       # Agent Runtime 适配器
│   └── skills.py         # Knowledge-only SkillToolSet
├── code_review/
│   ├── models.py         # ReviewRun / ChangedFile / Finding
│   ├── git_diff.py       # Git 采集和 Hunk 解析
│   ├── context_builder.py
│   ├── static_analysis.py
│   ├── database.py        # SQLAlchemy Schema 与 ReviewStore
│   ├── policy.py
│   ├── orchestrator.py
│   └── reporter.py
├── skills/               # 四个代码评审 SKILL.md
├── github_integration/
│   ├── app.py            # 验签、去重与 FastAPI 入口
│   ├── client.py         # GitHub App token 和 REST API
│   ├── checkout.py       # 受控、精确 commit 检出
│   ├── publisher.py      # Check Run 与行级评论
│   ├── service.py        # GitHub 评审编排
│   ├── runtime.py        # 环境配置与依赖构造
│   └── worker.py         # 租约、重试和崩溃恢复
└── sandbox/Dockerfile    # 离线运行时使用的分析镜像
```

## 安装

在仓库根目录安装项目：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

本地运行静态工具时另外安装：

```bash
pip install -r examples/code_review_agent/requirements-static.txt
```

## 先运行 no-LLM 模式

该模式不会读取模型配置。默认尝试在本地运行 Ruff 和 Bandit；工具未安装
时会记录为 `unavailable`，不会让评审失败：

```bash
python3 examples/code_review_agent/run_review.py \
  --repo . \
  --base HEAD~1 \
  --head HEAD \
  --no-llm
```

如需只采集 Diff、不运行任何工具：

```bash
python3 examples/code_review_agent/run_review.py \
  --repo . --base HEAD~1 --head HEAD \
  --no-llm --no-static-analysis
```

默认在当前目录的 `.code-review/` 中生成：

- `review.json`：完整、可机器消费的 ReviewRun。
- `review.md`：适合人工查看或未来写入 Check Run 的报告。
- `reviews.db`：SQLite 评审数据库。

相同仓库、实际 base/head commit、模型和完整运行配置会生成相同幂等键。
再次执行仍会完成评审，但持久化时会返回已经存在的记录，不会插入重复数据。

如需关闭数据库持久化：

```bash
python3 examples/code_review_agent/run_review.py \
  --repo . --base HEAD~1 --head HEAD \
  --no-llm --no-persist
```

## 数据库存储和查询

数据库包含以下表：

- `code_review_runs`
- `code_review_changed_files`
- `code_review_findings`
- `code_review_analyzer_executions`
- `code_review_github_deliveries`
- `code_review_github_jobs`
- `code_review_github_publications`
- `code_review_schema_version`

可以通过同步 SQLAlchemy URL 切换数据库：

```bash
export CODE_REVIEW_DATABASE_URL=sqlite:////absolute/path/reviews.db
```

也可以仅对单次运行传入：

```bash
python3 examples/code_review_agent/run_review.py \
  --repo . --base HEAD~1 --head HEAD \
  --no-llm \
  --database-url sqlite:////absolute/path/reviews.db
```

列出最近的运行：

```bash
python3 examples/code_review_agent/inspect_reviews.py list --limit 20
```

按状态或仓库过滤：

```bash
python3 examples/code_review_agent/inspect_reviews.py list \
  --status failed \
  --repository /path/to/repository
```

查看完整 Markdown 或 JSON：

```bash
python3 examples/code_review_agent/inspect_reviews.py show RUN_ID
python3 examples/code_review_agent/inspect_reviews.py show RUN_ID --format json
```

查看 GitHub Webhook 处理状态：

```bash
python3 examples/code_review_agent/inspect_reviews.py deliveries
python3 examples/code_review_agent/inspect_reviews.py deliveries --status failed
```

查看持久化队列和重放 dead job：

```bash
python3 examples/code_review_agent/inspect_reviews.py jobs
python3 examples/code_review_agent/inspect_reviews.py jobs --status dead
python3 examples/code_review_agent/inspect_reviews.py replay DELIVERY_ID
```

## 运行 LLM 评审

配置 OpenAI-compatible 模型：

```bash
export TRPC_AGENT_API_KEY=your-api-key
export TRPC_AGENT_BASE_URL=https://your-endpoint/v1
export TRPC_AGENT_MODEL_NAME=your-model
```

执行：

```bash
python3 examples/code_review_agent/run_review.py \
  --repo /path/to/repository \
  --base main \
  --head feature-branch
```

模型只收到经过过滤和预算限制的 unified diff，不会收到整个仓库。模型还
会收到静态分析的结构化结果，并可加载四个知识型评审 Skills。

## GitHub App Webhook

GitHub App 最小仓库权限：

- Contents: Read
- Checks: Read and write
- Pull requests: Read and write（关闭行级评论时可以只保留 Read）

订阅 `pull_request` 事件。当前处理 `opened`、`reopened`、
`synchronize` 和 `ready_for_review`，草稿 PR 会记录为 `ignored`。
Webhook URL 指向：

```text
https://your-service.example/webhooks/github
```

安装 GitHub App JWT 依赖：

```bash
pip install -r examples/code_review_agent/requirements-github.txt
```

参考 `.env.github.example` 配置环境变量，至少需要：

```bash
export GITHUB_WEBHOOK_SECRET=high-entropy-webhook-secret
export GITHUB_APP_ID=123456
export GITHUB_APP_PRIVATE_KEY_PATH=/absolute/path/github-app.pem
export TRPC_AGENT_API_KEY=your-api-key
export TRPC_AGENT_BASE_URL=https://your-endpoint/v1
export TRPC_AGENT_MODEL_NAME=your-model
```

开发环境也可以使用预生成的安装令牌：

```bash
export GITHUB_TOKEN=installation-token
```

分别启动 Webhook 接收进程和至少一个 Worker：

```bash
python3 examples/code_review_agent/run_github_webhook.py
python3 examples/code_review_agent/run_github_worker.py --concurrency 2
```

健康检查：

```bash
curl http://127.0.0.1:8080/healthz
```

默认 GitHub 模式使用 Docker 静态分析。处理流程为：

1. 对原始请求体执行 HMAC-SHA256 恒定时间验签。
2. 在同一数据库事务中写入 delivery 和 durable job，立即返回 HTTP 202。
3. Worker 以 CAS 方式领取任务并创建带心跳的有期限租约。
4. 创建 `in_progress` Check Run。
5. 通过环境变量向 Git 传递认证头，不把 token 放入 URL 或命令参数。
6. 检出签名 Payload 中的精确 base/head SHA，运行现有评审管道并持久化。
7. 分批发布最多 50 条/请求的 Check annotations。
8. 可选发布最多 `GITHUB_REVIEW_MAX_COMMENTS` 条右侧新增行评论。
9. 成功后完成 job；临时错误按指数退避重试，过期租约会被其他 Worker 恢复。
10. 达到 `GITHUB_REVIEW_MAX_ATTEMPTS` 后进入 dead/failed，等待显式重放。

REST 请求固定发送 `X-GitHub-Api-Version: 2026-03-10`。安装令牌请求主动
收窄为 Contents read、Checks write 和 Pull requests write；代码不依赖
安装令牌的固定长度。

生产环境应让 Webhook 与所有 Worker 共享同一个
`CODE_REVIEW_DATABASE_URL`。SQLite 适合单机运行；多副本部署建议使用
PostgreSQL 或 MySQL。主要队列参数：

```text
GITHUB_REVIEW_MAX_ATTEMPTS=5
GITHUB_REVIEW_WORKER_CONCURRENCY=1
GITHUB_REVIEW_POLL_SECONDS=2
GITHUB_REVIEW_LEASE_SECONDS=300
GITHUB_REVIEW_RETRY_BASE_SECONDS=5
GITHUB_REVIEW_RETRY_MAX_SECONDS=300
```

租约时间应显著长于正常心跳间隔；Worker 每隔约三分之一租约时间自动续租。
收到 SIGINT/SIGTERM 后 Worker 会停止领取新任务，并等待当前任务结束。

## Docker 沙箱

构建一次分析镜像：

```bash
docker build \
  -t trpc-code-review:latest \
  examples/code_review_agent/sandbox
```

在容器中运行 Ruff、Bandit，并可选运行 Pytest：

```bash
python3 examples/code_review_agent/run_review.py \
  --repo /path/to/repository \
  --base main \
  --head feature-branch \
  --no-llm \
  --static-runtime docker \
  --run-tests
```

运行容器时框架固定使用：

- `--network none`
- 仓库和根文件系统只读
- `--cap-drop ALL`
- `no-new-privileges`
- 非 root UID/GID
- CPU、内存、PID 和超时限制
- `--pull never`，评审期间不会隐式下载镜像

## 常用参数

```text
--direct-base               不使用 merge-base，直接比较 base 与 head
--context-lines 3           每个 Hunk 携带的上下文行数
--max-files 40              最大送审文件数
--max-file-chars 24000      单文件最大 Patch 字符数
--max-total-chars 120000    总 Diff 上下文字符数
--minimum-confidence 0.75   丢弃低置信度 Finding
--output-dir PATH           报告输出目录
--no-static-analysis        不运行 Ruff/Bandit/Pytest
--static-runtime MODE       local 或 docker
--run-tests                 额外执行 Pytest
--strict-static-tools       工具缺失或失败时让 ReviewRun 失败
--static-timeout 120        每个静态工具的超时秒数
--docker-image IMAGE        指定已经构建的分析镜像
--database-url URL          指定同步 SQLAlchemy 数据库 URL
--no-persist                不写入数据库
```

## Finding 约定

```json
{
  "rule_id": "python.correctness.none-sentinel",
  "severity": "medium",
  "confidence": 0.88,
  "category": "correctness",
  "file_path": "app.py",
  "start_line": 10,
  "end_line": 10,
  "title": "Ambiguous error result",
  "description": "Returning None silently changes the function contract.",
  "suggestion": "Raise a documented exception or return a typed result.",
  "source": "llm",
  "publishable": true
}
```

`publishable` 不由模型或扫描器决定。所有结果经过同一个 Policy 后，只有
文件属于本次 Diff 且行号命中新增行时才会被设置为 `true`。

## 测试

```bash
pytest tests/code_review -q
```

测试使用临时 Git 仓库、fake analyzer 和 fake reviewer，不调用真实模型：

- Diff 文件、重命名和行号解析
- revision 参数安全校验
- 二进制/生成目录过滤
- 上下文预算与截断
- Finding 路径、变更行、去重和置信度策略
- Ruff/Bandit JSON 到 Finding 的转换
- Docker 禁网、只读和资源限制参数
- 静态分析与 LLM Finding 的统一 Policy
- SQLite 完整对象图写入和读取
- 幂等重复写入、失败运行持久化和查询过滤
- 运行与查询 CLI 端到端
- GitHub 官方 HMAC 测试向量、Payload 解析和重复 delivery 冲突
- Webhook 原子入队、Worker 成功/重试/dead 状态转换
- 租约续期、过期租约恢复、所有权校验和人工重放
- GitHub API 版本/认证头、受控检出和 host allowlist
- Webhook 到评审、数据库、Check Run、精确新增行评论的模拟端到端
- no-LLM 与 fake reviewer 端到端管道

## 当前边界

- 当前使用内建 Schema 版本表和 `create_all` 初始化版本 4；后续修改表结构时
  应引入 Alembic，而不是依赖 `create_all` 修改既有表。
- `ReviewStore` 使用同步 SQLAlchemy 驱动；异步数据库 URL 目前不接受。
- 队列提供 at-least-once 执行语义。数据库与 GitHub 无法形成跨系统事务；
  Check Run 使用 delivery ID 作为 `external_id`，重试时会从 GitHub 恢复；
  行级评论也使用稳定标记跳过最近的重复项。由于 GitHub 最近评论查询存在分页
  边界，极端高流量 PR 在外部请求成功后立即崩溃时仍建议人工核对。
- 目前只支持 GitHub/GitHub Enterprise 的 HTTPS Git URL，允许的 clone host
  必须显式配置在 `GITHUB_CLONE_HOSTS`。
- Docker 镜像只包含通用 Python 工具，不会自动安装被评审项目的依赖；
  因此 Pytest 适合依赖自包含的项目，后续需要增加受控依赖准备策略。
- Finding 是否真实正确仍取决于所使用模型；Policy 只保证输出结构和
  Diff 位置合法，不能代替评审质量评估。
