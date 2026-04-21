# WebSearchTool 联网检索示例

本示例演示如何使用框架内置的 `WebSearchTool`，让 LLM Agent 通过公网搜索引擎获取实时信息，并按照"白名单 / 黑名单域名 + 必须引用 URL"的规则生成可追溯的回答，同时覆盖 **DuckDuckGo** 与 **Google Custom Search** 两个 provider。

## 关键特性

- **双 Provider 支持**：
  - `duckduckgo`：DuckDuckGo Instant Answer API，**无需 API Key**，适合事实/定义/百科类检索
  - `google`：Google Custom Search（CSE），需要 `api_key` + `engine_id`，提供真正的公网搜索结果，支持 `siteSearch`、`hl`、`safe`、`dateRestrict` 等 CSE 原生参数
- **域名白名单/黑名单**：通过工具调用参数 `allowed_domains` / `blocked_domains` 实现 site 级过滤，二者互斥；DDG 做客户端子域感知过滤，Google 在单域名时使用服务端 `siteSearch` 快速路径，多域名自动回退到客户端过滤
- **结构化输出**：`WebSearchResult` 统一返回 `query / provider / results[{title, url, snippet}] / summary`，便于 LLM 引用与拼装 `Sources:` 段落
- **强制引用规范**：`WebSearchTool.process_request` 自动向 LLM 追加"必须以 markdown 链接形式列出来源 URL"和"使用当前年份"的指令
- **结果裁剪与去重**：`results_num / snippet_len / title_len` 控制返回体大小；`dedup_urls`（默认 `True`）按 scheme/host/path 归一化后自动合并同一 URL 的重复命中，避免 LLM 在 `Sources:` 段里重复引用同一来源。若需要把原始结果交给下游 re-ranker / 多样化采样等流程处理，可显式传入 `dedup_urls=False` 保留每一条命中
- **共享 httpx 连接池**：Google 演示把 `http_client` 传给 `WebSearchTool`，在多个 agent / 多次调用之间复用连接，并演示外部负责 `aclose` 生命周期管理
- **Provider 原生参数透传**：`ddg_extra_params` / `google_extra_params` 让用户把 provider 专属的高级参数（如 Google CSE 的 `safe`、`dateRestrict`）封装在 agent 层，无需每次工具调用重复指定

## Agent 层级结构说明

本例提供四个独立 Agent，按场景顺序驱动；`root_agent` 默认指向 `ddg_agent`：

```text
ddg_agent (LlmAgent)                   # 默认 dedup_urls=True
├── model: OpenAIModel
├── tools:
│   └── WebSearchTool(provider="duckduckgo", results_num=3, snippet_len=300)
└── session: InMemorySessionService

ddg_raw_agent (LlmAgent)               # dedup_urls=False，保留原始命中
├── model: OpenAIModel
├── tools:
│   └── WebSearchTool(provider="duckduckgo", results_num=5, dedup_urls=False)
└── session: InMemorySessionService

google_agent (LlmAgent)                # 基线 Google CSE，覆盖大部分构造参数
├── model: OpenAIModel
├── tools:
│   └── WebSearchTool(
│         provider="google",
│         api_key=<GOOGLE_CSE_API_KEY>,
│         engine_id=<GOOGLE_CSE_ENGINE_ID>,
│         user_agent="trpc-agent-python-websearch-demo/1.0 (+google-cse)",
│         proxy=<HTTPS_PROXY/HTTP_PROXY>,
│         lang="en",
│         http_client=<shared httpx.AsyncClient>,
│         results_num=3, snippet_len=240, title_len=80, timeout=15.0,
│         dedup_urls=True,
│         google_extra_params={"safe": "active"},
│       )
└── session: InMemorySessionService

google_raw_agent (LlmAgent)            # dedup_urls=False + 6 个月时效性
├── model: OpenAIModel
├── tools:
│   └── WebSearchTool(
│         provider="google", ...,
│         results_num=5, snippet_len=320, title_len=100, timeout=20.0,
│         dedup_urls=False,
│         google_extra_params={"dateRestrict": "m6"},
│       )
└── session: InMemorySessionService
```

关键文件：

