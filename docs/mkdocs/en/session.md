# Session Management

The tRPC-Agent framework provides powerful Session management capabilities for maintaining conversation history and context information during Agent-user interactions. Through automatic persistence of conversation records, intelligent summary compression, and flexible storage backends, session management provides a complete infrastructure for building stateful intelligent Agents.

## Session Service

### Overview

In trpc-agent, `SessionService` is used to manage `Session` (sessions). A `Session` is a collection of multi-turn conversations that stores the interaction records between users and Agents, as well as between Agents.

#### Session vs Memory

| Feature | Session | Memory |
|---------|---------|--------|
| **Scope** | Single session | Cross-session (shared across all sessions) |
| **Lifecycle** | Created and destroyed with the session | Independent of sessions, controlled by TTL |
| **Stored Content** | Complete conversation history of the current session | Key events and knowledge fragments |
| **Access Method** | Automatically loaded into context | Retrieved via the `load_memory` tool |
| **Typical Use** | Context of a single conversation | Long-term memory, user profiles, knowledge accumulation |

---

### Core Structure of Session

Based on the implementation in `trpc_agent/sessions/_session.py`, a `Session` contains the following key fields:

#### 1. Identity

- **`id`**: Session ID, recommended to generate using UUID
- **`app_name`**: Identifies which App this conversation belongs to
- **`user_id`**: Identifies which User this conversation belongs to
- **`save_key`**: Format is `{app_name}/{user_id}`, used for storage and retrieval

#### 2. Conversation Records (Events)

- **`events`**: A list of `Event` objects, stored in chronological order
- **Event Types**: User messages, Agent responses, tool operations, etc.
- **Event Filtering**: Supports TTL and maximum count limits (`event_ttl_seconds`, `max_events`)

**Event Filtering Logic** (`_session.py`):
```python
def apply_event_filtering(self, event_ttl_seconds: float = 0.0, max_events: int = 0) -> None:
    """Apply event filtering: TTL filtering + count limit"""
    # 1. TTL filtering: remove expired events
    if event_ttl_seconds > 0:
        cutoff_time = time.time() - event_ttl_seconds
        self.events = [e for e in self.events if e.timestamp >= cutoff_time]

    # 2. Count limit: keep only the most recent max_events events
    if max_events > 0:
        if len(self.events) > max_events:
            self.events = self.events[-max_events:]

    # 3. Protect the first user message (if all events have been filtered)
    # Ensure at least one user message is retained to maintain conversation context integrity
```

#### 3. Session State (State)

- **`state`**: Dictionary type, stores session-related data
- **State Scopes**:
  - **Session State**: Session-level state (stored in `session.state`)
  - **User State**: User-level state (stored in `SessionService`, key prefix `user:`)
  - **App State**: Application-level state (stored in `SessionService`, key prefix `app:`)
  - **Temp State**: Temporary state (not persisted, key prefix `temp:`)

**State Merge Logic** (`_utils.py`):
```python
def extract_state_delta(state_delta: Optional[dict[str, Any]]) -> StateStorageEntry:
    """Extract state changes, separated into app, user, and session state"""
    # Separate state by key prefix:
    # - 'app:' prefix → app_state_delta
    # - 'user:' prefix → user_state_delta
    # - 'temp:' prefix → ignored (not persisted)
    # - others → session_state
```

#### 4. Metadata

- **`last_update_time`**: Last update time (timestamp)
- **`conversation_count`**: Number of conversation turns

---

### Core Features of SessionService

Based on the implementation in `trpc_agent/sessions/`, `SessionService` provides the following core features:

#### 1. Session Management (CRUD)

**Create Session**:
```python
session = await session_service.create_session(
    app_name="my_app",
    user_id="user_001",
    session_id=str(uuid.uuid4()),  # Optional; auto-generated if not provided
    state={"initial_key": "initial_value"}  # Optional initial state
)
```

**Get Session**:
```python
session = await session_service.get_session(
    app_name="my_app",
    user_id="user_001",
    session_id=session_id
)
```

