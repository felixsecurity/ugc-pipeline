#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

require_root

mode="report"
if [[ "${1:-}" == "--apply" ]]; then
  mode="apply"
  shift
elif [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  cat <<'EOF'
usage: sleep_on_learnings.sh [--apply]

Collect all client learnings, then run root-level Codex to reason about whether
the shared brain should change.

Default mode:
  - refreshes all_learnings.md
  - writes sleep_report.md
  - does not modify brain/

--apply mode:
  - refreshes all_learnings.md
  - allows Codex to modify brain/ if it decides a change is necessary
  - writes sleep_report.md
EOF
  exit 0
elif [[ "$#" -gt 0 ]]; then
  echo "unknown argument: $1" >&2
  exit 2
fi

if ! command -v codex >/dev/null 2>&1; then
  echo "codex command not found in PATH" >&2
  exit 1
fi

"$SCRIPT_DIR/collect_learnings.sh" "$REPO_ROOT/all_learnings.md" >/dev/null

report_path="$REPO_ROOT/sleep_report.md"
prompt_path="$(mktemp)"
trap 'rm -f "$prompt_path"' EXIT

if [[ "$mode" == "apply" ]]; then
  sandbox="workspace-write"
  apply_instruction="If, and only if, the learnings show a concrete improvement that should be incorporated, edit files under brain/ directly. Do not edit supervisor/ or client data. Keep changes small and explain them in the report."
else
  sandbox="read-only"
  apply_instruction="Do not modify files. Produce a report only."
fi

cat > "$prompt_path" <<EOF
You are running the root-level sleep process for the ugc-pipeline repository.

Goal:
- Read all_learnings.md.
- Inspect brain/README.md, brain/nano_banana.py, and brain/evaluate_image.py.
- Decide whether any changes to brain/ are necessary.
- $apply_instruction

Write the final answer as a concise markdown sleep report with these sections:

# Sleep Report

## Inputs Reviewed
List the files and learnings considered.

## Decision
State either "No brain change needed" or "Brain change needed".

## Reasoning
Explain the decision based on the learnings.

## Proposed Or Applied Changes
If report-only mode, describe proposed changes or say none.
If apply mode, describe exactly what changed.

## Follow-Up
List any operator follow-up.

Constraints:
- Do not include secrets.
- Do not copy Codex auth material.
- Do not read or write /srv/ugc-clients directly; rely on all_learnings.md.
- Do not invent client feedback that is not in all_learnings.md.
EOF

codex exec \
  -c approval_policy=\"never\" \
  --cd "$REPO_ROOT" \
  --sandbox "$sandbox" \
  --output-last-message "$report_path" \
  - < "$prompt_path"

chmod 644 "$report_path"
echo "$report_path"
