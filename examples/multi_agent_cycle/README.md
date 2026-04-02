# Cycle 迭代优化示例

本示例演示 `CycleAgent`（或循环编排）：写手生成内容，评估员打分，达标后通过 `exit_refinement_loop` 工具结束循环。

## 关键特性

- 多轮迭代直至质量满足阈值或工具退出
- 子 Agent：内容生成与质量评估分工
- 日志展示 Round 与工具 `exit_refinement_loop` 调用

## Agent 层级结构说明

```text
cycle_root (Cycle / loop orchestration)
├── content_writer (LlmAgent)
└── content_evaluator (LlmAgent)
```

关键文件：

- [examples/multi_agent_cycle/agent/agent.py](./agent/agent.py)
- [examples/multi_agent_cycle/run_agent.py](./run_agent.py)
- [examples/multi_agent_cycle/.env](./.env)

## 关键代码解释

- 评估 Agent 在高分时调用退出工具，循环终止
- 写手根据评估反馈（若有）在下一轮改写

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

在 [examples/multi_agent_cycle/.env](./.env) 中配置（或通过 `export` 设置）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

### 运行命令

```bash
cd examples/multi_agent_cycle
python3 run_agent.py
```

## 运行结果（实测）


```text
Cycle Agent Demo - Iterative Content Improvement Cycle
==================== Round 1  ====================
[content_writer] Content Creation：
**AI-Powered Smart Home Security System** ...
[content_evaluator] Quality Assessment：
Clarity: 10/10
...
🔧 Invoke Tool：exit_refinement_loop
📋 Tool Result：{'status': 'content_approved', ...}
🎉 Content Improvement Completed！
[END] multi_agent_cycle (exit_code=0)
```

## 结果分析（是否符合要求）

结论：**符合本示例测试要求**。

- 单轮即达标并调用退出工具，流程结束语与 `exit_code=0` 一致；`error.txt` 为空

## 适用场景建议

- 文案/代码“生成—评审—再生成”的受控循环
- 需硬退出条件（工具）防止无限迭代的生产管线
