#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

require_root

if [[ "$#" -lt 2 ]]; then
  echo "usage: run_codex_for_client.sh <client-id> <codex-args...>" >&2
  exit 2
fi

client_id="$(sanitize_client_id "$1")"
shift
user="$(client_user_for "$client_id")"
home_dir="$(client_home_for "$client_id")"

ensure_client_user_and_home "$client_id"

runuser -u "$user" -- env HOME="$home_dir" CODEX_HOME="$home_dir/.codex" codex "$@"
