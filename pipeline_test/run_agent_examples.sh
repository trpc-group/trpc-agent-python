#!/usr/bin/env bash

set -euo pipefail

export DISABLE_TRPC_AGENT_REPORT="${DISABLE_TRPC_AGENT_REPORT:-true}"

CORE_AGENT_EXAMPLES=(
    "examples/quickstart/"
    "examples/llmagent/"
    "examples/llmagent_with_schema/"
)

GRAPH_EXAMPLES=(
    "examples/graph/"
)

MULTI_AGENT_EXAMPLES=(
    "examples/multi_agent_chain/"
    "examples/multi_agent_parallel/"
)

TEAM_AGENT_EXAMPLES=(
    "examples/team/"
    "examples/team_as_sub_agent/"
)

run_example() {
    local example="$1"

    echo ""
    echo "=== Running ${example} ==="

    if [[ "$example" == *.py ]]; then
        if [[ ! -f "$example" ]]; then
            echo "Example file not found: $example" >&2
            exit 1
        fi
        python3 "$example"
        return
    fi

    if [[ ! -d "$example" ]]; then
        echo "Example directory not found: $example" >&2
        exit 1
    fi

    if [[ ! -f "${example%/}/run_agent.py" ]]; then
        echo "Example run_agent.py not found: ${example%/}/run_agent.py" >&2
        exit 1
    fi

    (
        cd "$example"
        python3 run_agent.py
    )
}

run_examples() {
    local examples=("$@")

    for example in "${examples[@]}"; do
        run_example "$example"
    done
}

run_core_agent() {
    echo "=== Running core agent examples ==="
    run_examples "${CORE_AGENT_EXAMPLES[@]}"
}

run_graph_agent() {
    echo "=== Running graph examples ==="
    run_examples "${GRAPH_EXAMPLES[@]}"
}

run_multi_agent() {
    echo "=== Running multi-agent examples ==="
    run_examples "${MULTI_AGENT_EXAMPLES[@]}"
}

run_team_agent() {
    echo "=== Running team-agent examples ==="
    run_examples "${TEAM_AGENT_EXAMPLES[@]}"
}

run_all() {
    run_core_agent
    run_graph_agent
    run_multi_agent
    run_team_agent
}

show_usage() {
    echo "Usage: $0 [core|graph|multi_agent|team_agent|all]"
    echo ""
    echo "Suites:"
    echo "  core         Run quickstart and basic LlmAgent examples."
    echo "  graph        Run the minimal GraphAgent example."
    echo "  multi_agent  Run basic multi-agent orchestration examples."
    echo "  team_agent   Run basic TeamAgent examples."
    echo "  all          Run every suite above."
    echo ""
    echo "If no suite is provided, core examples will be run."
}

suite="${1:-core}"

case "$suite" in
    core)
        run_core_agent
        ;;
    graph)
        run_graph_agent
        ;;
    multi_agent)
        run_multi_agent
        ;;
    team_agent)
        run_team_agent
        ;;
    all)
        run_all
        ;;
    -h|--help)
        show_usage
        ;;
    *)
        echo "Error: unknown suite '$suite'" >&2
        show_usage
        exit 1
        ;;
esac
