#!/bin/bash
# vpnconnect wrapper around openconnect's vpnc-script.
#
# openconnect runs this script with the connection described in environment
# variables (reason, TUNDEV, INTERNAL_IP4_ADDRESS, CISCO_SPLIT_INC and
# CISCO_SPLIT_INC_<i>_{ADDR,MASK,MASKLEN}); vpnconnect-helper adds
# VPNCONNECT_ID, VPNCONNECT_STATE_DIR and VPNCONNECT_ROUTES.
#
# 1. Split-routing guarantee. When the server pushes a full tunnel (no
#    CISCO_SPLIT_INC, or a split list that takes the default route), inject this
#    VPN's configured routes so vpnc-script adds only those and never a default
#    route. No routes configured: refuse.
# 2. Route the DNS servers the gateway pushes through the tunnel as /32
#    includes, as Cisco's client does. The stock vpnc-script only registers
#    them, so in split mode lookups for the VPN's domain would leave through
#    the normal interface and time out.
# 3. DNS. vpnc-script's macOS split-mode DNS handling prepends the pushed
#    servers to the primary resolver list, rewrites the Wi-Fi DNS with
#    networksetup and marks the tunnel OverridePrimary, so one unreachable VPN
#    resolver stalls every lookup on the Mac. The wrapper hides INTERNAL_IP4_DNS
#    from vpnc-script and registers the pushed servers only as a supplemental
#    resolver for the VPN's own domain(s), and only if one answers through the
#    tunnel. On disconnect it removes that entry.
# 4. Record "<TUNDEV> <IP>" in <state>/<id>.iface after a successful connect
#    so the dashboard can show them; remove the file on disconnect.
#
# Must run on macOS /bin/bash 3.2: no arrays, no mapfile.
set -u

# Replaced by scripts/setup-privileges.sh at install time.
VPNC_SCRIPT="__VPNC_SCRIPT__"

server_sent_split() {
  [ -n "${CISCO_SPLIT_INC:-}" ] && [ "${CISCO_SPLIT_INC}" -ge 1 ] 2>/dev/null
}

# vpnc-script turns an include of 0.0.0.0 into set_ipv4_default_route, and a
# masklen of 0 covers every address: either one is a full tunnel wearing a
# split list. Only called once server_sent_split proved the count is a number.
server_split_takes_the_default_route() {
  local i=0 addr len
  while [ "$i" -lt "$CISCO_SPLIT_INC" ]; do
    eval "addr=\${CISCO_SPLIT_INC_${i}_ADDR:-}"
    eval "len=\${CISCO_SPLIT_INC_${i}_MASKLEN:-}"
    if [ "$addr" = "0.0.0.0" ] || [ "$len" = "0" ]; then
      return 0
    fi
    i=$((i + 1))
  done
  return 1
}

# Drop the server's list entirely so vpnc-script sees only what we inject.
unset_server_split() {
  local i=0
  while [ "$i" -lt "$CISCO_SPLIT_INC" ]; do
    unset "CISCO_SPLIT_INC_${i}_ADDR" "CISCO_SPLIT_INC_${i}_MASK" \
      "CISCO_SPLIT_INC_${i}_MASKLEN" "CISCO_SPLIT_INC_${i}_PROTOCOL" \
      "CISCO_SPLIT_INC_${i}_SPORT" "CISCO_SPLIT_INC_${i}_DPORT"
    i=$((i + 1))
  done
  unset CISCO_SPLIT_INC
}

