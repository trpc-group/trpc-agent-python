# Automatic Code Review Agent — 维护与 PR 验收手册

[`README.md`](README.md) 是项目主入口，包含能力说明、官方验收标准、快速开始和实测基准；本文件是面向
合并 PR 前维护者的**详细维护与 PR 验收补充**。完整字段契约、预算与验收标准以同目录
[`DEV_SPEC.md`](DEV_SPEC.md) 为准。本文件同时提供 Windows PowerShell 与 Linux/macOS Bash 命令，
所有命令从仓库根目录执行。PowerShell 只复制代码块中的命令，不要复制终端提示符 `PS ...>` 或续行提示符 `>>`。

## 0. 前置检查与配置

```powershell
# Windows PowerShell
$py = ".\.venv\Scripts\python.exe"
& $py --version
docker version --format '{{.Client.Version}} / {{.Server.Version}}'
```

```bash
# Linux / macOS Bash
py=".venv/bin/python"
"$py" --version
docker version --format '{{.Client.Version}} / {{.Server.Version}}'
```

`.venv` 是所有开发、测试和评测的唯一 Python 环境。第二条 Docker 命令只在 Container
场景需要；Client 和 Server 都有版本号才表示 Docker Desktop daemon 可用。

### 依赖策略

仓库根 [`pyproject.toml`](../../pyproject.toml) 是 SDK 与开发依赖的规范源。本示例另外通过
[`requirements.txt`](requirements.txt) 声明 canonical report 校验所需的 `jsonschema`，从而不修改
共享 SDK 的依赖范围。新建维护环境需要安装根项目、开发工具和示例依赖：

```powershell
& $py -m pip install -e ".[dev]"
& $py -m pip install -r examples/skills_code_review_agent/requirements.txt
```

```bash
"$py" -m pip install -e ".[dev]"
"$py" -m pip install -r examples/skills_code_review_agent/requirements.txt
```

根项目提供 tRPC-Agent SDK、SQLAlchemy、Docker SDK、pytest 和 flake8；示例 requirements 只补充
`jsonschema`。本项目规则脚本本身优先使用标准库。Container 的额外前置是 Docker Desktop，
也不得在评审任务执行期间在线安装依赖。

| 需求 | 需要配置 | 不应放入 `.env` |
|---|---|---|
| `--model-mode off` / `fake` / `--dry-run` | 无 API Key | sandbox、网络、输出目录、DB URL |
| `--model-mode real` | 项目目录 `.env` 中的三项模型变量 | API Key 不会传给 sandbox |
| `--sandbox local` | 显式命令参数；无 Docker 要求 | 不可把它设成隐式默认 |
| `--sandbox container` | Docker Desktop 正在运行 | 不可把网络策略改为 `.env` 变量 |
| `--sandbox cube` | **当前示例不支持。** CLI 未注入 Cube/E2B runtime factory，且无机器可验证受控网络证明 | 不应尝试用环境变量绕过配置错误或 Filter deny；请改用 `container` 或显式 `local` |

仅首次创建真实模型配置时，复制模板后填入自己的值；已有 `.env` 不要覆盖：

```powershell
Copy-Item examples/skills_code_review_agent/.env.example examples/skills_code_review_agent/.env
```

```bash
cp examples/skills_code_review_agent/.env.example examples/skills_code_review_agent/.env
```

`.env` 只允许 `TRPC_AGENT_API_KEY`、`TRPC_AGENT_BASE_URL`、`TRPC_AGENT_MODEL_NAME`。
进程环境优先于 `.env`。它只供宿主上的 real LLM 增强使用，不会进入 sandbox、报告、数据库、日志或 Telemetry。

## 1. 产物定位、退出码与两条入口

每次 `review` 或 `user-query` 成功都会输出一行 JSON，包含 `task_id`、`entrypoint`、实际
`skill_tools`、`sandbox` 和：

```json
{
  "report_files": {
    "json": "C:\\...\\review_report.json",
    "markdown": "C:\\...\\review_report.md"
  }
}
```

