# Sample 10 - SECRET LEAK: printing an API key and writing a token to a file.
# Expected decision: deny  (SECRET_LEAK_OUTPUT, CRITICAL)
import os

api_key = os.environ.get("API_KEY", "")
print(api_key)

token = os.environ.get("AUTH_TOKEN", "")
with open("/tmp/leak.txt", "w") as fh:
    fh.write(token)
