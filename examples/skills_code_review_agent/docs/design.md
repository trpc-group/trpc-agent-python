# 方案设计

本原型采用 workflow-shaped、agent-driven 架构。Workflow 负责输入、校验、落库和报告；Agent 决定复用历史证据，还是加载 `code-review` Skill 进入沙箱。Skill 将安全、异步、资源、数据库、测试和敏感信息检查拆成六个脚本，diff 与文件读取均分页返回证据。输入支持 diff、文件列表、Git 工作区和 fixture，并保留 hunk、上下文和行号。检查运行在禁网 Docker workspace；代码只读，外部 diff 仅挂载私有副本，容器采用非 root、只读根文件系统、无 capability 及资源限制。超时进程在容器内终止，输出进入模型前限量脱敏。Filter 按输入类型限制命令、Skill 参数、路径、网络、环境变量和预算，拒绝项不执行。可替换存储接口默认使用 SQLite，也可由环境变量切换 PostgreSQL；两种实现均分表保存任务、输入、执行、拦截、finding、监控和报告，PostgreSQL 另有短事务、参数化 SQL、TLS 与连接/语句超时边界，并限制凭据暴露。结果按文件、行号、类别去重，低置信项进入 warnings。监控记录总耗时、沙箱耗时、工具调用、拦截、严重级别和异常；失败转人工复核并保留审计记录。
