# fixtures/llm_fixtures.py - LLM 裁决预录制数据（dry_run / 降级模式）
"""
预录制的 LLM 裁决，用于：
1. dry_run 模式：避免真调 LLM API
2. 无 API Key 降级：保证链路完整性
3. 测试环境：提供确定性的裁决结果

格式：{finding_key: verdict_dict}
finding_key = f"{rule_id}:{file}:{line}"
verdict_dict = {"verdict": "true_positive|false_positive", "reason": "..."}
"""

# 预录制的 LLM 裁决（模拟 LLM 对各种规则 findings 的判断）
recorded_verdicts = {
    # SECRET001 - API 密钥泄露（真阳性）
    "SECRET001:test.py:10": {
        "verdict": "true_positive",
        "reason": "明确的 API 密钥硬编码在代码中，属于真实安全风险"
    },

    # STYLE001 - 缺少文档字符串（误报）
    "STYLE001:test.py:20": {
        "verdict": "false_positive",
        "reason": "简单工具函数无需强制文档字符串，属于代码风格偏好而非真实问题"
    },

    # SECRET002 - 硬编码密码（真阳性）
    "SECRET002:auth.py:5": {
        "verdict": "true_positive",
        "reason": "硬编码的管理员密码存在严重安全风险"
    },

    # STYLE002 - 行过长（误报）
    "STYLE002:util.py:15": {
        "verdict": "false_positive",
        "reason": "代码行长度超过 80 字符属于风格问题，不影响功能正确性"
    },

    # SECRET003 - Token 泄露（真阳性）
    "SECRET003:config.py:8": {
        "verdict": "true_positive",
        "reason": "认证 Token 硬编码在配置文件中，存在泄露风险"
    },

    # INJECT001 - SQL 注入（真阳性）
    "INJECT001:secure.py:3": {
        "verdict": "true_positive",
        "reason": "字符串拼接构造 SQL 查询存在明显的 SQL 注入漏洞"
    },

    # PERF001 - 低效循环（边界情况）
    "PERF001:loop.py:12": {
        "verdict": "true_positive",
        "reason": "使用 range(len()) 进行迭代不够 Pythonic，建议使用 enumerate()"
    },

    # AST001 - 未导入的模块（真阳性）
    "AST001:import.py:2": {
        "verdict": "true_positive",
        "reason": "使用了未导入的模块，会导致运行时 ImportError"
    },

    # RULE001 - 复杂度过高（误报）
    "RULE001:complex.py:50": {
        "verdict": "false_positive",
        "reason": "虽然圈复杂度较高，但业务逻辑清晰，属于可接受的复杂度"
    },

    # SANDBOX001 - 危险系统调用（真阳性）
    "SANDBOX001:system.py:7": {
        "verdict": "true_positive",
        "reason": "直接执行用户输入的 shell 命令存在命令注入风险"
    },
}


def get_verdict(rule_id: str, file: str, line: int) -> dict | None:
    """获取预录制的裁决

    Args:
        rule_id: 规则 ID
        file: 文件路径
        line: 行号

    Returns:
        裁决字典 {"verdict": "...", "reason": "..."}，如果不存在返回 None
    """
    key = f"{rule_id}:{file}:{line}"
    return recorded_verdicts.get(key)


def get_all_verdicts() -> dict:
    """获取所有预录制裁决"""
    return recorded_verdicts.copy()


# 预制的低置信补召回示例（LLM 可以发现规则引擎漏掉的问题）
# 用于测试补召回功能
supplementary_findings = [
    {
        "rule_id": "LLM001",
        "file": "auth.py",
        "line": 15,
        "verdict": "true_positive",
        "reason": "LLM 补召回：虽然未触发规则，但存在认证绕过风险",
        "title": "Authentication bypass risk",
        "evidence": "if user.is_admin or user.token == 'special':",
        "category": "security",
        "severity": "high",
        "confidence": 0.6,  # 适中置信度，路由到 warnings
    },
    {
        "rule_id": "LLM002",
        "file": "payment.py",
        "line": 23,
        "verdict": "true_positive",
        "reason": "LLM 补召回：存在浮点数精度问题",
        "title": "Floating point precision issue",
        "evidence": "price = 0.1 + 0.2  # 结果为 0.30000000000000004",
        "category": "bug",
        "severity": "medium",
        "confidence": 0.6,
    },
    {
        "rule_id": "LLM003",
        "file": "data.py",
        "line": 8,
        "verdict": "true_positive",
        "reason": "LLM 补召回：存在资源泄漏风险",
        "title": "Resource leak risk",
        "evidence": "file = open('data.txt')  # 未关闭文件",
        "category": "bug",
        "severity": "medium",
        "confidence": 0.6,
    },
]
