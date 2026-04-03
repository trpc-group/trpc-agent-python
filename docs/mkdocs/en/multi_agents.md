# Multi Agents

Multi Agents is the core mechanism in the trpc_agent framework for orchestrating multiple Agents to work collaboratively. Unlike a single LlmAgent that focuses on specific tasks, Multi Agents combines multiple Agents through different orchestration patterns to automate complex workflow processing.

## Overview

### Difference Between Multi Agents and LlmAgent

- **LlmAgent**: A single Agent that uses an LLM as its brain and completes specific tasks through tool invocation
- **Multi Agents**: A multi-Agent orchestration system that combines multiple LlmAgents in specific patterns, completing complex workflows through state passing and collaboration

Multi Agents is built on the Sub Agent concept and supports the following orchestration patterns and auxiliary features:

### Core Collaboration Patterns

#### Chain Agent
- **Pattern**: Sequential execution, where the output of the previous Agent serves as input for the next Agent
- **Use cases**: Pipeline tasks requiring step-by-step processing, such as document processing (content extraction → translation)
- **Characteristics**: Linear execution, each Agent focuses on one stage of the pipeline

#### Parallel Agent
- **Pattern**: Multiple Agents execute simultaneously, each independently processing the same input
- **Use cases**: Tasks requiring multi-perspective analysis, such as content review (quality check + security check)
- **Characteristics**: Concurrent execution, improved efficiency, multi-dimensional results

#### Cycle Agent
- **Pattern**: Cyclic execution among multiple Agents until an exit condition is met
- **Use cases**: Tasks requiring iterative refinement, such as content creation (generate → evaluate → improve → re-evaluate)
- **Characteristics**: Iterative execution, continuous improvement, suitable for scenarios requiring multiple rounds of optimization

#### Sub Agents
- **Pattern**: Hierarchical Agent structure where a parent Agent can forward tasks to specialized child Agents
- **Use cases**: Complex task decomposition, such as intelligent customer service (routing Agent → specialized consultation Agent → problem resolution Agent)
- **Characteristics**: Hierarchical structure, task distribution, specialized processing

### Auxiliary Features

- **AgentTool** — Wraps an Agent as a tool for other Agents to invoke via the `tools` parameter
- **TransferAgent** — Enables custom Agents without transfer capability to participate in multi-Agent systems

### Deterministic Execution

Unlike LlmAgent, the orchestration patterns of Multi Agents (Chain, Parallel, Cycle) are inherently **deterministic** and do not rely on an LLM to determine execution order or flow. This means:

- **Chain Agent**: Always executes in the order of the sub_agents list, regardless of input
- **Parallel Agent**: Always executes all sub_agents simultaneously, regardless of input
- **Cycle Agent**: Executes in a fixed cyclic pattern until an explicit exit condition is met

This determinism ensures workflow predictability and reliability, while individual LlmAgents within the workflow can still dynamically adjust their behavior based on input.

## Core Collaboration Patterns

### Chain Agent

Chain Agent executes multiple Agents sequentially, forming a processing pipeline. It passes the output of the previous Agent to the next Agent via `output_key`, enabling sequential data passing and processing.

#### Use Cases

- **Content creation pipeline**: Planning → Research → Writing
- **Document processing pipeline**: Extraction → Translation → Proofreading
- **Problem solving pipeline**: Analysis → Design → Implementation

#### Basic Usage

```python
from trpc_agent_sdk.agents import ChainAgent, LlmAgent

# Step 1: Content Extraction Agent
extractor_agent = LlmAgent(
    name="content_extractor",
    model="deepseek-v3-local-II",
    instruction="Extract key information from the input text and structure it clearly.",
    output_key="extracted_content"  # Save output to a state variable
)

# Step 2: Translation Agent, referencing the previous Agent's output
translator_agent = LlmAgent(
    name="translator", 
    model="deepseek-v3-local-II",
    instruction="""Translate the following extracted content to English:

{extracted_content}

Provide a natural, professional English translation with clear structure and formatting.""",
    output_key="translated_content"  # Save the translation result to a state variable
)

# Create a Chain Agent
processing_chain = ChainAgent(
    name="document_processor",
    description="Sequential document processing: extract → translate",
    sub_agents=[extractor_agent, translator_agent],
)
```

