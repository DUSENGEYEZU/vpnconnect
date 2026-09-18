#!/bin/bash
# vpnconnect wrapper around openconnect's vpnc-script.
#
# openconnect runs this script with the connection described in environment
# variables (reason, TUNDEV, INTERNAL_IP4_ADDRESS, CISCO_SPLIT_INC and
# CISCO_SPLIT_INC_<i>_{ADDR,MASK,MASKLEN}); vpnconnect-helper adds
# VPNCONNECT_ID, VPNCONNECT_STATE_DIR and VPNCONNECT_ROUTES.
#
# 1. Split-routing guarantee. When the server pushes a full tunnel (no
#    CISCO_SPLIT_INC), inject this VPN's configured routes so vpnc-script adds
#    only those and never a default route. No routes configured: refuse.
# 2. Record "<TUNDEV> <IP>" in <state>/<id>.iface after a successful connect
#    so the dashboard can show them; remove the file on disconnect.
#
# Must run on macOS /bin/bash 3.2: no arrays, no mapfile.
set -u

# Replaced by scripts/setup-privileges.sh at install time.
VPNC_SCRIPT="__VPNC_SCRIPT__"

server_sent_split() {
  [ -n "${CISCO_SPLIT_INC:-}" ] && [ "${CISCO_SPLIT_INC}" -ge 1 ] 2>/dev/null
}

inject_routes() {
  local i=0 triple addr rest mask len
  for triple in $(printf '%s' "$VPNCONNECT_ROUTES" | tr ',' ' '); do
    addr=${triple%%:*}
    rest=${triple#*:}
    mask=${rest%%:*}
    len=${rest#*:}
    export "CISCO_SPLIT_INC_${i}_ADDR=$addr"
    export "CISCO_SPLIT_INC_${i}_MASK=$mask"
    export "CISCO_SPLIT_INC_${i}_MASKLEN=$len"
    export "CISCO_SPLIT_INC_${i}_PROTOCOL=0"
    export "CISCO_SPLIT_INC_${i}_SPORT=0"
    export "CISCO_SPLIT_INC_${i}_DPORT=0"
    i=$((i + 1))
  done
  export CISCO_SPLIT_INC=$i
}

case "${reason:-}" in
  connect|reconnect)
    if ! server_sent_split; then
      if [ -n "${VPNCONNECT_ROUTES:-}" ]; then
        inject_routes
      else
        echo "vpnconnect: server pushed a full tunnel and no routes are configured for this VPN; refusing to take the default route" >&2
        exit 1
      fi
    fi
    ;;
  disconnect)
    # Same environment as on connect so vpnc-script removes exactly what it added.
    if ! server_sent_split && [ -n "${VPNCONNECT_ROUTES:-}" ]; then
      inject_routes
    fi
    ;;
esac

"$VPNC_SCRIPT" "$@"
rc=$?

if [ -n "${VPNCONNECT_ID:-}" ] && [ -n "${VPNCONNECT_STATE_DIR:-}" ]; then
  iface_file="$VPNCONNECT_STATE_DIR/$VPNCONNECT_ID.iface"
  case "${reason:-}" in
    connect|reconnect)
      if [ "$rc" -eq 0 ]; then
        # Runs as root in a user-owned dir: replace the file, never write through it.
        rm -f "$iface_file"
        if ! (set -C; printf '%s %s\n' "${TUNDEV:-}" "${INTERNAL_IP4_ADDRESS:-}" >"$iface_file") 2>/dev/null; then
          echo "vpnconnect: could not create $iface_file" >&2
          exit 1
        fi
      fi
      ;;
    disconnect)
      rm -f "$iface_file"
      ;;
  esac
fi

exit "$rc"
