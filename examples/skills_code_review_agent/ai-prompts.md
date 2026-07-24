# Issue #92 开发过程记录

## 第 1 轮：Pipeline 架构与扫描器设计

初始实现阶段，确定了 8 阶段流水线架构：读取 diff → 解析 diff → 过滤器链 → 扫描代码 → 沙箱执行 → 去重脱敏 → 报告生成 → 数据库存储。

关键设计决策：
- 使用 pattern-matching 而非 LLM 做扫描，零 API 成本，确定性输出
- 6 个独立扫描器各司其职：security、async_error、resource_leak、db_lifecycle、missing_tests、secret_info
- 过滤器链在沙箱执行前拦截危险内容，防止恶意代码在沙箱中运行
- SQLite 存储完整审计追踪：任务、发现、沙箱运行、过滤器日志
- 8 个 test fixtures 覆盖所有扫描类别

测试方面，创建了 9 个模块化测试文件（116 tests），覆盖每个流水线阶段。

## 第 2 轮：Bug 修复与功能增强

审查代码发现并修复了以下问题：

1. **report.py 字段混淆**：`build_recommendations` 将 `secret_info`（category）当作 severity 使用 → 改为按 category 统计
2. **sandbox 路径解析**：脚本路径相对于 `run_review.py` 而非仓库根目录 → 修复为自动检测仓库根目录
3. **SKILL.md 引用缺失**：引用了不存在的 `rules/` 目录和 `docs/OUTPUT_SCHEMA.md` → 创建对应文件
4. **run_checks.py 是空壳**：只打印文件统计 → 改为实际运行扫描器的可执行脚本
5. **agent.py 硬编码模型**：`model="fake"` 硬编码 → 改为 `CR_AGENT_MODEL` 环境变量 + 配置驱动

功能增强方面，参考社区最佳实践并加以改进：

6. **三级置信度系统**：high (≥0.8)、warning (≥0.55)、needs_human_review (<0.55)，替代单一阈值
7. **Policy-as-Code 过滤器**：`filter_policy.json` 外部化过滤器规则，支持网络控制、命令阻止
8. **Schema 版本化**：`COLUMN_MIGRATIONS` 字典支持增量列迁移，兼容旧数据库
9. **FakeSandboxRunner**：通过 diff 文本中的触发字符串模拟超时/失败/密钥泄露等边缘场景
10. **AST 污点分析**：Python AST 分析 + JS/TS 正则回退，追踪用户输入到危险 sink
11. **新增 4 个扫描器**：bare_except、mutable_defaults、assert_control_flow、hardcoded_paths
12. **Fixture 评估框架**：precision/recall/F1 计算，支持 cross-validation
13. **SARIF 输出**：兼容 GitHub Code Scanning 和 Azure DevOps
14. **Policy-as-Code 过滤器** + 过滤器策略 JSON 文件

## 第 3 轮：大规模测试扩容

从 116 tests → 205 tests（+77%），15 个测试文件（+6 个新增）：

新增测试文件：
- `test_agent.py`（8 tests）：agent 模块测试，使用 mock 避免依赖真实模型
- `test_ast_analyzer.py`（18 tests）：Python AST、JS/TS 分析、语言检测
- `test_cli.py`（12 tests）：CLI 参数、输出文件、verbose 模式、全 8 个 fixture 端到端
- `test_edge_cases.py`（22 tests）：空输入、Unicode/emoji/日/韩、超长文件名、损坏数据
- `test_performance.py`（8 tests）：diff 解析缩放性、去重性能、报告生成速度
- `test_fixture_evaluation.py`（10 tests）：指纹匹配、FixtureResult、交叉验证

测试覆盖维度：
1. 单元测试：每个 pipeline 模块独立测试
2. 集成测试：完整 8 阶段流水线端到端
3. 边界测试：空 diff、单行变更、Unicode 多语言、超大输入
4. 回归测试：现有 116 tests 全部通过
5. 性能测试：20/50 文件解析、100/1000 findings 去重、全 fixture < 2min
6. 跨语言：中文、日文、韩文、emoji

## 第 4 轮：文档与最终验证

补充了完整的设计文档和开发记录：
- **DESIGN.md**：架构图、模块映射、6 个关键设计决策、失效模式与缓解、数据流、可扩展性指南
- **README.md** 增强：中英双语、CLI 参数表、快速复现步骤
- **SKILL.md** 增强：输出格式规范、完整的规则目录和过滤器策略

数据安全检查通过，无竞争分析或敏感内容。所有 205 tests 通过。
