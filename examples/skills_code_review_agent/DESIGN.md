# 方案设计说明

**总体链路**:CLI 将输入(unified diff / git 工作区 / 文件列表)解析为文件-hunk-候选行号结构并落库;LlmAgent 双挂 SkillToolSet 语义(仅 skill_load / skill_run 两个工具 + skill_repository,刻意不用 SkillToolSet 以免暴露 workspace_exec 等第二执行面);skill_load 注入规则文档,skill_run 单次调用在沙箱内执行 driver 脚本串行跑 6 类静态检查,findings 写 $OUTPUT_DIR 经文件通道回传(绕开 16KB stdout 截断);有 API key 时 LLM 单次复核并可补充发现。dry-run 用脚本化 FakeModel 发起同样的工具调用,无 key 也真实覆盖 skills/Filter/沙箱/落库全链路。

**Skill 设计**:SKILL.md + docs/rules-\*.md(6 类)+ scripts/。diff 解析器是单一实现(scripts/parse_diff.py),宿主经 importlib 复用,两侧永不分歧。repo 模式重建完整 post-image 跑 AST;diff-only 模式 gap 填空行保持行号对齐,AST 失败降级正则并降置信度。

**沙箱隔离**:默认 Container runtime——禁网(network_mode=none,附容器内出网探针断言测试)、宿主环境变量不进容器、skills 目录只读挂载;超时钳制 + 输出截断,超时/失败返回结构化结果不崩溃,任务降级为 partial 并落库。local 仅 --unsafe-local 显式回退。

**Filter 策略**:三层不重叠。SkillRunTool allowed_cmds 白名单挡 shell 元字符与非 python3 命令;ReviewToolFilter 前置拦截脚本越权(未知脚本→needs_human_review)、路径逃逸、env 注入、host 输入越界、超预算,并钳制超时参数,全部决策(含 allow)写 filter_event 表;拒绝 3 次后返回终止指令防重试空转;agent 级 after_tool_callback 统一脱敏。deny/needs_human_review 均不执行(测试断言 handler 未被调用,且工具面架构性断言无绕过路径)。

**数据库**:SqlStorage 传自定义 metadata 建 7 表(review_task / diff_file / sandbox_run / filter_event / finding / report / metrics),SQLite 默认,DSN 可切 MySQL/PostgreSQL。

**去重降噪**:两级——exact 键 sha256(rule_id+file+行号+归一化证据) 配 UNIQUE 约束双保险;同 (file, line, category) 合并保留最高 severity,其余以 suppressed 状态留档可审计。分流用确定性决策表:高精度静态直接进 findings;低精度按类别分流(注入/密钥宁报勿漏,测试缺失宁缺勿滥进 warnings);LLM 补充必须逐字引用 diff 否则丢弃;静态命中而 LLM 判否的高危项转人工复核。

**监控**:metrics 表记录总耗时、沙箱耗时、工具调用数、拦截数、finding 数、severity/异常分布、token 用量、阶段耗时,并标注口径(事件流直取 vs 自埋)。

**安全边界**:容器禁网、env 白名单、超时与输出上限、Redactor(20+ 密钥格式 + allowlist)在工具输出/落库/报告三处兜底,diff 内容在 prompt 中框定为不可信数据(附提示注入 fixture 断言结论不变)。
