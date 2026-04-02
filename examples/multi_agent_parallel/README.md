# Parallel 并行评审示例

本示例演示并行调用多个评审子 Agent（如质量、安全），对同一输入各自生成报告后汇总展示。

## 关键特性

- 并行子任务缩短_wall-clock_（相对顺序多次调用）
- 输出分块：`[Quality Review]` 与 `[Security Review]` Markdown
- 单进程 demo 打印合并结果

## Agent 层级结构说明

```text
parallel_root（并行编排）
├── quality_reviewer (LlmAgent)
└── security_reviewer (LlmAgent)
```

关键文件：

- [examples/multi_agent_parallel/agent/agent.py](./agent/agent.py)
- [examples/multi_agent_parallel/run_agent.py](./run_agent.py)
- [examples/multi_agent_parallel/.env](./.env)

## 关键代码解释

- 编排层等待各子 Agent 完成再拼接输出
- 适合 I/O 或模型调用可并行的独立评审维度

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

在 [examples/multi_agent_parallel/.env](./.env) 中配置（或通过 `export` 设置）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

### 运行命令

```bash
cd examples/multi_agent_parallel
python3 run_agent.py
```

## 运行结果（实测）


```text
Parallel Agent Demo - Parallel Review
Parallel Reviewing:
[quality_reviewer] Finished
[security_reviewer] Finished
[Quality Review] # Quality Review / Score: 6/10 / Feedback: ...
[Security Review] # Security Review: AI Smart Home System / Security Score: 5/10 ...
[END] multi_agent_parallel (exit_code=0)
```

## 结果分析（是否符合要求）

结论：**符合本示例测试要求**。

- 双评审均完成并输出结构化 Markdown；`exit_code=0`，`error.txt` 为空

## 适用场景建议

- 合规与安全并行扫描、双盲评审类工作流
- 延迟敏感且子任务无强顺序依赖的分析场景
