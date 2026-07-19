# Script Contracts

## `scripts/parse_diff.py`

Purpose:

- summarizes diff size and whether high-risk tokens appear

CLI:

```text
python scripts/parse_diff.py --diff-file <path>
```

Output:

- JSON to stdout
- fields:
  - `diff_file`
  - `line_count`
  - `file_count`
  - `has_security_keywords`

## `scripts/run_linters.py`

Purpose:

- runs deterministic lint-style checks over the diff content

CLI:

```text
python scripts/run_linters.py --diff-file <path>
```

Output:

- JSON to stdout
- fields:
  - `diff_file`
  - `warning_count`
  - `warnings`

Failure behavior:

- exits non-zero when the diff contains the `TODO_FAIL_SANDBOX` marker
- intended for sandbox failure testing and pipeline resilience checks

## `scripts/run_tests.py`

Purpose:

- reports whether test files are updated in the diff

CLI:

```text
python scripts/run_tests.py --diff-file <path>
```

Output:

- JSON to stdout
- fields:
  - `diff_file`
  - `changed_test_files`
  - `test_update_present`

## Shared Expectations

- scripts should stay deterministic
- scripts should not require network access
- scripts should be safe to run in dry-run and fake-model modes
- stdout/stderr may be truncated by sandbox policy
