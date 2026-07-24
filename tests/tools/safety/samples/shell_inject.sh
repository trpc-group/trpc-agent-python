#!/bin/bash
# Dangerous: shell injection — delete and read passwd
rm -rf /var/log; cat /etc/passwd