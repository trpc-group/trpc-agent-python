# Testing Rules

## TST-001: New Function Without Test
- **Severity**: medium
- **Pattern**: New `def test_*` functions not found in the diff but new production functions added
- **Detection**: Added functions in `added_lines` without corresponding test file changes
- **Recommendation**: Add unit tests for new functions

## TST-002: New Class Without Test
- **Severity**: medium
- **Pattern**: New `class` definitions without test coverage
- **Detection**: Added class definitions in `added_lines` without test file changes
- **Recommendation**: Add test cases for new classes

## TST-003: Modified Function Without Test Update
- **Severity**: low
- **Pattern**: Modified function logic without corresponding test assertion changes
- **Detection**: Function body changes without test file modifications
- **Recommendation**: Update tests to cover modified behavior

## TST-004: Error Paths Untested
- **Severity**: low
- **Pattern**: New try/except blocks without tests for exception paths
- **Detection**: Added `try:` blocks without corresponding error-case tests
- **Recommendation**: Add tests for exception handling paths
