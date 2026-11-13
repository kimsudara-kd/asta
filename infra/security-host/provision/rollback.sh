#!/bin/sh
# Restores the files saved by the last deploy.sh run and reloads the firewall. DESIGN ARTIFACT (M3A-R3).
#   ./rollback.sh            # DRY_RUN=1 (default) prints only;   DRY_RUN=0 ./rollback.sh   to execute
# FAIL-CLOSED NOTE: rolling back never leaves the host without a firewall: if no previous nftables.conf
# existed, a default-drop ruleset is installed instead of an empty one. A VM snapshot ("clean-before-install")
# is the second rollback path (hypervisor side).
set -eu
DRY_RUN="${DRY_RUN:-1}"
run() { echo "> $*"; [ "$DRY_RUN" = "0" ] && "$@" || true; }
TS="$(cat /var/backups/asta-last 2>/dev/null || true)"; BK="/var/backups/asta-$TS"
[ -n "$TS" ] && [ -d "$BK" ] || { echo "no backup recorded; use the hypervisor snapshot" >&2; exit 1; }
find "$BK" -type f | while read -r f; do
  dest="${f#"$BK"}"
  run install -D -m 0644 "$f" "$dest"
done
if [ ! -e "$BK/etc/nftables.conf" ]; then
  run sh -c 'printf "flush ruleset\ntable inet asta_fallback {\n chain input { type filter hook input priority 0; policy drop; iif lo accept; ct state established,related accept; }\n chain forward { type filter hook forward priority 0; policy drop; }\n chain output { type filter hook output priority 0; policy drop; oif lo accept; ct state established,related accept; }\n}\n" > /etc/nftables.conf'
fi
run nft -c -f /etc/nftables.conf
run systemctl daemon-reload
run systemctl restart nftables.service
run systemctl restart asta-egress-proxy.service || true
echo "Rolled back from $BK (DRY_RUN=$DRY_RUN)."
