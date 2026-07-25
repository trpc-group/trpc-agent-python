# 方案设计说明

原型分为五层。输入层解析 unified diff、文件列表及 Git 工作区，提取文件、hunk、上下文和目标行号，仅保存哈希、统计与脱敏预览。Skill 包含 `SKILL.md`、规则文档、机器规则和确定性脚本，经标准 `skill_load`、`skill_run` 调用，无模型密钥也可复现。治理层继承 `BaseFilter`，在创建 workspace 前检查脚本、路径、网络、环境变量、超时和输出预算；`deny` 与 `needs_human_review` 都会终止执行并记录原因。执行层默认使用禁网 Container，Local 仅作显式开发回退，失败或超时不会中断报告。存储层基于 `SqlStorage`，分别保存 task、sandbox run、filter decision、finding、metrics 和 report，可通过数据库 URL 更换后端。finding 按文件、行号、类别去重，高置信结果进入主列表，低置信结果转人工复核。扫描输出、主机解析、报告渲染和数据库写入均执行脱敏。监控记录总耗时、沙箱耗时、工具调用、拦截、severity、异常与脱敏次数，用于评测和回放。
