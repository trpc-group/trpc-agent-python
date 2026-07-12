# Trust-Aware Counterfactual Optimization Report

Gate: **REJECTED**

## Baseline failures

- `train_route`: tool_selection_error (actionable=True)
- `train_args`: tool_parameter_error (actionable=True)
- `train_json`: final_response_mismatch (actionable=True)

## Candidate changes

- `val_refund`: new_pass
- `val_shipping`: unchanged
- `val_billing`: new_fail

## Counterfactual regression diagnosis

- `val_billing`: tool_selection_error via replace_tool_name

## Decision

- Reasons: VALIDATION_DELTA, NEW_HARD_FAIL, PROTECTED_REGRESSION, TRAIN_ONLY_IMPROVEMENT, SEVERITY_ESCALATION
- Source write-back: False

## Optimization

- Target prompts: router_prompt, skill_prompt, system_prompt
- Candidate profile: overfit

## Known limitations

- A local trace edit can be structurally valid but semantically incoherent with an original tool response.
- LLM-judge variance auditing requires repeated real-judge samples and is not exercised by this deterministic example.
- Real optimizer wiring is mock-verified; production execution requires credentials, call_agent, and trace capture.
