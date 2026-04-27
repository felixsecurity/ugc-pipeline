#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UGC_RUNTIME_ROOT="${UGC_RUNTIME_ROOT:-/srv/ugc-pipeline}"
RUNTIME_BRAIN_ROOT="$UGC_RUNTIME_ROOT/brain"

if [[ "$(id -u)" != "0" ]]; then
  echo "init_permissions.sh must run as root." >&2
  exit 1
fi

chown -R root:root "$REPO_ROOT/brain" "$REPO_ROOT/supervisor"

find "$REPO_ROOT/brain" -type d -exec chmod 755 {} +
find "$REPO_ROOT/brain" -type f -exec chmod 644 {} +
find "$REPO_ROOT/brain" -type f -name '*.py' -exec chmod 755 {} +

chmod 700 "$REPO_ROOT/supervisor"
find "$REPO_ROOT/supervisor" -type f -name '*.sh' -exec chmod 700 {} +

rm -rf "$RUNTIME_BRAIN_ROOT"
install -d -m 755 -o root -g root "$RUNTIME_BRAIN_ROOT"
cp -a "$REPO_ROOT/brain/." "$RUNTIME_BRAIN_ROOT/"
find "$RUNTIME_BRAIN_ROOT" -name '__pycache__' -type d -prune -exec rm -rf {} +
chown -R root:root "$RUNTIME_BRAIN_ROOT"
find "$RUNTIME_BRAIN_ROOT" -type d -exec chmod 755 {} +
find "$RUNTIME_BRAIN_ROOT" -type f -exec chmod 644 {} +
find "$RUNTIME_BRAIN_ROOT" -type f -name '*.py' -exec chmod 755 {} +

echo "Permissions initialized."
