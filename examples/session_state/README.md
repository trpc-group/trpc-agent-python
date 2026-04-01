# Session State 示例

## 示例简介

本示例演示如何使用 **Session State** 功能来管理和共享会话状态，包括：

1. **模板引用**：在 Agent Instruction 中使用 State 进行动态模板替换
2. **工具中修改 State**：在工具函数中读取和修改 State
3. **多 Agent 协作**：使用 `output_key` 在 Agent 之间传递状态
4. **State 作用域**：演示 Session/User/App 三种不同作用域的状态管理

### 核心特性

- ✅ **动态模板**：在 Instruction 中使用 `{key}` 引用 State 中的值
- ✅ **工具访问**：通过 `tool_context.state` 在工具中读写 State
- ✅ **多 Agent 协作**：使用 `output_key` 实现 Agent 间的状态传递
- ✅ **作用域控制**：支持 Session/User/App/Temp 四种作用域
- ✅ **状态持久化**：State 随 Session 持久化（取决于 SessionService 实现）

## 环境要求

- Python 3.10+（强烈建议使用 3.12）

## 安装和运行

### 1. 下载并安装 trpc-agent

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e .
```

### 2. 配置环境变量

在 `.env` 文件中设置 LLM 相关变量（或通过 export 设置）:
- `TRPC_AGENT_API_KEY="your-api-key"`
- `TRPC_AGENT_BASE_URL="your-base-url"`
- `TRPC_AGENT_MODEL_NAME="your-model-name"`

### 3. 运行示例

```bash
cd examples/session_state/
python3 run_agent.py
```

---

## 代码说明

### 示例1：模板引用 - 在 Instruction 中使用 State

**功能**：演示如何在 Agent 的 Instruction 中使用 State 进行动态模板替换。

```python
# 创建 Agent，Instruction 中包含模板变量
instruction = "You are a helpful assistant. Answer the user's question. Username is {username}"
agent = create_agent(name="personalized_assistant", instruction=instruction)

# 创建 Session 时设置初始 State
await session_service.create_session(
    app_name="personalized_app",
    user_id="Alice",
    session_id=session_id,
    state={
        "username": "Alice",  # State 中的值会被注入到 Instruction 模板
    },
)
```

**工作原理**：
- Agent 的 `instruction` 中包含 `{username}` 模板变量
- 创建 Session 时设置的 `state["username"]` 会被自动注入到 Instruction 中
- Agent 执行时，Instruction 变为：`"You are a helpful assistant. Answer the user's question. Username is Alice"`

---

### 示例2：工具中修改 State

**功能**：演示如何在工具函数中读取和修改 State。

```python
async def update_user_preference(preference: str, value: str, tool_context: InvocationContext) -> str:
    """更新用户偏好"""
    # 保存用户级别偏好（使用 user: 前缀）
    tool_context.state[f"{preference}"] = value

    # 记录操作历史（会话级别，无前缀）
    history = tool_context.state.get("preference_history", [])
    history.append(f"UPDATE:{preference}={value}")
    tool_context.state["preference_history"] = history

    return f"已更新偏好 {preference} = {value}"

async def get_current_preferences(tool_context: InvocationContext) -> str:
    """获取当前偏好"""
    # 使用 to_dict() 方法遍历 State
    preferences = []
    for key, value in tool_context.state.to_dict().items():
        preferences.append(f"{key}={value}")
    return "当前用户偏好:\n" + "\n".join(preferences)
```

**关键点**：
- `tool_context.state` 提供对 State 的访问
- 使用 `state[key] = value` 设置值
- 使用 `state.get(key, default)` 读取值
- 使用 `state.to_dict()` 遍历所有键值对

---

### 示例3：多 Agent 协作 - 使用 output_key

**功能**：演示如何使用 `output_key` 在多个 Agent 之间传递状态。

```python
def create_chain_agent():
    """创建需求分析链"""
    # Agent 1: 需求分析器
    analyzer = LlmAgent(
        name="requirement_analyzer",
        instruction="分析用户需求，总结要点",
        output_key="analysis_result",  # 输出保存到 State["analysis_result"]
    )

    # Agent 2: 方案规划师
    planner = LlmAgent(
        name="solution_planner",
        instruction="基于分析结果制定解决方案：\n\n{analysis_result}\n\n请提供具体的行动建议。",
        output_key="solution_plan",  # 输出保存到 State["solution_plan"]
    )

    # 链式 Agent：按顺序执行
    return ChainAgent(
        name="analysis_chain",
        sub_agents=[analyzer, planner],
    )
