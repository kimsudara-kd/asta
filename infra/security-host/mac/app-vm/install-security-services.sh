#!/bin/sh
# Install the risk-halt bridge, the security monitor and the least-privilege database roles on asta-app (M3-B-R4).
# Runs INSIDE the app VM as root, from a checkout of this directory on the read-only payload disk.
# DRY RUN BY DEFAULT: prints what it would do. Nothing here enables an order path, creates a credential, or
# touches the network. Prerequisites: PostgreSQL running with the ASTA database migrated to head (0011).
#     sudo sh install-security-services.sh --apply
set -eu
APPLY=0
for a in "$@"; do case "$a" in --apply) APPLY=1 ;; -h|--help) sed -n 2,8p "$0"; exit 0 ;; *) echo "unknown option $a" >&2; exit 2 ;; esac; done
HERE="$(cd "$(dirname "$0")" && pwd)"
say() { printf '%s\n' "$*"; }
run() { if [ "$APPLY" = 1 ]; then say "+ $*"; "$@"; else say "[dry-run] $*"; fi; }
die() { say "REFUSING: $*" >&2; exit 1; }
[ "$(uname -s)" = Linux ] || die "this installs into the Linux app VM, not the Mac host"
if [ "$APPLY" = 1 ] && [ "$(id -u)" != 0 ]; then die "--apply needs root"; fi
for f in pg_roles_m3b.sql pg_hba.conf.asta pg_ident.conf.asta asta-risk-bridge.service asta-security-monitor.service asta-security-monitor.timer 'asta-security-alert@.service'; do
  [ -f "$HERE/$f" ] || die "missing $f next to this script"
done

say "== 1 service accounts (no login shell, no home, no password)"
for u in asta-bridge asta-monitor; do
  if id "$u" >/dev/null 2>&1; then say "exists: $u"; else run useradd --system --no-create-home --shell /usr/sbin/nologin "$u"; fi
done
say "(asta-orders is created only when the order path is authorised; asta-release is an interactive operator login, never a service)"

say; say "== 2 state directories and environment files (database URL over the unix socket, NO password)"
run install -d -o asta-bridge -g asta-bridge -m 0700 /var/lib/asta-bridge
run install -d -o asta-monitor -g asta-monitor -m 0700 /var/lib/asta-monitor
run install -d -o root -g root -m 0755 /etc/asta
if [ "$APPLY" = 1 ]; then
  printf 'ASTA_DATABASE_URL=postgresql+pg8000://asta_bridge@/asta?unix_sock=/var/run/postgresql/.s.PGSQL.5432\n' > /etc/asta/asta-bridge.env
  printf 'ASTA_DATABASE_URL=postgresql+pg8000://asta_monitor@/asta?unix_sock=/var/run/postgresql/.s.PGSQL.5432\n' > /etc/asta/asta-monitor.env
  chown root:asta-bridge /etc/asta/asta-bridge.env; chown root:asta-monitor /etc/asta/asta-monitor.env
  chmod 0640 /etc/asta/asta-bridge.env /etc/asta/asta-monitor.env
else
  say "[dry-run] write /etc/asta/asta-bridge.env and asta-monitor.env (root:<service> 0640, unix-socket URL, no password)"
fi

say; say "== 3 database roles and peer authentication"
run sudo -u postgres psql -v ON_ERROR_STOP=1 -d asta -f "$HERE/pg_roles_m3b.sql"
say "append the role lines of pg_hba.conf.asta / pg_ident.conf.asta to /etc/postgresql/16/main/ (review the diff first), then: systemctl reload postgresql"
say "one LOGIN role per human releaser, e.g.: CREATE ROLE rel_<name> LOGIN IN ROLE asta_release;  INSERT INTO kill_switch_releasers(name) VALUES ('rel_<name>');  (a reviewed, audited change)"
say "the service roles (asta_bridge, asta_monitor) need LOGIN for peer authentication: ALTER ROLE asta_bridge LOGIN; ALTER ROLE asta_monitor LOGIN;"

say; say "== 4 units"
for u in asta-risk-bridge.service asta-security-monitor.service asta-security-monitor.timer 'asta-security-alert@.service'; do
  run install -o root -g root -m 0644 "$HERE/$u" "/etc/systemd/system/$u"
done
run systemctl daemon-reload
run systemctl enable --now asta-risk-bridge.service asta-security-monitor.timer

say; say "== 5 verify (run after --apply; the order path stays CLOSED until the heartbeat is fresh)"
say "systemctl is-active asta-risk-bridge asta-security-monitor.timer"
say "sudo -u asta-monitor /opt/asta/venv/bin/python -m asta.orders.monitor    # exit 0 = healthy, 1 = latched, 2 = critical"
say "kill the bridge (systemctl kill -s KILL asta-risk-bridge): within 30 s the monitor must report BRIDGE_HEARTBEAT_STALE (exit 2) and an audit event must appear; systemd restarts the bridge within ~2 s"
[ "$APPLY" = 1 ] || say "(dry-run: nothing was changed)"
