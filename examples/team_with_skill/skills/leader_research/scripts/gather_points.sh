#!/usr/bin/env bash
set -euo pipefail

topic="${1:-general topic}"
output_file="${2:-out/leader_notes.txt}"
current_year="$(date +%Y)"

mkdir -p "$(dirname "$output_file")"

cat > "$output_file" <<EOF
Topic: ${topic}
Year: ${current_year}

- Key trend 1: Adoption keeps increasing in major markets.
- Key trend 2: Cost and efficiency improvements continue.
- Key trend 3: Integration and governance are core execution challenges.
EOF

echo "Notes generated at ${output_file}"

