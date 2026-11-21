#!/bin/sh
# Single-Mac (no VM) installation of the credential-side service. DRY RUN BY DEFAULT: prints what it would do and changes nothing.
#   sh infra/no-vm/install-no-vm.sh                 # plan only
#   sudo sh infra/no-vm/install-no-vm.sh --apply --python /path/to/python3.12   # needs root; creates users/dirs/files, installs the plist, does NOT load it
#   sudo sh infra/no-vm/install-no-vm.sh --apply --load   # also starts it (MOCK mode only; 'testnet' mode is refused by the service)
# Reversible with uninstall-no-vm.sh. Never prints a key. Never touches other users, firewall, SSH, Docker, or other projects.
set -eu
APPLY=0; LOAD=0; PY=""
while [ $# -gt 0 ]; do case "$1" in
  --apply) APPLY=1;; --load) LOAD=1;;
  --python) PY="${2-}"; shift;;   # a Python >= 3.12 used to create the service's private virtualenv
  *) echo "REFUSING: unknown option $1" >&2; exit 2;; esac; shift; done

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
CRED_USER=asta-cred; IPC_GROUP=asta-ipc
CODE=/opt/asta-cred; ETC=/etc/asta-cred; VAR=/var/asta-cred; RUN=/var/asta-cred-run; LOG=/var/log/asta-cred
PLIST=/Library/LaunchDaemons/com.asta.cred.plist

say() { printf '%s\n' "$*"; }
run() { if [ "$APPLY" = 1 ]; then say "+ $*"; "$@"; else say "[dry-run] $*"; fi; }

[ "$(uname -s)" = Darwin ] || { echo "REFUSING: macOS only" >&2; exit 1; }
if [ "$APPLY" = 1 ] && [ "$(id -u)" != 0 ]; then echo "REFUSING: --apply needs root (sudo); nothing was changed" >&2; exit 1; fi
say "== plan (APPLY=$APPLY LOAD=$LOAD)"
[ -d "$REPO/proposals/binance_testnet_signer" ] || { echo "REFUSING: run from the repository checkout" >&2; exit 1; }

free_id() { # first unused id in 300..399 for both users and groups (role-account range)
  i=300; while [ $i -lt 400 ]; do
    if ! dscl . -search /Users UniqueID $i 2>/dev/null | grep -q . && ! dscl . -search /Groups PrimaryGroupID $i 2>/dev/null | grep -q .; then echo $i; return; fi
    i=$((i+1)); done; echo "REFUSING: no free id" >&2; exit 1; }

if dscl . -read "/Groups/$IPC_GROUP" >/dev/null 2>&1; then say "group $IPC_GROUP exists"; else
  GID_NEW=$(free_id)
  run dscl . -create "/Groups/$IPC_GROUP"
  run dscl . -create "/Groups/$IPC_GROUP" PrimaryGroupID "$GID_NEW"
  run dscl . -create "/Groups/$IPC_GROUP" RealName "ASTA local IPC"
fi
if dscl . -read "/Users/$CRED_USER" >/dev/null 2>&1; then say "user $CRED_USER exists"; else
  UID_NEW=$(free_id)
  GID_IPC=$(dscl . -read "/Groups/$IPC_GROUP" PrimaryGroupID 2>/dev/null | awk '{print $2}'); GID_IPC=${GID_IPC:-$UID_NEW}
  run dscl . -create "/Users/$CRED_USER"
  run dscl . -create "/Users/$CRED_USER" UniqueID "$UID_NEW"
  run dscl . -create "/Users/$CRED_USER" PrimaryGroupID "$GID_IPC"
  run dscl . -create "/Users/$CRED_USER" UserShell /usr/bin/false
  run dscl . -create "/Users/$CRED_USER" NFSHomeDirectory "$VAR"
  run dscl . -create "/Users/$CRED_USER" RealName "ASTA credential service"
  run dscl . -create "/Users/$CRED_USER" IsHidden 1
  run dscl . -create "/Users/$CRED_USER" Password '*'
