#!/bin/bash

export DISABLE_TRPC_AGENT_REPORT=true

set -u
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

RUN_MODE="all"
FAIL_FAST=false
INCLUDE_MANUAL=false
EXAMPLE_TIMEOUT_SECONDS="${EXAMPLE_TIMEOUT_SECONDS:-500}"

PASSED=()
FAILED=()
SKIPPED=()
RUN_AGENT_EXAMPLES=()
EVALUATION_TESTS=()

TOTAL_TASKS=0
CURRENT_TASK=0

SKIPPED_EXAMPLES=(
    "examples/claude_agent_with_travel_planner/run_agent.py"
    "examples/dsl/classifier_mcp/run_agent.py"
    "examples/evaluation/webui/test_book_finder.py"
    "examples/knowledge_with_vectorstore/run_agent.py"
    "examples/mem0_tools/run_agent.py"
    "examples/memory_service_with_mem0/run_agent.py"
    "examples/memory_service_with_mempalace/run_agent.py"
    "examples/memory_service_with_redis/run_agent.py"
    "examples/memory_service_with_sql/run_agent.py"
    "examples/mempalace_tools/run_agent.py"
    "examples/session_service_with_redis/run_agent.py"
    "examples/session_service_with_sql/run_agent.py"
    "examples/skills_hub/run_agent.py"
    "examples/skills_with_container/run_agent.py"
    "examples/skills_with_cube/run_agent.py"
)

show_usage() {
    cat <<EOF
Usage: $0 [all|run-agent|evaluation|a2a] [options]

Run examples under examples/.

Modes:
  all          Run run_agent.py examples, evaluation tests, and A2A example.
  run-agent    Run every discovered examples/**/run_agent.py.
  evaluation   Run every examples/evaluation/**/test_*.py.
  a2a          Run the A2A server/client example.

Options:
  --fail-fast        Stop on the first failure.
  --include-manual   Include examples that are skipped by default.
  -h, --help         Show this help.

Environment:
  EXTRA_SKIP_EXAMPLES  Space-separated run_agent.py paths to skip.
  EXAMPLE_TIMEOUT_SECONDS
                       Max seconds for each example before marking it failed.
EOF
}

is_in_list() {
    local needle="$1"
    shift

    local item
    for item in "$@"; do
        if [[ "$item" == "$needle" ]]; then
            return 0
        fi
    done

    return 1
}

record_result() {
    local status="$1"
    local name="$2"

    case "$status" in
        pass)
            PASSED+=("$name")
            ;;
        fail)
            FAILED+=("$name")
            ;;
        skip)
            SKIPPED+=("$name")
            ;;
    esac
}

show_progress() {
    local current="$1"
    local total="$2"
    local name="$3"
    local percent=0

    if ((total > 0)); then
        percent=$((current * 100 / total))
    fi

    echo
    printf '[%d/%d] %3d%% %s\n' "$current" "$total" "$percent" "$name"
}

skip_example() {
    local name="$1"
    local reason="$2"

    CURRENT_TASK=$((CURRENT_TASK + 1))
    show_progress "$CURRENT_TASK" "$TOTAL_TASKS" "Skipping: ${name}"
    echo "Skipping ${name}: ${reason}"
    record_result skip "$name"
}

