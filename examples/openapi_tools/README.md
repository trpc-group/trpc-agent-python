# OpenAPI Tools 完整 Agent 示例

本示例参考 `examples/quickstart` 的目录结构，展示模型如何调用由 OpenAPI
文档自动生成的 Tool。模型负责判断使用哪个 API、生成参数并解释结果；
`OpenAPIToolSet` 负责把 Tool 调用转换为真实 HTTP 请求。

```text
用户请求
→ LlmAgent 选择 OpenAPI Tool
→ OpenAPIToolSet 生成 HTTP 请求
→ Pet API 返回结果
→ Agent 向用户解释结果
```

Pet API 使用 `httpx.MockTransport` 在当前进程中模拟，因此不会访问外部业务
服务；`LlmAgent` 仍需要配置真实的 OpenAI 兼容模型。

## 使用场景

适合以下场景：

- 企业已有大量 REST API，并且已经维护 OpenAPI 3.x 文档；
- 希望快速把 CRM、工单、库存、订单或内部平台接口提供给 Agent；
- 不希望为每个 HTTP 接口重复编写 `FunctionTool`；
- 希望由 OpenAPI Schema 自动约束模型生成的 Path、Query 和 JSON Body；
- 需要通过操作白名单只向模型开放部分安全接口。

例如，一个订单系统有数十个查询接口时，可以直接选择
`get_order`、`list_orders` 和 `query_inventory` 等 `operationId` 生成 Tool，
而不必逐个编写 HTTP 请求代码。

## Agent 层级结构

```text
root_agent (LlmAgent)
└── OpenAPIToolSet
    ├── get_pet
    └── create_pet
```

## 目录结构

```text
openapi_tools/
├── agent/
│   ├── agent.py
│   ├── config.py
│   ├── prompts.py
│   └── tools.py
├── .env
├── openapi.yaml
├── README.md
└── run_agent.py
```

- `openapi.yaml` 描述 `get_pet` 和 `create_pet` 两个 API。
- `agent/tools.py` 加载 OpenAPI，并配置 Mock API、Host 白名单和静态 Header。
- `agent/agent.py` 把 `OpenAPIToolSet` 交给 `LlmAgent`。
- `run_agent.py` 使用 `Runner` 发送查询和创建宠物的请求，并打印 Tool 调用。

## 关键代码

根据 OpenAPI 文档创建 ToolSet：

```python
toolset = OpenAPIToolSet(
    "openapi.yaml",
    allowed_hosts=["pets.local"],
    static_headers={"X-Demo-Token": "local-only"},
    transport=httpx.MockTransport(_mock_api),
)
```

将整个 ToolSet 直接交给 Agent：

```python
agent = LlmAgent(
    name="openapi_pet_agent",
    model=model,
    instruction=INSTRUCTION,
    tools=[toolset],
)
```

当用户查询 `pet-1` 时，模型调用：

```text
get_pet(pet_id="pet-1")
```

框架转换为：

```http
GET https://pets.local/v1/pets/pet-1
```

创建宠物时，模型调用 `create_pet`，`request_body` 会成为 HTTP JSON Body。

## 环境配置

```bash
cd examples/openapi_tools
```

填写 OpenAI 兼容模型配置：

```dotenv
TRPC_AGENT_API_KEY=your-api-key
TRPC_AGENT_BASE_URL=your-base-url
TRPC_AGENT_MODEL_NAME=your-model-name
```

## 运行

从仓库根目录执行：

```bash
python examples/openapi_tools/run_agent.py
```

示例会运行两个独立请求：

1. 查询 `pet-1`，预期调用 `get_pet`；
2. 创建名为 Pixel 的狗，预期调用 `create_pet`。

输出中可以看到模型生成的 Tool 参数、Mock API 返回值和 Agent 最终回答。

## 替换为真实 API

生产使用时：

1. 将 `openapi.yaml` 替换为经过审查的 OpenAPI 3.x 文档；
2. 删除 `httpx.MockTransport`；
3. 使用可信配置设置 `base_url` 和 `allowed_hosts`；
4. 使用 `auth` 或 `static_headers` 注入认证信息；
5. 通过 `operation_ids` 只开放必要接口。

支持范围和 SSRF 安全说明见
[OpenAPI Tools 文档](../../docs/mkdocs/zh/openapi_tools.md)。
