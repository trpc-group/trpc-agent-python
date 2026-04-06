# ChainAgent 顺序链路示例

本示例演示 `ChainAgent` 将多个 `LlmAgent` 固定顺序串联，通过 `output_key` 把前一步结构化输出交给后一步（抽取 → 翻译）。

## 关键特性

- 确定性执行顺序，无运行时路由
- 子 Agent 分工：内容抽取与翻译
- 控制台分别打印各子 Agent 产出

## Agent 层级结构说明

```text
chain_root (ChainAgent)
├── content_extractor (LlmAgent)
└── translator (LlmAgent)
```

关键文件：

- [examples/multi_agent_chain/agent/agent.py](./agent/agent.py)
- [examples/multi_agent_chain/run_agent.py](./run_agent.py)
- [examples/multi_agent_chain/.env](./.env)

## 关键代码解释

- `ChainAgent(sub_agents=[extractor_agent, translator_agent], ...)`
- 上游输出键写入 runner state，下游指令中引用该键

## 环境与运行

### 环境要求

- Python 3.12

### 安装步骤

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e .
```

### 环境变量要求

在 [examples/multi_agent_chain/.env](./.env) 中配置（或通过 `export` 设置）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

### 运行命令

```bash
cd examples/multi_agent_chain
python3 run_agent.py
```

## 运行结果（实测）


```text
Chain Agent Demo - Information Passing via output_key
Processing Flow: Extraction → Translation
[content_extractor] Output：（Markdown，含 # Smart Home Control System 等章节）
[translator] Output：（英文化 Markdown，结构与上游对应）
[END] multi_agent_chain (exit_code=0)
```

## 结果分析（是否符合要求）

结论：**符合本示例测试要求**。

- 两段 Markdown 输出先后打印，链路闭合；`exit_code=0`，`error.txt` 为空

## 适用场景建议

- ETL 式文本流水线：解析、重写、多语言固定步骤
- 不需要协调者动态选子 Agent 的批处理任务
