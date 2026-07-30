# Error Handling Rules

## ERR-001: Swallowed Exceptions
- **Severity**: high
- **Pattern**: `except:` or `except Exception:` with only `pass` or `return`
- **Detection**: `except(\s+Exception)?\s*:` followed by only whitespace/pass/return
- **Recommendation**: Log the exception or re-raise with context

## ERR-002: Bare Except Clauses
- **Severity**: medium
- **Pattern**: `except:` without specifying exception type
- **Detection**: `^(\s*)except\s*:`
- **Recommendation**: Catch specific exception types (`except ValueError as e:`)

## ERR-003: Missing Error Propagation
- **Severity**: medium
- **Pattern**: Function calls without checking return values or exceptions
- **Detection**: Functions returning error codes/tuples without checking
- **Recommendation**: Check return values or use raise/exception handling

## ERR-004: Too Broad Exception Handling
- **Severity**: low
- **Pattern**: `except Exception` catching and re-raising different types
- **Detection**: `except Exception as e:` without specific handling
- **Recommendation**: Handle only expected exception types
