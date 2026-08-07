#!/bin/bash

export DISABLE_TRPC_AGENT_REPORT=true

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "$REPO_ROOT"

is_virtualenv_python() {
    local python_bin="$1"
    [[ -x "${python_bin}" ]] && "${python_bin}" -c \
        'import sys; raise SystemExit(0 if sys.prefix != sys.base_prefix else 1)' >/dev/null 2>&1
}

resolve_project_venv() {
    local candidate
    for candidate in .venv venv; do
        if is_virtualenv_python "${candidate}/bin/python"; then
            printf '%s\n' "${candidate}"
            return 0
        fi
    done
    return 1
}

PROJECT_VENV_DIR=""
if ! PROJECT_VENV_DIR="$(resolve_project_venv)"; then
    echo "Error: no project virtual environment found (.venv or venv)." >&2
    echo "Create one first, for example: ./build.sh" >&2
    exit 1
fi

echo "Using project virtual environment: ${PROJECT_VENV_DIR}"
# shellcheck source=/dev/null
source "${PROJECT_VENV_DIR}/bin/activate"
# export PYTHON_BIN="${REPO_ROOT}/${PROJECT_VENV_DIR}/bin/python"

# shellcheck source=pipeline_test/_install_deps.sh
source "${SCRIPT_DIR}/_install_deps.sh"

# AgentEvaluator needs rouge-score / pandas / pytest from [eval]
pipeline_uv_install_extras "eval"

# Evaluation
cd examples/evaluation/quickstart && pytest test_quickstart.py -v -s && cd -
cd examples/evaluation/webui && pytest test_book_finder.py -v -s && cd -
cd examples/evaluation/callbacks && pytest test_callbacks.py -v -s && cd -
cd examples/evaluation/custom_runner && pytest test_custom_runner.py -v -s && cd -
cd examples/evaluation/context_messages && pytest test_context_messages.py -v -s && cd -
cd examples/evaluation/trace_mode && pytest test_trace_mode.py -v -s && cd -
cd examples/evaluation/pass_at_k && pytest test_pass_at_k.py -v -s && cd -
