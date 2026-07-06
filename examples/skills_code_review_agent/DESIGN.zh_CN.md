# 方案设计说明

**Skill 设计**：code-review Skill 以 SKILL.md 声明用法，规则文档与执行脚本分离；diff 解析器、规则引擎与密钥正则表统一放在 scripts/lib（纯标准库），沙箱内直接执行，宿主经 importlib 加载同一份代码，检测与脱敏单源不漂移。规则覆盖安全风险、异步错误、资源泄漏、测试缺失、敏感信息泄漏与数据库生命周期六类。

**沙箱隔离**：生产默认 Container 运行时，亦支持 Cube/E2B；本地运行时仅作开发回退，并重写环境构造逻辑，仅放行白名单变量。每次执行受超时与输出字节上限约束；失败、超时、异常均记录为 sandbox_run 行并触发宿主内规则回退，评审任务绝不中断。

**Filter 策略**：沙箱执行前经 BaseFilter 链决策：高风险脚本内容、非白名单命令、禁止路径、网络访问、超预算运行分别产生 deny 或 needs_human_review；被拦截的执行不会进入沙箱，原因同时写入报告与数据库。

**监控字段**：总耗时、沙箱耗时与次数、工具调用数、拦截次数与决策分布、finding 数、severity 分布、异常类型分布，随报告入库可查，并辅以 tracer span。

**数据库 schema**：cr_review_task、cr_sandbox_run、cr_filter_event、cr_finding、cr_report 五表，经 ReviewStore 抽象接口访问；SQLite 为默认实现，更换连接 URL 即切换 MySQL/PostgreSQL。

**去重降噪**：同文件同行同类问题仅报一条，保留最高严重级别并合并命中的规则号；置信度低于阈值的启发式结果进入人工复核桶，不混入高置信 findings。

**安全边界**：证据在沙箱内先行脱敏，宿主对报告与全部入库字段递归二次脱敏；diff 摘要不含代码内容，明文密钥不落任何持久化面。
