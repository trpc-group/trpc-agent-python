# Sample 10 — Secret leak
# Expected decision: deny
# Description: Hardcodes an API key in the source code, which could be
#   leaked through logs or error messages — must be blocked.

api_key = "sk-1234567890abcdef1234567890abcdef"
print(f"Using key: {api_key}")
