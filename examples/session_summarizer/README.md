# Session Summarizer 示例

本示例演示如何在长对话场景下使用 `SessionSummarizer` 与 `SummarizerSessionManager` 自动压缩历史消息，并持续保持对话可用性。

## 关键特性

- **多轮长对话压缩**：在 14 轮对话中持续触发摘要与压缩
- **阈值触发机制**：通过 `set_summarizer_conversation_threshold` 控制触发频率
- **压缩效果可观测**：日志直接输出 `Original event count`、`Compressed event count`、`Compression ratio`
- **支持手动摘要**：示例末尾包含一次手动强制摘要，便于验证最终压缩效果

## Agent 层级结构说明

```text
python_tutor (LlmAgent)
└── session manager: SummarizerSessionManager
    └── summarizer: SessionSummarizer
```

关键文件：

- [examples/session_summarizer/agent/agent.py](./agent/agent.py)
- [examples/session_summarizer/run_agent.py](./run_agent.py)
- [examples/session_summarizer/.env](./.env)

## 关键代码解释

- `create_summarizer_manager()`：创建并配置 `SessionSummarizer`，设置对话轮次阈值与保留策略
- `run_agent_with_summarizer_manager()`：执行多轮会话并在关键回合打印会话压缩状态
- 手动摘要阶段：在末尾显式触发一次摘要，验证高压缩率下的最终状态

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

在 [examples/session_summarizer/.env](./.env) 中配置（或通过 `export` 设置）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

### 运行命令

```bash
cd examples/session_summarizer
python3 run_agent.py
```

## 运行结果（实测）

```text
===========================================================
Example 2: LlmAgent + SummarizerSessionManager demo
============================================================
📊 Session: llm_summarizer_manager_demo/user_005/4ab2ca33-6805-4f90-86ab-046165bb4ad4
💬 Multi-turn dialogue (14 turns)...

[INFO] Generated summary for session ...: 603 characters
[INFO] Compressed session ...: 8 events -> 5 events
📊 Session state after turn 4:
   - Original event count: 8
   - Compressed event count: 5
   - Compression ratio: 37.5

[INFO] Generated summary for session ...: 603 characters
[INFO] Compressed session ...: 13 events -> 5 events
📊 Session state after turn 10:
   - Original event count: 13
   - Compressed event count: 5
   - Compression ratio: 61.53846153846154

--- Manual summary creation ---
[INFO] Generated summary for session ...: 603 characters
[INFO] Compressed session ...: 39 events -> 5 events
   - Original event count: 39
   - Compressed event count: 5
   - Compression ratio: 87.2%
```

## 结果分析（是否符合要求）

结论：**符合本示例测试要求**。

- **摘要触发正常**：在多轮过程中多次出现 `Generated summary` 与 `Compressed session`
- **压缩效果明确**：从 `8->5`、`13->5` 到最终 `39->5`，压缩率逐步提升至 `87.2%`
- **会话连续性正常**：压缩过程中对话持续进行，未出现中断或异常退出
- **手动摘要可用**：末尾手动触发成功，验证了主动压缩能力

## 适用场景建议

- 长会话、知识辅导、客服等高历史累积场景
- 需要平衡上下文保留与 token 成本的线上系统
- 需要可观测摘要触发与压缩比例的调优场景
