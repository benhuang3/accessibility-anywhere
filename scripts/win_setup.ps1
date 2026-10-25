# Set up a Windows machine (or Parallels VM) to run Ally's OS lab, then run it.
#
#   powershell -ExecutionPolicy Bypass -File scripts\win_setup.ps1                 # copy, install, run the lab
#   powershell -ExecutionPolicy Bypass -File scripts\win_setup.ps1 -LabArgs --auto
#   powershell -ExecutionPolicy Bypass -File scripts\win_setup.ps1 -Source D:\ally -SkipLab
#
# Copies the repo to a local folder (running from the Parallels \\Mac share is slow, and the Mac's .venv
# can't run here), installs Python 3.13 if missing, makes a .venv with only the packages the OS layer
# needs, and runs scripts\os_lab.py. Safe to run again: it re-syncs the code and keeps the .venv.

param(
    [string]$Source = "\\Mac\Home\gitssh\accessibility-anywhere",
    [string]$Dest = "C:\ally",
    [switch]$SkipLab,
    [string[]]$LabArgs = @()
)

$ErrorActionPreference = "Stop"

function Find-Python {
    foreach ($cmd in @("py -3.13", "py -3.12", "py -3.11", "python")) {
        $parts = $cmd.Split(" ")
        try {
            $v = & $parts[0] @($parts[1..9] | Where-Object { $_ }) -c "import sys; print(sys.version_info >= (3, 11))" 2>$null
            if ($v -eq "True") { return $cmd }
        } catch {}
    }
    return $null
}

Write-Host "== Copying $Source -> $Dest"
if (-not (Test-Path $Source)) { throw "Can't find the repo at $Source. Pass -Source (Parallels shares your Mac home as \\Mac\Home)." }
robocopy $Source $Dest /MIR /XD .venv .git logs state cache __pycache__ /XF .env /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy failed ($LASTEXITCODE)" }
$global:LASTEXITCODE = 0

Write-Host "== Python"
$py = Find-Python
if (-not $py) {
    Write-Host "Installing Python 3.13 with winget..."
    winget install -e --id Python.Python.3.13 --scope user --accept-package-agreements --accept-source-agreements
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + [Environment]::GetEnvironmentVariable("Path", "Machine")
    $py = Find-Python
    if (-not $py) { throw "Python 3.11+ still not found. Open a new PowerShell window and run this again." }
}
Write-Host "Using: $py"

$venvPy = Join-Path $Dest ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    Write-Host "== Creating $Dest\.venv"
    $parts = $py.Split(" ")
    & $parts[0] @($parts[1..9] | Where-Object { $_ }) -m venv (Join-Path $Dest ".venv")
}
Write-Host "== Installing the OS-layer packages"
# Only what the OS layer uses; mediapipe and faster-whisper are not needed for the lab
# (and may have no wheels for Windows on ARM, which is what Parallels runs on Apple silicon).
& $venvPy -m pip install --quiet --upgrade pip
& $venvPy -m pip install --quiet "comtypes>=1.2" "pynput>=1.7.6" "mss>=9.0" "pillow>=10.0"
& $venvPy -c "import struct, platform; print('python', platform.python_version(), struct.calcsize('P')*8, 'bit', platform.machine())"

if ($SkipLab) { Write-Host "Ready. Run: $venvPy $Dest\scripts\os_lab.py"; exit 0 }
Write-Host "== OS lab"
Set-Location $Dest
& $venvPy scripts\os_lab.py @LabArgs
exit $LASTEXITCODE
