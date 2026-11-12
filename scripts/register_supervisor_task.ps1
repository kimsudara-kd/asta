# OPTIONAL (not run automatically): keep the ASTA paper supervisor alive across log-offs/reboots.
# Registers a per-user Task Scheduler entry that starts the supervisor at log on and restarts it on
# failure. Remove with: Unregister-ScheduledTask -TaskName "ASTA paper supervisor" -Confirm:$false
# Requires the dev database container (asta-db) to be running; the supervisor's pre-resume gate refuses
# to start the service while any active session fails reconciliation.
$repo = "E:\Dev\asta"
$py = Join-Path $repo ".venv\Scripts\python.exe"
$env:ASTA_DATABASE_URL = "postgresql+pg8000://asta:asta_dev_only@127.0.0.1:5640/asta"
$action = New-ScheduledTaskAction -Execute $py -Argument "scripts\paper_supervisor.py" -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable
Register-ScheduledTask -TaskName "ASTA paper supervisor" -Action $action -Trigger $trigger -Settings $settings -Description "Supervises python -m asta.paper_service (virtual funds only)"
