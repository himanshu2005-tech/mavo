param([switch]$SetKey)
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonPath = Join-Path $ProjectRoot "backend\.venv\Scripts\python.exe"
if (-not (Test-Path $PythonPath)) { throw "Run .\scripts\setup.ps1 first." }
if (-not (Test-Path (Join-Path $ProjectRoot "publisher\.env"))) {
    throw "Copy publisher\.env.example to publisher\.env and set the Firebase project and data directory."
}
Push-Location $ProjectRoot
try {
    if ($SetKey) {
        & $PythonPath -c "from getpass import getpass; from mavo.publisher_gateway import GatewaySettings; from mavo.credentials import DpapiCredentialStore; value=getpass('Publisher AIRouter key: '); assert value.strip(), 'Key cannot be empty'; DpapiCredentialStore(GatewaySettings().data_dir).set('publisher-airouter',value.strip()); print('Publisher key saved with Windows DPAPI.')"
    }
    else { & $PythonPath -m mavo.publisher_gateway }
    if ($LASTEXITCODE -ne 0) { throw "Publisher service command failed." }
}
finally { Pop-Location }
