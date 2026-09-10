# 多租户 IM Agent 网关

这是针对“多租户与节点部署、数据同步与多后端、IM 接入、治理安全、故障恢复与运维”要求实现的可运行参考项目。它不是伪代码：在线模式会为每个租户创建真正的 tRPC-Agent `LlmAgent + Runner`，并按照租户配置选择 Redis、SQL 或内存 Session 后端。

完整设计与逐项验收说明见 [ARCHITECTURE.zh_CN.md](./ARCHITECTURE.zh_CN.md)。

## 已实现能力

- `(channel, account_id) -> tenant_id` 唯一路由，未知账号默认拒绝。
- Telegram 与企业微信两类 Channel Adapter，包含回调验签、统一消息信封与回复投递。
- 直接会话按用户隔离，群聊按群共享，线程继续隔离；Session ID 使用 HMAC，日志不保存平台原始用户 ID。
- SQL 唯一幂等键，防止 IM 重复投递造成模型和工具重复执行。
- 数据库 Session 租约串行化同一会话，Worker 无状态且不依赖 sticky session。
- tRPC-Agent Session 后端可按租户选择 InMemory、Redis 或 SQL。
- 回复与 Outbox 同事务提交；投递失败后台重试，Worker 崩溃后可恢复过期任务。
- 租户级用户白名单、输入长度、单请求/月度 Token 预算，并在 Runner 内增加真实的 tRPC-Agent Filter 二次 fail-closed 校验。
- 全字段审计表、Prometheus 指标、OpenTelemetry OTLP Trace。
- Docker Compose 最小部署与 Kubernetes 生产部署样例。

## 本地离线运行

离线模式不会调用模型和 IM 平台，适合验收路由、验签、幂等和审计：

### 评委一键验收（推荐）

安装项目依赖后，只需执行一条命令：

```powershell
python examples/multi_tenant_im_agent/scripts/judge_demo.py
```

脚本会自动选择本机端口、启动真实 HTTP Gateway、创建临时数据库、执行完整黑盒验收并清理进程和数据库。通过时会逐项输出 `[PASS]`，不需要模型 API Key、Telegram Bot 或企业微信账号，也不会访问外网。

### 手工启动

```powershell
examples/multi_tenant_im_agent/scripts/run_offline_demo.ps1
```

脚本使用 `config.local.json` 和仅供本机演示的占位密钥，不访问模型、Telegram 或企业微信。也可以参考 `.env.local.example` 手工设置环境变量。

服务启动后：

- `GET /healthz`：进程存活探针。
- `GET /readyz`：数据库就绪探针。
- `GET /metrics`：Prometheus 文本指标。
- `GET /admin/tenants`：需 `X-Admin-Token`，仅返回不含密钥的租户摘要。
- `POST /webhooks/telegram/acme-support-bot`：Telegram 回调。
- `POST /webhooks/wecom/acme-wecom-app`：企业微信回调。

另开一个 PowerShell 窗口，继承或设置 `.env.local.example` 中三个验收密钥后运行黑盒验收：

```powershell
$env:ADMIN_API_TOKEN="local-demo-admin-token"
$env:ACME_TELEGRAM_WEBHOOK_SECRET="local-telegram-secret"
$env:ACME_WECOM_CALLBACK_TOKEN="local-wecom-token"
python examples/multi_tenant_im_agent/scripts/acceptance.py
```

它会自动验证健康检查、Admin 鉴权、Telegram/企业微信验签、重复消息幂等、同 ID 异载荷冲突和 Prometheus 指标。`requests/webhooks.http` 还提供了可手工执行的请求样例。

## 在线运行

1. 复制 `config.example.json`，为每个租户配置独立的 Agent、模型、Session 后端和 IM 账号。
2. 只在配置中填写密钥对应的环境变量名称，真实密钥由 Vault、KMS 或 Kubernetes Secret 注入。
3. 设置 `OFFLINE_ECHO_MODE=false`，提供模型 API Key、Redis/SQL DSN 和通道密钥。
4. 将公网 IM 回调指向 `/webhooks/{channel}/{account_id}`。

开发环境可使用：

```powershell
docker compose -f examples/multi_tenant_im_agent/docker-compose.yml up --build
```

Compose 会先运行 Alembic 迁移，再启动 Gateway。手工迁移命令为：

```powershell
pip install -e ".[multi-tenant-im]"
alembic -c examples/multi_tenant_im_agent/alembic.ini upgrade head
```

生产环境从 `deploy/kubernetes.yaml` 起步，并将 Redis、SQL、Secret 管理和 Ingress 替换为企业托管服务。部署流水线应先等待迁移 Job 成功，再发布 Deployment；生产环境保持 `AUTO_CREATE_SCHEMA=false`。

## 测试

```powershell
pytest examples/multi_tenant_im_agent/tests -q
```

测试覆盖租户路由冲突、会话隔离、Telegram/企业微信验签、用户策略、幂等重投、载荷冲突、失败恢复、Session 租约、Outbox 重试和 HTTP 健康检查。

## 目录

| 文件 | 职责 |
|---|---|
| `config.py` | 租户配置和 IM 账号路由 |
| `domain.py` | 统一消息信封、租户配置和安全标识 |
| `adapters.py` | Telegram/企业微信适配和外发客户端 |
| `repository.py` | 数据模型、幂等、Session 租约、审计与 Outbox |
| `runtime.py` | 真正的 tRPC-Agent Runner 多租户集成 |
| `governance.py` | 租户级前置治理策略 |
| `telemetry.py` | Prometheus 指标和 OpenTelemetry |
| `service.py` | 完整消息处理编排 |
| `app.py` | Webhook、Admin API、探针和后台重试 |
| `migrations/` | Alembic 版本化数据库迁移 |
| `scripts/acceptance.py` | 已运行服务的黑盒验收 |
| `scripts/judge_demo.py` | 自启动、自验收、自清理的一键评审演示 |
