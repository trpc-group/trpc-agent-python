# dedup.py —— 四元组去重(保留最高置信) + 三桶路由
import hashlib
from agent.models import Finding, Bucket


def _key(f: Finding) -> tuple:
    """生成四元组键用于去重：(file, line, category, rule_id)"""
    return (f.file, f.line, f.category, f.rule_id)


def _fid(f: Finding) -> str:
    """生成 finding_id = sha256[:16]"""
    content = f"{f.file}|{f.line}|{f.category}|{f.rule_id}|{f.title}"
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def dedup_and_route(findings: list[Finding]) -> tuple[list[Finding], list[Finding], list[Finding]]:
    """
    四元组去重 + 三桶路由

    Args:
        findings: 原始 findings 列表

    Returns:
        (findings, warnings, needs_review) 三桶
        - findings: confidence >= 0.8
        - warnings: 0.55 <= confidence < 0.8
        - needs_review: confidence < 0.55
    """
    # 去重：同四元组保留最高置信度
    best = {}
    for f in findings:
        k = _key(f)
        if k not in best or f.confidence > best[k].confidence:
            best[k] = f

    # 路由到三桶
    routed = [[], [], []]  # [findings, warnings, needs_review]
    for f in best.values():
        f.finding_id = _fid(f)

        if f.confidence >= 0.8:
            f.bucket = Bucket.FINDINGS
            routed[0].append(f)
        elif f.confidence >= 0.55:
            f.bucket = Bucket.WARNINGS
            routed[1].append(f)
        else:
            f.bucket = Bucket.NEEDS_REVIEW
            routed[2].append(f)

    return routed[0], routed[1], routed[2]
