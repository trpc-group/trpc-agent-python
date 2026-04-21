# WebFetchTool 网页抓取示例

本示例演示如何使用框架内置的 `WebFetchTool`，让 LLM Agent 通过公网 HTTP GET 抓取单个 URL，并按照"白名单 / 黑名单域名 + SSRF 防护 + 内置 LRU 缓存"的规则生成可追溯的回答，同时针对 SSRF 场景专门跑一遍 loopback / 云元数据 / 内网 IP 三种典型 payload，验证在**没有任何 TCP 连接建立**之前就会被工具以 `SSRF_BLOCKED_URL` 结构化错误拒收。

## 关键特性

- **单次 HTTP GET + 文本化**：对指定 URL 发一次无鉴权 GET；HTML 被转换为 Markdown-ish 纯文本，其它 `text/*` / `application/json` 等文本型 MIME 按原样返回；二进制响应以结构化 `UNSUPPORTED_CONTENT_TYPE` 错误拒收
- **域名白名单/黑名单**：通过构造参数 `allowed_domains` / `blocked_domains` 实现工具级 site 过滤（**LLM 无法覆盖**）；子域感知匹配（`www.` 前缀剥离，`python.org` 同时匹配 `docs.python.org`），黑名单优先于白名单
- **SSRF 防护**：`block_private_network=True`（默认）会在每次请求及**每一跳重定向**后解析主机，拒绝回环 / 私网 / 链路本地（含 `169.254.169.254` 云元数据端点）/ 保留 / 组播 / 未指定地址
- **结构化输出**：`FetchResult` 统一返回 `url / status_code / status_text / content_type / content / bytes / duration_ms / cached / error`，便于 LLM 引用与降级
- **内容与字节双重裁剪**：`max_content_length`（字符）与 `max_response_bytes`（字节）分别控制返回文本长度与**线缆上实际读取的原始字节**；LLM 还可通过调用参数 `max_length` 进一步按需收紧
- **内置 LRU 缓存**：`enable_cache=True` 时启用进程内 URL → `FetchResult` LRU；`cache_ttl_seconds` / `cache_max_bytes` 控制新鲜度与预算，命中时响应上 `cached=true`

## Agent 层级结构说明

本例提供五个独立 Agent，按场景顺序驱动；`root_agent` 默认指向 `default_fetch_agent`：

```text
default_fetch_agent (LlmAgent)                  # 基线：HTTP 形态 + SSRF 默认项
├── model: OpenAIModel
├── tools:
│   └── WebFetchTool(
│         timeout=10.0,
│         user_agent="trpc-agent-python-webfetch-example/1.0",
│         max_content_length=4000,
│         max_response_bytes=1 MiB,
│         follow_redirects=True,
│         max_redirects=3,
│         block_private_network=True,
│       )
└── session: InMemorySessionService

cached_fetch_agent (LlmAgent)                   # 进程内 LRU 缓存
├── model: OpenAIModel
├── tools:
│   └── WebFetchTool(
│         enable_cache=True,
│         cache_ttl_seconds=120.0,
│         cache_max_bytes=2 MiB,
│       )
└── session: InMemorySessionService

whitelist_fetch_agent (LlmAgent)                # 域名白名单
├── model: OpenAIModel
├── tools:
│   └── WebFetchTool(allowed_domains=["python.org"])
└── session: InMemorySessionService

blocklist_fetch_agent (LlmAgent)                # 域名黑名单
├── model: OpenAIModel
├── tools:
│   └── WebFetchTool(blocked_domains=["example.com"])
└── session: InMemorySessionService

ssrf_fetch_agent (LlmAgent)                     # SSRF 防护（loopback / 元数据 / 内网）
├── model: OpenAIModel
├── tools:
│   └── WebFetchTool(
│         follow_redirects=True,
│         max_redirects=3,
│         block_private_network=True,
│       )
└── session: InMemorySessionService
```

关键文件：

- [examples/webfetch_tool/agent/agent.py](./agent/agent.py)：构建五个 `LlmAgent`，分别覆盖 HTTP 形态默认项、LRU 缓存、白名单、黑名单、SSRF 防护
- [examples/webfetch_tool/agent/prompts.py](./agent/prompts.py)：网页阅读助手提示词，要求引用 URL、复述 `BLOCKED_URL` / `SSRF_BLOCKED_URL` / `HTTP_STATUS` 错误、并在命中缓存时告知用户
- [examples/webfetch_tool/agent/config.py](./agent/config.py)：环境变量读取（LLM 凭据）
- [examples/webfetch_tool/run_agent.py](./run_agent.py)：测试入口，依次执行基线抓取、`max_length` 按需裁剪、缓存命中、白名单拒绝、黑名单拒绝、SSRF 三类 payload 拒绝等场景

