#!/bin/bash

pip install --upgrade pip


sh clean.sh

pip3 install -r requirements.txt
pip3 install -r requirements-test.txt

pip install -e .[dev]

# 检查依赖解析
pip install --dry-run .
