# 评委快速验收与需求证据矩阵

本页用于在较短时间内确认项目不是架构草图，而是包含真实 tRPC-Agent Runner、数据库迁移、双 IM Adapter、故障恢复和自动化验收的可运行工程。

## 1. 一条命令完成黑盒验收

```powershell
python examples/multi_tenant_im_agent/scripts/judge_demo.py
```

脚本启动独立 FastAPI Gateway 和临时数据库，从 HTTP 边界验证健康检查、Telegram/企业微信验签、账号路由、重复投递抑制、同 ID 异载荷冲突、Admin 鉴权和 Prometheus 指标，然后自动停止服务并清理数据库。离线确定性模型仍经过真实 tRPC-Agent `LlmAgent + Runner`，不需要模型或 IM 凭据。

完整单元与契约测试：

```powershell
pytest examples/multi_tenant_im_agent/tests -q
```

## 2. 项目要求与可检查证据

| 项目要求 | 已实现内容 | 主要代码 | 自动化证据 |
|---|---|---|---|
| 多租户模型 | tenant、Agent App、应用配置、模型、工具白名单、IM 绑定；`(tenant_id, agent_app_id)` 复合隔离 | `domain.py`、`config.py`、`repository.py` | 同名 Agent App 跨租户隔离测试 |
| 节点拓扑 | Gateway、无状态 Worker、Channel Adapter、共享 Session 后端、Admin API、Telemetry、Outbox | `service.py`、`app.py`、`runtime.py`、部署清单 | HTTP 探针、后台恢复与 Runner 关闭测试 |
| 水平扩展 | 无 sticky session；确定性 Session ID；SQL Session 租约串行化；Redis/SQL 共享 Session | `domain.py`、`repository.py`、`runtime.py` | 租约互斥、用户/群聊/租户隔离测试 |
| 租户隔离 | 配置、复合外键、IM 账号、Session HMAC、工具 allowlist、日志脱敏、密钥仅引用环境变量 | `repository.py`、`runtime.py`、`governance.py` | 跨租户相同消息号、相同应用 ID、未知工具 fail-closed 测试 |
| 多后端 | tRPC-Agent InMemory、Redis、SQL Session Service；Memory、Summary、Artifact、Knowledge、Audit 数据模型 | `runtime.py`、`repository.py` | 后端配置校验及 Alembic/ORM 契约测试 |
| 数据一致性 | Session 行锁递增序列、幂等声明、事务内消息完成与 Outbox 提交、租约恢复 | `repository.py` | 重复消息、payload conflict、失败重试、Outbox 恢复测试 |
| IM 软件接入 | Telegram 与企业微信 Adapter；验签、解析、群/单聊 Session、长度限制和外发转换 | `adapters.py` | 两通道签名与归一化测试、真实 HTTP 黑盒验收 |
| 幂等范围 | `(tenant_id, channel, account_id, external_message_id)`，同租户多机器人不会误冲突 | `repository.py` | 跨账号相同外部消息 ID 测试 |
| 治理安全 | 用户白名单、请求大小、输入和 Token 预算、模型超时、工具默认禁用、Runner Filter 二次校验 | `governance.py`、`runtime.py`、`app.py` | 策略拒绝、预算拒绝、未知工具拒绝测试 |
| 预算与成本 | 数据库行锁原子预留月预算，模型完成后按 usage metadata 结算；审计和 Prometheus 汇总 Token/成本 | `repository.py`、`service.py`、`telemetry.py` | 实际用量结算和 provider usage metadata 测试 |
| 可观测性 | OpenTelemetry 根 Span 并继承 Runner/模型/工具 Span；请求、阶段延迟、投递、审计、Token、成本指标 | `telemetry.py`、`service.py` | Metrics HTTP 验收与安全 Trace 测试 |
| 审计 | 题目要求字段齐全；用户 ID HMAC；不保存正文、Key、Token；审计故障不触发模型重放 | `repository.py`、`service.py` | 审计字段与审计库故障测试 |
| 故障恢复 | 模型失败可重投；回复结果先入 Outbox；指数退避、稳定抖动、8 次后死信；崩溃租约恢复 | `repository.py`、`service.py` | 模型失败恢复、投递恢复、死信测试 |
| 灰度和回滚 | 配置版本、按 tenant/account 路由 canary；迁移先行；RollingUpdate、HPA、PDB、探针 | `ARCHITECTURE.zh_CN.md`、`deploy/kubernetes.yaml` | 部署清单可静态检查 |
| 最小/生产部署 | Docker Compose 最小栈；Kubernetes 迁移 Job、Gateway、Redis、MySQL、Collector 拓扑 | `docker-compose.yml`、`deploy/kubernetes.yaml` | `/healthz`、`/readyz` 与迁移契约测试 |

## 3. 最容易忽略的工程细节

- IM 平台重投已完成消息时只返回幂等成功，不会再次调用模型、工具或发送回复。
- Agent 结果和投递 Outbox 在同一数据库事务完成；IM 故障不会导致昂贵的模型调用重放。
- 审计写入故障被独立计数并脱敏记录，不会把已经完成的消息错误标成 failed。
- 月度预算不是“先查询再判断”，而是在数据库事务中预留，避免多 Worker 并发超额。
- 离线验收使用确定性模型，但模型仍由真正的 Runner 执行；在线模式只替换模型实例和 Session 后端。
- 真实模型冒烟脚本只输出状态、字符数和 Token 数，不输出响应正文或凭据。

## 4. 诚实的实现边界

- 企业微信加密正文的 AES/KMS 解密交给认证 Ingress 插件；示例实现签名校验以及解密后 JSON/XML 归一化。
- Memory/Knowledge/Artifact 的跨介质迁移给出可执行阶段、数据模型和一致性规则，但不附带特定云厂商账号。
- 示例工具注册表默认为空并 fail-closed；真实危险工具需要业务审批系统，不能用演示确认按钮代替。
- 多区域需要 home-region 或全局序列服务；本示例不声称单数据库能够解决跨区域一致性。

以上边界不会影响本地自动验收，并避免为了演示而提交平台账号、生产密钥或不可验证的云资源。
