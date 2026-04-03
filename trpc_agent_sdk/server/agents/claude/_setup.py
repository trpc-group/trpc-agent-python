# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
#
"""Claude setup for TRPC Agent framework."""

import base64
import multiprocessing
import os
import time
from typing import Awaitable
from typing import Callable
from typing import Dict
from typing import Optional
from typing import Union

import cloudpickle as pickle
import requests
import uvicorn

from trpc_agent_sdk.log import logger
from trpc_agent_sdk.models import LLMModel

from ._proxy import AnthropicProxyApp


class _ServerState:
    """Global server state."""

    def __init__(self):
        self.process: Optional[multiprocessing.Process] = None
        self.host: str = ""
        self.port: int = 0
        self.is_running: bool = False

    def reset(self) -> None:
        """Reset all state variables to their default values."""
        self.process = None
        self.host = ""
        self.port = 0
        self.is_running = False


# Global server state instance
_state = _ServerState()


def _run_server_subprocess(host: str, port: int, serialized_models: Optional[bytes] = None):
    """Run the uvicorn server in a proxy process.

    This function is the target for the proxy process.

    Args:
        host: Host to bind to
        port: Port to bind to
        serialized_models: Pickled dictionary of default models (can be LLMModel instances or callable factories)
    """
    claude_models = None
    if serialized_models:
        claude_models = pickle.loads(serialized_models)

    app = AnthropicProxyApp(claude_models=claude_models)
    # set log_config=None to disable log
    config = uvicorn.Config(app=app.app, host=host, port=port, log_config=None)
    server = uvicorn.Server(config)
    server.run()


def _wait_for_server_ready(host: str, port: int, timeout: float = 10.0) -> bool:
    """Wait for the server to be ready by polling the health endpoint.

    Args:
        host: Server host
        port: Server port
        timeout: Maximum time to wait in seconds

    Returns:
        True if server is ready, False if timeout
    """
    start_time = time.time()
    url = f"http://{host}:{port}/"

    while time.time() - start_time < timeout:
        try:
            response = requests.get(url, timeout=1.0)
            if response.status_code == 200:
                logger.debug("Server is ready at %s", url)
                return True
        except requests.exceptions.RequestException:
            # Server not ready yet
            pass

        time.sleep(0.1)

    return False


def _get_server_url() -> str:
    """Get the server URL.

    Returns:
        Server URL (e.g., "http://0.0.0.0:8082")

    Raises:
        RuntimeError: If server is not initialized
    """
    if not _state.is_running:
        raise RuntimeError("Server not initialized. Call setup_claude_env() first.")
    return f"http://{_state.host}:{_state.port}"