完整路径只显示在当前终端，绝不写进报告、SQLite、日志或 Telemetry。数据库位置由你显式传入的
`--db-url` 决定；建议每次运行都把它放到同一 `--output-dir` 下。退出码 `0` 表示报告已生成，
`1` 表示 `--fail-on-severity` 命中正式 finding，`2` 表示配置或致命运行错误。

| 入口 | 何时使用 | 命令差异 |
|---|---|---|
| direct pipeline | 常规人工/CI 评审 | 使用 `review` |
| SDK Agent + SkillToolSet | 真实执行 `skill_load → skill_run` 并进入同一审查链路 | 使用 `user-query "<intent>"` 加一项结构化输入 |

两种调用方式共享 manifest、Filter、sandbox、storage、规则和 `ReviewPipeline`。Agent 入口只向
`skill_run` 提供宿主签发的一次性 request id；固定 Skill、脚本、argv、路径、环境和预算仍由宿主与
manifest 决定。终端 JSON 中 `skill_tools=["skill_load","skill_run"]` 是本次实际 SDK 工具事件，不是
静态声明。若未先 load、request id 无效或 Filter 非 ALLOW，沙箱执行副作用必须为零。

### 实时 trace（不泄露输入）

在 `review` 或 `user-query` 后增加 `--trace`，可参考 `examples/skills_with_container/run_agent.py` 的 Runner
事件展示方式，实时看到模型请求工具、工具响应和 pipeline 阶段。与该通用示例不同，本项目不会打印
`function_call.args`、`function_response.response` 或模型 thought/text：这些字段可能包含不可信内容。
trace 只会把下列脱敏 JSON Lines 写到 stderr，stdout 保持一条最终 JSON，便于 CI 使用：

```powershell
& $py examples/skills_code_review_agent/run_agent.py user-query `
  "请使用 code-review Skill 审查这个安全样例" `
  --fixture 02_security_simple `
  --trace `
  --sandbox local `
  --dry-run `
  --output-dir out/review_trace `
  --db-url sqlite+pysqlite:///out/review_trace/review.db
```

```text
[code-review-trace] {"event":"agent.tool_call","tool":"skill_load"}
[code-review-trace] {"event":"agent.tool_response","tool":"skill_load"}
[code-review-trace] {"event":"agent.tool_call","tool":"skill_run"}
[code-review-trace] {"event":"pipeline.filter_decision","action":"allow"}
[code-review-trace] {"event":"pipeline.sandbox_finished","status":"ok"}
[code-review-trace] {"event":"pipeline.report_persisted","status":"completed_with_warnings"}
```

完整事件还包括 `user_query.request_received`、`user_query.input_validated`、`review.started`、`agent.turn_started`、
`skill_run.started`、`pipeline.input_loaded`、`pipeline.sandbox_started`、`skill_run.completed` 和
`review.completed`。禁止把 stderr trace 当成审计持久化载体；最终 JSON、Markdown 与 SQLite bundle
仍是唯一可查询的交付结果。

### INFO / DEBUG 日志边界

默认 `--log-level INFO` 会在 stderr 显示 Agent 工具调用、输入计数、Filter action、沙箱状态、耗时、
finding 计数和报告保存位置。Container 运行时还会显示完整 Docker container ID，便于维护者关联 Docker
诊断。`--log-level DEBUG` 仅额外显示仓库相对路径、script_id 与已脱敏输出摘要。

SDK 原始 logger 被静默，避免其打印 SDK 源码绝对路径、workspace ID 或 request ID；最终 CLI JSON 仍独占
stdout。任何级别都不会打印原始 diff、代码、evidence、工具完整参数、环境变量、API Key、token 或 password。

## 2. 四种输入模式

以下均为本机开发 fallback，必须显式 `--sandbox local`，报告会保留隔离不可强制证明的 warning。

### Unified diff / PR patch

```powershell
& $py examples/skills_code_review_agent/run_agent.py review `
  --diff-file examples/skills_code_review_agent/tests/fixtures/diffs/02_security_complex.diff `
  --sandbox local `
  --dry-run `
  --output-dir out/review_diff `
  --db-url sqlite+pysqlite:///out/review_diff/review.db
```

