# Compose 多 Agent 编排示例

本示例演示 Compose 型编排：并行或组合调用质量分析与安全分析等子 Agent，对同一段产品文案输出多视角报告并最终汇总。

## 关键特性

- 多子 Agent 专责（如质量、安全、综合）
- 单次运行输出较长结构化分析文本
- 适合对比不同“评审角色”的结论

## Agent 层级结构说明

```text
compose_root（Compose 编排入口）
├── quality_analyst (LlmAgent)
├── security_analyst (LlmAgent)
└── ...（见 agent/agent.py 完整列表）
```

关键文件：

- [examples/multi_agent_compose/agent/agent.py](./agent/agent.py)
- [examples/multi_agent_compose/run_agent.py](./run_agent.py)
- [examples/multi_agent_compose/.env](./.env)

## 关键代码解释

- Compose 将多个子 Agent 的结果在编排层合并或续写
- `run_agent.py` 打印各阶段标题与正文片段

## 环境与运行

### 环境要求

- Python 3.10+（推荐 3.12）

### 安装步骤

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e .
```

### 环境变量要求

在 [examples/multi_agent_compose/.env](./.env) 中配置（或通过 `export` 设置）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

### 运行命令

```bash
cd examples/multi_agent_compose
python3 run_agent.py
```

## 运行结果（实测）


```text
Compose Agent Demo - Combined Orchestration
Run Process：
[quality_analyst] **Quality Analysis Report**
**Clarity:** 8/10
...
[security_analyst] ### **Security Analysis: Smart Home Security System**
...
[END] multi_agent_compose (exit_code=0)
```

## 结果分析（是否符合要求）

结论：**符合本示例测试要求**。

- 质量与安全分析报告均落地，并以 `exit_code=0` 结束；`error.txt` 为空

## 适用场景建议

- 产品/合规双人审阅自动化草稿
- 一次输入需要多维度评分与建议的生成式评审
