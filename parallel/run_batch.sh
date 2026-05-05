#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "usage: $0 /srv/batch_x [--once]" >&2
  exit 2
fi

batch_dir="$1"
mode="${2:-}"

args=("--batch-dir" "$batch_dir")
if [[ "$mode" == "--once" ]]; then
  args+=("--once")
fi

set +e
/opt/ugc-pipeline-venv/bin/python /srv/ugc-pipeline/parallel/orchestrate_batch.py "${args[@]}"
rc=$?
set -e

if [[ -f "$batch_dir/events.log" ]]; then
  /opt/ugc-pipeline-venv/bin/python /srv/ugc-pipeline/parallel/analyze_events.py --batch-dir "$batch_dir"
fi

exit "$rc"
