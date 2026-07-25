"""A sensitive environment variable disclosure sample."""

import os

api_key = os.getenv("SERVICE_API_KEY")
print(api_key)
