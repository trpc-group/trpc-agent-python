# Optimization Report

**Task ID**: `opt-20260802-130921-7280b549`
**Generated**: 2026-08-02 13:09:21 UTC

## Summary

| Metric | Baseline | Candidate | Delta |
|--------|----------|-----------|-------|
| Train Pass Rate | 70.6% | 97.1% | +26.5% |
| Val Pass Rate | 100.0% | 100.0% | +0.0% |

## Gate Decision

**Decision**: ✅ ACCEPT

**Reason**: All checks passed — improvement: +26.47%

## Candidate vs Baseline

| Case | Baseline | Candidate | Change |
|------|----------|-----------|--------|
| val_chinese_001 | ✅ | ✅ | — |
| val_chinese_002 | ✅ | ✅ | — |
| val_edge_001_tricky | ✅ | ✅ | — |
| val_edge_002_tricky | ✅ | ✅ | — |
| val_edge_003_tricky | ✅ | ✅ | — |
| val_format_001 | ✅ | ✅ | — |
| val_format_002 | ✅ | ✅ | — |
| val_multiturn_001 | ✅ | ✅ | — |
| val_multiturn_002_tricky | ✅ | ✅ | — |
| val_reasoning_001 | ✅ | ✅ | — |
| val_reasoning_002_tricky | ✅ | ✅ | — |
| val_reasoning_003_tricky | ✅ | ✅ | — |
| val_simple_math_001 | ✅ | ✅ | — |
| val_simple_math_002 | ✅ | ✅ | — |
| val_tool_001 | ✅ | ✅ | — |
| val_tool_002_tricky | ✅ | ✅ | — |

### Gate Checks

| Check | Result | Detail |
|-------|--------|--------|
| improvement_threshold | ✅ | Improvement: +26.47% (threshold: +5%) |
| critical_cases | ✅ | No critical cases regressed |
| new_failures | ✅ | No new failures |
| overfitting | ✅ | No validation regression |
| cost_budget | ✅ | Cost: $0.10 / $10.00 |

## Failure Attribution

Total failures: **10**

| Category | Count |
|----------|-------|
| final_response_mismatch | 9 |
| format_not_as_required | 1 |

## Validation Set Comparison

| Change | Count |
|--------|-------|
| New Passes | 0 |
| New Failures | 0 |
| Unchanged | 16 |

## Audit Trail

| Field | Value |
|-------|-------|
| Seed | 42 |
| Duration | 0.0s |
| Optimization Cost | $0.10 |
| Mode | fake |
| Reproduce | `python run_pipeline.py --mode fake` |

## Recommendations

- ✅ Accept the optimized prompt — improvement verified.
