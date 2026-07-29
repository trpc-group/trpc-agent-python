# A command-looking substitution in a comment is not executed: $(date)
echo 'literal $(date)'
for item in hello world; do echo "$item"; done
