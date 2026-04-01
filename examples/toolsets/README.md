# ToolSet工具集示例

本示例演示如何使用 `BaseToolSet` 创建一个天气工具集，并根据用户权限动态返回可用工具。

## 核心概念

- **ToolSet（工具集）**：将多个相关工具组织在一起，统一管理生命周期（初始化、获取、清理）。
- **动态工具筛选**：通过 `get_tools()` 方法，根据 `InvocationContext` 中的用户状态动态返回不同的工具列表。
  - 普通用户（basic）：仅可使用「查询当前天气」工具。
  - VIP 用户：可使用「查询当前天气」和「天气预报」两个工具。

## 环境要求

Python版本: 3.10+（强烈建议使用3.12）

## 在trpc-agent-python框架代码下如何运行此代码示例

1. 下载trpc-agent-python代码并安装

```bash
git clone https://git.woa.com/trpc-python/trpc-python-agent/trpc-agent
cd trpc-agent
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e .
```

2. 运行此代码示例

在 `.env` 文件中设置使用 LLM 相关的变量（也可以通过export设置）:
- TRPC_AGENT_API_KEY
- TRPC_AGENT_BASE_URL
- TRPC_AGENT_MODEL_NAME

然后运行下面的命令：

```bash
cd examples/tool_with_toolset/
python3 run_agent.py
```

## 项目结构

```
tool_with_toolset/
├── .env                # 环境变量配置
├── README.md           # 说明文档
├── run_agent.py        # 入口脚本
└── agent/
    ├── __init__.py     # 包初始化
    ├── agent.py        # Agent 创建与配置
    ├── config.py       # 模型配置（从环境变量读取）
    ├── prompts.py      # Agent 指令提示词
    └── tools.py        # WeatherToolSet 工具集定义
```

## 核心代码解释

### 1. 定义工具集并注册工具

继承 `BaseToolSet`，在 `initialize()` 中将工具函数包装为 `FunctionTool` 并注册：

```python
class WeatherToolSet(BaseToolSet):

    def initialize(self) -> None:
        super().initialize()
        self.tools = [
            FunctionTool(self.get_current_weather),   # 查询当前天气
            FunctionTool(self.get_weather_forecast),   # 天气预报
        ]
```

### 2. 动态工具筛选

重写 `get_tools()` 方法，根据 session 中的 `user_type` 状态决定返回哪些工具：

```python
async def get_tools(self, invocation_context=None):
    user_type = invocation_context.session.state.get("user_type", "basic")

    if user_type == "vip":
        return self.tools          # VIP 用户：返回全部工具
    else:
        return self.tools[:1]      # 普通用户：仅返回「查询当前天气」
```

每次 Agent 调用工具前，框架会调用 `get_tools()` 获取当前可用的工具列表，从而实现按用户身份动态控制工具权限。

### 3. 创建 session 时注入用户状态

在 `run_agent.py` 中，通过 `state` 参数将用户类型写入 session，供 `get_tools()` 读取：

```python
await session_service.create_session(
    app_name=app_name,
    user_id=user_id,
    session_id=session_id,
    state={"user_type": user_type},   # "basic" 或 "vip"
)
```

### 4. 将工具集挂载到 Agent

在 `agent.py` 中，将 `WeatherToolSet` 实例作为工具传给 Agent：

```python
weather_toolset = WeatherToolSet()
weather_toolset.initialize()

agent = LlmAgent(
    name="weather_toolset_agent",
    model=_create_model(),
    instruction=INSTRUCTION,
    tools=[weather_toolset],
)
```
