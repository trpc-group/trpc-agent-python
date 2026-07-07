#!/bin/bash

export DISABLE_TRPC_AGENT_REPORT=true

set -e

pip3 install -r pipeline_test/requirements.txt

# Define example categories
LLM_AGENT_EXAMPLES=(
    "examples/quickstart/"
    "examples/llmagent/"
    "examples/llmagent_with_cancel/"
    "examples/llmagent_with_model_create_fn/"
    "examples/llmagent_with_parallal_tools/"
    "examples/llmagent_with_schema/"
    "examples/llmagent_with_thinking/"
    "examples/llmagent_with_tool_prompt/"
    "examples/llmagent_with_streaming_progress_tool/"
    # "examples/memory_service_with_mempalace/"
    "examples/webfetch_tool/"
    "examples/websearch_tool/"
    "examples/code_executors/"
    "examples/litellm/"
    "examples/graph/"
    "examples/graph_multi_turns/"
    "examples/graph_with_interrupt/"
    "examples/agents/human_in_the_loop/llm_agent.py"
    "examples/agents/history_control/max_history_messages.py"
    "examples/agents/history_control/timeline_filtering.py"
    "examples/agents/history_control/branch_filtering.py"
)

MULTI_AGENT_EXAMPLES=(
    "examples/multi_agent_chain/"
    "examples/multi_agent_compose/"
    "examples/multi_agent_cycle/"
    "examples/multi_agent_parallel/"
    "examples/multi_agent_start_from_last/"
    "examples/multi_agent_subagent/"
)

TEAM_AGENT_EXAMPLES=(
    "examples/team/"
    "examples/team_as_sub_agent/"
    "examples/team_human_in_the_loop/"
    "examples/team_member_agent_langgraph/"
    "examples/team_member_agent_team/"
    "examples/team_member_message_filter/"
    "examples/team_parallel_execution/"
    "examples/team_with_cancel/"
)

run_examples() {
    local examples=("$@")
    for example in "${examples[@]}"; do
        echo "Running $example..."
        if [[ "$example" == *.py ]]; then
            python3 "$example"
        else
            cd "$example"
            python3 run_agent.py
            cd -
        fi
    done
}

run_llm_agent() {
    echo "=== Running LLM Agent Examples ==="
    run_examples "${LLM_AGENT_EXAMPLES[@]}"
}

run_multi_agent() {
    echo "=== Running Multi Agent Examples ==="
    run_examples "${MULTI_AGENT_EXAMPLES[@]}"
}

run_team_agent() {
    echo "=== Running Team Agent Examples ==="
    run_examples "${TEAM_AGENT_EXAMPLES[@]}"
}

run_all() {
    run_llm_agent
    run_multi_agent
    run_team_agent
}

show_usage() {
    echo "Usage: $0 [llm_agent|multi_agent|team_agent]"
    echo ""
    echo "Parameters:"
    echo "  llm_agent    - Run LLM agent examples (quickstart, llmagent_*, history_control, etc.)"
    echo "  multi_agent  - Run multi agent examples (multi_agent_*)"
    echo "  team_agent   - Run team agent examples (team_*)"
    echo ""
    echo "If no parameter is provided, all examples will be run."
}

# Main logic
if [ $# -eq 0 ]; then
    run_all
else
    case "$1" in
        llm_agent)
            run_llm_agent
            ;;
        multi_agent)
            run_multi_agent
            ;;
        team_agent)
            run_team_agent
            ;;
        -h|--help)
            show_usage
            exit 0
            ;;
        *)
            echo "Error: Unknown parameter '$1'"
            show_usage
            exit 1
            ;;
    esac
fi