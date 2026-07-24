# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Core code review pipeline.

Provides the `run_review()` function that orchestrates the full
review process: diff parsing → filter governance → sandbox execution
→ deduplication → report generation → database storage.

This is the central entry point for both CLI and server modes.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from config import ReviewAgentConfig
from storage.models import (
    FilterAction,
    FilterLog,
    FilterType,
    Finding,
    FindingCategory,
    FindingConfidence,
    FindingSeverity,
    FindingSource,
    MonitorSummary,
    ReportType,
    ReviewReport,
    ReviewResult,
    ReviewTask,
    SandboxRun,
    SandboxStatus,
    TaskStatus,
)
from storage.sqlite_repository import SqliteCrRepository


# ── Diff Parsing ──

def parse_diff(diff_content: str) -> dict[str, Any]:
    """Parse a unified diff into structured change information.

    Args:
        diff_content: Raw unified diff text.

    Returns:
        Dict with keys: files (list of file changes), total_additions,
        total_deletions, files_changed.
    """
    files: list[dict[str, Any]] = []
    current_file: dict[str, Any] | None = None
    total_additions = 0
    total_deletions = 0

    for line in diff_content.splitlines():
        # Detect file header: --- a/path  or  +++ b/path
        if line.startswith("--- a/"):
            continue
        if line.startswith("+++ b/"):
            if current_file:
                files.append(current_file)
            current_file = {
                "path": line[6:],
                "change_type": "modified",
                "additions": 0,
                "deletions": 0,
                "hunks": [],
            }
            continue

        # Detect hunk header: @@ -a,b +c,d @@
        hunk_match = re.match(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)", line)
        if hunk_match and current_file is not None:
            hunk = {
                "start_line": int(hunk_match.group(2)),
                "end_line": int(hunk_match.group(2)),
                "content": line,
                "added_lines": [],
                "deleted_lines": [],
            }
            current_file["hunks"].append(hunk)
            continue

        # Count additions/deletions
        if line.startswith("+") and not line.startswith("+++"):
            total_additions += 1
            if current_file and current_file["hunks"]:
                hunk = current_file["hunks"][-1]
                hunk["added_lines"].append(hunk["start_line"] + len(hunk["added_lines"]))
                current_file["additions"] += 1
        elif line.startswith("-") and not line.startswith("---"):
            total_deletions += 1
            if current_file and current_file["hunks"]:
                current_file["deletions"] += 1

    if current_file:
        files.append(current_file)

    return {
        "files": files,
        "total_additions": total_additions,
        "total_deletions": total_deletions,
        "files_changed": len(files),
    }


# ── Pattern-based Finding Detection ──

# Security patterns
SECURITY_PATTERNS: list[dict[str, Any]] = [
    {
        "category": FindingCategory.SECURITY,
        "severity": FindingSeverity.CRITICAL,
        "pattern": re.compile(r"cursor\.execute\(f\s*['\"]"),
        "title": "SQL注入风险",
        "evidence_template": "使用了 f-string 拼接 SQL 查询: {match}",
        "recommendation": "使用参数化查询: cursor.execute('SELECT ...', (param,))",
        "confidence": FindingConfidence.HIGH,
    },
    {
        "category": FindingCategory.SECURITY,
        "severity": FindingSeverity.CRITICAL,
        "pattern": re.compile(r"os\.system\(f\s*['\"]"),
        "title": "命令注入风险",
        "evidence_template": "使用了 f-string 拼接系统命令: {match}",
        "recommendation": "使用 subprocess.run() 并传递列表参数",
        "confidence": FindingConfidence.HIGH,
    },
    {
        "category": FindingCategory.SECURITY,
        "severity": FindingSeverity.CRITICAL,
        "pattern": re.compile(r"subprocess\.(?:call|Popen|run)\(.*shell=True"),
        "title": "Shell注入风险",
        "evidence_template": "subprocess 调用启用了 shell=True: {match}",
        "recommendation": "禁用 shell=True 并传递列表参数",
        "confidence": FindingConfidence.HIGH,
    },
    {
        "category": FindingCategory.SECURITY,
        "severity": FindingSeverity.WARNING,
        "pattern": re.compile(r"eval\(|exec\(|__import__\("),
        "title": "动态代码执行风险",
        "evidence_template": "使用了动态代码执行: {match}",
        "recommendation": "避免使用 eval/exec, 使用安全的替代方案",
        "confidence": FindingConfidence.MEDIUM,
    },
]

