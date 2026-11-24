# LazyForensic installer for Windows (PowerShell 5.1+).
#
# One-line install (PowerShell, normal user, no admin). The installer attached to a release installs
# exactly that release:
#   irm https://github.com/daeryundf2-prog/lazyforensic/releases/latest/download/install.ps1 | iex
#
# Offline install (analysis PC without internet): copy an extracted release folder
# to the PC, then in that folder run:
#   powershell -ExecutionPolicy Bypass -File install.ps1
#
# What it does:
#   1. Downloads the release archive lazyforensic-<tag>.zip and checks its SHA-256 against the
#      release's SHA256SUMS, GitHub's asset digest and (release installer) the value stamped below.
#      The release lookup must answer with the requested tag. Any mismatch or a failed release lookup
#      stops before anything is changed. There is no fallback to the main branch.
#   2. Finds Python 3.10+ (py launcher or python). If missing and winget exists,
#      installs Python 3.12 for the current user.
#   3. Replaces %LOCALAPPDATA%\LazyForensic\app (the old copy stays until the new one is complete).
#   4. Creates the venv %USERPROFILE%\.lfenv and installs Pillow from requirements-install.txt
#      (pip --require-hashes). Without internet this step is skipped; the report still works.
#   5. Puts %LOCALAPPDATA%\LazyForensic\bin (only lazyforensic.cmd) on the user PATH and writes
#      %LOCALAPPDATA%\LazyForensic\install-receipt.json (archive tag, name and SHA-256).
#
# Environment overrides:
#   LAZYFORENSIC_REF          release tag to install (default: the release this installer belongs to,
#                             else the latest release). A release installer installs another tag only
#                             together with LAZYFORENSIC_SHA256; otherwise use that release's installer.
#   LAZYFORENSIC_SHA256       expected SHA-256 of the release archive (an extra pin you got elsewhere)
#   LAZYFORENSIC_SOURCE       local folder to install from instead of downloading (recorded as unverified)
#   LAZYFORENSIC_RELEASE_API  GitHub API repository URL (mirrors and tests)
#
# This file is ASCII on purpose: "irm | iex" and Windows PowerShell 5.1 both read it safely.

$ErrorActionPreference = "Stop"
$Repo = "daeryundf2-prog/lazyforensic"
# Stamped by .github/workflows/release-assets.yml into the installer attached to a release.
$ReleaseTag = ""
$ReleaseArchiveSha256 = ""
$Api = if ($env:LAZYFORENSIC_RELEASE_API) { $env:LAZYFORENSIC_RELEASE_API.TrimEnd("/") } else { "https://api.github.com/repos/$Repo" }
$InstallRoot = Join-Path $env:LOCALAPPDATA "LazyForensic"
$AppDir = Join-Path $InstallRoot "app"
$BinDir = Join-Path $InstallRoot "bin"
$VenvDir = Join-Path $env:USERPROFILE ".lfenv"

function Say($msg) { Write-Host "[LazyForensic] $msg" }

# .NET instead of Get-FileHash / Expand-Archive: those come from script modules that Windows PowerShell
# may fail to load when started from PowerShell 7 (inherited PSModulePath).
function Get-Sha256($path) {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $stream = [System.IO.File]::OpenRead($path)
    try { return ([BitConverter]::ToString($sha.ComputeHash($stream)) -replace '-', '').ToLowerInvariant() }
    finally { $stream.Dispose(); $sha.Dispose() }
}

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

