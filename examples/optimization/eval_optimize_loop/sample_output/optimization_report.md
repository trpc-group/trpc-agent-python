# Optimization Report

- Run: `stage6_sample`
- Mode: `offline`
- Gate decision: ACCEPT
- Candidate: `fake-improve-60cc05b773e8`

## Full Evaluations

- Baseline train: 1/3 passed; average score=0.3333333333333333
- Baseline validation: 1/3 passed; average score=0.3333333333333333
- Candidate train: 3/3 passed; average score=1.0
- Candidate validation: 3/3 passed; average score=1.0

## Gate

- No rejection reasons or warnings.

## Candidate Changes

- system_prompt

## Overfit
- Status: not_detected
- Reason: Train score delta is 0.666667; validation score delta is 0.666667.

## Writeback
- Status: skipped
- Reason: disabled

## Pipeline Observations
- Cost: unavailable
- Tokens: unavailable
- Duration: available

## Optimizer Resources
- Rounds: not_applicable; unit=rounds; reason=Offline mode uses a deterministic candidate provider.
- Reflection calls: not_applicable; unit=calls; reason=Offline mode uses a deterministic candidate provider.
- Cost: not_applicable; unit=USD; reason=Offline mode uses a deterministic candidate provider.
- Token usage: not_applicable; unit=tokens; reason=Offline mode uses a deterministic candidate provider.
- Duration: not_applicable; unit=seconds; reason=Offline mode uses a deterministic candidate provider.

## Optimizer Scope
- Offline mode uses a deterministic candidate provider.
