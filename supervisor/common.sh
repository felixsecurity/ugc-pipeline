#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UGC_RUNTIME_ROOT="${UGC_RUNTIME_ROOT:-/srv/ugc-pipeline}"
CLIENTS_ROOT="$UGC_RUNTIME_ROOT/clients"
CODEX_TEMPLATE_ROOT="$UGC_RUNTIME_ROOT/codex-template"
RUNTIME_BRAIN_ROOT="$UGC_RUNTIME_ROOT/brain"

require_root() {
  if [[ "$(id -u)" != "0" ]]; then
    echo "This supervisor script must run as root." >&2
    exit 1
  fi
}

sanitize_client_id() {
  local raw="$1"
  if [[ ! "$raw" =~ ^[A-Za-z0-9_-]+$ ]]; then
    echo "client id may only contain letters, numbers, underscore, and dash: $raw" >&2
    exit 2
  fi
  echo "$raw" | tr '[:upper:]' '[:lower:]' | tr '-' '_'
}

sanitize_request_value() {
  local raw="$1"
  if [[ ! "$raw" =~ ^[A-Za-z0-9_-]+$ ]]; then
    echo "request value may only contain letters, numbers, underscore, and dash: $raw" >&2
    exit 2
  fi
  echo "$raw"
}

client_user_for() {
  local client_id="$1"
  echo "ugc_${client_id}"
}

client_home_for() {
  local client_id="$1"
  echo "$CLIENTS_ROOT/$client_id"
}

ensure_codex_template() {
  mkdir -p "$CODEX_TEMPLATE_ROOT"
  chmod 700 "$CODEX_TEMPLATE_ROOT"

  if [[ -f /root/.codex/auth.json ]]; then
    install -m 600 -o root -g root /root/.codex/auth.json "$CODEX_TEMPLATE_ROOT/auth.json"
  fi

  if [[ -f /root/.codex/config.toml ]]; then
    install -m 600 -o root -g root /root/.codex/config.toml "$CODEX_TEMPLATE_ROOT/config.toml"
  fi
}

publish_brain() {
  rm -rf "$RUNTIME_BRAIN_ROOT"
  install -d -m 755 -o root -g root "$RUNTIME_BRAIN_ROOT"

  cp -a "$REPO_ROOT/brain/." "$RUNTIME_BRAIN_ROOT/"
  find "$RUNTIME_BRAIN_ROOT" -name '__pycache__' -type d -prune -exec rm -rf {} +
  chown -R root:root "$RUNTIME_BRAIN_ROOT"
  find "$RUNTIME_BRAIN_ROOT" -type d -exec chmod 755 {} +
  find "$RUNTIME_BRAIN_ROOT" -type f -exec chmod 644 {} +
  find "$RUNTIME_BRAIN_ROOT" -type f -name '*.py' -exec chmod 755 {} +
}

install_codex_home_for_user() {
  local user="$1"
  local home_dir="$2"
  local codex_dir="$home_dir/.codex"

  mkdir -p "$codex_dir"

  if [[ -f "$CODEX_TEMPLATE_ROOT/auth.json" ]]; then
    install -m 600 -o "$user" -g "$user" "$CODEX_TEMPLATE_ROOT/auth.json" "$codex_dir/auth.json"
  fi

  if [[ -f "$CODEX_TEMPLATE_ROOT/config.toml" ]]; then
    install -m 600 -o "$user" -g "$user" "$CODEX_TEMPLATE_ROOT/config.toml" "$codex_dir/config.toml"
  fi

  chown -R "$user:$user" "$codex_dir"
  chmod 700 "$codex_dir"
}

ensure_client_user_and_home() {
  local client_id="$1"
  local user
  local home_dir
  user="$(client_user_for "$client_id")"
  home_dir="$(client_home_for "$client_id")"

  mkdir -p "$CLIENTS_ROOT"
  chmod 711 "$UGC_RUNTIME_ROOT"
  chmod 711 "$CLIENTS_ROOT"

  if ! id "$user" >/dev/null 2>&1; then
    useradd --system --create-home --home-dir "$home_dir" --shell /usr/sbin/nologin "$user"
  fi

  mkdir -p "$home_dir/requests"
  chown -R "$user:$user" "$home_dir"
  chmod 700 "$home_dir"
  chmod 700 "$home_dir/requests"

  ensure_codex_template
  publish_brain
  install_codex_home_for_user "$user" "$home_dir"
}

run_as_client() {
  local user="$1"
  local workdir="$2"
  shift 2
  runuser -u "$user" -- bash -lc "cd $(printf '%q' "$workdir") && $*"
}
