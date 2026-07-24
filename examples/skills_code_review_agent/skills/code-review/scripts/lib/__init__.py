# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Self-contained, stdlib-only rule library for the code-review skill.

Every module in this package must stay importable inside a bare sandbox
(no third-party site-packages). The host-side ``codereview`` package loads
this exact package through ``importlib`` so the sandbox and the host always
share a single implementation of the diff parser, the secret pattern table
and the review rules (single source of truth, no drift).
"""
