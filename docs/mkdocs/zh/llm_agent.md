
# LLM Agent

LlmAgent 封装了AI Agent的通用实现，它使用LLM作为大脑，通过工具调用与外部系统交互，可以实现复杂任务的自动处理。

与按固定流程执行的Agent不同，LlmAgent根据LLM动态理解指令和上下文，自主决定执行步骤、工具调用或是否交由其他Agent处理，比如RAG里，一般会先召回文档，然后再基于文档生成回复，而LlmAgent可能识别到用户问题与知识库不相关，直接返回"问题不相关"等回复，而不会走RAG的流程。

要创建一个 `LlmAgent`，需要配置 Agent 的基础信息及其使用的工具。

## 配置 Agent 的基础信息

如下所示，在 `trpc-agent` 中，一个 Agent 由以下信息标识：
- `name`（必填）：Agent 名称，用于唯一标识一个 Agent；
- `description`（选填）：Agent 描述，在多 Agent 场景下用于向其他 Agent 提供身份信息；
- `model`（必填）：Agent 的“大脑”；不同场景（对话/代码生成/复杂问题处理等）需要不同类型的模型；

```python
LlmAgent(
    name="weather_agent",
    description="A helpful assistant for query weather",
    model="deepseek-chat",
    instruction="...", # 将在下一节介绍
)
```

在运行示例之前，需要设置以下环境变量（也可以通过 `.env` 文件配置）：

```bash
export TRPC_AGENT_API_KEY="your-api-key"
export TRPC_AGENT_BASE_URL="your-base-url"
export TRPC_AGENT_MODEL_NAME="your-model-name"
```

更多 tRPC-Agent 支持的模型配置及不同模型如何实例化、传参等，请参考[模型调用](./model.md)文档。

## 配置 Agent 的指令（instruction）

`instruction` 参数是塑造 `LlmAgent` 行为最关键的配置项。它是一个字符串（或返回字符串的函数），用于告诉Agent：

* 它的核心任务或目标
* 它的个性或角色定位（如"你是一个友善的助手"、"你是一个专业的技术顾问"）
* 行为约束（如"只回答关于X的问题"、"永远不要透露Y"）
* 如何以及何时使用其工具。你应该解释每个工具的用途，以及在什么情况下应该调用它们
* 期望的输出格式（如"以JSON格式回复"、"提供要点列表"）

**有效指令的建议：**

* **清晰具体**：避免歧义。明确说明期望的操作和结果
* **使用Markdown**：通过标题、列表等提高复杂指令的可读性
* **提供示例（Few-Shot）**：对于复杂任务或特定输出格式，在指令中直接包含示例
* **指导工具使用**：不要只是列出工具；解释_何时_以及_为什么_Agent应该使用它们

**状态变量（占位符变量）**：

可以通过状态变量`{var}`在`instruction`中实现会话状态注入

* 指令字符串是一个模板，你可以使用 `{var}` 语法将动态值插入到指令中，注入会话状态
* `{var}` 用于插入会话状态中名为 var 的状态变量的值；若状态变量不存在，trpc_agent 将会忽略
* `{var?}` 为可选占位符；若不存在，替换为空字符串

```python
# 示例：添加指令
LlmAgent(
        name="weather_agent",
        description="A professional weather query assistant that can provide real-time weather and forecast information.",
        model="deepseek-chat",
        # 使用状态变量进行模板替换 - 演示 {var} 语法
        instruction="""
        你是一个专业的天气查询助手，为 {user_name} 提供服务。

        **当前用户信息：**
        - 用户名：{user_name}
        - 所在城市：{user_city}

        **你的任务：**
        - 理解用户的天气查询需求
        - 使用合适的工具获取天气信息
        - 提供清晰、有用的天气信息和建议

        **可用工具：**
        1. `get_weather`: 获取当前天气信息
        2. `get_weather_forecast`: 获取多日天气预报

        **工具使用指南：**
        - 当用户询问当前天气时，使用 `get_weather`
        - 当用户询问未来几天天气时，使用 `get_weather_forecast`
        - 如果查询不明确，可以同时使用两个工具

        **回复格式：**
        - 提供准确的天气信息
        - 根据天气情况给出合理的出行或穿衣建议
        - 保持友好、专业的语调
        - 如果用户没有指定城市，优先查询 {user_city} 的天气

        **限制：**
        - 只回答天气相关问题
        - 如果询问其他问题，请礼貌地重定向到天气话题
        """,
)
    # tools 将在下一节添加
```

`LlmAgent` 也可以配置 `output_key`，将 Agent 输出保存到状态变量中，以供模板使用（通常用于跨 Agent 交互场景），如下所示：

