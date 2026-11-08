$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$BackendRoot = Join-Path $ProjectRoot "backend"
$PythonPath = Join-Path $BackendRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $PythonPath)) {
    py -3.12 -m venv (Join-Path $BackendRoot ".venv")
    if ($LASTEXITCODE -ne 0) { throw "Python 3.12 environment creation failed." }
}

& $PythonPath -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed." }
& $PythonPath -m pip install -e "$BackendRoot[dev]"
if ($LASTEXITCODE -ne 0) { throw "Backend dependency installation failed." }
Push-Location $ProjectRoot
try {
    if (Test-Path (Join-Path $ProjectRoot "package-lock.json")) {
        npm ci
    }
    else {
        npm install
    }
    if ($LASTEXITCODE -ne 0) { throw "Frontend dependency installation failed." }
}
finally {
    Pop-Location
}

Write-Host "Mavo development dependencies are ready." -ForegroundColor Green
