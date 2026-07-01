#!/bin/bash
# Sample 08: Dependency installation.
# Should be DENIED (dependency_install, HIGH).
pip install malicious-package
npm install backdoor-lib
apt install trojan-tool
python -m pip install data-exfil
yarn add sneaky-dep