**List Sessions**:
```python
session_list = await session_service.list_sessions(
    app_name="my_app",
    user_id="user_001"
)
# Returns ListSessionsResponse, containing all sessions for the user (without events)
```

**Delete Session**:
```python
await session_service.delete_session(
    app_name="my_app",
    user_id="user_001",
    session_id=session_id
)
```

**Implementation Logic** (`_base_session_service.py`):
- `create_session`: Creates a session, separates and stores app/user/session state
- `get_session`: Retrieves a session, merges app/user/session state, applies event filtering
- `list_sessions`: Lists sessions (excludes events to reduce data transfer)
- `delete_session`: Deletes a session and its associated data

---

#### 2. Append Event

**Functionality**: Appends new events to a session, automatically updating state and TTL.

**Implementation Logic** (`_base_session_service.py`):
```python
async def append_event(self, session: Session, event: Event) -> Event:
    """Append an event to the session"""
    # 1. Skip partial events
    if event.partial:
        return event

    # 2. Remove temporary state (temp: prefix)
    event = self._trim_temp_delta_state(event)

    # 3. Update session state (session.state)
    self.__update_session_state(session, event)

    # 4. Add the event and apply filtering (TTL + max_events)
    session.add_event(event,
                      event_ttl_seconds=self._session_config.event_ttl_seconds,
                      max_events=self._session_config.max_events)

    # 5. Update storage (app/user state handled by specific implementation)
    return event
```

**State Update**:
- **Session State**: Directly updates `session.state`
- **User State**: Updates user state in `SessionService` (key prefix `user:`)
- **App State**: Updates application state in `SessionService` (key prefix `app:`)
- **Temp State**: Not persisted, exists only in memory

---

#### 3. Event Filtering

**Functionality**: Filters events based on TTL and maximum count limits to prevent excessively long context.

**Configuration** (`SessionServiceConfig`):
```python
from trpc_agent_sdk.sessions import SessionServiceConfig

session_config = SessionServiceConfig(
    event_ttl_seconds=3600,  # Event TTL: 1 hour
    max_events=100,          # Maximum event count: 100
    num_recent_events=10,    # Keep the most recent N events (optional)
)
```

**Filtering Timing**:
- **On Event Append**: `append_event` automatically applies filtering
- **On Session Retrieval**: `get_session` automatically applies filtering

**Filtering Logic** (`_session.py`):
1. **TTL Filtering**: Removes events where `timestamp < (now - event_ttl_seconds)`
2. **Count Limit**: Keeps only the most recent `max_events` events
3. **User Message Protection**: If all events are filtered out, at least the first user message is retained

---

#### 4. TTL (Time-To-Live) Cache Eviction

**Functionality**: Automatically cleans up expired session data to prevent unbounded storage growth.

**TTL Configuration** (`SessionServiceConfig`):
```python
from trpc_agent_sdk.sessions import SessionServiceConfig

session_config = SessionServiceConfig(
    ttl=SessionServiceConfig.create_ttl_config(
        enable=True,                    # Enable TTL
        ttl_seconds=86400,              # Session expiration: 24 hours
        cleanup_interval_seconds=3600,  # Cleanup interval: 1 hour (InMemory/SQL only)
    ),
)
```

**TTL Refresh Mechanism**:
- **Refresh on Access**: TTL is refreshed when `get_session` is called
- **Refresh on Update**: TTL is refreshed when `append_event` is called
- **Refresh on State Access**: TTL is refreshed when app/user state is accessed

**Implementation Differences**:
- **InMemorySessionService**: Background periodic cleanup task (`_cleanup_loop`)
- **RedisSessionService**: Native Redis `EXPIRE` mechanism (automatic expiration)
- **SqlSessionService**: Background periodic cleanup task (batch SQL DELETE)

---

#### 5. State Scope Management

**Functionality**: Supports state storage and access across different scopes.

**State Scopes** (`_utils.py`):

