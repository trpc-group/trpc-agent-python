# Tencent is pleased to support the open source community by making trpc-agent-python available.
# Copyright (C) 2025 Tencent. All rights reserved.
# trpc-agent-python is licensed under the Apache License Version 2.0.
"""JSON report generator."""

import json
from typing import Any


def generate_json_report(review_data: dict[str, Any]) -> str:
    """Generate a structured JSON review report."""
    findings = review_data.get('findings', [])
    warnings = review_data.get('warnings', [])
    monitoring = review_data.get('monitoring', {})

    severity_count = {'critical': 0, 'high': 0, 'medium': 0, 'low': 0}
    category_count: dict[str, int] = {}
    for f in findings:
        sev = f.get('severity', 'low')
        severity_count[sev] = severity_count.get(sev, 0) + 1
        cat = f.get('category', 'unknown')
        category_count[cat] = category_count.get(cat, 0) + 1

    report = {
        'task_id': review_data.get('task_id', ''),
        'summary': {
            'total_findings': len(findings),
            'total_warnings': len(warnings),
            'severity_distribution': severity_count,
            'category_distribution': category_count,
            'files_analyzed': monitoring.get('file_count', 0),
            'total_added_lines': monitoring.get('total_added_lines', 0),
        },
        'monitoring': {
            'total_duration_ms': monitoring.get('total_duration_ms', 0),
            'sandbox_duration_ms': monitoring.get('sandbox_duration_ms', 0),
            'tool_call_count': monitoring.get('tool_call_count', 0),
            'intercept_count': monitoring.get('intercept_count', 0),
        },
        'findings': findings,
        'warnings': warnings,
        'needs_human_review': [
            w for w in warnings if w.get('confidence', 0) < 0.85
        ],
    }

    # Pass through extra sections that the main pipeline populates
    for key in ('filter_decisions', 'sandbox_runs', 'agent_output'):
        if review_data.get(key):
            report[key] = review_data.get(key)

    return json.dumps(report, indent=2, ensure_ascii=False)
