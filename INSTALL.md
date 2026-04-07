# Installation Guide

## Overview

| Item       | Details                                                              |
| ---------- | -------------------------------------------------------------------- |
| Name       | tRPC-Agent-Python (`trpc-agent-py`)                                  |
| Version    | **1.0.0**                                                            |
| Description | A production-grade agent framework developed by Tencent, supporting multiple model providers (including OpenAI, Anthropic, DeepSeek, and LiteLLM). It provides tool-calling capabilities, multi-agent orchestration, session and long-term memory management, RAG-based knowledge, and seamless deployment as a service via FastAPI. |
| License    | Apache-2.0                                                           |
| Repository | https://github.com/trpc-group/trpc-agent-python                     |

## Platforms

| Operating System           | Support Status |
| -------------------------- | -------------- |
| Linux (Ubuntu/CentOS/Debian) | ✅ Fully supported (recommended for production) |
| macOS (Intel / Apple Silicon) | ✅ Fully supported (recommended for development) |

---

## Dependencies

### Required Dependencies

| Dependency    | Version        | Description                       | Download URL                                     |
| ------------- | -------------- | --------------------------------- | ------------------------------------------------ |
| **Python**    | 3.12           | Runtime environment               | https://www.python.org/downloads/                |
| **pip**       | >= 21.0        | Python package manager            | Bundled with Python, upgrade via `pip install --upgrade pip` |
| **git**       | >= 2.0         | Required for source code installation  | https://git-scm.com/downloads                    |

### Core Dependencies

The core dependencies are `automatically installed` when installing `trpc-agent-py`, the complete list of core dependencies please refer to `requirements.txt` ([./requirements.txt](./requirements.txt)) file.

### Optional Dependencies

| Dependency    | Purpose                           | Installation                                    |
| ------------- | --------------------------------- | ----------------------------------------------- |
| **Docker**    | CodeExecutor containerized execution | https://docs.docker.com/get-docker/          |
| **Redis**     | Redis session/memory       | https://redis.io/download                       |
| **MySQL**     | SQL session                | https://dev.mysql.com/downloads/                |