# Secret detection patterns
SECRET_PATTERNS: list[dict[str, Any]] = [
    {
        "category": FindingCategory.SECRET,
        "severity": FindingSeverity.CRITICAL,
        "pattern": re.compile(r"""(?i)(?:api_key|api[_-]?key|apikey)\s*[=:]\s*['\"](sk-[a-zA-Z0-9]{10,})['\"]"""),
        "title": "API Key 硬编码",
        "evidence_template": "检测到 API Key 硬编码: {match}",
        "recommendation": "使用环境变量或密钥管理服务存储敏感信息",
        "confidence": FindingConfidence.HIGH,
    },
    {
        "category": FindingCategory.SECRET,
        "severity": FindingSeverity.CRITICAL,
        "pattern": re.compile(r"""(?i)(?:password|passwd|pwd)\s*[=:]\s*['\"][^'"]{4,}['\"]"""),
        "title": "密码硬编码",
        "evidence_template": "检测到密码硬编码: {match}",
        "recommendation": "使用环境变量或密钥管理服务存储密码",
        "confidence": FindingConfidence.HIGH,
    },
    {
        "category": FindingCategory.SECRET,
        "severity": FindingSeverity.CRITICAL,
        "pattern": re.compile(r"ghp_[a-zA-Z0-9]{36,}"),
        "title": "GitHub Token 泄露",
        "evidence_template": "检测到 GitHub Personal Access Token: {match}",
        "recommendation": "立即撤销该 Token 并使用环境变量",
        "confidence": FindingConfidence.HIGH,
    },
    {
        "category": FindingCategory.SECRET,
        "severity": FindingSeverity.CRITICAL,
        "pattern": re.compile(r"AKIA[0-9A-Z]{16}"),
        "title": "AWS Access Key 泄露",
        "evidence_template": "检测到 AWS Access Key: {match}",
        "recommendation": "立即撤销该密钥并使用 IAM Role",
        "confidence": FindingConfidence.HIGH,
    },
    {
        "category": FindingCategory.SECRET,
        "severity": FindingSeverity.CRITICAL,
        "pattern": re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----"),
        "title": "私钥硬编码",
        "evidence_template": "检测到私钥硬编码",
        "recommendation": "使用密钥管理服务或环境变量, 不要将私钥提交到代码库",
        "confidence": FindingConfidence.HIGH,
    },
    {
        "category": FindingCategory.SECRET,
        "severity": FindingSeverity.CRITICAL,
        "pattern": re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
        "title": "JWT Token 硬编码",
        "evidence_template": "检测到 JWT Token 硬编码: {match_preview}",
        "recommendation": "使用环境变量存储 JWT Secret",
        "confidence": FindingConfidence.MEDIUM,
    },
    {
        "category": FindingCategory.SECRET,
        "severity": FindingSeverity.CRITICAL,
        "pattern": re.compile(r"postgres(?:ql)?://[^:]+:[^@]+@"),
        "title": "数据库连接字符串包含密码",
        "evidence_template": "检测到数据库连接字符串包含明文密码: {match_preview}",
        "recommendation": "使用环境变量存储数据库密码",
        "confidence": FindingConfidence.HIGH,
    },
]

# Async resource leak patterns
ASYNC_PATTERNS: list[dict[str, Any]] = [
    {
        "category": FindingCategory.RESOURCE_LEAK,
        "severity": FindingSeverity.WARNING,
        "pattern": re.compile(r"session\s*=\s*aiohttp\.ClientSession\(\)(?!.*\basync with\b)"),
        "title": "aiohttp ClientSession 未关闭",
        "evidence_template": "aiohttp ClientSession 未使用 async with 管理: {match}",
        "recommendation": "使用 async with aiohttp.ClientSession() as session: 确保自动关闭",
        "confidence": FindingConfidence.HIGH,
    },
    {
        "category": FindingCategory.ASYNC,
        "severity": FindingSeverity.WARNING,
        "pattern": re.compile(r"time\.sleep\("),
        "title": "阻塞调用在异步代码中",
        "evidence_template": "在异步代码中使用了阻塞的 time.sleep(): {match}",
        "recommendation": "使用 asyncio.sleep() 替代 time.sleep()",
        "confidence": FindingConfidence.MEDIUM,
    },
]

# DB connection patterns
DB_PATTERNS: list[dict[str, Any]] = [
    {
        "category": FindingCategory.DB,
        "severity": FindingSeverity.WARNING,
        "pattern": re.compile(r"sqlite3\.connect\(.*\)(?!.*\.close\(\))"),
        "title": "数据库连接未关闭",
        "evidence_template": "数据库连接未确保关闭: {match}",
        "recommendation": "使用 context manager (with) 管理数据库连接, 或在 finally 块中关闭",
        "confidence": FindingConfidence.MEDIUM,
    },
    {
        "category": FindingCategory.DB,
        "severity": FindingSeverity.CRITICAL,
        "pattern": re.compile(r"cursor\.execute\(f\s*['\"]"),
        "title": "SQL注入风险 (数据库层)",
        "evidence_template": "使用了 f-string 拼接 SQL 查询: {match}",
        "recommendation": "使用参数化查询: cursor.execute('SELECT ...', (param,))",
        "confidence": FindingConfidence.HIGH,
    },
]

# Resource leak patterns
RESOURCE_PATTERNS: list[dict[str, Any]] = [
    {
        "category": FindingCategory.RESOURCE_LEAK,
        "severity": FindingSeverity.WARNING,
        "pattern": re.compile(r"open\([^)]+\)(?!\s*as\s)"),
        "title": "文件句柄未使用 context manager",
        "evidence_template": "文件打开操作未使用 with 语句: {match}",
        "recommendation": "使用 with open(...) as f: 确保文件自动关闭",
        "confidence": FindingConfidence.MEDIUM,
    },
]

