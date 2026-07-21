"""Phase 6: 审计落盘引擎。"""
from __future__ import annotations
import json, time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from src.baseline import BaselineResult
from src.attribution import AttributionReport
from src.optimizer import OptimizationResult
from src.validator import ValidationResult

@dataclass
class AuditEntry:
    timestamp: str; iteration: int; candidate_id: str
    prompt_type: str; failure_category: str
    prompt_before: str; prompt_after: str
    change_log: list = field(default_factory=list)
    baseline_scores: dict = field(default_factory=dict)
    candidate_scores: dict = field(default_factory=dict)
    gate_accepted: bool = False; gate_reason: str = ""
    gate_checks: list = field(default_factory=list)
    cost_baseline: float = 0.0; cost_candidate: float = 0.0
    latency_ms: float = 0.0; random_seed: int = 42  # fake mode: baseline latency placeholder
    def to_dict(self): return asdict(self)

@dataclass
class AuditTrail:
    pipeline_name: str; run_id: str; started_at: str
    completed_at: str = ""; mode: str = "fake"; random_seed: int = 42
    entries: list = field(default_factory=list)
    total_cost: float = 0.0; avg_latency_ms: float = 0.0  # per-entry average (renamed from total_latency_ms)
    def to_dict(self):
        return {"pipeline_name":self.pipeline_name,"run_id":self.run_id,"started_at":self.started_at,"completed_at":self.completed_at,"mode":self.mode,"random_seed":self.random_seed,"entries":[e.to_dict() for e in self.entries],"total_cost":self.total_cost,"avg_latency_ms":self.avg_latency_ms}

class Auditor:
    def __init__(self, output_dir="output"):
        self.output_dir = Path(output_dir)

    def save(self, audit_trail, baseline, attribution, optimization, validation=None, gate_decision=None):
        ts_dir = audit_trail.run_id
        audit_path = self.output_dir / "audit" / ts_dir
        audit_path.mkdir(parents=True, exist_ok=True)
        full = {"audit_trail":audit_trail.to_dict(),"baseline":{k:v.to_dict() for k,v in baseline.items()},"attribution":attribution.to_dict(),"optimization":optimization.to_dict()}
        if validation: full["validation"] = validation.to_dict()
        if gate_decision: full["gate_decision"] = gate_decision
        with open(audit_path/"optimization_report.json","w",encoding="utf-8") as f:
            json.dump(full,f,ensure_ascii=False,indent=2)
        for entry in audit_trail.entries:
            cd = audit_path / f"candidate_{entry.iteration}"
            cd.mkdir(exist_ok=True)
            (cd/"prompt_before.txt").write_text(entry.prompt_before,"utf-8")
            (cd/"prompt_after.txt").write_text(entry.prompt_after,"utf-8")
            with open(cd/"change_log.json","w",encoding="utf-8") as f:
                json.dump(entry.change_log,f,ensure_ascii=False,indent=2)
        md = self._generate_md(audit_trail, baseline, attribution, optimization, validation, gate_decision)
        (audit_path/"optimization_report.md").write_text(md,"utf-8")
        return audit_path

    def build_trail(self, pipeline_name, mode, random_seed, optimization, baseline_val, validation=None, gate_decision=None, started_at=""):
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S") + f"_{random_seed}"
        entries = []
        for cand in optimization.candidates:
            entry = AuditEntry(
                timestamp=now,
                iteration=cand.iteration,
                candidate_id=cand.candidate_id,
                prompt_type=cand.target_prompt_type,
                failure_category=cand.failure_category,
                prompt_before=cand.prompt_before,
                prompt_after=cand.prompt_after,
                change_log=cand.change_log,
                baseline_scores=baseline_val.score_map if baseline_val else {},
                candidate_scores=validation.score_map if validation else {},
                gate_accepted=gate_decision.get("accepted", False) if gate_decision else False,
                gate_reason=gate_decision.get("reason", "") if gate_decision else "",
                gate_checks=gate_decision.get("checks", []) if gate_decision else [],
                cost_baseline=baseline_val.summary.avg_cost * baseline_val.summary.total if baseline_val else 0.0,
                cost_candidate=validation.summary.total_cost_candidate if validation else 0.0,
                latency_ms=baseline_val.summary.avg_latency_ms if baseline_val else 0.0,
                random_seed=random_seed,
            )
            entries.append(entry)
        return AuditTrail(
            pipeline_name=pipeline_name,
            run_id=run_id,
            started_at=started_at or now,
            completed_at=now,
            mode=mode,
            random_seed=random_seed,
            entries=entries,
            total_cost=sum(e.cost_candidate for e in entries),
            avg_latency_ms=baseline_val.summary.avg_latency_ms if baseline_val else 0.0,
        )

    @staticmethod
    def _generate_md(audit_trail, baseline, attribution, optimization, validation, gate_decision):
        L = []
        w = L.append
        w("# Optimization Report\n")
        w(f"**Pipeline**: {audit_trail.pipeline_name} | **Run**: {audit_trail.run_id}\n")
        w(f"**Mode**: {audit_trail.mode} | **Seed**: {audit_trail.random_seed}\n\n")
        w("## 1. Baseline Evaluation\n")
        for name in ("train","val"):
            r = baseline.get(name)
            if r is None: continue
            w(f"### {name}\n")
            w(f"Pass Rate: {r.summary.pass_rate:.1%} ({r.summary.passed}/{r.summary.total}) | Avg Score: {r.summary.avg_score:.3f}\n\n")
            for c in r.cases:
                st = "PASS" if c.passed else "FAIL"
                w(f"- [{st}] {c.case_id}: {c.ground_truth} -> {c.predicted} (score={c.score:.3f})\n")
            w("\n")
        w("## 2. Failure Attribution\n")
        w(f"Failures: {attribution.total_failures} (train:{attribution.train_failures}, val:{attribution.val_failures})\n\n")
        for cl in attribution.clusters:
            w(f"- **{cl.category}**: {cl.count} cases, conf={cl.avg_confidence:.2f} -> optimize {cl.prompt_target}\n")
        w("\n## 3. Optimization\n")
        for cand in optimization.candidates:
            w(f"### Candidate {cand.iteration}\n")
            w(f"- Target: `{cand.target_prompt_type}` | Category: `{cand.failure_category}`\n")
            for cl in cand.change_log:
                w(f"  - {cl}\n")
            w("\n")
        if validation and validation.delta_cases:
            w("## 4. Candidate Validation\n")
            for d in validation.delta_cases:
                w(f"- {d.case_id}: {d.baseline_score:.3f} -> {d.candidate_score:.3f} ({d.score_delta:+.3f}) [{d.status}]\n")
            w(f"\nSummary: improved={validation.summary.improved} regressed={validation.summary.regressed}\n\n")
        if gate_decision:
            w("## 5. Gate Decision\n")
            w(f"**Accepted**: {gate_decision.get('accepted',False)}\n")
            w(f"**Reason**: {gate_decision.get('reason','')}\n\n")
        w(f"## 6. Audit\n\n- Total Cost: ${audit_trail.total_cost:.6f}\n- Run ID: `{audit_trail.run_id}`\n")
        return "".join(L)
