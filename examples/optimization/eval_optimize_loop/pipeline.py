# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Eval -> Attribute -> Optimize -> Validate -> Gate -> Audit 闭环编排。

入口：EvalLoopPipeline.run()，返回结构化报告 dict；run_pipeline.py 负责把它
落盘成 optimization_report.json / .md 并写审计产物。

无需任何 API Key：评测走 call_agent 黑盒 + 确定性 fake_agent；优化走
RuleBasedOptimizer（与 GEPA 等价的确定性扩展机制）；judge 用框架内置的
final_response_avg_score(contains) 做确定性匹配。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Optional

from trpc_agent_sdk.evaluation import AgentEvaluator
from trpc_agent_sdk.evaluation import EvalConfig
from trpc_agent_sdk.evaluation import TargetPrompt
from trpc_agent_sdk.evaluation._eval_metrics import EvalStatus
from trpc_agent_sdk.evaluation._eval_set import EvalSet

import fake_agent
import real_call_agent
from attribution import analyze_set
from attribution import classify
from attribution import classify_real
from attribution import summarize_types
from gate import GateConfig
from gate import evaluate_gate
from optimizer import RuleBasedOptimizer


def _rate(pass_map: dict) -> float:
    if not pass_map:
        return 0.0
    return sum(1 for v in pass_map.values() if v) / len(pass_map)


@dataclass
class CandidateRecord:
    label: str
    prompt: str
    train_pass_map: dict = field(default_factory=dict)
    val_pass_map: dict = field(default_factory=dict)
    train_pass_rate: float = 0.0
    val_pass_rate: float = 0.0
    train_score_map: dict = field(default_factory=dict)
    val_score_map: dict = field(default_factory=dict)
    attributions: list = field(default_factory=list)
    gate: object = None  # GateDecision