# True only for a server list that adds routes without taking the default one.
usable_server_split() {
  server_sent_split || return 1
  if server_split_takes_the_default_route; then
    unset_server_split
    return 1
  fi
  return 0
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

# Append a host include for every pushed DNS server that is not already an
# include. Same result on connect and disconnect, so vpnc-script removes them.
add_dns_includes() {
  local dns i n addr found
  [ -n "${INTERNAL_IP4_DNS:-}" ] || return 0
  n="$CISCO_SPLIT_INC"
  for dns in $INTERNAL_IP4_DNS; do
    case "$dns" in
      *[!0-9.]*|"") continue ;;
    esac
    found=0
    i=0
    while [ "$i" -lt "$n" ]; do
      eval "addr=\${CISCO_SPLIT_INC_${i}_ADDR:-}"
      if [ "$addr" = "$dns" ]; then
        found=1
        break
      fi
      i=$((i + 1))
    done
    if [ "$found" -eq 0 ]; then
      export "CISCO_SPLIT_INC_${n}_ADDR=$dns"
      export "CISCO_SPLIT_INC_${n}_MASK=255.255.255.255"
      export "CISCO_SPLIT_INC_${n}_MASKLEN=32"
      export "CISCO_SPLIT_INC_${n}_PROTOCOL=0"
      export "CISCO_SPLIT_INC_${n}_SPORT=0"
      export "CISCO_SPLIT_INC_${n}_DPORT=0"
      n=$((n + 1))
    fi
  done
  export CISCO_SPLIT_INC=$n
}

case "${reason:-}" in
  connect|reconnect)
    if ! usable_server_split; then
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
    if ! usable_server_split && [ -n "${VPNCONNECT_ROUTES:-}" ]; then
      inject_routes
    fi
    ;;
esac

case "${reason:-}" in
  connect|reconnect|disconnect)
    if server_sent_split; then
      add_dns_includes
    fi
    ;;
esac

# Register the pushed DNS servers for the VPN's domains only, if they answer.
register_vpn_dns() {
  local server responding="" domains
  [ -n "$VPN_DNS_SERVERS" ] && [ -n "${TUNDEV:-}" ] || return 0
  domains=$(printf '%s' "$VPN_DNS_DOMAINS" | tr -s ' ' | sed 's/^ //;s/ $//')
  if [ -z "$domains" ]; then
    echo "vpnconnect: server pushed DNS $VPN_DNS_SERVERS without a domain; not registering it (it would apply to every lookup)" >&2
    return 0
  fi
  for server in $VPN_DNS_SERVERS; do
    if dig +time=1 +tries=1 +short "@$server" "${domains%% *}" SOA >/dev/null 2>&1; then
      responding="$responding $server"
    fi
  done
  responding="${responding# }"
  if [ -z "$responding" ]; then
    echo "vpnconnect: DNS $VPN_DNS_SERVERS did not answer through $TUNDEV; not registering it" >&2
    return 0
  fi
  scutil >/dev/null 2>&1 <<EOF
open
d.init
d.add ServerAddresses * $responding
d.add SupplementalMatchDomains * $domains
set State:/Network/Service/$TUNDEV/DNS
close
EOF
}

unregister_vpn_dns() {
  [ -n "${TUNDEV:-}" ] || return 0
  scutil >/dev/null 2>&1 <<EOF
open
remove State:/Network/Service/$TUNDEV/DNS
close
EOF
}

# Keep the DNS details for ourselves and hide them from vpnc-script, whose
# whole DNS block (prepend, networksetup, OverridePrimary) is gated on
# INTERNAL_IP4_DNS being set.
VPN_DNS_SERVERS="${INTERNAL_IP4_DNS:-}"
VPN_DNS_DOMAINS="${CISCO_DEF_DOMAIN:-}"
if [ -n "${CISCO_SPLIT_DNS:-}" ]; then
  VPN_DNS_DOMAINS="$VPN_DNS_DOMAINS $(printf '%s' "$CISCO_SPLIT_DNS" | tr ',' ' ')"
fi
unset INTERNAL_IP4_DNS INTERNAL_IP6_DNS

"$VPNC_SCRIPT" "$@"
rc=$?

case "${reason:-}" in
  connect|reconnect)
    if [ "$rc" -eq 0 ]; then
      register_vpn_dns
    fi
    ;;
  disconnect)
    unregister_vpn_dns
    ;;
esac

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
