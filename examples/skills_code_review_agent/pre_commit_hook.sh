#!/usr/bin/env bash
#
# Git pre-commit hook to run automatic code review on staged changes.
# Copy or symlink this to .git/hooks/pre-commit
#

echo "🔍 Running Automated Code Review Agent on staged changes..."

# Create a temporary diff file
TEMP_DIFF=$(mktemp)
git diff --cached > "$TEMP_DIFF"

# If diff is empty, exit early
if [ ! -s "$TEMP_DIFF" ]; then
    echo "✅ No staged changes found. Skipping code review."
    rm -f "$TEMP_DIFF"
    exit 0
fi

# Run the agent in fake-model (dry-run) mode
python3 -m examples.skills_code_review_agent.agent --diff-file "$TEMP_DIFF"
AGENT_EXIT_CODE=$?
if [ $AGENT_EXIT_CODE -ne 0 ]; then
    echo "❌ [BLOCK] Code Review Agent execution failed (Exit code: $AGENT_EXIT_CODE)."
    echo "Please resolve runtime/syntax errors before committing."
    rm -f "$TEMP_DIFF"
    exit 1
fi

# Check the generated JSON report for any high/critical findings
if [ -f "review_report.json" ]; then
    # Use python to parse JSON and check for high/critical severities
    python3 -c "
import json, sys
try:
    data = json.load(open('review_report.json'))
    findings = data.get('findings', [])
    high_critical = [f for f in findings if f.get('severity', '').lower() in ('critical', 'high')]
    if len(high_critical) > 0:
        print(f'❌ [BLOCK] Code Review Agent found {len(high_critical)} critical/high issues.')
        sys.exit(1)
except Exception as e:
    print('Failed to parse code review report:', e)
    sys.exit(1)
"
    if [ $? -ne 0 ]; then
        echo "Please review the suggestions in review_report.md before committing."
        rm -f "$TEMP_DIFF"
        exit 1
    fi
    # Clean up output files on success to prevent pollution
    rm -f review_report.json review_report.md
fi

echo "✅ Code review completed successfully. Committing..."
rm -f "$TEMP_DIFF"
exit 0
