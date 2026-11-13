# Rollback for register_supervisor_task.ps1: stops and removes ONLY the ASTA-Paper-Supervisor task,
# then stops ASTA's own supervisor/service processes (command-line match on ASTA scripts only).
$name = "ASTA-Paper-Supervisor"
if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
  Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
  Unregister-ScheduledTask -TaskName $name -Confirm:$false
}
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'paper_supervisor|asta\.paper_service' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Remove-Item "E:\Dev\asta\data\paper\supervisor.lock" -ErrorAction SilentlyContinue
"ASTA-Paper-Supervisor removed; sessions and data are untouched (they resume when a supervisor/service is started again)."