## 关键代码解释

这一节用于快速定位"HTTP 形态默认项、LRU 缓存、域名过滤、SSRF 防护"四条核心链路。

### 1) HTTP 形态默认项（`agent/agent.py::create_default_fetch_agent`）

- `timeout`：HTTP 读写超时，示例压到 10s 便于快速暴露网络异常
- `user_agent`：线上日志可由此识别示例流量
- `max_content_length`：返回 `content` 字符上限（示例 4000，便于屏显）；LLM 调用参数 `max_length` 可进一步**按调用覆盖**
- `max_response_bytes`：线缆上允许读取的**原始字节**上限（示例 1 MiB）。流式读取遇到该上限即终止下载，避免大文件吃满解码/内存预算
- `follow_redirects` / `max_redirects`：手动重定向循环，上限示例 3 跳
- `block_private_network`：默认开启的 SSRF 边界，**每一跳**都会重新做 DNS 解析校验

### 2) LRU 缓存（`agent/agent.py::create_cached_fetch_agent`）

- `enable_cache=True`：打开进程内 URL → `FetchResult` LRU（默认关闭，需显式 opt-in）
- `cache_ttl_seconds`：单条新鲜度，超时后下一次读触发穿透 + 淘汰
- `cache_max_bytes`：总字节预算，满后按 LRU 淘汰；**单条体积超过预算时会被静默跳过**
- 缓存键会做 URL 归一化（统一 scheme 大小写、剥离 `www.`、忽略默认端口、忽略尾 `/`），`https://example.com` 与 `https://www.example.com/` 共享同一条缓存项
- 命中时 `FetchResult.cached = True`，便于下游判断新鲜度

### 3) 域名白/黑名单（`agent/agent.py::create_whitelist_fetch_agent` / `create_blocklist_fetch_agent`）

- `allowed_domains` / `blocked_domains` 为**工具级**配置，**LLM 无法在调用参数里覆盖**；这是与 `WebSearchTool`（LLM 可逐调用传入名单）的主要差别
- 匹配规则：`www.` 前缀剥离，子域感知（`python.org` 同时匹配 `docs.python.org`）
- **黑名单优先**：同一主机同时命中两张名单时仍被拒绝，返回错误 `BLOCKED_URL: '<host>' is not permitted by the tool's domain policy`
- 每一跳重定向都会重新套一遍名单校验，防止"合法首跳 → 跳到被禁主机"的绕过

### 4) SSRF 防护（`agent/agent.py::create_ssrf_fetch_agent`）

- `block_private_network=True` 为默认值，示例里显式固定以便明确意图
- 在发起请求**和每一跳重定向**时对目标主机做 DNS 解析，命中以下任一即以结构化错误 `SSRF_BLOCKED_URL: <host> is a private/reserved address` 拒绝，**不会建立任何 TCP 连接**：
  - 回环（`127.0.0.0/8`，包含 `localhost`）
  - 链路本地（`169.254.0.0/16`，含 `169.254.169.254` AWS / GCP / Aliyun / 腾讯云通用元数据端点）
  - 私网（RFC 1918：`10/8`、`172.16/12`、`192.168/16`，以及 IPv6 ULA `fc00::/7`）
  - 保留 / 组播 / 未指定（`0.0.0.0`、`::`）地址
- 纯 IP 字面量会直接走 `ipaddress` 判定；域名则通过 `socket.getaddrinfo` 解析出的**每一条 A/AAAA 记录**都要求是公网地址，任意一条命中私网即整体拒绝，有效阻断"DNS 结果是一条公网 IP + 一条 `127.0.0.1`"这类 DNS Rebinding 变种
- 本示例专门用 `ssrf_fetch_agent` 驱动三类经典 payload：
  - `http://127.0.0.1/` — 本机 loopback，覆盖"让 Agent 打自己"的最短路径
  - `http://169.254.169.254/latest/meta-data/` — 云实例元数据端点，历史上是最常见的 SSRF 数据外泄目标
  - `http://10.0.0.1/` — RFC 1918 内网网段，覆盖"Agent 跑在 VPC / k8s 集群里被用来扫内网"
- 仅当调用方已用外部白名单限定目标、并确信输入可信（如内网集群）时才考虑关闭该开关；关闭后示例 5 的三条 payload 会真的发起连接，生产环境请谨慎评估

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

在 [examples/webfetch_tool/.env](./.env) 中配置（或通过 `export`）：

必填：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

### 运行命令

```bash
cd examples/webfetch_tool
python3 run_agent.py
```

