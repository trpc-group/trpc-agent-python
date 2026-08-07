#!/bin/bash

sudo rm  -rf  coverage.xml
sudo rm  -rf  *.log
sudo rm  -rf  htmlcov
sudo rm  -rf  .coverage
sudo rm  -rf  .__*
sudo rm  -rf  trpc-agent-py.egg-info
sudo rm  -rf dist/
sudo rm  -rf build/


sudo rm  -rf  test_tracemalloc*
sudo rm  -rf  test-ngtest-ut-trpc-agent-py*
sudo rm  -rf  cov.tmp
sudo rm  -rf  examples/*.lock
sudo rm  -rf  examples/*.log

sudo rm  -rf  examples/.__py_trpc_frame.lock
sudo rm  -rf  examples/.__trpc.lock


find . -type d -name __pycache__ -prune -exec sudo rm -rf {} +

find ./ -type f -name "*.log" -exec sudo rm {} \;


python -m pip freeze > tmp_requirements.txt
if [ -s tmp_requirements.txt ]; then
    python -m pip uninstall -r tmp_requirements.txt -y
fi
sudo rm -f tmp_requirements.txt