#### Architecture

```
Chain Agent (document_processor)
│
├── Step 1: Content Extraction Agent
│   └── output_key="extracted_content"
│
└── Step 2: Translation Agent
    ├── Reads {extracted_content}
    └── output_key="translated_content"
```

### Parallel Agent

Parallel Agent executes multiple Agents simultaneously, suitable for scenarios requiring multi-perspective analysis or parallel processing. Each Agent saves its independent analysis results via `output_key`.

#### Use Cases

- **Business decision analysis**: Market analysis, technical assessment, and risk assessment running concurrently
- **Content review**: Quality review + security review executed in parallel
- **Multi-dimensional evaluation**: Different experts evaluating the same issue simultaneously

#### Basic Usage

```python
from trpc_agent_sdk.agents import ParallelAgent, LlmAgent

# Quality Review Agent
quality_reviewer = LlmAgent(
    name="quality_reviewer",
    model="deepseek-v3-local-II",
    instruction="""Review content quality: clarity, accuracy, readability.
Provide quality score (1-10) and brief feedback.""",
    output_key="quality_review"
)

# Security Review Agent
security_reviewer = LlmAgent(
    name="security_reviewer", 
    model="deepseek-v3-local-II",
    instruction="""Review security concerns: data privacy, vulnerabilities.
Provide security score (1-10) and identify risks.""",
    output_key="security_review"
)

# Create a Parallel Agent
review_panel = ParallelAgent(
    name="review_panel",
    description="Parallel review: quality + security",
    sub_agents=[quality_reviewer, security_reviewer],
)
```

### Cycle Agent

Cycle Agent executes cyclically among multiple Agents, suitable for tasks requiring iterative refinement. It passes information during cycles via `output_key` and controls cycle exit through the exit tool.

#### Use Cases

- **Content refinement**: Generate → Evaluate → Improve → Repeat
- **Problem solving**: Propose → Evaluate → Enhance → Repeat
- **Quality assurance**: Draft → Review → Revise → Repeat

#### Cycle Control Mechanism

Cycle Agent provides two ways to exit the cycle:

1. **Tool exit**: By invoking a specific tool within an Agent, set `InvocationContext.actions.escalate = True` to actively exit
2. **Maximum iterations**: Set the maximum number of cycles via the `max_iterations` parameter to prevent infinite loops

Cycle Agent runs sub_agents in order, then repeats the entire process. It stops when any of the following occurs:

1. A tool invocation sets `actions.escalate = True`
2. The `max_iterations` limit is reached
3. The context is cancelled (timeout / manual cancellation)

**Default behavior**: If no exit tool is configured, Cycle Agent will only stop when `max_iterations` is reached or an error is encountered.

**Best practices**:
- Always set a reasonable `max_iterations` value (e.g., 3-10) as a safety net
- Provide clear exit conditions and tool invocations in the evaluator Agent
- Ensure the exit tool's trigger conditions are sufficiently explicit to avoid premature or delayed exits
- Keep exit tool functions lightweight, side-effect-free, and with proper `None` / parsing failure handling

#### Basic Usage

```python
from trpc_agent_sdk.agents import CycleAgent, LlmAgent, InvocationContext
from trpc_agent_sdk.tools import FunctionTool

def exit_refinement_loop(tool_context: InvocationContext):
    """Tool function to stop the content refinement loop"""
    tool_context.actions.escalate = True
    return {"status": "content_approved", "message": "Content quality is satisfactory"}

# Content Writer Agent
content_writer = LlmAgent(
    name="content_writer",
    model="deepseek-v3-local-II",
    instruction="""Create high-quality content based on the user's request.
    
If this is the first iteration, create original content.
If there's existing content with feedback, improve it based on the suggestions:

Existing content: {current_content}
Feedback: {feedback}

Output only the improved content.""",
    output_key="current_content"  # Save the current content to a state variable
)

# Content Evaluator Agent
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
    output_key="feedback",  # Save feedback to a state variable
    tools=[FunctionTool(exit_refinement_loop)]
)

# Create a Cycle Agent
content_refinement_cycle = CycleAgent(
    name="content_refinement_loop", 
    description="Iterative content refinement: write → evaluate → improve",
    max_iterations=5,  # Maximum number of cycles to prevent infinite loops
    sub_agents=[content_writer, content_evaluator],
)
```

