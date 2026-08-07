#!/bin/bash

export DISABLE_TRPC_AGENT_REPORT=true

set -e

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

# File Tools
cd examples/file_tools/
python3 run_agent.py
cd -

# Filter with Agent
cd examples/filter_with_agent/
python3 run_agent.py
cd -

# Filter with Model
cd examples/filter_with_model/
python3 run_agent.py
cd -

# Filter with Tool
cd examples/filter_with_tool/
python3 run_agent.py
cd -

# Session&Memory
python3 examples/session_state/run_agent.py
python3 examples/session_summarizer/run_agent.py

# Tools
# python3 examples/tools/mcp_tools/mcp_tools.py
python3 examples/mcp_tools/run_agent.py