| Scope | Prefix | Storage Location | Lifecycle | Example |
|-------|--------|-----------------|-----------|---------|
| **Session State** | No prefix | `session.state` | With session | `{"current_topic": "weather"}` |
| **User State** | `user:` | `SessionService` | Cross-session, user-level | `{"user:name": "Alice"}` |
| **App State** | `app:` | `SessionService` | Cross-session, application-level | `{"app:version": "1.0"}` |
| **Temp State** | `temp:` | Memory | Temporary, not persisted | `{"temp:cache": "..."}` |

**State Merge** (during `get_session`):
```python
# From _in_memory_session_service.py
async def get_session(...) -> Optional[Session]:
    session = self._get_session(app_name, user_id, session_id)
    app_state = self._get_app_state(app_name)      # Get app state
    user_state = self._get_user_state(app_name, user_id)  # Get user state

    # Merge state: session.state + user_state + app_state
    return self._merge_state(app_state, user_state, session)
```

---

#### 6. Session Summarization

**Functionality**: Compresses long conversations into summaries to reduce context length.

**Configuration** (`SummarizerSessionManager`):
```python
from trpc_agent_sdk.sessions import SummarizerSessionManager, SessionSummarizer

summarizer = SessionSummarizer(...)
summarizer_manager = SummarizerSessionManager(summarizer=summarizer)

# Set summarization trigger conditions
set_summarizer_conversation_threshold(summarizer_manager, threshold=10)  # Summarize after 10 conversation turns
set_summarizer_events_count_threshold(summarizer_manager, threshold=50)  # Summarize after 50 events

session_service = InMemorySessionService(summarizer_manager=summarizer_manager)
```

**Trigger Timing**:
- Conversation turn count reaches the threshold
- Event count reaches the threshold
- Time interval reaches the threshold
- Content length reaches the threshold

---

### SessionService Implementations

trpc-agent provides three `SessionService` implementations, allowing you to choose the appropriate storage backend based on your scenario:

#### InMemorySessionService

**How It Works**: Stores all session data directly in the application's memory.

**Implementation Details** (based on `_in_memory_session_service.py`):
- **Data Structures**:
  - `__sessions`: `dict[app_name, dict[user_id, dict[session_id, SessionWithTTL]]]`
  - `__user_state`: `dict[app_name, dict[user_id, StateWithTTL]]`
  - `__app_state`: `dict[app_name, StateWithTTL]`
- **Storage Location**: Process memory
- **TTL Mechanism**: Background periodic cleanup task (`_cleanup_loop`)
- **Cleanup Strategy**: Two-phase deletion (collect expired items → batch delete)

**Persistence**: ❌ **None**. All session data is lost if the application restarts.

**Applicable Scenarios**:
- ✅ Rapid development
- ✅ Local testing
- ✅ Demo and examples
- ✅ Scenarios that do not require long-term persistence

**Configuration Example**:
```python
from trpc_agent_sdk.sessions import InMemorySessionService, SessionServiceConfig

session_config = SessionServiceConfig(
    event_ttl_seconds=3600,  # Event TTL: 1 hour
    max_events=100,          # Maximum event count: 100
    ttl=SessionServiceConfig.create_ttl_config(
        enable=True,
        ttl_seconds=86400,              # Session expiration: 24 hours
        cleanup_interval_seconds=3600,  # Cleanup interval: 1 hour
    ),
)

session_service = InMemorySessionService(session_config=session_config)
```

**Notes**:
- The cleanup task runs in the background, periodically deleting expired sessions and state
- If `ttl.enable=False`, the cleanup task is not started
- State merge: `get_session` automatically merges app/user/session state

**Related Examples**:
- 📁 [`examples/session_service_with_in_memory/run_agent.py`](../../../examples/session_service_with_in_memory/run_agent.py) - Complete In-Memory Session Service usage example

---

#### RedisSessionService

**How It Works**: Uses Redis to store session data, supporting multi-node sharing.

**Implementation Details** (based on `_redis_session_service.py`):
- **Data Structure**: Redis Hash (stores Session JSON)
- **Storage Location**: External Redis storage
- **Key Format**:
  - Session: `session:{app_name}:{user_id}:{session_id}`
  - User state: `user_state:{app_name}:{user_id}`
  - App state: `app_state:{app_name}`
