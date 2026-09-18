#!/bin/bash
# Start the vpnconnect dashboard in the background (it survives closing the
# terminal). Same as `uv run flask run`, detached, logging to ~/Library/Logs.
set -eu
cd "$(dirname "$0")/.."
# Launched from a login item the PATH is minimal; make sure uv is found.
export PATH="$PATH:/opt/homebrew/bin:$HOME/.local/bin"
PORT=$(sed -n 's/^FLASK_RUN_PORT=//p' .flaskenv 2>/dev/null)
PORT=${PORT:-5110}
LOG="$HOME/Library/Logs/vpnconnect.log"
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t >/dev/null 2>&1; then
  echo "vpnconnect is already running: http://127.0.0.1:$PORT/"
  exit 0
fi
nohup uv run flask run >>"$LOG" 2>&1 &
for _ in 1 2 3 4 5 6 7 8 9 10; do
  sleep 1
  if curl -fs -m 2 "http://127.0.0.1:$PORT/api/v1/health" >/dev/null 2>&1; then
    echo "vpnconnect is running in the background: http://127.0.0.1:$PORT/"
    echo "log: $LOG"
    exit 0
  fi
done
echo "vpnconnect did not start; see $LOG" >&2
exit 1