- [examples/websearch_tool/agent/agent.py](./agent/agent.py)：构建四个 `LlmAgent`，分别挂载默认去重/关闭去重的 DuckDuckGo 与 Google CSE 版本 `WebSearchTool`
- [examples/websearch_tool/agent/prompts.py](./agent/prompts.py)：两套 instruction —— DuckDuckGo 版（实体名查询）与 Google 版（自然语言 + 年份 + 可选 `lang` 覆盖）
- [examples/websearch_tool/agent/config.py](./agent/config.py)：环境变量读取（LLM 凭据 + Google CSE 凭据 + 可选 HTTP 代理）
- [examples/websearch_tool/run_agent.py](./run_agent.py)：测试入口，先跑 DuckDuckGo 的白名单/黑名单/`dedup_urls=False` 场景，再跑 Google 的服务端 `siteSearch` / 客户端多域名过滤 / 黑名单 / 语言覆盖 / 时效性 bias 场景

## 关键代码解释

这一节用于快速定位 "Provider 切换、域名过滤、引用规范注入、URL 去重、共享连接池、Google 原生参数" 六条核心链路。

### 1) Provider 配置（`agent/agent.py`）

- `WebSearchTool(provider="duckduckgo", ...)`：使用 DuckDuckGo Instant Answer API，**无需 API Key**，适合定义/百科/事实型查询
- `WebSearchTool(provider="google", api_key=..., engine_id=..., ...)`：使用 Google Custom Search JSON API，需要 API Key 和 Programmable Search engine id（即 CSE 的 `cx`）
- `results_num / snippet_len / title_len` 控制单次调用返回体大小；`timeout` 控制 HTTP 超时；每个参数在构造时都会按 `[1, _MAX_*]` 做 clamp，避免误配置打爆上下文

### 2) 黑/白名单域名过滤（`run_agent.py` 的提示词驱动）

- 用户在请求中明确"只用 wikipedia.org"或"排除 duckduckgo.com"，LLM 会按工具 `FunctionDeclaration` 自动把对应的字符串数组放进 `allowed_domains` / `blocked_domains`
- `WebSearchTool._run_async_impl` 会：
  - 把 `allowed_domains` 与 `blocked_domains` 同时传入时返回 `INVALID_ARGS`（互斥校验）
  - 对 DuckDuckGo 结果做客户端域名过滤
  - 对 Google 结果：
    - **单个域名**时优先使用服务端 `siteSearch` + `siteSearchFilter=i/e`（更少流量、更快）
    - **多个域名**时跳过服务端过滤，回退到客户端 `_is_blocked` —— 因为 Google CSE 的 `siteSearch` 只接受一个值
  - 把 `www.` 前缀剥离并按子域匹配（如 `wikipedia.org` 同时匹配 `en.wikipedia.org`）

### 3) 引用规范与时间感知指令注入（`WebSearchTool.process_request`）

- 工具在每次请求前会自动 `append_instructions`，强制要求：
  - 回答末尾追加 `Sources:` 段并以 `[Title](URL)` 列出工具返回的 URL
  - 不允许编造 URL，只能引用工具实际返回的 URL
  - 在涉及"最新/recent/current"类查询时使用 _当前月份与年份_ 入参（避免 LLM 幻觉旧年份）
- 这部分逻辑无需例子代码额外配置，只要挂载 `WebSearchTool` 就会生效

### 4) URL 去重开关（`dedup_urls`）

- `WebSearchTool` 构造参数 `dedup_urls`（默认 `True`）控制是否合并"形态不同但语义相同"的 URL：
  - 归一化时会统一 `scheme` 大小写、剥离 `www.` 前缀、忽略末尾 `/`、忽略 http/https 默认端口
  - 例如 `https://docs.python.org/3/` 与 `https://www.docs.python.org/3` 会被视为同一来源，仅保留首次出现
- `ddg_agent` / `google_agent` 使用默认值 `True`，避免 `Sources:` 段里出现重复链接
- `ddg_raw_agent` / `google_raw_agent` 显式传入 `dedup_urls=False`，把每一条原始 provider 命中都透出给调用方，适配：
  - 外置 **re-ranker**（如 Cross-Encoder / Cohere Rerank）需要更多候选以挑出最相关项
  - **多样化采样 / MMR** 等希望看到重复变体以评估置信度的流水线
  - 需要做**链路可观测性**、记录原始召回列表用于离线评估的场景