```python
LlmAgent(
    name="weather_agent",
    description="A helpful assistant for query weather",
    model="deepseek-chat",
    instruction="...",
    output_key="weather_info",
)
```

## 配置 Agent 的工具（tools）

工具是Agent与外部世界交互的方式。它们可以是API调用、数据库查询、文件操作或任何可以用Python函数表示的操作。 目前支持多种工具包含：

- Function: 本地函数调用，支持函数形参（string、integer、float、list、dict、boolean, pydantic.BaseModel）
- AgentTool: 允许将 Agent 包装成 Tool，实现将一个 Agent 的输出，作为另一个 Agent 的输入
- McpTool: 集成外部 MCP 服务器工具的机制。通过MCP协议，Agent 可以调用其他进程提供的工具

更多工具参考：[tools](./tool.md)

```python
from trpc_agent_sdk.tools import FunctionTool

# 定义获取天气的工具函数
def get_weather_report(city: str) -> dict:
    """获取指定城市的天气信息"""
    # 模拟天气 API 调用
    weather_data = {
        "北京": {
            "temperature": "25°C",
            "condition": "Sunny",
            "humidity": "60%"
        },
        "上海": {
            "temperature": "28°C",
            "condition": "Cloudy",
            "humidity": "70%"
        },
        "广州": {
            "temperature": "32°C",
            "condition": "Thunderstorm",
            "humidity": "85%"
        },
    }
    return weather_data.get(city, {"temperature": "Unknown", "condition": "Data not available", "humidity": "Unknown"})


def get_weather_forecast(city: str, days: int = 3) -> list:
    """获取指定城市的多日天气预报"""
    # 模拟预报数据
    return [
        {
            "date": "2024-01-01",
            "temperature": "25°C",
            "condition": "Sunny"
        },
        {
            "date": "2024-01-02",
            "temperature": "23°C",
            "condition": "Cloudy"
        },
        {
            "date": "2024-01-03",
            "temperature": "20°C",
            "condition": "Light rain"
        },
    ][:days]


def create_agent():
    """创建一个天气查询 Agent，用于演示 LLM Agent 的各项能力。"""

    # 创建工具
    weather_tool = FunctionTool(get_weather_report)
    forecast_tool = FunctionTool(get_weather_forecast)

    return LlmAgent(
        name="weather_agent",
        description="A professional weather query assistant that can provide real-time weather and forecast information.",
        model="deepseek-chat",
        instruction=INSTRUCTION,  # INSTRUCTION和上述小节相同
        tools=[weather_tool, forecast_tool],
        # 配置生成参数
        generate_content_config=GenerateContentConfig(
            temperature=0.3,  # 降低随机性，使响应更加确定
            top_p=0.9,
            max_output_tokens=1500,
        ),
        # 启用 Planner 以增强推理能力（默认注释掉），取消下面一行的注释，为模型赋予推理能力，使其在生成响应之前先进行推理
        # planner=PlanReActPlanner(),
    )

```

**完整示例：**
- [examples/llmagent/run_agent.py](../../../examples/llmagent/run_agent.py) - 基础天气查询Agent示例

## 会话管理

当前 LLM Agent 可在需要时根据不同场景控制其对其他 Agent 生成的消息以及历史会话消息的可见性，可通过相关选项进行配置；在与 model 交互时仅将可见的内容输入给模型。下面介绍一些会话管理策略：

### 使用预设会话管理策略

LlmAgent提供了多种参数来控制会话历史的可见性，帮助您在不同场景下优化Agent的上下文管理：

- `max_history_messages` 和 `message_timeline_filter_mode` 用于控制Agent对完整会话历史的可见性
- `message_branch_filter_mode` 用于在多Agent场景下，控制其中一个Agent对其他Agent消息的可见性

#### max_history_messages

`max_history_messages` 参数用于限制传递给模型的历史消息数量，有助于控制长对话场景下的token使用量：

```python
from trpc_agent_sdk.agents import LlmAgent

agent = LlmAgent(
    name="history_demo",
    description="Agent demonstrating history control",
    ...,
    max_history_messages=max_history_messages, # 只保留最近 max_history_messages 条历史消息
)
```

**参数说明：**
- `max_history_messages=0`（默认值）：不限制历史消息数量，包含所有经过过滤的消息
- `max_history_messages=N`（N > 0）：只包含最近的 N 条历史消息（在其他过滤之后应用）

**使用场景：**
- 长对话场景下控制token使用量
- 需要 Agent 只关注最近若干条历史消息的场景
- 防止上下文过长导致的性能问题