function Get-ReleaseArchive($tmp) {
    # Returns the extracted source folder and what the archive was checked against.
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $ref = $env:LAZYFORENSIC_REF
    if (-not $ref) { $ref = $ReleaseTag }
    # A release installer carries the SHA-256 of its own archive only. Another tag needs its own installer or a pin.
    if ($ReleaseTag -and ($ref -cne $ReleaseTag) -and -not $env:LAZYFORENSIC_SHA256) {
        throw "This installer is for release $ReleaseTag and has no stamped SHA-256 for $ref. Use the install.ps1 attached to release $ref, or set LAZYFORENSIC_SHA256 to its archive's SHA-256. Nothing was changed."
    }
    $url = if ($ref) { "$Api/releases/tags/$ref" } else { "$Api/releases/latest" }
    try {
        $release = Invoke-RestMethod $url -UseBasicParsing
    } catch {
        throw "Release lookup failed ($url): $($_.Exception.Message.TrimEnd('.')). Nothing was changed. The installer does not fall back to the main branch; retry, or install from a verified local copy with LAZYFORENSIC_SOURCE."
    }
    $tag = [string]$release.tag_name
    if (-not $tag) { throw "Release lookup returned no tag ($url). Nothing was changed." }
    # The answer must be the release that was asked for, before any asset is looked at.
    if ($ref -and ($tag -cne $ref)) { throw "Release lookup for $ref returned release $tag. Nothing was changed." }
    $name = "lazyforensic-$tag.zip"
    $archiveAsset = $release.assets | Where-Object { $_.name -eq $name } | Select-Object -First 1
    $sumsAsset = $release.assets | Where-Object { $_.name -eq "SHA256SUMS" } | Select-Object -First 1
    if (-not $archiveAsset -or -not $sumsAsset) {
        throw "Release $tag has no $name with SHA256SUMS, so it cannot be verified. Nothing was changed."
    }
    $zip = Join-Path $tmp $name
    $sums = Join-Path $tmp "SHA256SUMS"
    Say "Downloading $name ($Repo $tag)..."
    Invoke-WebRequest $archiveAsset.browser_download_url -OutFile $zip -UseBasicParsing
    Invoke-WebRequest $sumsAsset.browser_download_url -OutFile $sums -UseBasicParsing
    $actual = Get-Sha256 $zip

    $listed = @()
    foreach ($line in [System.IO.File]::ReadAllLines($sums)) {
        if ($line -match '^([0-9A-Fa-f]{64}) [ *](.+)$' -and $Matches[2] -eq $name) { $listed += $Matches[1].ToLowerInvariant() }
    }
    if ($listed.Count -ne 1) { throw "SHA256SUMS of $tag does not list $name exactly once. Nothing was changed." }
    $pins = @(@{ source = "SHA256SUMS"; value = $listed[0] })
    if ($archiveAsset.digest) {
        $pins += @{ source = "GitHub asset digest"; value = ([string]$archiveAsset.digest -replace '^sha256:', '').ToLowerInvariant() }
    }
    if ($ReleaseTag -and ($tag -ceq $ReleaseTag)) {
        if (-not $ReleaseArchiveSha256) { throw "This release installer has no stamped SHA-256 for $name. Nothing was changed." }
        $pins += @{ source = "release installer"; value = $ReleaseArchiveSha256.ToLowerInvariant() }
    }
    if ($env:LAZYFORENSIC_SHA256) {
        $pins += @{ source = "LAZYFORENSIC_SHA256"; value = $env:LAZYFORENSIC_SHA256.Trim().ToLowerInvariant() }
    }
    foreach ($pin in $pins) {
        if ($pin.value -ne $actual) {
            throw "SHA-256 of $name does not match $($pin.source): expected $($pin.value), got $actual. Nothing was changed."
        }
    }
    Say "Verified $name SHA-256 $actual ($((($pins | ForEach-Object { $_.source }) -join ', ')))"
    $extract = Join-Path $tmp "extract"
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::ExtractToDirectory($zip, $extract)
    $src = (Get-ChildItem $extract -Directory | Where-Object { Test-Path (Join-Path $_.FullName "scripts\report_draft.py") } | Select-Object -First 1).FullName
    if (-not $src) { throw "Downloaded archive does not contain LazyForensic. Nothing was changed." }
    return @{ path = $src; record = [ordered]@{ kind = "release"; tag = $tag; archive = $name; sha256 = $actual; verified_against = @($pins | ForEach-Object { $_.source }) } }
}

$tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("lazyforensic-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $tmp | Out-Null
try {
    # 1. Program files: verify before anything on this PC changes.
    if ($env:LAZYFORENSIC_SOURCE) {
        $src = (Resolve-Path $env:LAZYFORENSIC_SOURCE).Path
        $source = [ordered]@{ kind = "local"; path = $src; sha256 = $null; verified_against = @() }
    } elseif ($PSCommandPath -and (Test-Path (Join-Path (Split-Path -Parent $PSCommandPath) "scripts\report_draft.py"))) {
        $src = Split-Path -Parent $PSCommandPath
        $source = [ordered]@{ kind = "local"; path = $src; sha256 = $null; verified_against = @() }
    } else {
        $fetched = Get-ReleaseArchive $tmp
        $src = $fetched.path
        $source = $fetched.record
    }

    # 2. Python
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

    # 3. Replace the app only after the new copy is complete.
    if ((Resolve-Path $src).Path -ne $AppDir) {
        $staged = Join-Path $InstallRoot "app.new"
        $previous = Join-Path $InstallRoot "app.old"
        foreach ($d in @($staged, $previous)) { if (Test-Path $d) { Remove-Item -Recurse -Force $d } }
        New-Item -ItemType Directory -Path $staged -Force | Out-Null
        Get-ChildItem -Force $src | Where-Object { $_.Name -ne ".git" } | Copy-Item -Destination $staged -Recurse -Force
        if (Test-Path $AppDir) { Move-Item -LiteralPath $AppDir -Destination $previous }
        Move-Item -LiteralPath $staged -Destination $AppDir
        if (Test-Path $previous) { Remove-Item -Recurse -Force $previous }
    }
} finally {
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}
Say "Installed to $AppDir"

# 4. venv + Pillow (hash-pinned)
$venvPy = Join-Path $VenvDir "Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    Say "Creating Python environment $VenvDir"
    & $py.path -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) { throw "python -m venv failed" }
}
$requirements = Join-Path $AppDir "requirements-install.txt"
& $venvPy -m pip install --disable-pip-version-check -q --require-hashes --only-binary=:all: -r $requirements
if ($LASTEXITCODE -ne 0) {
    Say "WARNING: Pillow install failed (offline?). Photo EXIF checks will be skipped; reports still work."
}
$pillow = $null
try {
    $version = & $venvPy -c "import PIL; print(PIL.__version__)" 2>$null
    if ($LASTEXITCODE -eq 0) { $pillow = "$version".Trim() }
} catch { }

# 5. Command on PATH
New-Item -ItemType Directory -Path $BinDir -Force | Out-Null
# cmd.exe decodes batch files using the active code page. Keep the shim ASCII
# and expand its Unicode location at runtime.
$shim = "@echo off`r`n`"%~dp0..\app\bin\lazyforensic.cmd`" %*`r`n"
[System.IO.File]::WriteAllText((Join-Path $BinDir "lazyforensic.cmd"), $shim, [System.Text.Encoding]::ASCII)
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (-not $userPath) { $userPath = "" }
if (($userPath -split ";") -notcontains $BinDir) {
    [Environment]::SetEnvironmentVariable("Path", (($userPath.TrimEnd(";") + ";" + $BinDir).TrimStart(";")), "User")
    Say "Added $BinDir to your PATH."
}
if (($env:Path -split ";") -notcontains $BinDir) { $env:Path = "$env:Path;$BinDir" }

& (Join-Path $BinDir "lazyforensic.cmd") report --help | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Self-test failed: lazyforensic report --help" }

# Install receipt: what was installed, from which verified archive.
$receipt = [ordered]@{
    installed_at = (Get-Date).ToUniversalTime().ToString("o")
    installer = if ($ReleaseTag) { "release $ReleaseTag" } else { "repository" }
    source = $source
    python = $py.version
    requirements_sha256 = if (Test-Path -LiteralPath $requirements) { Get-Sha256 $requirements } else { $null }
    pillow = $pillow
}
[System.IO.File]::WriteAllText((Join-Path $InstallRoot "install-receipt.json"), ($receipt | ConvertTo-Json -Depth 4), (New-Object System.Text.UTF8Encoding $false))

Say "Done. Open a NEW PowerShell or cmd window and run:"
Say "    lazyforensic report D:\path\to\evidence-folder"
