#!/bin/sh
# ASTA egress gate. Runs as the owner of the network namespace shared with the proxy container.
# 1) default-deny first (nftables, inet = IPv4 + IPv6), 2) allow only TCP to the addresses that the
# allowlisted names resolve to (resolved HERE, public unicast only) on the allowlisted ports,
# 3) allowlist-only DNS answered from the same data. Fail closed: any error stops the container and
# the proxy (which shares this namespace) then has no network at all.
set -eu

PROFILE="${ASTA_EGRESS_PROFILE:-runtime}"
ALLOW=/etc/asta-egress/allowlist.json
: "${PROTECTED_SUBNET:?PROTECTED_SUBNET required}"

HOSTS="$(jq -r --arg p "$PROFILE" '.profiles[$p][].host' "$ALLOW")"
PORTS="$(jq -r --arg p "$PROFILE" '[.profiles[$p][].ports[]] | unique | map(tostring) | join(", ")' "$ALLOW")"
[ -n "$HOSTS" ] && [ -n "$PORTS" ] || { echo "empty allowlist profile $PROFILE" >&2; exit 1; }

# --- 1+2. rules: default DROP in every hook, then the exceptions --------------------------------
nft delete table inet asta 2>/dev/null || true  # only OUR table; Docker's own rules (embedded DNS) stay
nft -f - <<EOF
table inet asta {
  set allowed_dst {
    type ipv4_addr
    flags timeout
    timeout 10m
  }
  chain input {
    type filter hook input priority 0; policy drop;
    iif lo accept
    ct state established,related accept
    ip saddr $PROTECTED_SUBNET tcp dport 3128 accept
    limit rate 5/minute log prefix "ASTA-GATE-DROP-IN "
    counter drop
  }
  chain output {
    type filter hook output priority 0; policy drop;
    oif lo accept
    ct state established,related accept
    ip daddr @allowed_dst tcp dport { $PORTS } accept
    limit rate 5/minute log prefix "ASTA-GATE-DROP-OUT "
    counter drop
  }
  chain forward {
    type filter hook forward priority 0; policy drop;
    counter drop
  }
}
EOF

# --- 3. resolver + allowed-address refresh -------------------------------------------------------
public_v4() {  # only public unicast IPv4 may ever become an allowed destination
  case "$1" in
    0.*|10.*|127.*|169.254.*|192.168.*|192.0.0.*|198.18.*|198.19.*|22[4-9].*|23?.*|24?.*|25?.*) return 1 ;;
    172.1[6-9].*|172.2?.*|172.3[0-1].*) return 1 ;;
    100.6[4-9].*|100.[7-9]?.*|100.1[0-1]?.*|100.12[0-7].*) return 1 ;;
  esac
  case "$1" in *.*.*.*) return 0 ;; *) return 1 ;; esac
}

refresh() {
  : > /run/asta-hosts.new
  ELEMS=""
  for h in $HOSTS; do
    for ip in $(getent ahostsv4 "$h" 2>/dev/null | awk '{print $1}' | sort -u); do
      if public_v4 "$ip"; then
        echo "$ip $h" >> /run/asta-hosts.new
        ELEMS="$ELEMS${ELEMS:+, }$ip"
      else
        echo "gate: refusing non-public answer $ip for $h" >&2
      fi
    done
  done
  mv /run/asta-hosts.new /run/asta-hosts
  if [ -n "$ELEMS" ]; then nft add element inet asta allowed_dst "{ $ELEMS }" || true; fi
  [ -s /run/pid-dnsmasq ] && kill -HUP "$(cat /run/pid-dnsmasq)" 2>/dev/null || true
}

: > /run/asta-hosts
cat > /run/dnsmasq-asta.conf <<EOF
no-resolv
no-hosts
addn-hosts=/run/asta-hosts
listen-address=127.0.0.1
bind-interfaces
cache-size=0
log-queries
log-facility=-
user=root
pid-file=/run/pid-dnsmasq
EOF

refresh
dnsmasq --conf-file=/run/dnsmasq-asta.conf
( while sleep 60; do refresh; done ) &
echo "$!" > /run/pid-refresh

echo "ASTA gate: profile=$PROFILE ports=$PORTS protected=$PROTECTED_SUBNET"
echo "hosts: $(echo $HOSTS | tr '\n' ' ')"
echo "allowed addresses: $(cat /run/asta-hosts | tr '\n' ';')"
nft list ruleset | sed 's/^/rule: /'
wait "$(cat /run/pid-refresh)"