```bash
"$py" examples/skills_code_review_agent/run_agent.py review --diff-file examples/skills_code_review_agent/tests/fixtures/diffs/02_security_complex.diff --sandbox local --dry-run --output-dir out/review_diff --db-url sqlite+pysqlite:///out/review_diff/review.db
```

### 当前 Git 工作区变更

```powershell
& $py examples/skills_code_review_agent/run_agent.py review `
  --repo-path . `
  --sandbox local `
  --dry-run `
  --output-dir out/review_repo `
  --db-url sqlite+pysqlite:///out/review_repo/review.db
```

```bash
"$py" examples/skills_code_review_agent/run_agent.py review --repo-path . --sandbox local --dry-run --output-dir out/review_repo --db-url sqlite+pysqlite:///out/review_repo/review.db
```

### 指定文件的全文件 snapshot 扫描

```powershell
& $py examples/skills_code_review_agent/run_agent.py review `
  --files examples/skills_code_review_agent/code_review/report.py `
  --input-root . `
  --sandbox local `
  --dry-run `
  --output-dir out/review_files `
  --db-url sqlite+pysqlite:///out/review_files/review.db
```

```bash
"$py" examples/skills_code_review_agent/run_agent.py review --files examples/skills_code_review_agent/code_review/report.py --input-root . --sandbox local --dry-run --output-dir out/review_files --db-url sqlite+pysqlite:///out/review_files/review.db
```

### 受控 fixture 输入

```powershell
& $py examples/skills_code_review_agent/run_agent.py review `
  --fixture 02_security_simple `
  --sandbox local `
  --dry-run `
  --output-dir out/review_fixture `
  --db-url sqlite+pysqlite:///out/review_fixture/review.db
```

```bash
"$py" examples/skills_code_review_agent/run_agent.py review --fixture 02_security_simple --sandbox local --dry-run --output-dir out/review_fixture --db-url sqlite+pysqlite:///out/review_fixture/review.db
```

要通过 SDK Agent 审查任一种输入，使用 `user-query`；自然语言只表达意图，输入路径或 fixture 必须显式传参：

```powershell
& $py examples/skills_code_review_agent/run_agent.py user-query "请审查这个安全样例" --fixture 02_security_simple --sandbox local --dry-run --log-level INFO --output-dir out/review_fixture_agent --db-url sqlite+pysqlite:///out/review_fixture_agent/review.db
```

```bash
"$py" examples/skills_code_review_agent/run_agent.py user-query "Review this security fixture" --fixture 02_security_simple --sandbox local --dry-run --log-level INFO --output-dir out/review_fixture_agent --db-url sqlite+pysqlite:///out/review_fixture_agent/review.db
```

`user-query` 同时支持 diff、Git 工作区和文件 snapshot；它不会把自由文本解释成文件路径、shell 命令或
环境变量。格式错误 diff、路径逃逸、非 UTF-8 文本、超预算输入和疑似明文凭据会在创建 Agent 前以退出码 2 拒绝：

```powershell
& $py examples/skills_code_review_agent/run_agent.py user-query `
  "请使用 code-review Skill 审查这个补丁" `
  --diff-file .\changes.diff `
  --sandbox local `
  --dry-run `
  --output-dir out/review_user_query `
  --db-url sqlite+pysqlite:///out/review_user_query/review.db
```

```bash
"$py" examples/skills_code_review_agent/run_agent.py user-query \
  "Use the code-review Skill to review this patch" \
  --diff-file ./changes.diff \
  --sandbox local \
  --dry-run \
  --output-dir out/review_user_query \
  --db-url sqlite+pysqlite:///out/review_user_query/review.db
```

`user-query` 的四种结构化输入与 `review` 一致。下面是可直接复制的完整 Agent 命令；输出位置均会在
stderr INFO 和最后一行 stdout JSON 的 `report_files` 中显示：

