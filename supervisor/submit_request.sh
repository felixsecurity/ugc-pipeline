#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

require_root

if [[ "$#" -ne 2 ]]; then
  echo "usage: submit_request.sh <client-id> <pokemon-name>" >&2
  exit 2
fi

client_id="$(sanitize_client_id "$1")"
pokemon="$(sanitize_request_value "$2")"
user="$(client_user_for "$client_id")"
home_dir="$(client_home_for "$client_id")"

ensure_client_user_and_home "$client_id"

request_id="$(date -u +%Y%m%dT%H%M%S%NZ)-$pokemon"
request_dir="$home_dir/requests/$request_id"

install -d -m 700 -o "$user" -g "$user" "$request_dir"
printf '%s\n' "$pokemon" > "$request_dir/request.txt"
chown "$user:$user" "$request_dir/request.txt"
chmod 600 "$request_dir/request.txt"

brain_script="$REPO_ROOT/brain/get_pokemon.py"
run_as_client "$user" "$request_dir" "$(printf '%q' "$brain_script") $(printf '%q' "$pokemon")"

cat > "$request_dir/learning.md" <<EOF
# Learning

- Client: $client_id
- Request: $pokemon
- Process B wrote \`poke_return.json\` in the request directory.
- Process C should verify the JSON is relevant to "$pokemon" and decide whether any follow-up is needed.
EOF

chown "$user:$user" "$request_dir/learning.md"
chmod 600 "$request_dir/learning.md"

echo "$request_dir"
