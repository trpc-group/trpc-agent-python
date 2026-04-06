# Memory Service Documentation

## Overview

`MemoryService` is a core component in trpc-agent for managing **long-term memory**. Unlike `SessionService` which manages the context of the current session, `MemoryService` focuses on storing and retrieving historical memories across sessions, helping the Agent recall relevant content in subsequent conversations.

### Memory vs Session

| Feature | Session | Memory |
|-----|---------|--------|
| **Scope** | Single session | Cross-session (shared across all sessions) |
| **Lifecycle** | Created and destroyed with the session | Independent of sessions, controlled by TTL |
| **Stored Content** | Complete conversation history of the current session | Key events and knowledge fragments |
| **Access Method** | Automatically loaded into context | Retrieved via `load_memory` tool |
| **Typical Use** | Context for a single conversation | Long-term memory, user profiles, knowledge accumulation |

---

## Core Capabilities of MemoryService

Based on the implementation in `trpc_agent_sdk/memory/`, MemoryService provides the following core capabilities:

### 1. Storing Session Memory

**Function**: Stores key events from a Session as long-term memory.

**Implementation Methods**:
- **InMemoryMemoryService**: Stored in an in-process memory dictionary
- **RedisMemoryService**: Stored in a Redis List (JSON format)
- **SqlMemoryService**: Stored in the `mem_events` table in MySQL/PostgreSQL

**Code Example**:
```python
# Store session to Memory
await memory_service.store_session(session=session)
```

**Storage Logic** (using `InMemoryMemoryService` as an example):
```python
# from trpc_agent_sdk/memory/_in_memory_memory_service.py
async def store_session(self, session: Session, agent_context: Optional[AgentContext] = None) -> None:
    # Data structure: {save_key: {session_id: [EventTtl, ...]}}
    self._session_events[session.save_key] = self._session_events.get(session.save_key, {})
    self._session_events[session.save_key][session.id] = [
        EventTtl(event=event, ttl=self._memory_service_config.ttl)
        for event in session.events
        if event.content and event.content.parts  # Only store events with content
    ]
```

---

### 2. Searching Related Memories

**Function**: Searches for related historical memories based on query keywords.

**Search Method**: **Keyword matching** (not semantic search)

**Implementation Logic** (using `InMemoryMemoryService` as an example):
```python
# From trpc_agent_sdk/memory/_in_memory_memory_service.py
async def search_memory(self, key: str, query: str, limit: int = 10, ...) -> SearchMemoryResponse:
    # 1. Extract query keywords (supports both Chinese and English)
    words_in_query = extract_words_lower(query)  # Extract English words and Chinese characters

    # 2. Iterate over all session events
    for session_events in self._session_events[key].values():
        for event_ttl in session_events:
            # 3. Extract keywords from the event
            words_in_event = extract_words_lower(' '.join([part.text for part in event.content.parts if part.text]))

            # 4. Keyword matching (return on any query word match)
            if any(query_word in words_in_event for query_word in words_in_query):
                response.memories.append(MemoryEntry(...))
                # 5. Refresh TTL (refresh expiration time on access)
                event_ttl.update_expired_at()
```

**Keyword Extraction** (`_utils.py`):
```python
def extract_words_lower(text: str) -> set[str]:
    """Extract English words and Chinese characters"""
    words = set()
    # Extract English words (letter sequences)
    words.update([word.lower() for word in re.findall(r'[A-Za-z]+', text)])
    # Extract Chinese characters (Unicode range \u4e00-\u9fff)
    words.update(re.findall(r'[\u4e00-\u9fff]', text))
    return words
```

**Usage Example**:
```python
from trpc_agent_sdk.types import SearchMemoryResponse

# Search related memories
search_key = f"{app_name}/{user_id}"  # Format: app_name/user_id
response: SearchMemoryResponse = await memory_service.search_memory(
    key=search_key,
    query="weather",  # Query keyword
    limit=10       # Return at most 10 memories
)

# Process search results
for memory in response.memories:
    print(f"Memory content: {memory.content}")
    print(f"Author: {memory.author}")
    print(f"Timestamp: {memory.timestamp}")
```

---

### 3. TTL (Time-To-Live) Cache Eviction

**Function**: Automatically cleans up expired memory data, preventing unlimited memory/storage growth.

**Implementation Methods**:
- **InMemoryMemoryService**: Background periodic cleanup task (`_cleanup_loop`)
- **RedisMemoryService**: Redis native `EXPIRE` mechanism (automatic expiration)
- **SqlMemoryService**: Background periodic cleanup task (batch SQL DELETE)

**TTL Configuration**:
```python
from trpc_agent_sdk.memory import MemoryServiceConfig

memory_service_config = MemoryServiceConfig(
    enabled=True,
    ttl=MemoryServiceConfig.create_ttl_config(
        enable=True,                    # Enable TTL
        ttl_seconds=86400,              # Memory expiration time: 24 hours
        cleanup_interval_seconds=3600,  # Cleanup interval: 1 hour (InMemory/SQL only)
    ),
)
```

**TTL Refresh Mechanism**:
- **Refresh on access**: TTL is refreshed for matched events during `search_memory`
- **Refresh on storage**: TTL is set for new events during `store_session`

---

### 4. Cross-Session Sharing

**Function**: Different sessions can share the same memory data.

**Implementation Method**:
- Uses `save_key` (format: `app_name/user_id`) as the memory key
- All sessions from the same user share the same memory space
- Uses `key=f"{app_name}/{user_id}"` during search to retrieve all memories for that user

