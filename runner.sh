#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CACHE_IMAGE_LIST_PATH="$SCRIPT_DIR/codex-home-cache/local-official-images.txt"
DEFAULT_PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

ensure_codex_in_path() {
  if command -v codex >/dev/null 2>&1; then
    return
  fi

  local parent_exe=""
  local parent_bin=""
  parent_exe="$(readlink -f "/proc/$PPID/exe" 2>/dev/null || true)"
  if [[ -n "$parent_exe" ]]; then
    parent_bin="$(dirname "$parent_exe")"
    if [[ -x "$parent_bin/codex" ]]; then
      export PATH="${parent_bin}:${PATH:-$DEFAULT_PATH}"
      return
    fi
  fi

  shopt -s nullglob
  local candidate_bin=""
  for candidate_bin in "$HOME"/.nvm/versions/node/*/bin; do
    if [[ -x "$candidate_bin/codex" ]]; then
      export PATH="${candidate_bin}:${PATH:-$DEFAULT_PATH}"
      break
    fi
  done
  shopt -u nullglob
}

ensure_codex_in_path

if command -v podman >/dev/null 2>&1; then
  podman_images_output="$(podman images --format '{{.Repository}}:{{.Tag}}' 2>/dev/null || true)"
  if [[ -n "$podman_images_output" ]]; then
    local_official_images="$(
      printf '%s\n' "$podman_images_output" \
        | awk '/^docker\.io\/library\// && $0 !~ /:<none>$/ { print }' \
        | sort -u \
        | paste -sd',' -
    )"
    export AUTOMATION_LOCAL_OFFICIAL_IMAGES="${local_official_images:-}"
    if [[ -n "${local_official_images:-}" ]]; then
      printf '%s\n' "$local_official_images" > "$CACHE_IMAGE_LIST_PATH"
    fi
  fi
fi

exec python3 "$SCRIPT_DIR/runner.py" "$@"