ALL_PATTERNS = SECURITY_PATTERNS + SECRET_PATTERNS + ASYNC_PATTERNS + DB_PATTERNS + RESOURCE_PATTERNS


def _match_preview(match: re.Match) -> str:
    """Get a short preview of a regex match for evidence."""
    text = match.group()
    if len(text) > 60:
        return text[:57] + "..."
    return text


def detect_findings_by_pattern(
    diff_content: str,
    task_id: str,
) -> list[Finding]:
    """Run pattern-based detection on diff content.

    Scans the diff content against all predefined patterns and returns
    a list of findings with file/line info extracted from the diff.

    Args:
        diff_content: The raw diff text.
        task_id: The review task ID to associate findings with.

    Returns:
        List of Finding objects.
    """
    findings: list[Finding] = []
    seen_dedup: set[str] = set()

    # First, parse the diff to get file paths and line numbers
    parsed = parse_diff(diff_content)
    file_map: dict[str, dict[str, Any]] = {}
    for f in parsed["files"]:
        file_map[f["path"]] = f

    # Scan line by line
    lines = diff_content.splitlines()
    current_file = ""
    current_line = 0

    for lineno, line in enumerate(lines, 1):
        # Track current file from +++ header
        if line.startswith("+++ b/"):
            current_file = line[6:]
            current_line = 0
            continue
        # Track line numbers from hunk headers
        hunk_match = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
        if hunk_match:
            current_line = int(hunk_match.group(1))
            continue

        # Only scan added lines (+)
        if not line.startswith("+"):
            if line.startswith("-"):
                # Deleted lines don't advance the counter
                continue
            # Context lines
            if current_line > 0:
                current_line += 1
            continue

        added_content = line[1:]  # Strip the leading '+'
        current_line_val = current_line

        for pattern_def in ALL_PATTERNS:
            match = pattern_def["pattern"].search(added_content)
            if not match:
                continue

            dedup_key = f"{current_file}:{current_line_val}:{pattern_def['category'].value}"
            if dedup_key in seen_dedup:
                continue
            seen_dedup.add(dedup_key)

            evidence = pattern_def["evidence_template"].format(
                match=_match_preview(match),
                match_preview=_match_preview(match),
            )

            finding = Finding(
                task_id=task_id,
                severity=pattern_def["severity"],
                category=pattern_def["category"],
                file_path=current_file,
                line_number=current_line_val,
                title=pattern_def["title"],
                evidence=evidence,
                recommendation=pattern_def["recommendation"],
                confidence=pattern_def["confidence"],
                source=FindingSource.PATTERN_MATCH,
                dedup_key=dedup_key,
            )
            findings.append(finding)

        if current_line > 0:
            current_line += 1

    return findings


# ── Secret Masking ──

SECRET_MASK_PATTERNS: list[re.Pattern] = [
    re.compile(r"(?i)(api_key|api[_-]?key|apikey|password|passwd|pwd|secret)\s*[=:]\s*['\"][^'\"]+['\"]"),
    re.compile(r"ghp_[a-zA-Z0-9]{36,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----.*?-----END (?:RSA |EC )?PRIVATE KEY-----"),
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    re.compile(r"(postgres(?:ql)?|mysql|redis)://[^:]+:[^@]+@"),
]


def mask_secrets(text: str) -> str:
    """Mask sensitive information in text.

    Replaces API keys, passwords, tokens, and private keys with '***'.
    Used before writing reports and database records.

    Args:
        text: The text to mask.

    Returns:
        The masked text with secrets replaced.
    """
    for pattern in SECRET_MASK_PATTERNS:
        text = pattern.sub(lambda m: _mask_match(m), text)
    return text


def _mask_match(match: re.Match) -> str:
    """Replace the matched secret with a masked version."""
    full = match.group()
    # For key=value pairs, keep the key
    if "=" in full or ":" in full:
        sep = "=" if "=" in full else ":"
        key, _ = full.split(sep, 1)
        return f"{key}{sep}'***'"
    # For URLs, keep the protocol and host
    if "://" in full:
        return full.split("@")[0] + "@***"
    # Otherwise, mask the entire match
    return "***"


# ── Finding Classification (Dedup & Noise Reduction) ──

