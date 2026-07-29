#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

"""Explicit real-model integration and checked-in sample-output tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from code_review.model_environment import load_model_environment  # noqa: E402
from code_review.redaction import contains_plaintext_secret  # noqa: E402
from code_review.report import CanonicalReportWriter, MarkdownReportRenderer  # noqa: E402


_MODEL_KEYS = ("TRPC_AGENT_API_KEY", "TRPC_AGENT_BASE_URL", "TRPC_AGENT_MODEL_NAME")
_IDENTITY_FIELDS = (
    "severity",
    "category",
    "file",
    "line",
    "title",
    "evidence",
    "confidence",
    "source",
    "rule_id",
    "bucket",
    "dedup_key",
)


def _run_review(tmp_path: Path, *, model_mode: str, dry_run: bool) -> tuple[dict[str, Any], dict[str, Any]]:
    """在隔离目录调用 CLI，并返回其安全摘要和 canonical JSON 报告。"""

    output_dir = tmp_path / model_mode
    database = tmp_path / f"{model_mode}.db"
    environment = os.environ.copy()
    for key in _MODEL_KEYS:
        environment.pop(key, None)
    arguments = [
        sys.executable,
        str(PROJECT_ROOT / "run_agent.py"),
        "review",
        "--fixture",
        "02_security_simple",
        "--sandbox",
        "local",
        "--model-mode",
        model_mode,
        "--output-dir",
        str(output_dir),
        "--db-url",
        f"sqlite+pysqlite:///{database.as_posix()}",
    ]
    if dry_run:
        arguments.append("--dry-run")
    completed = subprocess.run(
        arguments,
        cwd=PROJECT_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        encoding="utf-8",
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, "real_model_cli_failed"
    return (
        json.loads(completed.stdout),
        json.loads((output_dir / "review_report.json").read_text(encoding="utf-8")),
    )


def _finding_identity(report: dict[str, Any]) -> list[dict[str, Any]]:
    """提取冻结的 finding 身份字段，用于比较 fake 与 real 的检测结果。"""

    findings = [*report["findings"], *report["needs_human_review"]]
    return [{field: finding[field] for field in _IDENTITY_FIELDS} for finding in findings]


@pytest.mark.real_llm
def test_explicit_real_model_loads_project_dotenv_and_preserves_deterministic_findings(
    tmp_path: Path,
) -> None:
    """验证真实模型仅在显式 real 模式读取项目 .env，且不改变确定性 finding。"""

    configuration = load_model_environment(PROJECT_ROOT / ".env", environ={})
    if not all(configuration.get(key) for key in _MODEL_KEYS):
        pytest.skip("real_model_configuration_missing")

    real_summary, real_report = _run_review(tmp_path, model_mode="real", dry_run=False)
    fake_summary, fake_report = _run_review(tmp_path, model_mode="fake", dry_run=True)
    serialized_outputs = "\n".join(
        (
            json.dumps(real_summary, ensure_ascii=False, sort_keys=True),
            json.dumps(real_report, ensure_ascii=False, sort_keys=True),
        )
    )

    assert real_summary["status"] == "completed_with_warnings"
    assert real_report["metrics"]["llm_duration_ms"] > 0
    assert "llm_enhancement_failed" not in {warning["code"] for warning in real_report["warnings"]}
    assert _finding_identity(real_report) == _finding_identity(fake_report)
    assert contains_plaintext_secret(real_report) is False
    assert int(configuration["TRPC_AGENT_API_KEY"] in serialized_outputs) == 0


def test_checked_in_sample_is_schema_valid_rendered_from_json_and_secret_free() -> None:
    """验证提交的样例报告可通过 schema，Markdown 只由 JSON 渲染且不泄漏敏感信息。"""

    sample_dir = PROJECT_ROOT / "sample_output"
    json_text = (sample_dir / "review_report.json").read_text(encoding="utf-8")
    markdown_text = (sample_dir / "review_report.md").read_text(encoding="utf-8")
    report = json.loads(json_text)
    canonical = CanonicalReportWriter().validate(report)

    assert markdown_text == MarkdownReportRenderer().render(canonical)
    assert contains_plaintext_secret(canonical) is False
    assert contains_plaintext_secret(markdown_text) is False
    assert canonical["metrics"]["tool_call_count"] == 2
    assert "\\\\" not in json_text
    assert "\\\\" not in markdown_text