fi
say "(add YOUR app user to the group so it can reach the socket: dseditgroup -o edit -a <your-user> -t user $IPC_GROUP)"

run mkdir -p "$CODE/service" "$ETC" "$VAR" "$RUN" "$LOG"
# least privilege: ONLY the service files and the eight small asta modules it imports (tests/security/test_no_vm_install.py keeps this list honest)
for f in connector.py credential_service.py mock_exchange.py replay_store.py run_service.py signer.py; do
  run cp "$REPO/proposals/binance_testnet_signer/$f" "$CODE/service/$f"
done
for f in __init__.py core/__init__.py core/environment.py ipc/__init__.py ipc/unix_socket.py orders/__init__.py \
         orders/channel.py orders/extwire.py orders/lifecycle.py orders/ports.py paper/__init__.py paper/order_identity.py; do
  run mkdir -p "$CODE/asta/$(dirname "$f")"; run cp "$REPO/src/asta/$f" "$CODE/asta/$f"
done
if [ "$APPLY" = 1 ] && [ -z "$PY" ]; then echo "REFUSING: --apply needs --python /path/to/python3.12 (for the private virtualenv)" >&2; exit 1; fi
run "${PY:-python3.12}" -m venv "$CODE/venv"
run "$CODE/venv/bin/python" -m pip install --quiet "cryptography==50.0.2"
run chown -R root:wheel "$CODE"; run chmod -R go-w "$CODE"
run chown root:"$IPC_GROUP" "$ETC"; run chmod 0750 "$ETC"
run chown "$CRED_USER":"$IPC_GROUP" "$VAR"; run chmod 0700 "$VAR"   # state: replay/spend databases, only asta-cred
run chown "$CRED_USER":"$IPC_GROUP" "$RUN"; run chmod 0750 "$RUN"   # the socket directory: the app user (group asta-ipc) may traverse it
run chown "$CRED_USER":wheel "$LOG"; run chmod 0750 "$LOG"

if [ ! -e "$ETC/auth.key" ]; then
  if [ "$APPLY" = 1 ]; then ( umask 0227; openssl rand -hex 32 > "$ETC/auth.key" ); chown root:"$IPC_GROUP" "$ETC/auth.key"; chmod 0440 "$ETC/auth.key"; say "+ generated $ETC/auth.key (0440 root:$IPC_GROUP; value never printed)"
  else say "[dry-run] generate $ETC/auth.key (openssl rand -hex 32, 0440 root:$IPC_GROUP, never printed)"; fi
else say "$ETC/auth.key exists (kept)"; fi
if [ ! -e "$ETC/cred.json" ]; then
  APPUID=${SUDO_UID:-$(id -u)}
  if [ "$APPLY" = 1 ]; then
    cat > "$ETC/cred.json" <<JSON
{"mode": "mock", "socket": "$RUN/cred.sock", "auth_key_file": "$ETC/auth.key", "key_id": "k1",
 "replay_db": "$VAR/replay.db", "spend_db": "$VAR/spend.db", "enabled_file": "$VAR/enabled",
 "allowed_uids": [$APPUID], "senders": ["app"],
 "caps": {"symbols": ["BTC-USDT", "ETH-USDT"], "max_order_notional": "20", "max_day_notional": "200"}}
JSON
    chown root:"$IPC_GROUP" "$ETC/cred.json"; chmod 0440 "$ETC/cred.json"; say "+ wrote $ETC/cred.json (MOCK mode)"
  else say "[dry-run] write $ETC/cred.json (mode mock; allowed_uids=[$APPUID])"; fi
fi
run install -o root -g wheel -m 0644 "$REPO/infra/no-vm/com.asta.cred.plist" "$PLIST"
if [ "$LOAD" = 1 ]; then run launchctl bootstrap system "$PLIST"; else say "(not loaded; start later with: sudo launchctl bootstrap system $PLIST)"; fi
if [ "$APPLY" = 1 ]; then say "== done. Nothing else on this Mac was changed."; else say "== dry run: nothing was changed"; fi