### 5) 共享 httpx.AsyncClient（`http_client` + `user_agent` + `proxy` + `timeout`）

`google_agent` 与 `google_raw_agent` 共用一个由 `_get_shared_google_http_client()` 创建的 `httpx.AsyncClient`，通过构造参数传入 `WebSearchTool`：

- **连接复用**：多次搜索复用同一连接池，对连续调用明显更快；`httpx.Limits` 的上限由创建方决定（本例 `max_connections=16, max_keepalive_connections=8`）
- **生命周期归属**：传入外部 `http_client` 后，`WebSearchTool` **不会**帮你 `aclose()`，需要调用方显式关闭。示例在 `run_agent.py` 的 `main()` 结尾通过 `aclose_shared_google_http_client()` 在 **同一个事件循环** 内关闭，避免 `Unclosed client` 警告
- **强制生效的 `timeout` / `user_agent`**：即使传了外部 client，`WebSearchTool._get_json` 仍会在每次 `GET` 时把构造器里的 `timeout` 与 `user_agent` 覆盖到当次请求上，保证 agent 层的这些约束始终有效
- **可选 `proxy`**：demo 从 `HTTPS_PROXY` / `HTTP_PROXY` 读取；需要经过企业出口代理访问 Google 时填上即可，无需改代码

### 6) Provider 原生参数透传（`google_extra_params` / `ddg_extra_params`）

- `google_extra_params` / `ddg_extra_params` 允许把 provider 专属的高级参数固定在 agent 层，每次工具调用都自动带上
- 本 demo 演示两种典型用法：
  - `google_agent` 使用 `google_extra_params={"safe": "active"}` —— 打开 Google 的 SafeSearch
  - `google_raw_agent` 使用 `google_extra_params={"dateRestrict": "m6"}` —— 只保留过去 6 个月内被 Google 索引的结果，适合"最新/what's new"类查询
- 其他常见透传包括 Google CSE 的 `gl`（地理偏向）、`cr`（国家限制）、`filter`、`sort` 等

## 环境与运行

### 环境要求

- Python 3.12

### 安装步骤

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e .
```

### 环境变量要求

在 [examples/websearch_tool/.env](./.env) 中配置（或通过 `export`）：

必填（LLM 凭据）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

可选（Google CSE 场景，未设置时会自动跳过 Google 相关 demo）：

- `GOOGLE_CSE_API_KEY`：从 [Google Cloud Console](https://developers.google.com/custom-search/v1/overview) 申请
- `GOOGLE_CSE_ENGINE_ID`：在 [Programmable Search Engine](https://programmablesearchengine.google.com/) 创建搜索引擎后得到的 `cx`
- `HTTPS_PROXY` / `HTTP_PROXY`：可选的出口代理，仅 Google 场景使用

### 运行命令

```bash
cd examples/websearch_tool
python3 run_agent.py
```

## DuckDuckGo 运行结果

```text
========== DuckDuckGo · plain lookup ==========
🆔 Session ID: bc8f7289...
📝 User: Look up the entity 'Python (programming language)' and summarise it in one paragraph. Use count=1.
🤖 Assistant: 
🔧 [Invoke Tool: websearch({'query': 'Python (programming language)', 'count': 1})]
📊 [Tool Result: provider=duckduckgo query='Python (programming language)' hits=1 summary='Python is a high-level, general-purpose programming language...' top_titles=[Python (programming language)]]
Python is a high-level, general-purpose programming language known for its emphasis on code readability, ...

