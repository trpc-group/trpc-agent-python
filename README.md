# tRPC-Agent

tRPC-Agent是一个多Agent应用开发框架，与tRPC-Python框架及生态打通，提供开箱即用的Agent，可以快速开发、调试、部署、调用Agent服务。

<img src="docs/images/architecture.png" alt="tRPC-Agent Architecture" width="800"/>

## 核心特性

- **🤖 Agent开发**：
    - **LlmAgent**：集成Agent通用的工作流及组件，简单配置Prompt、Agent使用的Tool即可使用；
    - **Workflow编排**：默认支持LangGraphAgent，用户可以用LangGraph来定制单Agent的复杂工作流；
    - **多Agent编排**：
        - **预设工作流编排**：Chain模式依次执行Agent、Parallel模式并行执行Agent、Cycle模式Loop执行Agent；
        - **Agent自动编排**：以树的形式组织Agent的交互方式，Agent能通过配置sub_agents将控制流交给子Agent；
        - **CustomAgent自定义编排**：用户可以自由组合预设工作流，也可以按自定义逻辑实现Agent的编排；
- **🧩 Agent组件**：
    - **Model组件**：提供对OpenAI-Like的模型调用，正在接入其他协议的模型；
    - **Memory组件**：支持State（Agent间共享数据）、Session（多轮对话）、Memory（跨Session的记忆）三种组件；
    - **Tool组件**：支持接入用户自定义Tool/ToolSet/MCP Server，支持以Agent作为Tool与CodeExecutor的能力；
    - **Planner组件**：可控制模型的规划行为，默认提供ReAct Planner组件，为非思考模型引入思考能力；
    - **Knowledge组件**：可接入用户知识库，默认提供基于**LangChain的RAG组件**；
- **🔌 Agent埋点**：
    - **Filter**：提供**洋葱模型**的Filter接入机制，方便用户定义公用组件在模型调用前后、工具调用前后、Agent调用前后进行处理；
    - **Callbacks**：支持单个Agent配置Callbacks，方便用户侵入到特定Agent的执行流程；
- **🚀 Agent服务化**：
    - **Debug Server**：Agent开发好后，支持一键拉起Debug Server在网页对话调试Agent；
    - **tRPC-A2A**：以tRPC-Python部署google-a2a协议服务，提供完整的服务接口实现及Naming/Config/Tracing/Metrics等能力，用户只需关注Agent的开发；
    - **Tracing**：框架已接入OpenTelemetry，打通了Galileo Tracing；
    - **多节点部署**：目前支持基于Redis的多节点部署；

## 安装方式

### 环境要求

Python版本: 3.10+ (推荐 python3.12)

建议直接使用下面的IT云研发环境:

【tlinux3-trpc-python3.12】，点击链接即可使用模板创建云研发环境：https://devc.woa.com/open/env?templateUid=template-51ocxanvsj4e&bs=devc

或者镜像环境：

```sh
# python3.10
mirrors.tencent.com/todacc/trpc-agent-compile_tlinux3.2_py_310:0.1.1
# python3.12
mirrors.tencent.com/todacc/trpc-agent-compile_tlinux3.2_py_312:0.1.0
```

### 安装

使用 `pip` 安装：
```bash
pip install trpc-agent_sdk
# 如果期望安装带有redis特性的 session，采用扩展安装，如下：
# pip install trpc-agent_sdk[redis]
```

## 快速体验

如果你不想编写任何代码，我们也提供了一个例子 [examples/quickstart](./examples/quickstart/) 供你快速体验。

如果你想体验Agent的开发过程，请按下面的指引操作。

### 操作步骤

#### 1. 创建目录及编写agent文件

```bash
mkdir trpc_test_agents
touch trpc_test_agents/test_agent.py
```

#### 2. 打开trpc_test_agents/test_agent.py文件，编写agent逻辑

```python
import os
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import FunctionTool

async def get_weather(city: str) -> str:
    """Get the weather of a city"""
    return f"The weather of {city} is sunny."

root_agent = LlmAgent(
    name="test_agent",
    description="A helpful assistant for conversation",
    model=OpenAIModel(
        model_name="deepseek-v3-local-II",
        api_key=os.environ.get("API_KEY", ""),
        base_url="http://v2.open.venus.woa.com/llmproxy", # 此处用的是内部代理的deepseek模型
    ),
    instruction="You are a helpful assistant. Answer user questions.",
    tools=[FunctionTool(get_weather)],
)
```

#### 3. 启动Debug Server以调试Agent

注意，如果系统没有npm，linux系统下可以执行如下命令安装：
```bash
wget https://nodejs.org/dist/v22.17.0/node-v22.17.0-linux-x64.tar.xz
tar -xvf node-v22.17.0-linux-x64.tar.xz
ln -s node-v22.17.0-linux-x64/bin/node /usr/bin/node
ln -s node-v22.17.0-linux-x64/bin/npm /usr/bin/npm
```

执行如下命令启动Debug Server：
```bash
# 获取本机IP地址
export HOST_IP=$(hostname -I | awk '{print $1}')
echo "HOST_IP: $HOST_IP"

# 启动Web UI Server
git clone https://github.com/google/adk-web.git
cd adk-web
npm install
# --backend 填写 tRPC Agent Debug Server的地址
#   Web UI Server会通过这个地址转发流量到tRPC Agent Debug Server
npm run serve --backend=http://$HOST_IP:8000

# 启动tRPC Agent Debug Server，注意：API_KEY需要替换为你的DeepSeek API Key
export API_KEY=your_deepseek_api_key
python -m trpc_agent.server.debug.server --agents path-to-trpc_test_agents --host $HOST_IP  --port 8000
```

#### 4. 打开浏览器访问：http://$HOST_IP:4200 ，如下所示，即可愉快的和你的Agent对话了

<img src="docs/images/debug_ui.png" alt="tRPC Agent Debug UI" width="800"/>

### 使用其他模型体验

如果你没有DeepSeek官方的API Key，可以替换成[腾讯太极机器学习平台](https://taiji.woa.com/web-llm/web/home_api)的DeepSeek模型，申请API Key之后，需要稍微修改下Agent的构造，如下所示

```python
import os
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import FunctionTool

async def get_weather(city: str) -> str:
    """Get the weather of a city"""
    return f"The weather of {city} is sunny."

root_agent = LlmAgent(
    name="test_agent",
    description="A helpful assistant for conversation",
    model=OpenAIModel(
        model_name="DeepSeek-R1-Online",
        api_key="your_taiji_api_key",
        base_url="http://api.taiji.woa.com/openapi",
        client_args={"default_headers": { "Accept": "*/*" }}
    ),
    instruction="You are a helpful assistant. Answer user questions.",
    tools=[FunctionTool(get_weather)],
    generate_content_config=types.GenerateContentConfig(
        http_options=types.HttpOptions(extra_body={"openai_infer": True})
    ),
)
```

## 更多文档

见 [开发指南](./docs/README.md)
