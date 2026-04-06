# Mem0 记忆集成示例

本示例演示如何将 `Mem0` 接入 `trpc-agent`，让 Agent 具备“检索历史记忆 + 写入新记忆”的长期记忆能力，并支持自托管与平台两种模式。

## 关键特性

- **双记忆模式**：同一套 Agent 代码支持 `AsyncMemory`（自托管）和 `AsyncMemoryClient`（平台）
- **工具化记忆能力**：通过 `SearchMemoryTool` 与 `SaveMemoryTool` 在对话中自动查找/保存用户信息
- **低侵入切换**：通过 `create_agent(use_mem0_platform=...)` 一处开关切换模式
- **可观察记忆链路**：运行日志可看到 `search_memory` / `save_memory` 的调用与返回
- **面向实战排障**：覆盖向量维度不匹配、平台 key 缺失、Qdrant 连通性等常见问题

## Agent 层级结构说明

```text
personal_assistant (LlmAgent)
├── model: OpenAIModel (config from .env)
└── tools:
    ├── SearchMemoryTool (search_memory)
    └── SaveMemoryTool (save_memory)
        └── backend:
            ├── AsyncMemory (self-hosted)
            └── AsyncMemoryClient (Mem0 platform)
```

关键文件：

- [examples/mem_0/agent/agent.py](./agent/agent.py)
- [examples/mem_0/agent/config.py](./agent/config.py)
- [examples/mem_0/run_agent.py](./run_agent.py)
- `trpc_agent_sdk/tools/mem0_tool.py`

## 关键代码解释

### 1) 模式切换入口（`agent/agent.py`）

- `create_agent(use_mem0_platform=False)`：
  - `False` -> `AsyncMemory(config=...)`（自托管）
  - `True` -> `AsyncMemoryClient(api_key, host)`（平台）
- 两种模式最终都注入同一组记忆工具，保证调用方式一致

### 2) 记忆配置（`agent/config.py`）

- 自托管默认使用：
  - `vector_store = qdrant`
  - `llm = deepseek`（读取 `TRPC_AGENT_*`）
  - `embedder = huggingface`（`multi-qa-MiniLM-L6-cos-v1`）
- 平台模式读取：
  - `MEM0_API_KEY`
  - `MEM0_BASE_URL`

### 3) 运行逻辑（`run_agent.py`）

- 使用同一 `user_id`，配合多轮 query 验证“先查不到 -> 再写入 -> 再查到”
- 日志中打印工具调用和工具返回，便于检查记忆链路

## 环境与运行

### 环境要求

- Python 3.12
- `mem0ai`
- 自托管模式额外需要：`sentence-transformers`、`qdrant-client`

### 安装步骤

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
python3 -m venv .venv
source .venv/bin/activate

pip3 install -e .[mem0]
pip3 install mem0ai

# Self-hosted mode only
pip3 install sentence-transformers qdrant-client
```

### 环境变量要求

在 [examples/mem_0/.env](./.env) 中配置（或通过 `export`）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`
- `MEM0_API_KEY=your-mem0-api-key`  # Optional: Mem0 platform mode
- `MEM0_BASE_URL=https://api.mem0.ai` # Optional: Mem0 platform mode


### 运行命令

```bash
cd examples/mem_0
python3 run_agent.py
```

## 运行结果（实测）

示例典型输出（节选）：

```text
📝 User: Do you remember my name?
🔧 [Invoke Tool: search_memory({'query': "user's name"})]
📊 [Tool Result: {'status': 'no_memories', 'message': 'No relevant memories found'}]

📝 User: My name is Alice
🔧 [Invoke Tool: save_memory({'content': "The user's name is Alice."})]
📊 [Tool Result: {'status': 'success', 'message': 'Information saved to memory', ...}]

📝 User: Do you remember my name?
🔧 [Invoke Tool: search_memory({'query': "user's name"})]
📊 [Tool Result: {'status': 'success', 'memories': '- Name is Alice ...'}]
```

存储结果可在：

- 自托管 Qdrant：![Mem0 Result](./images/mem0_result.png)
- Mem0 平台：![Mem0 Platform Result](./images/mem0_plat.png)

## 结果分析（是否符合要求）

结论：**符合本示例测试目标**（记忆读写链路可用）。

