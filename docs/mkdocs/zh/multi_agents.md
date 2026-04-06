# Multi Agents

Multi Agents是trpc_agent框架中用于编排多个Agent协同工作的核心机制。与单个LlmAgent专注于特定任务不同，Multi Agents通过不同的编排模式将多个Agent组合起来，实现复杂工作流的自动化处理。

## 概述

### Multi Agents与LlmAgent的区别

- **LlmAgent**：单一Agent，使用LLM作为大脑，通过工具调用完成特定任务
- **Multi Agents**：多Agent编排系统，将多个LlmAgent按特定模式组合，通过状态传递和协作完成复杂工作流

Multi Agents基于 Sub Agent 概念构建，支持以下编排模式和辅助功能：

### 核心协作模式

#### Chain Agent
- **模式**：顺序执行，前一个Agent的输出作为下一个Agent的输入
- **适用场景**：需要按步骤处理的流水线任务，如文档处理（内容提取→翻译）
- **特点**：线性执行，每个Agent专注处理流程中的一个环节

#### Parallel Agent
- **模式**：同时执行多个Agent，各自独立处理相同输入
- **适用场景**：需要多角度分析的任务，如内容审查（质量检查+安全检查）
- **特点**：并发执行，提高效率，获得多维度结果

#### Cycle Agent
- **模式**：在多个Agent间循环执行，直到满足退出条件
- **适用场景**：需要迭代优化的任务，如内容创作（生成→评估→改进→再评估）
- **特点**：迭代执行，持续改进，适合需要多轮优化的场景

#### Sub Agents
- **模式**：层次化Agent结构，父Agent可以转发任务给专门的子Agent
- **适用场景**：复杂任务分解，如智能客服（路由Agent→专业咨询Agent→问题解决Agent）
- **特点**：层次结构，任务分发，专业化处理

### 辅助功能

- **Agent 工具 (AgentTool)** — 将 Agent 包装成工具，供其他 Agent 通过 `tools` 参数调用
- **TransferAgent（转移代理）** — 让不支持 transfer 能力的自定义 Agent 接入多 Agent 系统

### 确定性执行

与LlmAgent不同，Multi Agents的编排模式（Chain、Parallel、Cycle）本身是**确定性的**，不依赖LLM来决定执行顺序或流程。这意味着：

- **Chain Agent**：始终按照sub_agents列表中的顺序执行，无论输入如何
- **Parallel Agent**：始终同时执行所有sub_agents，无论输入如何  
- **Cycle Agent**：按照固定的循环模式执行，直到满足明确的退出条件

这种确定性确保了工作流的可预测性和可靠性，而工作流中的各个LlmAgent仍然可以根据输入动态调整其行为。

## 核心协作模式

### Chain Agent

Chain Agent按顺序执行多个Agent，形成处理流水线。通过 `output_key` 将前一个Agent的输出传递给下一个Agent，实现数据的顺序传递和处理。

#### 使用场景

- **内容创作流程**：规划 → 研究 → 写作
- **文档处理流程**：提取 → 翻译 → 校对
- **问题解决流程**：分析 → 设计 → 实现

#### 基本用法

```python
from trpc_agent_sdk.agents import ChainAgent, LlmAgent

# Step 1: 内容提取Agent
extractor_agent = LlmAgent(
    name="content_extractor",
    model="deepseek-v3-local-II",
    instruction="Extract key information from the input text and structure it clearly.",
    output_key="extracted_content"  # 将输出保存到状态变量
)

# Step 2: 翻译Agent，引用前一个Agent的输出
translator_agent = LlmAgent(
    name="translator", 
    model="deepseek-v3-local-II",
    instruction="""Translate the following extracted content to English:

{extracted_content}

Provide a natural, professional English translation with clear structure and formatting.""",
    output_key="translated_content"  # 将翻译结果保存到状态变量
)

# 创建链式Agent
processing_chain = ChainAgent(
    name="document_processor",
    description="Sequential document processing: extract → translate",
    sub_agents=[extractor_agent, translator_agent],
)
```

#### 架构

```
Chain Agent (document_processor)
│
├── Step 1: 内容提取 Agent
│   └── output_key="extracted_content"
│
└── Step 2: 翻译 Agent
    ├── 读取 {extracted_content}
    └── output_key="translated_content"
```

