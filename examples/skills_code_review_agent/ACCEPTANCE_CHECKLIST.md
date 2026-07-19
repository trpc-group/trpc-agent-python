# Acceptance Checklist

## 标准 1：8 条公开样本必须全部可运行并生成报告

- 已覆盖 8 条 fixture：
  - `clean.diff`
  - `security_issue.diff`
  - `async_resource_leak.diff`
  - `db_lifecycle_issue.diff`
  - `missing_tests.diff`
  - `duplicate_finding.diff`
  - `sandbox_failure.diff`
  - `secret_redaction.diff`
- 已有集成测试和 CLI 路径生成 `review_report.json` 与 `review_report.md`
- Phase 6 额外验证：
  - `fixture_runs_ok=8`
  - 新增质量门禁测试 `test_all_public_fixtures_generate_reports`

## 标准 2：隐藏样本高危问题检出率 >= 80%，误报率 <= 15%

- 当前实现以高信号确定性规则优先：
  - `eval`
  - `exec`
  - `pickle.loads`
  - `yaml.load`
  - `shell=True`
  - secret patterns
- 低置信项自动降级为 `needs_human_review` 或 `warning`
- 当前示例给出工程策略和测试基线，但隐藏样本上的最终指标仍需 PR 前人工复核说明

## 标准 3：数据库完整记录 task、sandbox run、finding 和 report

- SQLite 已持久化：
  - `review_tasks`
  - `review_inputs`
  - `filter_decisions`
  - `sandbox_runs`
  - `findings`
  - `review_reports`
- 已支持 `get_review_bundle(task_id)` 查询完整链路

## 标准 4：沙箱具备超时和输出限制，失败不崩

- 脚本执行层有 timeout
- stdout/stderr 有统一截断上限
- sandbox failure / timeout 转换为结构化记录和 finding
- 已有 `sandbox_failure.diff` 测试

## 标准 5：敏感信息脱敏检出率 >= 95%

- 报告和数据库前统一调用 `redactor.py`
- 覆盖：
  - API key
  - token
  - password
  - bearer token
  - private key
- 已有 `secret_redaction.diff` 集成测试

## 标准 6：dry-run / fake model 模式 <= 2 分钟

- 主链路不依赖真实模型
- 规则和脚本执行均为轻量 deterministic 路径
- 当前测试集运行时间远低于 2 分钟
- Phase 6 单次 security fixture dry-run 实测约 `9.87s`

## 标准 7：高风险脚本必须先经过 Filter 决策

- 所有 skill 脚本执行前统一经过 `filter_policy.py`
- `deny / needs_human_review` 不直接进入执行
- 已测试 forbidden path 拦截

## 标准 8：报告必须包含关键信息

- 当前报告包含：
  - findings
  - severity stats
  - human review items
  - filter summary
  - sandbox summary
  - monitoring summary
  - actionable recommendations

## PR 前仍需复核

- README 与最终示例输出是否同步
- 设计说明是否满足 300-500 字要求
- 是否需要再补一轮原生 `skill_run` 接入说明
- 是否需要附上最终 sample outputs 供 reviewer 直接查看