- **TTL Mechanism**: Native Redis `EXPIRE` command (automatic expiration)
- **TTL Refresh**: Automatically refreshed on access and update

**Persistence**: ✅ **Yes**. Data is persisted to Redis; sessions can be recovered after application restart.

**Applicable Scenarios**:
- ✅ Production environments
- ✅ Multi-node deployments
- ✅ High-performance caching requirements
- ✅ Distributed applications

**Configuration Example**:
```python
from trpc_agent_sdk.sessions import RedisSessionService, SessionServiceConfig
import os

# Read Redis configuration from environment variables
db_host = os.environ.get("REDIS_HOST", "127.0.0.1")
db_port = os.environ.get("REDIS_PORT", "6379")
db_password = os.environ.get("REDIS_PASSWORD", "")
db_db = os.environ.get("REDIS_DB", 0)

# Build the Redis connection URL
if db_password:
    db_url = f"redis://:{db_password}@{db_host}:{db_port}/{db_db}"
else:
    db_url = f"redis://{db_host}:{db_port}/{db_db}"

session_config = SessionServiceConfig(
    event_ttl_seconds=3600,
    max_events=100,
    ttl=SessionServiceConfig.create_ttl_config(
        enable=True,
        ttl_seconds=86400,  # 24-hour expiration (handled automatically by Redis)
    ),
)

session_service = RedisSessionService(
    db_url=db_url,
    is_async=True,          # Use async mode (recommended)
    session_config=session_config,
    **kwargs  # Other Redis connection parameters
)
```

**Redis Data Structure**:
```bash
# Session storage (Redis Hash)
session:weather_app:user_001:session_123
  └─ Field: JSON-serialized Session object

# TTL setting
EXPIRE session:weather_app:user_001:session_123 86400  # Expires after 24 hours
```

**Notes**:
- When `is_async=True`, an async Redis client is used, which is concurrency-friendly
- When `is_async=False`, a synchronous Redis client is used
- Redis `EXPIRE` mechanism automatically handles expired keys, **no background cleanup task is needed**
- The `cleanup_interval_seconds` parameter has no effect on RedisSessionService (Redis handles expiration natively)

**Related Examples**:
- 📁 [`examples/session_service_with_redis/run_agent.py`](../../../examples/session_service_with_redis/run_agent.py) - Complete Redis Session Service usage example

---

#### SqlSessionService

**How It Works**: Stores all session data in a relational database (MySQL/PostgreSQL).

**Implementation Details** (based on `_sql_session_service.py`):
- **Data Structure**: SQL tables
  - `sessions` table: Stores session metadata
  - `events` table: Stores session events (foreign key association)
- **Storage Location**: MySQL/PostgreSQL database
- **TTL Mechanism**: Background periodic cleanup task (batch SQL DELETE)
- **Cleanup Strategy**: Single SQL DELETE for batch deletion of expired sessions and events

**Persistence**: ✅ **Yes**. Data is persisted to the database; sessions can be recovered after application restart.

**Applicable Scenarios**:
- ✅ Production environments
- ✅ Transaction safety requirements
- ✅ Complex queries and statistical analysis
- ✅ Data persistence and backup requirements

**Configuration Example**:
```python
from trpc_agent_sdk.sessions import SqlSessionService, SessionServiceConfig
import os

# Read MySQL configuration from environment variables
db_user = os.environ.get("MYSQL_USER", "root")
db_password = os.environ.get("MYSQL_PASSWORD", "")
db_host = os.environ.get("MYSQL_HOST", "127.0.0.1")
db_port = os.environ.get("MYSQL_PORT", "3306")
db_name = os.environ.get("MYSQL_DB", "trpc_agent")

# Build the database connection URL
# Synchronous operations (pymysql)
db_url = f"mysql+pymysql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}?charset=utf8mb4"

# Asynchronous operations (aiomysql)
# db_url = f"mysql+aiomysql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}?charset=utf8mb4"

session_config = SessionServiceConfig(
    event_ttl_seconds=3600,
    max_events=100,
    ttl=SessionServiceConfig.create_ttl_config(
        enable=True,
        ttl_seconds=86400,              # 24-hour expiration
        cleanup_interval_seconds=3600,  # Cleanup every 1 hour
    ),
)

session_service = SqlSessionService(
    db_url=db_url,
    is_async=True,          # Use async mode (recommended)
    session_config=session_config,
    pool_pre_ping=True,     # Connection health check (recommended)
    pool_recycle=3600,      # Connection recycle time: 1 hour
)
```

