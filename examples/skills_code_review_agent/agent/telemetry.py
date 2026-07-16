# telemetry.py — 监控指标聚合
from collections import Counter

from agent.models import Finding, MonitoringSummary


def build_monitoring(
    total_duration_ms: int,
    sandbox_duration_ms: int,
    tool_call_count: int,
    blocked_count: int,
    findings: list[Finding],
    exceptions: list[dict],
) -> MonitoringSummary:
    """
    构建监控摘要，聚合 7 项指标。

    Args:
        total_duration_ms: 总运行时长（毫秒）
        sandbox_duration_ms: 沙箱运行总时长（毫秒）
        tool_call_count: 工具调用次数
        blocked_count: 被拦截的操作次数
        findings: 所有发现列表（findings + warnings + needs_review）
        exceptions: 异常列表，每项包含 exception_type 和 message

    Returns:
        MonitoringSummary: 包含 7 项监控指标的摘要
    """
    # 1. 总时长
    total_time = total_duration_ms

    # 2. 沙箱时长
    sandbox_time = sandbox_duration_ms

    # 3. 工具调用次数
    tools = tool_call_count

    # 4. 拦截次数
    blocked = blocked_count

    # 5. Finding 总数
    finding_count = len(findings)

    # 6. 严重级别分布
    severity_counts = Counter(f.severity.value for f in findings)
    severity_distribution = {
        "critical": severity_counts.get("critical", 0),
        "high": severity_counts.get("high", 0),
        "medium": severity_counts.get("medium", 0),
        "low": severity_counts.get("low", 0),
    }

    # 7. 异常类型分布
    exception_counts = Counter(e.get("exception_type", "Unknown") for e in exceptions)
    exception_distribution = dict(exception_counts)

    return MonitoringSummary(
        total_duration_ms=total_time,
        sandbox_duration_ms=sandbox_time,
        tool_call_count=tools,
        blocked_count=blocked,
        finding_count=finding_count,
        severity_distribution=severity_distribution,
        exception_distribution=exception_distribution,
    )
