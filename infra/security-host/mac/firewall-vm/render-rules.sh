#!/bin/sh
# Render the firewall VM's nftables file with the REAL resolver and NTP servers (M3-B-R6; read-only on the host).
#
#     sh render-rules.sh --detect                      # print what this Mac's LAN uses (DHCP DNS, system resolvers)
#     sh render-rules.sh --dns 8.8.8.8,1.1.1.1 --ntp 162.159.200.1,162.159.200.123 [--out FILE]
#
# Why: the design once assumed the router serves DNS and NTP at 192.168.1.1. On this LAN the router serves
# neither (queries to it time out), DHCP hands out public resolvers and no NTP server. Time sync is needed by the
# clock-skew guard (exchange timestamps), so NTP must be allowed to explicit servers. This tool refuses empty lists,
# non-IPv4 values, loopback/private/link-local addresses (an NTP or DNS rule must not open the LAN), and the old
# 192.168.1.1 placeholder. It does not change the router, the host or any running VM.
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
DNS=""; NTP=""; OUT=""; DETECT=0
while [ $# -gt 0 ]; do
  case "$1" in
    --detect) DETECT=1 ;;
    --dns) DNS="${2-}"; shift ;;
    --ntp) NTP="${2-}"; shift ;;
    --out) OUT="${2:?file}"; shift ;;
    -h|--help) sed -n 2,13p "$0"; exit 0 ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
  shift
done
if [ "$DETECT" = 1 ]; then
  echo "DHCP DNS (en0): $(ipconfig getoption en0 domain_name_server 2>/dev/null || echo none)"
  echo "DHCP NTP (en0): $(ipconfig getoption en0 ntp_server 2>/dev/null || echo none)"
  echo "system resolvers:"; scutil --dns 2>/dev/null | sed -n 's/.*nameserver\[[0-9]*\] : /  /p' | sort -u
  echo "router default: $(route -n get default 2>/dev/null | sed -n 's/.*gateway: //p')"
  exit 0
fi
die() { echo "REFUSING: $*" >&2; exit 1; }
[ -n "$DNS" ] && [ -n "$NTP" ] || die "--dns and --ntp are both required"
valid() { # one IPv4, public
  echo "$1" | grep -Eq '^([0-9]{1,3}\.){3}[0-9]{1,3}$' || return 1
  for o in $(echo "$1" | tr '.' ' '); do [ "$o" -le 255 ] || return 1; done
  case "$1" in 0.*|10.*|127.*|169.254.*|192.168.*|192.0.0.*|198.18.*|198.19.*|22[4-9].*|23?.*|24?.*|25?.*) return 1 ;;
    172.1[6-9].*|172.2?.*|172.3[01].*|100.6[4-9].*|100.[7-9]?.*|100.1[01]?.*|100.12[0-7].*) return 1 ;; esac
  return 0
}
for kind in DNS NTP; do
  eval "list=\$$kind"
  for ip in $(echo "$list" | tr ',' ' '); do
    valid "$ip" || die "$kind address '$ip' is not a public IPv4 address (private, loopback, link-local, multicast and the 192.168.1.1 placeholder are refused)"
  done
done
SRC="$HERE/nftables.conf"
TXT="$(sed -e "s|^define UPSTREAM_DNS = .*|define UPSTREAM_DNS = { $(echo "$DNS" | sed 's/,/, /g') }|" -e "s|^define NTP_SERVERS = .*|define NTP_SERVERS = { $(echo "$NTP" | sed 's/,/, /g') }|" "$SRC")"
printf '%s\n' "$TXT" | grep -q "define UPSTREAM_DNS = { $(echo "$DNS" | sed 's/,/, /g') }" || die "could not substitute UPSTREAM_DNS"
if [ -n "$OUT" ]; then printf '%s\n' "$TXT" > "$OUT"; echo "wrote $OUT"; else printf '%s\n' "$TXT"; fi