```

**工作流程**：

```
用户输入 → requirement_analyzer
    ↓
生成分析结果 → 保存到 State["analysis_result"]
    ↓
solution_planner 读取 State["analysis_result"]
    ↓
生成解决方案 → 保存到 State["solution_plan"]
    ↓
最终响应返回给用户
```

**关键点**：
- `output_key` 指定 Agent 的输出保存到 State 的哪个键
- 后续 Agent 可以通过模板变量 `{analysis_result}` 引用前一个 Agent 的输出
- 所有状态都保存在同一个 Session 中，实现 Agent 间的协作

---

### 示例4：State 作用域演示

**功能**：演示 Session/User/App 三种不同作用域的状态管理。

```python
async def set_state_at_different_levels(level: str, value: str, tool_context: InvocationContext) -> str:
    """设置不同级别的状态"""
    if level == "session":
        # 会话级：无前缀
        tool_context.state["value"] = value
    elif level == "user":
        # 用户级：user: 前缀
        tool_context.state["user:value"] = value
    elif level == "app":
        # 应用级：app: 前缀
        tool_context.state["app:value"] = value
    elif level == "temp":
        # 临时级：temp: 前缀（不持久化）
        tool_context.state["temp:value"] = value
```

**作用域说明**：

| 作用域 | 前缀 | 作用范围 | 生命周期 | 示例 |
|-------|------|---------|---------|------|
| **Session State** | 无前缀 | 当前会话 | 随 Session 创建和销毁 | `"value": "会话1数据"` |
| **User State** | `user:` | 同一用户的所有会话 | 跨会话持久化 | `"user:value": "用户1偏好"` |
| **App State** | `app:` | 整个应用的所有用户和会话 | 全局持久化 | `"app:value": "应用级数据"` |
| **Temp State** | `temp:` | 单次 run_async 调用 | 不持久化，处理完即丢弃 | `"temp:value": "临时数据"` |

---

## 运行结果分析

### 完整输出

```txt
python3 examples/session_state/run_agent.py
============================================================
示例1：模板引用 - 在 Instruction 中使用 State
============================================================
👤 用户: 你能说出我的名字吗？
🤖 Agent:
[personalized_assistant]: 当然可以！你的名字是 Alice。😊 有什么我可以帮你的吗？


============================================================
示例2：工具中修改 State
============================================================
👤 用户: 请帮我设置主题偏好为深色模式
🤖 Agent:
🔧 [调用工具: update_user_preference]
📊 [工具结果: {'result': '已更新偏好 theme = dark'}]

[preference_agent]: 您的主题偏好已成功设置为深色模式！

👤 用户: 请显示我当前的所有偏好设置
🤖 Agent:
🔧 [调用工具: get_current_preferences]
📊 [工具结果: {'result': "当前用户偏好:\ntheme=dark\npreference_history=['UPDATE:theme=dark']"}]

[preference_agent]: 您当前的偏好设置如下：

- **主题**: 深色模式 (`theme=dark`)
- **偏好历史记录**:
  - 最近更新: 将主题设置为深色模式 (`UPDATE:theme=dark`)

如果需要调整或添加其他偏好，请随时告诉我！


============================================================
示例3：多Agent协作 - 使用 output_key
============================================================
👤 用户: 我想学习编程，但不知道从哪里开始，也担心太难坚持不下去
🤖 Agent:
[requirement_analyzer]: 需求要点总结：
1. 学习编程的意愿
2. 需要入门指导（起点不明确）
3. 担心学习难度
4. 担心坚持力问题

建议方向：
- 推荐适合初学者的编程语言（如Python）
- 建议分阶段学习路径
- 推荐互动式学习平台
- 建议加入学习社群获得支持
- 鼓励设置小目标保持动力

[solution_planner] 基于您的需求，我为您制定了以下具体学习方案：

1. 编程语言选择：
- 首选Python（语法简单，应用广泛）
- 备选JavaScript（网页开发入门）

