#!/bin/bash
# Install or remove the login item that starts the dashboard when you log in.
# A launchd agent cannot be used: macOS blocks background services from
# ~/Documents. A tiny AppleScript app in ~/Applications runs app-start.sh
# instead; macOS may ask once to allow it access to your Documents folder.
# The app stays running with the dashboard: TCC checks the app that launched
# a process, and once that app has exited every access to ~/Documents fails.
set -eu
cd "$(dirname "$0")/.."
APP="$HOME/Applications/vpnconnect-autostart.app"
START="$PWD/scripts/app-start.sh"
case "${1:-}" in
  install)
    mkdir -p "$HOME/Applications"
    osascript -e 'tell application "System Events" to delete login item "vpnconnect-autostart"' >/dev/null 2>&1 || true
    rm -rf "$APP"
    osacompile -o "$APP" -e "do shell script \"'$START' --foreground >/dev/null 2>&1\""
    # No Dock icon while it runs; re-sign, since editing Info.plist breaks the seal.
    plutil -replace LSUIElement -bool true "$APP/Contents/Info.plist"
    codesign --force --sign - "$APP"
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
