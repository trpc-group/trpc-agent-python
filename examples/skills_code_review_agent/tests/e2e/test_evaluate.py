#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

"""Offline evaluation entry-point contracts for D4."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EVALUATE_PATH = PROJECT_ROOT / "evaluate.py"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import evaluate  # noqa: E402


@pytest.fixture(scope="module")
def evaluation_summary(tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    """执行一次真实 local Skill 评测，并返回其不含原始语料的摘要。"""

    output_dir = tmp_path_factory.mktemp("evaluation")
    completed = subprocess.run(
        [
            sys.executable,
            str(EVALUATE_PATH),
            "--sandbox",
            "local",
            "--output-dir",
            str(output_dir),
        ],
        cwd=output_dir,
        check=False,
        capture_output=True,
        encoding="utf-8",
        text=True,
        timeout=600,
    )
    assert completed.returncode == 0, completed.stderr
    summary_path = output_dir / "eval_summary.json"
    assert summary_path.is_file()
    assert not (output_dir / "review.db").exists()
    return json.loads(summary_path.read_text(encoding="utf-8"))


def test_evaluate_metrics_meet_public_proxy_gates(evaluation_summary: dict[str, object]) -> None:
    """验证公开代理语料的 fixture、召回、误报占比和 P/R/F1 统计。"""

    assert evaluation_summary["fixture_summary"] == {"passed": 8, "total": 8}
    assert evaluation_summary["corpus"]["boundary_cases"] >= 8
    metrics = evaluation_summary["metrics"]
    assert isinstance(metrics, dict)
    assert metrics["high_risk_recall"] >= 0.80
    assert metrics["finding_false_positive_share"] <= 0.15
    assert metrics["benign_secret_false_positives"] == 0
    assert set(("precision", "recall", "f1")) <= set(metrics)


def test_evaluate_redact_and_per_fixture_duration_gates(evaluation_summary: dict[str, object]) -> None:
    """验证密钥检测率与八条独立 Agent 审查均在单任务墙钟预算内完成。"""

    metrics = evaluation_summary["metrics"]
    assert isinstance(metrics, dict)
    assert metrics["redaction_detection_rate"] >= 0.95
    assert metrics["plaintext_hits"] == 0
    fixture_runs = evaluation_summary["fixture_runs"]
    assert isinstance(fixture_runs, list)
    assert len(fixture_runs) == 8
    assert {run["fixture"] for run in fixture_runs} == set(evaluate.FIXTURE_NAMES)
    assert all(run["entrypoint"] == "agent" for run in fixture_runs)
    assert all(run["skill_tools"] == ["skill_load", "skill_run"] for run in fixture_runs)
    assert all(0 < run["duration_ms"] <= 120_000 for run in fixture_runs)


def test_evaluate_summary_and_optional_history(tmp_path: Path) -> None:
    """验证摘要包含环境摘要、默认无业务库写入且可显式写入独立历史库。"""

    output_dir = tmp_path / "evaluation"
    history_db = tmp_path / "evaluation_history.db"
    completed = subprocess.run(
        [
            sys.executable,
            str(EVALUATE_PATH),
            "--sandbox",
            "local",
            "--output-dir",
            str(output_dir),
            "--write-db",
            str(history_db),
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        encoding="utf-8",
        text=True,
        timeout=600,
    )
    assert completed.returncode == 0, completed.stderr
    summary = json.loads((output_dir / "eval_summary.json").read_text(encoding="utf-8"))
    assert history_db.is_file()
    assert summary["history"]["enabled"] is True
    assert {"python", "platform", "runtime", "schema_version", "rule_pack_version", "config_digest"} <= set(summary)


def test_evaluate_rejects_real_or_llm_denoise_options(tmp_path: Path) -> None:
    """验证门禁入口固定 fake 模型，拒绝 real 与本期不存在的 LLM 降噪参数。"""

    for forbidden_arguments in (("--model-mode", "real"), ("--llm-denoise",)):
        completed = subprocess.run(
            [sys.executable, str(EVALUATE_PATH), *forbidden_arguments],
            cwd=tmp_path,
            check=False,
            capture_output=True,
            encoding="utf-8",
            text=True,
            timeout=30,
        )
        assert completed.returncode != 0


def test_evaluate_subprocess_environment_is_allowlisted(monkeypatch: pytest.MonkeyPatch) -> None:
    """验证评测子进程环境不携带任意名称或值的宿主敏感变量。"""

    canary_name = "AWS_SECRET_ACCESS_KEY"
    canary_value = "synthetic-canary-value"
    monkeypatch.setenv(canary_name, canary_value)

    environment = evaluate._sanitized_environment()

    assert canary_name not in environment
    assert canary_value not in environment.values()
    assert environment["PYTHONUTF8"] == "1"


def test_evaluate_subprocess_environment_keeps_resolved_docker_available(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """验证 container 评测仅加入已解析 Docker 目录，而非透传完整宿主 PATH。"""

    docker_directory = tmp_path / "docker-bin"
    docker_path = docker_directory / ("docker.exe" if os.name == "nt" else "docker")
    monkeypatch.setattr(
        evaluate.shutil,
        "which",
        lambda program: str(docker_path) if program == "docker" else None,
    )

    environment = evaluate._sanitized_environment()

    assert str(docker_directory) in environment["PATH"].split(os.pathsep)
    assert os.environ.get("PATH", "") != environment["PATH"]


def test_evaluate_configures_safe_sdk_logging(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """验证评测入口复用安全日志策略，避免 SDK 运行时诊断泄露临时工作区路径。"""

    configured_levels: list[str] = []

    def configure_logging(level: str) -> None:
        """记录评测入口请求的项目日志级别，不向测试输出真实日志。"""

        configured_levels.append(level)

    monkeypatch.setattr(evaluate, "configure_safe_logging", configure_logging)
    fixture_runs = [
        {
            "fixture": name,
            "entrypoint": "agent",
            "skill_tools": ["skill_load", "skill_run"],
            "status": "completed",
            "duration_ms": 100,
            "reports_verified": True,
            "database_verified": True,
        }
        for name in evaluate.FIXTURE_NAMES
    ]
    monkeypatch.setattr(evaluate, "_run_fixture_suite", lambda *_arguments: (fixture_runs, 0))
    monkeypatch.setattr(
        evaluate,
        "_evaluate_corpus",
        lambda *_arguments: {
            "corpus": {"positive_cases": 20, "clean_negative_cases": 10, "secret_cases": 48},
            "metrics": {
                "high_risk_recall": 1.0,
                "finding_false_positive_share": 0.0,
                "redaction_detection_rate": 1.0,
                "plaintext_hits": 0,
                "benign_secret_false_positives": 0,
            },
        },
    )
    monkeypatch.setattr(evaluate, "_observe_blind_spots", lambda *_arguments: (4, 0))

    exit_code = evaluate.main(["--sandbox", "local", "--output-dir", str(tmp_path / "evaluation")])

    assert exit_code == 0
    assert configured_levels == ["WARNING"]


def test_evaluate_fixture_suite_runs_each_fixture_as_an_independent_agent_task(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """验证八条 fixture 各自调用 Agent，保留独立任务的工具序列与单条耗时证据。"""

    reviewed_fixtures: list[str] = []

    def run_fixture_agent(
        _work_root: Path,
        fixture_name: str,
        _sandbox: str,
    ) -> tuple[dict[str, object], int]:
        """模拟一个已验证的 Agent 任务结果，避免测试依赖真实子进程时延。"""

        reviewed_fixtures.append(fixture_name)
        return (
            {
                "fixture": fixture_name,
                "entrypoint": "agent",
                "skill_tools": ["skill_load", "skill_run"],
                "status": "completed",
                "duration_ms": 125,
                "reports_verified": True,
                "database_verified": True,
            },
            0,
        )

    monkeypatch.setattr(evaluate, "_run_fixture_agent", run_fixture_agent)

    fixture_runs, plaintext_hits = evaluate._run_fixture_suite(tmp_path, "local")

    assert reviewed_fixtures == list(evaluate.FIXTURE_NAMES)
    assert len(fixture_runs) == len(evaluate.FIXTURE_NAMES)
    assert all(run["entrypoint"] == "agent" for run in fixture_runs)
    assert all(run["skill_tools"] == ["skill_load", "skill_run"] for run in fixture_runs)
    assert all(0 < run["duration_ms"] <= evaluate.HARD_LIMIT_MS for run in fixture_runs)
    assert plaintext_hits == 0


def test_evaluate_history_rejects_business_review_schema(tmp_path: Path) -> None:
    """验证评测历史库拒绝业务 review.db 名称和已有业务五表，避免污染审查数据。"""

    business_database = tmp_path / "business.db"
    connection = sqlite3.connect(business_database)
    try:
        connection.execute("CREATE TABLE cr_review_task (id TEXT PRIMARY KEY)")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(ValueError, match="evaluation_history_database_invalid"):
        evaluate._write_history(business_database, {"status": "safe"})
    with pytest.raises(ValueError, match="evaluation_history_database_invalid"):
        evaluate._write_history(tmp_path / "review.db", {"status": "safe"})


def test_evaluate_returns_nonzero_when_a_hard_gate_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """验证任一公开代理硬门禁失败时入口返回非零，而不是仅在摘要中记录失败。"""

    fixture_runs = [
        {
            "fixture": name,
            "entrypoint": "agent",
            "skill_tools": ["skill_load", "skill_run"],
            "status": "completed",
            "duration_ms": 100,
            "reports_verified": True,
            "database_verified": True,
        }
        for name in evaluate.FIXTURE_NAMES
    ]
    monkeypatch.setattr(evaluate, "_run_fixture_suite", lambda *_arguments: (fixture_runs, 0))
    monkeypatch.setattr(
        evaluate,
        "_evaluate_corpus",
        lambda *_arguments: {
            "corpus": {"positive_cases": 20, "clean_negative_cases": 10, "secret_cases": 48},
            "metrics": {
                "high_risk_recall": 0.79,
                "finding_false_positive_share": 0.0,
                "redaction_detection_rate": 1.0,
                "plaintext_hits": 0,
                "benign_secret_false_positives": 0,
            },
        },
    )
    monkeypatch.setattr(evaluate, "_observe_blind_spots", lambda *_arguments: (4, 0))
    original_hard_gates_pass = evaluate._hard_gates_pass
    hard_gate_call_count = 0

    def count_hard_gate_calls(
        metrics: dict[str, object],
        runs: list[dict[str, object]],
    ) -> bool:
        """记录评测入口计算硬门禁的次数，并保留真实判断逻辑。"""

        nonlocal hard_gate_call_count
        hard_gate_call_count += 1
        return original_hard_gates_pass(metrics, runs)

    monkeypatch.setattr(evaluate, "_hard_gates_pass", count_hard_gate_calls)

    exit_code = evaluate.main(["--sandbox", "local", "--output-dir", str(tmp_path / "evaluation")])

    assert exit_code == 1
    assert hard_gate_call_count == 1