def classify_findings(findings: list[Finding]) -> dict[str, list[Finding]]:
    """Classify findings into high-confidence, warnings, and needs-human-review.

    Applies dedup and noise reduction rules:
    - Low confidence → needs_human_review, severity downgraded to suggestion
    - Medium confidence + suggestion severity → needs_human_review
    - Duplicates → removed
    - Everything else → high-confidence findings

    Args:
        findings: Raw list of findings.

    Returns:
        Dict with keys: "findings" (high-confidence), "warnings", "needs_human_review".
    """
    high_conf: list[Finding] = []
    warnings: list[Finding] = []
    needs_review: list[Finding] = []
    seen_dedup: set[str] = set()

    for finding in findings:
        # Skip duplicates
        if finding.dedup_key:
            if finding.dedup_key in seen_dedup:
                continue
            seen_dedup.add(finding.dedup_key)

        # Apply noise reduction rules
        if finding.confidence == FindingConfidence.LOW:
            finding.needs_human_review = True
            finding.severity = FindingSeverity.SUGGESTION
            needs_review.append(finding)
        elif finding.confidence == FindingConfidence.MEDIUM and finding.severity == FindingSeverity.SUGGESTION:
            finding.needs_human_review = True
            needs_review.append(finding)
        elif finding.severity == FindingSeverity.CRITICAL:
            high_conf.append(finding)
        elif finding.severity == FindingSeverity.WARNING:
            warnings.append(finding)
        else:
            high_conf.append(finding)

    return {
        "findings": high_conf,
        "warnings": warnings,
        "needs_human_review": needs_review,
    }


# ── Filter Governance ──

# Reuse the same blocked patterns from SandboxSecurityFilter
FILTER_BLOCKED_PATTERNS: list[re.Pattern] = [
    re.compile(r"rm\s+-rf\s+/"),           # 删除根目录
    re.compile(r":\(\)\s*\{.*:\(\)\s*\;"),  # Fork 炸弹
    re.compile(r"sudo\s+"),                 # 提权
    re.compile(r"chmod\s+777"),             # 权限滥用
    re.compile(r">\s*/dev/sda"),            # 磁盘写入
    re.compile(r"dd\s+if="),                # dd 命令
    re.compile(r"mkfs\."),                  # 格式化
    re.compile(r"wget\s+.*\|\s*bash"),      # 远程下载执行
    re.compile(r"curl\s+.*\|\s*bash"),      # 远程下载执行
]

FILTER_ALLOWED_PATHS: list[str] = [
    "scripts/",
    "out/",
    "work/",
    "/tmp/",
]


def run_filter_governance(
    script_content: str,
    script_name: str,
    task_id: str,
) -> tuple[list[FilterLog], bool]:
    """Run filter governance on a script before execution.

    Checks script content against high-risk patterns, path safety,
    and environment variable whitelist. Returns filter logs and
    whether the script is allowed to execute.

    Args:
        script_content: The script content to check.
        script_name: The name of the script.
        task_id: The review task ID.

    Returns:
        Tuple of (filter_logs, allowed) where:
        - filter_logs is a list of FilterLog records
        - allowed is True if the script passes all filter checks
    """
    logs: list[FilterLog] = []

    # 1. Check for blocked patterns
    for pattern in FILTER_BLOCKED_PATTERNS:
        if pattern.search(script_content):
            logs.append(FilterLog(
                task_id=task_id,
                filter_type=FilterType.SANDBOX,
                action=FilterAction.DENY,
                target=script_name,
                reason=f"高风险脚本模式被拦截: {pattern.pattern}",
            ))
            return logs, False

    # 2. Check path safety
    if not any(
        script_name.startswith(allowed) for allowed in FILTER_ALLOWED_PATHS
    ):
        logs.append(FilterLog(
            task_id=task_id,
            filter_type=FilterType.SANDBOX,
            action=FilterAction.DENY,
            target=script_name,
            reason=f"脚本路径不在白名单中: {script_name}",
        ))
        return logs, False

    # 3. All checks passed → allow
    logs.append(FilterLog(
        task_id=task_id,
        filter_type=FilterType.SANDBOX,
        action=FilterAction.ALLOW,
        target=script_name,
        reason="安全策略检查通过",
    ))
    return logs, True

def run_sandbox_script(
    script_name: str,
    script_content: str,
    task_id: str,
    timeout: int = 30,
    max_output: int = 1_048_576,
    sandbox_type: str = "local",
) -> SandboxRun:
    """Execute a sandbox script with the configured executor.

    Uses ContainerCodeExecutor when sandbox_type is "container" and Docker
    is available. Falls back to UnsafeLocalCodeExecutor for local development.
    The original subprocess fallback is used when neither is available.

    Args:
        script_name: Name of the script being executed.
        script_content: The script content to execute.
        task_id: The review task ID.
        timeout: Max execution time in seconds.
        max_output: Max output size in bytes.
        sandbox_type: Sandbox executor type ("local", "container", "cube").

    Returns:
        A SandboxRun record with execution results.
    """
    start = time.time()
    run = SandboxRun(
        task_id=task_id,
        script_name=script_name,
        status=SandboxStatus.SUCCESS,
    )

    try:
        # Try SDK-based executors first
        if sandbox_type in ("container", "cube"):
            _result = _run_with_sdk_executor(
                script_content, sandbox_type, timeout, max_output, start, run,
            )
            if _result is not None:
                return _result

        # Fallback: UnsafeLocalCodeExecutor
        _result = _run_with_sdk_executor(
            script_content, "local", timeout, max_output, start, run,
        )
        if _result is not None:
            return _result

        # Last resort: native subprocess
        return _run_with_subprocess(script_content, timeout, max_output, start, run)

    except Exception as e:
        run.duration_ms = (time.time() - start) * 1000
        run.status = SandboxStatus.FAILED
        run.error_message = f"{type(e).__name__}: {str(e)[:500]}"
        return run


