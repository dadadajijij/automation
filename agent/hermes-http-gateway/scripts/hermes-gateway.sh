#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_BIN="${APP_BIN:-$ROOT_DIR/.venv/bin/hermes-http-gateway}"
PID_FILE="${PID_FILE:-$ROOT_DIR/.hermes-gateway.pid}"
LOG_FILE="${LOG_FILE:-$ROOT_DIR/.hermes-gateway.log}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8003}"

is_running() {
  [[ -f "$PID_FILE" ]] || return 1
  local pid
  pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  [[ -n "${pid:-}" ]] || return 1
  kill -0 "$pid" 2>/dev/null
}

cleanup_stale_pidfile() {
  if [[ -f "$PID_FILE" ]] && ! is_running; then
    rm -f "$PID_FILE"
  fi
}

start() {
  cleanup_stale_pidfile
  if is_running; then
    echo "hermes-gateway already running: $(cat "$PID_FILE")"
    return 0
  fi

  if [[ ! -x "$APP_BIN" ]]; then
    echo "missing executable: $APP_BIN" >&2
    echo "run uv sync first" >&2
    return 1
  fi

  cd "$ROOT_DIR"
  nohup env HOST="$HOST" PORT="$PORT" "$APP_BIN" >>"$LOG_FILE" 2>&1 &
  echo $! >"$PID_FILE"
  echo "started hermes-gateway on ${HOST}:${PORT}"
  echo "pid: $(cat "$PID_FILE")"
  echo "log: $LOG_FILE"
}

stop() {
  if ! is_running; then
    cleanup_stale_pidfile
    echo "hermes-gateway is not running"
    return 0
  fi

  local pid
  pid="$(cat "$PID_FILE")"
  kill "$pid" 2>/dev/null || true

  for _ in {1..50}; do
    if ! kill -0 "$pid" 2>/dev/null; then
      rm -f "$PID_FILE"
      echo "stopped hermes-gateway"
      return 0
    fi
    sleep 0.1
  done

  kill -9 "$pid" 2>/dev/null || true
  rm -f "$PID_FILE"
  echo "force-stopped hermes-gateway"
}

status() {
  if is_running; then
    echo "running: pid $(cat "$PID_FILE")"
    echo "log: $LOG_FILE"
  else
    cleanup_stale_pidfile
    echo "not running"
  fi
}

logs() {
  touch "$LOG_FILE"
  tail -f "$LOG_FILE"
}

case "${1:-}" in
  start)
    start
    ;;
  stop)
    stop
    ;;
  restart)
    stop
    start
    ;;
  status)
    status
    ;;
  logs)
    logs
    ;;
  *)
    cat <<'EOF'
Usage: scripts/hermes-gateway.sh {start|stop|restart|status|logs}

Environment:
  HOST=127.0.0.1   Bind address for the gateway
  PORT=8003        Listen port
  APP_BIN=...      Override gateway executable path
  PID_FILE=...     Override pid file path
  LOG_FILE=...     Override log file path
EOF
    exit 1
    ;;
esac