**注意事项：**
- 此策略在`message_timeline_filter_mode` 和`message_branch_filter_mode`过滤**之后**应用
- 只保留最近的N条

#### message_timeline_filter_mode

`message_timeline_filter_mode` 参数控制Agent在多轮对话下，历史消息的可见性：

```python
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.agents import TimelineFilterMode

agent = LlmAgent(
    name="timeline_demo",
    description="Agent demonstrating timeline filtering",
    ...,
    message_timeline_filter_mode=timeline_mode,
)
```

**可选值：**
- `TimelineFilterMode.ALL`（默认）：包含多轮对话的消息
- `TimelineFilterMode.INVOCATION`：只包含当前调用（`runner.run_async()`）生成的消息

**使用场景：**
- `ALL`：需要Agent记住完整对话历史
- `INVOCATION`：需要Agent只处理当前请求，忽略历史上下文

#### message_branch_filter_mode

在多Agent场景下,`message_branch_filter_mode` 参数控制当前Agent对其他Agent消息的可见性。每个Agent在执行时都有一个唯一的branch标识(如 `CustomerService.TechnicalSupport.DatabaseExpert`),通过branch过滤可以精确控制消息的可见范围,举例来说,有下面四个Agent:

```python
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.agents import BranchFilterMode

# 数据库专家Agent - 处理数据库相关问题
database_expert = LlmAgent(
    name="DatabaseExpert",
    description="数据库专家,诊断和解决数据库问题",
    instruction="你是数据库专家,专注于数据库性能和故障排查",
    message_branch_filter_mode=BranchFilterMode.PREFIX,  # 只看到相关层级
)

# 技术支持Agent - 处理技术问题,可以调用数据库专家
technical_support = LlmAgent(
    name="TechnicalSupport",
    instruction="你是技术支持专员,处理技术问题",
    message_branch_filter_mode=BranchFilterMode.PREFIX,  # 只看到相关层级
    sub_agents=[database_expert],
)

# 账单支持Agent - 完全隔离,只看到自己的消息
billing_support = LlmAgent(
    name="BillingSupport",
    instruction="你是账单支持专员,处理账单问题",
    message_branch_filter_mode=BranchFilterMode.EXACT,  # 完全隔离
)

# 客户服务Agent - 无需注意其他Agent的历史，只需在意当前请求被分发到哪里
customer_service = LlmAgent(
    name="CustomerService",
    instruction="你是客户服务协调员,根据用户请求路由到合适的部门",
    message_branch_filter_mode=BranchFilterMode.EXACT,  # 完全隔离
    sub_agents=[technical_support, billing_support],
)
```

**可选值：**

1. **`BranchFilterMode.ALL`(默认)**:包含所有Agent的消息
   - 使用场景：Agent需要与模型交互，并需要同步所有Agent生成的有效内容消息
   - 示例：需要跨部门信息共享的场景

2. **`BranchFilterMode.PREFIX`**：前缀匹配，包含相关层级的消息
   - 使用场景：希望传递当前Agent及相关上下游Agent生成的消息
   - 示例：技术支持Agent（branch: `CustomerService.TechnicalSupport`）可以看到：
     - 父级Agent `CustomerService` 的消息
     - 自己 `TechnicalSupport` 的消息
     - 子级Agent `DatabaseExpert` 的消息
     - 但**不能**看到兄弟Agent `BillingSupport` 的消息

3. **`BranchFilterMode.EXACT`**：精确匹配，只包含当前Agent的消息
   - 使用场景：Agent需要与模型交互，但只使用自己生成的消息，实现完全隔离
   - 示例：客户服务协调员只需要看见转发的消息，而不需要看见其他Agent的消息

**完整示例：**
- [examples/llmagent_with_max_history_messages/run_agent.py](../../../examples/llmagent_with_max_history_messages/run_agent.py) - max_history_messages 示例
- [examples/llmagent_with_timeline_filtering/run_agent.py](../../../examples/llmagent_with_timeline_filtering/run_agent.py) - message_timeline_filter_mode 示例  
- [examples/llmagent_with_branch_filtering/run_agent.py](../../../examples/llmagent_with_branch_filtering/run_agent.py) - message_branch_filter_mode 示例

### 设置历史会话内容

用户可能会期望设置历史会话内容到 agent 服务中，使用如下：

构造用户历史记录：
```python
from trpc_agent_sdk.sessions import HistoryRecord

def make_user_history_record() -> HistoryRecord:
    """构造用户历史记录，模拟用户之前的对话历史"""
    record: dict[str, str] = {
        "What's your name?": "My name is Alice",
        "what is the weather like in paris?": "The weather in Paris is sunny ...",
        "Do you remember my name?": "It seems I don't have your name stored ...",
    }

    history_record = HistoryRecord()
    for query, answer in record.items():
        history_record.add_record(query, answer)
    return history_record
```