### Parallel Agent（并行Agent）

Parallel Agent同时执行多个Agent，适合需要多角度分析或并行处理的场景。每个Agent通过 `output_key` 保存独立的分析结果。

#### 使用场景

- **商业决策分析**：市场分析、技术评估、风险评估同时进行
- **内容审查**：质量审查 + 安全审查并行执行
- **多维度评估**：不同专家同时评估同一问题

#### 基本用法

```python
from trpc_agent_sdk.agents import ParallelAgent, LlmAgent

# 质量审查Agent
quality_reviewer = LlmAgent(
    name="quality_reviewer",
    model="deepseek-v3-local-II",
    instruction="""Review content quality: clarity, accuracy, readability.
Provide quality score (1-10) and brief feedback.""",
    output_key="quality_review"
)

# 安全审查Agent
security_reviewer = LlmAgent(
    name="security_reviewer", 
    model="deepseek-v3-local-II",
    instruction="""Review security concerns: data privacy, vulnerabilities.
Provide security score (1-10) and identify risks.""",
    output_key="security_review"
)

# 创建并行Agent
review_panel = ParallelAgent(
    name="review_panel",
    description="Parallel review: quality + security",
    sub_agents=[quality_reviewer, security_reviewer],
)
```

### Cycle Agent

Cycle Agent在多个Agent间循环执行，适合需要迭代优化的任务。通过 `output_key` 在循环中传递信息，通过exit工具控制循环退出。

#### 使用场景

- **内容优化**：生成 → 评估 → 改进 → 重复
- **问题解决**：提出 → 评估 → 增强 → 重复
- **质量保证**：草稿 → 审查 → 修订 → 重复

#### 循环控制机制

Cycle Agent提供两种退出循环的方式：

1. **工具退出**：通过在Agent中调用特定工具，设置 `InvocationContext.actions.escalate = True` 来主动退出
2. **最大迭代次数**：通过 `max_iterations` 参数设置循环的最大次数，防止无限循环

Cycle Agent 按顺序运行 sub_agents，然后重复整套流程。它会在以下任一情况发生时停止：

1. 某个工具调用设置了 `actions.escalate = True`
2. 达到 `max_iterations` 设定的上限
3. 上下文被取消（超时 / 手动取消）

**默认行为**：如果不设置退出工具，Cycle Agent 只会在达到 `max_iterations` 或遇到错误时停止。

**最佳实践**：
- 始终设置合理的 `max_iterations` 值（如3-10次）作为安全网
- 在评估Agent中提供明确的退出条件和工具调用
- 确保退出工具的调用条件足够明确，避免过早或过晚退出
- 退出工具函数建议保持轻量、无副作用，并做好 `None` / 解析失败的防御处理

#### 基本用法

```python
from trpc_agent_sdk.agents import CycleAgent, LlmAgent, InvocationContext
from trpc_agent_sdk.tools import FunctionTool

def exit_refinement_loop(tool_context: InvocationContext):
    """停止内容改进循环的工具函数"""
    tool_context.actions.escalate = True
    return {"status": "content_approved", "message": "Content quality is satisfactory"}

# 内容创作Agent
content_writer = LlmAgent(
    name="content_writer",
    model="deepseek-v3-local-II",
    instruction="""Create high-quality content based on the user's request.
    
If this is the first iteration, create original content.
If there's existing content with feedback, improve it based on the suggestions:

Existing content: {current_content}
Feedback: {feedback}

Output only the improved content.""",
    output_key="current_content"  # 将当前内容保存到状态变量
)

# 内容评估Agent
content_evaluator = LlmAgent(
    name="content_evaluator",
    model="deepseek-v3-local-II",
    instruction="""Evaluate the following content for quality:

{current_content}

Assessment criteria:
- Clarity and readability (score 1-10)
- Structure and organization (score 1-10) 
- Completeness and accuracy (score 1-10)

If ALL scores are 8 or above, call the exit_refinement_loop tool immediately.
If any score is below 8, provide specific feedback for improvement.""",
    output_key="feedback",  # 将反馈保存到状态变量
    tools=[FunctionTool(exit_refinement_loop)]
)

# 创建循环Agent
content_refinement_cycle = CycleAgent(
    name="content_refinement_loop", 
    description="Iterative content refinement: write → evaluate → improve",
    max_iterations=5,  # 最大循环次数，防止无限循环
    sub_agents=[content_writer, content_evaluator],
)
```

