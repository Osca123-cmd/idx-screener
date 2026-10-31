# Register Windows Task Scheduler jobs for the IDX screener (run once, in PowerShell):
#   powershell -ExecutionPolicy Bypass -File scripts\register_task.ps1
# Remove them again with:
#   Unregister-ScheduledTask -TaskName "IDX Screener*" -Confirm:$false
#
# Times are local PC time; set them for WIB (the PC is assumed to be on Asia/Jakarta).

$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$logDir = Join-Path $root "logs"
New-Item -ItemType Directory -Force $logDir | Out-Null

function Register-Job($name, $jobArgs, $trigger) {
    $cmd = "/c cd /d `"$root`" && `"$python`" -m app.jobs $jobArgs >> `"$logDir\$($jobArgs.Split(' ')[0]).log`" 2>&1"
    $action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument $cmd -WorkingDirectory $root
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Hours 1)
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
    Write-Host "registered: $name"
}

# Daily scan after the IDX close (16:00 WIB) on trading weekdays.
Register-Job "IDX Screener - daily scan" "daily_scan" `
    (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At "16:30")

# Fundamentals + valuation snapshot every Saturday morning.
Register-Job "IDX Screener - weekly fundamentals" "weekly_fundamentals" `
    (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Saturday -At "08:00")
