import os

def get_model_config():
    api_key = os.environ.get("TRPC_AGENT_API_KEY", "EMPTY")
    url = os.environ.get("TRPC_AGENT_BASE_URL", "http://127.0.0.1:8000/v1")
    model_name = os.environ.get("TRPC_AGENT_MODEL_NAME", "hy3")
    return api_key, url, model_name