编写提示词，指示 Agent 优先从历史会话中查找答案：
```python
INSTRUCTION = """你是一个问答助手
**你的任务：**
- 理解提问，并给出友好回答
- 如果可以从历史会话中查询相关的数据，优先从历史会话中查找，减少大模型的工具调用；如果历史会话中没有，那么就去工具中查询
"""
```

运行时将历史记录与用户提问一起注入：
```python
from trpc_agent_sdk.configs import RunConfig
from trpc_agent_sdk.types import Content, Part

for query in demo_queries:
    # 获取历史记录对象，并根据当前 query 构建匹配的上下文内容
    history_record = make_user_history_record()
    history_content = history_record.build_content(query)
    user_content = Content(parts=[Part.from_text(text=query)])

    # 开启会话历史保存，使多轮对话可以累积上下文
    run_config = RunConfig(save_history_enabled=True)
    # new_message 传入 [history_content, user_content] 列表，
    # 将历史记录和用户当前提问一起注入到 Agent 的输入中
    async for event in runner.run_async(
        user_id=user_id,
        session_id=session_id,
        new_message=[history_content, user_content],
        run_config=run_config,
    ):
        ...
```

**完整示例：**
- [examples/llmagent_with_user_history/run_agent.py](../../../examples/llmagent_with_user_history/run_agent.py) - 设置历史会话内容示例

## 高级配置与控制

### GenerateContentConfig

用于调整LLM的生成行为，如temperature、top-p等参数：

```python
from trpc_agent_sdk.types import GenerateContentConfig

weather_agent = LlmAgent(
    name="weather_agent",
    model="deepseek-chat",
    instruction="...",
    tools=[weather_tool],
    generate_content_config=GenerateContentConfig(
        temperature=0.1,  # 降低随机性，获得更确定的回复
        top_p=0.95,
        max_output_tokens=1000,
    )
)
```

### ToolPrompt

有些时候，LLM 模型服务不支持 FunctionCall（例如微调模型场景）。为了让不支持 FunctionCall 的 LLM 也具备该能力，框架支持通过 `ToolPrompt` 将工具定义注入到 `system_prompt` 中，再从 LLM 输出中解析特定文本来实现工具调用。

使用方法很简单：只需要为 `OpenAIModel` 增加 `add_tools_to_prompt` 选项即可启用此功能。

```python
OpenAIModel(
    model_name="deepseek-chat",
    base_url="https://api.deepseek.com/v1",
    api_key=os.environ.get("API_KEY", ""),
    add_tools_to_prompt=True,
    # 框架提供"xml"和"json"两种注入tool_prompt的方式，如果不填tool_prompt，默认使用"xml"
    # tool_prompt="xml",
),
```

注意到，框架提供了 `tool_prompt` 让用户选择工具定义转成文本的格式，默认提供 xml 和 json 的转换格式，可以通过传入不同的字符串来切换。

`tool_prompt` 除了支持传入字符串之外，还可以传入继承 `ToolPrompt` 的类，为用户自定义工具转换的文本提供了扩展，如下所示，将会使用框架提供的 `XmlToolPrompt`：

```python
from trpc_agent_sdk.models.tool_prompt import XmlToolPrompt
OpenAIModel(
    model_name="deepseek-chat",
    base_url="https://api.deepseek.com/v1",
    api_key=os.environ.get("API_KEY", ""),
    add_tools_to_prompt=True,
    # 注意，此处传入类型而不是类实例，因为流式解析是基于chunk解析的，是有状态的
    tool_prompt=XmlToolPrompt,
),
```

用户可以自定义 `CustomToolPrompt` 实现工具到文本的转换，各个接口用途如下所示。

实现细节可以参考 [XmlToolPrompt](../../../trpc_agent_sdk/models/tool_prompt/_xml.py)。

```python
from trpc_agent_sdk.models.tool_prompt import ToolPrompt

class CustomToolPrompt(ToolPrompt):
     @override
    def build_prompt(self, tools: List[Tool]) -> str:
        """Build a prompt string from a list of tools.

        Args:
            tools: List of Tool objects to convert to prompt text

        Returns:
            String representation of tools for inclusion in system prompt
        """
        pass

    @override
    def parse_function(self, content: str) -> List[FunctionCall]:
        """Parse function calls from complete content.

        Args:
            content: Complete content string containing function calls

        Returns:
            List of FunctionCall objects parsed from content

        Raises:
            ValueError: If content cannot be parsed as function calls
        """
        pass
```

