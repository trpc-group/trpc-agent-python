"""Phase 1: Baseline 评测引擎。

对训练集和验证集进行 baseline 评测，记录每条的 metric 分、pass/fail、
失败原因和关键轨迹，作为后续优化流水线的基准线。

支持两种模式：
- fake: 无 API Key，使用 FakeLLM + FakeJudge 模拟评测
- real: 对接 PlateAgent 的 PlateEvaluator 真实评测

使用示例:
    runner = BaselineRunner(mode="fake")
    results = await runner.run(train_path, val_path)
    print(results["train"].summary.pass_rate)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
import hashlib
from pathlib import Path
from typing import Optional

from fake.fake_model import FakeLLM
from fake.fake_judge import FakeJudge, JudgeResult


# ═══════════════════════════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════════════════════════

@dataclass
class BaselineCaseResult:
    """单条 case 的 baseline 评测结果。"""
    case_id: str
    image: str
    ground_truth: str
    predicted: str
    score: float                     # 0.0-1.0 综合评分
    passed: bool                     # score >= 0.6 为通过
    correct: bool                    # 完全匹配
    char_correct: int = 0
    char_total: int = 0
    failure_reason: str = ""         # 失败原因（空=通过）
    judge_recognition: float = -1.0  # Judge 识别维度
    judge_blacklist: float = -1.0    # Judge 黑名单维度
    judge_response: float = -1.0     # Judge 回复维度
    cost: float = 0.0                # 预估 LLM token 成本
    latency_ms: float = 0.0          # pipeline 耗时
    conditions: dict = field(default_factory=dict)
    trajectory: dict = field(default_factory=dict)  # 关键轨迹片段

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "image": self.image,
            "ground_truth": self.ground_truth,
            "predicted": self.predicted,
            "score": round(self.score, 4),
            "passed": self.passed,
            "correct": self.correct,
            "char_correct": self.char_correct,
            "char_total": self.char_total,
            "failure_reason": self.failure_reason,
            "judge_recognition": self.judge_recognition,
            "judge_blacklist": self.judge_blacklist,
            "judge_response": self.judge_response,
            "cost": self.cost,
            "latency_ms": round(self.latency_ms, 1),
            "conditions": self.conditions,
            "trajectory": self.trajectory,
        }


@dataclass
class BaselineSummary:
    """Baseline 汇总统计。"""
    total: int = 0
    passed: int = 0
    failed: int = 0
    avg_score: float = 0.0
    avg_cost: float = 0.0
    avg_latency_ms: float = 0.0
    pass_rate: float = 0.0

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "avg_score": round(self.avg_score, 4),
            "avg_cost": round(self.avg_cost, 6),
            "avg_latency_ms": round(self.avg_latency_ms, 1),
            "pass_rate": round(self.pass_rate, 4),
        }


@dataclass
class BaselineResult:
    """单个数据集的完整 baseline 结果。"""
    dataset_name: str                # "train" | "val"
    cases: list[BaselineCaseResult] = field(default_factory=list)
    summary: BaselineSummary = field(default_factory=BaselineSummary)

    @property
    def failed_cases(self) -> list[BaselineCaseResult]:
        return [c for c in self.cases if not c.passed]

    @property
    def score_map(self) -> dict[str, float]:
        """{case_id: score} — 供 gate 模块直接使用"""
        return {c.case_id: c.score for c in self.cases}

    def to_dict(self) -> dict:
        return {
            "dataset_name": self.dataset_name,
            "summary": self.summary.to_dict(),
            "cases": [c.to_dict() for c in self.cases],
        }


# ═══════════════════════════════════════════════════════════════
# Fake 模式：预测值映射表
# ═══════════════════════════════════════════════════════════════

# 模拟不同图像在不同场景下的识别结果
# 用于构造 pass / fail / 边界三类 case
FAKE_PREDICTIONS: dict[str, dict[str, str]] = {
    # ???
    "train_001": {
        "predicted": "京A12345",     # ?? ? ????
        "trajectory": "preprocess→locate→segment→recognize(conf=0.92)→format_output",
    },
    "train_002": {
        "predicted": "京B12345",     # ?? ? 1?????A?B?????????????
        "trajectory": "preprocess(noise_reduction)→locate→segment→recognize(conf=0.45)→llm_verify→format_output",
    },
    "train_003": {
        "predicted": "苏X8U88",      # ?? ? ???+???????
        "trajectory": "preprocess(deblur_failed)→locate(partial)→segment(missing_char)→recognize(conf=0.38)→human_review→format_output",
    },
    # ???
    "val_001": {
        "predicted": "粤B54321",      # ?? case ? ????
        "trajectory": "preprocess→locate→segment→recognize(conf=0.95)→format_output",
    },
    "val_002": {
        "predicted": "粤B1XS79",      # ??+??? ? ?????????
        "trajectory": "preprocess→locate→segment→recognize(conf=0.42)→knowledge_search(miss)→format_output",
    },
    "val_003": {
        "predicted": "浙X36X1Z",      # ???? ? ?????????
        "trajectory": "preprocess(deblur_failed)→locate(shifted)→segment→recognize(conf=0.25)→human_review→format_output",
    },
}

class BaselineRunner:
    """Baseline 评测运行器。

    支持 fake 和 real 两种模式。
    """

    def __init__(self, mode: str = "fake", **kwargs):
        """
        Args:
            mode: "fake" | "real"
            **kwargs:
                fake — 无额外参数
                real — plate_agent_root: str（PlateAgent 项目根目录）
        """
        if mode not in ("fake", "real"):
            raise ValueError(f"Unknown mode: {mode}. Must be 'fake' or 'real'.")
        if mode == "real":
            import warnings
            warnings.warn("BaselineRunner real mode is not fully implemented. Use fake mode for working pipeline.", FutureWarning, stacklevel=2)
        self.mode = mode
        self.kwargs = kwargs

        if mode == "fake":
            self._fake_llm = FakeLLM()
            self._fake_judge = FakeJudge()

    # ── 公共接口 ────────────────────────────────────────

    async def run(
        self,
        train_path: str | Path,
        val_path: str | Path,
    ) -> dict[str, BaselineResult]:
        """运行 baseline 评测。

        Args:
            train_path: train.evalset.json 路径
            val_path:   val.evalset.json 路径

        Returns:
            {"train": BaselineResult, "val": BaselineResult}
        """
        train_result = await self.run_split(train_path, "train")
        val_result = await self.run_split(val_path, "val")
        return {"train": train_result, "val": val_result}

    async def run_split(
        self,
        evalset_path: str | Path,
        dataset_name: str,
    ) -> BaselineResult:
        """对单个数据集运行 baseline 评测。

        Args:
            evalset_path: JSON 文件路径
            dataset_name: "train" | "val"（用于日志和结果标记）

        Returns:
            BaselineResult: 完整评测结果
        """
        evalset_path = Path(evalset_path)
        with open(evalset_path, "r", encoding="utf-8") as f:
            evalset = json.load(f)

        cases_data = evalset.get("cases", [])
        if not cases_data:
            raise ValueError(f"No cases found in {evalset_path}")

        if self.mode == "fake":
            return await self._run_fake_split(cases_data, dataset_name)
        else:
            return await self._run_real_split(cases_data, dataset_name)

    # ── Fake 模式 ───────────────────────────────────────

    async def _run_fake_split(
        self,
        cases_data: list[dict],
        dataset_name: str,
    ) -> BaselineResult:
        """Fake 模式：使用 FakeLLM + FakeJudge 模拟评测。"""
        case_results: list[BaselineCaseResult] = []

        for case in cases_data:
            case_id = case["case_id"]
            ground_truth = case["ground_truth"]
            image = case.get("image", "")
            conditions = case.get("conditions", {})

            # 1. 获取 fake 预测
            fake_info = FAKE_PREDICTIONS.get(case_id, {})
            predicted = fake_info.get("predicted", "UNKNOWN")
            trajectory_text = fake_info.get("trajectory", "")

            # 模拟耗时（清晰 200ms，模糊/噪声 500ms）
            cond_type = conditions.get("type", "clear")
            fake_latency = 200 if cond_type == "clear" else 500

            # 2. Fake Judge 打分
            judge_result: JudgeResult = self._fake_judge.evaluate(
                case_id=case_id,
                ground_truth=ground_truth,
                predicted=predicted,
            )

            # 3. 构建结果
            correct = (predicted == ground_truth)
            char_correct = sum(
                1 for i, c in enumerate(predicted)
                if i < len(ground_truth) and c == ground_truth[i]
            )
            char_total = len(ground_truth)

            # fake 成本估算：每个 case 约 $0.0002
            fake_cost = 0.0002

            # 解析 trajectory 为结构化 dict
            trajectory = self._parse_trajectory(trajectory_text)

            case_result = BaselineCaseResult(
                case_id=case_id,
                image=image,
                ground_truth=ground_truth,
                predicted=predicted,
                score=judge_result.score.overall,
                passed=judge_result.passed,
                correct=correct,
                char_correct=char_correct,
                char_total=char_total,
                failure_reason=judge_result.failure_reason,
                judge_recognition=judge_result.score.recognition_quality,
                judge_blacklist=judge_result.score.blacklist_quality,
                judge_response=judge_result.score.response_quality,
                cost=fake_cost,
                latency_ms=fake_latency,
                conditions=conditions,
                trajectory=trajectory,
            )
            case_results.append(case_result)

        # 4. 汇总
        summary = self._build_summary(case_results)
        return BaselineResult(
            dataset_name=dataset_name,
            cases=case_results,
            summary=summary,
        )

    # ── Real 模式（待对接 PlateEvaluator）─────────────────

    async def _run_real_split(
        self,
        cases_data: list[dict],
        dataset_name: str,
    ) -> BaselineResult:
        """Real mode: interface with PlateAgent PlateEvaluator.

        PLACEHOLDER: requires plate-agent project environment + trpc_agent_sdk.
        The image_id hashing below is unstable and would need real dataset-id
        mapping for production use. Only reachable via direct API, not CLI.
        """
        plate_agent_root = self.kwargs.get("plate_agent_root")
        if not plate_agent_root:
            raise ValueError(
                "Real mode requires plate_agent_root kwarg pointing to plate-agent project."
            )

        import sys
        plate_root_str = str(Path(plate_agent_root))
        sys.path.insert(0, plate_root_str)
        path_restored = False
        try:
            from agent.session_manager import create_session_service, create_memory_service
            from eval.evaluator import PlateEvaluator
        except ImportError as e:
            sys.path.remove(plate_root_str)  # restore before raising
            path_restored = True
            raise ImportError(
                f"Cannot import PlateAgent modules from {plate_agent_root}. "
                f"Ensure trpc_agent_sdk is installed. Error: {e}"
            )
        finally:
            if not path_restored:
                sys.path.remove(plate_root_str)  # restore on success path too
        # 构建 ground_truth.json 格式（临时文件）
        # Build ground_truth items with sequential IDs for stable reverse mapping.
        # Uses enumerate(start=1) instead of SHA256 hash so that the id->case_id
        # mapping is trivially reversible and immune to hash collisions or
        # filename-normalisation differences in PlateEvaluator results.
        gt_items = []
        id_to_case: dict[int, str] = {}
        for i, case in enumerate(cases_data, start=1):
            gt_items.append({
                "id": i,
                "image": f"eval/dataset/test_plates/{case['image']}",
                "plate_number": case["ground_truth"],
                "conditions": case.get("conditions", {}),
            })
            id_to_case[i] = case["case_id"]

        session_service = create_session_service(use_redis=False)
        memory_service = create_memory_service(use_redis=False)

        evaluator = PlateEvaluator(
            gt_path=None,  # ?????????
            session_service=session_service,
            memory_service=memory_service,
        )
        # ???? ground_truth ??
        evaluator.ground_truth = gt_items

        report = await evaluator.run(verbose=False)

        # Convert to BaselineCaseResult list using the stable id->case_id mapping.
        # Falls back to filename heuristics only when image_id is missing from the map
        # (should not happen with sequential IDs; kept as defence-in-depth).
        case_results: list[BaselineCaseResult] = []
        for r in report.details:
            case_id = id_to_case.get(r.image_id)
            if case_id is None:
                image_key = Path(r.image_path).name if r.image_path else ""
                case_id = f"case_{r.image_id}"
            case_result = BaselineCaseResult(
                case_id=case_id,
                image=r.image_path,
                ground_truth=r.ground_truth,
                predicted=r.predicted,
                score=1.0 if r.correct else (r.char_correct / max(r.char_total, 1)),
                passed=r.correct,
                correct=r.correct,
                char_correct=r.char_correct,
                char_total=r.char_total,
                failure_reason="" if r.correct else f"predicted '{r.predicted}' != '{r.ground_truth}'",
                judge_recognition=r.judge_recognition,
                judge_blacklist=r.judge_blacklist,
                judge_response=r.judge_response,
                cost=0.0,  # real ?????? token_tracker ??
                latency_ms=r.pipeline_time_ms,
                conditions=r.conditions,
            )
            case_results.append(case_result)


        summary = self._build_summary(case_results)
        return BaselineResult(
            dataset_name=dataset_name,
            cases=case_results,
            summary=summary,
        )

    # ── 辅助方法 ────────────────────────────────────────

    @staticmethod
    def _build_summary(cases: list[BaselineCaseResult]) -> BaselineSummary:
        """从 case 列表构建汇总统计。"""
        total = len(cases)
        passed = sum(1 for c in cases if c.passed)
        failed = total - passed
        avg_score = sum(c.score for c in cases) / total if total > 0 else 0.0
        avg_cost = sum(c.cost for c in cases) / total if total > 0 else 0.0
        avg_latency = sum(c.latency_ms for c in cases) / total if total > 0 else 0.0
        pass_rate = passed / total if total > 0 else 0.0
        return BaselineSummary(
            total=total,
            passed=passed,
            failed=failed,
            avg_score=avg_score,
            avg_cost=avg_cost,
            avg_latency_ms=avg_latency,
            pass_rate=pass_rate,
        )

    @staticmethod
    def _parse_trajectory(trajectory_text: str) -> dict:
        """将轨迹文本解析为结构化 dict。

        "preprocess→locate→segment→recognize(conf=0.92)→format_output"
        → {"nodes": ["preprocess","locate","segment","recognize","format_output"],
           "confidence": 0.92, "human_review_triggered": False}
        """
        if not trajectory_text:
            return {}
        nodes = []
        confidence = None
        human_review = False
        for part in trajectory_text.split("→"):
            part = part.strip()
            if "(" in part:
                name = part.split("(")[0]
                if "conf=" in part:
                    try:
                        confidence = float(part.split("conf=")[1].rstrip(")"))
                    except ValueError:
                        pass
            else:
                name = part
            nodes.append(name)
            if name in ("human_review", "llm_verify"):
                human_review = True
        result = {
            "nodes": nodes,
            "human_review_triggered": human_review,
            "raw_steps": [s.strip() for s in trajectory_text.split("→")],
        }
        if confidence is not None:
            result["confidence"] = confidence
        return result


# ═══════════════════════════════════════════════════════════════
# 便捷函数
# ═══════════════════════════════════════════════════════════════

async def run_baseline(
    train_path: str | Path = "config/train.evalset.json",
    val_path: str | Path = "config/val.evalset.json",
    mode: str = "fake",
    **kwargs,
) -> dict[str, BaselineResult]:
    """一键运行 baseline 评测。

    Args:
        train_path: 训练集路径
        val_path: 验证集路径
        mode: "fake" | "real"

    Returns:
        {"train": BaselineResult, "val": BaselineResult}
    """
    runner = BaselineRunner(mode=mode, **kwargs)
    return await runner.run(train_path, val_path)
