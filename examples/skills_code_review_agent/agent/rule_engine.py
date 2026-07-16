# agent/rule_engine.py - 规则引擎正则层
import re
from agent.models import Finding, Severity, DiffFile
from agent.redaction import SECRET_KV_KEYS  # 单一真相源：确保检/脱同步

# 规则定义：(rule_id, category, pattern, severity, confidence, needs_literal_dynamic_distinction)
# SECRET001 使用 SECRET_KV_KEYS 构造正则，确保与 redaction.py 检/脱同步
RULES = [
    ("SEC001", "security", r"os\.system\s*\(", Severity.HIGH, 0.9, True),
    # SEC002: 扩展多行shell注入检测
    ("SEC002", "security", r"subprocess.*shell\s*=\s*True", Severity.HIGH, 0.92, False),
    ("SEC002", "security",
     r"subprocess\.(run|call|Popen)\s*\([^)]*\[\s*\"[^\"]*\",\s*[^\"]*\"[^\"]*\",\s*[^)]*shell\s*=\s*True",
     Severity.HIGH, 0.88, False),
    ("SEC002", "security", r"subprocess\.(run|call|Popen)\s*\(\s*[^)]*\+\s*[^)]*,\s*[^)]*shell\s*=\s*True",
     Severity.HIGH, 0.85, False),
    ("SEC003", "security", r"\b(eval|exec)\s*\(", Severity.HIGH, 0.88, False),
    ("SEC004", "security", r"pickle\.loads\s*\(", Severity.HIGH, 0.9, False),
    # SEC005: SQL注入检测 - 字符串拼接构造SQL查询
    ("SEC005", "security", r"(\.execute|\.executemany)\s*\(\s*f[\"']", Severity.HIGH, 0.85, False),
    ("SEC005", "security", r"(\.execute|\.executemany)\s*\(\s*[^)]*\%[^\)]*\)", Severity.HIGH, 0.85, False),
    ("SEC005", "security", r"(\.execute|\.executemany)\s*\(\s*[^)]*\.format\s*\(", Severity.HIGH, 0.85, False),
    ("SEC005", "security", r"(\.execute|\.executemany)\s*\(\s*[^)]*\+\s*[^)]*\)", Severity.HIGH, 0.8, False),
    # SEC006: 路径遍历检测 - 用户输入直接用于文件路径构造
    ("SEC006", "security", r"open\s*\(\s*f[\"'][^\"']*\{[^}]*\}[\"']", Severity.HIGH, 0.82, False),
    ("SEC006", "security", r"open\s*\(\s*[^)]*\%[^\)]*\)", Severity.HIGH, 0.82, False),
    ("SEC006", "security", r"open\s*\(\s*os\.path\.join\s*\([^)]*,\s*[^)]*\)", Severity.HIGH, 0.8, False),
    # SEC007: LDAP注入检测 - 关键词 + f-string 用户输入
    ("SEC007", "security", r"search.*filter\s*=\s*f[\"']", Severity.HIGH, 0.82, False),
    ("SEC007", "security", r"ldap.*filter\s*=\s*f[\"']", Severity.HIGH, 0.80, False),
    # SEC008: SSRF检测 - requests.get/httpx.get/urlopen + 函数调用模式
    ("SEC008", "security", r"requests\.(get|post)\s*\(\s*[^)]*url[^)]*\)", Severity.HIGH, 0.75, False),
    ("SEC008", "security", r"httpx\.(get|post)\s*\(\s*[^)]*url[^)]*\)", Severity.HIGH, 0.75, False),
    ("SEC008", "security", r"urlopen\s*\(\s*[^)]*url[^)]*\)", Severity.HIGH, 0.75, False),
    # SEC009: XSS检测 - render/Markup/innerHTML + tainted
    ("SEC009", "security", r"render\s*\([^)]*\{[^}]*\}", Severity.MEDIUM, 0.75, False),
    ("SEC009", "security", r"Markup\s*\([^)]*\{[^}]*\}", Severity.MEDIUM, 0.75, False),
    ("SEC009", "security", r"innerHTML\s*=\s*[^;]*\{[^}]*\}", Severity.MEDIUM, 0.7, False),
    # SEC010: 开放重定向检测 - redirect + tainted
    ("SEC010", "security", r"redirect\s*\([^)]*\{[^}]*\}", Severity.MEDIUM, 0.75, False),
    ("SEC010", "security", r"redirect\s*\([^)]*\+[^\)]*\)", Severity.MEDIUM, 0.72, False),
    ("ASYNC001", "async_error", r"asyncio\.create_task\s*\(", Severity.MEDIUM, 0.75, False),
    ("RES001", "resource_leak", r"(?<!with\s)(?<!with\.)\bopen\s*\(", Severity.MEDIUM, 0.7, False),
    ("DB001", "db_lifecycle", r"(sqlite3|psycopg2?|pymysql)\.connect\s*\(", Severity.HIGH, 0.85, False),
    # 使用 SECRET_KV_KEYS 动态构造正则，确保与 redaction.py 检/脱同步（扩展属性赋值模式）
    ("SECRET001", "sensitive_information", r"(\.|self\.)?(" + "|".join(SECRET_KV_KEYS) + r")\s*=\s*[\"'][^\"']+[\"']",
     Severity.HIGH, 0.95, False),
    # SECRET001 扩展：JSON 字典内部的敏感键值对（issue #92）
    ("SECRET001", "sensitive_information", r"(" + "|".join(SECRET_KV_KEYS) + r")['\"]\s*:\s*[\"'][^\"']{4,}[\"']",
     Severity.HIGH, 0.95, False),
]

