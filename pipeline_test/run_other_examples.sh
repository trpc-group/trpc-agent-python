#!/bin/bash

export DISABLE_TRPC_AGENT_REPORT=true

set -e

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