# Skills 代码评审 Agent

这个示例演示一个 examples 级 deterministic 自动代码评审 Agent：它会规范化代码评审输入，加载本地
`code-review` Skill 包，执行确定性规则，记录示例内治理和沙箱执行信息，将结果写入 SQLite，并输出
`review_report.json` 与 `review_report.md`。

该示例不调用真实模型 API，不要求 Docker 或 Cube 环境；默认 `dry-run` 可以在普通开发环境中跑通解析、规则、治理、落库和报告链路。

## 快速运行

运行前先进入示例目录：

```bash
cd examples/skills_code_review_agent
python run_review.py --fixture clean
python run_review.py --fixture secret --output-dir out-secret
```

也可以从仓库根目录运行测试：

```bash
python -m pytest examples/skills_code_review_agent/tests -q
```

## 输入模式

CLI 支持四种互斥输入：

- `--diff-file PATH`：读取 UTF-8 unified diff 或 patch 文件。
- `--repo-path PATH`：读取 Git 工作区变更，包括 tracked、staged、unstaged 和 untracked 文本文件。
- `--fixture NAME`：读取 `fixtures/<name>.diff`，也可以传显式 fixture 路径。
- `--file-list PATH`：读取 UTF-8 文件路径列表，每行一个文件；可读文本文件按 added-file 方式参与评审。

常用参数：

- `--output-dir PATH`：输出目录，默认 `output`。
- `--db-path PATH`：SQLite 路径；未显式设置时跟随 `--output-dir`，使用 `review.sqlite3`。
- `--runtime {dry-run,container,cube,local-dev}`：规则执行 runtime，默认 `dry-run`。
- `--allow-local`：允许无隔离的 `local-dev` runtime。
- `--timeout-sec SECONDS`：规则执行超时，默认 `30`。
- `--output-limit-bytes BYTES`：stdout/stderr 单流捕获上限，默认 `65536`。
- `--container-image IMAGE`：`container` runtime 使用的镜像，默认 `python:3-slim`。
- `--docker-base-url URL`：可选 Docker daemon 地址，不传时使用 Docker SDK 默认发现逻辑。

## Runtime 策略

- `dry-run`：默认路径，进程内执行规则脚本，不依赖外部服务。
- `local-dev`：本地 subprocess fallback，没有隔离能力，必须显式传入 `--allow-local`。
- `container`：通过 SDK Container workspace runtime 执行规则脚本，默认镜像为 `python:3-slim`，默认关闭网络；Docker 不可用时不会崩溃，而是记录为 `needs_human_review`。
- `cube`：通过 SDK Cube/E2B workspace runtime 执行规则脚本；需要环境变量 `CUBE_TEMPLATE_ID`、`E2B_API_URL`、`E2B_API_KEY`。缺少依赖、配置或后端不可达时记录为 `needs_human_review`。

所有 runtime 执行前都会经过治理策略，覆盖危险命令、禁止路径、网络命令、timeout、output limit 和环境变量白名单。
Container/Cube 执行时只上传 rule runner 所需的最小 Python bundle 和脱敏输入，不上传完整仓库。

## 输出文件

每次运行会写出以下文件：

- `parsed_input.json`：规范化后的 `InputSummary`，包含文件、hunk、上下文、候选行、诊断和 diff 摘要。
- `skill_manifest.json`：本地 Skill manifest，包含规则文档、脚本路径和 digest。
- `rule_result.json`：规则脚本输出的结构化 findings 和 diagnostics。
- `findings.json`：规则 findings 的独立切分结果。
- `filter_events.json`：示例内治理决策。
- `sandbox_runs.json`：沙箱或 dry-run 执行状态、耗时、退出码、stdout/stderr 和截断标记。
- `review_report.json`：最终结构化报告。
- `review_report.md`：人工可读 Markdown 报告。
- `review.sqlite3`：SQLite 数据库，保存 task、input summary、finding、filter event、sandbox run、metrics 和 report。

`sample_outputs/review_report.json` 和 `sample_outputs/review_report.md` 是基于 `secret` fixture 整理的脱敏样例输出，路径相对于本示例目录。

## SQLite 查询示例

```bash
sqlite3 output/review.sqlite3 \
  "select id, status, summary from review_task order by created_at desc limit 1;"
sqlite3 output/review.sqlite3 \
  "select route, severity, category, file, line, title from finding;"
sqlite3 output/review.sqlite3 \
  "select runtime, status, exit_code, error_type from sandbox_run;"
sqlite3 output/review.sqlite3 \
  "select finding_count, warning_count, needs_human_review_count from review_metrics;"
sqlite3 output/review.sqlite3 \
  "select json_path, md_path from report;"
```

`agent/store.py` 默认使用 SQLite，同时暴露最小 store protocol 和 factory 注入点；因此示例 CLI 不变，
但 pipeline 可以替换为其他 SQL 后端实现。

## 公开 Fixture

示例包含 8 条公开 diff 样本：

| Fixture | 覆盖内容 |
| --- | --- |
| `clean` | 源码和测试同步变更，无 findings、无 warnings |
| `security` | 动态执行和 `shell=True` 安全风险 |
| `async_leak` | async client 生命周期未闭合 |
| `db_lifecycle` | 数据库连接生命周期未闭合 |
| `missing_tests` | 低置信度缺测试 warning |
| `duplicate` | fingerprint 去重 |
| `sandbox_failure` | sandbox timeout / failure 转人工复核 |
| `secret` | secret finding 和报告/数据库脱敏 |

这些 fixture 会在示例业务测试中端到端运行，覆盖解析、规则、沙箱、落库和报告链路。

## 质量门禁

示例测试中维护了一组公开 deterministic 质量门禁，用于防止规则和脱敏能力回退：

- 高危问题召回代理指标不低于 `80%`。
- 高置信误报代理指标不高于 `15%`。
- 常见 API Key、token、password、private key、连接串密码脱敏命中率不低于 `95%`。

这些指标是示例内公开样本的回归代理，不等同于对外部隐藏样本的完整质量保证。

## 设计文档

更详细的架构、数据流、安全边界、持久化和测试策略见 `DESIGN.md`。