Sources:
- [Python (programming language)](https://en.wikipedia.org/wiki/Python_(programming_language))
----------------------------------------

========== DuckDuckGo · allowed_domains whitelist ==========
🔧 [Invoke Tool: websearch({'query': 'Python (programming language)', 'count': 3, 'allowed_domains': ['wikipedia.org']})]
📊 [Tool Result: provider=duckduckgo query='Python (programming language)' hits=1 ...]
...

Sources:
- [Python (programming language)](https://en.wikipedia.org/wiki/Python_(programming_language))
----------------------------------------

========== DuckDuckGo · raw multi-source hits ==========
🔧 [Invoke Tool: websearch({'query': 'Python (programming language)', 'count': 5})]
📊 [Tool Result: provider=duckduckgo hits=5 top_titles=[Python (programming language), Official site, Python (programming language) Category]]
...

Sources:
- [Python (programming language) - Wikipedia](https://en.wikipedia.org/wiki/Python_(programming_language))
- [Official site](https://www.python.org/)
- [Python (programming language) Category](https://duckduckgo.com/c/Python_(programming_language))
- [Pydoc](https://duckduckgo.com/Pydoc)
- [NumPy](https://duckduckgo.com/NumPy)
----------------------------------------

========== DuckDuckGo · blocked_domains blacklist ==========
🔧 [Invoke Tool: websearch({'query': 'Python programming language', 'count': 5, 'blocked_domains': ['duckduckgo.com']})]
📊 [Tool Result: provider=duckduckgo hits=2 top_titles=[Python (programming language), Official site]]
...

Sources:
- [Python (programming language) - Wikipedia](https://en.wikipedia.org/wiki/Python_(programming_language))
- [Official site - Python.org](https://www.python.org/)
----------------------------------------
```

## Google Custom Search 运行结果

```text
========== Google · plain web search ==========
🆔 Session ID: 6a63e9a0...
📝 User: What are the headline features of FastAPI 0.115? Use count=3.
🤖 Assistant: 
🔧 [Invoke Tool: websearch({'query': 'FastAPI 0.115 headline features', 'count': 3})]
📊 [Tool Result: provider=google query='FastAPI 0.115 headline features' hits=3 summary='' top_titles=[Features - FastAPI, [BUG] CREW getting stuck on any task as , Configure Swagger UI - FastAPI]]
The search results did not directly highlight the headline features of FastAPI 0.115. However, FastAPI is known for its high performance, ease of use, and built-in support for OpenAPI and Swagger UI. For specific details about version 0.115, you may need to refer to the official FastAPI documentation or release notes.

Sources:
- [Features - FastAPI](https://fastapi.tiangolo.com/features/)
- [GitHub Issue](https://github.com/crewAIInc/crewAI/issues/2997)
- [Configure Swagger UI - FastAPI](https://fastapi.tiangolo.com/how-to/configure-swagger-ui/)
----------------------------------------

========== Google · allowed_domains single (server-side siteSearch) ==========
🆔 Session ID: 4aed7a08...
📝 User: Search for 'Python asyncio tutorial' but only keep results from python.org. Return up to 3 results.
🤖 Assistant: 
🔧 [Invoke Tool: websearch({'query': 'Python asyncio tutorial', 'allowed_domains': ['python.org'], 'count': 3})]
📊 [Tool Result: provider=google query='Python asyncio tutorial' hits=3 summary='' top_titles=[A Conceptual Overview of asyncio - Pytho, asyncio — Asynchronous I/O — Python 3.14, Asyncio: Am I doing it wrong? - Python D]]
Here are the top results for "Python asyncio tutorial" from python.org:

1. **[A Conceptual Overview of asyncio - Python documentation](https://docs.python.org/3/howto/a-conceptual-overview-of-asyncio.html)**  
   This HOWTO article helps you build a mental model of how asyncio functions, including writing asynchronous variants of operations like sleep or database requests.

2. **[asyncio — Asynchronous I/O — Python 3.14.4 documentation](https://docs.python.org/3/library/asyncio.html)**  
   The official documentation for the `asyncio` library, which is used to write concurrent code using the `async/await` syntax. It serves as a foundation for many Python asynchronous frameworks.

3. **[Asyncio: Am I doing it wrong? - Python Discussions](https://discuss.python.org/t/asyncio-am-i-doing-it-wrong/5699)**  
   A discussion thread where users share their experiences and challenges with `asyncio`, including debugging tips and best practices.

Sources:
- [A Conceptual Overview of asyncio](https://docs.python.org/3/howto/a-conceptual-overview-of-asyncio.html)
- [asyncio — Asynchronous I/O](https://docs.python.org/3/library/asyncio.html)
- [Asyncio: Am I doing it wrong?](https://discuss.python.org/t/asyncio-am-i-doing-it-wrong/5699)
----------------------------------------

========== Google · allowed_domains multi (client-side filter) ==========
🆔 Session ID: dcf3fa07...
📝 User: Search for 'pydantic v2 migration guide' and restrict results to docs.pydantic.dev or github.com. Return up to 5 results.
🤖 Assistant: 
🔧 [Invoke Tool: websearch({'query': 'pydantic v2 migration guide', 'count': 5, 'allowed_domains': ['docs.pydantic.dev', 'github.com']})]
📊 [Tool Result: provider=google query='pydantic v2 migration guide' hits=1 summary='' top_titles=[Migrating @root_validator(pre=True) pyda]]
Here is a relevant result for the Pydantic V2 migration guide:

- [Migrating @root_validator(pre=True) pydantic > 2 #9035 - GitHub](https://github.com/pydantic/pydantic/discussions/9035): This discussion provides guidance on migrating from Pydantic V1 to V2, specifically addressing the deprecation of `@root_validator(pre=True)` and suggesting the use of `@model_validator` validators in Pydantic V2.

Sources:
- [Migrating @root_validator(pre=True) pydantic > 2 #9035 - GitHub](https://github.com/pydantic/pydantic/discussions/9035)
----------------------------------------

========== Google · blocked_domains blacklist ==========
🆔 Session ID: 7bf4393c...
📝 User: Search for 'HTML form tutorial' and exclude any results from w3schools.com. Return up to 3 results.
🤖 Assistant: 
🔧 [Invoke Tool: websearch({'query': 'HTML form tutorial', 'blocked_domains': ['w3schools.com'], 'count': 3})]
📊 [Tool Result: provider=google query='HTML form tutorial' hits=3 summary='' top_titles=[Learn HTML forms in 8 minutes - YouTube, Your first form - Learn web development , Learn HTML Forms In 25 Minutes - YouTube]]
Here are some resources for learning about HTML forms, excluding results from w3schools.com:

1. **[Learn HTML forms in 8 minutes - YouTube](https://www.youtube.com/watch?v=2O8pkybH6po)**  
   A quick tutorial explaining HTML forms with examples.

2. **[Your first form - Learn web development | MDN](https://developer.mozilla.org/en-US/docs/Learn_web_development/Extensions/Forms/Your_first_form)**  
   A detailed guide from MDN on creating your first HTML form, including form controls and their usage.

3. **[Learn HTML Forms In 25 Minutes - YouTube](https://www.youtube.com/watch?v=fNcJuPIZ2WE)**  
   A comprehensive tutorial covering HTML forms in under 25 minutes.

Sources:
- [Learn HTML forms in 8 minutes - YouTube](https://www.youtube.com/watch?v=2O8pkybH6po)
- [Your first form - Learn web development | MDN](https://developer.mozilla.org/en-US/docs/Learn_web_development/Extensions/Forms/Your_first_form)
- [Learn HTML Forms In 25 Minutes - YouTube](https://www.youtube.com/watch?v=fNcJuPIZ2WE)
----------------------------------------

========== Google · per-call lang override (zh-CN) ==========
🆔 Session ID: 36882630...
📝 User: Search for 'FastAPI 入门教程' in Chinese (pass lang='zh-CN'). Return up to 3 results.
🤖 Assistant: 
🔧 [Invoke Tool: websearch({'query': 'FastAPI 入门教程', 'count': 3, 'lang': 'zh-CN'})]
📊 [Tool Result: provider=google query='FastAPI 入门教程' hits=3 summary='' top_titles=[教程- 用户指南 - FastAPI, FastAPI 教程| 菜鸟教程, 整体的介绍FastAPI，快速上手开发，结合API 交互文档逐个讲解核心 ...]]
以下是关于“FastAPI 入门教程”的中文搜索结果：

1. **[教程- 用户指南 - FastAPI](https://fastapi.tiangolo.com/zh/tutorial/)**  
   所有代码片段都可以复制后直接使用（它们实际上是经过测试的Python 文件）。要运行任何示例，请将代码复制到 `main.py` 文件中，然后启动 `fastapi dev`。

2. **[FastAPI 教程| 菜鸟教程](https://www.runoob.com/fastapi/fastapi-tutorial.html)**  
   FastAPI 是一个用于构建API 的现代、快速（高性能）的Python Web 框架，专为构建RESTful API 而设计。FastAPI 使用Python 3.8+ 并基于标准的Python 类型提示。

3. **[整体的介绍FastAPI，快速上手开发](https://github.com/liaogx/fastapi-tutorial)**  
   整体的介绍 FastAPI，快速上手开发，结合 API 交互文档逐个讲解核心模块的使用。视频学习地址： - liaogx/fastapi-tutorial。

Sources:
- [教程- 用户指南 - FastAPI](https://fastapi.tiangolo.com/zh/tutorial/)
- [FastAPI 教程| 菜鸟教程](https://www.runoob.com/fastapi/fastapi-tutorial.html)
- [整体的介绍FastAPI，快速上手开发](https://github.com/liaogx/fastapi-tutorial)
----------------------------------------

========== Google · raw hits with 6-month recency bias ==========
🆔 Session ID: 25fdbf2d...
📝 User: What are the latest Python 3.13 release highlights this year? Return up to 5 results.
🤖 Assistant: 
🔧 [Invoke Tool: websearch({'query': 'Python 3.13 release highlights 2026', 'count': 5})]
📊 [Tool Result: provider=google query='Python 3.13 release highlights 2026' hits=5 summary='' top_titles=[Python Version in Production - Reddit, Migrating scripts to Python 3.13 in Work, Latest Python upgrade (3.13 -> 3.14) on ]]
Here are some highlights related to Python 3.13 in 2026:

1. **Reddit Discussion**: Users on Reddit discuss the adoption of Python 3.13 in production environments, noting its stability and reviewing its release notes. [Read more](https://www.reddit.com/r/Python/comments/1qgt083/python_version_in_production/)

2. **Workiva Support**: Workiva has introduced support for Python 3.13, providing guidance on migrating scripts from older versions like Python 3.9. Full release notes are referenced on Python.org. [Read more](https://support.workiva.com/hc/en-us/articles/40603516229780-Migrating-scripts-to-Python-3-13-in-Workiva)

3. **Anaconda Distribution**: Anaconda's release notes mention Python 3.13.9, along with updates to Conda and other user-facing changes. [Read more](https://www.anaconda.com/docs/getting-started/anaconda/release-notes)

4. **UiPath Documentation**: UiPath's Python activities package now supports Python 3.13, with new features and improvements introduced in March 2026. [Read more](https://docs.uipath.com/activities/other/latest/developer/release-notes-python-activities)

For the most detailed and official release highlights, it's recommended to check Python.org's "What's New in Python 3.13" section.

Sources:
- [Python Version in Production - Reddit](https://www.reddit.com/r/Python/comments/1qgt083/python_version_in_production/)
- [Migrating scripts to Python 3.13 in Workiva - Support Center](https://support.workiva.com/hc/en-us/articles/40603516229780-Migrating-scripts-to-Python-3-13-in-Workiva)
- [Anaconda Distribution release notes](https://www.anaconda.com/docs/getting-started/anaconda/release-notes)
- [Activities - Release notes - UiPath Documentation](https://docs.uipath.com/activities/other/latest/developer/release-notes-python-activities)
----------------------------------------
```

## 适用场景建议

- 需要 LLM 引用 **可追溯 URL** 回答事实/定义类信息：适合直接复用本示例 `WebSearchTool` + 提示词约定
- 仅需 **无 API Key、轻量定义类检索**：使用 `provider="duckduckgo"`（默认）即可
- 需要 **真实公网搜索、支持 site/语言/SafeSearch/时效性**：使用 `provider="google"`，并把 provider 专属的高级参数固化在 `google_extra_params` 中
- 需要在工具调用前后插入日志、审计、参数校验：参考 [examples/filter_with_tool](../filter_with_tool)，把 `WebSearchTool(filters_name=[...])` 与 `before_tool_callback` / `after_tool_callback` 组合使用
- 需要把搜索结果接入 RAG 流程：参考 [examples/knowledge_with_searchtool_rag_agent](../knowledge_with_searchtool_rag_agent)
