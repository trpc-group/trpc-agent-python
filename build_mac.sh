# 先注释
set -e

pip install --upgrade pip

sh clean.sh

pip install -r requirements.txt
pip install -r requirements-test.txt

pip install -e '.[dev]'

# 检查依赖解析
pip install --dry-run .
