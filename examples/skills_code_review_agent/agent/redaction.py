# agent/redaction.py - 脱敏引擎（检/脱共享同一正则集 + Shannon 熵兜底）
import re
import math
import collections

# 单一真相源：敏感键值对的所有键名（与 rule_engine SECRET001 共享）
# 检/脱同步：任何修改必须同步到 rule_engine.py
# 注意：database_url、db_url 等 URL 类键名不在此列表中，因为它们会被 URL 脱敏规则处理
SECRET_KV_KEYS = [
    "api_key",
    "password",
    "secret",
    "token",
    "access_key",
    "secret_key",
    "private_key",
    "auth_key",
    "db_password",  # issue #92: 补充 db_password 检测（非 URL 类）
    # database_url 和 db_url 被 URL 脱敏规则处理，不在此列表中
]

# 检/脱共享正则集：与 rule_engine SECRET001 同步，扩展覆盖 15+ 常见密钥模式
SECRET_PATTERNS = [
    # Stripe 密钥
    (re.compile(r"sk-[A-Za-z0-9]{20,}"), "[REDACTED_SK]"),

    # GitHub 个人访问令牌
    (re.compile(r"ghp_[A-Za-z0-9]{36}"), "[REDACTED_GHP]"),

    # GitHub OAuth 令牌
    (re.compile(r"gho_[A-Za-z0-9]{36}"), "[REDACTED_GHO]"),

    # GitHub 应用令牌
    (re.compile(r"(ghu|ghs|ghr)_[A-Za-z0-9]{36}"), "[REDACTED_GITHUB]"),

    # AWS 访问密钥 ID
    (re.compile(r"AKIA[0-9A-Z]{16}"), "[REDACTED_AKIA]"),

    # AWS 秘密访问密钥
    (re.compile(r"AWS[0-9A-Z]{20,}"), "[REDACTED_AWS]"),

    # JWT Token
    (re.compile(r"eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"), "[REDACTED_JWT]"),

    # PEM 私钥块（支持多种密钥类型）
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END[^-]*-----", re.S), "[REDACTED_KEY]"),

    # Slack 令牌
    (re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"), "[REDACTED_SLACK]"),

    # Google API 密钥
    (re.compile(r"AIza[A-Za-z0-9_-]{35}"), "[REDACTED_GOOGLE]"),

    # Google OAuth 访问令牌
    (re.compile(r"ya29\.[A-Za-z0-9_-]{100,}"), "[REDACTED_GOOGLE_OAUTH]"),

    # Stripe 可发布密钥
    (re.compile(r"pk_[A-Za-z0-9]{20,}"), "[REDACTED_PK]"),

    # Stripe Live 密钥
    (re.compile(r"sk_live_[A-Za-z0-9]{20,}"), "[REDACTED_SK_LIVE]"),

    # Twilio API 密钥
    (re.compile(r"AC[a-z0-9]{32}"), "[REDACTED_TWILIO]"),

    # SendGrid API 密钥
    (re.compile(r"SG\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}"), "[REDACTED_SENDGRID]"),

    # Mailgun API 密钥
    (re.compile(r"key-[0-9a-zA-Z]{32}"), "[REDACTED_MAILGUN]"),

    # 基础认证 URL（用户名:密码@）
    # 收紧正则：userinfo 部分不允许再出现 @ 或 /，避免误匹配边界情况（如 mongodb://user@host:pass@）
    (re.compile(r"://[^:@/\s]+:[^@/\s]+@"), "[REDACTED_URLAUTH]"),

    # 数据库连接字符串中的密钥
    (re.compile(r"(mongodb|mysql|postgresql|redis)://[^:@\s]+:[^@/\s]+@", re.I), "[REDACTED_DB_URL]"),

    # 敏感键值对（api_key/password/secret/token 等）- 只匹配值不以已知密钥前缀开头的情况
    # 使用负向前瞻避免重复匹配已脱敏的占位符
    # 动态构造正则，使用 SECRET_KV_KEYS 确保检/脱同步
    (re.compile(
        r"(" + "|".join(SECRET_KV_KEYS) + r")"
        r"\s*=\s*['\"]"
        r"(?!sk-|ghp_|gho_|AKIA|eyJ|ghu_|ghs_|ghr_|AWS|ya29|pk_|sg_|xox|key-|\[)"
        r"[^'\"\s]{4,}['\"]", re.I), "[REDACTED_KV]"),

    # JSON 字典内部的敏感键值对：{"password": "xxx"} 或 {"api_key": "xxx"}
    # 扩展覆盖 JSON 键值对模式（issue #92: 检测 JSON 字典中的硬编码密钥）
    (re.compile(
        r"(" + "|".join(SECRET_KV_KEYS) + r")"
        r"['\"]\s*:\s*['\"]"
        r"(?!sk-|ghp_|gho_|AKIA|eyJ|ghu_|ghs_|ghr_|AWS|ya29|pk_|sg_|xox|key-|\[)"
        r"[^'\"\s]{4,}['\"]", re.I), "[REDACTED_JSON_KV]"),
]


def _shannon_entropy(s: str) -> float:
    """计算字符串的 Shannon 熵（用于高熵字面量兜底检测）

    Args:
        s: 待计算熵值的字符串

    Returns:
        熵值（0.0-8.0，对于文本一般 <4.2）
    """
    if len(s) < 28:
        return 0.0

    cnt = collections.Counter(s)
    n = len(s)

    # 计算 Shannon 熵
    return -sum((c / n) * math.log2(c / n) for c in cnt.values())


def redact_text(text: str) -> tuple[str, int]:
    """对文本进行脱敏处理

    Args:
        text: 待脱敏的文本

    Returns:
        (脱敏后文本, 命中的密钥数量)
    """
    # 重复脱敏保护：避免对已脱敏内容重复处理
    if "[REDACTED_" in text:
        return text, 0

    count = 0

    # 应用所有正则模式进行脱敏
    for pattern, replacement in SECRET_PATTERNS:
        text, n = pattern.subn(replacement, text)
        count += n

    # 高熵字面量兜底检测（捕获正则可能遗漏的密钥）
    for match in re.finditer(r"['\"]([A-Za-z0-9+/=]{28,})['\"]", text):
        literal = match.group(1)

        # 字母表验证：确保字符多样性（密钥通常使用多种字符）
        if len(set(literal)) < 12:
            continue

        # 计算熵值并检查阈值（>4.2 表明高度随机性）
        if _shannon_entropy(literal) > 4.2:
            # 排除已知的非密钥前缀（谨慎排除 Base64 图片/配置误报，但不过度排除以免漏报真密钥）
            if not literal.startswith(("http", "pytest", "example", "test", "demo", "data:", "/9j/", "iVBOR",
                                       "R0lGO")):  # /9j/=JPEG, iVBOR=PNG, R0lGO=GIF
                text = text.replace(literal, "[REDACTED_ENTROPY]")
                count += 1

    return text, count


def redact_finding(finding) -> object:
    """对 Finding 对象的敏感字段进行脱敏

    Args:
        finding: Finding 对象（来自 agent.models）

    Returns:
        脱敏后的 Finding 对象（同实例，修改字段）
    """
    # 脱敏 evidence 字段
    finding.evidence, n1 = redact_text(finding.evidence)

    # 脱敏 title 字段
    finding.title, n2 = redact_text(finding.title)

    # 脱敏 recommendation 字段
    finding.recommendation, n3 = redact_text(finding.recommendation)

    return finding


def contains_unredacted_secret(text: str, secrets: list[str]) -> bool:
    """检查文本中是否包含未脱敏的密钥

    Args:
        text: 待检查的文本
        secrets: 密钥列表

    Returns:
        如果包含任何未脱敏的密钥返回 True，否则返回 False
    """
    return any(secret in text for secret in secrets)
