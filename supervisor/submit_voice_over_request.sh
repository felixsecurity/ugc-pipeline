#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

require_root

if [[ "$#" -lt 2 || "$#" -gt 3 ]]; then
  echo "usage: submit_voice_over_request.sh <client-id> <request-text-or-file> [character-id]" >&2
  exit 2
fi

client_id="$(sanitize_client_id "$1")"
request_input="$2"
character_id="${3:-astrid}"
character_id="$(sanitize_request_value "$character_id")"
user="$(client_user_for "$client_id")"
home_dir="$(client_home_for "$client_id")"
character_dir="$REPO_ROOT/characters/$character_id"

if [[ ! -d "$character_dir" ]]; then
  echo "character does not exist: $character_dir" >&2
  exit 2
fi

if [[ -f "$request_input" ]]; then
  prompt="$(<"$request_input")"
else
  prompt="$request_input"
fi

load_media_generation_keys
ensure_client_user_and_home "$client_id"

request_id="$(date -u +%Y%m%dT%H%M%S%NZ)-voice-over"
request_dir="$home_dir/requests/$request_id"
install -d -m 700 -o "$user" -g "$user" "$request_dir"

check_report="$request_dir/request_check.json"
"$REPO_ROOT/supervisor/check_request.py" --prompt "$prompt" --json-output "$check_report"

request_json="$request_dir/request.json"
python3 - "$request_json" "$request_id" "$client_id" "$prompt" "$character_id" "$character_dir" <<'PY'
import json
import sys
from pathlib import Path

output_path = Path(sys.argv[1])
request = {
    "request_id": sys.argv[2],
    "client_id": sys.argv[3],
    "prompt": sys.argv[4],
    "process_b_mode": "voice_over",
    "character_id": sys.argv[5],
    "character_dir": sys.argv[6],
    "expected_request_shape": 'Use Astrid. Stage direction: "...". Voiceover: "..."',
}
output_path.write_text(json.dumps(request, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

printf '%s\n' "$prompt" > "$request_dir/request.txt"
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

brain_script="$REPO_ROOT/brain/voice_over.py"
run_as_client_with_media_generation_keys "$user" "$request_dir" "$(printf '%q' "$BRAIN_PYTHON") $(printf '%q' "$brain_script") --request request.json --character-dir $(printf '%q' "$character_dir")"

evaluator_script="$REPO_ROOT/brain/evaluate_image.py"
run_as_client "$user" "$request_dir" "$(printf '%q' "$BRAIN_PYTHON") $(printf '%q' "$evaluator_script") ."

echo "$request_dir"
