#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

require_root

if [[ "$#" -lt 2 ]]; then
  echo "usage: submit_request.sh <client-id> <prompt> [image-url-or-file ...]" >&2
  exit 2
fi

client_id="$(sanitize_client_id "$1")"
prompt="$2"
shift 2
user="$(client_user_for "$client_id")"
home_dir="$(client_home_for "$client_id")"

load_fal_key
ensure_client_user_and_home "$client_id"

request_id="$(date -u +%Y%m%dT%H%M%S%NZ)-video"
request_dir="$home_dir/requests/$request_id"
inputs_dir="$request_dir/inputs"

install -d -m 700 -o "$user" -g "$user" "$request_dir"
install -d -m 700 -o "$user" -g "$user" "$inputs_dir"

check_report="$request_dir/request_check.json"
check_args=("--prompt" "$prompt" "--json-output" "$check_report")
for image_ref in "$@"; do
  check_args+=("--image" "$image_ref")
done
"$REPO_ROOT/supervisor/check_request.py" "${check_args[@]}"

image_inputs_json="[]"
copied_inputs=()
for image_ref in "$@"; do
  if [[ "$image_ref" =~ ^https?:// ]]; then
    copied_inputs+=("$image_ref")
  else
    file_name="$(basename "$image_ref")"
    destination="$inputs_dir/$file_name"
    install -m 600 -o "$user" -g "$user" "$image_ref" "$destination"
    copied_inputs+=("inputs/$file_name")
  fi
done

request_json="$request_dir/request.json"
python3 - "$request_json" "$request_id" "$client_id" "$prompt" "${copied_inputs[@]}" <<'PY'
import json
import sys
from pathlib import Path

output_path = Path(sys.argv[1])
request_id = sys.argv[2]
client_id = sys.argv[3]
prompt = sys.argv[4]
image_inputs = sys.argv[5:]

request = {
    "request_id": request_id,
    "client_id": client_id,
    "prompt": prompt,
    "image_inputs": image_inputs,
    "num_images": 4,
    "aspect_ratio": "auto",
    "output_format": "png",
    "resolution": "1K",
    "safety_tolerance": "1",
    "limit_generations": True,
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
chown "$user:$user" "$request_dir/request.txt" "$request_dir/request.json" "$check_report" "$request_dir/status.json"
chmod 600 "$request_dir/request.txt" "$request_dir/request.json" "$check_report" "$request_dir/status.json"

brain_script="$REPO_ROOT/brain/nano_banana.py"
run_as_client_with_fal "$user" "$request_dir" "$(printf '%q' "$BRAIN_PYTHON") $(printf '%q' "$brain_script") --request request.json"

evaluator_script="$REPO_ROOT/brain/evaluate_image.py"
run_as_client "$user" "$request_dir" "$(printf '%q' "$BRAIN_PYTHON") $(printf '%q' "$evaluator_script") ."

echo "$request_dir"
