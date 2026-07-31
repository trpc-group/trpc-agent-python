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

# Check the generated JSON report for any high/critical findings
if [ -f "review_report.json" ]; then
    CRITICAL_COUNT=$(grep -o '"severity": "critical"' review_report.json | wc -l)
    HIGH_COUNT=$(grep -o '"severity": "high"' review_report.json | wc -l)
    
    if [ "$CRITICAL_COUNT" -gt 0 ] || [ "$HIGH_COUNT" -gt 0 ]; then
        echo "❌ [BLOCK] Code Review Agent found $CRITICAL_COUNT critical and $HIGH_COUNT high issues."
        echo "Please review the suggestions in review_report.md before committing."
        rm -f "$TEMP_DIFF"
        exit 1
    fi
fi

echo "✅ Code review completed successfully. Committing..."
rm -f "$TEMP_DIFF"
exit 0
