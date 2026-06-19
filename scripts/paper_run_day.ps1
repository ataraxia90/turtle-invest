$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot
$env:PYTHONPATH = "src"
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [Text.Encoding]::UTF8

$LogDir = Join-Path $ProjectRoot "data\logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$LogPath = Join-Path $LogDir "paper_run_day_$Stamp.log"
$ExitCode = 0

try {
    python -m turtle_invest paper-run-day --after-open-only --once-per-day --send-report --config config.local.json 2>&1 |
        Tee-Object -FilePath $LogPath
    $ExitCode = $LASTEXITCODE
    if ($ExitCode -ne 0) {
        throw "paper-run-day failed with exit code $ExitCode"
    }
}
catch {
    Write-Error $_
    if ($ExitCode -eq 0) {
        $ExitCode = 1
    }
}
finally {
    "exit_code=$ExitCode" | Tee-Object -FilePath $LogPath -Append
}

exit $ExitCode
