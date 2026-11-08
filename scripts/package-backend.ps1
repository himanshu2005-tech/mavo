param([switch]$Release)
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$BackendRoot = Join-Path $ProjectRoot "backend"
$PythonPath = Join-Path $BackendRoot ".venv\Scripts\python.exe"
$OutputRoot = Join-Path $ProjectRoot "src-tauri\binaries"
$BuildRoot = Join-Path $ProjectRoot ".build\pyinstaller"
$env:PYINSTALLER_CONFIG_DIR = Join-Path $BuildRoot "cache"

if (-not (Test-Path $PythonPath)) {
    throw "Run .\scripts\setup.ps1 first."
}

New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null
New-Item -ItemType Directory -Force -Path $BuildRoot | Out-Null

# The publisher's desktop client ID is public application identity, not a user credential.
$PublisherBundle = Join-Path $BuildRoot "publisher.json"
if ($Release) {
    & $PythonPath -m mavo.publisher --output $PublisherBundle --release
}
else {
    & $PythonPath -m mavo.publisher --output $PublisherBundle
}
if ($LASTEXITCODE -ne 0) { throw "Could not load publisher Google configuration." }
$PublisherId = (Get-Content -LiteralPath $PublisherBundle -Raw | ConvertFrom-Json).google_client_id
if (-not $PublisherId) { Write-Warning "Google Connect will be unavailable: no publisher OAuth client ID configured." }

& $PythonPath -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --name "mavo-backend" `
    --paths (Join-Path $BackendRoot "src") `
    --add-data "$BackendRoot\migrations;migrations" `
    --add-data "$BackendRoot\alembic.ini;." `
    --add-data "$PublisherBundle;." `
    --distpath $OutputRoot `
    --workpath (Join-Path $BuildRoot "work") `
    --specpath $BuildRoot `
    (Join-Path $BackendRoot "run_backend.py")
if ($LASTEXITCODE -ne 0) { throw "Backend packaging failed." }

Write-Host "Packaged backend: $OutputRoot\mavo-backend\mavo-backend.exe" -ForegroundColor Green
