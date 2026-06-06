<#
.SYNOPSIS
    Feature 3.4. Set up the Python virtual environment and install dependencies.

.DESCRIPTION
    Creates the Python virtual environment (.venv) in the app root when it does
    not already exist, then installs any packages declared in
    dependencies/base.txt and dependencies/app.txt that are not yet present in
    the environment, keeping repeated runs fast.

    This script is called automatically by scripts/instantiate.py (new app
    setup), scripts/pullbase.py (after each base pull), and scripts/deploy.ps1
    (as the first phase of the deployment ceremony).

.PARAMETER AppRoot
    Path to the app root folder.
    Defaults to the parent directory of the folder that contains this script.

.PARAMETER Python
    Python executable (or full path) used to create the virtual environment.
    Defaults to 'python'.

.EXAMPLE
    .\scripts\setup_env.ps1
    .\scripts\setup_env.ps1 -AppRoot C:\Code\MyApp
    .\scripts\setup_env.ps1 -Python python3
#>

# Feature 3.4
[CmdletBinding()]
param(
    [string]$AppRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string]$Python  = "python"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"

# ---------- reporting helpers ----------

$script:passCount = 0
$script:failCount = 0

function Write-Phase {
    param([string]$Label)
    Write-Host ""
    Write-Host "=== $Label ===" -ForegroundColor Cyan
}

function Register-Criterion {
    param(
        [string]$Name,
        [bool]  $Pass,
        [string]$Detail = ""
    )
    $status = if ($Pass) { "PASS" } else { "FAIL" }
    $color  = if ($Pass) { "Green" } else { "Red" }
    $line   = "  [$status] $Name"
    if ($Detail) { $line += " -- $Detail" }
    Write-Host $line -ForegroundColor $color
    if ($Pass) { $script:passCount++ } else { $script:failCount++ }
}

# ---------- dependency helpers ----------

function Get-InstalledPackageNames {
    <#
    .SYNOPSIS
        Returns a hashtable of normalised installed package names from pip list.
    .DESCRIPTION
        Runs 'python -m pip list --format=freeze' and extracts the bare package name
        (lowercased, hyphens converted to underscores) for O(1) membership testing.
    .PARAMETER PythonExe
        Full path to the Python executable inside the virtual environment.
    #>
    param([string]$PythonExe)

    $output = & $PythonExe -m pip list --format=freeze 2>$null
    $names  = @{}
    foreach ($line in $output) {
        if ($line -match '^([A-Za-z0-9_\-\.]+)') {
            $normalised = $matches[1].ToLower() -replace '-', '_'
            $names[$normalised] = $true
        }
    }
    return $names
}

function Install-MissingPackages {
    <#
    .SYNOPSIS
        Installs packages from a requirements file that are not yet in the venv.
    .DESCRIPTION
        Reads each non-comment, non-empty line from ReqFile, normalises the
        package name, checks it against the currently installed set, and calls
        pip only for the missing subset. Returns $true on success.
    .PARAMETER ReqFile
        Absolute path to the requirements text file (e.g. dependencies/base.txt).
    .PARAMETER PythonExe
        Full path to the Python executable inside the virtual environment.
    #>
    param(
        [string]$ReqFile,
        [string]$PythonExe
    )

    if (-not (Test-Path $ReqFile)) {
        Write-Host "  [SKIP] Requirements file not found: $ReqFile" -ForegroundColor Yellow
        return $true
    }

    # Read package specs — skip blank lines and comments
    $specs = @(
        Get-Content $ReqFile |
            Where-Object { $_ -match '\S' -and $_ -notmatch '^\s*#' } |
            ForEach-Object { $_.Trim() }
    )

    if ($specs.Count -eq 0) {
        Write-Host "  [SKIP] No packages declared in $([System.IO.Path]::GetFileName($ReqFile))" -ForegroundColor Yellow
        return $true
    }

    # Identify missing packages
    $installed = Get-InstalledPackageNames -PythonExe $PythonExe
    $missing   = @()

    foreach ($spec in $specs) {
        # Strip version specifier to get the bare name
        $bare = ($spec -replace '[>=<!~\s].*', '').ToLower() -replace '-', '_'
        if (-not $installed.ContainsKey($bare)) {
            $missing += $spec
        }
    }

    if ($missing.Count -eq 0) {
        Write-Host "  [OK] All $($specs.Count) package(s) from $([System.IO.Path]::GetFileName($ReqFile)) already installed." -ForegroundColor Green
        return $true
    }

    Write-Host "  Installing $($missing.Count) missing package(s) from $([System.IO.Path]::GetFileName($ReqFile)): $($missing -join ', ')" -ForegroundColor Yellow
    & $PythonExe -m pip install @missing
    return ($LASTEXITCODE -eq 0)
}

# ---------- resolved paths ----------

$venvDir    = Join-Path $AppRoot ".venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"
$baseDeps   = Join-Path $AppRoot "dependencies\base.txt"
$appDeps    = Join-Path $AppRoot "dependencies\app.txt"

Write-Host ""
Write-Host "Setup Environment  --  AppRoot: $AppRoot" -ForegroundColor White

# ---------- phase: venv ----------

Write-Phase "VENV"

if (-not (Test-Path $venvPython)) {
    Write-Host "  Creating virtual environment at: $venvDir" -ForegroundColor Yellow
    & $Python -m venv $venvDir
    Register-Criterion "venv created" ($LASTEXITCODE -eq 0) $venvDir
} else {
    Register-Criterion "venv exists" $true $venvDir
}

if ($script:failCount -gt 0) {
    Write-Host "`n  Aborted: virtual environment creation failed." -ForegroundColor Red
    exit 1
}

# ---------- phase: dependencies ----------

Write-Phase "DEPENDENCIES"

$baseOk = Install-MissingPackages -ReqFile $baseDeps -PythonExe $venvPython
Register-Criterion "base dependencies installed" $baseOk

$appOk = Install-MissingPackages -ReqFile $appDeps -PythonExe $venvPython
Register-Criterion "app dependencies installed" $appOk

# ---------- summary ----------

Write-Host ""
Write-Host "=== SUMMARY ===" -ForegroundColor White
$summaryColor = if ($script:failCount -eq 0) { "Green" } else { "Red" }
Write-Host "  PASS: $($script:passCount)   FAIL: $($script:failCount)" -ForegroundColor $summaryColor

if ($script:failCount -gt 0) {
    Write-Host "  Environment setup FAILED." -ForegroundColor Red
    exit 1
} else {
    Write-Host "  Environment setup SUCCEEDED." -ForegroundColor Green
    exit 0
}