**完整示例：**
- [examples/llmagent_with_tool_prompt/run_agent.py](../../../examples/llmagent_with_tool_prompt/run_agent.py) - ToolPrompt 使用示例

### PlanReActPlanner

Planner 能定制 Agent 的规划过程，它本质上会介入 LLM 输入与输出的内容：在输入侧，Planner 可以注入与规划有关的信息；在输出侧，Planner 可以对规划结果进行处理，例如将 LLM 输出中的工具调用文本转换为 trpc_agent 的工具结构，从而在不支持原生工具调用的模型上也能使用工具调用。

框架提供了PlanReActPlanner，为LLM输入注入Reasoning的指令，能让不支持Reasoning的模型也能具备此能力。

```python
from trpc_agent_sdk.planners import PlanReActPlanner

weather_agent = LlmAgent(
    name="weather_agent",
    model="deepseek-chat",
    instruction="...",
    planner=PlanReActPlanner(),  # 启用规划功能
)
```

### 开启思考模式

当前有很多模型支持思考模式，框架通过 `BuiltInPlanner` 和 `ThinkingConfig` 来控制思考行为。思考模式允许模型在生成最终回复之前进行内部推理，提高回答质量。

使用思考模式需要配置以下组件：

- `BuiltInPlanner`: 内置规划器，支持思考功能
- `ThinkingConfig`: 思考配置，控制思考行为的参数
  - `include_thoughts`: 是否在输出中包含思考过程，默认为 False
  - `thinking_budget`: 思考过程的token预算，必须小于 `max_output_tokens`

```python
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel, OpenAIModel
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.types import GenerateContentConfig
from trpc_agent_sdk.planners import BuiltInPlanner
from trpc_agent_sdk.types import ThinkingConfig


def _create_model() -> LLMModel:
    """创建模型"""
    api_key, url, model_name = get_model_config()
    model = OpenAIModel(
        model_name=model_name,
        api_key=api_key,
        base_url=url,
        # 有两种场景，开启add_tools_to_prompt能提高Agent的生成效果:
        # 1. 当思考模型不支持工具调用时，
        #    可以启用ToolPrompt框架从LLM生成的文本中解析工具调用能力。
        # 2. 当思考模型在推理过程中调用工具时，
        #    如果LLM模型服务无法返回工具调用的JSON格式，也可以启用ToolPrompt。
        #    这将促使LLM模型在正文中输出工具调用的特殊文本，
        #    从而提高工具调用成功的概率。
        # 你可以取消下面的注释，以使用ToolPrompt。
        # add_tools_to_prompt=True,
        )
    return model


def create_agent():
    """创建天气查询Agent，展示思考模式的使用。"""

    return LlmAgent(
        name="weather_agent",
        description="专业的天气查询助手，能够提供实时天气和预报信息。",
        model=_create_model(),
        instruction=INSTRUCTION,
        # 注意：thinking_budget 必须小于 max_output_tokens
        generate_content_config=GenerateContentConfig(max_output_tokens=10240, ),
        # 模型必须是思考模型，才能使用此Planner；非思考模型此项配置将不会生效。
        planner=BuiltInPlanner(thinking_config=ThinkingConfig(
            include_thoughts=True,
            thinking_budget=2048,
        ), ),
    )

root_agent = create_agent()
```

注意事项：

- `thinking_budget`: 必须小于 `generate_content_config` 中的 `max_output_tokens`
- `include_thoughts`: 设置为 True 时，用户可以看到模型的思考过程；设置为 False 时，只显示最终结果
- 只有支持思考的模型才能使用此功能，目前支持的模型包括：deepseek-reasoner、glm-4.5-fp8 等
- 对于本身就带有思考特性的模型（如 qwen3-next-80b-a3b-thinking），也可以使用此配置来控制思考行为

**完整示例：**
- [examples/llmagent_with_thinking/run_agent.py](../../../examples/llmagent_with_thinking/run_agent.py) - 开启思考模式的Agent示例

### 模型创建回调函数

在某些场景下，你可能需要在每次运行时动态创建模型实例，而不是在初始化 Agent 时就固定模型。例如：
- 根据运行时配置动态调整模型参数
- 为不同的请求使用不同的 API key 或 base URL

框架支持将异步函数作为模型创建回调传递给 `LlmAgent`。该回调函数会接收来自 `RunConfig.custom_data` 的数据，并返回一个模型实例。

***除了LlmAgent之外，ClaudeAgent与TeamAgent均支持此用法。***

