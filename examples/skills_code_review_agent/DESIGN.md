# Skills Code Review Agent 方案设计

本示例以 `code-review` Skill 描述审查流程和规则，由 Agent 通过受控工具加载 Skill，并调用固定扫描脚本处理统一格式的 diff。输入层支持 diff 文件、文件列表、Git 工作区及内置 fixture，保留文件、hunk 和候选行号，二进制及非法路径转为明确告警或错误。

生产默认使用禁网 Container workspace；local runtime 仅在显式环境变量开启时作为开发后备。执行计划固定命令、工作目录、输入输出路径、环境变量名、超时和输出上限，并计算摘要。Filter 在执行前校验计划完整性、路径、命令、环境、网络与预算，先持久化决定；`DENY` 和 `NEEDS_HUMAN_REVIEW` 不进入沙箱。

SQLite 默认保存 review task、输入摘要、Filter 决定、sandbox run、finding 和最终报告，存储接口复用 SQLAlchemy/`SqlStorage`，可通过 SQL URL 切换受支持后端。Finding 使用固定结构，按任务、文件、行号和类别去重；低置信结果进入 warning 与人工复核，不混入高置信发现。

统一脱敏器覆盖沙箱输出、异常、数据库及 JSON/Markdown 报告，原始 diff 不入库。报告记录总耗时、沙箱耗时、工具调用、拦截次数、finding 数量、严重级别和异常类型分布。超时、输出超限或沙箱失败均形成可查询记录，不使评审流程崩溃；fake model 无需 API Key，仍执行解析、Filter、沙箱和落库主链。