### Sub Agents（Agent 委托）

Sub Agents通过层次化结构实现任务的智能分发，父Agent可以根据请求内容使用 `transfer_to_agent` 转发给最合适的子Agent处理。

当 `LlmAgent` 配置了 `sub_agents` 参数后，框架会自动注入 `transfer_to_agent` 工具，允许主 Agent 根据任务类型选择合适的 Sub Agent。

#### 使用场景

- **任务分类**：根据用户请求自动选择合适的 Sub Agent
- **智能路由**：将复杂任务路由到最合适的处理者
- **专业化处理**：每个 Sub Agent 专注于特定领域
- **无缝切换**：在 Sub Agent 之间无缝切换，保持对话连续性

#### 基本用法

```python
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.tools import FunctionTool

# 技术支持专员
technical_support_agent = LlmAgent(
    name="technical_support",
    model="deepseek-v3-local-II",
    instruction="""You are a technical support specialist. 
Help with device troubleshooting and system diagnostics.
Use check_system_status tool to check device status.""",
    tools=[FunctionTool(check_system_status)],
    # 禁止将控制权转回父Agent
    disallow_transfer_to_parent=True,
    output_key="technical_result"
)

# 销售咨询专员
sales_consultant_agent = LlmAgent(
    name="sales_consultant", 
    model="deepseek-v3-local-II",
    instruction="""You are a sales consultant. Help customers with product information.
Use get_product_info tool with: speakers, displays, or security.""",
    tools=[FunctionTool(get_product_info)],
    # 禁止将控制权转回父Agent
    disallow_transfer_to_parent=True,
    output_key="sales_result"
)

# 主客服协调员
customer_service_coordinator = LlmAgent(
    name="customer_service_coordinator",
    model="deepseek-v3-local-II",
    instruction="""You are a customer service coordinator.
Route customer inquiries:
- Technical issues → transfer to technical_support
- Product questions → transfer to sales_consultant""",
    sub_agents=[technical_support_agent, sales_consultant_agent],
    output_key="coordinator_result"
)
```

#### 委托架构

```
协调者 Agent (主入口)
├── 分析用户请求
├── 选择合适的 Sub Agent
└── 使用 transfer_to_agent 工具委托任务
    ├── 技术支持 Sub Agent (设备诊断)
    └── 销售咨询 Sub Agent (产品信息)
```

#### 转移控制选项

`LlmAgent` 提供以下参数控制 Agent 间的转移行为：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `disallow_transfer_to_parent` | `False` | 设为 `True` 禁止子 Agent 将控制权转回父 Agent |
| `disallow_transfer_to_peers` | `False` | 设为 `True` 禁止子 Agent 将控制权转给同级 Agent |
| `default_transfer_message` | `None` | 自定义转移指令，覆盖默认的转移提示语 |

## 组合模式（Compose Agents）

不同的编排模式可以灵活组合，通过 `output_key` 连接不同阶段的结果，创建更复杂的工作流：

```python
# Stage 1: 并行分析阶段
parallel_analysis_stage = ParallelAgent(
    name="parallel_analysis_team",
    description="Parallel quality and security analysis",
    sub_agents=[quality_analyst, security_analyst],
)

# Stage 2: 综合报告生成，引用并行分析结果
report_generator = LlmAgent(
    name="report_generator",
    model="deepseek-v3-local-II",
    instruction="""Generate analysis report based on:

Quality Analysis: {quality_analysis}
Security Analysis: {security_analysis}

Create summary with overall assessment and recommendations.""",
    output_key="final_report"
)

# 组合：并行分析 → 综合报告
analysis_pipeline = ChainAgent(
    name="analysis_pipeline",
    description="Parallel analysis → integrated report",
    sub_agents=[parallel_analysis_stage, report_generator],
)
```

更多组合方式：

