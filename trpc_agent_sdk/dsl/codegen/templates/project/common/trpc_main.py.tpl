# -*- coding: utf-8 -*-
"""Generated tRPC service entrypoint."""

import argparse
import os
import sys
from pathlib import Path
from typing import List

from dotenv import load_dotenv

import trpc
from trpc.log import logger
from trpc.plugin import PluginType
from trpc.plugin import register_plugin

# pylint: disable=unused-import
import trpc_agent
# import trpc_naming_polaris as _
# import trpc_metrics_runtime as _
# pylint: enable=unused-import

sys.path.append(str(Path(__file__) / "agent"))  # noqa
sys.path.append(str(Path(__file__)))

{% if is_http_mode %}
import http_service as _
{% elif is_a2a_mode %}
from a2a_service import register_a2a_service
{% else %}
from agui_service import register_agui_agent
{% endif %}


@register_plugin(PluginType.USER_DEFINED, "trpc_agent_log")
def set_trpc_agent_logger():
    """Set tRPC-Agent logger."""
    trpc_agent.log.set_logger(logger)


DEFAULT_CONF_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "trpc_python.yaml"))


def parse_args(argv: List[str]) -> str:
    """Parse command-line args."""
    parser = argparse.ArgumentParser(description="TRPC Python Framework Server")
    parser.add_argument(
        "--conf",
        "-c",
        default=DEFAULT_CONF_PATH,
        help="start server with conf path, for example: python trpc_main.py --conf=trpc_python.yaml",
    )
    try:
        args = parser.parse_args(argv[1:])
        if os.path.exists(args.conf):
            print(f"conf_path: {args.conf}")
            return args.conf
        print(f"conf_path: {args.conf} not exists")
        if os.path.exists(DEFAULT_CONF_PATH):
            print(f"use default conf path: {DEFAULT_CONF_PATH}")
            return DEFAULT_CONF_PATH
        print(f"default conf path: {DEFAULT_CONF_PATH} not exists, please check the path")
        sys.exit(1)
    except SystemExit:
        sys.exit(1)


def serve(conf_path: str):
    """Start service."""
{% if is_a2a_mode %}
    svr = trpc.new(conf_path)
    register_a2a_service()
    svr.serve()
{% elif is_agui_mode %}
    register_agui_agent()
    svr = trpc.new(conf_path)
    svr.serve()
{% else %}
    svr = trpc.new(conf_path)
    svr.serve()
{% endif %}


def main():
    """Main entry."""
    load_dotenv()
    conf_path = parse_args(sys.argv)
    logger.info("load config %s", conf_path)
    serve(conf_path)


if __name__ == "__main__":
    sys.exit(main())
