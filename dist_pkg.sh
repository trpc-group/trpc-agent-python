#!/bin/sh

INIT_PY="trpc_agent_sdk/version.py"

version=$(grep -oP "__version__ = '\K[^']+" "$INIT_PY")

echo "version is ${version}"

sh +x build.sh

sudo rm -rf dist/

pip3 install urllib3
pip install build
pip3 install twine
# dist source package
#python -m build --sdist
# dist wheel package
python -m build


twine upload dist/* -r tencent


