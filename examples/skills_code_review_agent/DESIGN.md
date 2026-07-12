# 方案设计说明

code-review Skill 保存稳定工作流，规则与脚本按需加载。Agent 从 diff、路径或 Git 工作区提取新增行，检查代码执行、异步阻塞、资源泄漏、数据库连接、敏感信息和测试缺失。同文件同一行同类仅保留最高置信项，低于阈值的结果进入人工复核。

生产默认使用无网络、只读挂载且限制资源的容器；fake mode 用于验收，本地执行只是开发 fallback。Filter 前置检查命令、路径、网络和预算，deny 或 needs_human_review 均不执行。环境采用白名单，输出、finding、报告和入库前统一脱敏；沙箱失败不终止静态评审。

SQLite 按 task id 关联 task、sandbox run、filter block、finding 与 report，可通过 ReviewStore 接入其他 SQL。监控记录耗时、工具、拦截、finding、severity 和异常。数据库不保存原始 diff，只保存哈希与脱敏摘要；JSON 供机器消费，Markdown 供审批。静态规则和容器不能发现所有风险，生产仍需最小权限、镜像审计和人工复核。
