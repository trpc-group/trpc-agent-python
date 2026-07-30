# Tencent is pleased to support the open source community by making trpc-agent-python available.
# Copyright (C) 2025 Tencent. All rights reserved.
# trpc-agent-python is licensed under the Apache License Version 2.0.
"""Markdown report generator."""

from typing import Any


_SEVERITY_EMOJI = {
    'critical': '\U0001f534',  # red circle
    'high': '\U0001f7e0',       # orange circle
    'medium': '\U0001f7e1',     # yellow circle
    'low': '\U0001f7e2',        # green circle
}


def generate_markdown_report(review_data: dict[str, Any]) -> str:
    """Generate a human-readable Markdown review report."""
    findings = review_data.get('findings', [])
    warnings = review_data.get('warnings', [])
    monitoring = review_data.get('monitoring', {})
    task_id = review_data.get('task_id', 'unknown')

    lines = [
        f'# Code Review Report',
        f'',
        f'**Task ID**: `{task_id}`',
        f'**Duration**: {monitoring.get("total_duration_ms", 0)}ms',
        f'**Files**: {monitoring.get("file_count", 0)}',
        f'**Lines Added**: {monitoring.get("total_added_lines", 0)}',
        f'',
        f'---',
        f'',
        f'## Summary',
        f'',
    ]

    # Findings summary table
    lines.append(f'| Severity | Count |')
    lines.append(f'|----------|-------|')
    sev_dist = monitoring.get('severity_distribution', {})
    for sev in ['critical', 'high', 'medium', 'low']:
        count = sev_dist.get(sev, 0)
        emoji = _SEVERITY_EMOJI.get(sev, '')
        lines.append(f'| {emoji} {sev.capitalize()} | {count} |')
    lines.append(f'| **Total** | **{len(findings)}** |')

    if warnings:
        lines.append(f'')
        lines.append(f'**Warnings**: {len(warnings)} (needs human review)')

    lines.append(f'')
    lines.append(f'---')
    lines.append(f'')

    # Findings detail
    if not findings:
        lines.append(f'## Findings')
        lines.append(f'')
        lines.append(f'*No issues detected. The changes look good!*')
    else:
        lines.append(f'## Findings ({len(findings)})')
        lines.append(f'')

        by_severity: dict[str, list] = {'critical': [], 'high': [], 'medium': [], 'low': []}
        for f in findings:
            sev = f.get('severity', 'low')
            by_severity[sev].append(f)

        for sev in ['critical', 'high', 'medium', 'low']:
            items = by_severity[sev]
            if not items:
                continue
            emoji = _SEVERITY_EMOJI.get(sev, '')
            lines.append(f'### {emoji} {sev.capitalize()} ({len(items)})')
            lines.append(f'')
            for i, f in enumerate(items, 1):
                lines.append(f'**{i}. [{f.get("rule_id", "")}] {f.get("title", "Issue")}**')
                lines.append(f'')
                lines.append(f'- **File**: `{f.get("file", "")}`')
                lines.append(f'- **Line**: {f.get("line", 0)}')
                lines.append(f'- **Category**: {f.get("category", "unknown")}')
                lines.append(f'- **Confidence**: {f.get("confidence", 0):.0%}')
                evidence = f.get('evidence', '')
                if evidence:
                    lines.append(f'- **Evidence**: `{evidence}`')
                rec = f.get('recommendation', '')
                if rec:
                    lines.append(f'- **Recommendation**: {rec}')
                lines.append(f'')

    # Warnings section
    if warnings:
        lines.append(f'---')
        lines.append(f'')
        lines.append(f'## Needs Human Review ({len(warnings)})')
        lines.append(f'')
        for i, w in enumerate(warnings, 1):
            lines.append(f'{i}. **[{w.get("rule_id", "")}] {w.get("title", "")}** — {w.get("file", "")}:{w.get("line", 0)} — confidence: {w.get("confidence", 0):.0%}')
        lines.append(f'')

    # Filter Intercepts section
    filter_decisions = review_data.get('filter_decisions', [])
    if filter_decisions:
        lines.append(f'---')
        lines.append(f'')
        lines.append(f'## Filter Intercepts')
        lines.append(f'')
        by_action: dict[str, list] = {}
        for d in filter_decisions:
            action = str(d.get('action', 'allow'))
            by_action.setdefault(action, []).append(d)
        for action in ['deny', 'ask', 'needs_human_review']:
            items = by_action.get(action, [])
            if not items:
                continue
            label = {'deny': 'Blocked', 'ask': 'Confirmation Required',
                     'needs_human_review': 'Needs Human Review'}.get(action, action)
            lines.append(f'### {label} ({len(items)})')
            lines.append(f'')
            for d in items:
                reason = str(d.get('reason', '') or 'blocked by filter')
                rule = d.get('rule', '')
                lines.append(f'- **[{rule}]** {reason}')
            lines.append(f'')

    # Sandbox Execution Summary section
    sandbox_runs = review_data.get('sandbox_runs', [])
    if sandbox_runs:
        lines.append(f'---')
        lines.append(f'')
        lines.append(f'## Sandbox Execution')
        lines.append(f'')
        lines.append(f'| Script | Exit | Duration | Timed Out |')
        lines.append(f'|--------|------|----------|-----------|')
        for s in sandbox_runs:
            script = str(s.get('script', 'N/A'))
            exit_code = s.get('exit_code', '?')
            dur = s.get('duration_ms', 0)
            timed_out = 'YES' if s.get('timed_out') else 'no'
            lines.append(f'| {script} | {exit_code} | {dur}ms | {timed_out} |')
        lines.append(f'')

    # Monitoring section
    lines.append(f'---')
    lines.append(f'')
    lines.append(f'## Monitoring')
    lines.append(f'')
    lines.append(f'- Total duration: {monitoring.get("total_duration_ms", 0)}ms')
    lines.append(f'- Sandbox duration: {monitoring.get("sandbox_duration_ms", 0)}ms')
    lines.append(f'- Tool calls: {monitoring.get("tool_call_count", 0)}')
    lines.append(f'- Filter intercepts: {monitoring.get("intercept_count", 0)}')

    return '\n'.join(lines)