> **Tip**: It is recommended to use [pyenv](https://github.com/pyenv/pyenv) or [conda](https://docs.conda.io/) to manage Python versions and avoid conflicts with the system Python.

---

## Installation

### Pip Installation

```bash
# Create a virtual environment
python3 -m venv .venv

# Activate the virtual environment
source .venv/bin/activate        # Linux / macOS
# .venv\Scripts\activate         # Windows

pip install trpc-agent-py
```

Install optional extensions:

```bash
# Choose as needed, multiple extensions can be combined with commas
pip install "trpc-agent-py[a2a,knowledge,agent-claude]"
```

---

### Source Code Installation

```bash
# Clone the repository
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
# Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate        # Linux / macOS
# .venv\Scripts\activate         # Windows
# Install
pip install -e .
```

### Optional Dependencies Reference

| Extension        | Purpose                       | Install Command                         |
| ---------------- | ----------------------------- | --------------------------------------- |
| `a2a`            | Google A2A protocol           | `pip install "trpc-agent-py[a2a]"`            |
| `ag-ui`          | AG-UI protocol                | `pip install "trpc-agent-py[ag-ui]"`          |
| `agent-claude`   | Claude Agent                  | `pip install "trpc-agent-py[agent-claude]"`   |
| `knowledge`      | Knowledge base / RAG          | `pip install "trpc-agent-py[knowledge]"`      |
| `mem0`           | Long-term memory (Mem0)       | `pip install "trpc-agent-py[mem0]"`           |
| `langchain_tool` | LangChain Tool integration    | `pip install "trpc-agent-py[langchain_tool]"` |
| `langfuse`       | Langfuse observability        | `pip install "trpc-agent-py[langfuse]"`       |
| `eval`           | Evaluation framework          | `pip install "trpc-agent-py[eval]"`           |
| `openclaw`       | OpenClaw integration          | `pip install "trpc-agent-py[openclaw]"`       |
| `dev`            | Development (lint/format/test)| `pip install "trpc-agent-py[dev]"`            |
| `all`            | All optional dependencies     | `pip install "trpc-agent-py[all]"`            |

---

## Configuration

### Environment Variables

trpc-agent-py uses environment variables to configure model connections. There are two ways to set them:

**Option 1**: Create a `.env` file in the project directory (recommended)

```bash
# .env file contents
# Model API key
TRPC_AGENT_API_KEY="your-api-key"

# Model service URL
TRPC_AGENT_BASE_URL="your-base-url"

# Default model name
TRPC_AGENT_MODEL_NAME="your-model-name"
```

> **Note**: Do not commit the `.env` file to version control. Make sure `.gitignore` includes `.env`.

**Option 2**: Export directly to the shell environment

```bash
export TRPC_AGENT_API_KEY="your-api-key"
export TRPC_AGENT_BASE_URL="your-base-url"
export TRPC_AGENT_MODEL_NAME="your-model-name"
```

### Configuration File Locations

| File              | Location  | Description                         |
| ----------------- | --------- | ----------------------------------- |
| `.env`            | Project root / example subdirectories | Environment variable config (templates available in each example directory) |
| `pyproject.toml`  | Project root | Build config, tool config (yapf/pytest, etc.) |

---

## Verification

### Check Framework Version

```bash
python -c "from trpc_agent_sdk.version import __version__; print(f'trpc-agent-py {__version__}')"
```

### Check Core Module Imports

```bash
python -c "
from trpc_agent_sdk.agents import LlmAgent, ChainAgent, ParallelAgent, TransferAgent
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.models import OpenAIModel
print('All core modules imported successfully.')
"
```

Expected output:
```
All core modules imported successfully.
```
### Run Unit Tests

```bash
# Install test dependencies
pip install -r requirements-test.txt
# Run all tests
pytest tests/ -v
```

## Troubleshooting

### Pip install is slow or times out

**Problem**: Installation hangs or reports `ReadTimeoutError`.

**Solution**: Use a mirror to speed up downloads.

```bash
# Temporary usage Tencent Cloud mirror
pip install trpc-agent-py -i https://mirrors.cloud.tencent.com/pypi/simple

# Set global Tencent Cloud mirror
pip config set global.index-url https://mirrors.cloud.tencent.com/pypi/simple
```

Other available mirrors:
| Mirror   | URL                                         |
| -------- | ------------------------------------------- |
| Tencent Cloud | https://mirrors.cloud.tencent.com/pypi/simple |
| Tsinghua | https://pypi.tuna.tsinghua.edu.cn/simple    |
| Alibaba Cloud | https://mirrors.aliyun.com/pypi/simple/ |

---

### Python version does not meet requirements

**Problem**:
```
ERROR: Package 'trpc-agent-py' requires a different Python: 3.9.x not in '>=3.10'
```

**Solution**: Upgrade to Python 3.12.

```bash
# Solution 1: Using pyenv to install Python 3.12
pyenv install 3.12
pyenv local 3.12

# Solution 2: Using conda to create a virtual environment and activate it
conda create -n trpc-agent-py python=3.12
conda activate trpc-agent-py
```

Verify the version:
```bash
python3 --version
```

---

### Permission denied

**Problem**:
```
ERROR: Could not install packages due to an EnvironmentError: [Errno 13] Permission denied
```

**Solution**:

```bash
# Recommended: use a virtual environment to avoid permission issues
python3 -m venv .venv
source .venv/bin/activate
pip install trpc-agent-py
```

> **Note**: It is not recommended to use `sudo pip install` as it may corrupt the system Python environment.

---

### Model call error: TRPC_AGENT_API_KEY must be set

**Problem**: Reports that the API key is not set or is empty.

**Solution**:

```bash
# Check if the environment variable is set
echo $TRPC_AGENT_API_KEY

# If empty, set the environment variables
export TRPC_AGENT_API_KEY="your-api-key"
export TRPC_AGENT_BASE_URL="your-base-url"
export TRPC_AGENT_MODEL_NAME="your-model-name"
```

If using a `.env` file, make sure dotenv is loaded in your code:
```python
from dotenv import load_dotenv
load_dotenv()
```

---

### ImportError: No module named 'xxx'

**Problem**: `ModuleNotFoundError` when importing an extension module.

**Reason**: The corresponding optional dependency is not installed.

**Solution**: Install the matching extension based on the missing module.

| Missing Module          | Install Command                             |
| ----------------------- | ------------------------------------------- |
| `a2a_sdk`               | `pip install "trpc-agent-py[a2a]"`          |
| `ag_ui_protocol`        | `pip install "trpc-agent-py[ag-ui]"`        |
| `claude_agent_sdk`      | `pip install "trpc-agent-py[agent-claude]"` |
| `langchain_community`   | `pip install "trpc-agent-py[knowledge]"`    |
| `mem0ai`                | `pip install "trpc-agent-py[mem0]"`         |
| `langchain_tavily`      | `pip install "trpc-agent-py[langchain_tool]"` |

---

### Pydantic version conflict

**Problem**:
If your environment contains packages pinned to Pydantic v1, you may encounter errors such as:
```text
pydantic.errors.PydanticImportError: `BaseSettings` has been moved to the `pydantic-settings` package
```
Or other Pydantic v1/v2 compatibility errors.

**Solution**: This framework requires Pydantic v2 (>= 2.11.3).

```bash
pip install --upgrade pydantic>=2.11.3
```

If other packages depend on Pydantic v1, use an isolated virtual environment.

---

## Next Step

- **Quick Start**: Check out [examples/quickstart/](./examples/quickstart/) to run your first Agent quickly
- **Full Documentation**: Visit the [docs/mkdocs/en/](./docs/mkdocs/en/)
- **More Examples**: Browse the [examples/](./examples/) directory, covering multi-agent, tool calling, knowledge, memory, service deployment, etc.
- **Contributing**: Read [CONTRIBUTING.md](./CONTRIBUTING.md)
