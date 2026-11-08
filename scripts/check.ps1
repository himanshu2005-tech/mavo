$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonPath = Join-Path $ProjectRoot "backend\.venv\Scripts\python.exe"
$UserCargoHome = [Environment]::GetEnvironmentVariable("CARGO_HOME", "User")
$CargoHomeCandidates = @($env:CARGO_HOME, $UserCargoHome, "D:\DevTools\Rust\cargo", (Join-Path $env:USERPROFILE ".cargo")) |
    Where-Object { $_ } |
    Select-Object -Unique
$CargoBin = $CargoHomeCandidates |
    ForEach-Object { Join-Path $_ "bin" } |
    Where-Object { Test-Path (Join-Path $_ "cargo.exe") } |
    Select-Object -First 1

if (-not (Get-Command cargo -ErrorAction SilentlyContinue) -and $CargoBin) {
    $env:PATH = "$CargoBin$([IO.Path]::PathSeparator)$env:PATH"
}
if (Get-Command cargo -ErrorAction SilentlyContinue) {
    if ($CargoBin) {
        $env:CARGO_HOME = Split-Path $CargoBin -Parent
    }
    $UserRustupHome = [Environment]::GetEnvironmentVariable("RUSTUP_HOME", "User")
    if ($UserRustupHome -and (Test-Path $UserRustupHome)) {
        $env:RUSTUP_HOME = $UserRustupHome
    }
    & (Join-Path $PSScriptRoot "enter-msvc.ps1")
}

Push-Location $ProjectRoot
try {
    & $PythonPath -m ruff check backend
    if ($LASTEXITCODE -ne 0) { throw "Backend lint failed." }
    & $PythonPath -m pytest backend
    if ($LASTEXITCODE -ne 0) { throw "Backend tests failed." }
    npm run check
    if ($LASTEXITCODE -ne 0) { throw "Frontend checks failed." }
    if (Get-Command cargo -ErrorAction SilentlyContinue) {
        cargo check --manifest-path src-tauri\Cargo.toml
        if ($LASTEXITCODE -ne 0) { throw "Tauri check failed." }
    }
    else {
        Write-Warning "Rust is not installed; skipped cargo check."
    }
}
finally {
    Pop-Location
}
