#!/usr/bin/env bash
# Shared uv-based installer for pipeline_test scripts.
# Default: cache enabled for faster repeated CI/local runs.
# Disable with: USE_CACHE=0

pipeline_ensure_uv() {
    local python_bin="${PYTHON_BIN:-python3}"

    if command -v uv >/dev/null 2>&1; then
        return 0
    fi

    if "${python_bin}" -m uv --version >/dev/null 2>&1; then
        return 0
    fi

    echo "uv not found; installing uv..."
    "${python_bin}" -m pip install --upgrade uv
}

pipeline_uv_cmd() {
    local python_bin="${PYTHON_BIN:-python3}"

    if command -v uv >/dev/null 2>&1; then
        uv "$@"
    else
        "${python_bin}" -m uv "$@"
    fi
}

pipeline_uv_cache_args() {
    # Cache on by default in pipeline_test (opposite of build.sh cold-install default).
    if [[ "${USE_CACHE:-1}" == "1" ]]; then
        echo "uv package cache enabled (USE_CACHE=1)." >&2
        return 0
    fi

    echo "uv package cache disabled (USE_CACHE=0)." >&2
    printf '%s\n' --no-cache
}

# Usage:
#   pipeline_uv_install_extras "graph,a2a"
#   pipeline_uv_install_extras "eval"
pipeline_uv_install_extras() {
    local extras="${1:?extras required}"
    local python_bin="${PYTHON_BIN:-python3}"
    local cache_args=()
    local arg

    pipeline_ensure_uv

    while IFS= read -r arg; do
        [[ -n "${arg}" ]] && cache_args+=("${arg}")
    done < <(pipeline_uv_cache_args)

    echo "Installing editable package with extras: [${extras}]"
    pipeline_uv_cmd pip install \
        "${cache_args[@]}" \
        --python "${python_bin}" \
        -e ".[${extras}]"
}

# Usage:
#   pipeline_uv_install_requirements "pipeline_test/requirements.txt"
pipeline_uv_install_requirements() {
    local req_file="${1:?requirements file required}"
    local python_bin="${PYTHON_BIN:-python3}"
    local cache_args=()
    local arg

    pipeline_ensure_uv

    while IFS= read -r arg; do
        [[ -n "${arg}" ]] && cache_args+=("${arg}")
    done < <(pipeline_uv_cache_args)

    echo "Installing requirements from: ${req_file}"
    pipeline_uv_cmd pip install \
        "${cache_args[@]}" \
        --python "${python_bin}" \
        -r "${req_file}"
}