### Sub Agents (Agent Delegation)

Sub Agents implement intelligent task distribution through a hierarchical structure, where a parent Agent can forward requests to the most suitable child Agent using `transfer_to_agent`.

When an `LlmAgent` is configured with the `sub_agents` parameter, the framework automatically injects the `transfer_to_agent` tool, allowing the main Agent to select the appropriate Sub Agent based on the task type.

#### Use Cases

- **Task classification**: Automatically select the appropriate Sub Agent based on user requests
- **Intelligent routing**: Route complex tasks to the most suitable handler
- **Specialized processing**: Each Sub Agent focuses on a specific domain
- **Seamless switching**: Seamlessly switch between Sub Agents while maintaining conversation continuity

#### Basic Usage

```python
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.tools import FunctionTool

# Technical Support Specialist
technical_support_agent = LlmAgent(
    name="technical_support",
    model="deepseek-v3-local-II",
    instruction="""You are a technical support specialist. 
Help with device troubleshooting and system diagnostics.
Use check_system_status tool to check device status.""",
    tools=[FunctionTool(check_system_status)],
    # Prevent transferring control back to the parent Agent
    disallow_transfer_to_parent=True,
    output_key="technical_result"
)

# Sales Consultant
sales_consultant_agent = LlmAgent(
    name="sales_consultant", 
    model="deepseek-v3-local-II",
    instruction="""You are a sales consultant. Help customers with product information.
Use get_product_info tool with: speakers, displays, or security.""",
    tools=[FunctionTool(get_product_info)],
    # Prevent transferring control back to the parent Agent
    disallow_transfer_to_parent=True,
    output_key="sales_result"
)

# Main Customer Service Coordinator
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

#### Delegation Architecture

```
Coordinator Agent (Main Entry Point)
├── Analyze user request
├── Select the appropriate Sub Agent
└── Delegate the task using the transfer_to_agent tool
    ├── Technical Support Sub Agent (Device Diagnostics)
    └── Sales Consultant Sub Agent (Product Information)
```

#### Transfer Control Options

`LlmAgent` provides the following parameters to control transfer behavior between Agents:

| Parameter | Default | Description |
|------|--------|------|
| `disallow_transfer_to_parent` | `False` | Set to `True` to prevent a child Agent from transferring control back to the parent Agent |
| `disallow_transfer_to_peers` | `False` | Set to `True` to prevent a child Agent from transferring control to peer Agents |
| `default_transfer_message` | `None` | Custom transfer instruction that overrides the default transfer prompt |

## Compose Patterns

Different orchestration patterns can be flexibly combined, connecting results of different stages via `output_key` to create more complex workflows:

```python
# Stage 1: Parallel Analysis Stage
parallel_analysis_stage = ParallelAgent(
    name="parallel_analysis_team",
    description="Parallel quality and security analysis",
    sub_agents=[quality_analyst, security_analyst],
)

# Stage 2: Integrated Report Generation, referencing parallel analysis results
report_generator = LlmAgent(
    name="report_generator",
    model="deepseek-v3-local-II",
    instruction="""Generate analysis report based on:

Quality Analysis: {quality_analysis}
Security Analysis: {security_analysis}

Create summary with overall assessment and recommendations.""",
    output_key="final_report"
)

