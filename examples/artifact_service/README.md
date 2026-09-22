# FileArtifactService 完整示例

本示例参考 `examples/quickstart` 的目录结构，演示完整的 Agent 调用链：

```text
用户请求
→ LlmAgent 生成 Markdown 报告
→ 调用 save_report Tool
→ InvocationContext.save_artifact()
→ FileArtifactService 持久化到本地文件
→ 重新创建 FileArtifactService 并读取验证
```

## 关键特性

- 使用标准 `LlmAgent`、`Runner` 和 `InMemorySessionService`
- Agent 通过 `FunctionTool` 保存、查询和加载 Artifact
- Tool 不直接拼接存储路径，而是通过当前 `InvocationContext` 获得
  App、用户和 Session 作用域
- `FileArtifactService` 将模型生成的报告持久化到 `artifact_data/`
- 重新创建存储实例后仍能读取报告，证明数据不是仅保存在内存中

## Agent 层级结构

```text
root_agent (LlmAgent)
├── save_report
├── list_reports
└── load_report
```

目录结构：

```text
artifact_service/
├── agent/
│   ├── agent.py
│   ├── config.py
│   ├── prompts.py
│   └── tools.py
├── .env.example
├── README.md
└── run_agent.py
```

## 关键代码

`agent/tools.py` 中的 `save_report` 使用框架注入的
`InvocationContext` 保存 Artifact：

```python
version = await tool_context.save_artifact(
    filename=filename,
    artifact=Part.from_text(text=content),
)
```

`run_agent.py` 将文件存储服务交给 `Runner`：

```python
artifact_service = FileArtifactService("./artifact_data")
runner = Runner(
    agent=root_agent,
    app_name="artifact_report_demo",
    artifact_service=artifact_service,
    session_service=InMemorySessionService(),
)
```

因此 Agent Tool 保存的内容最终会进入 `FileArtifactService`，并自动按
App、用户、Session、文件名和版本隔离。

## 环境要求

- Python 3.10+
- 可访问的 OpenAI 兼容模型服务

## 配置

```bash
cd examples/artifact_service
cp .env.example .env
```

填写：

```dotenv
TRPC_AGENT_API_KEY=your-api-key
TRPC_AGENT_BASE_URL=https://your-openai-compatible-endpoint/v1
TRPC_AGENT_MODEL_NAME=your-model-name
```

## 运行

从仓库根目录执行：

```bash
python examples/artifact_service/run_agent.py
```

或进入示例目录：

```bash
cd examples/artifact_service
python run_agent.py
```

## 预期结果

运行时可以看到：

1. 模型调用 `save_report`；
2. Tool 返回文件名和版本号；
3. Agent 返回保存成功的最终回答；
4. 新建的 `FileArtifactService` 列出 `quarterly_report.md`；
5. 程序从磁盘读取并打印报告内容。

持久化数据位于：

```text
examples/artifact_service/artifact_data/
```

该目录由 `.gitignore` 忽略。每次运行使用独立 Session，因此不会覆盖之前
Session 的报告。

更多存储布局、版本和安全说明请参考
[Artifact 文档](../../docs/mkdocs/zh/artifact.md)。
