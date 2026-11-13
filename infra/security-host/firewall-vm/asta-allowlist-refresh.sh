#!/bin/sh
# Refreshes nft set asta_fw/allowed_dst AND the hosts file used by the proxy from the allowlisted
# NAMES (infra/egress/allowlist.json, profile "runtime"). Only public unicast IPv4 may become an
# allowed destination. Run by asta-allowlist-refresh.timer every 60 s. Logs every change; never
# touches any other rule. The proxy resolves ONLY from the hosts file it writes (no DNS egress).
set -eu
ALLOW="${ASTA_ALLOWLIST:-/etc/asta-egress/allowlist.json}"
PROFILE="${ASTA_EGRESS_PROFILE:-runtime}"
HOSTS_NEW=/etc/asta-egress/hosts.new

public_v4() {
  case "$1" in
    0.*|10.*|127.*|169.254.*|192.168.*|192.0.0.*|198.18.*|198.19.*|22[4-9].*|23?.*|24?.*|25?.*) return 1 ;;
    172.1[6-9].*|172.2?.*|172.3[0-1].*) return 1 ;;
    100.6[4-9].*|100.[7-9]?.*|100.1[0-1]?.*|100.12[0-7].*) return 1 ;;
  esac
  case "$1" in *.*.*.*) return 0 ;; *) return 1 ;; esac
}

ELEMS=""
echo "127.0.0.1 localhost" > "$HOSTS_NEW"
for h in $(jq -r --arg p "$PROFILE" '.profiles[$p][].host' "$ALLOW"); do
  for ip in $(getent ahostsv4 "$h" | awk '{print $1}' | sort -u); do
    if public_v4 "$ip"; then
      ELEMS="$ELEMS${ELEMS:+, }$ip"
      echo "$ip $h" >> "$HOSTS_NEW"
    else
      logger -t asta-allowlist "refused non-public answer $ip for $h"
    fi
  done
done
mv "$HOSTS_NEW" /etc/asta-egress/hosts
if [ -n "$ELEMS" ]; then
  nft add element inet asta_fw allowed_dst "{ $ELEMS }"
  logger -t asta-allowlist "refreshed allowed_dst: $ELEMS"
fi