## 运行结果

```text
========== Default · plain fetch ==========
🆔 Session ID: d422c00b...
📝 User: Fetch https://example.com and summarise the page in one short paragraph.
🤖 Assistant: 
🔧 [Invoke Tool: webfetch({'url': 'https://example.com', 'max_length': 500})]
📊 [Tool Result: url='https://example.com' status=200 content_type='text/html' bytes=183 cached=False duration_ms=87 preview='Example Domain  # Example Domain  This domain is for use in documentation examples without needing permission. Avoid ...']
The page at **Example Domain** is a placeholder used for documentation purposes. It states that the domain is intended for examples and should not be used in actual operations. A link is provided to learn more about such domains.

Source: [https://example.com](https://example.com)
----------------------------------------

========== Default · per-call max_length override ==========
🆔 Session ID: 47afb4e2...
📝 User: Fetch https://example.com but only return the first ~200 characters of the body. Use max_length=200 on the tool call.
🤖 Assistant: 
🔧 [Invoke Tool: webfetch({'url': 'https://example.com', 'max_length': 200})]
📊 [Tool Result: url='https://example.com' status=200 content_type='text/html' bytes=183 cached=False duration_ms=75 preview='Example Domain  # Example Domain  This domain is for use in documentation examples without needing permission. Avoid ...']
Here is the first ~200 characters of the content from https://example.com:

Example Domain

# Example Domain

This domain is for use in documentation examples without needing permission. Avoid use in operations.

[Learn more](https://iana.org/domains/example)

Source: [https://example.com](https://example.com)
----------------------------------------

========== Cache · first fetch (network) ==========
🆔 Session ID: 64dbc485...
📝 User: Fetch https://example.com and summarise the page in one short paragraph.
🤖 Assistant: 
🔧 [Invoke Tool: webfetch({'url': 'https://example.com', 'max_length': 500})]
[INFO] WebFetchTool put result for https://example.com in LRU cache
📊 [Tool Result: url='https://example.com' status=200 content_type='text/html' bytes=183 cached=False duration_ms=79 preview='Example Domain  # Example Domain  This domain is for use in documentation examples without needing permission. Avoid ...']
The page at **Example Domain** is a placeholder used for documentation purposes. It states that this domain is intended for examples and should not be used in actual operations. A link is provided to learn more about such domains.

Source: [https://example.com](https://example.com)
----------------------------------------

========== Cache · second fetch (cache hit) ==========
🆔 Session ID: 857e415e...
📝 User: Fetch https://example.com again and summarise the page in one short paragraph. Tell me whether the tool served the response from the cache.
🤖 Assistant: 
🔧 [Invoke Tool: webfetch({'url': 'https://example.com', 'max_length': 500})]
[INFO] WebFetchTool got cached result for https://example.com in LRU cache
📊 [Tool Result: url='https://example.com' status=200 content_type='text/html' bytes=183 cached=True duration_ms=79 preview='Example Domain  # Example Domain  This domain is for use in documentation examples without needing permission. Avoid ...']
The page at **Example Domain** is a placeholder used for documentation purposes, advising users to avoid operational use. It provides a link to learn more about such domains.

The response was served from the in-process cache, so the content may be slightly stale (up to the tool's configured TTL).

Source: [https://example.com](https://example.com)
----------------------------------------

========== Whitelist · allowed host (python.org) ==========
🆔 Session ID: d56de0e1...
📝 User: Fetch https://www.python.org/ and summarise the landing page in one short paragraph.
🤖 Assistant: 
🔧 [Invoke Tool: webfetch({'url': 'https://www.python.org/', 'max_length': 500})]
📊 [Tool Result: url='https://www.python.org/' status=200 content_type='text/html' bytes=505 cached=False duration_ms=413 preview='Welcome to Python.org  **Notice:** This page displays a fallback because interactive scripts did not run. Possible ca...']
The Python.org landing page welcomes visitors to the official site of the Python programming language. It provides quick links to essential resources like the Python Software Foundation (PSF), documentation, the Python Package Index (PyPI), and more. The page also includes navigation options for exploring Python further.

Source: [Python.org](https://www.python.org/)
----------------------------------------

========== Whitelist · rejected host (example.com) ==========
🆔 Session ID: 980ff5ad...
📝 User: Fetch https://example.com and summarise the page. If the tool refuses the URL, explain why.
🤖 Assistant: 
🔧 [Invoke Tool: webfetch({'url': 'https://example.com'})]
📊 [Tool Result: error="BLOCKED_URL: 'example.com' is not permitted by the tool's domain policy" url='https://example.com' status=None]
The tool refused to fetch the URL `https://example.com` because the domain `example.com` is not permitted by the tool's domain policy. This means the tool has a blocklist or allowlist in place that restricts access to certain domains, and `example.com` is one of them.