2. 分阶段学习路径：
第一阶段（1-2周）：
✓ 完成Codecademy的Python入门课程
✓ 每天30分钟练习基础语法
✓ 制作第一个"Hello World"程序

第二阶段（3-4周）：
✓ 学习变量、循环和条件语句
✓ 完成5个小型编程挑战
✓ 尝试修改现有代码观察变化

3. 推荐学习平台：
- 互动式：Codecademy（免费基础课程）
- 视频教程：慕课网Python零基础
- 实践平台：LeetCode简单题库

4. 社群支持方案：
- 加入「Python新手互助」QQ群（群号：123456）
- 每周参加1次线上编程交流会
- 寻找1-2位学习伙伴互相监督

5. 动力维持策略：
- 设置里程碑奖励（如完成阶段目标后奖励自己）
- 使用「Forest」专注APP记录学习时间
- 每周记录学习成果（建议用GitHub仓库）

特别建议：前两周每天学习不超过1小时，重点培养编程兴趣而非追求进度。遇到困难时，记住每个程序员都经历过这个阶段！

📊 协作结果保存在状态中:
   📝 分析结果: 需求要点总结：
1. 学习编程的意愿
2. 需要入门指导（起点不明确）
3. 担心学习难度
4. 担...
   📋 解决方案: [solution_planner] 基于您的需求，我为您制定了以下具体学习方案：

1. 编程语言...


============================================================
示例4：State作用域演示
============================================================

🔹 步骤1: 设置初始状态 (user1, session1)
👤 用户: 请帮我设置各级别状态: 会话级状态为'会话1数据', 用户级状态为'用户1偏好', 应用级状态不变
🤖 Agent:
🔧 [调用工具: set_state_at_different_levels]

🔧 [调用工具: set_state_at_different_levels]
📊 [工具结果: {'result': '已设置会话级状态: value = 会话1数据'}]
📊 [工具结果: {'result': '已设置用户级状态: user:value = 用户1偏好'}]

[state_demo_agent]: 已成功设置以下状态：

- 会话级状态为：`会话1数据`
- 用户级状态为：`用户1偏好`
- 应用级状态保持不变。

📊 user1 session1 状态
   App: scope_demo_app, User: user1, Session: 7afa882f-1f50-443c-9820-40129ec092af
   📱 应用级状态: {'app:value': '应用级数据'}
   👤 用户级状态: {'user:value': '用户1偏好'}
   💬 会话级状态: {'value': '会话1数据'}
--------------------------------------------------

🔹 步骤2: 同一用户，新会话 (user1, session2)
👤 用户: 你好
🤖 Agent:
[state_demo_agent]: 你好！有什么可以帮您的吗？

📊 user1 session2 状态
   App: scope_demo_app, User: user1, Session: 11da09d0-1fb9-450e-8ba1-6469fbd06c7a
   📱 应用级状态: {'app:value': '应用级数据'}
   👤 用户级状态: {'user:value': '用户1偏好'}
   💬 会话级状态: {}
--------------------------------------------------

🔹 步骤3: 新用户，新会话 (user2, session3)
👤 用户: 你好
🤖 Agent:
[state_demo_agent]: 你好！有什么可以帮您的吗？

📊 user2 session3 状态
   App: scope_demo_app, User: user2, Session: ccf50bcb-f126-40b5-baa6-9374f10b72b0
   📱 应用级状态: {'app:value': '应用级数据'}
   👤 用户级状态: {}
   💬 会话级状态: {}
--------------------------------------------------
```

### 关键观察点分析

#### 1️⃣ **示例1：模板引用**

**运行结果**：
```
👤 用户: 你能说出我的名字吗？
🤖 Agent: 当然可以！你的名字是 Alice。😊
```

**分析**：
- ✅ Agent 成功识别了用户名字 "Alice"
- ✅ 这是因为 Instruction 中的 `{username}` 被替换为 State 中的值
- ✅ Agent 能够访问 State 中的信息并用于响应

**State 使用**：
```python
state = {
    "username": "Alice"  # 在 Instruction 中被引用
}
instruction = "... Username is {username}"  # 模板变量
```

---

#### 2️⃣ **示例2：工具中修改 State**

**运行结果**：
```
第一轮：设置主题偏好
📊 [工具结果: {'result': '已更新偏好 theme = dark'}]