# Compose: Parallel Analysis → Integrated Report
analysis_pipeline = ChainAgent(
    name="analysis_pipeline",
    description="Parallel analysis → integrated report",
    sub_agents=[parallel_analysis_stage, report_generator],
)
```

More composition patterns:

```python
# Chain + Cycle: Embedding iterative refinement within a pipeline
pipeline_with_refinement = ChainAgent(
    name="pipeline_with_refinement",
    sub_agents=[
        data_collector,          # Step 1: Data Collection
        content_refinement_cycle, # Step 2: Iterative Refinement (CycleAgent)
        final_formatter,         # Step 3: Final Formatting
    ],
)

# Team as Sub Agent: Team nested within a larger orchestration
team_based_pipeline = ChainAgent(
    name="team_pipeline",
    sub_agents=[
        requirement_analyzer,    # Step 1: Requirement Analysis
        content_team,            # Step 2: Team Collaboration (TeamAgent)
        quality_reviewer,        # Step 3: Quality Review
    ],
)
```

## Auxiliary Features

### AgentTool

AgentTool allows wrapping any Agent as a callable tool for other Agents to use via the `tools` parameter. Unlike the **control transfer** of `transfer_to_agent`, AgentTool uses a **function call** pattern — the main Agent invokes the child Agent as a tool, retrieves the result, and continues its own processing flow.

#### Use Cases

- **Specialized delegation**: The main Agent delegates specific tasks to a specialized Agent, retrieves results, and continues processing
- **Tool integration**: Encapsulates Agent capabilities as reusable tool components
- **Modular design**: Agents can be composed and reused like regular tools
- **Retained control**: The main Agent always retains control; the child Agent is only invoked as a tool

#### Basic Usage

```python
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.tools import AgentTool

# Create a specialized Translation Agent
translator_agent = LlmAgent(
    name="translator",
    model="deepseek-chat",
    description="A professional text translation tool",
    instruction="You are a professional translation tool capable of accurately translating text between Chinese and English.",
)

# Wrap the Agent as a tool
translator_tool = AgentTool(agent=translator_agent)

# Use the Agent tool in the main Agent
main_agent = LlmAgent(
    name="content_processor",
    model="deepseek-chat",
    description="Content processing assistant",
    instruction="You are a content processing assistant that can invoke the translation tool to handle multilingual content.",
    tools=[translator_tool],
)
```

#### Architecture

```
Content Processing Assistant (Main Agent)
├── Translation Tool (AgentTool)
│   └── Translation Agent (Specialized Agent)
├── Other Tools (FunctionTool)
└── ...
```

#### AgentTool Parameters

| Parameter | Type | Default | Description |
|------|------|--------|------|
| `agent` | AgentABC | Required | The Agent to be wrapped |
| `skip_summarization` | bool | False | Whether to skip summarization |
| `filters_name` | list[str] | None | Associated filter names |
| `filters` | list[BaseFilter] | None | Filter instances |


#### AgentTool vs transfer_to_agent Comparison

| Feature | AgentTool | transfer_to_agent |
|------|-----------|-------------------|
| Control | Main Agent retains control | Control is transferred to the child Agent |
| Invocation | Invoked as a tool function | Automatically injected via `sub_agents` |
| Return | Tool returns results to the main Agent | Child Agent responds directly to the user |
| Use case | When the main Agent needs to synthesize multiple results | When the child Agent needs to handle tasks independently |


### TransferAgent

TransferAgent is a transfer proxy Agent designed to enable custom Agents without transfer capability (such as KnotAgent, RemoteAgent, etc.) to gain transfer capability, thereby integrating them into the tRPC-Agent framework's multi-Agent system.

Through TransferAgent, custom Agents can:
- **Act as sub_agent**: The parent Agent can transfer control to this Agent, and this Agent can transfer control to parent/sibling Agents
- **Configure sub_agents**: Intelligently transfer control to other Agents based on the custom Agent's invocation results

#### Scenario 1: Acting as sub_agent

No need to provide `sub_agents` or `transfer_instruction` — TransferAgent directly makes the target Agent callable by other Agents.

```python
from trpc_agent_sdk.agents import TransferAgent, LlmAgent
from trpc_agent_sdk.server.knot_agent import KnotAgent

