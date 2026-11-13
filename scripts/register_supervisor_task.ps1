# Registers the dedicated per-user scheduled task "ASTA-Paper-Supervisor" (ASTA-specific only).
#  * runs as the CURRENT user, interactive logon, RunLevel Limited (no elevation, no stored password)
#  * starts at log on, then re-launches every 2 minutes if not running (keep-alive); never auto-stops
#  * MultipleInstances = IgnoreNew + the supervisor's own lock file => exactly one supervisor
#  * does not touch power plans, sleep settings, security policy or any other task/service
# Roll back with scripts\unregister_supervisor_task.ps1
$ErrorActionPreference = "Stop"
$name = "ASTA-Paper-Supervisor"
$repo = "E:\Dev\asta"
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$repo\scripts\run_paper_supervisor.cmd`"" -WorkingDirectory $repo
$logon = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
# Keep-alive: Task Scheduler's restart-on-failure does NOT fire when the process is force-killed
# (verified), so a 2-minute repeating trigger relaunches it; IgnoreNew + the supervisor's lock file
# guarantee that a new launch while one is running is a no-op.
$repeat = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 2)
$trigger = @($logon, $repeat)
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
  -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable -Priority 4 -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
  -Description "ASTA paper-trading supervisor (virtual funds only). Rollback: scripts\unregister_supervisor_task.ps1" | Out-Null
Get-ScheduledTask -TaskName $name | Select-Object TaskName, State | Format-Table -AutoSize
