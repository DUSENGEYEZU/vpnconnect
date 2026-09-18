#!/bin/bash
# Install or remove the login item that starts the dashboard when you log in.
# A launchd agent cannot be used: macOS blocks background services from
# ~/Documents. A tiny AppleScript app in ~/Applications runs app-start.sh
# instead; macOS may ask once to allow it access to your Documents folder.
set -eu
cd "$(dirname "$0")/.."
APP="$HOME/Applications/vpnconnect-autostart.app"
START="$PWD/scripts/app-start.sh"
case "${1:-}" in
  install)
    mkdir -p "$HOME/Applications"
    osascript -e 'tell application "System Events" to delete login item "vpnconnect-autostart"' >/dev/null 2>&1 || true
    rm -rf "$APP"
    osacompile -o "$APP" -e "do shell script \"'$START' >/dev/null 2>&1\""
    osascript -e "tell application \"System Events\" to make login item at end with properties {path:\"$APP\", hidden:true}" >/dev/null
    echo "installed: $APP runs scripts/app-start.sh at login"
    ;;
  remove)
    osascript -e 'tell application "System Events" to delete login item "vpnconnect-autostart"' >/dev/null 2>&1 || true
    rm -rf "$APP"
    echo "removed the vpnconnect login item"
    ;;
  *)
    echo "usage: scripts/autostart.sh install|remove" >&2
    exit 64
    ;;
esac
