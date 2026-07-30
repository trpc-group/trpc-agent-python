#!/usr/bin/env bash

set -euo pipefail

# Usage:
#   bash lint_flake8.sh                # check current project
#   bash lint_flake8.sh path/to/check  # check a specific path

TARGET_PATH="${1:-.}"

if ! command -v flake8 >/dev/null 2>&1; then
  echo "flake8 is not installed. Install it first:"
  echo "  python3 -m pip install flake8"
  exit 1
fi

echo "Running flake8 on: ${TARGET_PATH}"

flake8 "${TARGET_PATH}" \
  --max-line-length=120 \
  --extend-exclude=".git,__pycache__,.pytest_cache,.mypy_cache,.ruff_cache,venv,.venv,build,dist,node_modules"
