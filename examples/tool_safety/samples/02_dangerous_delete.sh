#!/bin/bash
# Sample 02: Dangerous recursive delete.
# Should be DENIED (dangerous_files, CRITICAL).
rm -rf /
rm -rf /home/user/*
rm -rf ~/.ssh
