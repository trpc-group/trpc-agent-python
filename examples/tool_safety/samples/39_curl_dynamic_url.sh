#!/usr/bin/env bash
# Sample 39: dynamic network target (deny under fail-closed policy).
curl "$EXFIL_URL"
wget -qO- $TARGET
