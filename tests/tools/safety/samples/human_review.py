import os
import requests

response = requests.get("https://api.example.com/data")

config_path = os.path.join(os.getcwd(), "config.ini")
with open(config_path) as f:
    config = f.read()

print(f"Loaded config: {len(config)} bytes")
