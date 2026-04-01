#!/bin/bash

python3 -m pip install --upgrade pip

pip3 install --upgrade --force-reinstall yapf


# 查找并格式化 Python 文件
yapf -i -r .

# 查找并格式化 examples 目录下的 Python 文件
# find {dir} -name '*.py' -print0 | xargs -0 yapf -i
