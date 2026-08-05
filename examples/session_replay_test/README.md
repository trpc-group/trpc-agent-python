# Session / Memory Replay Consistency Testing

Multi-backend replay and consistency verification framework built with
tRPC-Agent SDK. Tests that conversations produce consistent results
across different session and memory backends.

## Supported Backends

- **Session**: InMemory, Redis, SQL
- **Memory**: InMemory, Mem0, MemPalace, Redis, SQL

## Quick Start

```bash
pip install -e '.[redis,sql,mem0]'
export TRPC_AGENT_API_KEY=your-key
export TRPC_AGENT_BASE_URL=https://tokenhub.tencentmaas.com/v1
python run_agent.py
```

## Architecture

```
User → Agent (Hy3 LLM)
         ├── list_available_backends()
         ├── replay_conversation(session, memory, messages)
         └── compare_replays(results) → consistency report
```
