# LLM Agent 自定义编排示例

本示例演示如何基于 `BaseAgent` 实现自定义编排器（Custom Agent），并验证“文档分类 → 分支处理 → 质量校验”的动态工作流是否按预期执行。

## 关键特性

- **自定义编排能力**：通过继承 `BaseAgent` 实现条件分支与多阶段流程控制
- **动态处理策略**：根据文档类型（`simple` / `complex` / `technical`）选择不同子流程
- **ChainAgent 组合能力**：复杂文档场景使用 `ChainAgent` 串联“分析 → 处理”步骤
- **状态驱动流程**：通过 session state 传递 `doc_type`、`complex_analysis`、`quality_feedback`
- **阶段化日志可观测**：输出 Stage 1/2/3 进度，便于验证分支是否正确触发

## Agent 层级结构说明

本例是“1 个自定义总控 Agent + 多个子 Agent”的层级协作：

```text
smart_document_processor (Custom BaseAgent)
├── document_analyzer (LlmAgent)
├── simple_processor (LlmAgent)
├── complex_processor_chain (ChainAgent)
│   ├── complex_analyzer (LlmAgent)
│   └── complex_processor (LlmAgent)
├── technical_processor (LlmAgent)
└── quality_validator (LlmAgent)
```

关键文件：

- [examples/llmagent_with_custom_agent/agent/agent.py](./agent/agent.py)：自定义编排逻辑（条件分支 + 动态决策）
- [examples/llmagent_with_custom_agent/agent/prompts.py](./agent/prompts.py)：各子 Agent 的提示词
- [examples/llmagent_with_custom_agent/agent/config.py](./agent/config.py)：环境变量读取
- [examples/llmagent_with_custom_agent/run_agent.py](./run_agent.py)：3 类文档测试入口

## 关键代码解释

这一节用于快速定位“分类、分支、校验”三条主链路。

### 1) 文档分类与路由（`agent/agent.py`）

- 先由 `document_analyzer` 输出 `doc_type`
- 根据 `doc_type` 路由到不同处理流：
  - `simple` → `simple_processor`
  - `complex` → `complex_processor_chain`
  - `technical` → `technical_processor`

### 2) 复杂文档链式处理（`complex_processor_chain`）

- 复杂文档走 `ChainAgent`：
  - 第一步：`complex_analyzer` 产出结构化分析
  - 第二步：`complex_processor` 基于分析结果产出加工内容

### 3) 质量校验决策（Stage 3）

- `complex` / `technical` 文档会进入 `quality_validator`
- `simple` 文档跳过质量校验（性能优先）
- 校验反馈写入 `quality_feedback`，并输出是否通过的阶段日志

## 环境与运行

### 环境要求

- Python 3.10+（强烈建议 3.12）

### 安装步骤

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e .
```

### 环境变量要求

在 [examples/llmagent_with_custom_agent/.env](./.env) 中配置（或通过 `export`）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

### 运行命令

```bash
cd examples/llmagent_with_custom_agent
python3 run_agent.py
```

## 运行结果（实测）

```text
==================== Test case 1: Simple document example ====================
Expected type: simple
...
simple  [smart_document_processor] Document type recognition: simple
  [smart_document_processor] Stage 2: Use simple processing flow...
...
  [smart_document_processor] Simple document skipped quality validation stage
  [smart_document_processor] Smart document processing workflow completed!

==================== Test case 2: Complex document example ====================
Expected type: complex
...
complex  [smart_document_processor] Document type recognition: complex
  [smart_document_processor] Stage 2: Use complex document processing flow...
  [smart_document_processor] Use ChainAgent: Analyze → Process
...
  [smart_document_processor] Stage 3: Execute quality validation...
...
  [smart_document_processor] Quality validation found improvement points, provided suggestions
  [smart_document_processor] Smart document processing workflow completed!

==================== Test case 3: Technical document example ====================
Expected type: technical
...
technical  [smart_document_processor] Document type recognition: technical
  [smart_document_processor] Stage 2: Use technical document processing flow...
...
  [smart_document_processor] Stage 3: Execute quality validation...
quality verification passed
  [smart_document_processor] Quality validation found improvement points, provided suggestions
  [smart_document_processor] Smart document processing workflow completed!
```

## 结果分析（是否符合要求）

结论：**符合本示例测试要求**。

- **分类路由正确**：3 个测试样本均识别到预期类型（simple/complex/technical）
- **流程分支正确**：simple 跳过校验；complex 使用 `ChainAgent`；technical 进入技术处理 + 质量校验
- **状态驱动有效**：复杂与技术场景都触发 Stage 3，并输出质量反馈
- **编排能力验证通过**：自定义 Agent 成功实现“动态决策 + 多子 Agent 协作”

## 适用场景建议

- 需要按输入类型动态切换处理策略：适合使用本示例
- 需要将多步处理封装成可复用链路（如 Analyze→Process）：适合使用本示例
- 需要验证单 Agent 工具调用基础能力：建议使用 `examples/llmagent`