```python
# Chain + Cycle：流水线中嵌套迭代优化
pipeline_with_refinement = ChainAgent(
    name="pipeline_with_refinement",
    sub_agents=[
        data_collector,          # Step 1: 数据收集
        content_refinement_cycle, # Step 2: 循环优化（CycleAgent）
        final_formatter,         # Step 3: 最终格式化
    ],
)

# Team 作为 Sub Agent：团队嵌套在更大的编排中
team_based_pipeline = ChainAgent(
    name="team_pipeline",
    sub_agents=[
        requirement_analyzer,    # Step 1: 需求分析
        content_team,            # Step 2: 团队协作（TeamAgent）
        quality_reviewer,        # Step 3: 质量审查
    ],
)
```

## 辅助功能

### Agent 工具 (AgentTool)

AgentTool 允许将任何 Agent 包装成可调用的工具，供其他 Agent 通过 `tools` 参数使用。与 `transfer_to_agent` 的**控制权转移**不同，AgentTool 是**函数调用**模式——主 Agent 调用子 Agent 作为工具，获取结果后继续自己的处理流程。

#### 使用场景

- **专业化委托**：主 Agent 将特定任务委托给专业 Agent，获取结果后继续处理
- **工具集成**：将 Agent 能力封装成可复用的工具组件
- **模块化设计**：Agent 可以像普通工具一样被组合和复用
- **保持控制**：主 Agent 始终保持控制权，子 Agent 只是作为工具被调用

#### 基本用法

```python
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.tools import AgentTool

# 创建专门的翻译 Agent
translator_agent = LlmAgent(
    name="translator",
    model="deepseek-chat",
    description="专业的文本翻译工具",
    instruction="你是一个专业的翻译工具，能够准确翻译中英文文本。",
)

# 将 Agent 包装成工具
translator_tool = AgentTool(agent=translator_agent)

# 在主 Agent 中使用 Agent 工具
main_agent = LlmAgent(
    name="content_processor",
    model="deepseek-chat",
    description="内容处理助手",
    instruction="你是内容处理助手，可以调用翻译工具处理多语言内容。",
    tools=[translator_tool],
)
```

#### 架构

```
内容处理助手 (主 Agent)
├── 翻译工具 (AgentTool)
│   └── 翻译 Agent (专门化 Agent)
├── 其他工具 (FunctionTool)
└── ...
```

#### AgentTool 参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `agent` | AgentABC | 必需 | 需要被包装的 Agent |
| `skip_summarization` | bool | False | 是否需要跳过总结 |
| `filters_name` | list[str] | None | 关联的 filter 名称 |
| `filters` | list[BaseFilter] | None | 过滤器实例列表 |


#### AgentTool vs transfer_to_agent 对比

| 特性 | AgentTool | transfer_to_agent |
|------|-----------|-------------------|
| 控制权 | 主 Agent 保持控制权 | 控制权转移给子 Agent |
| 调用方式 | 作为工具函数调用 | 通过 `sub_agents` 自动注入 |
| 返回方式 | 工具返回结果给主 Agent | 子 Agent 直接响应用户 |
| 适用场景 | 需要主 Agent 综合多个结果 | 需要子 Agent 独立处理 |


### 转移代理（TransferAgent）

TransferAgent 是一个转移代理 Agent，用于让不支持 transfer 能力的自定义 Agent（如 KnotAgent、RemoteAgent 等）拥有 transfer 能力，从而接入到 tRPC-Agent 框架的多 Agent 系统中。

通过 TransferAgent，自定义 Agent 可以：
- **作为 sub_agent**：父 Agent 可转移控制权到此 Agent，此 Agent 可转移控制权给父/兄弟 Agent
- **配置 sub_agent**：根据自定义 Agent 的调用结果，智能转移控制权到其他 Agent

#### 场景 1：作为 sub_agent

不需要提供 `sub_agents` 和 `transfer_instruction`，TransferAgent 直接让目标 Agent 可被其他 Agent 调用。

