# 基于 Skill 的代码审查 Agent

本示例提供自动代码审查 Agent 的最小框架：Workflow 只负责输入、调用、校验、落库和报告等确定性步骤；Agent 负责判断是否需要 Skill 和沙箱检查。`code-review` Skill 提供规则和脚本，结果通过可替换存储层持久化，并生成 JSON 与 Markdown 报告。

## 目录结构

```text
skills_code_review_agent/
├── run_agent.py                  # 主要入口
├── workflow.py                   # 审查流程编排
├── docs/design.md                # 方案设计说明
├── agent/
│   ├── agent.py                  # LlmAgent 构建
│   ├── config.py                 # 模型配置
│   ├── fake.py                   # 确定性 fake model
│   ├── normalization.py          # 去重、降噪和脱敏
│   ├── prompts.py                # 审查 Prompt
│   └── tools.py                  # SkillToolSet 与沙箱连接
├── inputs/                       # diff、file list、worktree、fixture 输入
├── filters/                      # 命令策略和 SDK Tool Filter
├── skills/code-review/
│   ├── SKILL.md                  # Skill 入口
│   ├── agents/openai.yaml        # Skill UI 元数据
│   ├── references/RULES.md       # 审查规则
│   └── scripts/                  # 输入解析、受控读取及分类审查脚本
├── sandbox/
│   ├── base.py                   # 可替换沙箱接口
│   ├── factory.py                # 环境变量驱动的实现选择
│   ├── docker.py                 # Docker 实现
│   ├── lazy.py                   # 按工具调用惰性创建 runtime
│   ├── fake.py                   # 不执行代码的测试模拟器
│   ├── .dockerignore             # 最小化镜像构建上下文
│   └── Dockerfile                # 最小审查镜像
├── storage/
│   ├── base.py                   # BaseReviewStore 抽象基类
│   ├── factory.py                # 环境变量驱动的实现选择
│   ├── schema.sql                # 显式 SQLite schema
│   └── sqlite.py                 # SQLite 实现
├── reports/
│   ├── models.py                 # 结构化审查模型
│   └── writers.py                # JSON/Markdown 输出
├── tests/fixtures/               # 8 条要求样本及超时补充样本
├── tests/run_tests.py            # 非 Docker 验收测试入口
├── tests/run_docker_tests.py     # tRPC Container runtime 集成测试
├── tests/evaluate_fixtures.py    # 公开 fixture 指标评测
└── examples/review_report.*      # 示例报告
```

## 运行要求

- Python 3.10+
- 已按仓库根目录说明安装 `trpc-agent-python` 及其现有依赖
- fake/dry-run 不需要 Docker 或模型 API Key
- 真实模式需要 Docker daemon，以及模型环境变量

远程模型地址必须使用 HTTPS；仅 `localhost`、`127.0.0.1` 和 `::1`
允许使用 HTTP，便于连接本地开发模型服务。
生产环境建议设置 `TRPC_AGENT_ALLOWED_MODEL_HOSTS`，限制可接收 API Key
和审查证据的模型服务域名。

本示例不额外依赖 `.env` 解析库。入口只读取示例目录下权限为 `0600`、
键名前缀为 `TRPC_AGENT_` 或 `CODE_REVIEW_` 的普通文件；同名进程变量优先：

```bash
cp examples/skills_code_review_agent/.env.example \
  examples/skills_code_review_agent/.env
chmod 600 examples/skills_code_review_agent/.env
```

## 输入与运行方式

所有命令从仓库根目录执行。默认审查 Git 工作区变更：

```bash
uv run --project examples/skills_code_review_agent --with-editable . \
  python examples/skills_code_review_agent/run_agent.py \
  --repo-path /path/to/repository
```

其他输入：

```bash
# unified diff / PR patch
uv run --project examples/skills_code_review_agent --with-editable . \
  python examples/skills_code_review_agent/run_agent.py --diff-file change.patch

# 文件路径列表；真实模式同时提供列表所属仓库
uv run --project examples/skills_code_review_agent --with-editable . \
  python examples/skills_code_review_agent/run_agent.py \
  --repo-path /path/to/repository --file-list /path/to/repository/files.txt

# 内置 fixture，无模型、无 Docker
uv run --project examples/skills_code_review_agent --with-editable . \
  python examples/skills_code_review_agent/run_agent.py \
  --fixture security --fake-model
```

`--dry-run` 同样走确定性 fake 链路，仍执行解析、Filter、sandbox 模拟、落库和报告生成，但不执行任何宿主或容器命令。省略输入时从当前工作目录向上查找最近的 Git worktree，并仅审查其变更；全仓库审查必须显式添加 `--full`。

unified diff 解析结果保留每个 hunk 的 added、removed、unchanged context
行、old/new 双侧行号和候选变更行号，生命周期规则可利用未修改上下文降噪。

## 输出和持久化

默认输出位置：

- SQLite：`storage/reviews.sqlite3`
- JSON：`reports/output/<task-id>/review_report.json`
- Markdown：`reports/output/<task-id>/review_report.md`

持久化默认由以下环境变量选择：

```bash
CODE_REVIEW_STORAGE_BACKEND=sqlite
CODE_REVIEW_SQLITE_PATH=storage/reviews.sqlite3
CODE_REVIEW_SQLITE_SCHEMA_PATH=storage/schema.sql
```

