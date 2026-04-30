#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

require_root

if [[ "$#" -lt 2 || "$#" -gt 4 ]]; then
  echo "usage: submit_motion_control_request.sh <client-id> <video-url-or-file> [direction-text-or-file] [character-id]" >&2
  exit 2
fi

client_id="$(sanitize_client_id "$1")"
video_input="$2"
direction_input="${3:-}"
character_id="${4:-astrid}"
character_id="$(sanitize_request_value "$character_id")"
user="$(client_user_for "$client_id")"
home_dir="$(client_home_for "$client_id")"
character_dir="$REPO_ROOT/characters/$character_id"

if [[ ! -d "$character_dir" ]]; then
  echo "character does not exist: $character_dir" >&2
  exit 2
fi

direction=""
if [[ -n "$direction_input" ]]; then
  if [[ -f "$direction_input" ]]; then
    direction="$(<"$direction_input")"
  else
    direction="$direction_input"
  fi
fi

load_fal_key
ensure_client_user_and_home "$client_id"

request_id="$(date -u +%Y%m%dT%H%M%S%NZ)-motion-control"
request_dir="$home_dir/requests/$request_id"
inputs_dir="$request_dir/inputs"
install -d -m 700 -o "$user" -g "$user" "$request_dir"
install -d -m 700 -o "$user" -g "$user" "$inputs_dir"

check_report="$request_dir/request_check.json"
"$REPO_ROOT/supervisor/check_request.py" --prompt "$direction" --json-output "$check_report"

stored_video="$video_input"
if [[ "$video_input" =~ ^https?:// ]]; then
  stored_video="$video_input"
else
  if [[ ! -f "$video_input" ]]; then
    echo "input video does not exist: $video_input" >&2
    exit 2
  fi
  case "${video_input,,}" in
    *.mp4|*.mov|*.webm|*.m4v|*.gif) ;;
    *)
      echo "input video must be mp4, mov, webm, m4v, or gif: $video_input" >&2
      exit 2
      ;;
  esac
  file_name="$(basename "$video_input")"
  destination="$inputs_dir/$file_name"
  install -m 600 -o "$user" -g "$user" "$video_input" "$destination"
  stored_video="inputs/$file_name"
fi

request_json="$request_dir/request.json"
python3 - "$request_json" "$request_id" "$client_id" "$stored_video" "$direction" "$character_id" "$character_dir" <<'PY'
import json
import sys
from pathlib import Path

output_path = Path(sys.argv[1])
request = {
    "request_id": sys.argv[2],
    "client_id": sys.argv[3],
    "video_input": sys.argv[4],
    "direction": sys.argv[5],
    "prompt": sys.argv[5],
    "process_b_mode": "motion_control",
    "character_id": sys.argv[6],
    "character_dir": sys.argv[7],
    "expected_request_shape": "Motion-control request with input video plus optional background/outfit direction.",
}
output_path.write_text(json.dumps(request, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

{
  printf 'video_input: %s\n' "$stored_video"
  printf 'character_id: %s\n' "$character_id"
  printf 'direction: %s\n' "$direction"
} > "$request_dir/request.txt"

cat > "$request_dir/status.json" <<EOF
{
  "request_id": "$request_id",
  "stage": "process_a",
  "status": "accepted",
  "updated_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF
chown "$user:$user" "$request_json" "$request_dir/request.txt" "$check_report" "$request_dir/status.json"
chmod 600 "$request_json" "$request_dir/request.txt" "$check_report" "$request_dir/status.json"

brain_script="$REPO_ROOT/brain/motion_control.py"
run_as_client_with_fal "$user" "$request_dir" "$(printf '%q' "$BRAIN_PYTHON") $(printf '%q' "$brain_script") --request request.json --character-dir $(printf '%q' "$character_dir")"

evaluator_script="$REPO_ROOT/brain/evaluate_image.py"
run_as_client "$user" "$request_dir" "$(printf '%q' "$BRAIN_PYTHON") $(printf '%q' "$evaluator_script") ."

echo "$request_dir"
