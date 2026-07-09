# db_lifecycle test rule

```yaml
- id: DB001
  pattern: '(?:engine|conn|connection|db)\.connect\s*\('
  severity_hint: high
  confidence: 0.7
  type: ast
  description: Database connection should be closed with a context manager or explicit close.
- id: DB002
  pattern: '\.cursor\s*\('
  severity_hint: medium
  confidence: 0.65
  type: ast
  description: Cursor should be closed with a context manager or explicit close.
```