当前只实现 `sqlite`；新增后端应继承 `BaseReviewStore` 并在 `storage/factory.py` 注册。`--database` 的优先级高于 `CODE_REVIEW_SQLITE_PATH`。
`CODE_REVIEW_SQLITE_SCHEMA_PATH` 可选择兼容的 SQLite 初始化 schema；替换文件必须保留存储实现使用的表和字段契约，并且只能使用 `storage/` 下的普通文件。schema 有大小限制，初始化时禁止 attach、trigger、view、虚拟表、删除对象和业务数据写入。

SQLite 分表保存 `review_tasks`、`review_inputs`、`sandbox_runs`、`filter_decisions`、`findings`、`monitoring_summaries` 和 `review_reports`，`get_task_details(task_id)` 可查询完整审计记录。任务在 Agent 启动前以 `running` 状态落库，异常终止会更新为 `failed`。SQLite 启用 WAL、等待锁和 digest/profile 索引。对于内容不可变的 diff/fixture，缓存必须同时匹配输入摘要、规则、Skill、模式、模型和审查范围；是否复用仍由 Agent 决定。

沙箱实现由 `CODE_REVIEW_SANDBOX_BACKEND=docker` 选择，当前仅提供 Docker；新增实现需满足 `SandboxProvider` 并在 `sandbox/factory.py` 注册。可通过 `--output-dir` 和 `--docker-image` 覆盖输出目录和镜像。Docker runtime 按 Agent 的 workspace 工具调用惰性创建；代码只读挂载，diff/fixture 仅挂载任务级副本。容器禁网、非 root、删除 capabilities、启用 `no-new-privileges` 和只读根文件系统，并限制 CPU、内存、PID 与 tmpfs。模型服务仍由宿主进程调用，因此应使用符合代码数据策略的模型服务。

## 安全和治理

- 真实执行只通过加固 Docker；fake sandbox 不执行代码。报告目录和 SQLite 使用仅当前用户可读写权限。
- diff 任务副本使用仅当前用户可读权限；SQLite 在首次连接前以 `0600` 安全创建，并拒绝符号链接路径。
- unified diff 和 Git staged/unstaged diff 均通过聚合脚本调用安全、异步、资源、数据库、测试和敏感信息六个独立规则；结果按最多 24 条记录分页，避免 SDK 的 16KB inline 上限截断 JSON。
- 文件列表和受控文件读取同样分页；路径长度、数量、敏感文件和符号链接在容器内再次校验。Git 工作区的直接读取还会重新验证路径属于 changed 或 full scope，避免模型读取未选择文件。
- `skill_run` 必须先完成 `skill_load`。前置 Filter 同时检查输入模式、命令、脚本、Git 参数、路径、网络、环境变量和预算；`deny`、`needs_human_review` 不进入沙箱。`compileall` 只做有界语法编译；`unittest` 和 `pytest` 会执行不受信任的仓库代码，默认进入人工复核。仅在确认仓库与挂载内容可信后，才可设置 `CODE_REVIEW_ALLOW_REPOSITORY_EXECUTION=true` 显式放行。
- 单次 Skill run 默认 30 秒；整次 review 默认 110 秒、30 次工具调用和 12 次 sandbox run。所有限制均可通过 `.env` 中的 `CODE_REVIEW_*` 字段收紧。
- Workflow 可信地记录每个脚本的 cursor；缺少必需的 staged、unstaged、文件枚举或受控读取证据，或者任一 `next_cursor` 未读完时，报告会强制加入人工复核项。
- 代码、注释和工具输出均按不可信数据处理；Filter 阻止外部 diff helper、敏感路径和跨输入模式读取。
- 输入预览、finding、Filter、sandbox 输出、数据库和报告写入前执行敏感信息脱敏。
- 容器进程由容器内 `timeout` 终止；stdout/stderr 在返回模型前按 `CODE_REVIEW_MAX_OUTPUT_BYTES` 硬限制并脱敏。受 Skill 工具 16 KiB inline 契约约束，Docker 传输的两路输出合计还会取配置值与 15 KiB 的较小者，避免 Docker Desktop 在 64 KiB socket 边界产生长时间等待。
- findings 按 `(file, line, category)` 去重，置信度低于 `0.70` 自动进入 warnings。

## 测试

按要求使用 uv 启动，不运行 Docker：

```bash
uv run --project examples/skills_code_review_agent --with-editable . \
  python examples/skills_code_review_agent/tests/run_tests.py
```

公开 fixture 指标评测：

```bash
uv run --project examples/skills_code_review_agent --with-editable . \
  python examples/skills_code_review_agent/tests/evaluate_fixtures.py
```

指标输出包含高风险检出率、clean diff 误报率、敏感信息检出率，
并确认 8 个必需 fixture 均生成 JSON 和 Markdown 报告。

不调用模型、但实际启动 Docker runtime 的集成测试：

```bash
uv run --project examples/skills_code_review_agent --with-editable . \
  python examples/skills_code_review_agent/tests/run_docker_tests.py
```

测试覆盖：无问题、安全问题、异步任务泄漏、资源生命周期、数据库连接生命周期、测试缺失、重复 finding、sandbox 失败/超时、敏感信息脱敏、六类独立规则、配置工厂、分页、挂载最小化和报告注入。Docker 集成脚本验证 Skill 加载、规则执行、Filter、只读输入、禁网、真实超时、分页以及容器资源安全配置；模型效果仍取决于所配置模型，隐藏样本指标不能由公开 fixture 证明。

使用 `.env` 中的真实模型做完整联调：

```bash
uv run --project examples/skills_code_review_agent --with-editable . \
  python examples/skills_code_review_agent/run_agent.py --fixture security
```

详细取舍见 [docs/design.md](./docs/design.md)。