```python
from trpc_agent_sdk.agents import TransferAgent, LlmAgent
from trpc_agent_sdk.server.knot_agent import KnotAgent

# 创建自定义 Agent（不支持 transfer），KnotAgent为支持内部平台的特定Agent
knot_agent = KnotAgent(
    name="knot-assistant",
    knot_api_url="...",
    knot_api_key="...",
    knot_model="...",
)

# 通过 TransferAgent 让 knot_agent 拥有 transfer 能力
transfer_agent = TransferAgent(
    knot_agent,
    model=model,
)

# 现在 knot_agent（通过 transfer_agent）可以作为 sub_agent 被调用
coordinator = LlmAgent(
    name="coordinator",
    model=model,
    sub_agents=[transfer_agent],
)
```

#### 场景 2：配置 sub_agent

提供 `sub_agents` 后，TransferAgent 会根据目标 Agent 的返回结果，智能地转发到不同的子 Agent 进行进一步处理。`transfer_instruction` 可选，不提供时使用默认规则。

```python
from trpc_agent_sdk.agents import TransferAgent, LlmAgent
from trpc_agent_sdk.server.knot_agent import KnotAgent

# 创建自定义 Agent（不支持 transfer），KnotAgent为支持内部平台的特定Agent
knot_agent = KnotAgent(
    name="knot-assistant",
    knot_api_url="...",
    knot_api_key="...",
    knot_model="...",
)

# 创建子Agent
data_analyst = LlmAgent(
    name="data_analyst",
    model=model,
    description="Performs data analysis and generates insights",
    instruction="You are a data analyst...",
)

# 方式 1：提供自定义转移规则
transfer_agent = TransferAgent(
    knot_agent,
    model=model,
    sub_agents=[data_analyst],
    transfer_instruction=(
        "After knot-assistant returns results, analyze the response:\n"
        "1. If the result contains data or statistics, transfer to data_analyst.\n"
        "2. Otherwise, return directly to the user."
    ),
)

# 方式 2：使用默认转移规则（不提供 transfer_instruction）
transfer_agent = TransferAgent(
    knot_agent,
    model=model,
    sub_agents=[data_analyst],
)
```

默认规则会自动分析目标 Agent 的结果：
- 如果内容需要子 Agent 的专业能力（与子 Agent 的描述匹配），会转移
- 如果内容包含错误或不完整信息，会考虑转移
- 如果内容完整且满意，不会转移
- 根据子 Agent 的描述选择最合适的 Agent

#### 配置参数说明

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `agent` | BaseAgent | 是 | 目标 Agent，TransferAgent 将让该 Agent 拥有 transfer 能力 |
| `model` | Union[str, LLMModel, Callable] | 是 | 用于执行转移决策的 LLM 模型（仅在提供 `sub_agents` 时使用） |
| `sub_agents` | List[AgentABC] | 否 | 子 Agent 列表，提供后 TransferAgent 会分析目标 Agent 结果并决定是否转移 |
| `transfer_instruction` | str | 否 | 自定义转移规则，为空时提供 `sub_agents` 会自动使用默认规则 |

- **`agent`**：目标 Agent，必填。TransferAgent 会包装此 Agent 使其拥有 transfer 能力
- **`model`**：LLM 模型，必填。用于分析目标 Agent 的结果并决定是否转移（仅在提供 `sub_agents` 时使用）
- **`sub_agents`**：可选。如果提供，TransferAgent 会：
  1. 调用目标 Agent 并收集结果
  2. 使用 LLM 分析结果
  3. 根据 `transfer_instruction`（或默认规则）决定是否转移到子 Agent
- **`transfer_instruction`**：可选。自定义转移规则，仅在提供 `sub_agents` 时生效。如果为空，会自动使用默认的通用规则

#### 使用场景

- **场景 1（作为 sub_agent）**：不提供 `sub_agents` 时，TransferAgent 使目标 Agent 可被其他 Agent 调用（例如作为 sub_agent）
- **场景 2（转发给其他 sub_agent）**：提供 `sub_agents`，TransferAgent 会分析目标 Agent 的结果并决定是否转移到子 Agent。`transfer_instruction` 可选，不提供时使用默认规则

#### Agent 名称

TransferAgent 的名称会自动生成，格式为 `transfer_{target_agent_name}`。例如：
- 如果目标 Agent 名称是 `knot-assistant`，TransferAgent 的名称将是 `transfer_knot-assistant`
- 如果目标 Agent 名称是 `custom-agent`，TransferAgent 的名称将是 `transfer_custom-agent`

