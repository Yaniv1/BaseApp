<#
.SYNOPSIS
    Feature 3.5. BaseApp deployment ceremony script.

.DESCRIPTION
    Runs the full BaseApp deployment ceremony in three sequential phases:

      ENVIRONMENT:  Delegates to scripts/setup_env.ps1 to create the Python
                    virtual environment and install any missing dependencies.
      DEPLOY TEST:  Runs scripts/test_deployment.ps1 to exercise the full
                    instantiate / pullbase pipeline and verify the deployment
                    file layout.
      BUILD TESTS:  Runs test/tests/build.py via the virtual environment Python
                    to execute the configured build phase tests.

    Returns exit code 0 when every phase succeeds, or 1 on the first failure.

.PARAMETER AppRoot
    Path to the app root folder.
    Defaults to the parent directory of the folder that contains this script.

.PARAMETER Python
    Python executable (or full path) used by setup_env.ps1 to create the
    virtual environment.
    Defaults to 'python'.

.PARAMETER KeepTemp
    Forwarded to test_deployment.ps1 so that temporary TestApp folders are
    retained after the deployment test (useful for manual inspection).

.EXAMPLE
    .\scripts\deploy.ps1
    .\scripts\deploy.ps1 -Python python3
    .\scripts\deploy.ps1 -AppRoot C:\Code\MyApp
    .\scripts\deploy.ps1 -KeepTemp
#>

# Feature 3.5
[CmdletBinding()]
param(
    [string]$AppRoot  = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string]$Python   = "python",
    [switch]$KeepTemp
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

# ---------- resolved paths ----------

$setupEnv   = Join-Path $PSScriptRoot "setup_env.ps1"
$deployTest = Join-Path $PSScriptRoot "test_deployment.ps1"
$venvPython = Join-Path $AppRoot ".venv\Scripts\python.exe"
$buildTests = Join-Path $AppRoot "test\tests\build.py"

Write-Host ""
Write-Host "BaseApp Deploy  --  AppRoot: $AppRoot" -ForegroundColor White

# ---------- phase: environment ----------

Write-Phase "ENVIRONMENT"

& pwsh -NonInteractive -File $setupEnv -AppRoot $AppRoot -Python $Python
Register-Criterion "setup_env.ps1 passed" ($LASTEXITCODE -eq 0)

if ($script:failCount -gt 0) {
    Write-Host "`n  Aborted: environment setup failed." -ForegroundColor Red
    exit 1
}

# ---------- phase: deployment test ----------

Write-Phase "DEPLOYMENT TEST"

if (Test-Path $deployTest) {
    $dtArgs = @("-BaseAppRoot", $AppRoot, "-Python", $venvPython)
    if ($KeepTemp) { $dtArgs += "-KeepTemp" }
    & pwsh -NonInteractive -File $deployTest @dtArgs
    Register-Criterion "test_deployment.ps1 passed" ($LASTEXITCODE -eq 0)
} else {
    Register-Criterion "test_deployment.ps1 found" $false "not found: $deployTest"
}

# ---------- phase: build tests ----------

Write-Phase "BUILD TESTS"

if (Test-Path $buildTests) {
    & $venvPython $buildTests
    Register-Criterion "build phase tests passed" ($LASTEXITCODE -eq 0)
} else {
    Register-Criterion "build.py found" $false "not found: $buildTests"
}

# ---------- summary ----------

Write-Host ""
Write-Host "=== SUMMARY ===" -ForegroundColor White
$summaryColor = if ($script:failCount -eq 0) { "Green" } else { "Red" }
Write-Host "  PASS: $($script:passCount)   FAIL: $($script:failCount)" -ForegroundColor $summaryColor

if ($script:failCount -gt 0) {
    Write-Host "  Deployment FAILED." -ForegroundColor Red
    exit 1
} else {
    Write-Host "  Deployment SUCCEEDED." -ForegroundColor Green
    exit 0
}