should_skip() {
    local name="$1"
    shift

    if [[ "$INCLUDE_MANUAL" != true ]] && is_in_list "$name" "${SKIPPED_EXAMPLES[@]}"; then
        SKIP_REASON="skipped by default"
        return 0
    fi

    if (($# > 0)) && is_in_list "$name" "$@"; then
        SKIP_REASON="listed in EXTRA_SKIP_EXAMPLES"
        return 0
    fi

    return 1
}

run_command() {
    local name="$1"
    shift
    local exit_code

    CURRENT_TASK=$((CURRENT_TASK + 1))
    show_progress "$CURRENT_TASK" "$TOTAL_TASKS" "Running: ${name}"
    echo "============================================================"
    echo "Running: ${name}"
    echo "Command: $*"
    echo "============================================================"

    "$@"
    exit_code=$?

    if [[ "$exit_code" -eq 0 ]]; then
        record_result pass "$name"
        return 0
    else
        echo "FAILED: ${name} (exit code: ${exit_code})"
        record_result fail "$name"

        if [[ "$exit_code" -eq 130 ]]; then
            print_summary
            exit "$exit_code"
        fi

        if [[ "$FAIL_FAST" == true ]]; then
            print_summary
            exit "$exit_code"
        fi

        return "$exit_code"
    fi
}

run_python_file_from_dir() {
    local file_path="$1"
    local work_dir
    local file_name

    work_dir="$(dirname "$file_path")"
    file_name="$(basename "$file_path")"

    (
        cd "${REPO_ROOT}/${work_dir}"
        timeout "$EXAMPLE_TIMEOUT_SECONDS" python3 "$file_name"
    )
}

run_pytest_file_from_dir() {
    local file_path="$1"
    local work_dir
    local file_name

    work_dir="$(dirname "$file_path")"
    file_name="$(basename "$file_path")"

    (
        cd "${REPO_ROOT}/${work_dir}"
        timeout "$EXAMPLE_TIMEOUT_SECONDS" pytest "$file_name" -v -s
    )
}

run_discovered_agents() {
    local extra_skip=()
    local example
    if [[ -n "${EXTRA_SKIP_EXAMPLES:-}" ]]; then
        read -r -a extra_skip <<< "${EXTRA_SKIP_EXAMPLES}"
    fi

    for example in "${RUN_AGENT_EXAMPLES[@]}"; do
        if should_skip "$example" "${extra_skip[@]}"; then
            skip_example "$example" "$SKIP_REASON"
            continue
        fi

        run_command "$example" run_python_file_from_dir "$example"
    done
}

run_evaluation_tests() {
    local test_file
    for test_file in "${EVALUATION_TESTS[@]}"; do
        if should_skip "$test_file"; then
            skip_example "$test_file" "$SKIP_REASON"
            continue
        fi

        run_command "$test_file" run_pytest_file_from_dir "$test_file"
    done
}

wait_for_port() {
    local host="$1"
    local port="$2"
    local timeout_seconds="$3"

    python3 - "$host" "$port" "$timeout_seconds" <<'PY'
import socket
import sys
import time

host = sys.argv[1]
port = int(sys.argv[2])
deadline = time.time() + int(sys.argv[3])

while time.time() < deadline:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1)
        if sock.connect_ex((host, port)) == 0:
            sys.exit(0)
    time.sleep(1)

sys.exit(1)
PY
}

run_a2a_example() {
    local server_pid=""
    local test_status=0

    (
        cd "${REPO_ROOT}/examples/a2a"
        exec python3 run_server.py
    ) &
    server_pid=$!

    cleanup_a2a() {
        if [[ -n "$server_pid" ]]; then
            pkill -TERM -P "$server_pid" 2>/dev/null || true
            kill "$server_pid" 2>/dev/null || true
            sleep 2
            pkill -KILL -P "$server_pid" 2>/dev/null || true
            kill -KILL "$server_pid" 2>/dev/null || true
            wait "$server_pid" 2>/dev/null || true
        fi
    }

    if ! wait_for_port "127.0.0.1" "18081" "30"; then
        echo "FAILED: examples/a2a server did not start on 127.0.0.1:18081"
        cleanup_a2a
        return 1
    fi

    (
        cd "${REPO_ROOT}/examples/a2a"
        timeout "$EXAMPLE_TIMEOUT_SECONDS" python3 test_a2a.py
    )
    test_status=$?
    cleanup_a2a
    return "$test_status"
}

discover_run_agent_examples() {
    mapfile -t RUN_AGENT_EXAMPLES < <(
        cd "$REPO_ROOT"
        find examples -path "*/run_agent.py" -type f | sort
    )
}

discover_evaluation_tests() {
    mapfile -t EVALUATION_TESTS < <(
        cd "$REPO_ROOT"
        find examples/evaluation -name "test_*.py" -type f | sort
    )
}

prepare_tasks() {
    case "$RUN_MODE" in
        all)
            discover_run_agent_examples
            discover_evaluation_tests
            TOTAL_TASKS=$((${#RUN_AGENT_EXAMPLES[@]} + ${#EVALUATION_TESTS[@]} + 1))
            ;;
        run-agent)
            discover_run_agent_examples
            TOTAL_TASKS=${#RUN_AGENT_EXAMPLES[@]}
            ;;
        evaluation)
            discover_evaluation_tests
            TOTAL_TASKS=${#EVALUATION_TESTS[@]}
            ;;
        a2a)
            TOTAL_TASKS=1
            ;;
    esac
}

print_summary() {
    echo
    echo "==================== Example Run Summary ===================="
    echo "Passed : ${#PASSED[@]}"
    echo "Failed : ${#FAILED[@]}"
    echo "Skipped: ${#SKIPPED[@]}"

    if ((${#FAILED[@]} > 0)); then
        echo
        echo "Failed examples:"
        printf '  - %s\n' "${FAILED[@]}"
    fi
}

while (($# > 0)); do
    case "$1" in
        all|run-agent|evaluation|a2a)
            RUN_MODE="$1"
            ;;
        --fail-fast)
            FAIL_FAST=true
            ;;
        --include-manual)
            INCLUDE_MANUAL=true
            ;;
        -h|--help)
            show_usage
            exit 0
            ;;
        *)
            echo "Error: unknown argument '$1'"
            show_usage
            exit 1
            ;;
    esac
    shift
done

cd "$REPO_ROOT"
prepare_tasks

case "$RUN_MODE" in
    all)
        run_discovered_agents
        run_evaluation_tests
        run_command "examples/a2a" run_a2a_example
        ;;
    run-agent)
        run_discovered_agents
        ;;
    evaluation)
        run_evaluation_tests
        ;;
    a2a)
        run_command "examples/a2a" run_a2a_example
        ;;
esac

print_summary

if ((${#FAILED[@]} > 0)); then
    exit 1
fi
