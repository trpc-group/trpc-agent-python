"""逐 case delta：baseline vs candidate，分 5 桶。"""
from __future__ import annotations

from .models import (
    Bucket,
    CandidateDelta,
    CaseDelta,
    CaseSnapshot,
    DeltaBuckets,
    SplitDelta,
    SplitResult,
)

_EPS = 1e-9


def _bucket(base: CaseSnapshot, cand: CaseSnapshot) -> Bucket:
    if not base.passed and cand.passed:
        return "new_pass"
    if base.passed and not cand.passed:
        return "new_fail"
    if cand.score > base.score + _EPS:
        return "improved"
    if cand.score < base.score - _EPS:
        return "regressed"
    return "unchanged"


def _split_delta(base: SplitResult, cand: SplitResult) -> SplitDelta:
    return SplitDelta(
        split=cand.split,
        pass_rate_delta=cand.pass_rate - base.pass_rate,
        average_score_delta=cand.average_score - base.average_score,
    )


def compare(
    base_train: SplitResult,
    base_val: SplitResult,
    cand_train: SplitResult,
    cand_val: SplitResult,
) -> CandidateDelta:
    """合并 train+val 的逐 case 对比。case eval_id 全局唯一，可合并分桶。"""
    buckets = DeltaBuckets()
    case_deltas: list[CaseDelta] = []

    for base_split, cand_split in [(base_train, cand_train), (base_val, cand_val)]:
        base_map = {c.eval_id: c for c in base_split.cases}
        for cc in cand_split.cases:
            bc = base_map[cc.eval_id]
            b = _bucket(bc, cc)
            case_deltas.append(
                CaseDelta(
                    eval_id=cc.eval_id,
                    baseline_passed=bc.passed,
                    candidate_passed=cc.passed,
                    baseline_score=bc.score,
                    candidate_score=cc.score,
                    bucket=b,
                ))
            # 按桶分入 DeltaBuckets
            if b == "new_pass":
                buckets.new_pass.append(cc.eval_id)
            elif b == "new_fail":
                buckets.new_fail.append(cc.eval_id)
            elif b == "improved":
                buckets.improved.append(cc.eval_id)
            elif b == "regressed":
                buckets.regressed.append(cc.eval_id)
            else:
                buckets.unchanged.append(cc.eval_id)

    return CandidateDelta(
        train=_split_delta(base_train, cand_train),
        validation=_split_delta(base_val, cand_val),
        buckets=buckets,
    )
