# OpenAPI 工具

`OpenAPIToolSet` 可将 OpenAPI 3.x 文档中的操作转换为标准 tRPC-Agent `BaseTool`。它支持 Python 字典或本地 JSON/YAML 文件，以 `operationId` 生成工具名，向模型暴露 JSON 输入 Schema，并通过 `httpx` 异步发起请求。

## 使用场景

OpenAPI Tools 适合已经有 REST API 和 OpenAPI 文档，但不希望逐个开发
`FunctionTool` 的系统，例如：

- CRM 客户查询和跟进记录；
- 工单查询、创建和状态更新；
- 商品、库存和订单服务；
- 企业内部审批、CMDB 或运维平台；
- 由第三方提供 OpenAPI 规范的 SaaS 服务。

其核心价值是复用现有 API 契约：

```text
OpenAPI 文档
→ operationId 和参数 Schema
→ Agent 可见的 Tool
→ 模型选择 Tool 并生成参数
→ 异步 HTTP 请求
→ Tool Result 返回模型
```

它不会替代 Agent。`OpenAPIToolSet` 负责 API 到 Tool 的适配，`LlmAgent`
仍负责理解用户意图、选择 API、组合参数并解释响应。

## 基本用法

```python
from trpc_agent_sdk.tools import OpenAPIToolSet

toolset = OpenAPIToolSet(
    "openapi.yaml",
    base_url="https://api.example.com/v1",
    operation_ids=["get_pet", "create_pet"],
    static_headers={"Authorization": "Bearer secret"},
    allowed_hosts=["api.example.com"],
    timeout=20.0,
)

tools = await toolset.get_tools()
```

可将 `toolset` 直接放入 Agent 的 `tools` 列表。如果没有 Runner 管理生命周期，请调用 `await toolset.close()`。也可传入已有的 `httpx.AsyncClient`；此时客户端仍由调用方关闭。`transport` 可用于让 ToolSet 管理自定义传输，例如 `httpx.MockTransport`。

`auth` 接受 `httpx.Auth` 实现。`static_headers` 在操作参数之后写入，因此模型生成的 Header 参数不能覆盖应用注入的可信 Header。

## 转换逻辑

每个 HTTP 操作按以下规则转换：

1. `operationId` 成为工具名；缺失、重复或不安全的 ID 会立即报错。
2. 合并 Path Item 与 Operation 参数；相同 `(name, location)` 的 Operation 参数优先。
3. path、query、header、cookie 参数成为顶层工具参数。
4. `application/json` 请求体成为 `request_body` 参数。
5. 必填参数和必填请求体进入生成 JSON Schema 的 `required`。
6. 生成 Schema 前解析本地 `$ref` JSON Pointer。
7. Base URL 按 `base_url`、Operation servers、Path servers、文档 servers 的顺序选择。

成功调用返回：

```python
{
    "status_code": 200,
    "content_type": "application/json",
    "content": {"id": "pet-1"},
}
```

JSON 和 `+json` 媒体类型会被解析；其他响应返回文本，空响应返回 `None`。HTTP 4xx/5xx 抛出 `OpenAPIHTTPError`；传输、参数和转换错误会使用清晰的 `OpenAPIToolError` 或 `OpenAPISpecError`。

## 操作选择与过滤

使用 `operation_ids` 在转换时选择固定操作子集。指定不存在的 ID 会报错。标准 `tool_filter` 与 `is_include_all_tools` 参数仍可通过普通 ToolSet 接口实现运行时过滤。

## 安全与 SSRF

应将 OpenAPI 文档视为可执行配置：

- 优先使用代码仓库中受控的本地规范。为降低 SSRF 风险，本功能不支持直接从 URL 加载规范；如有需要，应在应用层获取并校验可信文档，再传入字典。
- 使用可信配置设置 `base_url`，不要接受用户输入。
- 使用 `allowed_hosts` 限制最终请求主机。重定向行为由传入的 `httpx.AsyncClient` 控制；默认客户端不跟随重定向。
- 使用 `auth` 或 `static_headers` 注入凭证，不要把密钥写入 OpenAPI 文件。
- 向模型暴露前审查操作，尤其是具有修改或删除效果的操作。
- 使用网络出口策略作为最终 SSRF 防线；Host 白名单不能替代 DNS 与网络层控制。

Base URL 必须是绝对 HTTP(S) URL，且不能包含内嵌凭证或 Fragment。

## 支持范围与限制

支持：

- Python 字典和本地 JSON/YAML 格式的 OpenAPI 3.x
- 具有安全且唯一 `operationId` 的 HTTP 操作
- path、query、header、cookie 参数
- JSON 请求体
- 本地、非循环 `$ref`
- Server 变量默认值
- JSON、文本和空响应体

暂不支持：

- Swagger/OpenAPI 2.x
- 远程或外部 `$ref`，以及远程规范 URL 加载
- 循环 Schema
- 非 JSON 请求体、multipart 上传、callback、link 或 webhook
- `deepObject` 等完整 OpenAPI 参数序列化样式
- 自动解析 OpenAPI `securitySchemes`
- 响应 Schema 校验

## 完整 Agent 示例

[OpenAPI 工具示例](../../../examples/openapi_tools/README.md)采用与 Quickstart
一致的完整 Agent 结构：

```text
root_agent (LlmAgent)
└── OpenAPIToolSet
    ├── get_pet
    └── create_pet
```

示例使用真实模型判断应调用 `get_pet` 还是 `create_pet`，并打印模型生成的
参数、Tool Result 和最终回答。Pet API 通过 `httpx.MockTransport` 在进程内
模拟，因此不会访问外部业务服务。

运行前复制并配置环境变量：

```bash
cd examples/openapi_tools
cp .env.example .env
python run_agent.py
```

生产环境可将 Mock Transport 替换为真实 HTTP API，并通过 `operation_ids`
只暴露经过审查的操作。
