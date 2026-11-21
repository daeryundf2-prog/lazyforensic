# LazyForensic installer for Windows (PowerShell 5.1+).
#
# One-line install (PowerShell, normal user, no admin):
#   irm https://raw.githubusercontent.com/daeryundf2-prog/lazyforensic/main/install.ps1 | iex
#
# Offline install (analysis PC without internet): copy an extracted release folder
# to the PC, then in that folder run:
#   powershell -ExecutionPolicy Bypass -File install.ps1
#
# What it does:
#   1. Finds Python 3.10+ (py launcher or python). If missing and winget exists,
#      installs Python 3.12 for the current user.
#   2. Copies LazyForensic to %LOCALAPPDATA%\LazyForensic\app (replaces an older copy).
#   3. Creates the venv %USERPROFILE%\.lfenv and installs Pillow (photo EXIF checks).
#      Without internet this step is skipped; the report still works.
#   4. Puts %LOCALAPPDATA%\LazyForensic\bin (only lazyforensic.cmd) on the user PATH.
#
# Environment overrides:
#   LAZYFORENSIC_REF     tag or branch to download (default: latest release, else main)
#   LAZYFORENSIC_SOURCE  local folder to install from instead of downloading
#
# This file is ASCII on purpose: "irm | iex" and Windows PowerShell 5.1 both read it safely.

$ErrorActionPreference = "Stop"
$Repo = "daeryundf2-prog/lazyforensic"
$InstallRoot = Join-Path $env:LOCALAPPDATA "LazyForensic"
$AppDir = Join-Path $InstallRoot "app"
$BinDir = Join-Path $InstallRoot "bin"
$VenvDir = Join-Path $env:USERPROFILE ".lfenv"

function Say($msg) { Write-Host "[LazyForensic] $msg" }

function Test-Python($exe, $extra) {
    try {
        $out = & $exe @extra -c "import sys; print('%d.%d' % sys.version_info[:2]); sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) { return $out }
    } catch { }
    return $null
}

function Find-Python {
    # Prefer versions Pillow ships Windows wheels for; bare "py -3" picks the newest
    # installed Python, which may be a preview without wheels (seen on CI: 3.15).
    $cands = @(
        @{ exe = "py"; extra = @("-3.13") },
        @{ exe = "py"; extra = @("-3.12") },
        @{ exe = "py"; extra = @("-3.11") },
        @{ exe = "py"; extra = @("-3.10") },
        @{ exe = "py"; extra = @("-3") },
        @{ exe = "python"; extra = @() },
        @{ exe = "python3"; extra = @() }
    )
    foreach ($c in $cands) {
        $cmd = Get-Command $c.exe -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        # The Microsoft Store alias (WindowsApps) only opens the Store.
        if ($cmd.Source -like "*\WindowsApps\*") { continue }
        $ver = Test-Python $c.exe $c.extra
        if ($ver) {
            $real = & $c.exe @($c.extra) -c "import sys; print(sys.executable)"
            return @{ path = $real.Trim(); version = $ver }
        }
    }
    foreach ($v in @("313", "312", "311", "310")) {
        $p = Join-Path $env:LOCALAPPDATA "Programs\Python\Python$v\python.exe"
        if (Test-Path $p) {
            $ver = Test-Python $p @()
            if ($ver) { return @{ path = $p; version = $ver } }
        }
    }
    return $null
}

# 1. Python
$py = Find-Python
if (-not $py) {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Say "Python 3.10+ not found. Installing Python 3.12 for this user with winget..."
        winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
        $py = Find-Python
    }
    if (-not $py) {
        Say "Python 3.10+ is required. Install it from https://www.python.org/downloads/windows/"
        Say "(check 'Add python.exe to PATH'), then run this installer again."
        exit 1
    }
}
Say "Python $($py.version): $($py.path)"

# 2. Program files
$tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("lazyforensic-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $tmp | Out-Null
try {
    if ($env:LAZYFORENSIC_SOURCE) {
        $src = (Resolve-Path $env:LAZYFORENSIC_SOURCE).Path
    } elseif ($PSCommandPath -and (Test-Path (Join-Path (Split-Path -Parent $PSCommandPath) "scripts\report_draft.py"))) {
        $src = Split-Path -Parent $PSCommandPath
    } else {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $ref = $env:LAZYFORENSIC_REF
        if (-not $ref) {
            try {
                $ref = (Invoke-RestMethod "https://api.github.com/repos/$Repo/releases/latest" -UseBasicParsing).tag_name
            } catch { $ref = "main" }
        }
        $zip = Join-Path $tmp "lazyforensic.zip"
        Say "Downloading $Repo ($ref)..."
        Invoke-WebRequest "https://github.com/$Repo/archive/$ref.zip" -OutFile $zip -UseBasicParsing
        Expand-Archive -Path $zip -DestinationPath $tmp
        $src = (Get-ChildItem $tmp -Directory | Where-Object { Test-Path (Join-Path $_.FullName "scripts\report_draft.py") } | Select-Object -First 1).FullName
        if (-not $src) { throw "Downloaded archive does not contain LazyForensic." }
    }
    if ((Resolve-Path $src).Path -ne $AppDir) {
        if (Test-Path $AppDir) { Remove-Item -Recurse -Force $AppDir }
        New-Item -ItemType Directory -Path $AppDir -Force | Out-Null
        Get-ChildItem -Force $src | Where-Object { $_.Name -ne ".git" } | Copy-Item -Destination $AppDir -Recurse -Force
    }
} finally {
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}
Say "Installed to $AppDir"

# 3. venv + Pillow
$venvPy = Join-Path $VenvDir "Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    Say "Creating Python environment $VenvDir"
    & $py.path -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) { throw "python -m venv failed" }
}
& $venvPy -m pip install --disable-pip-version-check -q "pillow>=10.0,<12"
if ($LASTEXITCODE -ne 0) {
    Say "WARNING: Pillow install failed (offline?). Photo EXIF checks will be skipped; reports still work."
}

# 4. Command on PATH
New-Item -ItemType Directory -Path $BinDir -Force | Out-Null
$shim = "@echo off`r`ncall `"$AppDir\bin\lazyforensic.cmd`" %*`r`nexit /b %ERRORLEVEL%`r`n"
[System.IO.File]::WriteAllText((Join-Path $BinDir "lazyforensic.cmd"), $shim, (New-Object System.Text.UTF8Encoding($false)))
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (-not $userPath) { $userPath = "" }
if (($userPath -split ";") -notcontains $BinDir) {
    [Environment]::SetEnvironmentVariable("Path", (($userPath.TrimEnd(";") + ";" + $BinDir).TrimStart(";")), "User")
    Say "Added $BinDir to your PATH."
}
if (($env:Path -split ";") -notcontains $BinDir) { $env:Path = "$env:Path;$BinDir" }

& (Join-Path $BinDir "lazyforensic.cmd") report --help | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Self-test failed: lazyforensic report --help" }

Say "Done. Open a NEW PowerShell or cmd window and run:"
Say "    lazyforensic report D:\path\to\evidence-folder"
