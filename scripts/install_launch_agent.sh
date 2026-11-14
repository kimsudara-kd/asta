#!/bin/sh
# Start the local ASTA stack (scripts/dev_stack.sh start) automatically at login via a per-USER launchd agent.
# No sudo, nothing system-wide. The plist is generated here with this machine's paths (it is not committed).
#   sh scripts/install_launch_agent.sh install | uninstall | status
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.asta.dev-stack"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"
LOG="$ROOT/infra/data/run/launchd.log"
case "${1:-status}" in
  install)
    mkdir -p "$HOME/Library/LaunchAgents" "$ROOT/infra/data/run"
    cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>/bin/sh</string><string>$ROOT/scripts/dev_stack.sh</string><string>start</string></array>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string></dict>
  <key>RunAtLoad</key><true/>
  <!-- dev_stack.sh start spawns background servers and exits: do not kill them when the job ends -->
  <key>AbandonProcessGroup</key><true/>
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
EOF
    plutil -lint "$PLIST" >/dev/null
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    launchctl bootstrap "$DOMAIN" "$PLIST"
    echo "installed: $PLIST (runs at every login; start now: launchctl kickstart $DOMAIN/$LABEL)"
    ;;
  uninstall)
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    echo "removed $LABEL (a running stack is left alone: sh scripts/dev_stack.sh stop)"
    ;;
  status)
    if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then echo "launch agent: loaded ($PLIST)"; else echo "launch agent: not loaded"; fi
    ;;
  *) echo "usage: $0 install|uninstall|status" >&2; exit 2 ;;
esac
