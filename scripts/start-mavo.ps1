# Starts Mavo the way a development copy needs it: the assistant service, then the app.
# Windows runs this at sign-in when "Start with Windows" is on in Settings. Safe to run again:
# anything already running is left alone.
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot "backend\.venv\Scripts\python.exe"

function Test-Listening($Port) {
    [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

# 1. The assistant service (chat and Crew planning) listens on 8741.
if ((Test-Path $Python) -and (Test-Path (Join-Path $ProjectRoot "publisher\.env")) -and -not (Test-Listening 8741)) {
    Start-Process -WindowStyle Hidden -FilePath $Python -ArgumentList "-m", "mavo.publisher_gateway" -WorkingDirectory $ProjectRoot
}

# 2. The app itself. dev.ps1 refuses to start a second copy, so a running one is simply kept.
$Running = Get-Process -Name mavo -ErrorAction SilentlyContinue
if (-not $Running) {
    & (Join-Path $PSScriptRoot "dev.ps1")
}
