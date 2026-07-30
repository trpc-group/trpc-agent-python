#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2025 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Evaluation → attribution → optimization → regression → audit pipeline."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shlex
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[2]
for _path in (str(_REPO_ROOT), str(_HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)


import fake_agent  # type: ignore[unresolved-import]
import gates  # type: ignore[unresolved-import]
import report  # type: ignore[unresolved-import]
import runner  # type: ignore[unresolved-import]

try:
    # 本地凭据从同目录 .env 读取（已被 gitignore）；已有环境变量优先。
    from dotenv import load_dotenv

    load_dotenv(_HERE / ".env")
except ImportError:
    pass

DEFAULT_RUN_JSON: dict[str, Any] = {
    "champion_prompt": "prompts/system.md",
    "train_evalset": "data/train.evalset.json",
    "val_evalset": "data/val.evalset.json",
    "metric_config": "data/test_config.json",
    "gate": {
        "min_val_lift": 0.02,
        "slice_tolerance": 0.05,
        "budget_tokens": 100_000,
        "budget_usd": None,
        "epsilon": 0.001,
    },
    "seed": 42,
}


@dataclass
class OptimizerCandidate:
    prompt: str
    info: dict[str, Any]
    rounds: list[dict[str, Any]]
    artifacts: dict[str, str]
    cost_status: str
    total_tokens: Optional[int]
    total_cost: Optional[float]


def _load_config(config_path: Optional[Path]) -> dict[str, Any]:
    if config_path is None:
        return json.loads(json.dumps(DEFAULT_RUN_JSON))
    if not config_path.exists():
        raise FileNotFoundError(f"run config 不存在：{config_path}")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    merged = json.loads(json.dumps(DEFAULT_RUN_JSON))
    merged.update(config)
    merged_gate = dict(DEFAULT_RUN_JSON["gate"])
    merged_gate.update(config.get("gate", {}))
    merged["gate"] = merged_gate
    return merged


def _resolve(base: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (base / path).resolve()


def _gate_config(config: dict[str, Any]) -> gates.GateConfig:
    return gates.GateConfig(
        min_val_lift=float(config.get("min_val_lift", 0.02)),
        slice_tolerance=float(config.get("slice_tolerance", 0.05)),
        budget_tokens=int(config.get("budget_tokens", 100_000)),
        budget_usd=(float(config["budget_usd"]) if config.get("budget_usd") is not None else None),
        epsilon=float(config.get("epsilon", 0.001)),
    )


def _build_repro_cmd(args: argparse.Namespace) -> str:
    argv = [sys.executable, str(_HERE / "pipeline.py")]
    for key, value in vars(args).items():
        if value is None or value is False:
            continue
        flag = f"--{key.replace('_', '-')}"
        argv.append(flag if value is True else f"{flag}={value}")
    # 路径可能含空格等特殊字符，引用后保证复现命令可直接粘贴执行。
    return " ".join(shlex.quote(arg) for arg in argv)


def _content_text(content: dict[str, Any]) -> str:
    return "\n".join(str(part.get("text", "")) for part in (content.get("parts") or []) if part.get("text"))


def _case_contexts(*paths: Path) -> dict[str, list[dict[str, Any]]]:
    """Index evaluation metadata by query without treating query as eval_id.

    The public optimizer callback receives only ``query``.  A query can legally
    appear in more than one eval case, so the audit entry preserves every
    matching case context instead of inventing an eval id from the query.
    """

    contexts: dict[str, list[dict[str, Any]]] = {}
    for path in paths:
        evalset = json.loads(path.read_text(encoding="utf-8"))
        for case in evalset.get("eval_cases", []):
            conversation = case.get("conversation") or []
            if not conversation:
                continue
            invocation = conversation[0]
            query = _content_text(invocation.get("user_content") or {})
            if not query:
                continue
            contexts.setdefault(query, []).append(
                {
                    "eval_id": case["eval_id"],
                    "split": ((case.get("session_input") or {}).get("state") or {}).get("split"),
                    "expected_response": _content_text(invocation.get("final_response") or {}),
                    "expected_tool_context": ((invocation.get("intermediate_data") or {}).get("tool_uses") or []),
                }
            )
    return contexts


def _audited_call_agent(
    base_call_agent,
    contexts: dict[str, list[dict[str, Any]]],
    audit: list[dict[str, Any]],
):
    async def call_agent(query: str) -> str:
        matching_contexts = contexts.get(query)
        if not matching_contexts:
            raise KeyError(f"call_agent 收到未登记的评测 query：{query!r}")
        try:
            response = await base_call_agent(query)
        except BaseException as error:
            audit.append(
                {
                    "query": query,
                    "eval_contexts": matching_contexts,
                    "status": "error",
                    "error": f"{type(error).__name__}: {error}",
                }
            )
            raise
        audit.append(
            {
                "query": query,
                "eval_contexts": matching_contexts,
                "status": "ok",
                "actual_response": response,
            }
        )
        return response

    # A plain callback does not prove what its model calls cost.  Propagate
    # explicit accounting metadata only when the provider/stub supplies it.
    call_agent.cost_status = getattr(base_call_agent, "cost_status", "unavailable")
    call_agent.total_tokens = getattr(base_call_agent, "total_tokens", None)
    call_agent.total_cost = getattr(base_call_agent, "total_cost", None)
    return call_agent


def _serialize_model(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True)
    if isinstance(value, dict):
        return value
    return dict(vars(value))


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


def _optimizer_cost(result: Any, call_agent) -> tuple[str, Optional[int], Optional[float]]:
    if getattr(call_agent, "cost_status", "unavailable") != "measured":
        return "unavailable", None, None
    agent_tokens = getattr(call_agent, "total_tokens", None)
    agent_cost = getattr(call_agent, "total_cost", None)
    if agent_tokens is None or agent_cost is None:
        return "unavailable", None, None

    usage = getattr(result, "total_token_usage", None) or {}
    optimizer_tokens = usage.get("total") if isinstance(usage, dict) else None
    optimizer_cost = getattr(result, "total_llm_cost", None)
    if optimizer_tokens is None or optimizer_cost is None:
        return "unavailable", None, None
    if int(optimizer_tokens) <= 0 and int(getattr(result, "total_reflection_lm_calls", 0) or 0) > 0:
        return "unavailable", None, None
    return (
        "measured",
        int(optimizer_tokens) + int(agent_tokens),
        float(optimizer_cost) + float(agent_cost),
    )


async def _run_optimize_for_candidate(
    *,
    optimizer_config_path: Path,
    champion_prompt_path: Path,
    train_evalset_path: Path,
    val_evalset_path: Path,
    output_dir: Path,
    call_agent,
) -> OptimizerCandidate:
    """Invoke the native optimizer and return its best prompt plus audit metadata."""

    from trpc_agent_sdk.evaluation import AgentOptimizer, TargetPrompt

    target = TargetPrompt().add_path("system", str(champion_prompt_path))
    # Windows 盘符含冒号，会被 evalset 的 "file.json:case_id" 语法误切；
    # 与 runner._run_evaluator 一致，切到示例根目录并传相对路径。
    previous_cwd = Path.cwd()
    try:
        os.chdir(_HERE)
        result = await AgentOptimizer.optimize(
            config_path=str(optimizer_config_path),
            call_agent=call_agent,
            target_prompt=target,
            train_dataset_path=os.path.relpath(train_evalset_path, _HERE),
            validation_dataset_path=os.path.relpath(val_evalset_path, _HERE),
            output_dir=str(output_dir),
            update_source=False,
            verbose=0,
        )
    finally:
        os.chdir(previous_cwd)
    status = _enum_value(result.status)
    if status != "SUCCEEDED" or not result.best_prompts:
        raise RuntimeError(
            f"AgentOptimizer 未产生 Candidate "
            f"(status={status}, finish_reason={_enum_value(result.finish_reason)}, "
            f"error={getattr(result, 'error_message', '')})"
        )
    prompt_name = "system" if "system" in result.best_prompts else next(iter(result.best_prompts))
    rounds: list[dict[str, Any]] = []
    for index, item in enumerate(result.rounds or [], start=1):
        round_record = _serialize_model(item)
        round_number = int(round_record.get("round", round_record.get("round_index", index)))
        round_record["artifact_path"] = str(output_dir / "rounds" / f"round_{round_number:03d}.json")
        rounds.append(round_record)
    info = {
        "algorithm": result.algorithm,
        "status": status,
        "finish_reason": _enum_value(result.finish_reason),
        "stop_reason": _enum_value(getattr(result, "stop_reason", None)),
        "total_rounds": result.total_rounds,
        "baseline_pass_rate": result.baseline_pass_rate,
        "best_pass_rate": result.best_pass_rate,
        "pass_rate_improvement": result.pass_rate_improvement,
    }
    cost_status, total_tokens, total_cost = _optimizer_cost(result, call_agent)
    artifacts = {
        "optimizer_dir": str(output_dir),
        "optimizer_result": str(output_dir / "result.json"),
        "optimizer_summary": str(output_dir / "summary.txt"),
        "optimizer_rounds": str(output_dir / "rounds"),
        "optimizer_config_snapshot": str(output_dir / "config.snapshot.json"),
        "optimizer_run_log": str(output_dir / "run.log"),
        "optimizer_baseline_prompt": str(output_dir / "baseline_prompts" / f"{prompt_name}.md"),
        "optimizer_best_prompt": str(output_dir / "best_prompts" / f"{prompt_name}.md"),
    }
    return OptimizerCandidate(
        prompt=result.best_prompts[prompt_name],
        info=info,
        rounds=rounds,
        artifacts=artifacts,
        cost_status=cost_status,
        total_tokens=total_tokens,
        total_cost=total_cost,
    )


def _failure_frozen(
    *,
    run_id: str,
    champion_prompt_path: Path,
    train_evalset_path: Path,
    val_evalset_path: Path,
    metric_config_path: Path,
    optimizer_config_path: Path,
    config: dict[str, Any],
    model_info: dict[str, Any],
    error: BaseException,
) -> runner.FrozenInputs:
    champion = champion_prompt_path.read_text(encoding="utf-8")
    started_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return runner.FrozenInputs(
        run_id=run_id,
        champion_sha256=runner.sha256_text(champion),
        challenger_sha256="",
        train_sha256=runner.sha256_file(train_evalset_path),
        val_sha256=runner.sha256_file(val_evalset_path),
        metric_config_sha256=runner.sha256_file(metric_config_path),
        run_config_sha256=runner.sha256_json(config),
        optimizer_config_sha256=(runner.sha256_file(optimizer_config_path) if optimizer_config_path.exists() else None),
        seed=int(config.get("seed", 42)),
        started_at=started_at,
        mode="optimize",
        candidate_source="agent_optimizer",
        gate_config=config["gate"],
        model_info=model_info,
        evaluator_info={
            "name": "AgentEvaluator",
            "metric_config_sha256": runner.sha256_file(metric_config_path),
        },
        optimizer_info={
            "status": "FAILED",
            "error_type": type(error).__name__,
            "error": str(error),
        },
    )


async def _amain(args: argparse.Namespace, *, call_agent=None) -> int:
    if args.config:
        config_path: Optional[Path] = _resolve(_HERE, args.config)
    else:
        default_run_json = _HERE / "run.json"
        config_path = default_run_json if default_run_json.exists() else None
    config = _load_config(config_path)
    champion_path = _resolve(_HERE, config["champion_prompt"])
    train_path = _resolve(_HERE, config["train_evalset"])
    val_path = _resolve(_HERE, config["val_evalset"])
    metric_path = _resolve(_HERE, config["metric_config"])
    optimizer_path = _resolve(_HERE, args.optimizer_config or "optimizer.json")
    seed = int(config.get("seed", 42))
    gate_config = _gate_config(config["gate"])
    run_id = runner.new_run_id()
    run_dir = _HERE / "runs" / run_id
    repro_cmd = _build_repro_cmd(args)
    model_info: dict[str, Any] = {"provider": "none", "model_name": "fake-trace"}
    optimizer: Optional[OptimizerCandidate] = None
    audited_calls: list[dict[str, Any]] = []

    if args.mode == "optimize":
        run_dir.mkdir(parents=True, exist_ok=False)
        optimizer_dir = run_dir / "optimizer"
        optimizer_dir.mkdir()
        try:
            base_call_agent = call_agent
            if base_call_agent is None:
                # 懒加载：fake 模式无需引入真实模型 SDK 依赖链。
                import live_agent  # type: ignore[unresolved-import]

                model_info = live_agent.model_info_from_env()
                base_call_agent = live_agent.build_call_agent(champion_path)
            else:
                model_info = {"provider": "test-stub", "model_name": "injected-call-agent"}
            contexts = _case_contexts(train_path, val_path)
            contextual_call_agent = _audited_call_agent(base_call_agent, contexts, audited_calls)
            optimizer = await _run_optimize_for_candidate(
                optimizer_config_path=optimizer_path,
                champion_prompt_path=champion_path,
                train_evalset_path=train_path,
                val_evalset_path=val_path,
                output_dir=optimizer_dir,
                call_agent=contextual_call_agent,
            )
            (run_dir / "call_agent_audit.json").write_text(
                json.dumps(audited_calls, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            optimizer.artifacts["call_agent_audit"] = str(run_dir / "call_agent_audit.json")
            challenger_text = optimizer.prompt
            candidate_source = "agent_optimizer"
            scenario = None
            regression_call_agent = contextual_call_agent
        except BaseException as error:
            audit_path = run_dir / "call_agent_audit.json"
            audit_path.write_text(
                json.dumps(audited_calls, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            error_path = run_dir / "optimizer_error.json"
            error_path.write_text(
                json.dumps(
                    {
                        "error_type": type(error).__name__,
                        "error": str(error),
                        "call_agent_audit": audited_calls,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            frozen = _failure_frozen(
                run_id=run_id,
                champion_prompt_path=champion_path,
                train_evalset_path=train_path,
                val_evalset_path=val_path,
                metric_config_path=metric_path,
                optimizer_config_path=optimizer_path,
                config=config,
                model_info=model_info,
                error=error,
            )
            (run_dir / "frozen.json").write_text(
                json.dumps(asdict(frozen), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            optimizer_artifacts = {
                "optimizer_error": str(error_path),
                "call_agent_audit": str(audit_path),
                "optimizer_dir": str(optimizer_dir),
            }
            for name in ("result.json", "summary.txt", "config.snapshot.json", "run.log"):
                candidate_path = optimizer_dir / name
                if candidate_path.exists():
                    optimizer_artifacts[f"optimizer_{candidate_path.stem}"] = str(candidate_path)
            failure_report = report.build_optimizer_failure_report(
                frozen=frozen,
                artifact_dir=run_dir,
                error=error,
                repro_cmd=repro_cmd,
                optimizer_artifacts=optimizer_artifacts,
            )
            report.write_report(failure_report, out_dir=run_dir)
            report.write_report(failure_report, out_dir=_HERE)
            print(f"决策: REJECT（优化器失败：{error}）", file=sys.stderr)
            print(f"报告: {_HERE / 'optimization_report.md'}")
            print(f"Artifacts: {run_dir}")
            return 2 if args.apply else 1
    elif args.candidate_file:
        challenger_text = _resolve(_HERE, args.candidate_file).read_text(encoding="utf-8")
        candidate_source = "candidate_file"
        scenario = None
        regression_call_agent = None
    elif args.scenario:
        challenger_text = fake_agent.build_candidate(args.scenario)
        candidate_source = "candidate_file"
        scenario = args.scenario
        regression_call_agent = None
    else:
        print("fake 模式必须指定 --scenario 或 --candidate-file。", file=sys.stderr)
        return 2

    before_sha = runner.sha256_file(champion_path)
    artifact = await runner.run_pair(
        champion_prompt_path=champion_path,
        challenger_text=challenger_text,
        train_evalset_path=train_path,
        val_evalset_path=val_path,
        metric_config_path=metric_path,
        artifact_root=_HERE / "runs",
        artifact_dir=run_dir,
        mode=args.mode,
        candidate_source=candidate_source,
        scenario=scenario,
        seed=seed,
        call_agent=regression_call_agent,
        run_config=config,
        gate_config=config["gate"],
        optimizer_config_path=optimizer_path if args.mode == "optimize" else None,
        model_info=model_info,
        optimizer_info=optimizer.info if optimizer else {},
        cost_status=optimizer.cost_status if optimizer else "measured",
        total_tokens=optimizer.total_tokens if optimizer else 0,
        total_cost=optimizer.total_cost if optimizer else 0.0,
        optimizer_artifacts=optimizer.artifacts if optimizer else {},
        optimizer_rounds=optimizer.rounds if optimizer else [],
    )
    if runner.sha256_file(champion_path) != before_sha:
        raise RuntimeError("Champion prompt 在回归评测后未恢复。")

    case_deltas = [
        gates.CaseDelta(
            eval_id=case.eval_id,
            split=case.split,
            slice_name=case.slice_name,
            risk_level=case.risk_level,
            protected=case.protected,
            champion_status=case.champion_status,
            challenger_status=case.challenger_status,
            champion_score=case.champion_score,
            challenger_score=case.challenger_score,
        )
        for case in artifact.cases
    ]
    decision = gates.evaluate(
        case_deltas,
        cost_status=artifact.cost_status,
        total_tokens=artifact.total_tokens,
        total_cost=artifact.total_cost,
        config=gate_config,
    )

    applied = False
    after_apply_sha: Optional[str] = None
    if args.apply and decision.accepted:
        from trpc_agent_sdk.evaluation import TargetPrompt

        target = TargetPrompt().add_path("system", str(champion_path))
        snapshot = await target.read_all()
        try:
            await target.write_all({"system": challenger_text})
            after_apply_sha = runner.sha256_file(champion_path)
            applied = True
        except BaseException:
            await target.write_all(snapshot)
            raise
    elif args.apply:
        print("Gate REJECT，--apply 已被拒绝；Champion 未修改。", file=sys.stderr)

    report_dict = report.build_report_dict(
        artifact,
        decision,
        applied=applied,
        before_apply_sha256=before_sha,
        after_apply_sha256=after_apply_sha,
        repro_cmd=repro_cmd,
    )
    report.write_report(report_dict, out_dir=artifact.artifact_dir)
    report.write_report(report_dict, out_dir=_HERE)
    if args.mode == "optimize":
        (run_dir / "call_agent_audit.json").write_text(
            json.dumps(audited_calls, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    print(f"决策: {'ACCEPT' if decision.accepted else 'REJECT'}")
    if decision.violated:
        print(f"违反 gate: {', '.join(decision.violated)}")
    print(f"报告: {_HERE / 'optimization_report.md'}")
    print(f"Artifacts: {artifact.artifact_dir}")
    if args.apply and not decision.accepted:
        return 2
    return 0 if decision.accepted else 1


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Issue #91 Evaluation + Optimization pipeline")
    parser.add_argument("--config", default=None)
    parser.add_argument("--mode", choices=("fake", "optimize"), default="fake")
    parser.add_argument("--scenario", choices=("success", "no_effect", "overfit"))
    parser.add_argument("--candidate-file")
    parser.add_argument("--optimizer-config")
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    return asyncio.run(_amain(_parse_args(argv)))


async def amain(argv: Optional[list[str]] = None, *, call_agent=None) -> int:
    return await _amain(_parse_args(argv), call_agent=call_agent)


if __name__ == "__main__":
    raise SystemExit(main())