class EvalLoopPipeline:
    """评测 - 失败归因 - 优化 - 回归验证 - 接受策略 - 产物审计 的自动闭环。"""

    def __init__(
        self,
        optimizer_path: str,
        baseline_prompt_path: str,
        train_path: str,
        val_path: str,
        output_dir: str,
        seed: int = 42,
        backend: str = "fake",
    ) -> None:
        self.optimizer_path = Path(optimizer_path)
        self.baseline_prompt_path = Path(baseline_prompt_path)
        self.train_path = Path(train_path)
        self.val_path = Path(val_path)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.backend = backend

        # 选择 call_agent / set_prompt / 分类函数（fake 与 real 两套契约一致）。
        if backend == "real":
            self._call_agent = real_call_agent.call_agent
            self._set_prompt = real_call_agent.set_prompt
            self._classify = classify_real
        else:
            self._call_agent = fake_agent.call_agent
            self._set_prompt = fake_agent.set_prompt
            self._classify = classify

        cfg = json.loads(self.optimizer_path.read_text(encoding="utf-8"))
        self.eval_cfg_dict = cfg["evaluate"]
        self.optimize_cfg = cfg["optimize"]
        self.eval_config = EvalConfig.model_validate_json(json.dumps(self.eval_cfg_dict))
        self.gate_cfg = GateConfig(**self.optimize_cfg.get("gate", {}))
        self.candidates = self.optimize_cfg.get("candidates", [])
        self.seed = self.optimize_cfg.get("seed", seed)
        self.budget = self.optimize_cfg.get("budget", {})

        self.baseline_text = self.baseline_prompt_path.read_text(encoding="utf-8")
        self.target_prompt = TargetPrompt().add_path("system_prompt", str(self.baseline_prompt_path))

    # ---- 评测 ----------------------------------------------------------
    def _load_eval_set(self, path: Path) -> EvalSet:
        return EvalSet.model_validate_json(path.read_text(encoding="utf-8"))

    async def _eval(self, prompt_text: str, eval_set: EvalSet):
        """评估某个 prompt 在某 eval_set 上，返回 (pass_map, score_map, results_by_id, reasons_by_id)。"""
        self._set_prompt(prompt_text)
        # 真实模式下串行评估以降低对 hy3 的并发压力，规避 429 限流。
        case_parallelism = 1 if self.backend == "real" else None
        case_eval_parallelism = 1 if self.backend == "real" else None
        _failed, _details, _lines, results_by_id = await AgentEvaluator.evaluate_eval_set(
            eval_set,
            call_agent=self._call_agent,
            eval_config=self.eval_config,
            num_runs=1,
            print_detailed_results=False,
            case_parallelism=case_parallelism,
            case_eval_parallelism=case_eval_parallelism,
        )
        pass_map: dict[str, bool] = {}
        score_map: dict[str, float] = {}
        reasons_by_id: dict[str, str] = {}
        for eval_id, results in results_by_id.items():
            r = results[0]
            passed = r.final_eval_status == EvalStatus.PASSED
            pass_map[eval_id] = passed
            scores = [m.score for m in r.overall_eval_metric_results if m.score is not None]
            score_map[eval_id] = (sum(scores) / len(scores)) if scores else (1.0 if passed else 0.0)
            # judge 的可解释原因：优先取逐 invocation 的 metric details.reason，
            # 回退到 overall metric details。
            reason = ""
            if r.eval_metric_result_per_invocation:
                for m in r.eval_metric_result_per_invocation[0].eval_metric_results:
                    if m.details and m.details.reason:
                        reason = m.details.reason
                        break
            if not reason and r.overall_eval_metric_results:
                det = r.overall_eval_metric_results[0].details
                reason = (det.reason if det else None) or ""
            reasons_by_id[eval_id] = reason or ""
        return pass_map, score_map, results_by_id, reasons_by_id

    # ---- 主流程 --------------------------------------------------------
    async def run(self) -> dict:
        started = datetime.now(timezone.utc).isoformat()
        t0 = time.time()

        train_set = self._load_eval_set(self.train_path)
        val_set = self._load_eval_set(self.val_path)

        # 阶段 1：Baseline 评测（训练集 + 验证集）
        base_tr_pass, base_tr_score, base_tr_res, base_tr_reason = await self._eval(self.baseline_text, train_set)
        base_va_pass, base_va_score, base_va_res, base_va_reason = await self._eval(self.baseline_text, val_set)

        # 阶段 2：失败归因（baseline）
        base_tr_attr = analyze_set(train_set, base_tr_res, classify_fn=self._classify, reason_by_id=base_tr_reason)
        base_va_attr = analyze_set(val_set, base_va_res, classify_fn=self._classify, reason_by_id=base_va_reason)
        base_attr_summary = summarize_types(base_tr_attr + base_va_attr)

        # 阶段 3：优化执行 —— 对每个候选在训练集评测生成候选
        optimizer = RuleBasedOptimizer(self.baseline_text, self.candidates, self.target_prompt)
        candidate_records: list[CandidateRecord] = []
        for label, prompt in optimizer.propose():
            # baseline 已在阶段 1 评过，候选循环里跳过它，避免冗余调用（也规避重评时的偶发限流脏数据）。
            if label == "baseline":
                continue
            tr_pass, tr_score, tr_res, tr_reason = await self._eval(prompt, train_set)
            va_pass, va_score, va_res, va_reason = await self._eval(prompt, val_set)
            attr = analyze_set(train_set, tr_res, base_pass_map=base_tr_pass,
                               classify_fn=self._classify, reason_by_id=tr_reason) + \
                   analyze_set(val_set, va_res, base_pass_map=base_va_pass,
                               classify_fn=self._classify, reason_by_id=va_reason)
            gate_decision = evaluate_gate(base_va_pass, va_pass, self.gate_cfg)
            candidate_records.append(CandidateRecord(
                label=label, prompt=prompt,
                train_pass_map=tr_pass, val_pass_map=va_pass,
                train_pass_rate=_rate(tr_pass), val_pass_rate=_rate(va_pass),
                train_score_map=tr_score, val_score_map=va_score,
                attributions=attr, gate=gate_decision,
            ))

        # 阶段 4/5：挑选被接受的候选中验证集最优者
        accepted = [r for r in candidate_records if r.label != "baseline" and r.gate.accept]
        best = max(accepted, key=lambda r: (r.val_pass_rate, r.train_pass_rate)) if accepted else None
        accepted_label = best.label if best else None

        duration = time.time() - t0
        finished = datetime.now(timezone.utc).isoformat()

        report = self._build_report(
            started, finished, duration,
            base_tr_pass, base_tr_score, base_va_pass, base_va_score,
            base_tr_attr, base_va_attr, base_attr_summary,
            candidate_records, accepted_label,
        )
        return report

    # ---- 报告构建 ------------------------------------------------------
    def _case_block(self, pass_map, score_map):
        return {eid: {"pass": bool(p), "score": round(score_map.get(eid, 0.0), 4)}
                for eid, p in pass_map.items()}

    def _gate_to_dict(self, gate):
        return {
            "accept": gate.accept,
            "val_score_before": round(gate.val_score_before, 4),
            "val_score_after": round(gate.val_score_after, 4),
            "improvement": round(gate.improvement, 4),
            "reasons": gate.reasons,
            "case_deltas": [
                {"eval_id": d.eval_id, "baseline_pass": d.baseline_pass,
                 "candidate_pass": d.candidate_pass, "delta": d.delta}
                for d in gate.case_deltas
            ],
        }

    def _attr_to_dict(self, attrs):
        return [
            {"eval_id": a.eval_id, "query": a.query, "passed": a.passed,
             "failure_type": a.failure_type, "reason": a.reason,
             "regression": a.regression, "actual_excerpt": a.actual_excerpt}
            for a in attrs
        ]

    def _build_report(self, started, finished, duration,
                      base_tr_pass, base_tr_score, base_va_pass, base_va_score,
                      base_tr_attr, base_va_attr, base_attr_summary,
                      candidate_records, accepted_label):
        candidates_out = []
        for r in candidate_records:
            candidates_out.append({
                "label": r.label,
                "prompt": r.prompt,
                "train_pass_rate": round(r.train_pass_rate, 4),
                "val_pass_rate": round(r.val_pass_rate, 4),
                "train_cases": self._case_block(r.train_pass_map, r.train_score_map),
                "val_cases": self._case_block(r.val_pass_map, r.val_score_map),
                "gate": self._gate_to_dict(r.gate),
                "attributions": self._attr_to_dict(r.attributions),
            })

        best = next((r for r in candidate_records if r.label == accepted_label), None)
        delta = None
        if best is not None:
            delta = {
                "train_pass_rate": round(best.train_pass_rate - _rate(base_tr_pass), 4),
                "val_pass_rate": round(best.val_pass_rate - _rate(base_va_pass), 4),
                "per_case_val": {
                    eid: {
                        "baseline_pass": bool(base_va_pass.get(eid, False)),
                        "candidate_pass": bool(best.val_pass_map.get(eid, False)),
                    } for eid in sorted(set(base_va_pass) | set(best.val_pass_map))
                },
            }

        report = {
            "meta": {
                "seed": self.seed,
                "started_at": started,
                "finished_at": finished,
                "duration_seconds": round(duration, 3),
                "config_path": str(self.optimizer_path),
                "baseline_prompt_path": str(self.baseline_prompt_path),
                "train_path": str(self.train_path),
                "val_path": str(self.val_path),
                "budget": self.budget,
                "mode": ("real_model (hy3) + real_judge (llm_rubric_response via hy3) + "
                         "call_agent" if self.backend == "real"
                         else "fake_model + fake_judge + call_agent (no API key required)"),
            },
            "baseline": {
                "train_pass_rate": round(_rate(base_tr_pass), 4),
                "val_pass_rate": round(_rate(base_va_pass), 4),
                "train_cases": self._case_block(base_tr_pass, base_tr_score),
                "val_cases": self._case_block(base_va_pass, base_va_score),
                "failure_attribution": base_attr_summary,
                "attributions": self._attr_to_dict(base_tr_attr + base_va_attr),
            },
            "candidates": candidates_out,
            "accepted_candidate": accepted_label,
            "delta": delta,
            "gate_decision": self._gate_to_dict(best.gate) if best else None,
            "rejection_summary": [
                {"label": r.label, "val_pass_rate": round(r.val_pass_rate, 4),
                 "reasons": r.gate.reasons}
                for r in candidate_records
                if r.label != "baseline" and not r.gate.accept
            ],
        }
        return report