```python
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import OpenAIModel, LLMModel
from trpc_agent_sdk.configs import RunConfig
from trpc_agent_sdk.runners import Runner

async def create_model(custom_data: dict) -> LLMModel:
    """模型创建回调函数
    
    Args:
        custom_data: 从 RunConfig.custom_data 传递的数据
        
    Returns:
        LLMModel 实例
    """
    print(f"📦 Model creation function received custom_data: {custom_data}")
    
    return OpenAIModel(
        model_name=model_name,
        base_url="https://api.openai.com/v1",
        api_key=os.environ.get("OPENAI_API_KEY", ""),
    )

# 创建 Agent 时传入模型创建回调
weather_agent = LlmAgent(
    ...,
    model=create_model,  # 传入模型创建回调
)

# 创建 Runner
runner = Runner(
    app_name="weather_app",
    agent=weather_agent,
    session_service=session_service
)

# 通过 RunConfig 传递 custom_data
run_config = RunConfig(custom_data={"user_tier": "premium"})

async for event in runner.run_async(
    ...
    run_config=run_config
):
    # 处理Agent输出...
    pass
```

**完整示例：**
- [examples/llmagent_with_model_create_fn/run_agent.py](../../../examples/llmagent_with_model_create_fn/run_agent.py) - 模型创建回调使用示例

### 结构化输入输出

#### output_schema用法

`LlmAgent` 支持配置结构化输出（`output_schema`）。通过配置 `output_schema`，可以指定 Agent 的输出格式；通常需要在 `instruction` 中说明目标结构。

output_schema 的实现机制根据是否使用tools有两种不同的方法：

