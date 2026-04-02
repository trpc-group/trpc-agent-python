# LLM Agent Schema 输出能力示例

本示例演示如何在 `LlmAgent` 中结合 Schema、工具调用与结构化输出，验证“工具增强 + 直接 JSON 输出 + AgentTool 封装”三种典型路径是否工作正常。

## 关键特性

- **Schema 约束输出**：通过 `UserProfileOutput` 等结构定义，验证模型输出可被稳定解析
- **工具增强分析**：在用户画像分析中调用多个工具（评分、兴趣分析、模型响应拼装）完成结构化结果
- **无工具直出 JSON**：覆盖“不开工具，仅依赖模型 JSON 输出能力”的路径
- **AgentTool 二次封装**：将画像分析能力作为 `AgentTool` 对外暴露，验证复用能力
- **单脚本多场景验证**：一次运行覆盖 3 组测试场景，便于快速回归

## Agent 层级结构说明

本例以单 Agent 为主，并演示 Agent 能力工具化复用：

```text
profile_analyzer_agent (LlmAgent, schema output)
├── tools:
│   ├── calculate_profile_score
│   ├── get_user_interests_analysis
│   └── set_model_response
└── output schema: UserProfileOutput

direct_json_agent (LlmAgent, no tools)
└── output schema: UserProfileOutput

profile_analyzer (AgentTool)
└── wraps profile_analyzer_agent for external invocation
```

关键文件：

- [examples/llmagent_with_schema/agent/agent.py](./agent/agent.py)
- [examples/llmagent_with_schema/run_agent.py](./run_agent.py)
- [examples/llmagent_with_schema/.env](./.env)

## 关键代码解释

### 1) Schema 与结构化输出

- 定义用户画像输出结构（如姓名、年龄段、性格特征、推荐活动、评分、总结）
- 运行后可直接得到结构化对象，便于后续服务消费或持久化

### 2) 工具协同分析链路

- 先调用评分工具与兴趣分析工具，再将结果汇总到最终响应
- 终态输出包含工具结果与自然语言总结，兼顾可读性与可解析性

### 3) AgentTool 复用能力

- 将画像分析 Agent 包装为 `AgentTool`
- 上层 Agent 只需传入文本或结构参数，即可复用同一分析能力

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

在 [examples/llmagent_with_schema/.env](./.env) 中配置（或通过 `export` 设置）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

### 运行命令

```bash
cd examples/llmagent_with_schema
python3 run_agent.py
```

## 运行结果（实测）

```text
🚀 Start running Agent Schema example...
🆔 Session ID: 859fb6b2...
📝 User profile:
 {
    "name": "Zhang San",
    "age": 28,
    "email": "zhangsan@example.com",
    "interests": [
        "programming",
        "fitness"
    ],
    "location": "Beijing"
}
🤖 Analysis result:
🔧 [Call tool: calculate_profile_score({'age': 28, 'interests': ['programming', 'fitness'], 'location': 'Beijing'})]
🔧 [Call tool: get_user_interests_analysis({'interests': ['programming', 'fitness']})]
📊 [Tool result: {'result': 9}]
📊 [Tool result: {'personality_traits': ['Logical thinking', 'Self-discipline'], 'recommended_activities': ['Programming marathon', 'Open source project', 'Technical conference', 'Gym', 'Outdoor activity', 'Marathon']}]
🔧 [Call tool: set_model_response({...})]
💾 Get UserProfileOutput: user_name='Zhang San' age_group='28' ... profile_score=9 ...

------------------------------------------------------------

🚀 Agent Without Tools - Direct JSON Output Demo
🆔 Session ID: 8955a036...
📝 User profile:
 {
    "name": "Wang Wu",
    "age": 35,
    "email": "wangwu@example.com",
    "interests": [
        "reading",
        "traveling",
        "photography",
        "cooking"
    ],
    "location": "Shenzhen"
}
🤖 Direct JSON analysis result: {
    "user_name": "Wang Wu",
    "age_group": "adult",
    "personality_traits": ["curious", "creative"],
    "recommended_activities": ["joining a photography club", "attending cooking workshops"],
    "profile_score": 8,
    "summary": "..."
}
💾 Get UserProfileOutput: user_name='Wang Wu' ... profile_score=8 ...

------------------------------------------------------------

🔧 AgentTool with Schema example
📝 Extract user profile information: My name is Li Si, I'm 32 years old ...
🔧 [Call tool: profile_analyzer({'name': 'Li Si', 'age': 32, 'email': 'lisi@example.com', 'interests': ['reading', 'traveling', 'photography'], 'location': 'Shanghai'})]
📊 [Tool result: {'user_name': 'Li Si', 'age_group': 'Adult', 'personality_traits': [], 'recommended_activities': [], 'profile_score': 10, 'summary': '...'}]
Here is the analysis of your profile:
- **Name**: Li Si
- **Age Group**: Adult
- **Location**: Shanghai
- **Interests**: Reading, Traveling, Photography
- **Profile Score**: 10 (indicating a basic profile setup)

------------------------------------------------------------
🎉 Successfully running all examples!
```

## 结果分析（是否符合要求）

结论：**符合本示例测试要求**。

- **Schema 输出生效**：三组场景均成功得到 `UserProfileOutput` 结构化结果
- **工具链路正确**：第一组样例出现评分/兴趣分析/响应汇总的串联调用，且结果一致
- **无工具 JSON 路径正常**：第二组样例不依赖工具，仍能输出可解析 JSON 并转换为结构对象
- **AgentTool 复用成功**：第三组样例通过 `profile_analyzer` 完成结构化分析，证明能力可复用
- **端到端执行完成**：日志以 `Successfully running all examples!` 结束，主流程无中断

## 适用场景建议

- 需要验证“结构化输出是否稳定可解析”的场景
- 需要对比“工具增强”与“纯模型直出 JSON”效果的场景
- 需要将某个 Agent 能力封装为 `AgentTool` 供上层编排复用的场景
