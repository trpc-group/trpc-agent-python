#!/usr/bin/env bash
# Sample 22: xargs rm pipeline.
find . -name "*.tmp" | xargs rm -rf