- **记忆查询生效**：初次查询返回 `no_memories`
- **记忆写入生效**：写入后返回 `success`
- **记忆回读生效**：后续查询可以检索到已写入用户信息
- **模式设计合理**：同一 Agent 逻辑兼容自托管与平台模式

## 特有说明

### 1) 自托管模式的重要前置：Qdrant 向量维度

当嵌入模型为 `multi-qa-MiniLM-L6-cos-v1` 时，向量维度是 **384**。  
若 Qdrant 集合按 1536 初始化，会出现维度错误。

```python
from qdrant_client import QdrantClient
from qdrant_client.models import VectorParams, Distance

client = QdrantClient(host="localhost", port=6333)
client.create_collection(
    collection_name="mem0",
    vectors_config=VectorParams(size=384, distance=Distance.COSINE),
)
```

### 2) 两种模式对比（简版）

| 维度 | 自托管 | 平台模式 |
|---|---|---|
| 部署 | 需本地组件（如 Qdrant） | 云端托管 |
| 控制力 | 高 | 中 |
| 运维成本 | 较高 | 较低 |
| 适用场景 | 本地调试、定制化 | 快速上线、托管场景 |

### 3) 常见问题速查

- **Mem0 API Key 缺失**
  - 报错：`ValueError: Mem0 API Key not provided`
  - 处理：设置 `MEM0_API_KEY`

- **Qdrant 无法连接**
  - 报错：`Cannot connect to Qdrant at localhost:6333`
  - 处理：检查容器/服务状态

- **维度不匹配**
  - 报错：`expected dim: 1536, got 384`
  - 处理：重建集合或切换匹配维度的 embedding 模型

- **依赖安装异常**
  - 可按需补装：
    ```bash
    pip3 install "langchain_huggingface>=0.1.0"
    pip3 install "huggingface-hub>=0.33.4,<1.0.0"
    pip3 install sentence_transformers nvidia-ml-py pynvml
    ```

## 适用场景建议

- 需要跨会话保留用户偏好、历史事实的个人助理场景
- 需要可控数据链路（自托管）或快速集成（平台）两类落地场景
- 需要评估“记忆增强”对回答个性化和连续性的提升效果

## Mem0 服务搭建
### 模式一：自托管 Mem0

mem0 官方提供 `AsyncMemory` 和 `Memory` 两种 sdk 类，后续都是以 `AsyncMemory` 为基础介绍

### 组件架构

自托管模式需要三个核心组件：

| 组件 | 默认提供商 | 默认模型/配置 |
|------|----------|--------------|
| **LLM** | OpenAI | `gpt-4o` |
| **嵌入模型** | OpenAI | `text-embedding-3-small`（1536 维） |
| **向量存储** | 内存存储 | 本地内存（非持久化） |
| **版本** | v1.1 | - |
| **历史数据库** | SQLite | `{mem0_dir}/history.db` |

mem0 支持的提供商

| 组件 | 支持的提供商 |
|------|-------------|
| **向量存储** | Qdrant, Pinecone, Chroma, Weaviate, Milvus, In-Memory |
| **LLM** | OpenAI, DeepSeek, Anthropic, Gemini, Groq, Azure OpenAI |
| **嵌入模型** | OpenAI, HuggingFace, Ollama, Azure OpenAI |



#### 高级用法

如果用户期望完全设置自定义的三个核心组件，可以使用如下的方式，这里测试选型如下（用户有需要可以自行选择其他的）：

| 组件 | 提供商 | 测试值 | 用途 |
|------|--------|---------|------|
| **向量存储(vector_store)** | Qdrant | - | 存储记忆嵌入向量 |
| **LLM** | DeepSeek | deepseek-v3 | 生成记忆摘要 |
| **嵌入模型(embedder)** | HuggingFace | multi-qa-MiniLM-L6-cos-v1 | 将文本转换为向量（384 维） |

##### 步骤 1：部署 Qdrant 向量数据库

```bash
# 拉取 Qdrant 镜像
docker pull qdrant/qdrant

# 创建存储目录
mkdir -p /tmp/qdrant_storage && chmod 777 /tmp/qdrant_storage

# 启动 Qdrant 服务
docker run -d --name qdrant_server -v /tmp/qdrant_storage:/qdrant/storage -p 6333:6333 qdrant/qdrant

# 验证服务运行状态
docker logs qdrant_server

# 访问控制台
# 浏览器打开：http://localhost:6333/dashboard#/welcome
```

