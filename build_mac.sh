# 先注释
set -e

pip install --upgrade pip

sh clean.sh

pip install -r requirements-pypi.txt
pip install -r requirements.txt
pip install -r requirements-test.txt
pip install -r requirements-trpc.txt

pip install --no-build-isolation git+http://git.woa.com/trpc-python/trpc-python.git@master#egg=trpc --index-url https://mirrors.cloud.tencent.com/pypi/simple/ --extra-index-url https://mirrors.tencent.com/repository/pypi/tencent_pypi/simple/

pip install -e '.[dev]' --index-url https://mirrors.cloud.tencent.com/pypi/simple/ --extra-index-url https://mirrors.tencent.com/repository/pypi/tencent_pypi/simple/

# 检查依赖解析
pip install --dry-run .