1. 当没有为LlmAgent配置tools时，会走LLM的 [Structured Output](https://platform.openai.com/docs/guides/structured-outputs) 能力（需要模型服务支持此能力），当LLM支持response_format为json_schema时（比如gpt系列），不需要在instruction里编写输出格式，框架会自动填充。而当LLM仅支持response_format为json_object时（比如deepseek），需要用户在instruction里指定要输出的json格式。
2. 当配置tools时，不会走LLM的Structured Output能力（使用Tools时不能使用Structured Output），框架将会为Agent注入set_model_response工具，以触发大模型调用这个工具设置json的输出。

```python
from pydantic import BaseModel
from typing import List

from trpc_agent_sdk.agents import LlmAgent

class UserProfileOutput(BaseModel):
    """Output schema for user profile analysis."""
    user_name: str
    age_group: str  # "young", "adult", "senior"
    personality_traits: List[str]
    recommended_activities: List[str]
    profile_score: int  # 1-10
    summary: str

profile_agent = LlmAgent(
    name="user_profile_agent",
    model="deepseek-chat",
    instruction="...",
    output_schema=UserProfileOutput,
    output_key="user_profile",
)

# 在runner之后，通过state获取Agent的结构化数据
async for event in runner.run_async(...):
    pass

session = await session_service.get_session(xxx)
user_profile_json = session.state[profile_agent.output_key]
user_profile = UserProfileOutput.model_validate_json(user_profile_json)
print(user_profile)
```

#### input_schema 用法

LlmAgent也支持结构化输入(input_schema)，一般需要配合 [AgentTool](./tool.md) 使用，AgentTool会自动校验Agent的输入/输出是否符合schema，如下所示：

```python
from pydantic import BaseModel
from typing import List, Optional

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.tools import AgentTool

class UserProfileInput(BaseModel):
    """Input schema for user profile creation."""

    name: str
    age: int
    email: str
    interests: List[str]
    location: Optional[str] = None

profile_agent = LlmAgent(
    ...,
    input_schema=UserProfileInput,
    output_schema=UserProfileOutput,
)

profile_tool = AgentTool(
    agent=profile_agent,
    skip_summarization=True,
)

main_agent = LlmAgent(
    name="main_processor",
    description="主处理Agent，可以调用用户档案分析工具",
    model="deepseek-chat",
    instruction="...",
    tools=[profile_tool],
)
```

**完整示例：**
- [examples/llmagent_with_schema/run_agent.py](../../../examples/llmagent_with_schema/run_agent.py) - 结构化输入输出Agent示例

### 设置工具并发调用

当 Agent 调用多个工具的时候，若工具存在网络耗时较多，可以采用并发调用的方式

```python
def create_agent():
    """创建配置了兴趣相关工具的 Agent"""
    # ...
    return LlmAgent(
        name="hobby_toolset_agent",
        description="演示兴趣ToolSet用法的助手",
        model=model,
        tools=[hobby_toolset],
        parallel_tool_calls=True,
        instruction="""
你是一个热爱生活的虚拟人，根据用户兴趣选择合适的工具来获取兴趣信息，并提供友好的回复。
**你的任务：**
- 如果对话中存在运行或者 sports 相关的内容，请必须调用 sports 工具，如果没有提供运动参数，默认是跑步
- 如果对话中存在电视或者 tv 相关的内容，请必须调用 watch_tv 工具，如果没有提供 tv 参数，默认是 cctv
- 如果对话中存在音乐或者 music 相关的内容，请必须调用 listen_music 工具，如果没有提供 music 参数，默认是 QQ 音乐
""",
    )
```

在 `LlmAgent` 中开启 `parallel_tool_calls` 字段可启用工具并发调用。

**完整示例：**
- [examples/llmagent_with_parallal_tools/run_agent.py](../../../examples/llmagent_with_parallal_tools/run_agent.py) - 工具并发调用示例

### 禁用框架自动注入的提示词

在某些场景下，你可能希望完全控制传递给 LLM 的提示词内容，而不希望框架自动注入额外的信息。trpc-agent 提供了两个配置项来禁用框架的自动注入行为：

#### add_name_to_instruction

默认情况下，框架会在多个地方自动注入 Agent 的名称信息：

1. **在 instruction 中注入名称**：格式为 `"You are an agent who's name is [agent_name]."`
2. **在多 Agent 协作场景中**：当一个 Agent 的输出被传递给另一个 Agent 时，会添加 `"[agent_name]: content"` 前缀

通过将 `add_name_to_instruction` 设置为 `False`，可以禁用这些行为：

```python
COORDINATOR_INSTRUCTION = """You are a customer service coordinator.
Route customer requests to the appropriate department:
- Weather questions -> WeatherAssistant
- Translation requests -> TranslationAssistant
Be concise and professional."""

coordinator = LlmAgent(
    name="Coordinator",
    description="Customer service coordinator",
    ..., 
    instruction=COORDINATOR_INSTRUCTION,
    add_name_to_instruction=False,  # 禁用自动注入，instruction 中不会被添加 "You are an agent who's name is [Coordinator]."
)
```

#### default_transfer_message

在多 Agent 场景下，当为 Agent 配置 `sub_agents` 时，框架会自动注入与子 Agent 相关的提示词。通过设置 `default_transfer_message`，可以覆盖框架默认注入的 prompt：

```python
CUSTOM_TRANSFER_MESSAGE = """When you need help from other agents:
- Call the transfer_to_agent tool
- Choose the most suitable agent based on the user's question
Available agents:
- WeatherAssistant: handles weather queries
- TranslationAssistant: handles translation requests
"""

coordinator = LlmAgent(
    name="Coordinator",
    ...,
    description="Customer service coordinator",
    instruction=COORDINATOR_INSTRUCTION,
    default_transfer_message=CUSTOM_TRANSFER_MESSAGE,  # 使用自定义转发提示词
)
```

**注意：为确保能够顺利委派子 Agent，请在提示词中明确提到 `transfer_to_agent` 工具。Agent 只有调用该工具（配置 `sub_agents` 时框架会自动注入）才能完成委派。**

这个参数有如下配置：
- None(默认)：框架将会启用自动注入
- 空字符串：将会禁用框架自动注入
- 自定义：将会使用用户设置的字符串，替代框架自动注入信息

完整示例见：[examples/llmagent_with_custom_prompt/run_agent.py](../../../examples/llmagent_with_custom_prompt/run_agent.py)。

### 下一轮对话从最后回复的Agent开始

默认情况下，多轮对话的每一轮都从入口Agent开始，但有些场景需要从最后一个回复的Agent开始，比如，委派到Agent之后，持续几轮和该Agent对话，框架提供 `RunConfig` 的选项以支持这个能力：

```python
from trpc_agent_sdk.configs import RunConfig

run_config = RunConfig(start_from_last_agent=True)
async for event in runner.run_async(...,run_config=run_config):
    ...
```

完整示例见：[examples/multi_agent_start_from_last/run_agent.py](../../../examples/multi_agent_start_from_last/run_agent.py)。

## 核心概念

### InvocationContext

`InvocationContext`表示一次 Agent 调用的上下文，包含本次执行所需的服务、会话、用户输入、运行配置和状态信息。

通常不需要手动创建 `InvocationContext`，更推荐直接使用 `Runner.run_async()`，由框架自动完成 `session`、`invocation_id`、`branch`、`run_config` 等上下文准备工作。只有在需要完全控制执行流程时，才建议直接构造 `InvocationContext` 并调用 `agent.run_async(ctx)`。

常见使用场景：

- 需要完全控制调用上下文
- 需要自行构造或复用 `session`
- 测试、自定义框架接入、底层调试场景

`InvocationContext` 中较常用的字段有：

- `session_service`：负责管理当前调用关联的会话读写、持久化和上下文装配
- `artifact_service`：用于保存和读取附件、文件等产物；未配置时不可用
- `memory_service`：用于存储和检索长期记忆、历史信息；未配置时不可用
- `invocation_id`：每次调用都会分配一个唯一标识，便于链路追踪和问题排查
- `branch`：用于多 Agent 或子 Agent 场景下隔离可见历史，避免并行分支互相污染上下文
- `agent`：表示当前实际正在执行逻辑的 Agent 实例
- `agent_context`：用于承载用户交互控制相关上下文，例如交互策略或运行时控制信息
- `user_content`：表示触发本次调用的用户输入内容
- `session`：承载当前会话本身及其状态数据
- `end_invocation`：可在回调或工具执行期间置为 `True`，用于提前终止本次调用
- `run_config`：保存本次执行使用的运行配置，例如模型、流式输出或其他运行参数

#### Invocation 状态

`InvocationContext` 提供了两类常用状态访问方式：

- `ctx.state`：可写状态，采用字典风格接口，并自动记录本轮调用的增量变更
- `ctx.session_state`：只读视图，用于安全读取当前会话状态

补充说明：

- `ctx.state`：底层会复用当前 `session.state`，但会同时把本轮修改记录到 `event_actions.state_delta`，便于框架感知和提交增量变更
- `ctx.session_state`：返回的是只读映射视图，适合在只需要读取状态时使用，可避免误修改共享状态

### Event

`Event` 是 Agent 执行过程中产出的统一事件对象，也是 `Runner.run_async()` 和 `agent.run_async()` 持续产出的内容单元。`Event` 继承自 `LlmResponse`，在响应内容之外补充了调用链和事件元信息。

常用字段包括：

- `content`：事件实际承载的内容主体，既可以是文本，也可以包含工具调用、工具结果等结构化part
- `partial`：常见于流式输出过程；为 `True` 时通常表示当前事件还不是完整结果
- `error_code` / `error_message`：用于标识事件是否处于错误状态，以及附带的错误说明
- `invocation_id`：用于把事件关联回所属调用链，一次 invocation 过程中产生的多个事件通常共享同一个 ID
- `author`：标识是谁写入了该事件，常用于区分用户消息、Agent 输出和其他参与者
- `actions`：承载本次事件附带的动作信息，例如状态增量、附件变更或其他执行副作用
- `branch`：用于多 Agent 分支隔离，帮助框架判断不同子链路之间的历史可见性
- `id` / `timestamp`：分别用于唯一标识事件和记录事件产生时间，便于排序、追踪与调试
- `visible`：设为 `False` 时，事件仍可存在于内部流程中，但 `Runner` 可以选择不向外部调用方透出

`Event` 还提供了一些便捷方法，例如：

- `is_final_response()`：不仅会判断是否没有工具调用和工具返回，还会结合 `partial`、长耗时工具调用等条件综合判断当前事件是否可视为最终响应
- `get_function_calls()`：从 `content.parts` 中提取所有 `function_call`，便于统一处理工具请求
- `get_function_responses()`：从 `content.parts` 中提取所有 `function_response`，便于读取工具执行结果
- `get_text()`：会拼接事件中所有文本 part，若没有文本内容则返回空字符串

### AgentABC

`Agent` 是所有 Agent 的抽象基类，定义了 Agent 的基础属性、子 Agent 管理能力和异步执行入口。

核心能力包括：

- `name` / `description`：Agent 的名称和能力描述
- `sub_agents`：子 Agent 列表
- `find_agent()` / `find_sub_agent()`：在 Agent 树中按名称查找 Agent
- `run_async(parent_context)`：Agent 的底层异步执行入口

对于业务代码，通常仍然推荐使用 `Runner.run_async()` 作为统一入口；`AgentABC.run_async()` 更适合高级封装、自定义 Agent 实现或测试场景。
## 其他 Agent 类型

现在你已经了解了 `trpc_agent` 中提供的 LLM Agent。可通过下面链接了解其他 Agent 类型及其用法：

- **[LangGraph Agent](./langgraph_agent.md)**：了解如何使用Graph来为Agent定制可控的工作流
- **[Multi Agents](./multi_agents.md)**：掌握Chain、Parallel、Cycle和Sub Agents的使用方法和最佳实践  
- **[Custom Agent](./custom_agent.md)**：了解如何实现完全自定义的Agent/Multi-Agent逻辑

选择最适合你需求的 Agent 类型，开始构建强大的 AI 应用！
