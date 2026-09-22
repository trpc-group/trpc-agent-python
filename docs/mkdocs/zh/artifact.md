# Artifact 存储

Artifact 是 Agent 产生或使用的版本化文件。Artifact API 将标识、元数据与
`Part` 载荷分离，并同时提供临时内存存储和持久化本地文件存储。

## 架构

`ArtifactServiceABC` 定义异步存储接口：

- `save_artifact` 创建新的不可变版本；
- `load_artifact` 加载指定版本或最新版本；
- `list_artifact_keys` 列出当前作用域可见的 Artifact；
- `list_versions` 和 `list_artifact_versions` 获取版本历史；
- `get_artifact_version` 在不返回载荷的情况下读取元数据；
- `delete_artifact` 删除一个 Artifact 的全部版本。

SDK 提供两种实现：

- `InMemoryArtifactService` 适合测试和短生命周期进程；
- `FileArtifactService` 将 Artifact 持久化到本地目录，可在进程重启后恢复。

`FileArtifactService` 将每个完整版本保存为独立 JSON 记录，其中包含序列化的
`Part` 和 `ArtifactVersion`。写入时先创建并同步临时文件，再原子替换最终记录。
独占版本预留文件可防止并发写入者使用相同版本号。

## 基本用法

```python
from google.genai.types import Part
from trpc_agent_sdk.abc import ArtifactId
from trpc_agent_sdk.artifacts import FileArtifactService

service = FileArtifactService("./artifact_data")
artifact_id = ArtifactId(
    app_name="reporting",
    user_id="alice",
    session_id="session-1",
    filename="report.txt",
)

version = await service.save_artifact(
    artifact_id=artifact_id,
    artifact=Part(text="季度报告"),
    metadata={"status": "draft"},
)
latest = await service.load_artifact(artifact_id=artifact_id)
specific = await service.load_artifact(
    artifact_id=artifact_id,
    version=version,
)
keys = await service.list_artifact_keys(artifact_id=artifact_id)
versions = await service.list_artifact_versions(artifact_id=artifact_id)
await service.delete_artifact(artifact_id=artifact_id)
```

文本、内联二进制数据、外部文件引用和 Artifact 引用均使用现有的
`google.genai.types.Part` 表示并完整保留。

## 作用域与版本语义

Artifact 标识由 `app_name`、`user_id`、可选 `session_id` 和 `filename`
组成。

- 普通文件名属于 Session 作用域，仅对相同 App、用户和 Session 可见；
- 以 `user:` 开头的文件名属于用户作用域。存储时忽略 Session ID，并可在同一
  App 和用户的所有 Session 之间共享；
- `list_artifact_keys` 返回当前 Session 的 Artifact 和用户作用域 Artifact；
  不提供 Session ID 时仅返回用户作用域 Artifact；
- 版本号从 `0` 开始并单调递增；保存操作不会修改旧版本；
- 不传 `version` 时加载编号最高的完整版本；Artifact 或版本不存在时返回
  `None`；
- 删除操作会移除目标 Artifact 的全部版本，但不影响其他作用域或文件名。

元数据按版本存储在 `ArtifactVersion.custom_metadata` 中。MIME 类型、创建
时间、版本号和规范 `artifact://` URI 也会随版本持久化。

## 持久化与并发

使用相同根目录创建新的 `FileArtifactService` 实例，即可在进程重启后读取原有
数据。原子替换保证读取者不会看到写入一半的记录。共享同一文件系统的多个服务
实例并发保存时，会预留不同的版本号。

该后端适用于本地磁盘，或具备常规原子文件创建和重命名语义的文件系统。它不是
分布式数据库；删除整个存储目录等破坏性管理操作需要与应用写入者协调。

## 安全

应将存储根目录视为应用专属数据：

- App、用户、Session 和文件名组件不能为空、`.` 或 `..`，也不能包含路径
  分隔符或 NUL 字节；
- 各组件在作为目录名之前会编码；
- 每个解析后的路径都会检查是否仍位于配置的根目录下；
- 如果符号链接将生成路径重定向到根目录外，操作会被拒绝；
- 本服务不会加密 Artifact 内容或元数据。

请使用操作系统权限限制访问，不要将根目录放在 Web 可直接访问的目录中；敏感
Artifact 应使用加密磁盘。仅使用可信文件系统：能够直接修改存储目录的用户也能
破坏或删除记录。

## 完整示例

可运行的
[`examples/artifact_service` README](../../../examples/artifact_service/README.md)
展示了完整的 `LlmAgent`、Tool、`Runner` 与 `FileArtifactService`
调用链。Agent 生成 Markdown 报告，通过 Tool 保存 Artifact，并在重新创建
文件存储实例后读取报告，以验证持久化结果。
