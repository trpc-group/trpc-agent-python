#!/bin/bash

export DISABLE_TRPC_AGENT_REPORT=true

set -e

# Evaluation
cd examples/evaluation/quickstart && pytest test_quickstart.py -v -s && cd -
cd examples/evaluation/webui && pytest test_book_finder.py -v -s && cd -
cd examples/evaluation/callbacks && pytest test_callbacks.py -v -s && cd -
cd examples/evaluation/custom_runner && pytest test_custom_runner.py -v -s && cd -
cd examples/evaluation/context_messages && pytest test_context_messages.py -v -s && cd -
cd examples/evaluation/trace_mode && pytest test_trace_mode.py -v -s && cd -
cd examples/evaluation/pass_at_k && pytest test_pass_at_k.py -v -s && cd -