**Data Structure** (InMemoryMemoryService):
```python
# Data structure: {save_key: {session_id: [EventTtl, ...]}}
_session_events = {
    "weather_app/user_001": {
        "session_1": [EventTtl(...), EventTtl(...)],
        "session_2": [EventTtl(...), EventTtl(...)],
    },
    "weather_app/user_002": {
        "session_3": [EventTtl(...)],
    }
}
```

---

## MemoryService Implementations

trpc-agent provides three `MemoryService` implementations, allowing you to choose the appropriate storage backend based on your scenario:

### InMemoryMemoryService

**How It Works**: Stores memory data directly in the application's memory.

**Implementation Details** (based on `_in_memory_memory_service.py`):
- **Data Structure**: `dict[str, dict[str, list[EventTtl]]]` (nested dictionaries)
- **Storage Location**: Process memory
- **Search Method**: Keyword matching (iterating over in-memory dictionary)
- **TTL Mechanism**: Background periodic cleanup task (`_cleanup_loop`)
- **Cleanup Method**: Two-phase deletion (collect expired items → batch delete)

**Persistence**: ❌ **None**. All memory data is lost if the application restarts.

**Applicable Scenarios**:
- ✅ Rapid development
- ✅ Local testing
- ✅ Demo examples
- ✅ Scenarios that do not require long-term persistence

**Configuration Example**:
```python
from trpc_agent_sdk.memory import InMemoryMemoryService, MemoryServiceConfig

memory_service_config = MemoryServiceConfig(
    enabled=True,
    ttl=MemoryServiceConfig.create_ttl_config(
        enable=True,
        ttl_seconds=86400,              # 24-hour expiration
        cleanup_interval_seconds=3600,  # Cleanup every 1 hour
    ),
)

memory_service = InMemoryMemoryService(memory_service_config=memory_service_config)
```

**Notes**:
- When `enabled=True`, MemoryService automatically stores Session events, **no need to manually call `store_session`**
- If `enabled=False`, MemoryService will not store any data
- The cleanup task runs in the background, periodically deleting expired events

**Related Examples**:
- 📁 [`examples/memory_service_with_in_memory/run_agent.py`](../../../examples/memory_service_with_in_memory/run_agent.py) - Complete In-Memory Memory Service usage example

---

### RedisMemoryService

**How It Works**: Uses Redis to store memory data, supporting multi-node sharing.

**Implementation Details** (based on `_redis_memory_service.py`):
- **Data Structure**: Redis List (`RPUSH` to store event JSON)
- **Storage Location**: Redis external storage
- **Key Format**: `memory:{save_key}:{session_id}`
- **Search Method**: `KEYS memory:{key}:*` + keyword matching
- **TTL Mechanism**: Redis native `EXPIRE` command (automatic expiration)
- **TTL Refresh**: Automatically refreshed on access (during `search_memory`)

**Persistence**: ✅ **Yes**. Data is persisted in Redis and can be recovered after application restart.

**Applicable Scenarios**:
- ✅ Production environments
- ✅ Multi-node deployments
- ✅ High-performance caching requirements
- ✅ Distributed applications

**Configuration Example**:
```python
from trpc_agent_sdk.memory import RedisMemoryService, MemoryServiceConfig
import os

# Read Redis configuration from environment variables
db_host = os.environ.get("REDIS_HOST", "127.0.0.1")
db_port = os.environ.get("REDIS_PORT", "6379")
db_password = os.environ.get("REDIS_PASSWORD", "")
db_db = os.environ.get("REDIS_DB", 0)

# Build Redis connection URL
if db_password:
    db_url = f"redis://:{db_password}@{db_host}:{db_port}/{db_db}"
else:
    db_url = f"redis://{db_host}:{db_port}/{db_db}"

memory_service_config = MemoryServiceConfig(
    enabled=True,
    ttl=MemoryServiceConfig.create_ttl_config(
        enable=True,
        ttl_seconds=86400,  # 24-hour expiration (automatically handled by Redis)
    ),
)

memory_service = RedisMemoryService(
    db_url=db_url,
    is_async=True,          # Use async mode (recommended)
    memory_service_config=memory_service_config,
    enabled=True,
)
```

**Redis Data Structure**:
```bash
# Storage format: Redis List
memory:weather_app/user_001:session_1
  └─ [0] '{"id":"event_1","author":"user","content":{...},"timestamp":...}'
  └─ [1] '{"id":"event_2","author":"assistant","content":{...},"timestamp":...}'

# TTL setting
EXPIRE memory:weather_app/user_001:session_1 86400  # Expires after 24 hours
```

**Notes**:
- When `is_async=True`, the async Redis client is used, which is friendly for concurrent scenarios
- When `is_async=False`, the synchronous Redis client is used
- Redis's `EXPIRE` mechanism automatically handles expired keys, **no background cleanup task required**
- The `cleanup_interval_seconds` parameter has no effect on RedisMemoryService (Redis handles expiration automatically)

**Related Examples**:
- 📁 [`examples/memory_service_with_redis/run_agent.py`](../../../examples/memory_service_with_redis/run_agent.py) - Complete Redis Memory Service usage example

---

### SqlMemoryService

**How It Works**: Stores memory data in a relational database (MySQL/PostgreSQL).