```powershell
# diff / PR patch
& $py examples/skills_code_review_agent/run_agent.py user-query "请审查这个补丁" --diff-file .\changes.diff --sandbox local --dry-run --output-dir out/agent_diff --db-url sqlite+pysqlite:///out/agent_diff/review.db
# 当前 Git 工作区
& $py examples/skills_code_review_agent/run_agent.py user-query "请审查当前工作区变更" --repo-path . --sandbox local --dry-run --output-dir out/agent_repo --db-url sqlite+pysqlite:///out/agent_repo/review.db
# 指定文件 snapshot
& $py examples/skills_code_review_agent/run_agent.py user-query "请审查这些文件" --files examples/skills_code_review_agent/code_review/report.py --input-root . --sandbox local --dry-run --output-dir out/agent_files --db-url sqlite+pysqlite:///out/agent_files/review.db
# 内置 fixture
& $py examples/skills_code_review_agent/run_agent.py user-query "请审查安全风险样例" --fixture 02_security_simple --sandbox local --dry-run --output-dir out/agent_fixture --db-url sqlite+pysqlite:///out/agent_fixture/review.db
```

```bash
"$py" examples/skills_code_review_agent/run_agent.py user-query "Review this patch" --diff-file ./changes.diff --sandbox local --dry-run --output-dir out/agent_diff --db-url sqlite+pysqlite:///out/agent_diff/review.db
"$py" examples/skills_code_review_agent/run_agent.py user-query "Review the current Git workspace" --repo-path . --sandbox local --dry-run --output-dir out/agent_repo --db-url sqlite+pysqlite:///out/agent_repo/review.db
"$py" examples/skills_code_review_agent/run_agent.py user-query "Review these files" --files examples/skills_code_review_agent/code_review/report.py --input-root . --sandbox local --dry-run --output-dir out/agent_files --db-url sqlite+pysqlite:///out/agent_files/review.db
"$py" examples/skills_code_review_agent/run_agent.py user-query "Review the security fixture" --fixture 02_security_simple --sandbox local --dry-run --output-dir out/agent_fixture --db-url sqlite+pysqlite:///out/agent_fixture/review.db
```

`--dry-run` / `--model-mode fake` 使用离线模型，但仍由真实 `LlmAgent + Runner` 发出两个工具调用；
`--model-mode real` 会让 Agent 编排和报告增强都显式使用 `.env` 中的真实模型。无论模型模式如何，
确定性 finding、Filter 决策和沙箱执行都不交给模型。

## 3. 16 个公开 fixture

下面函数为每个 fixture 创建独立输出目录和 SQLite 文件。默认走 direct pipeline；如需验证 Agent
入口请使用上一节的 `user-query "<intent>" --fixture <name>`，不再提供 `-ViaAgent` 兼容开关。

```powershell
function Invoke-ReviewFixture {
  param(
    [Parameter(Mandatory = $true)][string]$Fixture
  )
  $outputDir = "out\fixtures\$Fixture"
  $dbUrl = "sqlite+pysqlite:///$($outputDir.Replace('\', '/'))/review.db"
  $cliArguments = @(
    "examples/skills_code_review_agent/run_agent.py", "review",
    "--fixture", $Fixture,
    "--sandbox", "local",
    "--dry-run",
    "--output-dir", $outputDir,
    "--db-url", $dbUrl
  )
  & $py @cliArguments
}
```

| 场景 | simple | complex |
|---|---|---|
| 无问题 | `Invoke-ReviewFixture 01_clean_simple` | `Invoke-ReviewFixture 01_clean_complex` |
| 安全风险 | `Invoke-ReviewFixture 02_security_simple` | `Invoke-ReviewFixture 02_security_complex` |
| 异步/资源泄漏 | `Invoke-ReviewFixture 03_async_leak_simple` | `Invoke-ReviewFixture 03_async_leak_complex` |
| 数据库生命周期 | `Invoke-ReviewFixture 04_db_lifecycle_simple` | `Invoke-ReviewFixture 04_db_lifecycle_complex` |
| 测试缺失 | `Invoke-ReviewFixture 05_missing_tests_simple` | `Invoke-ReviewFixture 05_missing_tests_complex` |
| 去重 | `Invoke-ReviewFixture 06_duplicate_finding_simple` | `Invoke-ReviewFixture 06_duplicate_finding_complex` |
| 沙箱失败即数据 | `Invoke-ReviewFixture 07_sandbox_failure_simple` | `Invoke-ReviewFixture 07_sandbox_failure_complex` |
| 密钥检测与脱敏 | `Invoke-ReviewFixture 08_secret_redaction_simple` | `Invoke-ReviewFixture 08_secret_redaction_complex` |

