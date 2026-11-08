param([switch]$Restart)
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$DevelopmentExecutable = [IO.Path]::GetFullPath((Join-Path $ProjectRoot "src-tauri\target\debug\mavo.exe"))
$RunningInstances = @(Get-Process -Name mavo -ErrorAction SilentlyContinue | Where-Object {
    $_.Path -and [string]::Equals($_.Path, $DevelopmentExecutable, [StringComparison]::OrdinalIgnoreCase)
})
if ($RunningInstances.Count -gt 0 -and -not $Restart) {
    throw "Mavo is still running from this project and Windows cannot rebuild its executable. Choose Quit from the Mavo tray/desktop button (closing its window only hides it), or run .\scripts\dev.ps1 -Restart. Restart interrupts active work; saved data is retained."
}
foreach ($Instance in $RunningInstances) {
    Write-Host "Stopping this project's development Mavo (PID $($Instance.Id)) before rebuilding. Saved data is retained; active work is interrupted."
    if (-not $Instance.HasExited) {
        Stop-Process -InputObject $Instance -ErrorAction Stop
        if (-not $Instance.WaitForExit(10000)) {
            throw "Mavo did not exit. Choose Quit from its tray icon and try again."
        }
    }
}
$PythonPath = Join-Path $ProjectRoot "backend\.venv\Scripts\python.exe"
$UserCargoHome = [Environment]::GetEnvironmentVariable("CARGO_HOME", "User")
$CargoHomeCandidates = @($env:CARGO_HOME, $UserCargoHome, "D:\DevTools\Rust\cargo", (Join-Path $env:USERPROFILE ".cargo")) |
    Where-Object { $_ } |
    Select-Object -Unique
$CargoBin = $CargoHomeCandidates |
    ForEach-Object { Join-Path $_ "bin" } |
    Where-Object { Test-Path (Join-Path $_ "cargo.exe") } |
    Select-Object -First 1

if (-not (Test-Path $PythonPath)) {
    throw "Run .\scripts\setup.ps1 first."
}
if (-not (Get-Command cargo -ErrorAction SilentlyContinue) -and $CargoBin) {
    $env:PATH = "$CargoBin$([IO.Path]::PathSeparator)$env:PATH"
}
if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
    throw "Rust is required for the desktop shell. Install it from https://rustup.rs."
}
if ($CargoBin) {
    $env:CARGO_HOME = Split-Path $CargoBin -Parent
}
$UserRustupHome = [Environment]::GetEnvironmentVariable("RUSTUP_HOME", "User")
if ($UserRustupHome -and (Test-Path $UserRustupHome)) {
    $env:RUSTUP_HOME = $UserRustupHome
}

& (Join-Path $PSScriptRoot "enter-msvc.ps1")

Push-Location $ProjectRoot
try {
    npm run tauri dev
    if ($LASTEXITCODE -ne 0) { throw "Tauri development process failed." }
}
finally {
    Pop-Location
}
