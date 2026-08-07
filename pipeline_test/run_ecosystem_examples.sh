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

# A2A + Claude team member examples
pipeline_uv_install_extras "a2a,agent-claude"
pipeline_uv_install_requirements "pipeline_test/requirements-ecosystem.txt"

# 启动A2A服务端（后台运行）
echo "启动A2A服务端..."
python3 examples/a2a/trpc_main.py &
SERVER_PID=$!

# 等待服务端启动
sleep 5

# 运行A2A客户端测试
echo "运行A2A客户端测试..."
python3 examples/a2a/test_a2a.py
# python3 examples/a2a/raw_client.py
# python3 examples/a2a/client.py

# # TeamAgent with Remote A2A Member
# echo "运行 TeamAgent with Remote A2A Member..."
# cd examples/team_member_agent_remote_a2a/
# python3 run_agent.py
# cd -

# 停止服务端
echo "停止A2A服务端..."
kill $SERVER_PID 2>/dev/null || true

# TeamAgent with Claude Member
echo "运行 TeamAgent with Claude Member..."
cd examples/team_member_agent_claude/
python3 run_agent.py
cd -

# python3 examples/ecosystem/langchain_knowledge/custom_document_loader.py
# python3 examples/ecosystem/langchain_knowledge/custom_retriever.py
# python3 examples/ecosystem/langchain_knowledge/custom_text_splitter.py