If you need information from a different domain, please provide a URL that is allowed by the tool's policy.
----------------------------------------

========== Blacklist · rejected host (example.com) ==========
🆔 Session ID: a58386ba...
📝 User: Fetch https://example.com and summarise the page. If the tool refuses the URL, explain why.
🤖 Assistant: 
🔧 [Invoke Tool: webfetch({'url': 'https://example.com'})]
📊 [Tool Result: error="BLOCKED_URL: 'example.com' is not permitted by the tool's domain policy" url='https://example.com' status=None]
The tool refused to fetch the URL `https://example.com` because the domain `example.com` is blocked by the tool's domain policy. This means the tool is configured to disallow requests to this specific domain.

If you need information from this domain, you may need to use an alternative method or tool to access it.
----------------------------------------

========== SSRF · loopback (127.0.0.1) ==========
🆔 Session ID: 0b0503e6...
📝 User: Fetch http://127.0.0.1/ and summarise the page. If the tool refuses the URL, explain why in one or two sentences.
🤖 Assistant: 
🔧 [Invoke Tool: webfetch({'url': 'http://127.0.0.1/'})]
📊 [Tool Result: error='SSRF_BLOCKED_URL: 127.0.0.1 is a private/reserved address' url='http://127.0.0.1/' status=0]
The tool refused to fetch the URL `http://127.0.0.1/` because it resolves to a loopback (private/reserved) address (`127.0.0.1`), which is blocked by the SSRF guard to prevent potential security risks.
----------------------------------------

========== SSRF · cloud metadata (169.254.169.254) ==========
🆔 Session ID: a370e17b...
📝 User: Fetch http://169.254.169.254/latest/meta-data/ and summarise what it returns. If the tool refuses the URL, explain why in one or two sentences.
🤖 Assistant: 
🔧 [Invoke Tool: webfetch({'url': 'http://169.254.169.254/latest/meta-data/'})]
📊 [Tool Result: error='SSRF_BLOCKED_URL: 169.254.169.254 is a private/reserved address' url='http://169.254.169.254/latest/meta-data/' status=0]
The tool refused to fetch the URL `http://169.254.169.254/latest/meta-data/` because the IP address `169.254.169.254` is a private/reserved address, and accessing such addresses is blocked by the SSRF (Server-Side Request Forgery) guard to prevent security risks. 

Source: [http://169.254.169.254/latest/meta-data/](http://169.254.169.254/latest/meta-data/)
----------------------------------------

========== SSRF · RFC 1918 intranet (10.0.0.1) ==========
🆔 Session ID: 9a667a8d...
📝 User: Fetch http://10.0.0.1/ and summarise the page. If the tool refuses the URL, explain why in one or two sentences.
🤖 Assistant: 
🔧 [Invoke Tool: webfetch({'url': 'http://10.0.0.1/'})]
📊 [Tool Result: error='SSRF_BLOCKED_URL: 10.0.0.1 is a private/reserved address' url='http://10.0.0.1/' status=0]
The tool refused to fetch the URL `http://10.0.0.1/` because the target address `10.0.0.1` is a private/reserved IP address, which is blocked by the SSRF (Server-Side Request Forgery) guard to prevent unauthorized access to internal networks.
----------------------------------------
```

## 适用场景建议

- 需要 LLM 阅读 **指定网页并引用 URL** 的场景：适合直接复用本示例 `WebFetchTool` + 提示词约定
- 对 **公网 SSRF 风险敏感**（例如 Agent 跑在云实例、能访问内网元数据端点）：保留 `block_private_network=True` 默认值，并参考 `ssrf_fetch_agent` 用 loopback / `169.254.169.254` / RFC 1918 三条 payload 固化回归测试
- 存在 **热点页面反复读取**（文档、changelog、status page）：打开 `enable_cache=True` 并按请求体量调整 `cache_max_bytes`
- 需要把抓取工具限定在一小组可信站点（白名单）或屏蔽已知噪声站点（黑名单）：通过 `allowed_domains` / `blocked_domains` 配置，**这两个参数 LLM 无法覆盖**
- 需要在工具调用前后插入日志、审计、参数校验：参考 [examples/filter_with_tool](../filter_with_tool)，把 `WebFetchTool(filters_name=[...])` 与 `before_tool_callback` / `after_tool_callback` 组合使用
- 需要自定义 HTTP 形态（公司代理、mTLS、连接池复用）：通过构造参数 `proxy` 或 `http_client` 注入
