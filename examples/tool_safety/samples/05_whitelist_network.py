# Sample 05: Whitelisted network requests.
# Should be ALLOWED (hosts are in policy.whitelisted_domains).
import requests

requests.get("https://api.github.com/repos/trpc-group/trpc-agent-python")
requests.get("http://localhost:8080/healthcheck")
requests.get("https://pypi.org/simple/")
