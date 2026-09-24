# TencentDB Agent Memory Service 使用示例

本示例演示如何通过 `TencentDBMemoryService` 将 tRPC-Agent 接入
[TencentDB Agent Memory](https://github.com/TencentCloud/TencentDB-Agent-Memory)，
在一个 Session 中写入用户偏好，并在另一个 Session 中召回服务端提取的 L1 长期记忆。

## 工作流程

```text
第一轮对话结束
  -> Runner 自动调用 TencentDBMemoryService.store_session()
  -> POST /v3/conversation/add 写入 L0 原始对话
  -> TencentDB Agent Memory 异步提取 L1 记忆

第二轮对话
  -> Agent 调用 load_memory
  -> TencentDBMemoryService.search_memory()
  -> POST /v3/atomic/search 搜索 L1 记忆
  -> L1 无结果时，POST /v3/conversation/search 搜索 L0 原始对话
```

写入记忆由框架自动完成，不需要给 Agent 添加 `save_memory` 工具。Agent 只需通过
`load_memory` 工具主动检索已经提取的长期记忆。

> **本地部署注意：必须在 Memory Core 服务端正确配置
> `MEMORY_PROMPT_MODE`。**
>
> 该参数虽然不由 tRPC-Agent 客户端读取，但会决定 TencentDB Agent Memory
> 服务端使用哪一组提示词执行 L1/L2/L3 记忆提取。配置不匹配时，L0 对话仍会
> 写入成功，但服务端可能提取出 0 条 L1 记忆，因此后续搜索无法召回。
>
> - `chat`：个人偏好、用户画像、对话经历、教学和通用助手场景。
> - `code`：项目事实、工程任务、技术决策、SOP 和团队协作场景。
>
> 本示例写入“喜欢的颜色”。本地部署需要在 Memory Core 服务端选择 `chat`
> 模式。腾讯云托管实例的提取策略由产品服务端管理，本客户端不会读取或发送
> `MEMORY_PROMPT_MODE`。

## 选择部署方式

本示例支持两种互斥的接入方式：

- **本地部署**：自行下载并启动 TencentDB Agent Memory，需要 Docker，并可配置
  Memory Core 的提取模式。按照“方案一”操作。
- **腾讯云托管实例**：不需要下载或启动服务端，直接使用控制台提供的 Gateway
  地址和凭证。按照“方案二”操作。

两种方式只共用最后的“运行示例”步骤。不要把本地服务端配置、Panel 登录密钥和
腾讯云 Gateway 凭证混用。

## 公共环境要求

- Python 3.10+
- 一个 OpenAI 兼容的 LLM 服务
- tRPC-Agent Python 源码及其依赖环境

## 方案一：本地部署

本地部署还需要 Docker。以下命令都在
`TencentDB-Agent-Memory/deploy/global-images` 目录执行。

### 1. 下载 TencentDB Agent Memory

建议使用包含 V3 API 的 `feat/server_team` 分支：

```bash
git clone -b feat/server_team \
  https://github.com/TencentCloud/TencentDB-Agent-Memory.git

cd TencentDB-Agent-Memory/deploy/global-images
```

### 2. 配置并启动服务端

先复制服务端配置文件：

```bash
cp .env.example .env
```

本示例保存的是“喜欢的颜色”这类个人偏好，服务端必须使用 `chat` 提取模式。在
`TencentDB-Agent-Memory/deploy/global-images/.env` 中确认：

```dotenv
MEMORY_PROMPT_MODE=chat
```

然后运行启动脚本。脚本会交互式要求填写 Memory 和 Proxy 使用的 LLM 地址、
API Key、模型名称及协议：

```bash
./start-all.sh
```

如果提示无法连接 Docker daemon，请先启动 Docker：

```bash
sudo systemctl start docker
```

`code` 模式面向工程事实、任务和决策，可能会把个人偏好判断为无须沉淀的闲聊，
从而产生 `extracted=0`。

如果服务已经以 `code` 模式启动，修改 `.env` 后执行下面的命令重新创建
Memory Core 容器，已有数据卷会保留：

```bash
./start-memory-core.sh
```

启动成功后默认端口如下：

- Memory Core V3 API：`http://127.0.0.1:8420`
- Memory Panel：`http://127.0.0.1:8125`
- Knowledge Service：`http://127.0.0.1:8424`
- Memory Proxy：`http://127.0.0.1:8096`

可以检查容器状态：

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
curl http://127.0.0.1:8420/health
```

### 3. 创建 Team、User 和 Agent

获取首次启动时生成的管理员登录密钥：

```bash
cd TencentDB-Agent-Memory/deploy/global-images
cat .admin-key
```

打开 `http://localhost:8125`，使用 `.admin-key` 中的 `sk-mem-*` 登录，然后：

1. 点击左上角 Team 切换器，选择“新建团队”，例如创建 `trpc-agent-test`。
2. 进入该 Team 的“成员管理”，选择“添加成员”。
3. 选择“新建用户并加入团队”，例如创建 `test_user`。创建时完整
   `user_key` 只显示一次，请妥善保存。
4. 进入该 Team 的 “Agents 管理”，创建 `memory-assistant`。
5. 记录创建结果中的真实 `team_id` 和 `agent_id`，配置示例时需要使用 ID，
   不能填写 Team 或 Agent 的显示名称。

如果服务部署在远程主机，推荐从本地建立 SSH 隧道：

```bash
ssh -L 8125:127.0.0.1:8125 <user>@<server-ip>
```

然后在本地浏览器访问 `http://localhost:8125`。不要直接将管理面板暴露到公网。

这里有两种不同的 User 概念：

- Panel User：用于登录管理面板、管理 Team 和 Agent，拥有 `sk-mem-*` User Key。
- Memory User：框架运行时传入的业务用户 ID。本示例在 `run_agent.py` 中使用
  `user_id="alice"`，同一业务用户的不同 Session 共享长期记忆。

Panel User Key 不是 LLM API Key，也不是本示例中的 Gateway API Key。

### 4. 配置本地客户端

回到 tRPC-Agent Python 仓库，复制本地模板：

```bash
cp examples/memory_service_with_tencentdb/.env.local \
  examples/memory_service_with_tencentdb/.env
```

然后编辑 [`examples/memory_service_with_tencentdb/.env`](./.env)：

```dotenv
# 示例 Agent 自身调用的 OpenAI 兼容 LLM
TRPC_AGENT_API_KEY=<LLM API Key>
TRPC_AGENT_BASE_URL=<OpenAI 兼容的 /v1 地址>
TRPC_AGENT_MODEL_NAME=<模型名称>

# 直连 Memory Core V3 API，不是 8096 Proxy
TENCENTDB_MEMORY_ENDPOINT=http://127.0.0.1:8420

# 对应服务端 MEMORY_CORE_GATEWAY_API_KEY。
# 本地部署默认未开启 Gateway Bearer 鉴权时可使用 local。
TENCENTDB_MEMORY_API_KEY=local

# 本地部署的实例 ID 固定为 default
TENCENTDB_MEMORY_SERVICE_ID=default

# 填写 Panel 中创建出的真实 ID，不要填写显示名称
TENCENTDB_MEMORY_TEAM_ID=<team_id>
TENCENTDB_MEMORY_AGENT_ID=<agent_id>

# L1 提取是异步的，首次验证建议设置为 15 秒
TENCENTDB_MEMORY_PIPELINE_WAIT_SECONDS=15
```

如果 tRPC-Agent 与 Memory Core 不在同一台机器，需要把
`TENCENTDB_MEMORY_ENDPOINT` 改成 tRPC-Agent 进程能够访问的 Memory Core 地址。

> `MEMORY_PROMPT_MODE=chat` 应配置在 TencentDB Agent Memory 服务端的
> `deploy/global-images/.env` 中，而不是 tRPC-Agent 客户端的 `.env` 中。

## 方案二：腾讯云托管实例

如果使用腾讯云上的 TencentDB Agent Memory 产品，不需要运行本地
`start-all.sh`，也不需要访问本地 Memory Panel。请先从腾讯云控制台获取：

- Gateway 地址
- Gateway API Key
- Memory 实例 ID
- Team ID
- Agent ID
- 运行时使用的业务 User ID

复制远端模板：

```bash
cp examples/memory_service_with_tencentdb/.env.remote \
  examples/memory_service_with_tencentdb/.env
```

然后编辑 `.env`：

```dotenv
TRPC_AGENT_API_KEY=<LLM API Key>
TRPC_AGENT_BASE_URL=<OpenAI 兼容的 /v1 地址>
TRPC_AGENT_MODEL_NAME=<模型名称>

TENCENTDB_MEMORY_ENDPOINT=https://<控制台提供的 Gateway 地址>
TENCENTDB_MEMORY_API_KEY=<Gateway API Key>
TENCENTDB_MEMORY_SERVICE_ID=<Memory 实例 ID>
TENCENTDB_MEMORY_TEAM_ID=<team_id>
TENCENTDB_MEMORY_AGENT_ID=<agent_id>
TENCENTDB_MEMORY_PIPELINE_WAIT_SECONDS=15
```

腾讯云托管实例的 L1 提取策略由产品服务端管理，不属于
`TencentDBMemoryService` 的数据面接入范围。当前实例不适合个人偏好场景时，本示例
可能表现为：

```text
conversation/add 写入成功
L1 extracted=0
atomic/search 返回空结果
```

`TENCENTDB_MEMORY_API_KEY` 是 Gateway API Key，不是 Panel 的 `sk-mem-*`
User Key；Gateway 地址必须支持 `/v3/conversation/add` 和
`/v3/atomic/search`。

此外，请确认 `run_agent.py` 中的 `user_id` 是当前实例使用的业务用户 ID。同一个
业务用户的不同 Session 才能共享记忆。

## 公共步骤：运行示例

在 tRPC-Agent Python 仓库根目录运行：

```bash
python3 examples/memory_service_with_tencentdb/run_agent.py
```

示例执行以下流程：

1. `session-write` 告诉 Agent：“My favorite color is blue.”
2. 第一轮结束后，Runner 自动将新增事件写入 L0。
3. Memory Core 异步将该偏好提取为 L1 记忆。
4. 等待提取完成后，`session-recall` 在另一个 Session 中询问喜欢的颜色。
5. Agent 调用 `load_memory`，跨 Session 搜索 `alice` 的长期记忆。

## 测试结果

一次成功运行的关键输出如下，模型的具体措辞可能不同：

```text
User (session-write): My favorite color is blue. Please remember it.
Assistant: ... I've noted that your favorite color is blue.
Waiting <N>s for asynchronous memory extraction...

User (session-recall): What is my favorite color?
Assistant: Your favorite color is blue!
```

实际写入发生在对话结束后的 Runner 阶段，对 Agent 是透明的。第二个 Session 能
回答 `blue`，说明记忆检索链路可用；但由于当前实现会在 L1 无结果时搜索 L0 原始
对话，仅凭回答正确不能证明 L1 已成功提取。

## 故障排查

### 写入返回 HTTP 400

查看 tRPC-Agent 日志中的服务端错误信息。V3 API 要求消息时间为带时区的
RFC 3339 格式，例如 `2026-09-28T02:34:29Z`。

### 本地部署：写入成功但搜索不到记忆

检查 Memory Core 日志：

```bash
docker logs --since 10m tdai-memory-core
```

如果看到下面的日志：

```text
promptMode=code
Total extracted memories: 0
l1-empty reason=empty_scenes
```

说明服务端仍在使用 `code` 模式。设置 `MEMORY_PROMPT_MODE=chat` 并重新执行
`./start-memory-core.sh`。

### 腾讯云：L1 一直为空

腾讯云托管实例的提取模式不能通过本示例的 `.env` 修改。请确认：

1. Gateway 地址、实例 ID、Team ID 和 Agent ID 来自同一个实例。
2. `run_agent.py` 的 `user_id` 与查询时使用的业务用户一致。
3. 当前实例的服务端提取策略适合测试内容。默认编码场景可能不会沉淀“喜欢的颜色”
   这类个人偏好。
4. 已等待足够时间；L0 写入成功不代表 L1 已经生成。

如果仍然无法生成或检索 L1，请参考腾讯云官方文档检查服务端配置：

- [创建 Memory Prompt](https://cloud.tencent.com/document/product/1813/137169)
- [应用 Memory Prompt](https://cloud.tencent.com/document/product/1813/137172)
- [查询生效的 Memory Prompt](https://cloud.tencent.com/document/product/1813/137170)
- [L1 记忆语义检索](https://cloud.tencent.com/document/product/1813/135156)

这些属于腾讯云产品侧配置，不会由 `TencentDBMemoryService` 自动创建或修改。

如果只是异步提取尚未完成，可增大等待时间：

```dotenv
TENCENTDB_MEMORY_PIPELINE_WAIT_SECONDS=30
```

### 本地部署：查看写入和提取状态

```bash
docker logs --since 10m tdai-memory-core | \
  grep -E 'conversation/add|L1 complete|extracted|atomic/search'
```

预期可以看到：

```text
POST /v3/conversation/add status=200
L1 complete: extracted=1, stored=1
POST /v3/atomic/search status=200
```

## 实现说明

- `service_id/team_id/agent_id/user_id` 共同构成记忆隔离边界。
- `session_id` 仅在写入时发送；搜索时不限定 Session，因此支持跨 Session 召回。
- `/v3/atomic/search` 是 L1 语义检索接口；如果 L1 没有命中，当前实现会使用
  `/v3/conversation/search` 对 L0 原始对话做语义兜底。
- 服务只发送当前进程中尚未成功写入的事件。
- 进程重启后采用至少一次投递语义，因为 V3 写入接口没有调用方提供的幂等键。
- L1 提取是异步的，写入成功不代表记忆可以立即搜索。
- 记忆保留和删除由 TencentDB Agent Memory 管理，框架 TTL 不适用于该服务。

