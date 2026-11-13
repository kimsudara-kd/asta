#!/bin/sh
# Applies the ASTA security-host configuration ON a dedicated VM (alternative to cloud-init).
# DESIGN ARTIFACT (M3A-R3); run only on the dedicated firewall/runtime VM, as root, after a snapshot.
#   ./deploy.sh firewall|runtime <repo-checkout-dir>           # DRY_RUN=1 (default) prints only
#   DRY_RUN=0 ./deploy.sh firewall /opt/asta-config
# Every file that is replaced is first copied to /var/backups/asta-<timestamp>; rollback.sh restores it.
set -eu
ROLE="${1:?firewall|runtime}"; SRC="${2:?repo checkout}"; DRY_RUN="${DRY_RUN:-1}"
SH="$SRC/infra/security-host"; EG="$SRC/infra/egress"
TS="$(date -u +%Y%m%dT%H%M%SZ)"; BK="/var/backups/asta-$TS"
[ "$(id -u)" = "0" ] || [ "$DRY_RUN" = "1" ] || { echo "run as root" >&2; exit 1; }
run() { echo "> $*"; [ "$DRY_RUN" = "0" ] && "$@" || true; }

install_file() {  # src dest mode
  [ -e "$2" ] && run install -D -m 0600 "$2" "$BK$2"
  run install -D -m "$3" "$1" "$2"
}

run mkdir -p "$BK"
echo "$TS" > /tmp/asta-last-backup.$$ ; [ "$DRY_RUN" = "0" ] && cp /tmp/asta-last-backup.$$ /var/backups/asta-last || true
case "$ROLE" in
  firewall)
    install_file "$SH/firewall-vm/nftables.conf" /etc/nftables.conf 0644
    install_file "$SH/firewall-vm/asta-allowlist-refresh.sh" /usr/local/sbin/asta-allowlist-refresh.sh 0750
    for u in asta-allowlist-refresh.service asta-allowlist-refresh.timer asta-egress-proxy.service; do
      install_file "$SH/firewall-vm/$u" "/etc/systemd/system/$u" 0644
    done
    install_file "$SH/firewall-vm/netplan-50-asta.yaml" /etc/netplan/50-asta.yaml 0600
    install_file "$EG/proxy.py" /opt/asta-egress/proxy.py 0644
    install_file "$EG/allowlist.json" /etc/asta-egress/allowlist.json 0644
    [ -e /etc/asta-egress/empty-resolv.conf ] || run sh -c 'echo "# intentionally empty" > /etc/asta-egress/empty-resolv.conf'
    run nft -c -f /etc/nftables.conf                       # syntax/semantics check BEFORE loading
    run systemctl daemon-reload
    run systemctl enable --now nftables.service
    run systemctl enable --now asta-allowlist-refresh.timer asta-egress-proxy.service
    ;;
  runtime)
    install_file "$SH/runtime-vm/nftables.conf" /etc/nftables.conf 0644
    install_file "$SH/runtime-vm/netplan-50-asta.yaml" /etc/netplan/50-asta.yaml 0600
    run nft -c -f /etc/nftables.conf
    run systemctl enable --now nftables.service
    echo "NOTE: asta-vault / asta-connector units are NOT installed or enabled (M3-B, not authorized)."
    ;;
  *) echo "unknown role $ROLE" >&2; exit 2 ;;
esac
run netplan generate
echo "Applied (DRY_RUN=$DRY_RUN). Backup: $BK. Next: acceptance/vm_acceptance.py $ROLE pre, reboot, then post."
