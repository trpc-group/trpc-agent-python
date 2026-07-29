# missing_tests rules

Structural check (`checks/check_missing_tests.py`, `CATEGORY="missing_tests"`): it classifies
paths and looks for changed `def`/`class` header lines, it does not analyse behaviour. Of the
six categories this one is the most false-positive prone, so its design bias is **when in
doubt, stay silent**: a missed nag costs little, a wrong nag erodes trust in every other rule.

## Test file detection

A path counts as a test file when either

* any parent directory segment is `test` or `tests` (e.g. `tests/util/data_helper.py`), or
* the basename matches `test_*.py`, `*_test.py`, or `conftest.py`.

Matching is case-insensitive. `testing.py`, `latest.py`, `contest.py` do **not** match.

## Rules

### TEST001 — source change ships without test changes

Fires only when **all** of the following hold:

1. the file is Python, is not a test file, and is not deleted/binary;
2. its changed lines (candidate lines) contain at least one `def`/`class`/`async def`
   header line — found via AST (`FunctionDef`/`AsyncFunctionDef`/`ClassDef` whose `lineno`
   is a candidate line); when the AST is unavailable (diff-only gap reconstruction or a
   syntax error) a per-changed-line regex `^\s*(async\s+def|def|class)\s` is the fallback;
3. the changeset contains **no** test file at all (added, modified, renamed or deleted —
   any test activity, on the old or new path, silences the rule).

Severity grading (the honest-uncertainty ladder):

| Situation | severity | precision | confidence | routed to |
|---|---|---|---|---|
| repo mode, no repo test name contains the source stem | medium | high * | medium * | findings |
| repo mode, matching repo test exists but untouched | — not reported — | | | |
| diff-only mode (repository tests invisible) | info | low | low | warnings / needs_human_review |

\* both drop to `low` on the rare regex fallback (repo file with a syntax error).

"Matching repo test" means any path in `context["repo_context"]["test_files"]` whose
basename contains the source stem: `calculator.py` matches `test_calculator.py`,
`calculator_test.py`, `test_calculator_ops.py`, … The substring match deliberately errs
toward suppression.

The finding is anchored on the first changed definition line (always a candidate line) and
carries a pytest skeleton naming the actually changed definitions as `fix_snippet.after`.

### TEST002 — test file deleted

A test file with `change_type=deleted` is always reported: severity `medium`, precision
`high`, confidence `high`, anchored at line 1 (deleted files have no candidate lines; the
file header itself is the evidence — this is the documented exception to the
"report only changed lines" principle).

Modified test files are **not** analysed for a net decrease of `def test_` counts:
hunk-local counting over partial diffs is unreliable (moved, renamed or parametrised tests
would look like deletions), so only whole-file deletions raise TEST002.

## Deliberately not reported (false-positive guards)

* **Doc/config-only changes**: `.md`, `.txt`, `.rst`, `.yaml`, `.json`, `.toml`, `.cfg`,
  `.ini` and every other non-Python language — TEST001 requires `language == "python"`.
* **Renames without content changes**: no candidate lines, therefore no changed definitions.
* **Body-only edits**: changes that touch no `def`/`class` header line. A bugfix inside an
  existing function arguably deserves a test too, but flagging every edited line would drown
  reviewers; only definition-level changes count.
* **Packaging glue**: `__init__.py`, `__main__.py`, `setup.py`. Import/re-export-only
  `__init__.py` edits carry no definition lines anyway; def-carrying ones are still skipped
  because stem matching against `__init__` is meaningless.
* **Demo/doc trees**: files under `examples/`, `samples/`, `demo/`, `docs/`, `benchmarks/`,
  `migrations/` and similar directories are not unit-test targets.
* **Changesets with any test activity**: an added, modified, renamed or deleted test file
  proves the author looked at the suite; TEST001 stays silent (a deleted test is already
  TEST002 — double-reporting it as TEST001 would be noise).
* **Repo mode with an existing matching test file**: the module already has tests; whether
  the change needs a new case is a human call, not a static one.
* **Deleted non-Python assets under `tests/`** (fixtures, data files): not TEST002.
* **Deleted source files**: removing code does not demand new tests by itself.

## Diff-only robustness

For `content_complete=False` files the gap-reconstructed post-image may not parse;
`parse_ast()` then returns `None` and the check falls back to the per-changed-line regex
(precision stays `low`) or produces nothing — it never raises. In diff-only mode every
TEST001 finding is `info`/`low`/`low` because the repository's test suite is invisible: the
decision table routes that tier into the warnings bucket instead of blocking findings.