每一项都会生成 JSON、Markdown 和 SQLite bundle。`_simple` 是 AC1/AC2 的公开快速门禁；
`_complex` 是同类的多文件、60–150 行工程上下文回归，不改变 evaluate 的 8 条分母。

Linux/macOS Bash 使用以下等价函数和全部 16 个 direct 调用；Agent 场景使用上一节的 `user-query` 命令：

```bash
review_fixture() {
  local fixture="$1"
  local output_dir="out/fixtures/${fixture}"
  local db_url="sqlite+pysqlite:///${output_dir}/review.db"
  local args=(examples/skills_code_review_agent/run_agent.py review --fixture "$fixture" --sandbox local --dry-run --output-dir "$output_dir" --db-url "$db_url")
  "$py" "${args[@]}"
}

review_fixture 01_clean_simple
review_fixture 01_clean_complex
review_fixture 02_security_simple
review_fixture 02_security_complex
review_fixture 03_async_leak_simple
review_fixture 03_async_leak_complex
review_fixture 04_db_lifecycle_simple
review_fixture 04_db_lifecycle_complex
review_fixture 05_missing_tests_simple
review_fixture 05_missing_tests_complex
review_fixture 06_duplicate_finding_simple
review_fixture 06_duplicate_finding_complex
review_fixture 07_sandbox_failure_simple
review_fixture 07_sandbox_failure_complex
review_fixture 08_secret_redaction_simple
review_fixture 08_secret_redaction_complex

# 任选一个 fixture 验证 SDK Agent + SkillToolSet 入口。
"$py" examples/skills_code_review_agent/run_agent.py user-query "Review this security fixture" --fixture 02_security_simple --sandbox local --dry-run --output-dir out/agent_fixture --db-url sqlite+pysqlite:///out/agent_fixture/review.db
```

## 4. 模型和沙箱组合

### 模型模式

```powershell
# 关闭 LLM 文本增强；规则、Filter、sandbox 和落库仍完整执行。
& $py examples/skills_code_review_agent/run_agent.py review --fixture 02_security_simple --sandbox local --model-mode off --output-dir out/review_off --db-url sqlite+pysqlite:///out/review_off/review.db

# fake 增强，离线可复现。
& $py examples/skills_code_review_agent/run_agent.py review --fixture 02_security_simple --sandbox local --model-mode fake --output-dir out/review_fake --db-url sqlite+pysqlite:///out/review_fake/review.db

# real 增强；需要 .env 三项，且不可同时传 --dry-run。
& $py examples/skills_code_review_agent/run_agent.py review --fixture 02_security_simple --sandbox local --model-mode real --output-dir out/review_real --db-url sqlite+pysqlite:///out/review_real/review.db
```

`--dry-run` 强制 fake，因此 `--dry-run --model-mode real` 不会调用真实模型。

Linux/macOS Bash 的模型命令与参数保持一致，只替换调用方式：

```bash
"$py" examples/skills_code_review_agent/run_agent.py review --fixture 02_security_simple --sandbox local --model-mode off --output-dir out/review_off --db-url sqlite+pysqlite:///out/review_off/review.db
"$py" examples/skills_code_review_agent/run_agent.py review --fixture 02_security_simple --sandbox local --model-mode fake --output-dir out/review_fake --db-url sqlite+pysqlite:///out/review_fake/review.db
"$py" examples/skills_code_review_agent/run_agent.py review --fixture 02_security_simple --sandbox local --model-mode real --output-dir out/review_real --db-url sqlite+pysqlite:///out/review_real/review.db
```

### local、Container 与当前不可用的 Cube