# Create a custom Agent (without transfer capability); KnotAgent is a specific Agent supporting an internal platform
knot_agent = KnotAgent(
    name="knot-assistant",
    knot_api_url="...",
    knot_api_key="...",
    knot_model="...",
)

# Enable transfer capability for knot_agent via TransferAgent
transfer_agent = TransferAgent(
    knot_agent,
    model=model,
)

# Now knot_agent (via transfer_agent) can be invoked as a sub_agent
coordinator = LlmAgent(
    name="coordinator",
    model=model,
    sub_agents=[transfer_agent],
)
```

#### Scenario 2: Configuring sub_agents

After providing `sub_agents`, TransferAgent analyzes the target Agent's returned results and intelligently forwards them to different child Agents for further processing. `transfer_instruction` is optional — when not provided, default rules are used.

```python
from trpc_agent_sdk.agents import TransferAgent, LlmAgent
from trpc_agent_sdk.server.knot_agent import KnotAgent

# Create a custom Agent (without transfer capability); KnotAgent is a specific Agent supporting an internal platform
knot_agent = KnotAgent(
    name="knot-assistant",
    knot_api_url="...",
    knot_api_key="...",
    knot_model="...",
)

# Create child Agents
data_analyst = LlmAgent(
    name="data_analyst",
    model=model,
    description="Performs data analysis and generates insights",
    instruction="You are a data analyst...",
)

# Method 1: Provide custom transfer rules
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

# Method 2: Use default transfer rules (do not provide transfer_instruction)
transfer_agent = TransferAgent(
    knot_agent,
    model=model,
    sub_agents=[data_analyst],
)
```

The default rules automatically analyze the target Agent's results:
- If the content requires a child Agent's specialized capability (matching the child Agent's description), it will transfer
- If the content contains errors or incomplete information, it will consider transferring
- If the content is complete and satisfactory, it will not transfer
- Selects the most suitable Agent based on child Agent descriptions

#### Configuration Parameters

| Parameter | Type | Required | Description |
|------|------|------|------|
| `agent` | BaseAgent | Yes | The target Agent; TransferAgent will enable transfer capability for this Agent |
| `model` | Union[str, LLMModel, Callable] | Yes | The LLM model used for transfer decisions (only used when `sub_agents` is provided) |
| `sub_agents` | List[AgentABC] | No | List of child Agents; when provided, TransferAgent analyzes the target Agent's results and decides whether to transfer |
| `transfer_instruction` | str | No | Custom transfer rules; when empty but `sub_agents` is provided, default rules are used automatically |

- **`agent`**: The target Agent, required. TransferAgent wraps this Agent to enable transfer capability
- **`model`**: The LLM model, required. Used to analyze the target Agent's results and decide whether to transfer (only used when `sub_agents` is provided)
- **`sub_agents`**: Optional. When provided, TransferAgent will:
  1. Invoke the target Agent and collect results
  2. Use the LLM to analyze results
  3. Decide whether to transfer to a child Agent based on `transfer_instruction` (or default rules)
- **`transfer_instruction`**: Optional. Custom transfer rules, only effective when `sub_agents` is provided. When empty, a default general-purpose rule is used automatically

#### Use Cases

- **Scenario 1 (Acting as sub_agent)**: Without providing `sub_agents`, TransferAgent directly transfers control to the target Agent
- **Scenario 2 (Forwarding to other sub_agents)**: When `sub_agents` is provided, TransferAgent analyzes the target Agent's results and decides whether to transfer to a child Agent. `transfer_instruction` is optional — when not provided, default rules are used

#### Agent Naming

TransferAgent's name is automatically generated in the format `transfer_{target_agent_name}`. For example:
- If the target Agent's name is `knot-assistant`, the TransferAgent's name will be `transfer_knot-assistant`
- If the target Agent's name is `custom-agent`, the TransferAgent's name will be `transfer_custom-agent`

#### Notes

1. **Model requirement**: `model` is a required parameter, used for transfer decisions (only used when `sub_agents` is provided)
3. **Default rules**: If `sub_agents` is provided without `transfer_instruction`, a default general-purpose transfer rule is used automatically
4. **Transfer rule design**: In Scenario 2, it is recommended to provide clear `transfer_instruction` to help the LLM accurately determine when and where to transfer
5. **Target Agent restrictions**: The target Agent cannot be the TransferAgent itself, nor can it be in the `sub_agents` list (it will be automatically removed if present)


## State Passing and Context Management

Multi Agents passes information between Agents through the `output_key` mechanism. Each Agent can save its output to a state variable, and subsequent Agents reference it using the `{var}` syntax:

### How State Variables Work

1. **Storage**: When an Agent has `output_key` set, its output is automatically saved to the session's state dictionary
2. **Reference**: Use the `{variable_name}` syntax in instructions to insert state variable values
3. **Scope**: State variables are shared across the entire session and accessible to all Agents
4. **Override**: If multiple Agents use the same `output_key`, the later-executing Agent will overwrite the previous value

### State Variable Best Practices

- **Naming conventions**: Use descriptive variable names, such as `extracted_content`, `quality_review`, etc.
- **Avoid conflicts**: Ensure different Agents have unique `output_key` values, unless intentional overwriting is desired
- **Type consistency**: Maintain consistent data types for state variables to facilitate processing by subsequent Agents
- **Documentation**: Clearly describe the expected state variable format in instructions

```python
# An Agent can save its output to a state variable
content_analyzer = LlmAgent(
    name="content_analyzer",
    model="deepseek-v3-local-II",
    instruction="Analyze the input content and provide detailed insights.",
    output_key="analysis_result",  # Save output to a state variable
)

