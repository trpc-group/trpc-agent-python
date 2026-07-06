# 优化报告 — 场景 `no_effect`

> 结论：❌ **拒绝候选 prompt**
> 理由：闸门 min_val_improvement 未通过：验证集通过率提升 +0.0000（要求 ≥ 1e-09），平均分提升 +0.0000（要求 ≥ 0） —— 提升不足，不值得接受

- 生成时间：2026-07-06T15:49:26.626079+00:00　随机种子：42　报告 schema：v1
- 优化算法：gepa_reflective（status=SUCCEEDED，4 轮，接受 0 轮，耗时 0.17s）
- 审计产物目录：`optimize/`（每轮候选 prompt、评测结果、接受理由、成本、seed 快照均在其中）

## 一、baseline vs candidate 概览

| 切分 | baseline 通过率 | candidate 通过率 | baseline 平均分 | candidate 平均分 | 通过率 Δ |
| --- | --- | --- | --- | --- | --- |
| train | 33.3% (1/3) | 33.3% (1/3) | 0.500 | 0.500 | +0.000 |
| val | 33.3% (1/3) | 33.3% (1/3) | 0.500 | 0.500 | +0.000 |

## 二、baseline 失败归因统计

| 失败类型 | 中文说明 | 涉及 case 数 |
| --- | --- | --- |
| `wrong_tool_call` | 工具调用错误 | 2 |
| `wrong_tool_args` | 工具参数错误 | 2 |
| `knowledge_recall_miss` | 知识召回不足 | 2 |
| `format_violation` | 格式不符合要求 | 2 |
| `llm_rubric_fail` | LLM rubric 不达标 | 4 |
| `final_answer_mismatch` | 最终回复不匹配 | 2 |

主要归因（每个失败 case 的根因）：
- `train_convert_3km` → `wrong_tool_args`（工具参数错误）
- `train_intro_shenzhen` → `wrong_tool_call`（工具调用错误）
- `val_convert_5km` → `wrong_tool_args`（工具参数错误）
- `val_intro_hangzhou` → `wrong_tool_call`（工具调用错误）

## 三、逐 case delta（验证集为准，训练集附后）

### val

| case | baseline | candidate | 分数变化 | 判定 |
| --- | --- | --- | --- | --- |
| `val_convert_5km` | ❌ 0.375 | ❌ 0.375 | +0.000 | 无变化（`unchanged`） |
| `val_identity` | ✅ 1.000 | ✅ 1.000 | +0.000 | 无变化（`unchanged`） |
| `val_intro_hangzhou` | ❌ 0.125 | ❌ 0.125 | +0.000 | 无变化（`unchanged`） |

### train

| case | baseline | candidate | 分数变化 | 判定 |
| --- | --- | --- | --- | --- |
| `train_convert_3km` | ❌ 0.375 | ❌ 0.375 | +0.000 | 无变化（`unchanged`） |
| `train_identity` | ✅ 1.000 | ✅ 1.000 | +0.000 | 无变化（`unchanged`） |
| `train_intro_shenzhen` | ❌ 0.125 | ❌ 0.125 | +0.000 | 无变化（`unchanged`） |

## 四、优化过程（优化器视角）

- 优化器内部验证集通过率：33.3% → 33.3%（注意：优化器只看 optimizer.json 里的弱指标；overfit 场景中它看到的还是泄漏调参集 —— 是否真的变好以上面的独立验证集复评为准）
- 成本：$0.0000，反思 LM 调用 4 次，metric 调用 27/60

## 五、gate 决策明细

| 闸门 | 结果 | 说明 |
| --- | --- | --- |
| `min_val_improvement` | ❌ | 验证集通过率提升 +0.0000（要求 ≥ 1e-09），平均分提升 +0.0000（要求 ≥ 0） —— 提升不足，不值得接受 |
| `no_new_hard_fail` | ✅ | 验证集无新增失败 case |
| `protected_cases` | ✅ | 保护 case（val_identity）均未退化 |
| `overfit_guard` | ✅ | 未触发过拟合守卫（train +0.0000 / val +0.0000） |
| `cost_budget` | ✅ | 优化成本 $0.0000（预算 $1） |
| `duration_budget` | ✅ | pipeline 耗时 0.3s（预算 180s） |

## 六、是否值得接受

候选 prompt 未能通过接受策略：闸门 min_val_improvement 未通过：验证集通过率提升 +0.0000（要求 ≥ 1e-09），平均分提升 +0.0000（要求 ≥ 0） —— 提升不足，不值得接受 **建议拒绝**，保持 baseline prompt 不变；可根据上面的失败归因调整评测集或优化配置后重试。
