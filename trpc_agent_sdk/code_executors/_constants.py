# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Constants for code executors."""

# Default file modes and common subdirectories
DEFAULT_SCRIPT_FILE_MODE = 0o644
DEFAULT_EXEC_FILE_MODE = 0o755

# Normalization constants
NORMALIZE_GLOBS_VAR_PREFIX = "$"
NORMALIZE_GLOBS_VAR_LBRACE = "${"
NORMALIZE_GLOBS_VAR_RBRACE = "}"
NORMALIZE_GLOBS_SLASH = "/"
NORMALIZE_GLOBS_BACKSLASH = "\\"
NORMALIZE_GLOBS_WORKSPACE = "WORKSPACE_DIR"
NORMALIZE_GLOBS_SKILLS = "SKILLS_DIR"
NORMALIZE_GLOBS_WORK = "WORK_DIR"
NORMALIZE_GLOBS_OUT = "OUTPUT_DIR"
NORMALIZE_GLOBS_WORKSPACE_DIR = "."
# Well-known environment keys
WORKSPACE_ENV_DIR_KEY = "WORKSPACE_DIR"

# Well-known subdirectories in a workspace
DIR_SKILLS = "skills"
DIR_WORK = "work"
DIR_RUNS = "runs"
DIR_OUT = "out"
META_FILE_NAME = "metadata.json"
TMP_FILE_NAME = ".metadata.tmp"

# Additional environment variable keys injected at runtime
ENV_SKILLS_DIR = "SKILLS_DIR"
ENV_WORK_DIR = "WORK_DIR"
ENV_OUTPUT_DIR = "OUTPUT_DIR"
ENV_RUN_DIR = "RUN_DIR"
ENV_SKILL_NAME = "SKILL_NAME"

DEFAULT_TIMEOUT_SEC = 10
DEFAULT_FILE_MODE = 0o644
MAX_READ_SIZE_BYTES = 4 * 1024 * 1024  # 4 MiB per output file
DEFAULT_MAX_FILES = 100
DEFAULT_MAX_TOTAL_BYTES = 64 * 1024 * 1024  # 64 MiB total

# Constants
DEFAULT_CREATE_TIMEOUT_SEC = 5
DEFAULT_RM_TIMEOUT_SEC = 10
DEFAULT_STAGE_TIMEOUT_SEC = 10
DEFAULT_RUN_CONTAINER_BASE = "/tmp/run"
DEFAULT_SKILLS_CONTAINER = "/opt/trpc-agent/skills"
DEFAULT_INPUTS_CONTAINER = "/opt/trpc-agent/inputs"

# Directory constants
INLINE_SOURCE_DIR = "src"
