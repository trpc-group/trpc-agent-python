"""Dangerous: leak API key to file."""
api_key = "sk-123456789012345678901234"
with open("/tmp/leak.txt", "w") as f:
    f.write(f"API_KEY={api_key}")