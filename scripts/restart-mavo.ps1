# Restarts everything a development copy of Mavo needs, so a backend change takes effect:
# the assistant service (chat and Crew planning, port 8741), then the app.
# Run it after changing backend code, or when the chat says "cannot reach its assistant service".
# Active work is interrupted; saved data is kept.
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot "backend\.venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { throw "Run .\scripts\setup.ps1 first." }

# 1. Stop the assistant service if one is running, so the new code is what answers.
$Listener = Get-NetTCPConnection -LocalPort 8741 -State Listen -ErrorAction SilentlyContinue
if ($Listener) {
    Write-Host "Stopping the assistant service (PID $($Listener.OwningProcess))."
    Stop-Process -Id $Listener.OwningProcess -Force
    Start-Sleep -Seconds 2
}

# 2. Start it again, hidden, with the current code.
if (Test-Path (Join-Path $ProjectRoot "publisher\.env")) {
    Start-Process -WindowStyle Hidden -FilePath $Python -ArgumentList "-m", "mavo.publisher_gateway" -WorkingDirectory $ProjectRoot
    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Milliseconds 750
        try {
            if ((Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 "http://127.0.0.1:8741/health").StatusCode -eq 200) { Write-Host "Assistant service is up."; break }
        } catch { }
    }
} else {
    Write-Host "publisher\.env is missing: the assistant service was not started."
}

# 3. Restart the app itself (stops the running development copy, rebuilds, opens it).
& (Join-Path $PSScriptRoot "dev.ps1") -Restart
