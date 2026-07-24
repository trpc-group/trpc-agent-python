from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from examples.optimization.eval_optimize_loop.pipeline.comparator import compare_case
from examples.optimization.eval_optimize_loop.pipeline.config import canonical_sha256, load_pipeline_config
from examples.optimization.eval_optimize_loop.pipeline.gate import evaluate_gate
from examples.optimization.eval_optimize_loop.pipeline.normalization import parse_fake_response
from examples.optimization.eval_optimize_loop.pipeline.models import (
    CandidateReport,
    CaseSnapshot,
    GateSettings,
    OptimizationReport,
    SplitReport,
)
from examples.optimization.eval_optimize_loop.pipeline.reporter import write_reports


def _case(eval_id: str, *, passed: bool, score: float) -> CaseSnapshot:
    return CaseSnapshot(
        eval_id=eval_id,
        split="validation",
        run_count=1,
        passed=passed,
        hard_failed=False,
        aggregate_score=score,
        metric_scores={"final_response_avg_score": score},
        metric_thresholds={"final_response_avg_score": 1.0},
        metric_passed={"final_response_avg_score": passed},
        trace_digest="sha256:test",
    )


def test_strict_case_snapshot_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        CaseSnapshot(
            eval_id="val_refund_critical",
            split="validation",
            run_count=1,
            passed=True,
            hard_failed=False,
            aggregate_score=1.0,
            metric_scores={},
            metric_thresholds={},
            metric_passed={},
            trace_digest="sha256:test",
            unsupported=True,
        )


def test_canonical_digest_is_order_independent() -> None:
    assert canonical_sha256({"b": 2, "a": 1}) == canonical_sha256({"a": 1, "b": 2})


def test_fake_config_loads_without_live_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config_path = tmp_path / "optimizer.json"
    config_path.write_text(json.dumps({"pipeline": {"reproducibility": {"seed": 42}}}), encoding="utf-8")
    monkeypatch.delenv("TRPC_AGENT_API_KEY", raising=False)
    assert load_pipeline_config(config_path, mode="fake").pipeline.reproducibility.seed == 42


def test_critical_pass_to_fail_is_hard_regression() -> None:
    delta = compare_case(
        _case("val_refund_critical", passed=True, score=1.0),
        _case("val_refund_critical", passed=False, score=0.0),
        epsilon=1e-6,
        critical_case_ids={"val_refund_critical"},
    )
    assert delta.transition == "REGRESSION"
    assert delta.hard_fail_added is True


def test_gate_rejects_validation_critical_regression() -> None:
    baseline = SplitReport.from_cases([_case("val_refund_critical", passed=True, score=1.0)])
    candidate = SplitReport.from_cases([_case("val_refund_critical", passed=False, score=0.0)])
    decision = evaluate_gate(
        baseline,
        candidate,
        settings=GateSettings(critical_case_ids=["val_refund_critical"]),
        case_deltas=[compare_case(baseline.cases[0], candidate.cases[0], epsilon=1e-6, critical_case_ids={"val_refund_critical"})],
    )
    assert decision.accepted is False
    assert any(rule.rule == "no_critical_regression" and not rule.passed for rule in decision.rules)


def test_reporter_writes_json_and_markdown(tmp_path: Path) -> None:
    report = OptimizationReport.empty(mode="fake", seed=42)
    report.candidates.append(CandidateReport(candidate_id="candidate_noop", accepted=False, reasons=["no improvement"]))
    json_path, markdown_path = write_reports(report, tmp_path)
    assert json.loads(json_path.read_text(encoding="utf-8"))["schema_version"] == "1.0"
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "Selected candidate" in markdown
    assert "Baseline" in markdown
    assert "Validation deltas" in markdown
    assert "Gate rules" in markdown


def test_fake_response_parser_preserves_tool_name_and_arguments() -> None:
    snapshot = parse_fake_response(
        '{"route":"order_lookup","tool":"lookup_order","arguments":{"order_id":"A100"},'
        '"answer":"正在查询订单 A100。"}'
    )
    assert snapshot.tool_calls[0].name == "lookup_order"
    assert snapshot.tool_calls[0].arguments == {"order_id": "A100"}
