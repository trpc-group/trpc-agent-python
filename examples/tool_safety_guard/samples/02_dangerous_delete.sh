#!/usr/bin/env bash
# Sample 02 — DANGEROUS FILE OP: recursive force delete of the filesystem root.
# Expected verdict: deny (FS001, critical).
rm -rf /