# 字面量参数检测：os.system("ls") 不报，os.system(user_input) 报
LITERAL_ARG = re.compile(r"\(['\"][^'\"]*['\"]\)")


def _has_close_signal(hunk_context: list[str], sink: str) -> bool:
    """保守抑制：仅识别 with 语句明确管理的资源（避免过度抑制导致漏报）。

    核心原则：漏报比误报致命。只抑制明确用 with 语句管理的情况，
    其他情况（如分行 close、无关 close）一律不抑制，允许误报交给后续层降噪。

    Args:
        hunk_context: hunk 的 context_after 列表
        sink: 要检测的函数名（如 "open", "connect"）

    Returns:
        如果检测到 with 语句管理资源返回 True，否则返回 False
    """
    for c in hunk_context:
        # 只识别 with 语句明确管理的资源
        if sink == "open" and re.search(r"\bwith\b.*\bopen\s*\(", c):
            return True
        if sink == "connect" and re.search(r"\bwith\b.*\bconnect\s*\(", c):
            return True
    return False


def review_rules(files: list[DiffFile]) -> list[Finding]:
    """规则引擎主函数：对 diff 的 added 行跑 6 类规则

    Args:
        files: DiffFile 对象列表

    Returns:
        Finding 对象列表
    """
    findings = []

    # 检查是否有测试文件变更
    test_files = {f.path for f in files if "test" in f.path.lower()}

    # 检查是否有生产代码变更（非测试文件且是 .py 文件且有新增行）
    prod_changed = any(f.path.endswith(".py") and f.path not in test_files and f.added_lines for f in files)

    # 遍历所有文件
    for f in files:
        # 遍历文件的所有 hunk
        for hunk in f.hunks:
            # 遍历 hunk 的所有新增行
            for line in hunk.added:
                # 遍历所有规则
                for rule_id, cat, pat, sev, conf, lit in RULES:
                    # 用正则匹配新增行内容
                    if not re.search(pat, line.content):
                        continue

                    # 如果规则需要区分字面量参数，检查是否为字面量
                    if lit and LITERAL_ARG.search(line.content):
                        continue  # 字面量参数跳过降误报

                    # 对于资源泄漏和数据库生命周期规则，检查是否有 close 信号
                    if cat in ("resource_leak", "db_lifecycle"):
                        sink = "open" if cat == "resource_leak" else "connect"
                        if _has_close_signal(hunk.context_after, sink):
                            continue  # 有 close 信号跳过

                    # 修SECRET001 FP：避免在数据库连接上下文中误报
                    if rule_id == "SECRET001" and re.search(r'(connect|password)\s*=', line.content):
                        # 如果是在数据库连接函数调用中的password参数，跳过
                        if re.search(r'(sqlite3|psycopg2|pymysql|\.connect)\([^)]*password\s*=', line.content):
                            continue

                    # 构造 Finding 对象（按 models.py 的实际构造签名）
                    finding = Finding(severity=sev,
                                      category=cat,
                                      file=f.path,
                                      line=line.new_line,
                                      title=f"{rule_id} 触发",
                                      evidence=line.content,
                                      recommendation="见 references 修复指引",
                                      confidence=conf,
                                      source="rule",
                                      rule_id=rule_id)
                    findings.append(finding)

    # missing_tests：生产代码改了但无测试文件
    if prod_changed and not test_files:
        # 获取第一个生产文件路径作为报告位置
        prod_files = [f.path for f in files if f.path.endswith(".py") and f.path not in test_files]
        if prod_files:
            finding = Finding(severity=Severity.LOW,
                              category="missing_tests",
                              file=prod_files[0],
                              line=None,
                              title="生产代码变更缺少测试",
                              evidence="无 test_ 文件改动",
                              recommendation="补充对应测试",
                              confidence=0.65,
                              source="rule",
                              rule_id="TEST001")
            findings.append(finding)

    return findings
