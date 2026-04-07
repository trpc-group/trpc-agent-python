# 安装文档

## 项目简介

| 项目       | 信息                                                                 |
| ---------- | -------------------------------------------------------------------- |
| 名称   | tRPC-Agent-Python (`trpc-agent-py`)                                  |
| 版本   | **0.1.0**                                                            |
| 描述   | 腾讯开源的生产级 Agent 框架，支持多模型（OpenAI / Anthropic / DeepSeek / LiteLLM）、工具调用、多 Agent 编排、会话与长期记忆、知识库（RAG）、FastAPI 服务部署 |
| 许可证     | Apache-2.0                                                           |
| 仓库地址   | https://github.com/trpc-group/trpc-agent-python                     |

## 支持平台

| 操作系统           | 支持情况 |
| ------------------ | -------- |
| Linux (Ubuntu/CentOS/Debian) | ✅ 完全支持（推荐生产环境） |
| macOS (Intel / Apple Silicon) | ✅ 完全支持（推荐开发环境） |

---

## 安装依赖

### 基础依赖

| 依赖          | 版本要求       | 说明                              | 下载地址                                         |
| ------------- | -------------- | --------------------------------- | ------------------------------------------------ |
| **Python**    | 3.12 | 运行时环境                        | https://www.python.org/downloads/                |
| **pip**       | >= 21.0        | Python 包管理器                   | Python 自带，可以通过`pip install --upgrade pip` 升级    |
| **git**       | >= 2.0         | 采用源码安装时必须                    | https://git-scm.com/downloads                    |

### 主要依赖

安装 `trpc-agent-py` 时主要依赖会`自动安装`，完整的主要依赖列表请参考 `requirements.txt` ([./requirements.txt](./requirements.txt)) 文件。

### 系统级可选依赖

| 依赖          | 用途                              | 安装方式                                        |
| ------------- | --------------------------------- | ----------------------------------------------- |
| **Docker**    | CodeExecutor 容器化代码执行       | https://docs.docker.com/get-docker/             |
| **Redis**     | Redis 会话/记忆后端               | https://redis.io/download                       |
| **MySQL**     | SQL 会话后端                      | https://dev.mysql.com/downloads/                |


