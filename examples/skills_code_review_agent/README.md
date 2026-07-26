# 自动代码评审 Agent

这是一个基于 tRPC-Agent 原生 `LlmAgent`、`SkillToolSet`、Workspace Runtime、Filter 和 SQLite 的代码评审示例。输入可以是 unified diff、Git 工作区或文件列表；输出为结构化 findings、JSON/Markdown 报告和按 task id 回读的审计记录。

## Agent 编排

真实模型路径采用“CLI 预处理 → Agent 决策 → 受控执行 → 结果融合”的编排：

```text
CLI
 ├─ 解析 --diff-file / --repo-path / --files
 ├─ 脱敏 diff、hunk、候选变更行
 ├─ 生成 task_id 与 workspace 输入
 └─ Runner + InMemorySessionService
       └─ LlmAgent
            ├─ SkillToolSet
            │    ├─ skill_load
            │    ├─ skill_list_docs
            │    └─ skill_select_docs
            ├─ FunctionTool: run_selected_review_actions
            │    └─ 原生 SkillRunTool → Workspace Runtime
            └─ FunctionTool: save_review_report
                 ├─ 规则融合、校验、去重、降噪
                 └─ JSON / Markdown / SQLite
```

1. CLI 在模型调用前解析并脱敏输入；模型不会获取宿主机绝对路径。
2. `SkillToolSet(require_skill_loaded=True)` 要求先加载 Skill。模型可按语言选择参考文档，避免一次性加载全部资料。
3. 模型不能直接调用 shell 或 `skill_run`，只能调用 `run_selected_review_actions(action_ids=[...])`。该 FunctionTool 将 action id 展开为确定性白名单命令，再委托仓库原生 `SkillRunTool`。
4. 当前受控执行器实现的 action 是 `analyze_pr`，对应 `scripts/pr-analyzer.py`。`ruff`、`pytest` 仍受白名单和 Filter 治理，但尚未作为独立 action id 暴露给模型。
5. `CodeReviewSkillRunFilter` 在进入 Runtime 前检查命令、网络/依赖安装、禁止路径、超时和 stdin；`deny` 与 `needs_human_review` 不执行。
6. `save_review_report` 融合确定性规则和模型 findings，校验 file/line 与 evidence，完成去重、降噪和持久化。

| 模式 | 参数 | 模型 API | SDK Agent / Runtime | 用途 |
|---|---|---:|---:|---|
| dry-run | `--dry-run` | 不需要 | 不启动 | 解析、规则、脱敏、报告、SQLite 的快速验证。 |
| FakeModel | `--fake-model` | 不需要 | 启动 | 验证 `Runner → LlmAgent → FunctionTool → 报告`。 |
| 真实模型 | 默认 | 需要 | 启动 | 使用 OpenAI 兼容模型进行 Skill 驱动评审。 |

`--dry-run` 和 `--fake-model` 互斥。

## 目录

```text
agent/
  agent.py     # LlmAgent 工厂、OpenAI 兼容模型、FakeModel
  tools.py     # SkillToolSet、输入 staging、受控 action、报告生成
  filter.py    # Agent/SkillRun 审计与 SDK Filter
  policy.py    # 命令、网络、路径、超时策略
  parser.py    # unified diff、hunk 与候选行解析
  rules.py     # 敏感信息、异步、数据库、测试缺失规则
  redactor.py  # API key/token/password 脱敏
  storage.py   # SQLite 审计存储与 task 回读
skills/code-review/
  SKILL.md、reference/、scripts/pr-analyzer.py
run_review.py  # CLI 入口
```

## Skill 来源与许可证

