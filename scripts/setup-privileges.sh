#!/bin/bash
# One-time privilege setup for vpnconnect. Run from the repository root:
#
#     sudo scripts/setup-privileges.sh
#
# Installs vpnconnect-helper and vpnc-split.sh root-owned under
# /usr/local/libexec/vpnconnect, bakes in the absolute paths they need, and
# allows the invoking user to run the helper (and only the helper) through
# sudo without a password. Safe to re-run after pulling changes to the scripts.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "run with sudo: sudo $0" >&2
  exit 1
fi
TARGET_USER="${SUDO_USER:-}"
if [ -z "$TARGET_USER" ] || [ "$TARGET_USER" = "root" ]; then
  echo "could not determine the invoking user (SUDO_USER is unset); run via sudo, not as root" >&2
  exit 1
fi

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LIBEXEC_DIR="${LIBEXEC_DIR:-/usr/local/libexec/vpnconnect}"
STATE_DIR="${STATE_DIR:-$REPO_DIR/state}"
SUDOERS_FILE="/etc/sudoers.d/vpnconnect"
VPNC_SCRIPT="${VPNC_SCRIPT:-/opt/homebrew/etc/vpnc/vpnc-script}"
OPENCONNECT="${OPENCONNECT:-$(command -v openconnect || echo /opt/homebrew/bin/openconnect)}"

if [ ! -x "$OPENCONNECT" ]; then
  echo "openconnect not found at $OPENCONNECT; install it with: brew install openconnect" >&2
  exit 1
fi
if [ ! -f "$VPNC_SCRIPT" ]; then
  echo "vpnc-script not found at $VPNC_SCRIPT (set VPNC_SCRIPT=/path to override)" >&2
  exit 1
fi

mkdir -p "$LIBEXEC_DIR"
sed -e "s|__STATE_DIR__|$STATE_DIR|g" \
    -e "s|__LIBEXEC_DIR__|$LIBEXEC_DIR|g" \
    -e "s|__OPENCONNECT__|$OPENCONNECT|g" \
    "$REPO_DIR/scripts/vpnconnect-helper" >"$LIBEXEC_DIR/vpnconnect-helper"
sed -e "s|__VPNC_SCRIPT__|$VPNC_SCRIPT|g" \
    "$REPO_DIR/scripts/vpnc-split.sh" >"$LIBEXEC_DIR/vpnc-split.sh"
chown root:wheel "$LIBEXEC_DIR" "$LIBEXEC_DIR/vpnconnect-helper" "$LIBEXEC_DIR/vpnc-split.sh"
chmod 755 "$LIBEXEC_DIR" "$LIBEXEC_DIR/vpnconnect-helper" "$LIBEXEC_DIR/vpnc-split.sh"

mkdir -p "$STATE_DIR"
chown "$TARGET_USER" "$STATE_DIR"

TMP_SUDOERS="$(mktemp)"
printf '%s ALL=(root) NOPASSWD: %s/vpnconnect-helper\n' "$TARGET_USER" "$LIBEXEC_DIR" >"$TMP_SUDOERS"
visudo -cf "$TMP_SUDOERS" >/dev/null
install -m 0440 -o root -g wheel "$TMP_SUDOERS" "$SUDOERS_FILE"
rm -f "$TMP_SUDOERS"

echo "installed:"
echo "  $LIBEXEC_DIR/vpnconnect-helper"
echo "  $LIBEXEC_DIR/vpnc-split.sh"
echo "  $SUDOERS_FILE  ($TARGET_USER may run the helper without a password)"
echo "state dir: $STATE_DIR"
echo
echo "check it works (should print 'ok' with no password prompt):"
echo "  sudo -n $LIBEXEC_DIR/vpnconnect-helper disconnect selftest && echo ok"
