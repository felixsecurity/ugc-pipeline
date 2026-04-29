#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

require_root

if [[ "$#" -lt 2 || "$#" -gt 3 ]]; then
  echo "usage: submit_scripted_tts_request.sh <client-id> <script-file> [character-id]" >&2
  exit 2
fi

client_id="$(sanitize_client_id "$1")"
script_file="$2"
character_id="$(sanitize_request_value "${3:-astrid}")"

if [[ ! -f "$script_file" ]]; then
  echo "script file does not exist: $script_file" >&2
  exit 2
fi

character_dir="$REPO_ROOT/characters/$character_id"
if [[ ! -d "$character_dir" ]]; then
  echo "character does not exist: $character_dir" >&2
  exit 2
fi

user="$(client_user_for "$client_id")"
home_dir="$(client_home_for "$client_id")"

load_elevenlabs_key
ensure_client_user_and_home "$client_id"

request_id="$(date -u +%Y%m%dT%H%M%S%NZ)-scripted-tts"
request_dir="$home_dir/requests/$request_id"
inputs_dir="$request_dir/inputs"

install -d -m 700 -o "$user" -g "$user" "$request_dir"
install -d -m 700 -o "$user" -g "$user" "$inputs_dir"
install -m 600 -o "$user" -g "$user" "$script_file" "$request_dir/script.md"

request_json="$request_dir/request.json"
python3 - "$request_json" "$request_id" "$client_id" "$character_id" "$character_dir" <<'PY'
import json
import sys
from pathlib import Path

output_path = Path(sys.argv[1])
request = {
    "request_id": sys.argv[2],
    "client_id": sys.argv[3],
    "process_b_mode": "scripted_tts",
    "character_id": sys.argv[4],
    "character_dir": sys.argv[5],
    "script": "script.md",
    "goal": "text_to_speech",
    "provider": "elevenlabs",
    "voice_name": "Riley",
    "voice_id": "hA4zGnmTwX2NQiTRMt7o",
    "model_id": "eleven_multilingual_v2",
    "output_format": "mp3_44100_128",
    "voice_settings": {
        "speed": 0.92,
        "stability": 0.78,
        "similarity_boost": 0.85,
        "style": 0.23,
        "use_speaker_boost": True,
    },
}
output_path.write_text(json.dumps(request, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

cat > "$request_dir/status.json" <<EOF
{
  "request_id": "$request_id",
  "stage": "process_a",
  "status": "accepted",
  "updated_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF
chown "$user:$user" "$request_json" "$request_dir/status.json"
chmod 600 "$request_json" "$request_dir/status.json"

brain_script="$REPO_ROOT/brain/elevenlabs_tts.py"
run_as_client_with_elevenlabs "$user" "$request_dir" "$(printf '%q' "$BRAIN_PYTHON") $(printf '%q' "$brain_script") --script script.md --character-dir $(printf '%q' "$character_dir")"

echo "$request_dir"
