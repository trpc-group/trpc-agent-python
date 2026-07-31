# 自动代码评审 Agent

本目录实现了一个基于 tRPC-Agent-Python 的 Skill 体系、数据库持久化和 Filter 治理策略的自动代码评审 (CR) Agent 原型。

## 设计方案说明

### 1. Skill 设计
代码评审 Agent 在 `skills/code-review/` 目录下定义了一个专属 Skill。它通过 `SKILL.md` 声明输入输出、规则和命令，并在 `scripts/` 目录下放置沙箱执行脚本：
- `parse_diff.py`：负责将原始 unified diff 或 PR 补丁解析为结构化的修改行与上下文信息。
- `run_checks.py`：在沙箱环境中对 diff 内容执行静态分析。支持高精度 AST 分析，并在语法不完整时回退为正则行匹配。

### 2. 沙箱隔离策略
沙箱用于运行静态分析和检查脚本。系统支持 Docker 容器运行时 (`ContainerWorkspaceRuntime`) 作为默认的生产隔离环境，并支持本地工作区运行时 (`LocalWorkspaceRuntime`) 作为开发与测试的 Fallback 方案。沙箱执行受限于超时、内存限制和文件配额。

### 3. Filter 策略与安全边界
前置拦截器 `FilterGovernance` 在脚本和命令进入沙箱执行前进行安全校验：
- **高危脚本拦截**：使用黑名单拦截 `rm -rf`、`curl` 等危险命令。针对 `nc` 等短指令，引入了正则单词边界（`\b`）判定，避免误杀 `async` 等包含相关字母的合法变量。
- **路径与预算检查**：限制对敏感路径（如 `/etc`，`C:\Windows`）的访问。
- **敏感信息脱敏**：在检查阶段，通过正则匹配明文 API Key、密码、Token 等敏感信息，在写入 findings 证据、最终报告和数据库时统一替换为 `[REDACTED]`。

### 4. 去重和降噪
针对同一文件、同一行、同一类别的 findings 采用去重合并逻辑。同时，基于 `confidence` 字段将问题分层：高置信度问题进入 findings 栏目，低置信度问题则隔离至 `needs_human_review` (warnings)，避免噪音干扰。

### 5. 数据库 Schema 设计
基于 SQLAlchemy 模型在 SQLite 上实现了持久化（可无缝切换至 MySQL/Postgres）：
- `review_tasks`：保存评审任务状态及 diff 摘要。
- `sandbox_runs`：记录沙箱脚本的执行状态、耗时、标准输出与标准错误。
- `findings`：保存结构化 findings。
- `review_reports`：存储最终生成的 JSON 及 Markdown 评审报告。
- `filter_logs`：保存 Filter 的拦截决策历史。

### 6. 监控审计
每次 review 均自动审计并记录：总耗时、沙箱执行耗时、工具调用次数、拦截次数、findings 数量、各项严重级别分布以及异常类型分布。

### 7. 本地化拔高亮点 (Top-3 核心特性)
- **高精度 AST 语法树检查**：对于工作区存在的 Python 文件，使用 Python 内置 `ast` 模块进行节点级检测，检测更加精准，并配备了面向 diff 行匹配的鲁棒回退机制。
- **Git Pre-commit 钩子一键集成**：提供内置的 pre-commit 钩子脚本 ([pre_commit_hook.sh](file:///d:/my_document/project/others/trpc-agent-python/examples/skills_code_review_agent/pre_commit_hook.sh))，可以自动在本地 `git commit` 时拦截包含 `CRITICAL` 或 `HIGH` 严重级别漏洞的提交。
- **Rich 炫酷终端面板与编码自适应**：CLI 自动渲染炫酷的控制台表格，并在 Windows non-UTF8 环境下优雅回退至 ASCII 文本表格以防止乱码。

---

## 快速开始

### 1. 运行代码评审 Agent
指定 diff 文件路径运行自动评审：
```bash
python -m examples.skills_code_review_agent.agent --diff-file examples/skills_code_review_agent/fixtures/fixture_security.diff
```

### 2. 运行自动化测试
使用 pytest 执行全部 8 条测试样例：
```bash
$env:PYTHONPATH="d:\my_document\project\others\trpc-agent-python"; pytest examples/skills_code_review_agent/test_agent.py
```
