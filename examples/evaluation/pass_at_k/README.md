# pass@k / pass^k 示例

多轮运行后解析 (n, c)，计算 pass@1、pass@5、pass^2。目录与用法仿照 [quickstart](../quickstart/)。

## 目录结构

- `pass_at_k/`：示例根目录
- `agent/`：内含 `agent.py`、`weather_agent.evalset.json`、`config.py`、`test_config.json`（其中 **num_runs: 5**）
- `test_pass_at_k.py`：使用 get_executer、evaluate、get_result、parse_pass_nc、pass_at_k、pass_hat_k

## 环境要求

Python 3.10+。环境变量同 quickstart（`TRPC_AGENT_API_KEY` 等）。

## 运行

```bash
cd examples/evaluation/pass_at_k
pytest test_pass_at_k.py -v --tb=short -s
```

终端会打印各评测集的 n、c 以及 pass@1、pass@5、pass^2。
