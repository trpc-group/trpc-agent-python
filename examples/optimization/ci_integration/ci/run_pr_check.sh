#!/bin/bash
# PR 阶段：跑 pytest 守护质量底线。
# - 失败时 pytest exit code != 0 → CI 红灯
# - --junitxml 产出 GitHub Actions / Tencent CI / 蓝盾流水线均原生支持的报告
set -euo pipefail
cd "$(dirname "$0")/.."

mkdir -p runs

PY="${PYTHON:-python3}"
exec "$PY" -m pytest tests/ -v --tb=short \
    --junitxml=runs/pytest_report.xml
