# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under the Apache License, Version 2.0.
"""Repository-level Stage 6 sample-output acceptance tests."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from examples.optimization.eval_optimize_loop.schemas import ArtifactIndex
from examples.optimization.eval_optimize_loop.schemas import OptimizationReport


_REPO_ROOT = Path(__file__).resolve().parents[2]
_SAMPLE = (
    _REPO_ROOT
    / "examples"
    / "optimization"
    / "eval_optimize_loop"
    / "sample_output"
)


def test_sample_output_is_schema_valid_and_self_consistent():
    report_path = _SAMPLE / "optimization_report.json"
    markdown_path = _SAMPLE / "optimization_report.md"
    index_path = _SAMPLE / "artifact_index.json"
    report = OptimizationReport.model_validate_json(
        report_path.read_text(encoding="utf-8")
    )
    index = ArtifactIndex.model_validate_json(index_path.read_text(encoding="utf-8"))

    assert report.execution_mode == "offline"
    assert report.gate_decision.decision == "accept"
    assert index.run_id == report.run_id
    assert report.run_id in markdown_path.read_text(encoding="utf-8")
    for reference in index.artifacts:
        if reference.status != "available":
            continue
        path = _SAMPLE / reference.relative_path  # type: ignore[arg-type]
        payload = path.read_bytes()
        assert len(payload) == reference.size_bytes
        assert sha256(payload).hexdigest() == reference.sha256


def test_sample_output_does_not_contain_credentials_or_machine_paths():
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            _SAMPLE / "optimization_report.json",
            _SAMPLE / "optimization_report.md",
            _SAMPLE / "artifact_index.json",
        )
    )
    assert "/home/" not in combined
    assert "TRPC_AGENT_API_KEY" not in combined
    assert "TRPC_AGENT_BASE_URL" not in combined