#### 注意事项

1. **模型要求**：`model` 是必填参数，用于执行转移决策（仅在提供 `sub_agents` 时使用）
2. **默认规则**：如果提供 `sub_agents` 但未提供 `transfer_instruction`，会自动使用默认的通用转移规则
3. **转移规则设计**：在场景 2 中，建议提供清晰的 `transfer_instruction`，帮助 LLM 准确判断何时转移到哪个子 Agent
4. **目标 Agent 限制**：目标 Agent 不能是 TransferAgent 本身，也不能在 `sub_agents` 列表中（如果存在会被自动移除）


## 状态传递与上下文管理

Multi Agents通过 `output_key` 机制在Agent间传递信息。每个Agent可以将输出保存到状态变量，后续Agent通过 `{var}` 语法引用：

### 状态变量工作原理

1. **存储**：当Agent设置了 `output_key`，其输出会自动保存到session的state字典中
2. **引用**：在instruction中使用 `{variable_name}` 语法可以插入状态变量的值
3. **作用域**：状态变量在整个session中共享，所有Agent都可以访问
4. **覆盖**：如果多个Agent使用相同的 `output_key`，后执行的Agent会覆盖前面的值

### 状态变量最佳实践

- **命名规范**：使用描述性的变量名，如 `extracted_content`、`quality_review` 等
- **避免冲突**：确保不同Agent的 `output_key` 具有唯一性，除非有意覆盖
- **类型一致**：保持状态变量的数据类型一致性，便于后续Agent处理
- **文档化**：在instruction中明确说明期望的状态变量格式

```python
# Agent可以将输出保存到状态变量
content_analyzer = LlmAgent(
    name="content_analyzer",
    model="deepseek-v3-local-II",
    instruction="Analyze the input content and provide detailed insights.",
    output_key="analysis_result",  # 保存输出到状态变量
)

# 后续Agent可以通过模板引用前面的结果
report_writer = LlmAgent(
    name="report_writer", 
    model="deepseek-v3-local-II",
    instruction="""Generate a comprehensive report based on the analysis:

**Analysis Results:**
{analysis_result}

Create a structured report with summary, key findings, and recommendations.""",  # 引用状态变量
    output_key="final_report"
)
```

### 高级状态管理

除了基本的 `output_key` 机制，还可以通过以下方式管理状态：

```python
# 在运行时访问完整的session状态
session = await session_service.get_session(
    app_name=APP_NAME, 
    user_id=USER_ID, 
    session_id=session_id
)

# 访问所有状态变量
if session and session.state:
    analysis_result = session.state.get("analysis_result")
    quality_review = session.state.get("quality_review")
    security_review = session.state.get("security_review")
```

### 移除前面 Agent 的消息
在一次会话中，有时前面 Agent 产生的消息对当前 Agent 的执行没有帮助，可通过将 `include_previous_history` 设为 `False`，避免把这些消息拼进当前 Agent 的上下文，例如：

```python
LlmAgent(
    ...,
    include_previous_history=False,
)
```

## 完整示例

各种Multi Agents模式的完整示例见：

### 核心协作模式示例
- Chain Agent 示例：[examples/multi_agent_chain/run_agent.py](../../../examples/multi_agent_chain/run_agent.py)
- Parallel Agent 示例：[examples/multi_agent_parallel/run_agent.py](../../../examples/multi_agent_parallel/run_agent.py)
- Cycle Agent 示例：[examples/multi_agent_cycle/run_agent.py](../../../examples/multi_agent_cycle/run_agent.py)
- 组合模式示例：[examples/multi_agent_compose/run_agent.py](../../../examples/multi_agent_compose/run_agent.py)
- Sub Agents 示例：[examples/multi_agent_subagent/run_agent.py](../../../examples/multi_agent_subagent/run_agent.py)

### 辅助功能示例
- AgentTool 示例：[examples/agent_tools/run_agent.py](../../../examples/agent_tools/run_agent.py)
- TransferAgent 示例：[examples/transfer_agent/run_agent.py](../../../examples/transfer_agent/run_agent.py)