def _run_with_sdk_executor(
    script_content: str,
    sandbox_type: str,
    timeout: int,
    max_output: int,
    start: float,
    run: SandboxRun,
) -> Optional[SandboxRun]:
    """Try to execute a script using the SDK's CodeExecutor.

    Returns a SandboxRun on success, or None to indicate fallback needed.
    """
    import asyncio

    try:
        if sandbox_type == "container":
            from trpc_agent_sdk.code_executors import ContainerCodeExecutor
            executor = ContainerCodeExecutor(
                timeout=timeout,
                max_output_size=max_output,
                env_whitelist=["PATH", "HOME", "PYTHONPATH", "WORKSPACE_DIR"],
            )
        elif sandbox_type == "cube":
            from trpc_agent_sdk.code_executors.cube import CubeCodeExecutor
            executor = CubeCodeExecutor(
                timeout=timeout,
                max_output_size=max_output,
            )
        else:
            from trpc_agent_sdk.code_executors import UnsafeLocalCodeExecutor
            executor = UnsafeLocalCodeExecutor(
                timeout=timeout,
                max_output_size=max_output,
            )

        from trpc_agent_sdk.context import new_agent_context
        from trpc_agent_sdk.code_executors import CodeExecutionInput, CodeBlock

        ctx = new_agent_context(timeout=timeout * 1000)
        input_data = CodeExecutionInput(
            code=script_content,
            code_blocks=[CodeBlock(code=script_content, language="python")],
        )

        result = asyncio.run(executor.execute_code(ctx, input_data))
        run.duration_ms = (time.time() - start) * 1000

        output = (result.stdout or "") + (result.stderr or "")
        if len(output) > max_output:
            output = output[:max_output] + "\n... [truncated]"
            run.output_size_bytes = max_output
        else:
            run.output_size_bytes = len(output)

        if result.stderr and result.exit_code != 0:
            run.status = SandboxStatus.FAILED
            run.error_message = result.stderr[:500]
            run.exit_code = result.exit_code
        else:
            run.status = SandboxStatus.SUCCESS
            run.exit_code = 0

        return run

    except Exception:
        # Executor not available or failed → return None to trigger fallback
        return None


