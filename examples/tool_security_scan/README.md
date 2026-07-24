# Tool Security Scanning & Filter

Security scanning and filter/monitoring framework for tRPC-Agent tool execution.

## Features

- **Pattern-based scanning**: Shell injection, path traversal, network exfil, code execution, env access
- **Filter policy**: Block dangerous tools, sanitize inputs
- **Integrity checks**: Size limits, JSON depth limits
- **Extensible**: Add custom patterns and rules

## Quick Start

```bash
export TRPC_AGENT_API_KEY=your-key
export TRPC_AGENT_BASE_URL=https://tokenhub.tencentmaas.com/v1
python run_agent.py
```