第二轮：查看偏好
📊 [工具结果: {'result': "当前用户偏好:\ntheme=dark\npreference_history=['UPDATE:theme=dark']"}]
```

**分析**：
- ✅ 第一轮调用 `update_user_preference` 工具，成功设置 `theme=dark`
- ✅ 工具同时记录了操作历史到 `preference_history`
- ✅ 第二轮调用 `get_current_preferences` 工具，成功读取到之前设置的值
- ✅ State 在工具调用之间保持持久化

**State 变化**：
```python
# 第一轮后
state = {
    "theme": "dark",                           # 用户级偏好
    "preference_history": ["UPDATE:theme=dark"]  # 会话级历史
}

# 第二轮读取
# 工具遍历 state.to_dict() 获取所有键值对
```

---

#### 3️⃣ **示例3：多 Agent 协作**

**运行结果**：
```
[requirement_analyzer]: 需求要点总结：
1. 学习编程的意愿
2. 需要入门指导（起点不明确）
...

[solution_planner] 基于您的需求，我为您制定了以下具体学习方案：
1. 编程语言选择：
- 首选Python（语法简单，应用广泛）
...

📊 协作结果保存在状态中:
   📝 分析结果: 需求要点总结：...
   📋 解决方案: [solution_planner] 基于您的需求...
```

**分析**：
- ✅ `requirement_analyzer` 分析用户需求，输出保存到 `State["analysis_result"]`
- ✅ `solution_planner` 读取 `State["analysis_result"]`，生成解决方案
- ✅ `solution_planner` 的输出保存到 `State["solution_plan"]`
- ✅ 两个 Agent 通过 State 实现了协作

**State 变化**：
```python
# requirement_analyzer 执行后
state = {
    "analysis_result": "需求要点总结：\n1. 学习编程的意愿\n..."
}

# solution_planner 执行后
state = {
    "analysis_result": "需求要点总结：\n1. 学习编程的意愿\n...",
    "solution_plan": "[solution_planner] 基于您的需求...\n1. 编程语言选择：..."
}
```

**工作流程**：
```
用户输入
    ↓
requirement_analyzer 处理
    ↓ output_key="analysis_result"
State["analysis_result"] = "需求要点总结..."
    ↓
solution_planner 读取 {analysis_result}
    ↓ output_key="solution_plan"
State["solution_plan"] = "学习方案..."
    ↓
返回最终响应
```

---

#### 4️⃣ **示例4：State 作用域演示**

**运行结果对比**：

| 步骤 | 用户/会话 | 应用级状态 | 用户级状态 | 会话级状态 |
|-----|---------|----------|----------|----------|
| **步骤1** | user1/session1 | `app:value = 应用级数据` | `user:value = 用户1偏好` | `value = 会话1数据` |
| **步骤2** | user1/session2 | `app:value = 应用级数据` ✅ | `user:value = 用户1偏好` ✅ | `{}` ❌ |
| **步骤3** | user2/session3 | `app:value = 应用级数据` ✅ | `{}` ❌ | `{}` ❌ |

**分析**：

**步骤1（user1, session1）**：
- ✅ 设置了应用级、用户级、会话级三种状态
- ✅ 所有状态都正确保存

**步骤2（user1, session2）**：
- ✅ **应用级状态保留**：`app:value` 仍然存在（全局共享）
- ✅ **用户级状态保留**：`user:value` 仍然存在（同一用户跨会话）
- ❌ **会话级状态丢失**：`value` 为空（新会话，会话级状态不共享）

**步骤3（user2, session3）**：
- ✅ **应用级状态保留**：`app:value` 仍然存在（全局共享）
- ❌ **用户级状态丢失**：`user:value` 为空（不同用户，用户级状态不共享）
- ❌ **会话级状态丢失**：`value` 为空（新会话）

**关键结论**：

1. **应用级状态（`app:` 前缀）**：
   - ✅ 所有用户、所有会话共享
   - ✅ 适合存储全局配置、共享资源

2. **用户级状态（`user:` 前缀）**：
   - ✅ 同一用户的所有会话共享
   - ✅ 不同用户之间隔离
   - ✅ 适合存储用户偏好、个人信息

3. **会话级状态（无前缀）**：
   - ✅ 仅在当前会话中有效
   - ✅ 新会话不会继承
   - ✅ 适合存储任务进度、临时计算结果

---

## 功能特性详解

### 1. 模板引用机制

**使用场景**：
- 个性化 Agent：根据用户信息定制 Instruction
- 动态配置：根据运行时状态调整 Agent 行为

**语法**：
```python
# Instruction 中使用 {key} 引用 State
instruction = "You are {role}. User name is {username}."

