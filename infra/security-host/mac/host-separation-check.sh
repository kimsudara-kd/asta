#!/bin/sh
# READ-ONLY check that the Mac is an ASTA-only host (M3-B-R4 workstream A, post-migration acceptance).
#
#     sh host-separation-check.sh [report.json]        exit 0 only if every check passes
#
# Changes nothing, stops nothing, needs no sudo (listeners owned by root are visible through connection tests
# against the machine's own addresses). Each check prints PASS/FAIL/INFO. Expected today: FAIL (the host is
# shared); expected after the owner-approved migration: PASS. Policy, owner-adjustable at the top.
set -u
ALLOWED_PORTS="${ASTA_ALLOWED_PORTS:-4640 3640 5640}"          # ASTA dev stack (loopback only is checked separately)
ALLOWED_SERVICES="${ASTA_ALLOWED_LAUNCHD:-com.asta. com.apple. com.docker.helper homebrew.mxcl.tailscale}"   # prefixes
REPORT="${1:-/dev/stdout}"
FAILS=0; OUT=""
rec() { # name result detail
  printf '%-5s %s %s\n' "$2" "$1" "$3"
  OUT="$OUT{\"check\":\"$1\",\"result\":\"$2\",\"detail\":\"$(printf '%s' "$3" | tr -d '"\\' | cut -c1-160)\"},"
  if [ "$2" = FAIL ]; then FAILS=$((FAILS+1)); fi
  return 0
}

# 1 containers: none that are not ASTA's own
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  RUN="$(docker ps --format '{{.Names}}' | grep -v '^asta-' | tr '\n' ' ')"
  ALL="$(docker ps -a --format '{{.Names}}' | grep -v '^asta-' | tr '\n' ' ')"
  [ -z "$RUN" ] && rec no_unrelated_running_containers PASS "" || rec no_unrelated_running_containers FAIL "running: $RUN"
  [ -z "$ALL" ] && rec no_unrelated_stopped_containers PASS "" || rec no_unrelated_stopped_containers FAIL "present: $ALL"
  VOLS="$(docker volume ls -q | grep -v '^asta' | wc -l | tr -d ' ')"
  [ "$VOLS" = 0 ] && rec no_unrelated_docker_volumes PASS "" || rec no_unrelated_docker_volumes FAIL "$VOLS volumes"
else
  rec docker_daemon_not_running PASS "Docker is not running (the desired end state: ASTA does not depend on it)"
fi

# 2 listeners: reachable on the LAN address only if allowed; the ASTA stack must be loopback-only
IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)"
if [ -n "$IP" ]; then
  EXPOSED=""
  for p in 21 22 23 80 88 139 443 445 548 631 3000 3283 3389 5000 5432 5442 5900 7000 8080 8443 49366 52415; do
    nc -z -G 1 -w1 "$IP" "$p" 2>/dev/null && EXPOSED="$EXPOSED $p"
  done
  [ -z "$EXPOSED" ] && rec no_lan_reachable_listeners PASS "none of the common service ports answer on $IP" || rec no_lan_reachable_listeners FAIL "answering on $IP:$EXPOSED"
else
  rec no_lan_reachable_listeners INFO "no LAN address found"
fi
for p in $ALLOWED_PORTS; do
  if [ -n "$IP" ] && nc -z -G 1 -w1 "$IP" "$p" 2>/dev/null; then rec "asta_port_$p" FAIL "answers on the LAN address (must be loopback only)"; fi
done

# 3 unrelated software that listens or runs unattended
pgrep -fl "EXO.app|exo/exo" >/dev/null 2>&1 && rec no_exo FAIL "EXO is running (listens on all interfaces)" || rec no_exo PASS ""
OTHER="$(launchctl list 2>/dev/null | awk 'NR>1{print $3}' | grep -v -E "^($(echo $ALLOWED_SERVICES | sed 's/ /|/g; s/\./\\./g'))" | grep -v -E '^(application|0x|-$)' | head -20 | tr '\n' ' ')"
[ -z "$OTHER" ] && rec no_unrelated_launchd_jobs PASS "" || rec no_unrelated_launchd_jobs INFO "review: $OTHER"

# 4 sharing and remote services (owner decides which are required; the default expectation is none)
for spec in "22:Remote Login (ssh)" "445:File Sharing (SMB)" "5900:Screen Sharing" "548:AFP"; do
  port="${spec%%:*}"; name="${spec#*:}"
  nc -z -G 1 -w1 127.0.0.1 "$port" 2>/dev/null && rec "service_off_$port" FAIL "$name is on" || rec "service_off_$port" PASS "$name is off"
done

# 5 host posture
fdesetup status 2>/dev/null | grep -q "FileVault is On" && rec filevault_on PASS "" || rec filevault_on FAIL "FileVault is not on"
/usr/libexec/ApplicationFirewall/socketfilterfw --getglobalstate 2>/dev/null | grep -Eq "State = [12]\)" && rec app_firewall_on PASS "" || rec app_firewall_on FAIL "application firewall is off"
[ "$(defaults read /Library/Preferences/com.apple.loginwindow autoLoginUser 2>/dev/null || true)" = "" ] && rec no_auto_login PASS "" || rec no_auto_login FAIL "automatic login is set"
[ "$(dscl . -read /Groups/admin GroupMembership 2>/dev/null | wc -w)" -le 4 ] && rec admin_group_small PASS "" || rec admin_group_small FAIL "more than the expected admins"

printf '{"host_separation":[%s],"failed":%s}\n' "${OUT%,}" "$FAILS" > "$REPORT"
[ "$FAILS" = 0 ]
