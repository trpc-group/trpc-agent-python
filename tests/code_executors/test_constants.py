# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

from trpc_agent_sdk.code_executors._constants import (
    DEFAULT_CREATE_TIMEOUT_SEC,
    DEFAULT_EXEC_FILE_MODE,
    DEFAULT_FILE_MODE,
    DEFAULT_INPUTS_CONTAINER,
    DEFAULT_MAX_FILES,
    DEFAULT_MAX_TOTAL_BYTES,
    DEFAULT_RM_TIMEOUT_SEC,
    DEFAULT_RUN_CONTAINER_BASE,
    DEFAULT_SCRIPT_FILE_MODE,
    DEFAULT_SKILLS_CONTAINER,
    DEFAULT_STAGE_TIMEOUT_SEC,
    DEFAULT_TIMEOUT_SEC,
    DIR_OUT,
    DIR_RUNS,
    DIR_SKILLS,
    DIR_WORK,
    ENV_OUTPUT_DIR,
    ENV_RUN_DIR,
    ENV_SKILL_NAME,
    ENV_SKILLS_DIR,
    ENV_WORK_DIR,
    INLINE_SOURCE_DIR,
    MAX_READ_SIZE_BYTES,
    META_FILE_NAME,
    NORMALIZE_GLOBS_BACKSLASH,
    NORMALIZE_GLOBS_OUT,
    NORMALIZE_GLOBS_SKILLS,
    NORMALIZE_GLOBS_SLASH,
    NORMALIZE_GLOBS_VAR_LBRACE,
    NORMALIZE_GLOBS_VAR_PREFIX,
    NORMALIZE_GLOBS_VAR_RBRACE,
    NORMALIZE_GLOBS_WORK,
    NORMALIZE_GLOBS_WORKSPACE,
    NORMALIZE_GLOBS_WORKSPACE_DIR,
    TMP_FILE_NAME,
    WORKSPACE_ENV_DIR_KEY,
)


class TestFileModeConstants:
    """Test file mode constants."""

    def test_default_script_file_mode(self):
        assert DEFAULT_SCRIPT_FILE_MODE == 0o644

    def test_default_exec_file_mode(self):
        assert DEFAULT_EXEC_FILE_MODE == 0o755

    def test_default_file_mode(self):
        assert DEFAULT_FILE_MODE == 0o644


class TestNormalizeGlobsConstants:
    """Test normalization constants for glob patterns."""

    def test_var_prefix(self):
        assert NORMALIZE_GLOBS_VAR_PREFIX == "$"

    def test_var_lbrace(self):
        assert NORMALIZE_GLOBS_VAR_LBRACE == "${"

    def test_var_rbrace(self):
        assert NORMALIZE_GLOBS_VAR_RBRACE == "}"

    def test_slash(self):
        assert NORMALIZE_GLOBS_SLASH == "/"

    def test_backslash(self):
        assert NORMALIZE_GLOBS_BACKSLASH == "\\"

    def test_workspace(self):
        assert NORMALIZE_GLOBS_WORKSPACE == "WORKSPACE_DIR"

    def test_skills(self):
        assert NORMALIZE_GLOBS_SKILLS == "SKILLS_DIR"

    def test_work(self):
        assert NORMALIZE_GLOBS_WORK == "WORK_DIR"

    def test_out(self):
        assert NORMALIZE_GLOBS_OUT == "OUTPUT_DIR"

    def test_workspace_dir(self):
        assert NORMALIZE_GLOBS_WORKSPACE_DIR == "."


class TestWorkspaceEnvConstants:
    """Test workspace environment variable constants."""

    def test_workspace_env_dir_key(self):
        assert WORKSPACE_ENV_DIR_KEY == "WORKSPACE_DIR"

    def test_env_skills_dir(self):
        assert ENV_SKILLS_DIR == "SKILLS_DIR"

    def test_env_work_dir(self):
        assert ENV_WORK_DIR == "WORK_DIR"

    def test_env_output_dir(self):
        assert ENV_OUTPUT_DIR == "OUTPUT_DIR"

    def test_env_run_dir(self):
        assert ENV_RUN_DIR == "RUN_DIR"

    def test_env_skill_name(self):
        assert ENV_SKILL_NAME == "SKILL_NAME"


