# Starts the app. Its development backend owns Core startup and bounded recovery.
# Windows runs this at sign-in when "Start with Windows" is on in Settings. Safe to run again:
# anything already running is left alone.
param([switch]$Restart)
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot "backend\.venv\Scripts\python.exe"

function Test-PublisherCommand([string]$CommandLine) {
    $ModuleMatch = [regex]::Match($CommandLine, '^\s*(?:"(?<exe>[^"]+)"|(?<exe>\S+))\s+-m\s+mavo\.publisher_gateway(?:\s|$)')
    if (-not $ModuleMatch.Success) { return $false }
    try {
        return [string]::Equals([IO.Path]::GetFullPath($ModuleMatch.Groups['exe'].Value), [IO.Path]::GetFullPath($Python), [StringComparison]::OrdinalIgnoreCase)
    } catch { return $false }
}

# Explicit restart only: never stop an unrelated process that happens to own this port.
if ($Restart) {
    # Stop the owning development app first, otherwise its supervisor can restart
    # Core while this explicit restart is taking place. Saved data is retained.
    $DevelopmentExecutable = [IO.Path]::GetFullPath((Join-Path $ProjectRoot "src-tauri\target\debug\mavo.exe"))
    foreach ($Instance in @(Get-Process -Name mavo -ErrorAction SilentlyContinue | Where-Object {
        $_.Path -and [string]::Equals($_.Path, $DevelopmentExecutable, [StringComparison]::OrdinalIgnoreCase)
    })) {
        Write-Host "Stopping this project's Mavo for restart; active work is interrupted, saved data is retained."
        Stop-Process -InputObject $Instance -ErrorAction Stop
        if (-not $Instance.WaitForExit(10000)) { throw "Mavo did not stop. Quit it from the tray and retry." }
    }
    $PublisherListeners = @(Get-NetTCPConnection -LocalPort 8741 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique)
    foreach ($PublisherPid in $PublisherListeners) {
        $PublisherProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$PublisherPid"
        $OwnedPublisher = $PublisherProcess -and (Test-PublisherCommand $PublisherProcess.CommandLine)
        # Windows venv python.exe can be a launcher whose base-Python child owns the port.
        if ($PublisherProcess -and -not $OwnedPublisher -and $PublisherProcess.CommandLine -match '^\s*(?:"[^"]+"|\S+)\s+-m\s+mavo\.publisher_gateway(?:\s|$)') {
            $PublisherParent = Get-CimInstance Win32_Process -Filter "ProcessId=$($PublisherProcess.ParentProcessId)"
            $OwnedPublisher = $PublisherParent -and $PublisherParent.ExecutablePath -eq [IO.Path]::GetFullPath($Python) -and (Test-PublisherCommand $PublisherParent.CommandLine) -and $PublisherParent.CreationDate -le $PublisherProcess.CreationDate
        }
        if (-not $OwnedPublisher) {
            throw "Port 8741 belongs to another service. Close that service yourself; Mavo will not stop it."
        }
        Write-Host "Restarting this project's Mavo Core service. An in-flight chat may be interrupted; saved data is retained."
        Stop-Process -Id $PublisherPid -ErrorAction Stop
        Wait-Process -Id $PublisherPid -Timeout 10 -ErrorAction SilentlyContinue
    }
}

# Both this entry point and direct dev.ps1 use the debug host's Core supervisor.
# No detached publisher process or required publisher/.env-file existence check.
$Running = Get-Process -Name mavo -ErrorAction SilentlyContinue
if ($Restart) {
    & (Join-Path $PSScriptRoot "dev.ps1") -Restart
} elseif (-not $Running) {
    & (Join-Path $PSScriptRoot "dev.ps1")
}
