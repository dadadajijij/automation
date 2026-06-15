#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SERVICE_NAME="machine-health-check.service"
TIMER_NAME="machine-health-check.timer"

sudo cp "$PROJECT_DIR/deploy/$SERVICE_NAME" "/etc/systemd/system/$SERVICE_NAME"
sudo cp "$PROJECT_DIR/deploy/$TIMER_NAME" "/etc/systemd/system/$TIMER_NAME"
sudo systemctl daemon-reload
sudo systemctl enable --now "$TIMER_NAME"
sudo systemctl start "$SERVICE_NAME"
sudo systemctl status "$TIMER_NAME" --no-pager
