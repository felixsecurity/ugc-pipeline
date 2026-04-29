#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLIENTS_ROOT="${UGC_CLIENTS_ROOT:-/srv/ugc-clients}"
CODEX_TEMPLATE_ROOT="${UGC_CODEX_TEMPLATE_ROOT:-/etc/ugc-pipeline/codex-template}"
FAL_ENV_PATH="${UGC_FAL_ENV_PATH:-/etc/ugc-pipeline/fal.env}"
BRAIN_PYTHON="${UGC_BRAIN_PYTHON:-/opt/ugc-pipeline-venv/bin/python}"

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

load_fal_key() {
  if [[ ! -f "$FAL_ENV_PATH" ]]; then
    echo "Missing FAL API key file: $FAL_ENV_PATH" >&2
    echo "Create it as root with: install -m 600 -o root -g root /dev/null $FAL_ENV_PATH" >&2
    echo "Then add: FAL_KEY=your_fal_api_key" >&2
    exit 1
  fi

  # shellcheck source=/dev/null
  source "$FAL_ENV_PATH"

  if [[ -z "${FAL_KEY:-}" && -n "${FALAPIKEY:-}" ]]; then
    FAL_KEY="$FALAPIKEY"
  fi

  if [[ -z "${FAL_KEY:-}" ]]; then
    echo "FAL_KEY is not set in $FAL_ENV_PATH" >&2
    exit 1
  fi
}

load_elevenlabs_key() {
  if [[ ! -f "$FAL_ENV_PATH" ]]; then
    echo "Missing provider API key file: $FAL_ENV_PATH" >&2
    echo "Create it as root with: install -m 600 -o root -g root /dev/null $FAL_ENV_PATH" >&2
    echo "Then add: ELEVENLABS_API_KEY=your_elevenlabs_api_key" >&2
    exit 1
  fi

  # shellcheck source=/dev/null
  source "$FAL_ENV_PATH"

  if [[ -z "${ELEVENLABS_API_KEY:-}" ]]; then
    echo "ELEVENLABS_API_KEY is not set in $FAL_ENV_PATH" >&2
    exit 1
  fi
}

load_media_generation_keys() {
  load_fal_key
  if [[ -z "${ELEVENLABS_API_KEY:-}" ]]; then
    # shellcheck source=/dev/null
    source "$FAL_ENV_PATH"
  fi
  if [[ -z "${ELEVENLABS_API_KEY:-}" ]]; then
    echo "ELEVENLABS_API_KEY is not set in $FAL_ENV_PATH" >&2
    exit 1
  fi
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
  install -d -m 700 -o root -g root "$(dirname "$CODEX_TEMPLATE_ROOT")"
  mkdir -p "$CODEX_TEMPLATE_ROOT"
  chmod 700 "$CODEX_TEMPLATE_ROOT"

  if [[ -f /root/.codex/auth.json ]]; then
    install -m 600 -o root -g root /root/.codex/auth.json "$CODEX_TEMPLATE_ROOT/auth.json"
  fi

  if [[ -f /root/.codex/config.toml ]]; then
    install -m 600 -o root -g root /root/.codex/config.toml "$CODEX_TEMPLATE_ROOT/config.toml"
  fi
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
  local current_home
  user="$(client_user_for "$client_id")"
  home_dir="$(client_home_for "$client_id")"

  mkdir -p "$CLIENTS_ROOT"
  chmod 711 "$(dirname "$CLIENTS_ROOT")"
  chmod 711 "$CLIENTS_ROOT"

  if id "$user" >/dev/null 2>&1; then
    current_home="$(getent passwd "$user" | cut -d: -f6)"
    if [[ "$current_home" != "$home_dir" ]]; then
      usermod -d "$home_dir" "$user"
    fi
  else
    useradd --system --create-home --home-dir "$home_dir" --shell /usr/sbin/nologin "$user"
  fi

  mkdir -p "$home_dir/requests"
  chown -R "$user:$user" "$home_dir"
  chmod 700 "$home_dir"
  chmod 700 "$home_dir/requests"

  ensure_codex_template
  install_codex_home_for_user "$user" "$home_dir"
}

run_as_client() {
  local user="$1"
  local workdir="$2"
  shift 2
  runuser -u "$user" -- bash -lc "cd $(printf '%q' "$workdir") && $*"
}

run_as_client_with_fal() {
  local user="$1"
  local workdir="$2"
  local env_file
  shift 2
  load_fal_key
  env_file="$(mktemp)"
  printf 'export FAL_KEY=%q\n' "$FAL_KEY" > "$env_file"
  chown "$user:$user" "$env_file"
  chmod 600 "$env_file"
  runuser -u "$user" -- bash -lc "source $(printf '%q' "$env_file"); rm -f $(printf '%q' "$env_file"); cd $(printf '%q' "$workdir") && $*"
}

run_as_client_with_elevenlabs() {
  local user="$1"
  local workdir="$2"
  local env_file
  shift 2
  load_elevenlabs_key
  env_file="$(mktemp)"
  printf 'export ELEVENLABS_API_KEY=%q\n' "$ELEVENLABS_API_KEY" > "$env_file"
  chown "$user:$user" "$env_file"
  chmod 600 "$env_file"
  runuser -u "$user" -- bash -lc "source $(printf '%q' "$env_file"); rm -f $(printf '%q' "$env_file"); cd $(printf '%q' "$workdir") && $*"
}

run_as_client_with_media_generation_keys() {
  local user="$1"
  local workdir="$2"
  local env_file
  shift 2
  load_media_generation_keys
  env_file="$(mktemp)"
  {
    printf 'export FAL_KEY=%q\n' "$FAL_KEY"
    printf 'export ELEVENLABS_API_KEY=%q\n' "$ELEVENLABS_API_KEY"
  } > "$env_file"
  chown "$user:$user" "$env_file"
  chmod 600 "$env_file"
  runuser -u "$user" -- bash -lc "source $(printf '%q' "$env_file"); rm -f $(printf '%q' "$env_file"); cd $(printf '%q' "$workdir") && $*"
}
