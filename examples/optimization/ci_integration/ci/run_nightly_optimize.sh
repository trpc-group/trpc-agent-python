#!/bin/bash
# 夜间窗口：跑 GEPA 优化，把最优 prompt 直接写回源文件。
# 真实 CI 里通常会在末尾追加 `git diff` 看是否有改动，再开 PR。
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-python3}"
"$PY" run_optimization.py

echo ""
echo "=== Optimization done ==="
echo "Best prompts have been written back to:"
echo "  agent/prompts/system.md"
echo "  agent/prompts/skill.md"
echo ""
echo "Next steps for a real CI pipeline:"
echo "  git diff agent/prompts/   # see what GEPA changed"
echo "  git checkout -b auto/optimize-\$(date +%Y%m%d)"
echo "  git add agent/prompts/ && git commit -m 'auto: optimize prompts'"
echo "  # then open a PR; PR check (run_pr_check.sh) re-validates the new prompts."
