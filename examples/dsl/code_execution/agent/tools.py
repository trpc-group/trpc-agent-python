# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Tool helpers for generated graph workflow."""

CODE_CODE1 = "import statistics\nimport json\n\n# Sample data\ndata = [5, 12, 8, 15, 7, 9, 11]\n\n# Calculate statistics\nresults = {\n    'count': len(data),\n    'min': min(data),\n    'max': max(data),\n    'mean': round(statistics.mean(data), 2),\n    'median': statistics.median(data),\n    'stdev': round(statistics.stdev(data), 2)\n}\n\n# Print results\nprint('=== Python Data Analysis ===')\nfor key, value in results.items():\n    print(f'{key}: {value}')\nprint(json.dumps(results, indent=2))"

CODE_CODE2 = 'echo \'=== System Information ===\'\necho "Date: $(date)"\necho "User: $(whoami)"\necho "Working Directory: $(pwd)"\necho "Python Version: $(python3 --version 2>&1)"\necho "Bash Version: $BASH_VERSION"'
