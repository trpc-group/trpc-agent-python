# Counterfactual Trace Feasibility Probe

Supported: **true**

The probe uses trace-mode `EvalCase` objects and the public `AgentEvaluator.get_executer()` API for baseline and counterfactual scoring.

| Case | Intervention | Legal | Fail to pass | Repaired metrics | Unchanged metrics |
|---|---|---:|---:|---|---|
| probe_final_response | replace_final_response | true | true | final_response_avg_score | tool_trajectory_avg_score |
| probe_final_response | replace_tool_name | false | false | - | - |
| probe_final_response | replace_tool_arguments | false | false | - | - |
| probe_final_response | replace_tool_name_and_arguments | false | false | - | - |
| probe_final_response | replace_tool_name+final_response | false | false | - | - |
| probe_final_response | replace_tool_arguments+final_response | false | false | - | - |
| probe_final_response | normalize_format | false | false | - | - |
| probe_tool_name | replace_final_response | true | false | - | final_response_avg_score, tool_trajectory_avg_score |
| probe_tool_name | replace_tool_name | true | true | tool_trajectory_avg_score | final_response_avg_score |
| probe_tool_name | replace_tool_arguments | true | false | - | final_response_avg_score, tool_trajectory_avg_score |
| probe_tool_name | replace_tool_name_and_arguments | true | true | tool_trajectory_avg_score | final_response_avg_score |
| probe_tool_name | replace_tool_name+final_response | true | true | tool_trajectory_avg_score | final_response_avg_score |
| probe_tool_name | replace_tool_arguments+final_response | true | false | - | final_response_avg_score, tool_trajectory_avg_score |
| probe_tool_name | normalize_format | false | false | - | - |
| probe_compound_tool | replace_final_response | true | false | - | final_response_avg_score, tool_trajectory_avg_score |
| probe_compound_tool | replace_tool_name | true | false | - | final_response_avg_score, tool_trajectory_avg_score |
| probe_compound_tool | replace_tool_arguments | true | false | - | final_response_avg_score, tool_trajectory_avg_score |
| probe_compound_tool | replace_tool_name_and_arguments | true | true | tool_trajectory_avg_score | final_response_avg_score |
| probe_compound_tool | replace_tool_name+final_response | true | false | - | final_response_avg_score, tool_trajectory_avg_score |
| probe_compound_tool | replace_tool_arguments+final_response | true | false | - | final_response_avg_score, tool_trajectory_avg_score |
| probe_compound_tool | normalize_format | false | false | - | - |

## Conclusion

Official trace metrics distinguish final-response, tool-name, and tool-argument interventions; the compound case passes only after the combined tool-name-and-arguments intervention.