**Qdrant 控制台预览：**

![Qdrant Dashboard](./images/qdrant_dashboard.png)

##### 步骤 2：初始化 Qdrant 集合

**⚠️ 重要提示**：嵌入模型 `multi-qa-MiniLM-L6-cos-v1` 生成 **384 维**向量，但 Qdrant 默认维度为 1536。首次使用前必须使用正确的维度初始化集合。

```python
from qdrant_client import QdrantClient
from qdrant_client.models import VectorParams, Distance

# 连接本地 Qdrant
client = QdrantClient(host="localhost", port=6333)

# 删除已有集合（如需要）
try:
    client.delete_collection("mem0")
except Exception:
    pass

# 创建正确维度的集合
client.create_collection(
    collection_name="mem0",
    vectors_config=VectorParams(
        size=384,  # 必须与嵌入模型输出维度匹配
        distance=Distance.COSINE
    )
)

print("✅ 集合 'mem0' 创建成功")
```

**在控制台中验证**：[http://localhost:6333/dashboard#/collections](http://localhost:6333/dashboard#/collections)

![Qdrant Collection](./images/qdrant_mem.png)

##### 步骤 3：配置记忆设置

编辑 `agent/config.py` 或设置环境变量：

```python
# agent/config.py
def get_memory_config() -> MemoryConfig:
    """获取自托管模式的记忆配置"""
    memory_config = {
        "vector_store": {
            "provider": "qdrant",
            "config": {
                "host": "localhost",
                "port": 6333,
                "collection_name": "mem0",
            }
        },
        "llm": {
            "provider": "deepseek",
            "config": {
                "model": os.getenv('TRPC_AGENT_MODEL_NAME', 'deepseek-v3'),
                "api_key": os.getenv('TRPC_AGENT_API_KEY', ''),
                "deepseek_base_url": os.getenv('TRPC_AGENT_BASE_URL', ''),
                "temperature": 0.2,
                "max_tokens": 2000,
            }
        },
        "embedder": {
            "provider": "huggingface",
            "config": {
                "model": "multi-qa-MiniLM-L6-cos-v1"  # 384 维
            }
        }
    }
    return MemoryConfig(**memory_config)
```

---

### 模式二：Mem0 平台（云端 API）

#### 注册 Mem0 平台

访问 [https://app.mem0.ai/dashboard](https://app.mem0.ai/dashboard) 创建账号。

#### 获取 API 凭证

注册后，从控制台获取 API Key 和组织/项目 ID。
![Mem0 Platform](./images/mem0_ai.png)

#### 初始化平台客户端

##### 更新 `.env` 文件，添加 Mem0 凭证：

```bash
MEM0_API_KEY=m0-your-api-key
MEM0_BASE_URL=https://api.mem0.ai
```

#### 创建平台客户端

```python
 from mem0 import AsyncMemoryClient
# agent/config.py
def get_mem0_platform_config() -> dict:
    """从环境变量获取 Mem0 平台配置"""
    return {
        "api_key": os.getenv('MEM0_API_KEY', ''),
        "host": os.getenv('MEM0_BASE_URL', 'https://api.mem0.ai'),
    }
 # agent/agent.py

mem0_platform_config = get_mem0_platform_config()
mem_client = AsyncMemoryClient(api_key=mem0_platform_config['api_key'], host=mem0_platform_config['host'], org_id="xxx")

```

AsyncMemoryClient 平台客户端参数

| 参数 | 类型 | 说明 | 默认值 | 必需 |
|------|------|------|--------|------|
| `api_key` | str | Mem0 API 认证密钥 | - | ✅ 是 |
| `host` | str | Mem0 API 基础 URL | `https://api.mem0.ai` | 否 |
| `org_id` | str | 组织 ID | `None` | 否 |
| `project_id` | str | 项目 ID | `None` | 否 |

完整代码参考：[agent.py](./agent/agent.py)

---

## 参考资料

- [Mem0 Docs](https://docs.mem0.ai/introduction)
- [Mem0 Examples](https://github.com/mem0ai/mem0/tree/main/examples)
- [tRPC-Agent Mem0 Tool](../../trpc_agent_ecosystem/tools/mem0_tool.py)