**Database Table Schema**:
```sql
-- sessions table: stores session metadata
CREATE TABLE sessions (
    app_name VARCHAR(255) NOT NULL,
    user_id VARCHAR(255) NOT NULL,
    id VARCHAR(255) NOT NULL,
    state JSON,
    conversation_count INT DEFAULT 0,
    create_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (app_name, user_id, id),
    INDEX idx_update_time (update_time)  -- Used for cleanup task
);

-- events table: stores session events
CREATE TABLE events (
    id VARCHAR(255) NOT NULL,
    app_name VARCHAR(255) NOT NULL,
    user_id VARCHAR(255) NOT NULL,
    session_id VARCHAR(255) NOT NULL,
    invocation_id VARCHAR(255),
    author VARCHAR(255),
    content JSON,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    -- ... other fields
    PRIMARY KEY (id, app_name, user_id, session_id),
    FOREIGN KEY (app_name, user_id, session_id)
        REFERENCES sessions(app_name, user_id, id)
        ON DELETE CASCADE,  -- Cascade delete
    INDEX idx_timestamp (timestamp)  -- Used for event filtering
);
```

**Cleanup Task** (batch deletion):
```python
# From _sql_session_service.py
async def _cleanup_expired_async(self) -> None:
    """Batch delete expired sessions and events"""
    expire_before = datetime.now() - timedelta(seconds=self._session_config.ttl.ttl_seconds)

    # Single SQL DELETE for batch deletion (cascades to events)
    DELETE FROM sessions
    WHERE update_time < expire_before;
```

**Notes**:
- When `is_async=True`, the `aiomysql` driver is used; install with: `pip install aiomysql`
- When `is_async=False`, the `pymysql` driver is used; install with: `pip install pymysql`
- `pool_pre_ping=True` is recommended to avoid stale connections
- `pool_recycle=3600` sets the connection recycle time to avoid long-lived connections
- The cleanup task uses batch SQL DELETE for performance optimization
- Foreign key cascade delete: deleting a session automatically deletes associated events

**Related Examples**:
- 📁 [`examples/session_service_with_sql/run_agent.py`](../../../examples/session_service_with_sql/run_agent.py) - Complete SQL Session Service usage example

---

### Comparison of the Three Implementations

| Feature | InMemorySessionService | RedisSessionService | SqlSessionService |
|---------|----------------------|-------------------|------------------|
| **Data Storage** | Process memory | External Redis storage | MySQL/PostgreSQL |
| **Persistence** | ❌ Lost on process restart | ✅ Persisted to Redis | ✅ Persisted to database |
| **Distributed** | ❌ Cannot share across processes | ✅ Supports cross-process/server | ✅ Supports cross-process/server |
| **TTL Mechanism** | ✅ Periodic cleanup task | ✅ **Native Redis expiration** | ✅ **Periodic cleanup task (batch)** |
| **Cleanup Efficiency** | ⭐⭐⭐ Requires scanning | ⭐⭐⭐⭐⭐ Native Redis | ⭐⭐⭐⭐ **Single SQL batch delete** |
| **Transaction Support** | ❌ | ❌ | ✅ **ACID transactions** |
| **Complex Queries** | ❌ | ❌ | ✅ **SQL queries** |
| **State Management** | ✅ In-memory dictionary | ✅ Redis Hash | ✅ SQL tables |
| **Event Storage** | ✅ In-memory list | ✅ Redis Hash | ✅ SQL tables (foreign key association) |
| **Deployment Scenario** | Local dev / single node | Production / distributed / caching | Production / distributed / relational data |
| **Performance** | ⭐⭐⭐⭐⭐ Extremely fast | ⭐⭐⭐⭐ Fast | ⭐⭐⭐ Moderate |