**Implementation Details** (based on `_sql_memory_service.py`):
- **Data Structure**: SQL table `mem_events`
- **Storage Location**: MySQL/PostgreSQL database
- **Search Method**: SQL `SELECT` + keyword matching
- **TTL Mechanism**: Background periodic cleanup task (batch SQL DELETE)
- **Cleanup Method**: Single SQL DELETE for batch deletion of expired events

**Persistence**: ✅ **Yes**. Data is persisted in the database and can be recovered after application restart.

**Applicable Scenarios**:
- ✅ Production environments
- ✅ Transaction safety requirements
- ✅ Complex query and statistical analysis requirements
- ✅ Data persistence and backup requirements

**Configuration Example**:
```python
from trpc_agent_sdk.memory import SqlMemoryService, MemoryServiceConfig
import os

# Read MySQL configuration from environment variables
db_user = os.environ.get("MYSQL_USER", "root")
db_password = os.environ.get("MYSQL_PASSWORD", "")
db_host = os.environ.get("MYSQL_HOST", "127.0.0.1")
db_port = os.environ.get("MYSQL_PORT", "3306")
db_name = os.environ.get("MYSQL_DB", "trpc_agent_memory")

# Build database connection URL
# Synchronous operation (pymysql)
db_url = f"mysql+pymysql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}?charset=utf8mb4"

# Asynchronous operation (aiomysql)
# db_url = f"mysql+aiomysql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}?charset=utf8mb4"

memory_service_config = MemoryServiceConfig(
    enabled=True,
    ttl=MemoryServiceConfig.create_ttl_config(
        enable=True,
        ttl_seconds=86400,              # 24-hour expiration
        cleanup_interval_seconds=3600,  # Cleanup every 1 hour
    ),
)

memory_service = SqlMemoryService(
    db_url=db_url,
    is_async=True,          # Use async mode (recommended)
    memory_service_config=memory_service_config,
    enabled=True,
    pool_pre_ping=True,     # Connection health check (recommended)
    pool_recycle=3600,      # Connection recycle time: 1 hour
)
```

**Database Table Structure**:
```sql
CREATE TABLE mem_events (
    id VARCHAR(255) NOT NULL,              -- Event UUID
    save_key VARCHAR(255) NOT NULL,        -- app_name/user_id
    session_id VARCHAR(255) NOT NULL,       -- Session ID
    invocation_id VARCHAR(255),            -- Invocation ID
    author VARCHAR(255),                    -- Author (user/assistant)
    content JSON,                          -- Event content (JSON)
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,  -- Creation time
    -- ... other fields
    PRIMARY KEY (id, save_key, session_id),
    INDEX idx_save_key (save_key),         -- For retrieval
    INDEX idx_timestamp (timestamp)        -- For cleanup task
);
```

**Cleanup Task** (batch deletion):
```python
# From _sql_memory_service.py
async def _cleanup_expired_async(self) -> None:
    """Batch delete expired events"""
    expire_before = datetime.now() - timedelta(seconds=self._memory_service_config.ttl.ttl_seconds)

    # Single SQL DELETE for batch deletion
    DELETE FROM mem_events
    WHERE timestamp < expire_before;
```

**Notes**:
- When `is_async=True`, the `aiomysql` driver is used; requires installation: `pip install aiomysql`
- When `is_async=False`, the `pymysql` driver is used; requires installation: `pip install pymysql`
- `pool_pre_ping=True` is recommended to avoid stale connections
- `pool_recycle=3600` sets connection recycle time to avoid long-lived connections
- The cleanup task uses batch SQL DELETE for performance optimization

**Related Examples**:
- 📁 [`examples/memory_service_with_sql/run_agent.py`](../../../examples/memory_service_with_sql/run_agent.py) - Complete SQL Memory Service usage example

---

## Comparison of Three Implementations

| Feature | InMemoryMemoryService | RedisMemoryService | SqlMemoryService |
|-----|----------------------|-------------------|------------------|
| **Data Storage** | Process memory | Redis external storage | MySQL/PostgreSQL |
| **Persistence** | ❌ Lost on process restart | ✅ Persisted in Redis | ✅ Persisted in database |
| **Distributed** | ❌ Cannot share across processes | ✅ Supports cross-process/server | ✅ Supports cross-process/server |
| **TTL Mechanism** | ✅ Periodic cleanup task | ✅ **Redis automatic expiration** | ✅ **Periodic cleanup task (batch)** |
| **Cleanup Efficiency** | ⭐⭐⭐ Requires scanning | ⭐⭐⭐⭐⭐ Redis native | ⭐⭐⭐⭐ **Single SQL batch delete** |
| **Transaction Support** | ❌ | ❌ | ✅ **ACID transactions** |
| **Complex Queries** | ❌ | ❌ | ✅ **SQL queries** |
| **Deployment Scenarios** | Local development/single node | Production/distributed/caching | Production/distributed/relational data |
| **Performance** | ⭐⭐⭐⭐⭐ Extremely fast | ⭐⭐⭐⭐ Fast | ⭐⭐⭐ Medium |

**Recommendations**:
- **Development and testing** → `InMemoryMemoryService` (zero dependencies, quick startup)
- **Production (high performance)** → `RedisMemoryService` (Redis automatic expiration, no background tasks)
- **Production (transactions/queries)** → `SqlMemoryService` (transaction safety, supports complex queries)
- **Enterprise (TRPC ecosystem)** → `TrpcRedisMemoryService` (service discovery, monitoring and alerting)

---