```powershell
# local：开发 fallback，输出目录下的 .workspaces 仅作运行期工作根，任务结束后会清理。
& $py examples/skills_code_review_agent/run_agent.py review --fixture 02_security_simple --sandbox local --dry-run --output-dir out/review_local --db-url sqlite+pysqlite:///out/review_local/review.db

# Container：生产严格默认；Docker daemon 必须已启动，运行时强制 network_mode=none。
& $py examples/skills_code_review_agent/run_agent.py review --fixture 02_security_simple --sandbox container --model-mode fake --output-dir out/review_container --db-url sqlite+pysqlite:///out/review_container/review.db

# Container + real 模型：模型调用在宿主侧；Key 不传入容器。
& $py examples/skills_code_review_agent/run_agent.py review --fixture 02_security_simple --sandbox container --model-mode real --output-dir out/review_real_container --db-url sqlite+pysqlite:///out/review_real_container/review.db

```

> **Cube/E2B 当前不可用，不是可执行示例。** `--sandbox cube` 为后续 SDK runtime 接线预留的
> CLI 枚举值；当前入口没有注入 `cube_runtime_factory`，所以命令会以配置错误（退出码 2）结束。
> 即使未来提供 runtime factory，仍必须先提供
> 可机器验证的无出口网络或受控网关证明；否则 Filter 也会拒绝执行。不要把它作为 CI 命令，也不要
> 用 `.env` 或 Filter 参数尝试绕过。生产评审请使用 `container`，本地调试请显式使用 `local`。

Container 不可用时会返回配置错误，绝不会静默回退到 local。首次启动若 Docker 需要拉取 SDK 默认镜像，
由 Docker 按本机策略完成；评审任务本身仍是 `network_mode=none`，不会在任务执行期间在线安装依赖。

Linux/macOS Bash 的沙箱命令：

```bash
"$py" examples/skills_code_review_agent/run_agent.py review --fixture 02_security_simple --sandbox local --dry-run --output-dir out/review_local --db-url sqlite+pysqlite:///out/review_local/review.db
"$py" examples/skills_code_review_agent/run_agent.py review --fixture 02_security_simple --sandbox container --model-mode fake --output-dir out/review_container --db-url sqlite+pysqlite:///out/review_container/review.db
"$py" examples/skills_code_review_agent/run_agent.py review --fixture 02_security_simple --sandbox container --model-mode real --output-dir out/review_real_container --db-url sqlite+pysqlite:///out/review_real_container/review.db
```

## 5. 报告、数据库与 CI 退出码

把 CLI JSON 保存为 PowerShell 对象即可立刻打开报告并按 task id 查询：

```powershell
$result = & $py examples/skills_code_review_agent/run_agent.py review --fixture 02_security_simple --sandbox local --dry-run --output-dir out/review_query --db-url sqlite+pysqlite:///out/review_query/review.db | ConvertFrom-Json
$result.report_files.json
$result.report_files.markdown
& $py examples/skills_code_review_agent/run_agent.py show $result.task_id --db-url sqlite+pysqlite:///out/review_query/review.db
& $py examples/skills_code_review_agent/run_agent.py list --db-url sqlite+pysqlite:///out/review_query/review.db
& $py examples/skills_code_review_agent/run_agent.py init-db --db-url sqlite+pysqlite:///out/review_query/review.db
```

用于 CI 阻断 high/critical finding：

```powershell
& $py examples/skills_code_review_agent/run_agent.py review --fixture 02_security_simple --sandbox local --dry-run --fail-on-severity high --output-dir out/review_ci --db-url sqlite+pysqlite:///out/review_ci/review.db
```

此命令即使成功生成报告也会以退出码 `1` 返回；Filter 拦截、sandbox warning、人工复核项默认不改变退出码。

Linux/macOS Bash 查询同一个 bundle 时，先把成功 JSON 保存在输出目录，再读取 task id：