**Selection Guide**:
- **Development & Testing** → `InMemorySessionService` (zero dependencies, quick startup)
- **Production (High Performance)** → `RedisSessionService` (native Redis expiration, no background tasks)
- **Production (Transactions/Queries)** → `SqlSessionService` (transaction safety, supports complex queries)

---

### Usage Examples

#### Basic Usage Flow

```python
import uuid
from trpc_agent_sdk.sessions import InMemorySessionService, SessionServiceConfig
from trpc_agent_sdk.runners import Runner

# 1. Create SessionService
session_config = SessionServiceConfig(
    event_ttl_seconds=3600,  # Event TTL: 1 hour
    max_events=100,          # Maximum event count: 100
    ttl=SessionServiceConfig.create_ttl_config(
        enable=True,
        ttl_seconds=86400,
        cleanup_interval_seconds=3600,
    ),
)
session_service = InMemorySessionService(session_config=session_config)

# 2. Create Runner and configure SessionService
runner = Runner(
    app_name="my_app",
    agent=my_agent,
    session_service=session_service
)

# 3. Run Agent (if the Session does not exist, the framework creates one automatically)
async for event in runner.run_async(
    user_id=user_id,
    session_id=session_id,  # Optional; auto-generated if not provided
    new_message=user_message
):
    # Process events...
    pass
```

#### Manual Session Management

```python
import uuid
from trpc_agent_sdk.sessions import InMemorySessionService

session_service = InMemorySessionService()

app_name = "SessionTest"
user_id = "Alice"
session_id = str(uuid.uuid4())

# Create Session
session = await session_service.create_session(
    app_name=app_name,
    user_id=user_id,
    session_id=session_id,
    state={"initial_key": "initial_value"}  # Optional initial state
)

# Get Session
session = await session_service.get_session(
    app_name=app_name,
    user_id=user_id,
    session_id=session_id
)

# List existing Sessions
session_list = await session_service.list_sessions(
    app_name=app_name,
    user_id=user_id
)
print(f"Session: {session_list.sessions}")

# Delete Session
await session_service.delete_session(
    app_name=app_name,
    user_id=user_id,
    session_id=session_id
)
```

#### State Scope Usage

```python
# Session State (session-level)
session.state["current_topic"] = "weather"

# User State (user-level, cross-session)
event.actions.state_delta = {
    "user:name": "Alice",        # User-level state
    "user:preference": "dark"    # User preference
}

# App State (application-level, cross-session)
event.actions.state_delta = {
    "app:version": "1.0",         # Application version
    "app:config": {...}           # Application configuration
}

# Temp State (temporary state, not persisted)
event.actions.state_delta = {
    "temp:cache": "..."          # Temporary cache, not stored
}
```

---

### Notes

#### 1. Automatic Session Creation

**Before `Runner.run_async`, if `create_session` has not been called, the framework will automatically create a Session**.

```python
# No manual creation needed; the framework creates one automatically
async for event in runner.run_async(
    user_id=user_id,
    session_id=session_id,  # Optional
    new_message=user_message
):
    pass
```

#### 2. Event Filtering

- `event_ttl_seconds`: Event TTL; expired events are automatically deleted
- `max_events`: Maximum event count; the oldest events are deleted when the limit is exceeded
- Filtering protects the first user message to ensure conversation context integrity

#### 3. TTL Configuration

- `ttl_seconds`: Session expiration time (in seconds)
- `cleanup_interval_seconds`: Cleanup interval (InMemory/SQL only; Redis handles expiration natively)
- TTL is automatically refreshed on access and update, extending the session's validity

#### 4. State Scopes

- **Session State**: Stored in `session.state`, follows the session lifecycle
- **User State**: Stored in `SessionService`, key prefix `user:`, shared across sessions
- **App State**: Stored in `SessionService`, key prefix `app:`, shared across sessions
- **Temp State**: Not persisted, key prefix `temp:`, exists only in memory

#### 5. Concurrency Safety

