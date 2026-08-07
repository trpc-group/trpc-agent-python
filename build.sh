#!/usr/bin/env bash

set -e

show_help() {
    cat <<'EOF'
Usage:
  ./build.sh
  ./build.sh "[extras]"
  ./build.sh uv|pip "[extras]"
  ./build.sh help

Arguments:
  uv|pip    Dependency installer. Defaults to uv when omitted.
  extras    Optional dependency groups from pyproject.toml, written like
            pip extras: "[graph]" or "[dev,graph,a2a]".
            Defaults to "[dev]" when omitted.

Environment behavior:
  - Reuses a healthy project virtual environment first (.venv, then venv) and runs clean.sh against it.
  - Without a project venv, reuses an active environment or creates .venv.
  - clean.sh is skipped for active external or newly created environments.
  - Installs uv automatically when uv is selected but unavailable.
  - Disables pip/uv caches by default to better match cold user installs.
  - After a pip install, activate a newly created environment with:
      source .venv/bin/activate
      For example:
      source .venv/bin/activate
      python3 <command>
  - After a uv install, run commands without activation with:
      uv run --no-sync <command>

Examples:
  ./build.sh
      Install the [dev] extra with uv (default).

  ./build.sh "[graph]"
      Install the [graph] extra with uv (default installer).

  ./build.sh uv "[dev,graph]"
      Install development and LangGraph/DSL dependencies with uv.

  ./build.sh pip
      Install with pip ([dev] extra).

  ./build.sh pip "[dev,knowledge,knowledge-hf]"
      Install development and Knowledge/Hugging Face dependencies with pip.

  ./build.sh uv "[dev,graph,ag-ui,agent-claude,a2a]"
      Install dependencies needed by feature-specific test suites.

Optional environment variables:
  PYTHON_BIN   Python used to create or select the environment (default: python3).
  SKIP_CLEAN   Set to 1 to skip cleaning an existing project .venv.
  USE_CACHE    Set to 1 to allow pip/uv caches (default: disabled).
  PIP_INDEX_URL / UV_DEFAULT_INDEX
               Custom package indexes used by pip and uv.

Environment variable examples:
  PYTHON_BIN=python3.12 ./build.sh uv "[dev,graph]"
      Create/select the environment with Python 3.12.

  SKIP_CLEAN=1 ./build.sh uv "[dev]"
      Keep packages already installed in the existing project .venv.

  USE_CACHE=1 ./build.sh uv "[dev]"
      Reuse local pip/uv caches for faster repeated installs.

  PIP_INDEX_URL=https://mirror.example/simple ./build.sh pip
      Install through a custom pip package index.

  PIP_INDEX_URL=https://mirror.example/simple \
  UV_DEFAULT_INDEX=https://mirror.example/simple \
  ./build.sh uv "[dev,graph]"
      Install uv itself and project dependencies through a custom index.
EOF
}

normalize_extras() {
    local raw="${1:?extras required}"
    raw="${raw//[[:space:]]/}"
    if [[ "${raw}" == \[*\] ]]; then
        raw="${raw:1:${#raw}-2}"
    fi
    if [[ -z "${raw}" ]]; then
        echo "Extras list cannot be empty. Use e.g. \"[graph]\" or \"[dev,graph]\"." >&2
        exit 2
    fi
    printf '%s\n' "${raw}"
}

case "${1:-}" in
    help|-h|--help)
        show_help
        exit 0
        ;;
esac

PYTHON_BIN="${PYTHON_BIN:-python3}"
CREATED_VENV=0
RUN_CLEAN=0
USE_CACHE="${USE_CACHE:-0}"
PIP_CACHE_ARGS=()
UV_CACHE_ARGS=()

# Parse: ./build.sh
#        ./build.sh "[extras]"
#        ./build.sh uv|pip
#        ./build.sh uv|pip "[extras]"
INSTALLER="uv"
EXTRAS="dev"

