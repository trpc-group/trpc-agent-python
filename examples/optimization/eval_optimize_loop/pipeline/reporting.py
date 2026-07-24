"""报告生成：optimization_report.json + .md + audit snapshots。原子写盘。"""
from __future__ import annotations

import json
import platform
import sys
from pathlib import Path

from .models import CandidateResult, OptimizationReport

SCHEMA_VERSION = "eval_optimize_loop.v1"


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _write_json(path: Path, obj: object) -> None:
    _atomic_write(path, json.dumps(obj, sort_keys=True, ensure_ascii=False, indent=2))


def _sdk_version() -> str:
    try:
        import trpc_agent_sdk

        return getattr(trpc_agent_sdk, "__version__", "unknown")
    except Exception:
        return "unknown"


def derive_status(candidates: list[CandidateResult], selected_id: str | None) -> str:
    if selected_id:
        return "accept"
    if any(c.gate.decision == "needs_review" for c in candidates):
        return "needs_review"
    return "reject"


def build_report(
    *,
    mode: str,
    seed: int,
    baseline,
    candidates: list[CandidateResult],
    selected_id: str | None,
    failure_attribution,
    optimizer,
    data_quality,
    audit,
) -> OptimizationReport:
    status = derive_status(candidates, selected_id)
    return OptimizationReport(
        schema_version=SCHEMA_VERSION,
        status=status,  # type: ignore[arg-type]
        mode=mode,  # type: ignore[arg-type]
        seed=seed,
        baseline=baseline,
        candidates=candidates,
        selected_candidate_id=selected_id,
        failure_attribution=failure_attribution,
        optimizer=optimizer,
        data_quality=data_quality,
        audit=audit,
    )


def write_outputs(report: OptimizationReport, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    audit_dir = output_dir / "audit"
    audit_dir.mkdir(exist_ok=True)

    _write_json(output_dir / "optimization_report.json", report.model_dump())
    (output_dir / "optimization_report.md").write_text(_render_md(report), encoding="utf-8")

    _write_json(
        audit_dir / "input.snapshot.json",
        {
            "config_sha256": report.audit.config_sha256,
            "train_sha256": report.audit.train_sha256,
            "validation_sha256": report.audit.validation_sha256,
            "baseline_prompt_sha256": report.audit.baseline_prompt_sha256,
        },
    )
    _write_json(
        audit_dir / "environment.snapshot.json",
        {
            "sdk_version": _sdk_version(),
            "python_version": sys.version.split()[0],
            "platform": platform.platform(),
            "mode": report.mode,
            "seed": report.seed,
        },
    )
    _write_json(
        audit_dir / "gate_decisions.json",
        {
            "selected_candidate_id": report.selected_candidate_id,
            "candidates": {
                c.candidate_id: c.gate.model_dump()
                for c in report.candidates
            },
        },
    )
    _write_json(
        audit_dir / "proposals.json",
        {
            c.candidate_id: {
                "source": c.source,
                "prompts": c.prompts,
                "sha256": c.audit_prompt_sha256,
            }
            for c in report.candidates
        },
    )


def _render_md(report: OptimizationReport) -> str:
    out: list[str] = []
    out.append("# Evaluation + Optimization 闭环报告\n")
    out.append(f"- **状态**: `{report.status}`  |  **模式**: `{report.mode}`"
               f"  |  **seed**: {report.seed}  |  **schema**: {report.schema_version}")
    out.append(
        f"- **选中候选**: `{report.selected_candidate_id or '(无 — 全部被拒绝)'}`  |  **耗时**: {report.audit.duration_seconds}s\n")

    out.append("## 1. Baseline\n")
    out.append("| split | pass_rate | average_score |")
    out.append("|---|---|---|")
    out.append(f"| train | {report.baseline.train.pass_rate:.2f} | {report.baseline.train.average_score:.2f} |")
    val = report.baseline.validation
    out.append(f"| validation | {val.pass_rate:.2f} | {val.average_score:.2f} |\n")

    fa = report.failure_attribution
    out.append("## 2. 失败归因\n")
    out.append(
        f"覆盖 **{fa.explained_failed_cases}/{fa.total_failed_cases}** 失败 case（coverage = {fa.coverage_rate:.0%}）。\n")
    if fa.category_counts:
        out.append("| 类别 | 数量 |")
        out.append("|---|---|")
        for k, v in sorted(fa.category_counts.items(), key=lambda kv: -kv[1]):
            out.append(f"| `{k}` | {v} |")
        out.append("")

    out.append("## 3. 候选决策\n")
    out.append("| candidate | train Δpr | val Δpr | overfit? | gate | risk |")
    out.append("|---|---|---|---|---|---|")
    for c in report.candidates:
        mark = "✅" if c.gate.accepted else "❌"
        out.append(f"| {c.candidate_id} | {c.delta.train.pass_rate_delta:+.2f} | "
                   f"{c.delta.validation.pass_rate_delta:+.2f} | "
                   f"{'是' if c.gate.overfitting_detected else '否'} | "
                   f"{mark} **{c.gate.decision}** | {c.gate.risk_level} |")
    out.append("")

    for c in report.candidates:
        if c.gate.accepted:
            continue
        out.append(f"### `{c.candidate_id}` 拒绝/复核理由")
        for chk in c.gate.checks:
            flag = "✅" if chk.passed else "❌"
            out.append(f"- {flag} **{chk.check}**: {chk.reason}")
        out.append("")

    out.append("## 4. 审计\n")
    out.append(f"- config_sha256: `{report.audit.config_sha256}`")
    out.append(f"- train_sha256: `{report.audit.train_sha256}`")
    out.append(f"- validation_sha256: `{report.audit.validation_sha256}`")
    out.append(f"- cost_measurement: `{report.audit.cost.measurement}`\n")
    out.append("## 5. 复现\n")
    out.append(f"```\n{report.audit.command}\n```")
    return "\n".join(out) + "\n"
