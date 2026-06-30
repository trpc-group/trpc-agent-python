# Optimization Report

## 人话总结

本次（fake_trace 模式）决定**拒绝**候选 prompt。训练集均分 0.25→0.7833（+0.5333），验证集 0.7333→0.6667（-0.0666）。训练涨但验证跌，呈现过拟合特征。验证集新增通过：val_search_fallback_new_pass。⚠️ 验证集新增失败：val_smalltalk_no_tool_regression、val_weather_soft_degradation。被以下 gate 拦截：validation_gain_threshold、no_new_hard_fail、no_critical_regression、not_overfit_train_up_val_down。baseline 失败归因：final_response_mismatch×4、tool_call_error×2、format_error×1、knowledge_recall_insufficient×1。

- Mode: `fake_trace`
- Decision: **REJECT**
- Reason: validation_gain_threshold; no_new_hard_fail; no_critical_regression; not_overfit_train_up_val_down
- Baseline train score: 0.25
- Candidate train score: 0.7833
- Baseline val score: 0.7333
- Candidate val score: 0.6667
- Train gain: +0.5333
- Val gain: -0.0666

## Failure Attribution

- final_response_mismatch: 4
- tool_call_error: 2
- format_error: 1
- knowledge_recall_insufficient: 1

## Validation Delta

- `val_search_fallback_new_pass`: new_pass (0.2 -> 1.0, delta +0.8000)
- `val_smalltalk_no_tool_regression`: new_fail (1.0 -> 0.45, delta -0.5500)
- `val_weather_soft_degradation`: new_fail (1.0 -> 0.55, delta -0.4500)

## Gate Checks

- FAIL `validation_gain_threshold`: val_gain=-0.0666, required>=+0.1000
- FAIL `no_new_hard_fail`: new_hard_fails=['val_smalltalk_no_tool_regression', 'val_weather_soft_degradation']
- FAIL `no_critical_regression`: critical_regressions=['val_smalltalk_no_tool_regression', 'val_weather_soft_degradation']
- FAIL `not_overfit_train_up_val_down`: train_gain=+0.5333, val_gain=-0.0666
- PASS `cost_budget`: cost_usd=0.0000, budget=0.0100