```bash
mkdir -p out/review_query
"$py" examples/skills_code_review_agent/run_agent.py review --fixture 02_security_simple --sandbox local --dry-run --output-dir out/review_query --db-url sqlite+pysqlite:///out/review_query/review.db | tee out/review_query/cli_result.json
task_id=$("$py" -c "import json; print(json.load(open('out/review_query/cli_result.json', encoding='utf-8'))['task_id'])")
"$py" examples/skills_code_review_agent/run_agent.py show "$task_id" --db-url sqlite+pysqlite:///out/review_query/review.db
"$py" examples/skills_code_review_agent/run_agent.py list --db-url sqlite+pysqlite:///out/review_query/review.db
"$py" examples/skills_code_review_agent/run_agent.py init-db --db-url sqlite+pysqlite:///out/review_query/review.db
```

## 6. 自动化测试、评测与 PR 验收

```powershell
# 分层测试
& $py -m pytest examples/skills_code_review_agent/tests/unit -q -p no:cacheprovider
& $py -m pytest examples/skills_code_review_agent/tests/integration -q -p no:cacheprovider
& $py -m pytest examples/skills_code_review_agent/tests/e2e -q -p no:cacheprovider

# 普通完整回归：不要求 Docker 或真实模型。
& $py -m pytest examples/skills_code_review_agent/tests -q -m "not container and not real_llm" -p no:cacheprovider

# 可选的实际 Container / real 模型验证。
& $py -m pytest examples/skills_code_review_agent/tests/integration -q -m container -p no:cacheprovider
& $py -m pytest examples/skills_code_review_agent/tests/integration -q -m real_llm -p no:cacheprovider

# 公开代理门禁与静态规范。
& $py examples/skills_code_review_agent/evaluate.py --sandbox local --output-dir out/eval_local
& $py examples/skills_code_review_agent/evaluate.py --sandbox container --output-dir out/eval_container
& $py -m flake8 examples/skills_code_review_agent
```

`evaluate.py` 只对公开代理语料证明 AC2，不代表官方隐藏样本结果。Container/real 模型测试是可选集成：
缺 Docker 或 `.env` 时可以跳过对应标记，但不能把实现缺失伪装为 skip。

Linux/macOS Bash 的测试、评测和 lint 命令仅将 `& $py` 替换为 `"$py"`：

```bash
"$py" -m pytest examples/skills_code_review_agent/tests/unit -q -p no:cacheprovider
"$py" -m pytest examples/skills_code_review_agent/tests/integration -q -p no:cacheprovider
"$py" -m pytest examples/skills_code_review_agent/tests/e2e -q -p no:cacheprovider
"$py" -m pytest examples/skills_code_review_agent/tests -q -m "not container and not real_llm" -p no:cacheprovider
"$py" -m pytest examples/skills_code_review_agent/tests/integration -q -m container -p no:cacheprovider
"$py" -m pytest examples/skills_code_review_agent/tests/integration -q -m real_llm -p no:cacheprovider
"$py" examples/skills_code_review_agent/evaluate.py --sandbox local --output-dir out/eval_local
"$py" examples/skills_code_review_agent/evaluate.py --sandbox container --output-dir out/eval_container
"$py" -m flake8 examples/skills_code_review_agent
```

## 7. 常见故障

| 现象 | 原因与处理 |
|---|---|
| PowerShell 出现 `UnexpectedToken PS` 或 `>>` 重定向错误 | 复制了终端提示符或上一条未结束的续行符；按 `Ctrl+C` 返回正常提示符后，只粘贴代码块。 |
| `container_runtime_unavailable` | Docker Desktop 未运行或 daemon 不可达；先运行本手册第 0 节检查。 |
| real 模型只出现 warning | `.env` 缺任一白名单变量、变量为空，或误加了 `--dry-run`；不要把 Key 打印到终端。 |
| 找不到报告 | 读取成功 JSON 的 `report_files.json` / `report_files.markdown`；不要猜默认目录。 |
| `--sandbox cube` 失败 | 当前示例未接入 Cube/E2B runtime factory，命令会以配置错误（退出码 2）结束；即使后续接入也必须提供机器可验证的网络隔离证明。请使用 `container` 或显式 `local`。 |
| 想让 sandbox 运行目标仓库测试 | 这是 `DEV_SPEC.md` 第 7 章未来工作；当前 CLI 故意拒绝 `--run-tests`。 |
