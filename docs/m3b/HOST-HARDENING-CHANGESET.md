# Mac host hardening — reviewed change set (M3-B-FINAL phase 3)

**Status: PREPARED, NOT APPLIED** (every privileged step is reserved action 3/4). Actual current state is measured by
`python3 scripts/host_posture.py` (unprivileged, read-only); latest output: `docs/evidence/m3b-final/host-posture-2026-11-14.json`.
Measured gaps: FileVault off · application firewall off · auto-login on · SSH (22) and SMB (445) listening · P2-db/P3 and an unrelated desktop app (`exo`) listening on all interfaces · Docker Desktop with unrelated workloads.

Each step: command, affected object, expected effect, rollback. Order matters: **recovery access first** (step 0), disable the old access last.
| # | Command (operator, console or an existing admin session) | Affects | Expected effect | Rollback |
|---|---|---|---|---|
| 0 | Create a second admin account `asta-admin` (System Settings → Users) with a unique passphrase stored offline; confirm it can log in at the console and with `sudo -v` | users | recovery path independent of the daily account | delete the account |
| 1 | `sudo fdesetup enable -user asta-admin` → store the **personal recovery key** printed once in the offline safe (separate from the backup-key ceremony); do not email/photo it | disk | FileVault on; after power loss the Mac waits at the password prompt (documented in `HOST-HARDENING-RUNBOOK.md` §startup; recovery with the stored key) | `sudo fdesetup disable` (needs admin password) |
| 2 | `sudo defaults delete /Library/Preferences/com.apple.loginwindow autoLoginUser` | loginwindow plist | no auto-login | `sudo defaults write ... autoLoginUser <name>` (not recommended once FileVault is on; macOS disables it) |
| 3 | `sudo /usr/libexec/ApplicationFirewall/socketfilterfw --setglobalstate on --setstealthmode on --setblockall off` then `--add`/`--unblockapp` only for the VM runtime | firewall | inbound blocked except listed apps; stealth | `--setglobalstate off` |
| 4 | SMB off: `sudo launchctl disable system/com.apple.smbd` and System Settings → General → Sharing → File Sharing off | smbd | port 445 closed | re-enable the toggle |
| 5 | SSH: keep Remote Login only if needed. `/etc/ssh/sshd_config.d/100-asta.conf`: `PasswordAuthentication no`, `KbdInteractiveAuthentication no`, `PermitRootLogin no`, `AllowUsers asta-admin`, `AllowGroups` none; restrict sources with the router/pf rule to the single management address. Test from a second terminal **before** closing the first. | sshd | key-only, one user | remove the drop-in; `sudo launchctl kickstart -k system/com.openssh.sshd` |
| 6 | Install the operator's public key into `~asta-admin/.ssh/authorized_keys` (0600, dir 0700) **before** step 5 | file | access preserved | — |
| 7 | Unrelated listeners: quit the desktop app `exo` or bind it to loopback; P2-db/P3 → only after the Docker package (`DOCKER-STORAGE-RECOVERY-PACKAGE.md`) or owner relocation | processes | `host_posture.py --require-dedicated` exits 0 | restart the app |
| 8 | `sudo pmset -a autorestart 1 sleep 0 disksleep 0` (already autorestart=1) | power | unattended recovery after power loss (then FileVault prompt, see 1) | `pmset -a autorestart 0` |
| 9 | Logs: `sudo chmod 640 /var/log/asta*`; ship the audit chain head to the off-host store (alerts package) | logs | preserved, protected | — |
| 10 | Verify: `python3 scripts/host_posture.py --require-dedicated; echo $?` → 0; `nc -z <lan-ip> 22 445 3000 5442` refused except the approved management port | all | evidence for the gate matrix | — |
**Do not disable the current remote access until steps 0 and 6 are verified.** A host reboot test (reserved, needs a maintenance window) follows step 10.