> **提示**：建议使用 [pyenv](https://github.com/pyenv/pyenv) 或 [conda](https://docs.conda.io/) 管理 Python 版本，避免与系统 Python 冲突。

---

## 安装步骤

### Pip 安装

```bash
# 创建虚拟环境
python3 -m venv .venv

# 激活虚拟环境
source .venv/bin/activate        # Linux / macOS
# .venv\Scripts\activate         # Windows

pip install trpc-agent-py
```

安装扩展功能（可选）:

```bash
# 按需选择，多个扩展可用逗号组合
pip install "trpc-agent-py[a2a,knowledge,agent-claude]"
```

---

### 源码安装

```bash
# 克隆仓库
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
# 创建并激活虚拟环境
python3 -m venv .venv
source .venv/bin/activate        # Linux / macOS
# .venv\Scripts\activate         # Windows
# 安装
pip install -e .
```

### 可选依赖对照表

| 扩展名           | 用途                          | 关键依赖                                     | 安装命令                                |
| ---------------- | ----------------------------- | -------------------------------------------- | --------------------------------------- |
| `a2a`            | Google A2A 协议               | a2a-sdk, protobuf                            | `pip install "trpc-agent-py[a2a]"`            |
| `ag-ui`          | AG-UI 协议                    | ag-ui-protocol                               | `pip install "trpc-agent-py[ag-ui]"`          |
| `agent-claude`   | Claude Agent                  | claude-agent-sdk, cloudpickle                | `pip install "trpc-agent-py[agent-claude]"`   |
| `knowledge`      | 知识库 / RAG                  | numpy, langchain_community, langchain_huggingface | `pip install "trpc-agent-py[knowledge]"` |
| `mem0`           | 长期记忆（Mem0）              | mem0ai, sentence-transformers                | `pip install "trpc-agent-py[mem0]"`           |
| `langchain_tool` | LangChain Tool 集成           | langchain_tavily                             | `pip install "trpc-agent-py[langchain_tool]"` |
| `langfuse`       | Langfuse 可观测性             | opentelemetry-sdk, opentelemetry-exporter    | `pip install "trpc-agent-py[langfuse]"`       |
| `eval`           | 评测框架                      | pytest, rouge-score, pandas, tabulate        | `pip install "trpc-agent-py[eval]"`           |
| `openclaw`       | OpenClaw 集成                 | nanobot-ai, wecom-aibot-sdk-python           | `pip install "trpc-agent-py[openclaw]"`       |
| `dev`            | 开发环境（lint/格式化/测试）  | yapf, flake8, pytest, pytest-asyncio         | `pip install "trpc-agent-py[dev]"`            |
| `all`            | 所有可选依赖                  | 上述全部                                     | `pip install "trpc-agent-py[all]"`            |

---

## 配置说明

### 环境变量配置

trpc-agent-py 框架通过环境变量配置模型连接信息。有两种配置方式：

**方式 1**：在项目目录下创建 `.env` 文件（推荐）

```bash
# .env 文件内容
# 模型 API 密钥
TRPC_AGENT_API_KEY="your-api-key"

# 模型服务地址
TRPC_AGENT_BASE_URL="your-base-url"

#模型名称
TRPC_AGENT_MODEL_NAME="your-model-name"
```

> **注意**：请勿将 `.env` 文件提交到版本控制中。确保 `.gitignore` 中包含 `.env`。

**方式 2**：直接导出到 Shell 环境

```bash
# 导出环境变量
export TRPC_AGENT_API_KEY="your-api-key"
export TRPC_AGENT_BASE_URL="your-base-url"
export TRPC_AGENT_MODEL_NAME="your-model-name"
```

### 配置文件位置

| 文件              | 位置      | 说明                                |
| ----------------- | --------- | ----------------------------------- |
| `.env`            | 项目根目录 / example 子目录 | 环境变量配置（各 example 目录下有模板） |
| `pyproject.toml`  | 项目根目录 | 构建配置、工具配置（yapf/pytest 等） |

---

## 验证安装

### 检查框架版本

```bash
python -c "from trpc_agent_sdk.version import __version__; print(f'trpc-agent-py {__version__}')"
```

### 检查框架核心模块

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

预期输出：
```
All core modules imported successfully.
```

### 运行单元测试

```bash
# 安装测试依赖
pip install -r requirements-test.txt
# 运行全部测试
pytest tests/ -v
```

## 常见问题与解决方案

### Pip install 速度慢或超时

**问题**：安装过程长时间无响应或报 `ReadTimeoutError`。

**解决方案**：使用国内镜像源加速。

```bash
# 临时使用
pip install trpc-agent-py -i https://mirrors.cloud.tencent.com/pypi/simple

# 设置全局镜像
pip config set global.index-url https://mirrors.cloud.tencent.com/pypi/simple
```

其他可用镜像源：
| 镜像名   | 地址                                        |
| -------- | ------------------------------------------- |
| 腾讯云   | https://mirrors.cloud.tencent.com/pypi/simple |
| 清华     | https://pypi.tuna.tsinghua.edu.cn/simple    |
| 阿里云   | https://mirrors.aliyun.com/pypi/simple/     |

---

### Python 版本不满足要求

**问题**：
```
ERROR: Package 'trpc-agent-py' requires a different Python: 3.9.x not in '>=3.10'
```

**解决方案**：升级到 Python 3.12。

```bash
# 解决方案 1: 使用 pyenv 安装
pyenv install 3.12
pyenv local 3.12

# 解决方案 2: 使用 conda 创建虚拟环境并激活
conda create -n trpc-agent-py python=3.12
conda activate trpc-agent-py
```

验证版本：
```bash
python3 --version
```

---

### 权限不足

**问题**：
```
ERROR: Could not install packages due to an EnvironmentError: [Errno 13] Permission denied
```

**解决方案**：

```bash
# 推荐：使用虚拟环境（避免权限问题）
python3 -m venv .venv
source .venv/bin/activate
pip install trpc-agent-py
```

> **注意**：不推荐使用 `sudo pip install`，可能导致系统 Python 环境混乱。

---

### 模型调用报错  TRPC_AGENT_API_KEY must be set

**问题**：API Key 未设置或为空

**解决方案**：

```bash
# 检查环境变量是否已设置
echo $TRPC_AGENT_API_KEY

# 如果为空，设置环境变量
export TRPC_AGENT_API_KEY="your-api-key"
export TRPC_AGENT_BASE_URL="your-base-url"
export TRPC_AGENT_MODEL_NAME="your-model-name"
```

如果使用 `.env` 文件，请确保代码中加载了 dotenv：
```python
from dotenv import load_dotenv
load_dotenv()
```

---

### ImportError: No module named 'xxx'

**问题**：导入某个扩展模块时报 `ModuleNotFoundError`。

**原因**：未安装对应的可选依赖。

**解决方案**：根据报错模块安装对应扩展。

| 缺失模块                | 安装命令                                    |
| ----------------------- | ------------------------------------------- |
| `a2a_sdk`               | `pip install "trpc-agent-py[a2a]"`          |
| `ag_ui_protocol`        | `pip install "trpc-agent-py[ag-ui]"`        |
| `claude_agent_sdk`      | `pip install "trpc-agent-py[agent-claude]"` |
| `langchain_community`   | `pip install "trpc-agent-py[knowledge]"`    |
| `mem0ai`                | `pip install "trpc-agent-py[mem0]"`         |
| `langchain_tavily`      | `pip install "trpc-agent-py[langchain_tool]"` |

---

### Pydantic 版本冲突

**问题**：
如果环境包含 pinned 到 Pydantic v1 的包，可能会遇到以下错误：
```
pydantic.errors.PydanticImportError: `BaseSettings` has been moved to the `pydantic-settings` package
```
或其他 Pydantic v1/v2 兼容性错误。

**解决方案**：本框架要求 Pydantic v2（>= 2.11.3）。

```bash
pip install --upgrade pydantic>=2.11.3
```

如果其他包依赖 Pydantic v1，建议使用隔离的虚拟环境。

---

## 下一步

- **快速开始**：查看 [examples/quickstart/](./examples/quickstart/)，快速跑通第一个 Agent
- **完整文档**：访问 [docs/mkdocs/zh/](./docs/mkdocs/zh/)
- **更多示例**：浏览 [examples/](./examples/) 目录，涵盖多 Agent 编排、工具调用、知识库、服务部署等场景
- **参与贡献**：阅读 [CONTRIBUTING.md](./CONTRIBUTING.md)
