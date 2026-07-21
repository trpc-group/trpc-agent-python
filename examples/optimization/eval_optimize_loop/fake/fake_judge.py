"""Fake Judge — 无 LLM API 调用下模拟评测打分。

基于规则引擎（非 LLM）对预测结果和 ground truth 进行对比评分，
输出与 LLMJudge 相同的数据结构，保证 pipeline 可无缝切换。

三维评分均基于字符匹配率推导，模拟真实 LLM Judge 行为：
识别差 → 黑名单召回和回复质量也会相应下降。
"""

from dataclasses import dataclass


@dataclass
class JudgeScore:
    """模拟的三维评分"""
    recognition_quality: float    # 0.0-1.0
    blacklist_quality: float      # 0.0-1.0
    response_quality: float       # 0.0-1.0

    @property
    def overall(self) -> float:
        return (self.recognition_quality + self.blacklist_quality + self.response_quality) / 3.0

    @property
    def passed(self) -> bool:
        return self.overall >= 0.6


@dataclass
class JudgeResult:
    """模拟的评测结果"""
    case_id: str
    ground_truth: str
    predicted: str
    score: JudgeScore
    passed: bool
    failure_reason: str = ""


class FakeJudge:
    """基于规则的假 Judge。

    评分逻辑（完全确定性，无 LLM 依赖）：
    - recognition_quality: 字符匹配率（0.0-1.0）
    - blacklist_quality: 基于识别质量推导（识别差→黑名单召回也差）
    - response_quality: 基于识别质量推导（识别差→回复质量也差）

    使用方式:
        judge = FakeJudge()
        result = judge.evaluate("val_001", "京A12345", "京A12345")
    """

    def evaluate(
        self,
        case_id: str,
        ground_truth: str,
        predicted: str,
    ) -> JudgeResult:
        """对单条 case 进行评测。

        Args:
            case_id: case 标识
            ground_truth: 标注真值
            predicted: Agent 预测结果

        Returns:
            JudgeResult: 包含三维评分和 pass/fail 判断
        """
        recognition = self._char_match_score(ground_truth, predicted)
        # 黑名单和回复质量随识别质量缩放（模拟真实场景）
        blacklist = max(0.1, recognition * 0.9)
        response = min(1.0, max(0.2, recognition * 1.05))

        score = JudgeScore(
            recognition_quality=recognition,
            blacklist_quality=blacklist,
            response_quality=response,
        )

        passed = score.passed
        reason = ""
        if not passed:
            if recognition < 0.8:
                reason = f"final_answer_mismatch: char_match={recognition:.2f}"
            elif blacklist < 0.6:
                reason = "knowledge_recall_insufficient: blacklist miss"
            else:
                reason = f"llm_rubric_fail: overall={score.overall:.2f}"

        return JudgeResult(
            case_id=case_id,
            ground_truth=ground_truth,
            predicted=predicted,
            score=score,
            passed=passed,
            failure_reason=reason,
        )

    @staticmethod
    def _char_match_score(a: str, b: str) -> float:
        """字符级匹配得分。

        完全匹配 → 1.0，逐字符比较取平均。
        """
        if not a or not b:
            return 0.0
        if a == b:
            return 1.0
        matches = sum(1 for ca, cb in zip(a, b) if ca == cb)
        return matches / max(len(a), len(b))
