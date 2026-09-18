#!/bin/bash
# Stop the background vpnconnect dashboard. VPN tunnels stay up (openconnect
# runs as root on its own); the dashboard adopts them again when restarted.
# Press "Disconnect all" in the dashboard first if you want the tunnels down.
set -u
cd "$(dirname "$0")/.."
PORT=$(sed -n 's/^FLASK_RUN_PORT=//p' .flaskenv 2>/dev/null)
PORT=${PORT:-5110}
PIDS=$(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null || true)
if [ -z "$PIDS" ]; then
  echo "vpnconnect is not running"
  exit 0
fi
# shellcheck disable=SC2086
kill $PIDS && echo "vpnconnect stopped"
