# 评测 + 优化流水线（Evaluation + Optimization Pipeline）
#
# 本包实现了完整的 6 阶段自动闭环流水线：
#   1. _stage_baseline.py            — 基线评测
#   2. _stage_failure_attribution.py — 失败归因
#   3. _stage_optimization.py        — 优化执行
#   4. _stage_validation.py          — 候选验证
#   5. _stage_acceptance_gate.py     — 接受门控
#   6. _stage_audit_trail.py         — 审计轨迹
#
# _models.py  — 所有阶段的 Pydantic 数据模型
# _runner.py  — PipelineRunner 编排器