# Subsequent Agents can reference previous results via templates
report_writer = LlmAgent(
    name="report_writer", 
    model="deepseek-v3-local-II",
    instruction="""Generate a comprehensive report based on the analysis:

**Analysis Results:**
{analysis_result}

Create a structured report with summary, key findings, and recommendations.""",  # Reference the state variable
    output_key="final_report"
)
```

### Advanced State Management

In addition to the basic `output_key` mechanism, state can also be managed through the following approaches:

```python
# Access the full session state at runtime
session = await session_service.get_session(
    app_name=APP_NAME, 
    user_id=USER_ID, 
    session_id=session_id
)

# Access all state variables
if session and session.state:
    analysis_result = session.state.get("analysis_result")
    quality_review = session.state.get("quality_review")
    security_review = session.state.get("security_review")
```

### Removing Previous Agent Messages
In a session, sometimes messages from previously executed Agents have no relevance to the current Agent's execution. You can set the `include_previous_history` parameter to prevent messages from previous Agents from being concatenated into the current Agent's context, as shown below:

```python
LlmAgent(
    ...,
    include_previous_history=False,
)
```

## Complete Examples

For complete examples of various Multi Agents patterns, see:

### Core Collaboration Pattern Examples
- Chain Agent Example: [examples/multi_agent_chain/run_agent.py](../../../examples/multi_agent_chain/run_agent.py)
- Parallel Agent Example: [examples/multi_agent_parallel/run_agent.py](../../../examples/multi_agent_parallel/run_agent.py)
- Cycle Agent Example: [examples/multi_agent_cycle/run_agent.py](../../../examples/multi_agent_cycle/run_agent.py)
- Compose Pattern Example: [examples/multi_agent_compose/run_agent.py](../../../examples/multi_agent_compose/run_agent.py)
- Sub Agents Example: [examples/multi_agent_subagent/run_agent.py](../../../examples/multi_agent_subagent/run_agent.py)

### Auxiliary Feature Examples
- AgentTool Example: [examples/agent_tools/run_agent.py](../../../examples/agent_tools/run_agent.py)
- TransferAgent Example: [examples/transfer_agent/run_agent.py](../../../examples/transfer_agent/run_agent.py)