# State 中设置值
state = {
    "role": "assistant",
    "username": "Alice"
}

# 实际执行时，Instruction 变为：
# "You are assistant. User name is Alice."
```

**注意事项**：
- 模板变量名必须与 State 中的键名完全匹配
- 如果 State 中没有对应的键，模板变量不会被替换（保持原样）

---

### 2. 工具中访问 State

**访问方式**：
```python
async def my_tool(param: str, tool_context: InvocationContext) -> str:
    # 读取 State
    value = tool_context.state.get("key", "default")

    # 设置 State
    tool_context.state["key"] = "new_value"

    # 遍历所有 State
    for key, value in tool_context.state.to_dict().items():
        print(f"{key}: {value}")

    return "result"
```

**作用域控制**：
```python
# 会话级（无前缀）
tool_context.state["session_key"] = "value"

# 用户级（user: 前缀）
tool_context.state["user:preference"] = "dark"

# 应用级（app: 前缀）
tool_context.state["app:config"] = "value"

# 临时级（temp: 前缀，不持久化）
tool_context.state["temp:cache"] = "value"
```

---

### 3. 多 Agent 协作

**ChainAgent 工作流程**：

```
用户输入
    ↓
Agent 1 (requirement_analyzer)
    ↓ output_key="analysis_result"
State["analysis_result"] = "分析结果..."
    ↓
Agent 2 (solution_planner)
    ↓ 读取 State["analysis_result"]
    ↓ output_key="solution_plan"
State["solution_plan"] = "解决方案..."
    ↓
返回最终响应
```

**配置示例**：
```python
# Agent 1: 分析需求
analyzer = LlmAgent(
    name="requirement_analyzer",
    instruction="分析用户需求",
    output_key="analysis_result",  # 输出保存到 State
)

# Agent 2: 制定方案
planner = LlmAgent(
    name="solution_planner",
    instruction="基于分析结果：\n\n{analysis_result}\n\n制定方案",
    output_key="solution_plan",  # 输出保存到 State
)

# 链式执行
chain = ChainAgent(
    sub_agents=[analyzer, planner],
)
```

**关键点**：
- `output_key` 指定 Agent 输出保存到 State 的键名
- 后续 Agent 可以通过模板变量 `{key}` 引用前一个 Agent 的输出
- 所有 Agent 共享同一个 Session State

---

### 4. State 作用域详解

#### Session State（会话级）

**特点**：
- 无前缀，直接使用键名
- 仅在当前会话中有效
- 新会话不会继承

**使用场景**：
- 任务进度追踪：`current_step: 'processing'`
- 临时计算结果：`calculation_result: 42`
- 会话特定的配置：`language: 'zh'`

**示例**：
```python
# 设置会话级状态
tool_context.state["current_step"] = "step2"

# 读取会话级状态
step = tool_context.state.get("current_step")
```

---

#### User State（用户级）

**特点**：
- 使用 `user:` 前缀
- 同一用户的所有会话共享
- 跨会话持久化

**使用场景**：
- 用户偏好：`user:theme: 'dark'`
- 个人信息：`user:name: 'Alice'`
- 用户配置：`user:language: 'zh'`

**示例**：
```python
# 设置用户级状态
tool_context.state["user:theme"] = "dark"

# 读取用户级状态
theme = tool_context.state.get("user:theme")
```

**跨会话验证**：
```
user1/session1: 设置 user:theme = "dark"
user1/session2: 读取 user:theme → "dark" ✅（跨会话共享）
user2/session3: 读取 user:theme → None ❌（不同用户隔离）
```

---

#### App State（应用级）

**特点**：
- 使用 `app:` 前缀
- 所有用户、所有会话共享
- 全局持久化

**使用场景**：
- 全局配置：`app:version: '1.0'`
- 共享资源：`app:maintenance_mode: false`
- 系统状态：`app:feature_flag: true`

**示例**：
```python
# 设置应用级状态
tool_context.state["app:version"] = "1.0"

