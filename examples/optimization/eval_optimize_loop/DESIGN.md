# 设计说明

## 失败归因
评测先由 FakeJudge 产出结构化失败，再用规则归因：JSON、禁用格式归为 `format_violation`，精确答案错归为 `final_response_mismatch`，工具名、参数、知识召回、长度和 rubric 各有独立类别。每个失败都保留 reason 与 evidence，便于复核。若样例声明期望类别，报告会计算归因准确率。

## 接受门禁
Gate 要求验证集提升达到阈值，并检查新硬失败、受保护样例退化、单例降分和累计成本。若训练分上涨但验证不涨，直接标记过拟合并拒绝。

## 防过拟合
训练集只用于暴露问题，候选必须通过验证集和 protected case。过拟合候选即使修好训练格式，只要把验证集自然语言或受保护精确答案改坏，也会被拒绝。`delta_type` 标出 new_pass、new_fail、score_up、score_down，避免只看平均分掩盖局部退化。

## Fake 与 SDK
Fake mode 由 expectation、tags、protected 和 simulated_outputs 驱动，不依赖样例 id，可无 API key 稳定复现。SDK mode 通过 `SDKBackend` 调用 `AgentOptimizer` 与 `TargetPrompt`，失败时给出明确错误，不回退到 fake。

## 审计与回写
报告同时写 JSON、Markdown 和 `runs/<run_id>/`，保存输入哈希、配置快照、候选 prompt、diff、case 结果与成本，并给出可复现命令。默认不回写源 prompt，只有显式 `--update-source` 才允许，报告会记录该选择。
