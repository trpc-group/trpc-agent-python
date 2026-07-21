# 设计边界

本示例把运行环境与替代组件分开描述。`offline`、`real`、`trace` 是三种 Pipeline 运行模式。`offline` 仍使用 SDK 的 `LlmAgent`、Runner 和独立 Session，只把 Agent 内部模型替换成 `DeterministicFakeModel`，并用确定性 Candidate Provider 代替真实优化器。它用于验证 Prompt 改变是否经过真实 Agent 编排影响输出。

`real` 使用真实业务模型生成回复，并由 `AgentOptimizer` 调用真实反思模型产生 Prompt 候选；只有 Gate 接受、源 Prompt 哈希未漂移且显式启用写回时，才允许更新源文件。

`trace` 直接评测预录制的 `actual_conversation`，不再次运行 Agent、Model 或 Candidate Provider。它适合复现工具轨迹和生产故障，但只能证明候选版本与轨迹的关联，不能证明 Prompt 导致了该轨迹。因此 Trace 即使获得 ACCEPT，也固定跳过源 Prompt 写回。

确定性 metric 负责精确匹配等硬规则。LLM Judge 若需要，应作为带 rubric 的评测指标显式配置；本示例不提供容易混淆职责的 `use_fake_judge` 开关。