def setup_claude_env(
    proxy_host: str = "0.0.0.0",
    proxy_port: int = 8082,
    timeout: float = 10.0,
    claude_models: Optional[Dict[str, Union[LLMModel, Callable[[], Awaitable[LLMModel]]]]] = None,
) -> None:
    """Initialize and start the proxy server as a proxy process.

    This creates and starts the uvicorn server in a separate process, allowing it
    to handle Anthropic API requests independently. The function waits for the
    server to be ready before returning.

    Process lifecycle management:
    - Creates a new proxy process when setup_claude_env() is called
    - Waits for the server to be ready by polling the health endpoint
    - Properly terminates the proxy process when destroy() is called

    Design:
    - The AnthropicProxyApp class manages the FastAPI application and business logic
    - This module manages the server lifecycle (starting, stopping, cleanup)
    - Uvicorn server runs in a separate proxy process

    Args:
        proxy_host: Host address to bind the server to (default: "0.0.0.0")
        proxy_port: Port number to bind the server to (default: 8082)
        timeout: Maximum time to wait for server startup in seconds (default: 10.0)
        claude_models: Dictionary mapping model names to either:
                       - LLMModel instances (used directly)
                       - Callable factories returning Awaitable[LLMModel] (invoked when model is requested)
                       Special key "all" will be expanded to "sonnet", "opus", "haiku"
                       to support claude-code default model names.
                       Note: Callable factories require cloudpickle for serialization.

    Raises:
        RuntimeError: If server is already initialized or fails to start

    Example:
        >>> from trpc_agent_sdk.server.agents.claude import setup_claude_env, destroy_claude_env, add_model
        >>> from trpc_agent_sdk.models.openai_model import OpenAIModel
        >>>
        >>> # Initialize with default models
        >>> model = OpenAIModel(model_name="gpt-4", api_key="...")
        >>> setup_claude_env(
        ...     proxy_host="0.0.0.0",
        ...     proxy_port=8082,
        ...     claude_models={"all": model}  # Maps to sonnet, opus, haiku
        ... )
        >>>
        >>> # Or specify individual models
        >>> setup_claude_env(
        ...     proxy_host="0.0.0.0",
        ...     proxy_port=8082,
        ...     claude_models={
        ...         "sonnet": model_sonnet,
        ...         "opus": model_opus,
        ...         "haiku": model_haiku,
        ...     }
        ... )
        >>>
        >>> # Or use callable factories for lazy model creation
        >>> async def create_model():
        ...     return OpenAIModel(model_name="gpt-4", api_key="...")
        >>> setup_claude_env(
        ...     proxy_host="0.0.0.0",
        ...     proxy_port=8082,
        ...     claude_models={"all": create_model}  # Factory will be called when model is needed
        ... )
        >>>
        >>> # ... use the server ...
        >>> destroy_claude_env()  # Properly terminate proxy process
    """
    if _state.process is not None:
        raise RuntimeError("Server already initialized. Call destroy_claude_env() first.")

    logger.debug("Initializing proxy server on %s:%s", proxy_host, proxy_port)

    # Process claude_models
    processed_models = None
    if claude_models:
        processed_models = {}

        # Check if "all" key exists
        if "all" in claude_models:
            all_model = claude_models["all"]
            logger.debug("Expanding 'all' model to sonnet, opus, haiku: %s", all_model)
            processed_models["sonnet"] = all_model
            processed_models["opus"] = all_model
            processed_models["haiku"] = all_model

            # Add any other keys (but skip "all")
            for key, model in claude_models.items():
                if key != "all":
                    processed_models[key] = model
        else:
            # No "all" key, just copy the dict
            processed_models = claude_models.copy()

    # Store host and port
    _state.host = proxy_host
    _state.port = proxy_port

    # Serialize models if present
    serialized_models = None
    if processed_models:
        serialized_models = pickle.dumps(processed_models)

    # Create and start the proxy process
    _state.process = multiprocessing.Process(
        target=_run_server_subprocess,
        args=(proxy_host, proxy_port, serialized_models),
        name="AnthropicProxyServer",
        daemon=False,
    )

    _state.process.start()
    logger.info("Proxy server proxy process started (PID: %s)", _state.process.pid)

    # Wait for server to be ready
    logger.debug("Waiting for server to be ready...")
    if not _wait_for_server_ready(proxy_host, proxy_port, timeout=timeout):
        # Server failed to start, clean up
        logger.error("Server failed to start within timeout")
        _state.process.terminate()
        _state.process.join(timeout=5.0)
        if _state.process.is_alive():
            logger.warning("Force killing server process...")
            _state.process.kill()
            _state.process.join()
        _state.reset()
        raise RuntimeError(f"Server failed to start within {timeout} seconds")

    _state.is_running = True
    logger.info("Proxy server is ready at http://%s:%s", proxy_host, proxy_port)

    # Set environment variables for Anthropic client
    server_url = f"http://{proxy_host}:{proxy_port}"
    os.environ["ANTHROPIC_BASE_URL"] = server_url
    os.environ["ANTHROPIC_AUTH_TOKEN"] = "xxxx"


def destroy_claude_env() -> None:
    """Stop and destroy the proxy server proxy process.

    This properly shuts down the running server proxy process and cleans up all resources.
    Any added models in the proxy process will be cleared.

    The function ensures proper proxy process lifecycle management:
    - Terminates the proxy process gracefully
    - Waits for the proxy process to exit (with timeout)
    - Forces kill if proxy process doesn't exit within timeout
    - Cleans up all references

    It's safe to call this function multiple times - if the server is not running,
    it will log a warning and return without error.

    Example:
        >>> from trpc_agent_sdk.server.agents.claude import setup_claude_env, destroy_claude_env
        >>> setup_claude_env()
        >>> # ... use the server ...
        >>> destroy_claude_env()  # Properly terminate proxy process and clean up
    """
    if _state.process is None:
        logger.warning("Server not initialized, nothing to destroy.")
        return

    _state.is_running = False

    if _state.process.is_alive():
        logger.info("Terminating proxy process (PID: %s)...", _state.process.pid)
        _state.process.terminate()

        # Wait for process to complete (with timeout)
        _state.process.join(timeout=5.0)

        if _state.process.is_alive():
            logger.warning("Subprocess did not stop within timeout, force killing...")
            _state.process.kill()
            _state.process.join()
            logger.info("Subprocess killed successfully.")
        else:
            logger.info("Subprocess terminated successfully.")
    else:
        logger.info("Proxy process already stopped.")

    _state.reset()