def _run_with_subprocess(
    script_content: str,
    timeout: int,
    max_output: int,
    start: float,
    run: SandboxRun,
) -> SandboxRun:
    """Execute a script using native subprocess (last resort fallback)."""
    import subprocess  # noqa: F811

    try:
        result = subprocess.run(
            [sys.executable, "-c", script_content],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        run.duration_ms = (time.time() - start) * 1000
        run.exit_code = result.returncode

        output = (result.stdout or "") + (result.stderr or "")
        if len(output) > max_output:
            output = output[:max_output] + "\n... [truncated]"
            run.output_size_bytes = max_output
        else:
            run.output_size_bytes = len(output)

        if result.returncode != 0:
            run.status = SandboxStatus.FAILED
            run.error_message = result.stderr[:500] if result.stderr else f"Exit code {result.returncode}"

    except subprocess.TimeoutExpired:
        run.duration_ms = (time.time() - start) * 1000
        run.status = SandboxStatus.TIMEOUT
        run.error_message = f"Execution timed out after {timeout}s"
    except Exception as e:
        run.duration_ms = (time.time() - start) * 1000
        run.status = SandboxStatus.FAILED
        run.error_message = f"{type(e).__name__}: {str(e)[:500]}"

    return run


# ── Report Generation ──

def generate_json_report(
    task: ReviewTask,
    findings: list[Finding],
    warnings: list[Finding],
    needs_review: list[Finding],
    sandbox_runs: list[SandboxRun],
    filter_intercepts: list[FilterLog],
    monitor: Optional[MonitorSummary],
) -> str:
    """Generate a JSON format review report.

    Args:
        task: The review task.
        findings: High-confidence findings.
        warnings: Warning-level findings.
        needs_review: Findings needing human review.
        sandbox_runs: Sandbox execution records.
        filter_intercepts: Filter interception records.
        monitor: Monitoring summary.

    Returns:
        JSON string of the full report.
    """
    report: dict[str, Any] = {
        "task_id": task.id,
        "status": task.status.value,
        "input_type": task.input_type,
        "input_summary": json.loads(task.input_summary) if task.input_summary else {},
        "total_duration_ms": task.total_duration_ms,
        "finding_count": task.finding_count,
        "severity_distribution": json.loads(task.severity_distribution) if task.severity_distribution else {},
        "findings": [f.model_dump() for f in findings],
        "warnings": [f.model_dump() for f in warnings],
        "needs_human_review": [f.model_dump() for f in needs_review],
        "sandbox_runs": [s.model_dump() for s in sandbox_runs],
        "filter_intercepts": [i.model_dump() for i in filter_intercepts],
        "monitoring": monitor.model_dump() if monitor else {},
    }
    return json.dumps(report, ensure_ascii=False, indent=2, default=str)


def generate_markdown_report(
    task: ReviewTask,
    findings: list[Finding],
    warnings: list[Finding],
    needs_review: list[Finding],
    sandbox_runs: list[SandboxRun],
    filter_intercepts: list[FilterLog],
    monitor: Optional[MonitorSummary],
) -> str:
    """Generate a Markdown format review report.

    Args:
        task: The review task.
        findings: High-confidence findings.
        warnings: Warning-level findings.
        needs_review: Findings needing human review.
        sandbox_runs: Sandbox execution records.
        filter_intercepts: Filter interception records.
        monitor: Monitoring summary.

    Returns:
        Markdown string of the full report.
    """
    severity_dist = json.loads(task.severity_distribution) if task.severity_distribution else {}
    n_critical = severity_dist.get("critical", 0)
    n_warning = severity_dist.get("warning", 0)
    n_suggestion = severity_dist.get("suggestion", 0)

    lines = [
        f"# 代码审查报告",
        f"",
        f"**任务 ID**: {task.id}",
        f"**状态**: {task.status.value}",
        f"**耗时**: {task.total_duration_ms:.0f}ms",
        f"",
        f"## 摘要",
        f"",
        f"| 指标 | 数量 |",
        f"|------|------|",
        f"| 🚨 Critical | {n_critical} |",
        f"| ⚠️ Warning | {n_warning} |",
        f"| 💡 Suggestion | {n_suggestion} |",
        f"| 待人工复核 | {len(needs_review)} |",
        f"| 沙箱执行 | {len(sandbox_runs)} |",
        f"| Filter 拦截 | {len(filter_intercepts)} |",
        f"",
    ]

    # Critical findings
    if findings:
        lines.append("## 🚨 必须修复")
        lines.append("")
        for f in findings:
            lines.append(f"### {f.title}")
            lines.append(f"")
            lines.append(f"- **文件**: `{f.file_path}` L{f.line_number}")
            lines.append(f"- **类别**: {f.category.value}")
            lines.append(f"- **置信度**: {f.confidence.value}")
            lines.append(f"- **证据**: `{f.evidence}`")
            lines.append(f"- **建议**: {f.recommendation}")
            lines.append("")

    # Warnings
    if warnings:
        lines.append("## ⚠️ 建议修复")
        lines.append("")
        for f in warnings:
            lines.append(f"### {f.title}")
            lines.append(f"")
            lines.append(f"- **文件**: `{f.file_path}` L{f.line_number}")
            lines.append(f"- **类别**: {f.category.value}")
            lines.append(f"- **证据**: `{f.evidence}`")
            lines.append(f"- **建议**: {f.recommendation}")
            lines.append("")

    # Needs human review
    if needs_review:
        lines.append("## 🔍 待人工复核")
        lines.append("")
        for f in needs_review:
            lines.append(f"- **{f.title}** (`{f.file_path}` L{f.line_number}) — {f.evidence}")
        lines.append("")

    # Filter intercepts
    if filter_intercepts:
        lines.append("## 🔒 Filter 拦截记录")
        lines.append("")
        lines.append("| 类型 | 动作 | 目标 | 原因 |")
        lines.append("|------|------|------|------|")
        for fi in filter_intercepts:
            lines.append(f"| {fi.filter_type.value} | {fi.action.value} | {fi.target or '-'} | {fi.reason or '-'} |")
        lines.append("")

    # Sandbox runs
    if sandbox_runs:
        lines.append("## ⚡ 沙箱执行摘要")
        lines.append("")
        lines.append("| 脚本 | 状态 | 耗时(ms) | 输出大小 |")
        lines.append("|------|------|---------|---------|")
        for s in sandbox_runs:
            lines.append(f"| {s.script_name} | {s.status.value} | {s.duration_ms:.0f} | {s.output_size_bytes} bytes |")
        if any(s.error_message for s in sandbox_runs):
            lines.append("")
            lines.append("**错误详情**:")
            for s in sandbox_runs:
                if s.error_message:
                    lines.append(f"- `{s.script_name}`: {s.error_message}")
        lines.append("")

    # Monitoring
    if monitor:
        lines.append("## 📊 监控指标")
        lines.append("")
        lines.append(f"- 总耗时: {monitor.total_duration_ms:.0f}ms")
        lines.append(f"- 沙箱耗时: {monitor.sandbox_duration_ms:.0f}ms")
        lines.append(f"- 工具调用次数: {monitor.tool_call_count}")
        lines.append(f"- 拦截次数: {monitor.intercept_count}")

    return "\n".join(lines)


# ── Main Pipeline ──

def run_review(config: ReviewAgentConfig) -> Optional[ReviewResult]:
    """Run the full code review pipeline.

    Orchestrates: diff parsing → pattern detection → sandbox execution
    → deduplication → report generation → database storage.

    Args:
        config: ReviewAgentConfig with all settings for this run.

    Returns:
        A ReviewResult object with all findings, reports, and metadata,
        or None if the pipeline fatally failed.
    """
    start_time = time.time()
    repo = SqliteCrRepository(config.db_path)

    try:
        # ── 1. Create Review Task ──
        task = ReviewTask(
            input_type=config.input_source,
            input_summary="{}",
            status=TaskStatus.RUNNING,
        )
        repo.create_task(task)
        task_id = task.id

        # ── 2. Read Input ──
        diff_content = ""
        if config.input_source == "fixture":
            fixture_path = Path(__file__).parent / "evals" / "fixtures" / f"{config.input_value}.diff"
            if fixture_path.exists():
                diff_content = fixture_path.read_text(encoding="utf-8")
            else:
                # Fall back to dynamic generator for fixtures that contain
                # fake credentials (to avoid CodeCC false positives)
                try:
                    from evals.fixtures.generate_fixtures import get_fixture_content
                    generated = get_fixture_content(config.input_value)
                    if generated is not None:
                        diff_content = generated
                except ImportError:
                    pass
        elif config.input_source == "diff_file":
            diff_path = Path(config.input_value)
            if diff_path.exists():
                diff_content = diff_path.read_text(encoding="utf-8")
        else:
            diff_content = config.input_value

        if not diff_content:
            task.status = TaskStatus.FAILED
            task.error_message = "No diff content found"
            repo.update_task(task)
            return None

        # ── 3. Parse Diff ──
        parsed = parse_diff(diff_content)
        task.input_summary = json.dumps(parsed, ensure_ascii=False)
        repo.update_task(task)

        # ── 4. Pattern-based Detection ──
        raw_findings = detect_findings_by_pattern(diff_content, task_id)

        # ── 5. Run Sandbox Scripts with Filter Governance ──
        sandbox_runs: list[SandboxRun] = []
        all_filter_intercepts: list[FilterLog] = []
        if not config.dry_run:
            # Static check script
            static_script = _build_static_check_script(diff_content)
            flogs, allowed = run_filter_governance(static_script, "scripts/run_static_check.py", task_id)
            for fl in flogs:
                repo.create_filter_log(fl)
            all_filter_intercepts.extend(flogs)

            if allowed:
                sb_run = run_sandbox_script(
                    "run_static_check.py", static_script, task_id,
                    timeout=config.sandbox_timeout, max_output=config.sandbox_max_output,
                    sandbox_type=config.sandbox_type,
                )
                sandbox_runs.append(sb_run)
            else:
                # Record intercepted sandbox run
                sandbox_runs.append(SandboxRun(
                    task_id=task_id,
                    script_name="run_static_check.py",
                    status=SandboxStatus.INTERCEPTED,
                    intercept_reason=flogs[-1].reason if flogs else "Filter denied",
                ))

            # Secret detection script
            secret_script = _build_secret_detection_script(diff_content)
            flogs2, allowed2 = run_filter_governance(secret_script, "scripts/detect_secrets.py", task_id)
            for fl in flogs2:
                repo.create_filter_log(fl)
            all_filter_intercepts.extend(flogs2)

            if allowed2:
                sb_run2 = run_sandbox_script(
                    "detect_secrets.py", secret_script, task_id,
                    timeout=config.sandbox_timeout, max_output=config.sandbox_max_output,
                    sandbox_type=config.sandbox_type,
                )
                sandbox_runs.append(sb_run2)
            else:
                sandbox_runs.append(SandboxRun(
                    task_id=task_id,
                    script_name="detect_secrets.py",
                    status=SandboxStatus.INTERCEPTED,
                    intercept_reason=flogs2[-1].reason if flogs2 else "Filter denied",
                ))

        # Store sandbox runs
        for sb in sandbox_runs:
            repo.create_sandbox_run(sb)

        # ── 6. Classify Findings ──
        classified = classify_findings(raw_findings)

        # ── 7. Store Findings ──
        for finding in classified["findings"] + classified["warnings"] + classified["needs_human_review"]:
            repo.create_finding(finding)

        # ── 8. Compute Severity Distribution ──
        all_fs = classified["findings"] + classified["warnings"] + classified["needs_human_review"]
        severity_dist = {
            "critical": sum(1 for f in all_fs if f.severity == FindingSeverity.CRITICAL),
            "warning": sum(1 for f in all_fs if f.severity == FindingSeverity.WARNING),
            "suggestion": sum(1 for f in all_fs if f.severity == FindingSeverity.SUGGESTION),
        }
        severity_dist_json = json.dumps(severity_dist, ensure_ascii=False)

        # ── 9. Update Task as Completed (before report generation) ──
        total_duration = (time.time() - start_time) * 1000
        sandbox_duration = sum(s.duration_ms for s in sandbox_runs)
        task.status = TaskStatus.COMPLETED
        task.total_duration_ms = total_duration
        task.finding_count = len(all_fs)
        task.severity_distribution = severity_dist_json
        repo.update_task(task)

        # Mask secrets in findings before generating reports
        masked_findings = _mask_finding_secrets(classified["findings"])
        masked_warnings = _mask_finding_secrets(classified["warnings"])
        masked_needs_review = _mask_finding_secrets(classified["needs_human_review"])

        # Build monitor summary
        monitor = MonitorSummary(
            task_id=task_id,
            total_duration_ms=total_duration,
            sandbox_duration_ms=sandbox_duration,
            tool_call_count=1,
            intercept_count=0,
            finding_count=len(all_fs),
            severity_distribution=severity_dist_json,
            exception_types=json.dumps([]),
        )

        # ── 10. Generate Reports ──
        os.makedirs(config.output_dir, exist_ok=True)
        json_path = os.path.join(config.output_dir, "review_report.json")
        md_path = os.path.join(config.output_dir, "review_report.md")

        json_content = generate_json_report(
            task, masked_findings, masked_warnings, masked_needs_review,
            sandbox_runs, [], monitor,
        )
        md_content = generate_markdown_report(
            task, masked_findings, masked_warnings, masked_needs_review,
            sandbox_runs, [], monitor,
        )

        with open(json_path, "w", encoding="utf-8") as f:
            f.write(json_content)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)

        # Store reports
        repo.create_report(ReviewReport(
            task_id=task_id,
            report_type=ReportType.JSON,
            content=json_content,
            summary=json.dumps({"finding_count": len(all_fs), "severity_distribution": severity_dist}),
            monitoring_metrics=monitor.model_dump_json(),
        ))
        repo.create_report(ReviewReport(
            task_id=task_id,
            report_type=ReportType.MARKDOWN,
            content=md_content,
        ))

        # Store monitor summary
        repo.create_monitor_summary(monitor)

        # Build result
        return ReviewResult(
            task=task,
            findings=masked_findings,
            warnings=masked_warnings,
            needs_human_review=masked_needs_review,
            sandbox_runs=sandbox_runs,
            filter_intercepts=all_filter_intercepts,
            monitor=monitor,
            report_path_json=json_path,
            report_path_md=md_path,
        )

    except Exception as e:
        # ── Fatal error handling: don't crash, record failure ──
        import traceback
        error_msg = f"{type(e).__name__}: {str(e)[:500]}"
        try:
            task.status = TaskStatus.FAILED
            task.error_message = error_msg
            repo.update_task(task)
        except Exception:
            pass

        return ReviewResult(
            task=ReviewTask(
                id=task_id if "task_id" in dir() else str(uuid.uuid4()),
                status=TaskStatus.FAILED,
                error_message=error_msg,
            ),
            findings=[],
            warnings=[],
            needs_human_review=[],
            sandbox_runs=[],
            filter_intercepts=[],
        )

    finally:
        repo.close()