- `InMemorySessionService`: Thread-safe within a single process
- `RedisSessionService`: Supports multi-process/multi-server concurrency
- `SqlSessionService`: Supports multi-process/multi-server concurrency (using database transactions)

---

### Related Examples

The following examples demonstrate the usage of different SessionService implementations:

#### InMemorySessionService

📁 **Example Path**: `examples/session_service_with_in_memory/`

**Description**:
- Demonstrates basic usage of In-Memory Session Service
- Shows session creation, retrieval, listing, and deletion
- Demonstrates event appending and filtering
- Demonstrates state scopes (Session/User/App State)

**How to Run**:
```bash
cd examples/session_service_with_in_memory/
python3 run_agent.py
```

---

#### RedisSessionService

📁 **Example Path**: `examples/session_service_with_redis/`

**Description**:
- Demonstrates Redis Session Service usage
- Shows the Redis automatic expiration mechanism
- Provides detailed Redis operation guide
- Includes output analysis and Redis command examples

**How to Run**:
```bash
cd examples/session_service_with_redis/
python3 run_agent.py
```

---

#### SqlSessionService

📁 **Example Path**: `examples/session_service_with_sql/`

**Description**:
- Demonstrates SQL Session Service usage
- Shows MySQL table schema and data operations
- Demonstrates batch cleanup tasks
- Provides MySQL operation commands and output analysis

**How to Run**:
```bash
cd examples/session_service_with_sql/
python3 run_agent.py
```

---

### Core Feature Summary

#### 1. Session Management (CRUD)

- ✅ Create, retrieve, list, and delete sessions
- ✅ Automatic session creation (during Runner execution)
- ✅ Session listing excludes events (reduces data transfer)

#### 2. Event Management

- ✅ Append events to sessions
- ✅ Event filtering (TTL + maximum count)
- ✅ User message protection (ensures context integrity)

#### 3. State Management

- ✅ Multi-scope state (Session/User/App/Temp)
- ✅ Automatic state merge (during `get_session`)
- ✅ State persistence (User/App state shared across sessions)

#### 4. TTL Cache Eviction

- ✅ Automatic cleanup of expired sessions to prevent unbounded storage growth
- ✅ Automatic TTL refresh on access and update
- ✅ Different cleanup mechanisms for different implementations

#### 5. Session Summarization

- ✅ Supports session summarization (compresses long conversations)
- ✅ Configurable trigger conditions (turn count, event count, time interval, etc.)

#### 6. Flexible Storage Backends

- ✅ Supports three implementations: In-Memory, Redis, and SQL
- ✅ Supports TRPC Redis integration
- ✅ Choose the appropriate implementation based on your scenario

---

### Summary

SessionService provides powerful session management capabilities:

- ✅ **Session Management**: Complete CRUD operations
- ✅ **Event Management**: Append, filter, and protect user messages
- ✅ **State Management**: Multi-scope state (Session/User/App/Temp)
- ✅ **TTL Eviction**: Automatic cleanup of expired sessions
- ✅ **Session Summarization**: Compress long conversations to reduce context length
- ✅ **Multiple Implementations**: In-Memory, Redis, SQL, TRPC Redis

By properly using SessionService, you can achieve:
- Multi-turn conversation context management
- User state persistence
- Application-level state sharing
- Session lifecycle management

For more detailed usage examples, refer to the related examples in the `examples/` directory.

- [examples/session_service_with_in_memory/run_agent.py](../../../examples/session_service_with_in_memory/run_agent.py)
- [examples/session_service_with_redis/run_agent.py](../../../examples/session_service_with_redis/run_agent.py)
- [examples/session_service_with_sql/run_agent.py](../../../examples/session_service_with_sql/run_agent.py)

---

## State

In trpc_agent, **State** is a key-value collection associated with each Session. It can be thought of as the session's "notepad", storing dynamic information that the Agent needs to remember and reference during conversations, enabling the Agent to perceive key contextual information within the session.

