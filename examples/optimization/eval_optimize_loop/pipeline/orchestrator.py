# -*- coding: utf-8 -*-
# Copyright @ 2025 Tencent.com
"""Pipeline orchestrator — 6 stages: Baseline → Attribution → Optimize → Validate → Gate → Report."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from .config import (
    BASELINE_PRESETS,
    FailureReport,
    GateConfig,
    PipelineConfig,
    PipelineContext,
)
from .evaluator import run_evaluation
from .attributor import analyze as attr_analyze
from .comparator import compare
from .gate import decide
from .reporter import generate


def _apply_baseline_preset(
    ctx: PipelineContext, pconfig: PipelineConfig
) -> tuple[str, str]:
    """Apply the `baseline_prompt_preset` from pconfig to disk.

    Returns (original_system, original_skill) snapshot so the caller can
    restore prompts after the pipeline completes (or after Gate rejects).
    """
    _prompts_dir = ctx.project_dir / "agent" / "prompts"
    _system_path = _prompts_dir / "system.md"
    _skill_path = _prompts_dir / "skill.md"

    # Snapshot current prompts (usually the "original" baseline but may have
    # been altered by a prior run).
    orig_sys = _system_path.read_text(encoding="utf-8").strip()
    orig_sk = _skill_path.read_text(encoding="utf-8").strip()

    preset_name = pconfig.baseline_prompt_preset
    if ctx.is_trace_mode or preset_name in (None, "", "original"):
        # Trace mode uses pre-recorded actual_conversations; prompt changes
        # have no effect. Similarly "original" → no-op.
        return orig_sys, orig_sk

    preset = BASELINE_PRESETS.get(preset_name)
    if preset is None:
        raise ValueError(
            f"Unknown baseline_prompt_preset='{preset_name}'. "
            f"Valid options: {sorted(BASELINE_PRESETS)}"
        )

    # Backup files for manual inspection (useful to diff after a run).
    out = Path(ctx.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(
            _system_path, out / "system_prompt_before_preset.md"
        )
        shutil.copy2(
            _skill_path, out / "skill_prompt_before_preset.md"
        )
    except Exception:
        pass

    _system_path.write_text(preset["system"].strip() + "\n", encoding="utf-8")
    _skill_path.write_text(preset["skill"].strip() + "\n", encoding="utf-8")

    print(f"  [Preset] Applied baseline_prompt_preset='{preset_name}'")
    print(f"    system.md: {len(preset['system'])} chars; "
          f"skill.md: {len(preset['skill'])} chars")
    return orig_sys, orig_sk


async def run_pipeline(
    ctx: PipelineContext,
    gate_cfg: GateConfig,
    pconfig: PipelineConfig | None = None,
) -> PipelineContext:
    """Execute all 6 pipeline stages.

    Args:
        ctx: Shared pipeline context (paths + in-progress results).
        gate_cfg: Acceptance gate thresholds.
        pconfig: Optional top-level PipelineConfig; when provided its
            `baseline_prompt_preset` is applied to the prompt files on
            disk BEFORE Stage 1 runs, so a strong LLM (deepseek, etc.)
            can be FORCED to produce diverse failure types.
    """
    # ─── Prompt file paths (used by both trace & live) ───────────
    _prompts_dir = ctx.project_dir / "agent" / "prompts"
    _system_path = _prompts_dir / "system.md"
    _skill_path = _prompts_dir / "skill.md"

    # ─── Stage 0: Apply baseline prompt preset (live mode only) ──
    #       Rewrites system.md / skill.md with intentionally-defective
    #       instructions so the subsequent baseline evaluation produces
    #       a DIVERSE failure-attribution mix.
    preset_restored = {"done": False}
    try:
        if pconfig is not None:
            print("\n[Stage 0/6] Apply baseline prompt preset...")
            orig_sys, orig_sk = _apply_baseline_preset(ctx, pconfig)
        else:
            orig_sys, orig_sk = (
                _system_path.read_text(encoding="utf-8").strip(),
                _skill_path.read_text(encoding="utf-8").strip(),
            )

        def _restore_original_prompts_if_needed() -> None:
            """Best-effort restore: don't leave defective prompts on disk."""
            if preset_restored["done"]:
                return
            try:
                _system_path.write_text(orig_sys + "\n", encoding="utf-8")
                _skill_path.write_text(orig_sk + "\n", encoding="utf-8")
                preset_restored["done"] = True
            except Exception:
                pass

        # ─── Config selection ────────────────────────────────────
        if ctx.is_trace_mode:
            eval_config = str(Path(ctx.project_dir) / "agent" / "test_config_trace.json")
            eval_kwargs: dict = {}
        else:
            eval_config = str(Path(ctx.project_dir) / "agent" / "test_config.json")
            eval_kwargs = {"agent_module": "agent"}

        # ═══════════════════════════════════════════════════════════
        # Stage 1: Baseline Evaluation
        # ═══════════════════════════════════════════════════════════
        t0 = time.time()
        print("\n[Stage 1/6] Baseline Evaluation...")

        ctx.baseline_train = await run_evaluation(
            evalset_path=ctx.train_path,
            eval_config_path=eval_config,
            print_results=False,
            **eval_kwargs,
        )
        print(f"  Train: pass_rate={ctx.baseline_train.pass_rate:.2%} "
              f"({sum(1 for c in ctx.baseline_train.per_case if c.overall_status == 'PASSED')}"
              f"/{len(ctx.baseline_train.per_case)} cases)")

        ctx.baseline_val = await run_evaluation(
            evalset_path=ctx.val_path,
            eval_config_path=eval_config,
            print_results=False,
            **eval_kwargs,
        )
        print(f"  Val:   pass_rate={ctx.baseline_val.pass_rate:.2%} "
              f"({sum(1 for c in ctx.baseline_val.per_case if c.overall_status == 'PASSED')}"
              f"/{len(ctx.baseline_val.per_case)} cases)")

        ctx.stage_timings["baseline"] = time.time() - t0

        # ═══════════════════════════════════════════════════════════
        # Stage 2: Failure Attribution
        # ═══════════════════════════════════════════════════════════
        t0 = time.time()
        print("\n[Stage 2/6] Failure Attribution...")

        train_failures = attr_analyze(ctx.baseline_train)
        val_failures = attr_analyze(ctx.baseline_val)
        combined = FailureReport(
            total_failures=train_failures.total_failures + val_failures.total_failures,
            by_type={},
            per_case=train_failures.per_case + val_failures.per_case,
        )
        for ft, n in train_failures.by_type.items():
            combined.by_type[ft] = combined.by_type.get(ft, 0) + n
        for ft, n in val_failures.by_type.items():
            combined.by_type[ft] = combined.by_type.get(ft, 0) + n
        ctx.failure_report = combined

        print(f"  Failures: {combined.total_failures}")
        for ft, n in sorted(combined.by_type.items(), key=lambda x: -x[1]):
            print(f"    {ft}: {n}")
        ctx.stage_timings["attribution"] = time.time() - t0

        # ═══════════════════════════════════════════════════════════
        # Stage 3: Optimization
        # ═══════════════════════════════════════════════════════════
        t0 = time.time()
        print("\n[Stage 3/6] Optimization...")
        baseline_system = _system_path.read_text(encoding="utf-8").strip()
        baseline_skill = _skill_path.read_text(encoding="utf-8").strip()
        ctx._baseline_system_snapshot = baseline_system
        ctx._baseline_skill_snapshot = baseline_skill

        if ctx.is_trace_mode:
            # ── Trace: diagnose from training failures → targeted fix ──
            diag_parts = []
            if ctx.failure_report:
                mi_count = ctx.failure_report.by_type.get("missing_information", 0)
                re_count = ctx.failure_report.by_type.get("reasoning_failure", 0)
                if mi_count > 0:
                    diag_parts.append(
                        f"训练集 {mi_count} 个 case 存在信息遗漏: "
                        "Agent 调用了正确工具但回答未覆盖所有子问题。"
                    )
                if re_count > 0:
                    diag_parts.append(
                        f"训练集 {re_count} 个 case 存在推理不足: "
                        "需跨值计算/比较, Prompt 无法修复。"
                    )
            diagnosis = "; ".join(diag_parts) if diag_parts else "训练集全部通过，无需优化。"
            print(f"  Diagnosis: {diagnosis}")

            optimized_system = baseline_system
            optimized_skill = baseline_skill
            if "信息遗漏" in diagnosis:
                optimized_system = (
                    baseline_system + "\n\n"
                    "重要：回答用户问题时，必须覆盖用户提出的每一个子问题。"
                    "如果用户同时问了价格和库存，你的回答必须包含两者。"
                )
            print(f"  Baseline prompt ({len(baseline_system)} chars)"
                  f" → Optimized prompt ({len(optimized_system)} chars)")

            from trpc_agent_sdk.evaluation._optimize_result import OptimizeResult
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).isoformat()
            ctx.optimize_result = OptimizeResult(
                algorithm="trace_simulated_gepa",
                status="SUCCEEDED",
                finish_reason="completed",
                stop_reason="completed",
                baseline_pass_rate=ctx.baseline_val.pass_rate,
                best_pass_rate=ctx.baseline_val.pass_rate,
                pass_rate_improvement=0.0,
                baseline_metric_breakdown=ctx.baseline_val.metric_breakdown,
                best_metric_breakdown=ctx.baseline_val.metric_breakdown,
                metric_thresholds={"final_response_avg_score": 0.6},
                baseline_prompts={"system_prompt": baseline_system, "skill": baseline_skill},
                best_prompts={"system_prompt": optimized_system, "skill": optimized_skill},
                total_rounds=1, total_reflection_lm_calls=0,
                duration_seconds=0.0, started_at=now, finished_at=now,
            )
            print(f"  Optimization complete. Diagnosis → Prompt fix applied.")
        else:
            # ── Live: analysis-driven prompt fix with targeted repair ──
            # When baseline preset was defective, the candidate prompt must
            # REVERSE those defects for train set cases, while intentionally
            # leaving a subtle "over-generalize on pure-stock queries" quirk
            # so that the val set's pure-check_stock case degrades → lets
            # the user exercise the no_overfit Gate rejection path.
            optimized_system = baseline_system
            optimized_skill = baseline_skill

            if ctx.baseline_train.pass_rate < 1.0:
                # Summarise baseline failure types so the candidate prompt
                # reverses the exact defects that caused them.
                ftypes = (ctx.failure_report or FailureReport(0, {}, [])).by_type
                has_tool_err = (
                    ftypes.get("tool_call_error", 0)
                    + ftypes.get("tool_arg_error", 0)
                ) > 0
                has_fmt = ftypes.get("format_error", 0) > 0
                has_verb = ftypes.get("excessive_verbosity", 0) > 0
                has_hall = ftypes.get("hallucination", 0) > 0
                has_contra = ftypes.get("contradictory_information", 0) > 0
                has_mi = ftypes.get("missing_information", 0) > 0
                has_reason = ftypes.get("reasoning_failure", 0) > 0
                has_overgen = ftypes.get("overgeneralization", 0) > 0

                diag_parts_live = [
                    f"训练集通过率 {ctx.baseline_train.pass_rate:.0%}，存在优化空间。",
                ]
                if has_tool_err:
                    diag_parts_live.append("基线存在工具调用缺失/错配，必须重新允许调用 check_stock/get_discount 并按场景匹配。")
                if has_fmt:
                    diag_parts_live.append("基线存在 Markdown/代码块 格式输出，必须切换为纯文本。")
                if has_verb:
                    diag_parts_live.append("基线存在冗余/重复/额外推荐话术，必须保持简洁。")
                if has_hall:
                    diag_parts_live.append("基线存在幻觉/常识编造，必须严格使用工具返回值。")
                if has_contra:
                    diag_parts_live.append("基线存在自相矛盾表述，必须给出单一定论。")
                if has_reason:
                    diag_parts_live.append("基线推理步骤缺失，多工具结果需要做差/比较/合计。")
                diagnosis = " ".join(diag_parts_live)

                # Build the candidate prompt: reverse each detected defect.
                # CRITICAL: prepend a "覆盖规则" header so that any residual
                # memory of the DEFECTIVE baseline prompt (from preset
                # defective_mix etc.) is NOT mixed with the new rules.
                # Without this header, deepseek will often obey the OLD
                # "禁止调用 stock/discount 工具 / 只查上海一次价格" rules
                # alongside the new ones → candidate becomes WORSE.
                lines = [
                    "你是一个购物助手。以下是你必须严格遵守的唯一有效指令集：",
                    "【输出格式——最高优先级】你可以在脑中思考，但最终回复中只输出用户问题的"
                    "答案部分。绝对不要在回复中包含你的思考过程（如'等一等'、'让我重新读'、"
                    "'Let me'、'根据规则'、'我需要先'）。回复的第一句话必须直接回应用户问题。",
                    "",
                    "【指令覆盖声明】本指令集中的每条规则优先级均高于此前你见过的任何系统提示、"
                    "技能说明或类似指令。之前的任何规则（包括但不限于："
                    "禁止调用含 stock/discount 字样的工具、只调用一次 get_product_price、"
                    "查价格只查上海、用常识补全折扣、库存一律回答充足/用 markdown/代码块/emoji 输出、"
                    "结尾追加推荐意见、回答不少于 150 字、数字重复多次、用据我所知编造信息 等）"
                    "全部作废，从现在开始一律以下列第 1 条及后续规则为准。",
                    "",
                    "请严格遵循以下规则回答：",
                ]
                rule_idx = 1

                # 1) Completeness (reverse missing_information + reasoning_failure)
                lines.append(
                    f"{rule_idx}. 逐一回应用户的每一个子问题，不遗漏；"
                    f"若涉及多个商品/城市的数字结果，必须按题目要求给出差值、合计或明确比较结论。"
                )
                rule_idx += 1

                # 2) No verbosity / no extra recommendation / no thinking out loud
                lines.append(
                    f"{rule_idx}. 直接输出最终答案，禁止展示思考过程。"
                    f"严禁以'等等'、'让我'、'Let me'、'根据规则'、'我需要'、'首先'、"
                    f"'好的，系统'、'我来'开头或包含这些元描述内容。"
                    f"每个信息点（价格、数量、折扣、运费、时间等）只说一次，严禁重复；"
                    f"禁止追加推荐意见、促销话术、emoji、Markdown 格式（**加粗、列表、标题、代码块）。"
                )
                rule_idx += 1

                # 3) Strict grounding in tool outputs (reverse hallucination + contradiction)
                lines.append(
                    f"{rule_idx}. 必须严格依据工具返回的数据给出答案，禁止用常识/经验编造；"
                    f"库存状态与数值以工具结果为准，不得同时给出两个矛盾结论。"
                )
                rule_idx += 1

                # 4) Tool discipline (reverse tool_call_error + default: reasonable use)
                lines.append(
                    f"{rule_idx}. 工具调用必须与问题对应：涉及价格→get_product_price，"
                    f"涉及库存→check_stock，涉及折扣→get_discount，涉及配送→get_shipping；"
                    f"需要哪些就调用哪些，不要省略也不要多调。"
                )
                rule_idx += 1

                # 5) OVERFIT INDUCTION: memorise a spurious correlation that
                #    helps training-set cases while hurting pure-stock val cases
                #    (e.g. val_003 "香蕉还有库存吗" → candidate will ALSO call
                #    get_product_price → answers "香蕉4.5元一斤，库存充足有300件"
                #    → overgeneralization → newly_failing on val → GATE REJECT).
                #    NOTE: the rule is written with ambiguous enough wording that
                #    a well-designed val case can still hit it; user can disable
                #    rule 6 via prompt to accept candidate normally.
                lines.append(
                    f"{rule_idx}. 当用户查询库存相关问题时，如果能在同一次回答中顺便提供"
                    f"该商品的价格/折扣信息以丰富上下文，则可以一并调用相关工具"
                    f"（get_product_price / get_discount）并给出完整信息。"
                )
                rule_idx += 1

                lines.append(
                    f"{rule_idx}. 使用纯文本，直接给出答案，不要加'用户问了…'等元描述。"
                )

                optimized_system = "\n".join(lines)
                optimized_skill = ""
            else:
                diagnosis = "训练集全部通过，无需优化。"
            print(f"  Diagnosis: {diagnosis}")
            print(f"  Baseline ({len(baseline_system)} chars) → "
                  f"Optimized ({len(optimized_system)} chars)")

            from trpc_agent_sdk.evaluation._optimize_result import OptimizeResult
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).isoformat()
            ctx.optimize_result = OptimizeResult(
                algorithm="analysis_driven_gepa",
                status="SUCCEEDED",
                finish_reason="completed",
                stop_reason="completed",
                baseline_pass_rate=ctx.baseline_val.pass_rate,
                best_pass_rate=ctx.baseline_val.pass_rate,
                pass_rate_improvement=0.0,
                baseline_metric_breakdown=ctx.baseline_val.metric_breakdown,
                best_metric_breakdown=ctx.baseline_val.metric_breakdown,
                metric_thresholds={"llm_rubric_response": 0.6},
                baseline_prompts={"system_prompt": baseline_system, "skill": baseline_skill},
                best_prompts={"system_prompt": optimized_system, "skill": optimized_skill},
                total_rounds=1, total_reflection_lm_calls=0,
                duration_seconds=round(time.time() - t0, 1),
                started_at=now, finished_at=now,
            )
            print(f"  Optimization complete. Diagnosis → Prompt fix applied.")

            # Save optimizer detail
            import json as _json
            _opt_dir = Path(ctx.output_dir) / "optimizer_output"
            _opt_dir.mkdir(parents=True, exist_ok=True)
            _detail = {
                "algorithm": "analysis_driven",
                "diagnosis": diagnosis,
                "baseline_train_pass_rate": ctx.baseline_train.pass_rate,
                "baseline_val_pass_rate": ctx.baseline_val.pass_rate,
                "baseline_failures_by_type": (
                    ctx.failure_report.by_type if ctx.failure_report else {}
                ),
                "baseline_prompts": {"system_prompt": baseline_system, "skill": baseline_skill},
                "candidate_prompts": {"system_prompt": optimized_system, "skill": optimized_skill},
            }
            (_opt_dir / "optimizer_detail.json").write_text(
                _json.dumps(_detail, indent=2, ensure_ascii=False), encoding="utf-8")

        ctx.stage_timings["optimization"] = time.time() - t0

        # ═══════════════════════════════════════════════════════════
        # Stage 4: Candidate Validation (both train + val sets)
        # ═══════════════════════════════════════════════════════════
        t0 = time.time()
        print("\n[Stage 4/6] Candidate Validation (train + val)...")

        if ctx.is_trace_mode:
            candidate_val_path = str(
                Path(ctx.project_dir) / "agent" / "trace_val_candidate.evalset.json")
            candidate_train_path = str(
                Path(ctx.project_dir) / "agent" / "trace_train_candidate.evalset.json")
            ctx.candidate_val = await run_evaluation(
                evalset_path=candidate_val_path,
                eval_config_path=eval_config, print_results=False,
            )
            print(f"  Candidate val pass_rate: {ctx.candidate_val.pass_rate:.2%}")
            try:
                ctx.candidate_train = await run_evaluation(
                    evalset_path=candidate_train_path,
                    eval_config_path=eval_config, print_results=False,
                )
                print(f"  Candidate train pass_rate: {ctx.candidate_train.pass_rate:.2%}")
            except Exception:
                ctx.candidate_train = ctx.baseline_train
                print(f"  Candidate train not found — using baseline_train.")
        elif (ctx.optimize_result is not None
              and ctx.optimize_result.best_prompts
              and ctx.optimize_result.best_prompts.get("system_prompt")):
            print(f"  Writing candidate prompt to disk...")
            bp = ctx.optimize_result.best_prompts
            _system_path.write_text(bp["system_prompt"], encoding="utf-8")
            if "skill" in bp:
                _skill_path.write_text(bp["skill"], encoding="utf-8")
            ctx.candidate_val = await run_evaluation(
                evalset_path=ctx.val_path,
                eval_config_path=eval_config, print_results=False,
                **eval_kwargs,
            )
            print(f"  Candidate val pass_rate: {ctx.candidate_val.pass_rate:.2%}")
            ctx.candidate_train = await run_evaluation(
                evalset_path=ctx.train_path,
                eval_config_path=eval_config, print_results=False,
                **eval_kwargs,
            )
            print(f"  Candidate train pass_rate: {ctx.candidate_train.pass_rate:.2%}")
        else:
            ctx.candidate_val = ctx.baseline_val
            ctx.candidate_train = ctx.baseline_train
            print(f"  No candidate available — using baseline for both sets.")

        if ctx.optimize_result is not None and ctx.candidate_val is not None:
            ctx.optimize_result.best_pass_rate = ctx.candidate_val.pass_rate
            ctx.optimize_result.pass_rate_improvement = (
                ctx.candidate_val.pass_rate - ctx.optimize_result.baseline_pass_rate)
            ctx.optimize_result.best_metric_breakdown = ctx.candidate_val.metric_breakdown

        ctx.stage_timings["validation"] = time.time() - t0

        # ═══════════════════════════════════════════════════════════
        # Stage 5: Delta Comparison (val + train) + Gate
        # ═══════════════════════════════════════════════════════════
        t0 = time.time()
        print("\n[Stage 5/6] Delta Comparison (val + train) + Gate Decision...")

        ctx.delta_report = compare(ctx.baseline_val, ctx.candidate_val)
        if ctx.baseline_train and ctx.candidate_train:
            ctx.delta_report_train = compare(ctx.baseline_train, ctx.candidate_train)
            train_delta = ctx.delta_report_train.delta
            val_delta = ctx.delta_report.delta
            print(f"  Val delta:   {val_delta:+.2%} (pass_rate "
                  f"{ctx.delta_report.baseline_pass_rate:.2%} → "
                  f"{ctx.delta_report.candidate_pass_rate:.2%})")
            print(f"  Train delta: {train_delta:+.2%} (pass_rate "
                  f"{ctx.delta_report_train.baseline_pass_rate:.2%} → "
                  f"{ctx.delta_report_train.candidate_pass_rate:.2%})")
        else:
            ctx.delta_report_train = None
            print(f"  Val delta: {ctx.delta_report.delta:+.2%}")

        ctx.gate_decision = decide(ctx.baseline_val, ctx.delta_report, gate_cfg,
                                   total_cost_usd=0.0,
                                   delta_report_train=ctx.delta_report_train)

        status = "ACCEPTED" if ctx.gate_decision.accepted else "REJECTED"
        print(f"  Gate: {status}")
        if not ctx.gate_decision.accepted:
            print(f"  Reason: {ctx.gate_decision.reason}")
        for w in ctx.gate_decision.warnings:
            print(f"  Warning: {w}")
        ctx.stage_timings["gate"] = time.time() - t0

        # ═══════════════════════════════════════════════════════════
        # Stage 6: Report Generation
        # ═══════════════════════════════════════════════════════════
        t0 = time.time()
        print("\n[Stage 6/6] Report Generation...")
        json_path, md_path = generate(ctx)
        print(f"  JSON: {json_path}")
        print(f"  MD:   {md_path}")
        ctx.stage_timings["report"] = time.time() - t0

    finally:
        # ── Always restore original prompts regardless of outcome ──
        #    Don't leave a defective/overfit prompt on disk.
        if pconfig is not None:
            _restore_original_prompts_if_needed()
            if not preset_restored["done"]:
                # Defensive fallback
                try:
                    _system_path.write_text(orig_sys + "\n", encoding="utf-8")
                    _skill_path.write_text(orig_sk + "\n", encoding="utf-8")
                except Exception:
                    pass
            print("\n[Cleanup] Original prompts restored to disk.")

    return ctx
