#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

require_root

if [[ "$#" -ne 2 ]]; then
  echo "usage: submit_astrid_avatar_request.sh <client-id> <request-text-or-script-file>" >&2
  exit 2
fi

client_id="$(sanitize_client_id "$1")"
request_input="$2"
user="$(client_user_for "$client_id")"
home_dir="$(client_home_for "$client_id")"
character_id="astrid"
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

request_id="$(date -u +%Y%m%dT%H%M%S%NZ)-astrid-avatar"
request_dir="$home_dir/requests/$request_id"
install -d -m 700 -o "$user" -g "$user" "$request_dir"

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
    "process_b_mode": "astrid_scripted_avatar",
    "character_id": sys.argv[5],
    "character_dir": sys.argv[6],
    "expected_request_shape": 'Use Astrid and let her say: "...script..."',
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
chown "$user:$user" "$request_json" "$request_dir/request.txt" "$request_dir/status.json"
chmod 600 "$request_json" "$request_dir/request.txt" "$request_dir/status.json"

brain_script="$REPO_ROOT/brain/astrid_avatar.py"
run_as_client_with_media_generation_keys "$user" "$request_dir" "$(printf '%q' "$BRAIN_PYTHON") $(printf '%q' "$brain_script") --request request.json --character-dir $(printf '%q' "$character_dir")"

echo "$request_dir"