## Usage Examples

### Basic Usage Flow

```python
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.memory import InMemoryMemoryService, MemoryServiceConfig
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.types import Content, Part

# 1. Create MemoryService
memory_service_config = MemoryServiceConfig(
    enabled=True,
    ttl=MemoryServiceConfig.create_ttl_config(
        enable=True,
        ttl_seconds=86400,
        cleanup_interval_seconds=3600,
    ),
)
memory_service = InMemoryMemoryService(memory_service_config=memory_service_config)

# 2. Create SessionService
session_service = InMemorySessionService()

# 3. Create Runner and configure services
runner = Runner(
    app_name="my_app",
    agent=my_agent,
    session_service=session_service,
    memory_service=memory_service  # Configure MemoryService
)

# 4. Run Agent (MemoryService will automatically store events)
async for event in runner.run_async(
    user_id=user_id,
    session_id=session_id,
    new_message=user_message
):
    # Process events...
    pass

# 5. Search related memories (via load_memory tool)
# Agent will automatically call memory_service.search_memory()
```

### Manual Storage and Search

```python
# Manually store session to Memory
session = await session_service.get_session(
    app_name="my_app",
    user_id=user_id,
    session_id=session_id
)
if session:
    await memory_service.store_session(session=session)

# Manually search memories
search_key = f"{app_name}/{user_id}"
response = await memory_service.search_memory(
    key=search_key,
    query="user's name",
    limit=10
)

for memory in response.memories:
    print(f"Memory: {memory.content}")
```

---

## Integrating SessionService and MemoryService

In practical applications, you typically need to use both `SessionService` and `MemoryService` together:

```python
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.memory import InMemoryMemoryService, MemoryServiceConfig
from trpc_agent_sdk.runners import Runner

# Create service instances
session_service = InMemorySessionService()
memory_service_config = MemoryServiceConfig(
    enabled=True,
    ttl=MemoryServiceConfig.create_ttl_config(
        enable=True,
        ttl_seconds=86400,
    ),
)
memory_service = InMemoryMemoryService(memory_service_config=memory_service_config)

# Create Runner and configure services
runner = Runner(
    app_name="my_app",
    agent=my_agent,
    session_service=session_service,
    memory_service=memory_service  # Optional: configure MemoryService
)

# Run Agent
async for event in runner.run_async(
    user_id=user_id,
    session_id=session_id,
    new_message=user_message
):
    # Process events...
    pass
```

**Workflow**:

1. **SessionService** manages the context of the current session (conversation history, state, etc.)
2. **MemoryService** automatically stores Session events to long-term memory (if `enabled=True`)
3. **load_memory tool** calls `memory_service.search_memory()` to retrieve related memories
4. The Agent can simultaneously access the current session context and historical memories, providing a more coherent conversation experience

---

## Related Examples

The following examples demonstrate the usage of different MemoryService implementations:

### InMemoryMemoryService

📁 **Example Path**: [`examples/memory_service_with_in_memory/run_agent.py`](../../../examples/memory_service_with_in_memory/run_agent.py)

**Description**:
- Demonstrates basic usage of In-Memory Memory Service
- Shows cross-session memory sharing
- Demonstrates TTL cache eviction mechanism
- Includes detailed analysis of execution results

**How to Run**:
```bash
cd examples/memory_service_with_in_memory/
python3 run_agent.py
```

---

### RedisMemoryService

📁 **Example Path**: [`examples/memory_service_with_redis/run_agent.py`](../../../examples/memory_service_with_redis/run_agent.py)

**Description**:
- Demonstrates Redis Memory Service usage
- Shows Redis automatic expiration mechanism
- Provides detailed Redis operations guide
- Includes execution result analysis and Redis command examples

**How to Run**:
```bash
cd examples/memory_service_with_redis/
python3 run_agent.py
```

---

### SqlMemoryService

📁 **Example Path**: [`examples/memory_service_with_sql/run_agent.py`](../../../examples/memory_service_with_sql/run_agent.py)

**Description**:
- Demonstrates SQL Memory Service usage
- Shows MySQL table structure and data operations
- Demonstrates batch cleanup task
- Provides MySQL operation commands and execution result analysis

**How to Run**:
```bash
cd examples/memory_service_with_sql/
python3 run_agent.py
```

---

## Integrating Mem0

### What is Mem0?

Mem0 is an intelligent, self-improving memory layer for LLMs that can persist and retrieve user information across conversations, enabling more personalized and coherent user experiences.

**Core Capabilities:**
- 🧠 Intelligent memory extraction and storage
- 🔍 Semantic search of historical conversations
- 🔄 Automatic memory updates and deduplication
- 🎯 User-level memory isolation

