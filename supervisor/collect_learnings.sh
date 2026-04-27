#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

require_root

output_path="${1:-$UGC_RUNTIME_ROOT/all_learnings.md}"
mkdir -p "$(dirname "$output_path")"

{
  echo "# Aggregated Learnings"
  echo
  echo "Generated at $(date -u +%Y-%m-%dT%H:%M:%SZ)."
  echo

  if [[ -d "$CLIENTS_ROOT" ]]; then
    find "$CLIENTS_ROOT" -type f \( -name 'learning.md' -o -name 'learnings.md' \) | sort | while read -r learning_file; do
      echo "## $learning_file"
      echo
      sed -n '1,200p' "$learning_file"
      echo
    done
  fi
} > "$output_path"

chmod 600 "$output_path"
echo "$output_path"
