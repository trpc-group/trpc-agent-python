#!/usr/bin/env bash
# Sample 19: python -c inline dangerous payload.
python -c "import os; os.system('rm -rf /tmp/x')"
