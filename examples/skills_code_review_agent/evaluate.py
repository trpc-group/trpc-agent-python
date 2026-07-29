#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

"""Offline evaluation entry point for the automatic code-review Agent."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from code_review.config import ReviewConfig
from code_review.skill_loader import load_skill_module
from code_review.store import SqlReviewStore
from run_agent import configure_safe_logging

PROJECT_ROOT = Path(__file__).resolve().parent
CORPUS_PATH = PROJECT_ROOT / "tests" / "fixtures" / "corpus" / "evaluation_corpus.json"
BLIND_SPOT_PATH = PROJECT_ROOT / "tests" / "fixtures" / "corpus" / "blind_spot_observations.json"
FIXTURE_DIR = PROJECT_ROOT / "tests" / "fixtures" / "diffs"
RUN_CHECKS_PATH = PROJECT_ROOT / "skills" / "code-review" / "scripts" / "run_checks.py"
RUN_AGENT_PATH = PROJECT_ROOT / "run_agent.py"
FIXTURE_NAMES = (
    "01_clean_simple",
    "02_security_simple",
    "03_async_leak_simple",
    "04_db_lifecycle_simple",
    "05_missing_tests_simple",
    "06_duplicate_finding_simple",
    "07_sandbox_failure_simple",
    "08_secret_redaction_simple",
)
FINDING_CONFIDENCE = 0.80
HARD_LIMIT_MS = 120_000


def _load_json(path: Path) -> dict[str, Any]:
    """读取受控 JSON 语料；格式异常仅向调用者报告固定错误语义。"""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("evaluation_corpus_invalid") from exc
    if not isinstance(payload, dict):
        raise ValueError("evaluation_corpus_invalid")
    return payload


def _unified_diff(path: str, lines: Sequence[str]) -> str:
    """把受控语料行转换为新增文件 diff，保留真实 changed-line 定位。"""

    body = "\n".join(f"+{line}" for line in lines)
    return (
        f"diff --git a/{path} b/{path}\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        f"+++ b/{path}\n"
        f"@@ -0,0 +1,{len(lines)} @@\n"
        f"{body}\n"
    )


def _sanitized_environment(workspace: Path | None = None) -> dict[str, str]:
    """构造无宿主凭据的子进程环境，避免评测脚本继承模型或令牌配置。"""

    environment = {"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    executable_directories = [str(Path(sys.executable).parent)]
    docker_executable = shutil.which("docker")
    if docker_executable is not None:
        executable_directories.append(str(Path(docker_executable).parent))
    system_root = os.environ.get("SYSTEMROOT")
    if system_root:
        environment["SYSTEMROOT"] = system_root
        executable_directories.append(f"{system_root}\\System32")
    environment["PATH"] = os.pathsep.join(dict.fromkeys(executable_directories))
    if workspace is not None:
        temporary_directory = workspace / "temporary"
        temporary_directory.mkdir(parents=True, exist_ok=True)
        environment["TEMP"] = str(temporary_directory)
        environment["TMP"] = str(temporary_directory)
    return environment


def _run_skill_checks(
    diff: str,
    work_root: Path,
    case_id: str,
    sandbox: str,
) -> tuple[dict[str, Any], int]:
    """在临时 local workspace 真正执行受信任 run_checks 脚本并读取脱敏 finding 输出。"""

    workspace = work_root / hashlib.sha256(case_id.encode("utf-8")).hexdigest()[:16]
    input_path = workspace / "work" / "inputs" / "diff.json"
    input_path.parent.mkdir(parents=True, exist_ok=True)
    input_path.write_text(
        json.dumps({"source_kind": "diff_file", "diff": diff}, ensure_ascii=False),
        encoding="utf-8",
    )
    started = time.monotonic()
    if sandbox == "local":
        command = [sys.executable, str(RUN_CHECKS_PATH)]
        output_path = workspace / "out" / "findings.json"
        timeout = 30
    else:
        diff_path = workspace / "input.diff"
        output_dir = workspace / "reports"
        database = workspace / "evaluation.db"
        diff_path.write_text(diff, encoding="utf-8")
        command = [
            sys.executable,
            str(RUN_AGENT_PATH),
            "review",
            "--diff-file",
            str(diff_path),
            "--sandbox",
            "container",
            "--dry-run",
            "--model-mode",
            "fake",
            "--db-url",
            f"sqlite+pysqlite:///{database.as_posix()}",
            "--output-dir",
            str(output_dir),
        ]
        output_path = output_dir / "review_report.json"
        timeout = 110
    completed = subprocess.run(
        command,
        cwd=workspace,
        env=_sanitized_environment(workspace),
        check=False,
        capture_output=True,
        encoding="utf-8",
        text=True,
        timeout=timeout,
    )
    duration_ms = int((time.monotonic() - started) * 1000)
    if completed.returncode != 0 or not output_path.is_file():
        raise RuntimeError("trusted_skill_execution_failed")
    return _load_json(output_path), duration_ms


def _finding_keys(payload: Mapping[str, Any]) -> set[tuple[str, int, str]]:
    """提取正式 findings 桶使用的三元组，忽略低置信度候选和运行问题。"""

    findings = payload.get("findings", [])
    if not isinstance(findings, list):
        raise ValueError("trusted_skill_output_invalid")
    keys: set[tuple[str, int, str]] = set()
    for finding in findings:
        if not isinstance(finding, Mapping):
            raise ValueError("trusted_skill_output_invalid")
        confidence = finding.get("confidence")
        if not isinstance(confidence, (int, float)) or confidence < FINDING_CONFIDENCE:
            continue
        file_name = finding.get("file")
        line = finding.get("line")
        category = finding.get("category")
        if not isinstance(file_name, str) or not isinstance(line, int) or not isinstance(category, str):
            raise ValueError("trusted_skill_output_invalid")
        keys.add((file_name, line, category))
    return keys


def _iter_positive_cases(corpus: Mapping[str, Any]) -> Iterable[tuple[str, list[str], int, str, bool]]:
    """展开带标注正样本模板，确保语料规模和六类覆盖可由静态文件审计。"""

    templates = corpus.get("positive_templates")
    if not isinstance(templates, list):
        raise ValueError("evaluation_corpus_invalid")
    for template in templates:
        if not isinstance(template, Mapping):
            raise ValueError("evaluation_corpus_invalid")
        identifier = template.get("id")
        copies = template.get("copies")
        lines = template.get("lines")
        line = template.get("line")
        category = template.get("category")
        scored = template.get("scored")
        if (
            not isinstance(identifier, str)
            or not isinstance(copies, int)
            or not isinstance(lines, list)
            or not all(isinstance(item, str) for item in lines)
            or not isinstance(line, int)
            or not isinstance(category, str)
            or not isinstance(scored, bool)
        ):
            raise ValueError("evaluation_corpus_invalid")
        for index in range(copies):
            yield f"positive_{identifier}_{index + 1}", list(lines), line, category, scored


def _run_fixture_agent(work_root: Path, fixture_name: str, sandbox: str) -> tuple[dict[str, Any], int]:
    """以独立 user-query 子进程审查一条 fixture，并返回不含路径或原始内容的时延与产物摘要。"""

    fixture_root = work_root / f"fixture_{fixture_name}"
    fixture_root.mkdir(parents=True, exist_ok=True)
    output_dir = fixture_root / "reports"
    database = fixture_root / "fixture.db"
    database_url = f"sqlite+pysqlite:///{database.as_posix()}"
    command = [
        sys.executable,
        str(RUN_AGENT_PATH),
        "user-query",
        "请使用 code-review Skill 完成受控代码评审。",
        "--fixture",
        fixture_name,
        "--sandbox",
        sandbox,
        "--dry-run",
        "--model-mode",
        "fake",
        "--db-url",
        database_url,
        "--output-dir",
        str(output_dir),
    ]
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=fixture_root,
            env=_sanitized_environment(fixture_root),
            check=False,
            capture_output=True,
            encoding="utf-8",
            text=True,
            timeout=HARD_LIMIT_MS / 1000,
        )
    except subprocess.TimeoutExpired:
        return (
            {
                "fixture": fixture_name,
                "entrypoint": "agent",
                "skill_tools": [],
                "status": "timeout",
                "duration_ms": int((time.monotonic() - started) * 1000),
                "reports_verified": False,
                "database_verified": False,
            },
            0,
        )

    duration_ms = int((time.monotonic() - started) * 1000)
    try:
        terminal = json.loads(completed.stdout)
    except json.JSONDecodeError:
        terminal = {}
    report_path = output_dir / "review_report.json"
    markdown_path = output_dir / "review_report.md"
    report_files_exist = report_path.is_file() and markdown_path.is_file()
    task_id = terminal.get("task_id") if isinstance(terminal, Mapping) else None
    bundle: Mapping[str, Any] | None = None
    if database.is_file() and isinstance(task_id, str):
        store = SqlReviewStore(database_url)
        try:
            store.initialize()
            bundle = store.get_task_bundle(task_id)
        finally:
            store.close()
    database_verified = bundle is not None
    plaintext_hits = 0
    if report_files_exist:
        report_text = report_path.read_text(encoding="utf-8")
        markdown_text = markdown_path.read_text(encoding="utf-8")
        plaintext_hits += _contains_plaintext_secret(report_text)
        plaintext_hits += _contains_plaintext_secret(markdown_text)
    if database.is_file():
        plaintext_hits += _contains_plaintext_secret(database.read_bytes().decode("utf-8", errors="ignore"))
    if bundle is not None:
        plaintext_hits += _contains_plaintext_secret(json.dumps(bundle, ensure_ascii=False, sort_keys=True))
    plaintext_hits += _contains_plaintext_secret(completed.stdout)
    plaintext_hits += _contains_plaintext_secret(completed.stderr)
    tool_sequence = terminal.get("skill_tools") if isinstance(terminal, Mapping) else []
    if not isinstance(tool_sequence, list) or not all(isinstance(tool, str) for tool in tool_sequence):
        tool_sequence = []
    status = terminal.get("status") if isinstance(terminal, Mapping) else "failed"
    if not isinstance(status, str):
        status = "failed"
    return (
        {
            "fixture": fixture_name,
            "entrypoint": terminal.get("entrypoint") if isinstance(terminal, Mapping) else "unknown",
            "skill_tools": tool_sequence,
            "status": status if completed.returncode == 0 else "failed",
            "duration_ms": duration_ms,
            "reports_verified": report_files_exist,
            "database_verified": database_verified,
        },
        plaintext_hits,
    )


def _fixture_run_passed(run: Mapping[str, Any]) -> bool:
    """判定一条独立 Agent fixture 是否满足工具序列、产物、持久化与单任务时延门禁。"""

    return bool(
        run.get("entrypoint") == "agent"
        and run.get("skill_tools") == ["skill_load", "skill_run"]
        and run.get("status") in {"completed", "completed_with_warnings"}
        and isinstance(run.get("duration_ms"), int)
        and 0 < run["duration_ms"] <= HARD_LIMIT_MS
        and run.get("reports_verified") is True
        and run.get("database_verified") is True
    )


def _run_fixture_suite(work_root: Path, sandbox: str) -> tuple[list[dict[str, Any]], int]:
    """逐条执行八个独立 Agent 审查任务，汇总单条时延而不把总墙钟时间作为 AC6 门禁。"""

    fixture_runs: list[dict[str, Any]] = []
    plaintext_hits = 0
    for fixture_name in FIXTURE_NAMES:
        fixture_run, fixture_plaintext_hits = _run_fixture_agent(work_root, fixture_name, sandbox)
        fixture_runs.append(fixture_run)
        plaintext_hits += fixture_plaintext_hits
    return fixture_runs, plaintext_hits


def _contains_plaintext_secret(value: str) -> int:
    """使用 Skill 同源模式检查一个输出文本是否仍含明文凭据，只返回计数。"""

    secret_rules = load_skill_module("secret_rules")
    return int(secret_rules.contains_secret(value))


def _evaluate_corpus(corpus: Mapping[str, Any], work_root: Path, sandbox: str) -> dict[str, Any]:
    """运行正负/密钥/良性语料，按固定三元组计算公开代理评测指标。"""

    expected: set[tuple[str, int, str]] = set()
    observed: set[tuple[str, int, str]] = set()
    positive_count = 0
    clean_count = 0
    secret_detected = 0
    plaintext_hits = 0

    for case_id, lines, line, category, scored in _iter_positive_cases(corpus):
        path = f"src/{case_id}.py"
        payload, _ = _run_skill_checks(_unified_diff(path, lines), work_root, case_id, sandbox)
        keys = _finding_keys(payload)
        if scored:
            expected.add((path, line, category))
            observed.update(keys)
        positive_count += 1
        plaintext_hits += _contains_plaintext_secret(json.dumps(payload, ensure_ascii=False))

    clean_cases = corpus.get("clean_cases")
    if not isinstance(clean_cases, list):
        raise ValueError("evaluation_corpus_invalid")
    for index, lines in enumerate(clean_cases, start=1):
        if not isinstance(lines, list) or not all(isinstance(item, str) for item in lines):
            raise ValueError("evaluation_corpus_invalid")
        path = f"tests/test_clean_{index}.py"
        payload, _ = _run_skill_checks(_unified_diff(path, lines), work_root, f"clean_{index}", sandbox)
        observed.update(_finding_keys(payload))
        clean_count += 1
        plaintext_hits += _contains_plaintext_secret(json.dumps(payload, ensure_ascii=False))

    secret_case_count = corpus.get("secret_case_count")
    if not isinstance(secret_case_count, int) or secret_case_count < 48:
        raise ValueError("evaluation_corpus_invalid")
    for index in range(secret_case_count):
        path = f"config/secret_{index + 1}.env"
        secret = f"AKIA{index:016X}"
        payload, _ = _run_skill_checks(
            _unified_diff(path, [f"access_key = '{secret}'"]),
            work_root,
            f"secret_{index}",
            sandbox,
        )
        if (path, 1, "secrets") in _finding_keys(payload):
            secret_detected += 1
        plaintext_hits += _contains_plaintext_secret(json.dumps(payload, ensure_ascii=False))

    benign_values = corpus.get("benign_secret_values")
    if not isinstance(benign_values, list) or len(benign_values) < 10:
        raise ValueError("evaluation_corpus_invalid")
    benign_false_positives = 0
    for index, value in enumerate(benign_values, start=1):
        if not isinstance(value, str):
            raise ValueError("evaluation_corpus_invalid")
        payload, _ = _run_skill_checks(
            _unified_diff(f"config/benign_{index}.env", [f"token = '{value}'"]),
            work_root,
            f"benign_{index}",
            sandbox,
        )
        benign_false_positives += int(bool(_finding_keys(payload)))
        plaintext_hits += _contains_plaintext_secret(json.dumps(payload, ensure_ascii=False))

    true_positives = len(expected & observed)
    false_positives = len(observed - expected) + benign_false_positives
    false_negatives = len(expected - observed)
    precision = true_positives / (true_positives + false_positives) if true_positives + false_positives else 1.0
    recall = true_positives / (true_positives + false_negatives) if expected else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "corpus": {
            "positive_cases": positive_count,
            "clean_negative_cases": clean_count,
            "secret_cases": secret_case_count,
            "benign_secret_cases": len(benign_values),
            "blind_spot_cases": 0,
        },
        "metrics": {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "high_risk_recall": recall,
            "finding_false_positive_share": false_positives / len(observed) if observed else 0.0,
            "redaction_detection_rate": secret_detected / secret_case_count,
            "plaintext_hits": plaintext_hits,
            "true_positives": true_positives,
            "false_positives": false_positives,
            "false_negatives": false_negatives,
            "benign_secret_false_positives": benign_false_positives,
        },
    }


def _observe_blind_spots(payload: Mapping[str, Any], work_root: Path, sandbox: str) -> tuple[int, int]:
    """执行声明盲区并仅记录观测数量，绝不并入 AC2 代理门禁分母。"""

    cases = payload.get("cases")
    if not isinstance(cases, list):
        raise ValueError("evaluation_corpus_invalid")
    finding_count = 0
    for case in cases:
        if (
            not isinstance(case, Mapping)
            or not isinstance(case.get("id"), str)
            or not isinstance(case.get("lines"), list)
        ):
            raise ValueError("evaluation_corpus_invalid")
        lines = case["lines"]
        if not all(isinstance(item, str) for item in lines):
            raise ValueError("evaluation_corpus_invalid")
        findings, _ = _run_skill_checks(
            _unified_diff(f"blindspot/{case['id']}.py", lines),
            work_root,
            f"blindspot_{case['id']}",
            sandbox,
        )
        finding_count += len(_finding_keys(findings))
    return len(cases), finding_count


def _boundary_case_count(corpus: Mapping[str, Any]) -> int:
    """校验并统计不参与阈值分母的输入解析边界语料，防止其从评测产物静默消失。"""

    cases = corpus.get("boundary_cases")
    if not isinstance(cases, list) or len(cases) < 8:
        raise ValueError("evaluation_corpus_invalid")
    for case in cases:
        if not isinstance(case, Mapping):
            raise ValueError("evaluation_corpus_invalid")
        if not all(isinstance(case.get(key), str) and case[key] for key in ("id", "payload", "expectation")):
            raise ValueError("evaluation_corpus_invalid")
    return len(cases)


def _write_summary(output_dir: Path, summary: Mapping[str, Any]) -> Path:
    """原子写出不含原始语料和路径的评测摘要，供 CI 和人工审计读取。"""

    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / "eval_summary.json"
    temporary = output_dir / "eval_summary.json.tmp"
    temporary.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(target)
    return target


def _write_history(database: Path, summary: Mapping[str, Any]) -> None:
    """仅在显式请求时向独立 SQLite 写入摘要哈希，避免污染业务 review.db。"""

    if database.name.lower() == "review.db":
        raise ValueError("evaluation_history_database_invalid")
    if database.exists():
        inspection = sqlite3.connect(database)
        try:
            tables = {
                str(row[0])
                for row in inspection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
        finally:
            inspection.close()
        if any(name.startswith("cr_review_") for name in tables):
            raise ValueError("evaluation_history_database_invalid")
    database.parent.mkdir(parents=True, exist_ok=True)
    canonical = json.dumps(summary, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS cr_evaluation_history "
            "(summary_sha256 TEXT PRIMARY KEY, summary_json TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT OR REPLACE INTO cr_evaluation_history (summary_sha256, summary_json) VALUES (?, ?)",
            (hashlib.sha256(canonical.encode("utf-8")).hexdigest(), canonical),
        )
        connection.commit()
    finally:
        connection.close()


def _hard_gates_pass(metrics: Mapping[str, Any], fixture_runs: Sequence[Mapping[str, Any]]) -> bool:
    """按锁定阈值判定离线 CI 门禁；AC6 只约束每条独立 Agent 审查而不约束聚合评测总时长。"""

    return bool(
        len(fixture_runs) == len(FIXTURE_NAMES)
        and all(_fixture_run_passed(run) for run in fixture_runs)
        and metrics.get("high_risk_recall", 0.0) >= 0.80
        and metrics.get("finding_false_positive_share", 1.0) <= 0.15
        and metrics.get("redaction_detection_rate", 0.0) >= 0.95
        and metrics.get("plaintext_hits", 1) == 0
        and metrics.get("benign_secret_false_positives", 1) == 0
    )


def _build_parser() -> argparse.ArgumentParser:
    """构造固定 fake 模型的评测参数，仅允许显式 local 或可选 container 运行时。"""

    parser = argparse.ArgumentParser(description="Offline public-proxy evaluation for the code-review Agent")
    parser.add_argument("--sandbox", choices=("local", "container"), default="local")
    parser.add_argument("--output-dir", type=Path, default=Path("evaluation"))
    parser.add_argument("--write-db", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """执行八条 fixture、公开语料与盲区观测，写摘要并依据硬门禁返回退出码。"""

    args = _build_parser().parse_args(argv)
    configure_safe_logging("WARNING")
    started = time.monotonic()
    try:
        corpus = _load_json(CORPUS_PATH)
        blind_spots = _load_json(BLIND_SPOT_PATH)
        boundary_cases = _boundary_case_count(corpus)
        with tempfile.TemporaryDirectory(
            prefix="code-review-evaluate-",
            ignore_cleanup_errors=True,
        ) as temporary_directory:
            work_root = Path(temporary_directory)
            fixture_runs, fixture_plaintext_hits = _run_fixture_suite(work_root, args.sandbox)
            corpus_result = _evaluate_corpus(corpus, work_root, args.sandbox)
            blind_spot_cases, blind_spot_findings = _observe_blind_spots(blind_spots, work_root, args.sandbox)
        duration_ms = int((time.monotonic() - started) * 1000)
        metrics = dict(corpus_result["metrics"])
        metrics["plaintext_hits"] = int(metrics["plaintext_hits"]) + fixture_plaintext_hits
        corpus_summary = dict(corpus_result["corpus"])
        corpus_summary["blind_spot_cases"] = blind_spot_cases
        corpus_summary["boundary_cases"] = boundary_cases
        summary: dict[str, Any] = {
            "schema_version": "1.0.0",
            "rule_pack_version": "1.0.0",
            "config_digest": ReviewConfig().config_digest,
            "model_mode": "fake",
            "runtime": args.sandbox,
            "python": platform.python_version(),
            "platform": platform.platform(),
            "docker_available": shutil.which("docker") is not None,
            "fixture_summary": {
                "passed": sum(_fixture_run_passed(run) for run in fixture_runs),
                "total": len(FIXTURE_NAMES),
            },
            "fixture_runs": fixture_runs,
            "corpus": corpus_summary,
            "blind_spot_observation": {"findings": blind_spot_findings},
            "metrics": metrics,
            "duration_ms": duration_ms,
            "history": {"enabled": args.write_db is not None},
            "ac2_statement": "公开代理语料仅用于佐证 AC2，不代表官方隐藏样本结果。",
        }
        _write_summary(args.output_dir, summary)
        if args.write_db is not None:
            _write_history(args.write_db, summary)
        hard_gates_passed = _hard_gates_pass(metrics, fixture_runs)
        status = "passed" if hard_gates_passed else "failed"
        print(json.dumps({"status": status}, ensure_ascii=False))
        return 0 if hard_gates_passed else 1
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
        print(json.dumps({"status": "failed", "error": "evaluation_runtime_error"}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