if (( $# > 2 )); then
    show_help >&2
    exit 2
elif (( $# == 0 )); then
    :
elif [[ "$1" == "pip" || "$1" == "uv" ]]; then
    INSTALLER="$1"
    if (( $# == 2 )); then
        EXTRAS="$(normalize_extras "$2")"
    fi
else
    # First argument is extras like "[graph]"; installer stays uv.
    EXTRAS="$(normalize_extras "$1")"
    if (( $# == 2 )); then
        echo "Unexpected second argument '$2' after extras '$1'." >&2
        echo "Use: ./build.sh \"[extras]\"  or  ./build.sh uv|pip \"[extras]\"" >&2
        echo "Run './build.sh help' for usage." >&2
        exit 2
    fi
fi

INSTALL_SPEC=".[${EXTRAS}]"

case "${INSTALLER}" in
    pip|uv) ;;
    *)
        echo "Unsupported INSTALLER=${INSTALLER}; use pip or uv." >&2
        echo "Run './build.sh help' for usage." >&2
        exit 2
        ;;
esac

echo "Installer: ${INSTALLER}; extras: [${EXTRAS}]"

if [[ "${USE_CACHE}" != "1" ]]; then
    export PIP_NO_CACHE_DIR=1
    export UV_NO_CACHE=1
    PIP_CACHE_ARGS=(--no-cache-dir)
    UV_CACHE_ARGS=(--no-cache)
    echo "Package caches disabled (cold-install mode)."
else
    echo "Package caches enabled."
fi

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
if [[ "${SKIP_CLEAN:-0}" == "1" ]] && is_virtualenv_python "${PYTHON_BIN}"; then
    echo "Using the explicitly selected virtual environment; clean.sh will be skipped."
elif PROJECT_VENV_DIR="$(resolve_project_venv)"; then
    echo "Reusing existing project virtual environment: ${PROJECT_VENV_DIR}"
    PYTHON_BIN="$(pwd)/${PROJECT_VENV_DIR}/bin/python"
    RUN_CLEAN=1
elif is_virtualenv_python "${PYTHON_BIN}"; then
    echo "Using the active virtual environment; clean.sh will be skipped."
else
    if [[ -e .venv || -L .venv ]]; then
        echo "Existing .venv is invalid; removing it before recreation..."
        rm -rf .venv
    fi
    echo "No project virtual environment detected; creating .venv..."
    if ! "${PYTHON_BIN}" -m venv .venv; then
        rm -rf .venv
        echo "Failed to create .venv with ${PYTHON_BIN}." >&2
        echo "Ensure the Python venv/ensurepip component is installed, then retry." >&2
        exit 1
    fi
    PROJECT_VENV_DIR=".venv"
    PYTHON_BIN="$(pwd)/.venv/bin/python"
    CREATED_VENV=1
fi

# Activate the selected environment inside this script so helper scripts that
# call pip/pip3 also operate on the same environment.
VENV_DIR="$("${PYTHON_BIN}" -c 'import sys; print(sys.prefix)')"
if [[ -f "${VENV_DIR}/bin/activate" ]]; then
    source "${VENV_DIR}/bin/activate"
else
    export PATH="${VENV_DIR}/bin:${PATH}"
fi
PYTHON_BIN="${VENV_DIR}/bin/python"
echo "Using virtual environment: ${VENV_DIR}"

if [[ "${RUN_CLEAN}" == "1" && "${SKIP_CLEAN:-0}" != "1" ]]; then
    echo "Cleaning the existing project virtual environment..."
    sh clean.sh
fi

case "${INSTALLER}" in
    pip)
        "${PYTHON_BIN}" -m pip install "${PIP_CACHE_ARGS[@]}" --upgrade pip
        "${PYTHON_BIN}" -m pip install "${PIP_CACHE_ARGS[@]}" -r requirements.txt
        "${PYTHON_BIN}" -m pip install "${PIP_CACHE_ARGS[@]}" -r requirements-test.txt
        "${PYTHON_BIN}" -m pip install "${PIP_CACHE_ARGS[@]}" --editable "${INSTALL_SPEC}"
        # 检查依赖解析
        "${PYTHON_BIN}" -m pip install "${PIP_CACHE_ARGS[@]}" --dry-run .
        ;;
    uv)
        # uv 只是开发安装工具，不需要加入项目运行时依赖。
        if ! "${PYTHON_BIN}" -m uv --version >/dev/null 2>&1; then
            "${PYTHON_BIN}" -m pip install "${PIP_CACHE_ARGS[@]}" --upgrade uv
        fi
        "${PYTHON_BIN}" -m uv pip install \
            "${UV_CACHE_ARGS[@]}" \
            --python "${PYTHON_BIN}" \
            --editable "${INSTALL_SPEC}"
        "${PYTHON_BIN}" -m uv pip check --python "${PYTHON_BIN}"
        # 检查依赖解析
        "${PYTHON_BIN}" -m uv pip install \
            "${UV_CACHE_ARGS[@]}" \
            --python "${PYTHON_BIN}" \
            --dry-run .
        ;;
esac

case "${INSTALLER}" in
    pip)
        if [[ "${CREATED_VENV}" == "1" ]]; then
            echo "Virtual environment created at ${PROJECT_VENV_DIR:-.venv}"
            echo "Activate it in your shell with: source ${PROJECT_VENV_DIR:-.venv}/bin/activate"
            echo "For example:"
            echo "source ${PROJECT_VENV_DIR:-.venv}/bin/activate"
            echo " python3 <command>"
        else
            echo "Installation completed in the active virtual environment: ${VENV_DIR}"
            echo "For example:"
            echo "python3 -m venv .venv && source .venv/bin/activate"
            echo " python3 <command>"
        fi
        ;;
    uv)
        echo "Run project commands without activating the environment:"
        echo " uv run --no-sync <command>"
        echo "For example:"
        echo " uv run --no-sync pytest"
        ;;
esac
