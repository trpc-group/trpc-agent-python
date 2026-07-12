# Code Review Agent

An automated AI code reviewer built with tRPC-Agent SDK, backed by Hy3 LLM.

## Features

- **Structured review**: Bugs, style, security, improvement suggestions
- **SQLite persistence**: All reviews saved to `code_reviews.db`
- **Skills-ready**: Can be extended with CubeSandbox skills for sandboxed execution

## Quick Start

```bash
pip install -e '.[cube]'  # from trpc-agent-python root

export TRPC_AGENT_API_KEY=your-hy3-key
export TRPC_AGENT_BASE_URL=http://127.0.0.1:8000/v1
export TRPC_AGENT_MODEL_NAME=tencent/Hy3

python run_agent.py path/to/your/code.py
```

## Architecture

```
User → Code Review Agent (Hy3 LLM)
         ├── review_code() → Analyze code
         ├── save_review() → SQLite persistence
         └── skills (optional) → CubeSandbox sandbox
```
