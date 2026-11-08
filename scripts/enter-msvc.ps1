function Test-WindowsSdkLibrary {
    if (-not $env:LIB) { return $false }
    foreach ($LibraryPath in ($env:LIB -split ';')) {
        if ($LibraryPath -and (Test-Path (Join-Path $LibraryPath "kernel32.lib"))) {
            return $true
        }
    }
    return $false
}

$ErrorActionPreference = "Stop"

if ((Get-Command link.exe -ErrorAction SilentlyContinue) -and (Test-WindowsSdkLibrary)) {
    return
}

$PreferredDevCommand = "D:\DevTools\VSBuildTools2022\Common7\Tools\VsDevCmd.bat"
if (Test-Path $PreferredDevCommand) {
    $DevCommand = $PreferredDevCommand
}
else {
    $SearchRoots = @(
        (Join-Path $env:ProgramFiles "Microsoft Visual Studio")
        (Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio")
    ) | Where-Object { Test-Path $_ }

    $DevCommand = Get-ChildItem $SearchRoots -Filter "VsDevCmd.bat" -Recurse -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1 -ExpandProperty FullName
}

if (-not $DevCommand) {
    throw "Microsoft C++ Build Tools are required. Install the 'Desktop development with C++' workload from Visual Studio Installer."
}

$EnvironmentLines = & $env:COMSPEC /s /c "`"$DevCommand`" -no_logo -arch=x64 -host_arch=x64 >nul 2>&1 && set"
if ($LASTEXITCODE -ne 0) {
    throw "Visual Studio's C++ build environment could not be initialized."
}

foreach ($Line in $EnvironmentLines) {
    if ($Line -match '^([^=]+)=(.*)$') {
        Set-Item -Path "Env:$($Matches[1])" -Value $Matches[2]
    }
}

if (-not (Get-Command link.exe -ErrorAction SilentlyContinue)) {
    throw "Visual Studio was found, but its C++ linker is unavailable. Add the 'Desktop development with C++' workload."
}
if (-not (Test-WindowsSdkLibrary)) {
    throw "The Windows SDK libraries are missing. In Visual Studio Installer, modify the installation and add 'Desktop development with C++' plus a Windows 10 or 11 SDK."
}
