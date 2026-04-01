#!/bin/sh

sh +x build.sh

pip3 install -r requirements-test.txt

rm -rf coverage.xml
rm -rf test-ngtest-ut-trpc-agent-py.xml
rm -rf .coverage
rm -rf htmlcov*


test_dir=trpc_agent
pytest --cov-report html --cov-report term --cov-report xml --cov=$test_dir tests/  \
       --junitxml=test-ngtest-ut-$test_dir.xml > cov.tmp