def _add_model(model: LLMModel, generate_content_config: Optional[any] = None) -> str:
    """Add a model to the proxy server via HTTP.

    This function serializes the model and optional generate_content_config using pickle
    and sends it to the /add_model endpoint in the proxy process.

    Args:
        model: The LLMModel instance to add
        generate_content_config: Optional GenerateContentConfig to associate with the model

    Returns:
        Model key that can be used to reference this model

    Raises:
        RuntimeError: If server is not initialized
        requests.exceptions.RequestException: If the HTTP request fails

    Example:
        >>> from trpc_agent_sdk.server.agents.claude import setup_claude_env, _add_model
        >>> from trpc_agent_sdk.models.openai_model import OpenAIModel
        >>> from trpc_agent_sdk.types import GenerateContentConfig
        >>>
        >>> setup_claude_env()
        >>> model = OpenAIModel(model_name="gpt-4", api_key="...")
        >>> config = GenerateContentConfig(temperature=0.7, max_output_tokens=1000)
        >>> model_key = _add_model(model, config)
        >>> print(f"Model added with key: {model_key}")
    """
    if not _state.is_running:
        raise RuntimeError("Server not initialized. Call setup_claude_env() first.")

    # Pickle the model
    pickled_model = pickle.dumps(model)
    # Encode as base64 for JSON transport
    encoded_model = base64.b64encode(pickled_model).decode("ascii")

    # Prepare request data
    request_data = {"model_data": encoded_model}

    # If generate_content_config is provided, pickle and encode it too
    if generate_content_config:
        pickled_config = pickle.dumps(generate_content_config)
        encoded_config = base64.b64encode(pickled_config).decode("ascii")
        request_data["config_data"] = encoded_config

    # Send to server
    url = f"{_get_server_url()}/add_model"
    response = requests.post(
        url,
        json=request_data,
        timeout=10.0,
    )

    response.raise_for_status()
    result = response.json()

    model_key = result.get("model")
    if not model_key:
        raise RuntimeError("Server did not return a model key")

    return model_key


def _delete_model(model_key: str) -> bool:
    """Delete a model from the proxy server via HTTP.

    This function sends a request to the /delete_model endpoint in the proxy process
    to remove a previously added model.

    Args:
        model_key: The model key returned from _add_model

    Returns:
        True if the model was successfully deleted, False otherwise

    Raises:
        RuntimeError: If server is not initialized
        requests.exceptions.RequestException: If the HTTP request fails

    Example:
        >>> from trpc_agent_sdk.server.agents.claude import setup_claude_env, _add_model, _delete_model
        >>> from trpc_agent_sdk.models.openai_model import OpenAIModel
        >>>
        >>> setup_claude_env()
        >>> model = OpenAIModel(model_name="gpt-4", api_key="...")
        >>> model_key = _add_model(model)
        >>> print(f"Model added with key: {model_key}")
        >>> # ... use the model ...
        >>> success = _delete_model(model_key)
        >>> print(f"Model deleted: {success}")
    """
    if not _state.is_running:
        raise RuntimeError("Server not initialized. Call setup_claude_env() first.")

    # Prepare request data
    request_data = {"model_key": model_key}

    # Send to server
    url = f"{_get_server_url()}/delete_model"
    response = requests.post(
        url,
        json=request_data,
        timeout=10.0,
    )

    response.raise_for_status()
    result = response.json()

    success = result.get("success", False)
    message = result.get("message", "")

    if success:
        logger.debug("Model '%s' deleted successfully: %s", model_key, message)
    else:
        logger.warning("Failed to delete model '%s': %s", model_key, message)

    return success
