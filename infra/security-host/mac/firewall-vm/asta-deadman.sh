#!/bin/sh
# Dead-man's-switch heartbeat from the FIREWALL VM (M3-B-R5; design: docs/m3b/SECURITY-EVENTS-AND-ALERTS.md s.2).
# Runs every minute from asta-deadman.timer as root on asta-fw. It pings an external heartbeat monitor ONLY while the
# firewall's own facts are healthy; if the host, the VM, the network or this job dies, the pings stop and the EXTERNAL
# service alerts the owner with no help from ASTA. Disabled until /etc/asta-egress/deadman.env defines DEADMAN_URL.
#   DEADMAN_URL  an https:// ping URL (a bearer secret: root-only file, never in git, never printed or logged)
# Checks: the running nftables ruleset (counters normalised) equals the pinned hash; the egress proxy is active;
# IP forwarding is off. A failed check sends the /fail signal instead of the heartbeat. The destination host must be on
# the egress allowlist (a signed two-reviewer change). No exchange credential is involved or readable here.
set -u
ENV=/etc/asta-egress/deadman.env
[ -r "$ENV" ] && . "$ENV"
[ -n "${DEADMAN_URL:-}" ] || { echo "deadman: not configured (no DEADMAN_URL): nothing sent"; exit 0; }
case "$DEADMAN_URL" in https://*) ;; *) echo "deadman: refusing a non-https URL" >&2; exit 1 ;; esac
PIN=/etc/asta-egress/ruleset.sha256
norm() { nft list ruleset | sed -E 's/counter packets [0-9]+ bytes [0-9]+/counter/' | sha256sum | cut -d' ' -f1; }
ok=1
[ -r "$PIN" ] && [ "$(norm)" = "$(cut -d' ' -f1 "$PIN")" ] || ok=0
systemctl is-active --quiet asta-egress-proxy.service || ok=0
[ "$(cat /proc/sys/net/ipv4/ip_forward 2>/dev/null)" = 0 ] || ok=0
# G-13: the APPLICATION must also be alive. asta-app writes a signed status stamp (asta.orders.alerting) over the
# one-way status feed; a missing, stale, forged or critical stamp turns the heartbeat into a /fail signal, and a dead
# app VM stops refreshing it. Key: /etc/asta-egress/status.key (root-only, ASCII, shared with the monitor only).
STAMP="${STATUS_FILE:-/var/lib/asta-egress/app-status}"
SKEY="${STATUS_KEY_FILE:-/etc/asta-egress/status.key}"
app_ok() {
  [ -r "$STAMP" ] && [ -r "$SKEY" ] || return 1
  line="$(head -c 400 "$STAMP")"
  ts="${line#ts=}"; ts="${ts%% *}"
  okf="${line#* ok=}"; okf="${okf%% *}"
  codes="${line#* codes=}"; codes="${codes%% mac=*}"
  mac="${line##* mac=}"
  case "$ts$okf" in *[!0-9]*|"") return 1 ;; esac
  want="$(printf 'ts=%s;ok=%s;codes=%s' "$ts" "$okf" "$codes" | openssl dgst -sha256 -hmac "$(tr -d '\n' < "$SKEY")" -r | cut -d' ' -f1)"
  [ "$want" = "$mac" ] || return 1
  now="$(date +%s)"
  [ $((now - ts)) -le 120 ] && [ $((ts - now)) -le 120 ] && [ "$okf" = 1 ]
}
app_ok || ok=0
if [ "$ok" = 1 ]; then URL="$DEADMAN_URL"; else URL="${DEADMAN_URL%/}/fail"; fi
curl --fail --silent --show-error --max-time 10 --retry 2 --output /dev/null "$URL" 2>/dev/null || { echo "deadman: ping failed (not printing the URL)" >&2; exit 2; }
[ "$ok" = 1 ] && echo "deadman: heartbeat sent" || { echo "deadman: FAIL signal sent (a local check failed)" >&2; exit 3; }