class TestDirectoryConstants:
    """Test well-known directory name constants."""

    def test_dir_skills(self):
        assert DIR_SKILLS == "skills"

    def test_dir_work(self):
        assert DIR_WORK == "work"

    def test_dir_runs(self):
        assert DIR_RUNS == "runs"

    def test_dir_out(self):
        assert DIR_OUT == "out"

    def test_inline_source_dir(self):
        assert INLINE_SOURCE_DIR == "src"


class TestFileNameConstants:
    """Test metadata file name constants."""

    def test_meta_file_name(self):
        assert META_FILE_NAME == "metadata.json"

    def test_tmp_file_name(self):
        assert TMP_FILE_NAME == ".metadata.tmp"


class TestResourceLimitConstants:
    """Test resource limit constants."""

    def test_default_timeout_sec(self):
        assert DEFAULT_TIMEOUT_SEC == 10

    def test_max_read_size_bytes(self):
        assert MAX_READ_SIZE_BYTES == 4 * 1024 * 1024

    def test_default_max_files(self):
        assert DEFAULT_MAX_FILES == 100

    def test_default_max_total_bytes(self):
        assert DEFAULT_MAX_TOTAL_BYTES == 64 * 1024 * 1024


class TestContainerConstants:
    """Test container-related constants."""

    def test_default_create_timeout_sec(self):
        assert DEFAULT_CREATE_TIMEOUT_SEC == 5

    def test_default_rm_timeout_sec(self):
        assert DEFAULT_RM_TIMEOUT_SEC == 10

    def test_default_stage_timeout_sec(self):
        assert DEFAULT_STAGE_TIMEOUT_SEC == 10

    def test_default_run_container_base(self):
        assert DEFAULT_RUN_CONTAINER_BASE == "/tmp/run"

    def test_default_skills_container(self):
        assert DEFAULT_SKILLS_CONTAINER == "/opt/trpc-agent/skills"

    def test_default_inputs_container(self):
        assert DEFAULT_INPUTS_CONTAINER == "/opt/trpc-agent/inputs"


class TestConstantsTypes:
    """Verify constant types are correct."""

    def test_file_modes_are_int(self):
        assert isinstance(DEFAULT_SCRIPT_FILE_MODE, int)
        assert isinstance(DEFAULT_EXEC_FILE_MODE, int)
        assert isinstance(DEFAULT_FILE_MODE, int)

    def test_string_constants_are_str(self):
        for val in [
            NORMALIZE_GLOBS_VAR_PREFIX,
            NORMALIZE_GLOBS_VAR_LBRACE,
            NORMALIZE_GLOBS_VAR_RBRACE,
            NORMALIZE_GLOBS_SLASH,
            NORMALIZE_GLOBS_BACKSLASH,
            NORMALIZE_GLOBS_WORKSPACE,
            NORMALIZE_GLOBS_SKILLS,
            NORMALIZE_GLOBS_WORK,
            NORMALIZE_GLOBS_OUT,
            NORMALIZE_GLOBS_WORKSPACE_DIR,
            WORKSPACE_ENV_DIR_KEY,
            DIR_SKILLS,
            DIR_WORK,
            DIR_RUNS,
            DIR_OUT,
            META_FILE_NAME,
            TMP_FILE_NAME,
            ENV_SKILLS_DIR,
            ENV_WORK_DIR,
            ENV_OUTPUT_DIR,
            ENV_RUN_DIR,
            ENV_SKILL_NAME,
            DEFAULT_RUN_CONTAINER_BASE,
            DEFAULT_SKILLS_CONTAINER,
            DEFAULT_INPUTS_CONTAINER,
            INLINE_SOURCE_DIR,
        ]:
            assert isinstance(val, str)

    def test_numeric_constants_are_int(self):
        for val in [
            DEFAULT_TIMEOUT_SEC,
            MAX_READ_SIZE_BYTES,
            DEFAULT_MAX_FILES,
            DEFAULT_MAX_TOTAL_BYTES,
            DEFAULT_CREATE_TIMEOUT_SEC,
            DEFAULT_RM_TIMEOUT_SEC,
            DEFAULT_STAGE_TIMEOUT_SEC,
        ]:
            assert isinstance(val, int)

    def test_numeric_constants_are_positive(self):
        for val in [
            DEFAULT_TIMEOUT_SEC,
            MAX_READ_SIZE_BYTES,
            DEFAULT_MAX_FILES,
            DEFAULT_MAX_TOTAL_BYTES,
            DEFAULT_CREATE_TIMEOUT_SEC,
            DEFAULT_RM_TIMEOUT_SEC,
            DEFAULT_STAGE_TIMEOUT_SEC,
        ]:
            assert val > 0