**Official Resources:**
- Official documentation: [https://docs.mem0.ai/introduction](https://docs.mem0.ai/introduction)
- GitHub: [https://github.com/mem0ai/mem0](https://github.com/mem0ai/mem0)

---

### tRPC-Agent Integration Methods

tRPC-Agent provides two methods for integrating Mem0:

| Method | Class / Tool | Applicable Scenario |
|---|---|---|
| **Framework-level memory service** (recommended) | `Mem0MemoryService` | The framework automatically handles cross-session memory storage and retrieval, transparent to the Agent |
| **Tool-based memory** | `SearchMemoryTool` / `SaveMemoryTool` | The Agent actively calls Mem0 through tools, with flexible control over storage and retrieval timing |

---

### Mem0MemoryService (Recommended)

`Mem0MemoryService` is tRPC-Agent's **framework-level memory service**. The framework automatically calls `store_session` after each turn of the conversation completes to store session memories. The Agent actively retrieves related memories through the `load_memory` tool when generating a response, without manual management of storage and retrieval timing.

#### Core Design

- **Two-level key strategy**: `session.save_key` → Mem0 `user_id` (user dimension); `session.id` → `run_id` (session dimension)
- **Cross-session sharing**: Different sessions from the same user share the same memory
- **TTL automatic expiration**: Background periodic cleanup of expired memories

#### Quick Integration

**Step 1: Create `Mem0MemoryService`**

```python
from mem0 import AsyncMemory, AsyncMemoryClient
from trpc_agent_sdk.memory import MemoryServiceConfig
from trpc_agent_sdk.memory.mem0_memory_service import Mem0MemoryService

# Self-hosted mode (AsyncMemory + Qdrant)
from mem0.configs.base import MemoryConfig
mem0_client = AsyncMemory(config=MemoryConfig(**{
    "vector_store": {"provider": "qdrant", "config": {"host": "localhost", "port": 6333}},  # Vector database declaration
    "llm": {"provider": "deepseek", "config": {"model": "...", "api_key": "..."}},          # Used for memory summarization (used when infer=True)
    "embedder": {"provider": "huggingface", "config": {"model": "multi-qa-MiniLM-L6-cos-v1"}},  # Open-source embedding model
}))

# Or: Remote platform mode (AsyncMemoryClient), no self-hosted infrastructure needed
mem0_client = AsyncMemoryClient(api_key="your_mem0_api_key", host="https://api.mem0.ai")

memory_service = Mem0MemoryService(
    mem0_client=mem0_client,
    memory_service_config=MemoryServiceConfig(
        enabled=True,
        ttl=MemoryServiceConfig.create_ttl_config(enable=False),  # Disable TTL, memories are retained permanently
    ),
    infer=False,   # False=store raw content (stable), True=semantic extraction (intelligent)
)
```

**Step 2: Pass `memory_service` to `Runner`**

```python
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.tools import load_memory_tool

agent = LlmAgent(
    name="assistant",
    model=your_model,
    tools=[load_memory_tool],   # Agent actively retrieves memories through this tool
    instruction="Use load_memory to recall relevant past conversations before answering.",
)

runner = Runner(
    app_name="my_app",
    agent=agent,
    session_service=InMemorySessionService(),
    memory_service=memory_service,   # Framework handles storage automatically
)
```

**Step 3: Run, memories are automatically persisted across sessions**

```python
# First conversation round (session_1)
async for event in runner.run_async(user_id="alice", session_id="session_1", new_message=...):
    ...
# The framework automatically calls store_session after the conversation ends, storing this round's messages into Mem0

# Second conversation round (session_2) — new session, but can retrieve memories from session_1
async for event in runner.run_async(user_id="alice", session_id="session_2", new_message=...):
    ...
```

#### `infer` Parameter Selection

| | `infer=False` (recommended) | `infer=True` |
|---|---|---|
| Stored Content | Raw conversation text | Semantic facts extracted by LLM |
| Stability | High, every entry is stored | Medium, not stored when LLM determines NONE |
| Token Consumption | Low (no LLM calls) | High (LLM called on each write) |
| Conflict Resolution | None | Automatic (new facts override old facts) |
| Recommended Scenario | Complete history archival, production environments | Long-term user profiling, preference extraction |

#### TTL Configuration (Optional)

```python
memory_service_config = MemoryServiceConfig(
    enabled=True,
    ttl=MemoryServiceConfig.create_ttl_config(
        enable=True,
        ttl_seconds=86400,           # Memory retention: 24 hours
        cleanup_interval_seconds=3600,  # Cleanup every 1 hour
    ),
)
```

> For detailed explanations, execution result analysis, and FAQ: [examples/memory_service_with_mem0/README.md](../../../examples/memory_service_with_mem0/README.md)

---

### Tool-based Integration (mem0_tool)

tRPC-Agent integrates Mem0 through **Tools**, providing memory capabilities to Agents. The framework provides two core tool classes:

| Tool Class | Tool Name | Function | Use Case |
|--------|--------|------|---------|
| `SearchMemoryTool` | `search_memory` | Search historical memories | When the Agent needs to recall past conversations |
| `SaveMemoryTool` | `save_memory` | Save important information | When the Agent determines user information should be remembered |

> **Note**: Both tool classes require a Mem0 client to be passed during instantiation. The `user_id` is automatically injected by the framework through `InvocationContext` and does not need to be explicitly passed as a tool parameter.

#### Integration Architecture

```
┌──────────────────────┐
│    User Input        │
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│  tRPC-Agent          │◄─────────┐
│  LlmAgent            │          │
└──────────┬───────────┘          │
           │                      │
           │ Call tools            │ Return memories
           │                      │
           ▼                      │
┌──────────────────────┐          │
│  Mem0 Tools          │──────────┘
│  - SearchMemoryTool  │
│  - SaveMemoryTool    │
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│  Mem0 Client         │
│  (AsyncMemory /      │
│   AsyncMemoryClient) │
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│  Storage             │
│  - Qdrant            │
│  - Mem0 Cloud        │
└──────────────────────┘
```

---

### Deployment Modes

tRPC-Agent supports two deployment modes for Mem0: self-hosted mode and platform mode

#### Mode Comparison

| Feature | Self-hosted Mode | Platform Mode |
|------|-----------|---------|
| **Client Type** | `AsyncMemory` | `AsyncMemoryClient` |
| **Storage Location** | Local vector database (e.g., Qdrant) | Mem0 Cloud |
| **Dependencies** | Vector database + Embedding model + LLM | API Key only |
| **Data Control** | Full control | Managed service |
| **Applicable Scenario** | Development testing, data-sensitive, local deployment | Production environment, rapid deployment |

#### Mode 1: Self-hosted (AsyncMemory)

Suitable for scenarios requiring full control over data and infrastructure.

**Core Components:**
- **Vector Store**: Supports multiple backends (see the complete list below)
- **LLM**: Used for generating memory summaries (OpenAI / DeepSeek / Gemini, etc.)
- **Embedding Model**: Used for vectorization (HuggingFace / OpenAI, etc.)

**Complete List of Supported Vector Stores for Self-hosted:**
- `azure_ai_search`
- `azure_mysql`
- `baidu`
- `cassandra`
- `chroma`
- `databricks`
- `elasticsearch`
- `faiss`
- `langchain`
- `milvus`
- `mongodb`
- `neptune_analytics`
- `opensearch`
- `pgvector`
- `pinecone`
- `qdrant`
- `redis`
- `s3_vectors`
- `supabase`
- `turbopuffer`
- `upstash_vector`
- `valkey`
- `vertex_ai_vector_search`
- `weaviate`

> Official vector store implementation list (refer to the mem0 repository): [mem0/vector_stores](https://github.com/mem0ai/mem0/tree/main/mem0/vector_stores)

**Example Code:**
```python
from mem0 import AsyncMemory
from trpc_agent_sdk.server.tools.mem0_tool import SearchMemoryTool, SaveMemoryTool

# Configure custom components
config = {
    "vector_store": {"provider": "qdrant", "config": {...}},
    "llm": {"provider": "deepseek", "config": {...}},
    "embedder": {"provider": "huggingface", "config": {...}}
}

# Create Mem0 client
memory = await AsyncMemory.from_config(config)

# Instantiate tools with the client
search_memory_tool = SearchMemoryTool(client=memory)
save_memory_tool = SaveMemoryTool(client=memory)
```

**Detailed Configuration:** See [Complete Example - Self-hosted Mode](../../../examples/memory_service_with_mem0/README.md#自托管模式asyncmemory--qdrant)

#### Mode 2: Platform (AsyncMemoryClient)

Suitable for rapid deployment and production environment usage.

**Prerequisites:**
- Register a [Mem0 platform account](https://app.mem0.ai/dashboard)
- Obtain an API Key

**Example Code:**
```python
from mem0 import AsyncMemoryClient
from trpc_agent_sdk.server.tools.mem0_tool import SearchMemoryTool, SaveMemoryTool

# Create platform client
client = AsyncMemoryClient(
    api_key="m0-your-api-key",
    host="https://api.mem0.ai"
)

# Instantiate tools with the client
search_memory_tool = SearchMemoryTool(client=client)
save_memory_tool = SaveMemoryTool(client=client)
```

**Detailed Configuration:** See [Complete Example - Platform Mode](../../../examples/memory_service_with_mem0/README.md#远端平台模式asyncmemoryclient)

---

### Mem0 Quick Start

#### 1. Install Dependencies

```bash
# Install Mem0 core package
pip install mem0ai

# Additional dependencies for self-hosted mode
pip install sentence-transformers qdrant-client

# Or install via trpc-agent extension
pip install trpc-agent[mem0]
```

#### 2. Create Agent

```python
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.server.tools.mem0_tool import SearchMemoryTool, SaveMemoryTool

# Step 1: Instantiate tools, pass in Mem0 client (choose self-hosted or platform mode)
search_memory_tool = SearchMemoryTool(client=your_mem0_client)
save_memory_tool = SaveMemoryTool(client=your_mem0_client)

# Step 2: Create Agent with memory tools
agent = LlmAgent(
    name="memory_assistant",
    description="A personal assistant with memory capabilities",
    model=your_model,
    instruction="""
    You are a helpful assistant with memory capabilities.
    - Use search_memory to recall past conversations
    - Use save_memory to store important information
    - Always personalize responses based on memory
    """,
    tools=[search_memory_tool, save_memory_tool],
)
```

#### 3. Run Agent

```python
from trpc_agent_sdk.runners import Runner

runner = Runner(
    app_name="memory_app",
    agent=agent,
    session_service=your_session_service
)

# Interact with Agent, memory features are used automatically
async for event in runner.run_async(
    user_id="alice",
    session_id="session_1",
    new_message=user_input
):
    # Process response
    pass
```

**Complete Runnable Example:** [examples/memory_service_with_mem0/run_agent.py](../../../examples/memory_service_with_mem0/run_agent.py)

---

### Tool API

#### SearchMemoryTool

Search the user's historical memories.

**Constructor:**
```python
SearchMemoryTool(
    client: Union[AsyncMemoryClient, AsyncMemory],
    filters_name: str | None = None,   # Optional: filter name passed through to BaseTool
    filters: dict | None = None,       # Optional: filter conditions passed through to BaseTool
    **kwargs,                          # Optional: additional parameters passed through to client.search() (e.g., limit)
)
```

**Agent Tool Parameters (callable by LLM):**
- `query` (string, required): Search query content (natural language)

> `user_id` is automatically injected by the framework from `InvocationContext` and does not need to be passed as a tool parameter.

**Return Value:**
```python
# Memories found successfully
{
    "status": "success",
    "memories": "- Memory content 1\n- Memory content 2",
    "user_id": "alice"
}

# No memories found
{
    "status": "no_memories",
    "message": "No relevant memories found"
}
```

#### SaveMemoryTool

Save important information to user memory.

**Constructor:**
```python
SaveMemoryTool(
    client: Union[AsyncMemoryClient, AsyncMemory],
    filters_name: str | None = None,   # Optional: filter name passed through to BaseTool
    filters: dict | None = None,       # Optional: filter conditions passed through to BaseTool
    infer: bool = True,                # Optional: whether to enable LLM semantic extraction (default True)
    **kwargs,                          # Optional: additional parameters passed through to client.add()
)
```

> When `infer=True`, Mem0 calls the LLM for semantic extraction before storage; when `infer=False`, the raw content is stored directly.

**Agent Tool Parameters (callable by LLM):**
- `content` (string, required): Content to save

> `user_id` is automatically injected by the framework from `InvocationContext` and does not need to be passed as a tool parameter.

**Return Value:**
```python
# Save successful
{
    "status": "success",
    "message": "Information saved to memory",
    "result": {...},
    "user_id": "alice"
}

# Save failed
{
    "status": "error",
    "message": "Failed to save memory: error details",
    "user_id": "alice"
}
```

**Tool Source Code:** [trpc_agent_sdk/tools/mem0_tool.py](../../../trpc_agent_sdk/tools/mem0_tool.py)

---

### Typical Workflow (Tool-based)

#### Scenario: Personal Assistant Remembering User Preferences

```
1. User: Do you remember my name?
   ↓
   Agent calls: search_memory(query="user's name")
   Framework automatically injects user_id="alice"
   ↓
   Result: no_memories
   ↓
   Agent: I don't have your name. Could you tell me?

2. User: My name is Alice
   ↓
   Agent calls: save_memory(content="User's name is Alice")
   Framework automatically injects user_id="alice"
   ↓
   Result: success
   ↓
   Agent: Thank you, Alice! I'll remember that.

3. User: Do you remember my name?
   ↓
   Agent calls: search_memory(query="user's name")
   Framework automatically injects user_id="alice"
   ↓
   Result: success, memories="- Name is Alice"
   ↓
   Agent: Yes, your name is Alice!
```

**View Complete Demo Output (Mem0MemoryService):** [Execution Result Analysis](../../../examples/memory_service_with_mem0/README.md#运行结果分析)

---

### Advanced Features

#### Multi-user Memory Isolation

Memory isolation at the user level is achieved through the `user_id` parameter:

```python
# User A's memories
await runner.run_async(user_id="user_a", ...)

# User B's memories (completely independent)
await runner.run_async(user_id="user_b", ...)
```

#### Memory Filtering and Search

The `filters` parameter enables fine-grained memory retrieval, supporting filtering by user, category, and other dimensions to avoid interference from cross-user or irrelevant memories:

```python
memories = await mem0_client.search(
    query="favorite food",       # Semantic search query (Mem0 vectorizes and matches)
    filters={
        "user_id": "alice",      # Restrict to user scope, ensuring memory isolation
        "category": "preferences",  # Custom category tag, narrowing search scope
    },
    limit=5,                     # Return at most 5 most relevant memories
)
```

#### Direct Memory Management

In addition to indirect operations through Agent tools, you can also directly call the Mem0 client API to manage memories (add, delete, query):

```python
# Get all memories for a specific user
all_memories = await memory.get_all(user_id="alice")

# Delete a single memory by memory_id
await memory.delete(memory_id="memory-id")

# Clear all memories for the user
await memory.delete_all(user_id="alice")
```

**More Advanced Usage:** [Advanced Usage Documentation](../../../examples/mem_0/README.md#高级用法)

---

### Mem0 FAQ

#### How to Choose a Deployment Mode?

| Consideration | Self-hosted | Platform |
|---------|-------|------|
| High data privacy requirements | ✅ | ❌ |
| Quick startup | ❌ | ✅ |
| Need custom embedding models | ✅ | ❌ |
| Production high availability | ❌ | ✅ |
| Cost-sensitive (small scale) | ✅ | ❌ |

#### Common Errors in Self-hosted Mode

**Vector Dimension Mismatch:**
```
Vector dimension error: expected dim: 1536, got 384
```
**Cause:** Embedding model dimensions do not match the vector database collection.
**Solution:** Ensure the embedding model and vector database collection have matching dimensions (e.g., `multi-qa-MiniLM-L6-cos-v1` outputs 384 dimensions).

**Cannot Connect to Qdrant:**
```
ConnectionError: Cannot connect to Qdrant at localhost:6333
```
**Solution:** Confirm Qdrant is running (`docker run -p 6333:6333 qdrant/qdrant`).

**More Issues:** [Mem0MemoryService FAQ](../../../examples/memory_service_with_mem0/README.md#常见问题-qa)

---

### Mem0 References

#### Framework Resources

| Resource | Path | Description |
|---|---|---|
| `Mem0MemoryService` complete example | [examples/memory_service_with_mem0/](../../../examples/memory_service_with_mem0/README.md) | Includes execution result analysis, FAQ |
| `Mem0MemoryService` source code | [mem0_memory_service.py](../../../trpc_agent_sdk/memory/mem0_memory_service.py) | Service implementation |
| Tool-based integration source code | [mem0_tool.py](../../../trpc_agent_sdk/tools/mem0_tool.py) | `SearchMemoryTool` / `SaveMemoryTool` tool classes |
| infer parameter details | [README.md#infer-参数详解](../../../examples/memory_service_with_mem0/README.md#infer-参数详解) | True vs False comparison |
| FAQ | [README.md#常见问题-qa](../../../examples/memory_service_with_mem0/README.md#常见问题-qa) | Error analysis and answers |

#### Mem0 Official Resources
- **Official Documentation:** [https://docs.mem0.ai/introduction](https://docs.mem0.ai/introduction)
- **GitHub:** [https://github.com/mem0ai/mem0](https://github.com/mem0ai/mem0)
- **Example Code:** [https://github.com/mem0ai/mem0/tree/main/examples](https://github.com/mem0ai/mem0/tree/main/examples)
- **Platform Console:** [https://app.mem0.ai/dashboard](https://app.mem0.ai/dashboard)

---

### Next Steps

1. **Quick Start (recommended):** Check out the [Mem0MemoryService Complete Example](../../../examples/memory_service_with_mem0/) and run `run_agent.py`
2. **Choose Deployment Mode:** Refer to the [Self-hosted vs Remote Platform Comparison](../../../examples/memory_service_with_mem0/README.md#两种部署模式详解)
3. **Understand infer Differences:** Refer to [infer Parameter Details](../../../examples/memory_service_with_mem0/README.md#infer-参数详解) to choose the appropriate configuration
4. **Platform Deployment:** Register on the [Mem0 Platform](https://app.mem0.ai/dashboard) and obtain an API Key
5. **Custom Development:** Extend custom logic based on the [Mem0MemoryService Source Code](../../../trpc_agent_sdk/memory/mem0_memory_service.py)

---

## Core Feature Summary

### 1. Cross-session Memory Sharing

- ✅ Different sessions can access the same memory data
- ✅ Uses `save_key` (`app_name/user_id`) as the memory key
- ✅ Suitable for storing user profiles, long-term preferences, and other cross-session information

### 2. Keyword Search

- ✅ Supports keyword extraction and matching for both Chinese and English
- ✅ Uses `extract_words_lower` to extract English words and Chinese characters
- ✅ Matching logic: returns on any query word match

### 3. TTL Cache Eviction

- ✅ Automatically cleans up expired memories, preventing unlimited storage growth
- ✅ Refreshes TTL on access (during `search_memory`)
- ✅ Different implementations use different cleanup mechanisms

### 4. Automatic Storage

- ✅ When `enabled=True`, MemoryService automatically stores Session events
- ✅ No need to manually call `store_session` (unless special control is required)
- ✅ Only stores events with content (`event.content and event.content.parts`)

### 5. Flexible Storage Backends

- ✅ Supports multiple implementations: In-Memory, Redis, SQL, Mem0, etc.
- ✅ Supports TRPC Redis integration
- ✅ Supports Mem0 semantic memory integration (vector search + LLM extraction)
- ✅ Choose the appropriate implementation based on your scenario

---

## Notes

### 1. enabled Parameter

- `enabled=True`: MemoryService automatically stores Session events, **no need to manually call `store_session`**
- `enabled=False`: MemoryService does not store any data; both `store_session` and `search_memory` will have no effect

### 2. Keyword Search Limitations

- The current implementation uses **keyword (token) matching**, not semantic search
- After `extract_words_lower` (whole English words, individual Chinese characters), **any** query token that appears in the event's token set counts as a match (this is not full-sentence semantic similarity)
- Suitable for rapid prototyping, not suitable for complex semantic retrieval requirements

### 3. TTL Configuration

- `ttl_seconds`: Memory expiration time (in seconds)
- `cleanup_interval_seconds`: Cleanup interval (InMemory/SQL only; Redis handles expiration automatically)
- TTL is automatically refreshed on access, extending the memory's validity period

### 4. Concurrency Safety

- `InMemoryMemoryService`: Thread-safe within a single process
- `RedisMemoryService`: Supports multi-process/multi-server concurrency
- `SqlMemoryService`: Supports multi-process/multi-server concurrency (using database transactions)

---

## Summary

MemoryService provides powerful long-term memory management capabilities:

- ✅ **Cross-session sharing**: Different sessions can access shared memories
- ✅ **Automatic storage**: Automatically stores Session events when `enabled=True`
- ✅ **Keyword search**: Supports Chinese and English keyword matching
- ✅ **TTL eviction**: Automatically cleans up expired memories
- ✅ **Multiple implementations**: In-Memory, Redis, SQL, TRPC Redis, Mem0

Through proper use of MemoryService, you can achieve:
- User profile construction
- Long-term preference memory
- Cross-session knowledge sharing
- Intelligent conversation context

For more detailed usage examples, please refer to the related examples in the `examples/` directory.

- [examples/memory_service_with_in_memory/run_agent.py](../../../examples/memory_service_with_in_memory/run_agent.py)
- [examples/memory_service_with_redis/run_agent.py](../../../examples/memory_service_with_redis/run_agent.py)
- [examples/memory_service_with_sql/run_agent.py](../../../examples/memory_service_with_sql/run_agent.py)
- [examples/memory_service_with_mem0/run_agent.py](../../../examples/memory_service_with_mem0/run_agent.py)