def _build_static_check_script(diff_content: str) -> str:
    """Build a static analysis script that runs on the diff content."""
    escaped = diff_content.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")
    return f"""
import sys
import json

diff_content = '''{escaped}'''

# Simple static analysis: check for common issues
findings = []
lines = diff_content.split('\\n')
current_file = ''

for i, line in enumerate(lines):
    if line.startswith('+++ b/'):
        current_file = line[6:]
    if line.startswith('+') and not line.startswith('+++'):
        # Check for TODO/FIXME
        if 'TODO' in line.upper():
            findings.append({{
                "severity": "suggestion",
                "category": "maintainability",
                "file": current_file,
                "line": i + 1,
                "title": "遗留 TODO 注释",
                "evidence": line.strip(),
                "recommendation": "在提交前解决 TODO 项",
                "confidence": "low",
                "source": "static_check"
            }})

print(json.dumps({{"findings": findings}}, ensure_ascii=False))
"""


def _build_secret_detection_script(diff_content: str) -> str:
    """Build a secret detection script that runs on the diff content."""
    escaped = diff_content.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")
    return f"""
import sys
import json
import re

diff_content = '''{escaped}'''

# Secret detection patterns
patterns = [
    (r"(?i)(api_key|api[_-]?key|apikey|password|secret)\\\\s*[=:]\\\\s*['\\\"][^'\\\"]+['\\\"]", "可能的敏感信息"),
    (r"ghp_[a-zA-Z0-9]{{36,}}", "GitHub Token"),
    (r"AKIA[0-9A-Z]{{16}}", "AWS Access Key"),
    (r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----", "私钥"),
]

findings = []
lines = diff_content.split('\\n')
current_file = ''

for i, line in enumerate(lines):
    if line.startswith('+++ b/'):
        current_file = line[6:]
    if line.startswith('+') and not line.startswith('+++'):
        for pattern, label in patterns:
            if re.search(pattern, line):
                findings.append({{
                    "severity": "critical",
                    "category": "secret",
                    "file": current_file,
                    "line": i + 1,
                    "title": f"检测到{{label}}",
                    "evidence": line.strip()[:80],
                    "recommendation": "移除硬编码的敏感信息，使用环境变量",
                    "confidence": "high",
                    "source": "static_check"
                }})

print(json.dumps({{"findings": findings}}, ensure_ascii=False))
"""


def _mask_finding_secrets(findings: list[Finding]) -> list[Finding]:
    """Mask secrets in finding evidence and recommendation fields."""
    masked = []
    for f in findings:
        f.evidence = mask_secrets(f.evidence) if f.evidence else None
        f.recommendation = mask_secrets(f.recommendation) if f.recommendation else None
        masked.append(f)
    return masked