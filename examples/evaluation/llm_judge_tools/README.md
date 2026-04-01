# 为裁判 Agent 注册工具示例（Rubric 指标）

通过 **LLM_EVALUATOR_REGISTRY** 为 LLM Judge 的裁判 Agent 注册工具，并在 **rubric 指标**（`llm_rubric_response`）的细则中**规定工具的调用时机和用法**，使裁判在打分时必须先调用工具获取判定标准，再按标准条款判定。

## 原理

- 本示例使用 **llm_rubric_response** 指标（而非 llm_final_response），通过多条 Rubric 约束裁判行为。
- 在 `test_config.json` 的 **rubrics** 中明确写出：
  - **调用时机**：裁判在打分前必须先调用 `get_eval_policy` 获取本用例的判定标准。
  - **用法**：仅根据该工具返回的标准条款进行判定，不得自行增加或减少条款；后续细则要求「根据 get_eval_policy 返回的标准第 N 条判定」。
- 裁判由框架内的 **LlmAgent** 担任；通过 `LLM_EVALUATOR_REGISTRY.register_judge_tools("llm_rubric_response", [FunctionTool(get_eval_policy)])` 为裁判注入 **get_eval_policy** 工具。裁判模型在推理时会看到 rubrics，因此会先调用工具再按条款打分，工具效果明显。

## 目录结构

- `agent/`：被评测的 Agent、评测集 `judge_tools.evalset.json`、`test_config.json`（含 llm_rubric_response 与规定工具用法的 rubrics）
- `test_llm_judge_tools.py`：注册 judge 工具 `get_eval_policy` 并执行评测

## 示例代码

在运行评测前为 **llm_rubric_response** 注册工具：

```python
from trpc_agent_sdk.evaluation import LLM_EVALUATOR_REGISTRY
from trpc_agent_sdk.tools import FunctionTool

def get_eval_policy() -> str:
    """裁判在打分前必须调用：返回本用例的判定标准。"""
    return (
        "本用例判定标准（共 3 条）：\n"
        "1. 最终回答须包含明确的温度数值。\n"
        "2. 最终回答须包含天气状况描述。\n"
        "3. 回答须与用户问题直接相关。"
    )

LLM_EVALUATOR_REGISTRY.register_judge_tools(
    "llm_rubric_response",
    [FunctionTool(get_eval_policy)],
)
```

在 `test_config.json` 的 rubrics 中规定调用时机与用法（见 `agent/test_config.json`）：

- 第一条 rubric：裁判在打分前必须先调用 `get_eval_policy`，再仅根据返回的条款判定。
- 其余 rubrics：根据 `get_eval_policy` 返回的标准第 1、2、3 条逐条判定。

取消注册：

```python
LLM_EVALUATOR_REGISTRY.unregister_judge_tools("llm_rubric_response")
```

## 环境变量

- `TRPC_AGENT_API_KEY` 或 `API_KEY`（必填，Agent 与裁判模型共用）
- `TRPC_AGENT_BASE_URL`（可选）
- `TRPC_AGENT_MODEL_NAME`（可选）

## 运行

```bash
cd examples/evaluation/llm_judge_tools
pytest test_llm_judge_tools.py -v --tb=short -s
```
