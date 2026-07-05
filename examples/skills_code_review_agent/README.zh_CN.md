# 代码评审 Agent（Skills + 沙箱 + 数据库）

基于 tRPC-Agent 的 Skills、沙箱执行与数据库存储能力构建的自动代码评审 Agent（issue #92）。
输入一个 diff 或仓库路径，它会识别问题、产出结构化 findings、落库，并生成
`review_report.json` 与 `review_report.md`。

## 快速开始（无需 API Key）

```bash
pip install -r requirements.txt

# 评审内置样本（无需模型）。默认运行时是沙箱
# （auto → 有 Docker 走容器，否则本地子进程沙箱）：
python run_review.py --fixture security.diff --out-dir /tmp/cr

# 评审你自己的 diff、工作区，或指定文件列表：
python run_review.py --diff-file my.diff
python run_review.py --repo-path /path/to/repo --no-db
python run_review.py --files pipeline/engine.py,pipeline/scanners.py

# 在带标注的样本上打分自测（检出率 / 误报率）：
python selftest.py

# 走 LlmAgent + fake 模型（无需 API key）：
python run_agent.py --fixture security.diff --dry-run
```

样本报告见 [`sample_output/`](./sample_output/)；规则清单见
[`../../skills/code-review/docs/RULES.md`](../../skills/code-review/docs/RULES.md)，设计说明见 [DESIGN.md](./DESIGN.md)。

## 工作原理

findings 来自**确定性静态扫描器**而非 LLM，因此结果可复现、验收阈值可调：

`diff/repo → diff_parser(unidiff) → scanners(bandit/ruff/detect-secrets/semgrep)
→ 去重降噪 → 脱敏 → 报告(json+md) / 落库(SqlStorage，默认 SQLite，可切 PG/MySQL)`

| 类别 | 扫描器 |
|---|---|
| security | bandit, semgrep |
| secret_leakage | detect-secrets |
| async_errors | ruff（ASYNC）|
| resource_leak | ruff（SIM115 / bugbear）|
| db_lifecycle | semgrep（`skills/code-review/rules/db_lifecycle.yaml`）|

## 设计要点

主干是**确定性流水线**；Agent（Skills+沙箱+Filter）只是两个 finding 来源之一，而非总指挥——
这是 dry-run 无 Key 要求逼出来的（扫描器路径必须能独立出完整报告），也消除了最大风险：
LLM 来源的 findings 无法在隐藏集上复现阈值，而扫描器输出稳定。

**Skill**：`skills/code-review/` 把评审打包为可移植 Skill（SKILL.md + 脚本 + semgrep 规则），
可在沙箱中独立运行并按 `docs/OUTPUT_SCHEMA.md` 输出。**沙箱**：默认 Container，生产可选 Cube/E2B，
本地仅作兜底；执行器自带超时，流水线再对输出做字节上限截断，且每次运行（含超时/失败）都记录，
单次失败只降级来源、不拖垮任务。**Filter**：工具级 `BaseFilter` 在进沙箱前拦截高危脚本/禁止路径/
非白名单网络/超预算，拦截原因写入报告与库。**监控**：每次评审的耗时、工具调用数、拦截数、
finding 数、严重级分布、异常类型分布经 OpenTelemetry 上报并写入报告。**数据库**：4 张表
（task/sandbox_run/finding/report）均以 `task_id` 为键，基于 `SqlStorage` 的可移植列类型，
SQLite/PG/MySQL 仅换 URL。**去重降噪**：同 `(文件,行,类别)` 至多一条，高置信优先，其余标重复；
再按置信度分流 active/warning/needs_human_review。**安全边界**：单一 `redact()` 汇聚点，
入库/出报告前统一脱敏，绝不散落。

## 状态

已实现：确定性流水线、数据库落库、8 个样本、打分自测、CLI、基线脱敏。
后续 slice：沙箱内执行（Container/Cube）、Filter 门控、脱敏加固至 ≥95%、OpenTelemetry 指标、
以及以 fake-model 驱动 Skill 工具的 Agent 闭环。
