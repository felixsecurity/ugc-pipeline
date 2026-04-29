#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ "$(id -u)" != "0" ]]; then
  echo "init_permissions.sh must run as root." >&2
  exit 1
fi

chown -R root:root "$REPO_ROOT/brain" "$REPO_ROOT/supervisor" "$REPO_ROOT/characters"

find "$REPO_ROOT/brain" -type d -exec chmod 755 {} +
find "$REPO_ROOT/brain" -type f -exec chmod 644 {} +
find "$REPO_ROOT/brain" -type f -name '*.py' -exec chmod 755 {} +

find "$REPO_ROOT/characters" -type d -exec chmod 755 {} +
find "$REPO_ROOT/characters" -type f -exec chmod 644 {} +

chmod 700 "$REPO_ROOT/supervisor"
find "$REPO_ROOT/supervisor" -type f -name '*.sh' -exec chmod 700 {} +
find "$REPO_ROOT/supervisor" -type f -name '*.py' -exec chmod 700 {} +

echo "Permissions initialized."
