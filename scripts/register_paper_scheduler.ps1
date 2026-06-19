param(
    [string]$TaskName = "TurtleInvest-PaperRunDay",
    [string[]]$At = @("22:35", "23:35")
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$RunScript = Join-Path $PSScriptRoot "paper_run_day.ps1"

if (-not (Test-Path $RunScript)) {
    throw "Run script not found: $RunScript"
}

$RunTimes = @(
    foreach ($TimeText in $At) {
        [datetime]::ParseExact($TimeText, "HH:mm", [Globalization.CultureInfo]::InvariantCulture)
    }
)
$UserId = "$env:USERDOMAIN\$env:USERNAME"

$Action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$RunScript`"" `
    -WorkingDirectory $ProjectRoot

$Triggers = @(
    foreach ($RunAt in $RunTimes) {
        New-ScheduledTaskTrigger `
            -Weekly `
            -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday `
            -At $RunAt
    }
)
$Triggers += New-ScheduledTaskTrigger -AtLogOn -User $UserId

$Settings = New-ScheduledTaskSettingsSet `
    -WakeToRun `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

$Principal = New-ScheduledTaskPrincipal `
    -UserId $UserId `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Triggers `
    -Settings $Settings `
    -Principal $Principal `
    -Description "Run turtle-invest paper trading after the US market opens. Wakes the PC from sleep." `
    -Force | Out-Null

Write-Output "registered=$TaskName"
Write-Output "times=$($At -join ',')"
Write-Output "logon_catchup=true"
Write-Output "wake_to_run=true"
Write-Output "script=$RunScript"
