#!/usr/bin/env bash
#
# uv-based development setup for trpc-agent-python (macOS).
#
# This is a standalone alternative to build_mac.sh (which uses pip).
# It uses uv to manage the Python toolchain, virtualenv and dependencies.
#
# Usage:
#   bash build_mac_uv.sh                 # core + dev extra
#   EXTRAS="a2a knowledge" bash build_mac_uv.sh   # also install extras
#
set -euo pipefail

# Make sure uv's default install location is on PATH before probing for uv,
# so a previously installed uv is reused instead of reinstalled every run.
export PATH="$HOME/.local/bin:$PATH"

# 1. Ensure uv is available.
if ! command -v uv >/dev/null 2>&1; then
    echo "[build_mac_uv] uv not found, installing..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

echo "[build_mac_uv] uv version: $(uv --version)"

# 2. Create the virtual environment using the user's local Python.
uv venv --python-preference only-system

# 3. Sync dependencies: core + the `dev` extra, plus any requested extras.
EXTRA_ARGS=()
for e in ${EXTRAS:-}; do
    EXTRA_ARGS+=("--extra" "$e")
done
uv sync --extra dev ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}

# 4. Smoke test the installation.
uv run python -c "import trpc_agent_sdk; from trpc_agent_sdk.version import __version__; print(f'trpc-agent-py {__version__} installed via uv')"

echo "[build_mac_uv] Done. Activate the env with: source .venv/bin/activate"