For example, the following information can be stored via State:
- User information: Remember user preferences (e.g., `user_theme: 'dark'`)
- Task progress: Track multi-step task status (e.g., `booking_step: 'confirm_payment'`)
- Information accumulation: Build lists or summaries (e.g., `shopping_cart: ['book', 'pen']`)
- Intermediate results: Pass processing results between Agents (e.g., `analysis_result: '...'`)

### Features

#### Data Structure

State is stored in key-value form within the Session, which implies the following constraints:
- Keys must be strings
- Values must be serializable to strings by default (since they will be injected into the Agent's Prompt)
- Persistence:
    - If using InMemorySessionService, State is lost after process restart
    - If using RedisSessionService, State is persisted to Redis and can be recovered after process restart/scaling

#### Scope Control

State keys use different prefixes to distinguish different levels of session information.

##### No Prefix (Session State)
- Scope: Current session
- Lifecycle: Follows the Session lifecycle
- Typical use: Task progress, temporary computation results
- Example: `current_step: 'processing'`

##### `user:` Prefix (User State)
- Scope: All sessions of a specific user
- Lifecycle: Persisted across sessions
- Typical use: User preferences, personal information
- Example: `user:language: 'zh'`, `user:name: 'Zhang San'`

##### `app:` Prefix (App State)
- Scope: All users and sessions of the entire application
- Lifecycle: Globally persisted
- Typical use: Global configuration, shared resources
- Example: `app:version: '1.0'`, `app:maintenance_mode: false`

##### `temp:` Prefix (Temporary State)
- Scope: A single run_async invocation
- Lifecycle: Never persisted, discarded after processing
- Typical use: Intermediate computation results, debug information
- Example: `temp:api_response: {...}`

### Usage

#### Template Reference: Using in Instructions

Reference state variables in the Agent's `instruction` using the `{key}` syntax:

```python
LlmAgent(
    name="personalized_assistant",
    model="deepseek-chat",
    instruction="""Hello {user:name}!

Current task progress: {current_step}
User preferred language: {user:language}

Provide assistance for {user:name} based on user preferences.""",
)
```

#### Multi-Agent Collaboration: Using output_key

An Agent can automatically save its output to a state variable:

```python
# Agent 1: Analyze user requirements
analyzer = LlmAgent(
    name="need_analyzer",
    model="deepseek-chat",
    instruction="Analyze user requirements and provide a detailed analysis",
    output_key="analysis_result"  # Output saved to state
)

# Agent 2: Develop a plan based on the analysis result
planner = LlmAgent(
    name="solution_planner",
    model="deepseek-chat",
    instruction="Develop a solution based on the analysis result:\n\n{analysis_result}",
    output_key="solution_plan"
)
```

#### Modifying in Tools: Using InvocationContext

Modify state in tool functions via `tool_context.state`:

**Note: You must use `tool_context` as the parameter name; using a different name will cause errors**

```python
async def update_user_preference(preference: str, value: str,
                                tool_context: InvocationContext) -> str:
    """Update user preference settings"""
    # Save user-level preference
    tool_context.state[f"user:{preference}"] = value

    # Record operation history (session-level)
    history = tool_context.state.get("preference_history", [])
    history.append(f"Updated {preference} to {value}")
    tool_context.state["preference_history"] = history

    return f"Preference {preference} = {value} has been updated"
```

#### Setting State Before and After Agent Execution

You can set initial state via `session_service` before and after Agent execution:

```python
# Set initial state before Agent execution
session = await session_service.create_session(
    app_name="my_app",
    user_id="user123",
    session_id="session456",
    state={
        "user:name": "Zhang San",
        "user:language": "Chinese",
        "current_step": "started",
        "app:version": "1.0.0"
    }
)

# runner.run_async(...)

# Since session is read-only, you need to re-fetch the session to get the updated state
session = await session_service.get_session(app_name="my_app", user_id="user123", session_id="session456")
print(session.state)
# After Agent execution, you can modify the state, but it must be updated to session_service to take effect
session.state["current_step"] = "end"
await session_service.update_session(session)
```

### Complete Example

See the complete State usage example: [examples/session_state/run_agent.py](../../../examples/session_state/run_agent.py)