`skills/code-review/` 基于上游 [awesome-skills/code-review-skill](https://github.com/awesome-skills/code-review-skill) 引入。本示例在其 `SKILL.md` 中补充了面向自动化 Agent 的 `review-agent-plan`，并在示例侧实现了输入 staging、受控 Skill 执行、Filter 治理、审计存储和结构化报告。

上游 Skill 的许可证保留在 `skills/code-review/LICENSE`；修改或精简上游文件时，必须继续保留该许可证和上游来源说明。

## 前置条件

在仓库根目录安装项目依赖并激活虚拟环境。Linux 通常使用 `python3`，Windows 通常使用 `python`。

### Docker 镜像

Docker Runtime 需要带 Python、Ruff、pytest 的镜像。首次建议手动构建一次，后续复用缓存。

PowerShell：

```powershell
docker build -t trpc-code-review:latest -f .\examples\skills_code_review_agent\Dockerfile .\examples\skills_code_review_agent
```

Bash：

```bash
docker build -t trpc-code-review:latest -f examples/skills_code_review_agent/Dockerfile examples/skills_code_review_agent
```

### `.env`

本示例与仓库其他示例一样，提供受版本控制的 `examples/skills_code_review_agent/.env` 占位模板。先将其中所有 `your-xxx` 值替换为本地配置，或通过 shell 环境变量覆盖；该文件不得写入真实密钥。若密钥曾进入 Git 历史，应立即在服务商侧轮换。

```dotenv
# 真实模型；--fake-model 和 --dry-run 不需要 API Key。
OPENAI_API_KEY=your-api-key
OPENAI_BASE_URL=your-base-url
OPENAI_MODEL=your-model-name

# 填写非默认镜像/构建上下文；保留 your-xxx 时自动使用示例默认值。
CODE_REVIEW_DOCKER_IMAGE=your-code-review-docker-image
CODE_REVIEW_DOCKER_BUILD_CONTEXT=your-code-review-docker-build-context

# 单位 KiB；MODEL_RUN_MAX_COUNT 为条数。
CODE_REVIEW_TOOL_OUTPUT_MAX_KIB=your-tool-output-max-kib
CODE_REVIEW_MODEL_AUDIT_MAX_KIB=your-model-audit-max-kib
CODE_REVIEW_MODEL_RUN_MAX_COUNT=your-model-run-max-count

# Cube/E2B 预留配置。
CUBE_TEMPLATE_ID=your-cube-template-id
E2B_API_URL=your-e2b-api-url
E2B_API_KEY=your-e2b-api-key
```

## 运行命令

以下命令都从仓库根目录执行。默认输出为：

```text
examples/skills_code_review_agent/review-output/<task_id>/
```

每次运行生成新 task id，例如 `cr-20260725-153640-hardcoded-token-13a11c26`。可用 `--output-dir` 覆盖输出目录。

### PowerShell：dry-run

```powershell
# 评审 unified diff
python .\examples\skills_code_review_agent\run_review.py --diff-file .\examples\skills_code_review_agent\tests\fixtures\02_hardcoded_token\input.diff --dry-run

# 评审当前 Git 工作区变更
python .\examples\skills_code_review_agent\run_review.py --repo-path . --dry-run

# 评审文件列表（会构造成 diff）
python .\examples\skills_code_review_agent\run_review.py --files .\src\example.py --dry-run

# 自定义输出目录
python .\examples\skills_code_review_agent\run_review.py --diff-file .\examples\skills_code_review_agent\tests\fixtures\02_hardcoded_token\input.diff --dry-run --output-dir .\review-output
```

### PowerShell：FakeModel

```powershell
# Docker 中验证 SDK Agent 链路，无需 API Key
python .\examples\skills_code_review_agent\run_review.py --diff-file .\examples\skills_code_review_agent\tests\fixtures\02_hardcoded_token\input.diff --runtime docker --fake-model

# Docker 中评审当前 Git 工作区
python .\examples\skills_code_review_agent\run_review.py --repo-path . --runtime docker --fake-model

# local Runtime 仅用于开发调试，不是生产默认方案
python .\examples\skills_code_review_agent\run_review.py --diff-file .\examples\skills_code_review_agent\tests\fixtures\02_hardcoded_token\input.diff --runtime local --fake-model
```

### PowerShell：真实模型 + Docker

```powershell
# 评审 patch
python .\examples\skills_code_review_agent\run_review.py --diff-file .\examples\skills_code_review_agent\tests\fixtures\02_hardcoded_token\input.diff --runtime docker

# 评审当前 Git 工作区；会 stage diff 与变更源码
python .\examples\skills_code_review_agent\run_review.py --repo-path . --runtime docker

# 指定模型地址、模型名和输出目录
python .\examples\skills_code_review_agent\run_review.py --diff-file .\examples\skills_code_review_agent\tests\fixtures\02_hardcoded_token\input.diff --runtime docker --model-base-url https://your-openai-compatible-endpoint/v1 --model your-model-name --output-dir .\review-output-real

# 从指定环境变量读取 API Key
python .\examples\skills_code_review_agent\run_review.py --repo-path . --runtime docker --model-api-key-env DEEPSEEK_API_KEY
```

### Bash：dry-run、FakeModel、真实 Docker

```bash
# dry-run
python3 examples/skills_code_review_agent/run_review.py \
  --diff-file examples/skills_code_review_agent/tests/fixtures/02_hardcoded_token/input.diff \
  --dry-run

# FakeModel + Docker
python3 examples/skills_code_review_agent/run_review.py \
  --diff-file examples/skills_code_review_agent/tests/fixtures/02_hardcoded_token/input.diff \
  --runtime docker \
  --fake-model

# 真实模型 + Docker，评审当前工作区
python3 examples/skills_code_review_agent/run_review.py \
  --repo-path . \
  --runtime docker \
  --output-dir ./review-output-real
```

若 Bash 环境中的 `python` 已指向 Python 3，可替换 `python3`。PowerShell 多行命令使用反引号 `` ` ``，不能使用 Bash 的反斜杠 `\`。

### Cube / E2B

```bash
# Cube + FakeModel
python3 examples/skills_code_review_agent/run_review.py --repo-path . --runtime cube --fake-model

# Cube + 真实模型
python3 examples/skills_code_review_agent/run_review.py --repo-path . --runtime cube
```

CLI 接受 `--runtime cube` 与 `--runtime e2b`。当前异步远程 Runtime 实际通过 SDK Cube workspace 创建；`e2b` 仅保留了 CLI 名称和环境变量，尚未实现独立 E2B client 初始化。因此当前可验证的隔离 Runtime 是 Docker 和 Cube。

## 输入、Sandbox 与 Filter

- `--diff-file`：stage 脱敏后的 `input.diff`。没有完整源码时，源码级 Ruff 检查会转入 `needs_human_review`，不会执行。
- `--repo-path`：运行 `git -C <repo> diff --no-ext-diff --`，stage 脱敏 diff 和 diff 中的变更源码；适合 Docker/Cube 实际检查。
- `--files`：读取文件并构造 diff，适合简单开发验证。
- 宿主输入通过 `WorkspaceInputSpec` stage 至 `$WORK_DIR/work/inputs/`；Skill stage 至 workspace 的 `skills/code-review/`。
- Filter 仅允许 `python`、`ruff`、`pytest`，拒绝 `curl`、`wget`、`pip install`、`npm install`、敏感路径、超时任务和 stdin；原因写入报告与 SQLite。
- Docker 是默认生产 Runtime；`local` 仅是开发 fallback。Docker 镜像和远程模板应在基础设施侧限制网络与权限。

## 输出、审计与安全

每个 task 目录包含：

```text
review_report.json  # 机器可读结果
review_report.md    # 人工可读摘要
```

输出根目录还包含 `reviews.sqlite`。`ReviewStorage.get_task(task_id)` 可读取 task、findings、Filter 决策、skill runs、model runs、监控指标和最终报告。

finding 字段为：

```text
severity, category, file, line, title, evidence,
recommendation, confidence, source
```

报告还含 `needs_human_review`、`skill_runs`、`model_runs`、`filter_decisions` 和以下指标：

```text
total_duration_seconds
sandbox_duration_seconds
tool_call_count
finding_count
needs_human_review_count
sandbox_run_count
blocked_count
severity_distribution
exception_distribution
skill_run_status_distribution
```

确定性规则覆盖敏感信息、异步资源、数据库连接/事务和测试缺失。模型 finding 必须满足：file/line 位于 diff、evidence 可追溯、同一 `category + file + line` 只保留一条；`confidence < 0.7` 转入 `needs_human_review`。

所有 diff、日志、finding、模型审计内容先脱敏。stdout/stderr 按 `CODE_REVIEW_TOOL_OUTPUT_MAX_KIB` 截断；模型审计按 `CODE_REVIEW_MODEL_AUDIT_MAX_KIB` 和 `CODE_REVIEW_MODEL_RUN_MAX_COUNT` 限制。报告和 SQLite 不应保存明文 API Key、token 或 password。

## 验证

PowerShell：

```powershell
python -m pytest tests\examples\test_code_review_agent.py -q
python -m compileall -q examples\skills_code_review_agent
git diff --check
```

Bash：

```bash
python3 -m pytest tests/examples/test_code_review_agent.py -q
python3 -m compileall -q examples/skills_code_review_agent
git diff --check
```

Windows 部分环境直接运行完整 SDK 路径可能受 `python-magic/libmagic` 原生 DLL 影响；此时优先使用 `--dry-run`，真实 Docker/FakeModel 验证建议在 Linux、WSL 或已配置 Docker 的环境运行。