# 读取应用级状态
version = tool_context.state.get("app:version")
```

**全局共享验证**：
```
user1/session1: 设置 app:version = "1.0"
user1/session2: 读取 app:version → "1.0" ✅
user2/session3: 读取 app:version → "1.0" ✅（所有用户共享）
```

---

#### Temp State（临时级）

**特点**：
- 使用 `temp:` 前缀
- 单次 `run_async` 调用有效
- **不持久化**，处理完即丢弃

**使用场景**：
- 中间计算结果：`temp:api_response: {...}`
- 调试信息：`temp:debug_info: '...'`
- 临时缓存：`temp:cache: '...'`

**示例**：
```python
# 设置临时状态（不会持久化）
tool_context.state["temp:cache"] = "value"

# 在同一 run_async 调用中可以读取
cache = tool_context.state.get("temp:cache")

# 调用结束后，temp: 前缀的状态会被自动清理
```

---

## State 持久化

### 持久化机制

State 的持久化取决于使用的 SessionService：

| SessionService | 持久化方式 | 进程重启后 |
|---------------|----------|-----------|
| **InMemorySessionService** | 进程内存 | ❌ 丢失 |
| **RedisSessionService** | Redis | ✅ 恢复 |
| **SqlSessionService** | MySQL/PostgreSQL | ✅ 恢复 |

### 作用域持久化规则

| 作用域 | InMemory | Redis | SQL |
|-------|---------|-------|-----|
| **Session State** | 随 Session | 随 Session | 随 Session |
| **User State** | 跨 Session | 跨 Session | 跨 Session |
| **App State** | 全局 | 全局 | 全局 |
| **Temp State** | ❌ 不持久化 | ❌ 不持久化 | ❌ 不持久化 |

---

## 最佳实践

### 1. 选择合适的作用域

- **会话级**：任务进度、临时计算结果
- **用户级**：用户偏好、个人信息、用户配置
- **应用级**：全局配置、共享资源、系统状态
- **临时级**：中间计算结果、调试信息

### 2. 命名规范

```python
# ✅ 好的命名
tool_context.state["user:theme"] = "dark"           # 用户级
tool_context.state["app:version"] = "1.0"            # 应用级
tool_context.state["current_step"] = "step2"         # 会话级
tool_context.state["temp:cache"] = "value"           # 临时级

# ❌ 避免的命名
tool_context.state["theme"] = "dark"                 # 不明确作用域
tool_context.state["user_theme"] = "dark"            # 应该用 user: 前缀
```

### 3. 模板引用

```python
# ✅ 好的实践
instruction = "User name is {username}. Current step is {current_step}."
state = {
    "username": "Alice",
    "current_step": "step1"
}

# ❌ 避免
instruction = "User name is {user_name}"  # 键名不匹配
state = {
    "username": "Alice"  # 键名不一致，模板变量不会被替换
}
```

### 4. 多 Agent 协作

```python
# ✅ 好的实践
analyzer = LlmAgent(
    output_key="analysis_result",  # 清晰的键名
)
planner = LlmAgent(
    instruction="基于 {analysis_result} 制定方案",  # 引用前一个 Agent 的输出
    output_key="solution_plan",
)

# ❌ 避免
analyzer = LlmAgent(
    output_key="result",  # 键名太通用，容易冲突
)
```

---

## 总结

本示例展示了 Session State 的四种核心使用场景：

1. **模板引用**：在 Instruction 中使用 State 进行动态模板替换
2. **工具访问**：在工具函数中读写 State
3. **多 Agent 协作**：使用 `output_key` 实现 Agent 间的状态传递
4. **作用域控制**：通过前缀区分不同作用域的状态

### 核心要点

- ✅ **State 是键值对集合**：存储会话的上下文信息
- ✅ **支持四种作用域**：Session/User/App/Temp
- ✅ **模板引用**：在 Instruction 中使用 `{key}` 引用 State
- ✅ **工具访问**：通过 `tool_context.state` 读写 State
- ✅ **多 Agent 协作**：使用 `output_key` 传递状态
- ✅ **持久化**：取决于 SessionService 实现

通过合理使用 State，可以实现：
- 个性化 Agent 响应
- 跨工具的状态共享
- 多 Agent 协作
- 用户偏好管理
- 任务进度追踪
