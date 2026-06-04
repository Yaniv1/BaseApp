<#
.SYNOPSIS
    Feature 3.3.1. TestApp deployment integration test.

.DESCRIPTION
    Verifies the full BaseApp deployment pipeline by exercising three sequential phases:

      PRE:        Validates that all required BaseApp source files are present before
                  attempting any deployment step.
      INSTANTIATE: Runs scripts/instantiate.py to create a fresh TestApp in a unique
                  temp folder and verifies that pull-manifest and once-manifest items
                  are correctly placed, and that config/app.json APP_NAME was set.
      PULLBASE:   Runs scripts/pullbase.py from inside the newly instantiated TestApp,
                  pointing back to BaseApp as the source, and verifies exit code 0.
      POST:       Verifies the final state of TestApp: pull-manifest items are present
                  and content-identical to the source; once-manifest items (e.g.
                  config/app.json APP_NAME) were NOT overwritten by the pullbase run.

    Cleans up the temp folder on exit unless -KeepTemp is set.
    Returns exit code 0 if every criterion passes, 1 if any criterion fails.

.PARAMETER BaseAppRoot
    Path to the BaseApp root folder.
    Defaults to the parent directory of the folder that contains this script.

.PARAMETER Python
    Python executable (or full path) to use for running .py scripts.
    Defaults to 'python'.

.PARAMETER KeepTemp
    When set, the temporary TestApp folder is NOT deleted after the test.
    Useful for manual inspection after a failure.

.EXAMPLE
    .\scripts\test_deployment.ps1
    .\scripts\test_deployment.ps1 -Python python3 -KeepTemp
    .\scripts\test_deployment.ps1 -BaseAppRoot C:\Code\BaseApp
#>

[CmdletBinding()]
param(
    [string]$BaseAppRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string]$Python      = "python",
    [switch]$KeepTemp
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"

# ---------- helpers ----------

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

# ---------- setup ----------

$timestamp       = Get-Date -Format "yyyyMMddTHHmmssZ"
$tempParent      = Join-Path ([System.IO.Path]::GetTempPath()) "BaseAppDeployTest_$timestamp"
$testAppDir      = Join-Path $tempParent "TestApp"
$expectedAppName = "TestApp"

Write-Host "Feature 3.3.1 -- TestApp Deployment Integration Test" -ForegroundColor White
Write-Host "BaseApp root : $BaseAppRoot"
Write-Host "Temp TestApp : $testAppDir"
Write-Host "Python       : $Python"

# ---------- PRE: BaseApp source validity ----------

Write-Phase "PRE -- BaseApp source validity"

$sourceChecks = @(
    "scripts/instantiate.py",
    "scripts/pullbase.py",
    "config/base.json",
    "docs/manifests/pull.json",
    "docs/manifests/once.json"
)

foreach ($rel in $sourceChecks) {
    $full = Join-Path $BaseAppRoot ($rel -replace '/', [System.IO.Path]::DirectorySeparatorChar)
    Register-Criterion -Name "source_has_$($rel -replace '/','_')" -Pass (Test-Path $full)
}

if ($script:failCount -gt 0) {
    Write-Host ""
    Write-Host "PRE phase failed -- aborting." -ForegroundColor Red
    exit 1
}

# ---------- INSTANTIATE ----------

Write-Phase "INSTANTIATE -- scripts/instantiate.py"

$instScript = Join-Path $BaseAppRoot "scripts/instantiate.py"
$instOutput = ""
$instExit   = -1

try {
    $instOutput = & $Python $instScript $testAppDir 2>&1 | Out-String
    $instExit   = $LASTEXITCODE
} catch {
    $instOutput = $_.Exception.Message
    $instExit   = -1
}

Register-Criterion -Name "instantiate_exit_code_zero" -Pass ($instExit -eq 0) -Detail "exit=$instExit"

if ($instExit -ne 0) {
    Write-Host "  instantiate output:" -ForegroundColor Yellow
    $instOutput -split "`n" | ForEach-Object { if ($_.Trim()) { Write-Host "    $_" } }
}

# Pull-manifest items expected in the new TestApp
$pullItems = @(
    "app/base.py",
    "config/base.json",
    "scripts/pullbase.py",
    "utils/baseutils.py",
    "utils/datautils.py",
    "docs/manifests/pull.json"
)
foreach ($rel in $pullItems) {
    $full = Join-Path $testAppDir ($rel -replace '/', [System.IO.Path]::DirectorySeparatorChar)
    Register-Criterion -Name "testapp_pull_item_$($rel -replace '/','_')" -Pass (Test-Path $full)
}

# Once-manifest items expected in the new TestApp (created because they were absent)
$onceItems = @(
    "config/app.json",
    "app/app.py"
)
foreach ($rel in $onceItems) {
    $full = Join-Path $testAppDir ($rel -replace '/', [System.IO.Path]::DirectorySeparatorChar)
    Register-Criterion -Name "testapp_once_item_$($rel -replace '/','_')" -Pass (Test-Path $full)
}

# APP_NAME in config/app.json must equal the target folder name ("TestApp")
$appConfigPath = Join-Path $testAppDir "config/app.json"
if (Test-Path $appConfigPath) {
    try {
        $appConfig = Get-Content $appConfigPath -Raw | ConvertFrom-Json
        $appName   = $appConfig.COMMON.APP_NAME
        Register-Criterion -Name "testapp_app_name_set" `
            -Pass ($appName -eq $expectedAppName) `
            -Detail "APP_NAME=$appName"
    } catch {
        Register-Criterion -Name "testapp_app_name_set" -Pass $false -Detail $_.Exception.Message
    }
} else {
    Register-Criterion -Name "testapp_app_name_set" -Pass $false -Detail "config/app.json missing"
}

# ---------- PULLBASE ----------

Write-Phase "PULLBASE -- scripts/pullbase.py (run from TestApp)"

$pullScript = Join-Path $testAppDir "scripts/pullbase.py"
$pullOutput = ""
$pullExit   = -1

if (Test-Path $pullScript) {
    try {
        $pullOutput = & $Python $pullScript --source $BaseAppRoot 2>&1 | Out-String
        $pullExit   = $LASTEXITCODE
    } catch {
        $pullOutput = $_.Exception.Message
        $pullExit   = -1
    }
    Register-Criterion -Name "pullbase_exit_code_zero" -Pass ($pullExit -eq 0) -Detail "exit=$pullExit"
    if ($pullExit -ne 0) {
        Write-Host "  pullbase output:" -ForegroundColor Yellow
        $pullOutput -split "`n" | ForEach-Object { if ($_.Trim()) { Write-Host "    $_" } }
    }
} else {
    Register-Criterion -Name "pullbase_exit_code_zero" -Pass $false -Detail "pullbase.py missing in TestApp"
}

# ---------- POST: final TestApp state ----------

Write-Phase "POST -- final TestApp state"

# Pull-manifest items must still be present after pullbase
$postPullItems = @(
    "app/base.py",
    "utils/baseutils.py",
    "utils/datautils.py",
    "utils/testutils.py",
    "docs/tasks/base.json",
    "docs/requirements/base.json",
    "scripts/pullbase.py"
)
foreach ($rel in $postPullItems) {
    $full = Join-Path $testAppDir ($rel -replace '/', [System.IO.Path]::DirectorySeparatorChar)
    Register-Criterion -Name "post_testapp_has_$($rel -replace '/','_')" -Pass (Test-Path $full)
}

# app/base.py must be content-identical to the BaseApp source (pullbase updated it)
$srcBasePy = Join-Path $BaseAppRoot "app/base.py"
$dstBasePy = Join-Path $testAppDir  "app/base.py"
if ((Test-Path $srcBasePy) -and (Test-Path $dstBasePy)) {
    $srcHash = (Get-FileHash $srcBasePy -Algorithm SHA256).Hash
    $dstHash = (Get-FileHash $dstBasePy -Algorithm SHA256).Hash
    Register-Criterion -Name "post_base_py_matches_source" -Pass ($srcHash -eq $dstHash)
} else {
    Register-Criterion -Name "post_base_py_matches_source" -Pass $false -Detail "one or both files missing"
}

# config/app.json APP_NAME must NOT have been overwritten by pullbase (once-manifest skip behavior)
if (Test-Path $appConfigPath) {
    try {
        $appConfig = Get-Content $appConfigPath -Raw | ConvertFrom-Json
        $appName   = $appConfig.COMMON.APP_NAME
        Register-Criterion -Name "post_once_app_name_preserved" `
            -Pass ($appName -eq $expectedAppName) `
            -Detail "APP_NAME=$appName (expected '$expectedAppName')"
    } catch {
        Register-Criterion -Name "post_once_app_name_preserved" -Pass $false -Detail $_.Exception.Message
    }
} else {
    Register-Criterion -Name "post_once_app_name_preserved" -Pass $false -Detail "config/app.json missing"
}

# ---------- CLEANUP ----------

if (-not $KeepTemp) {
    if (Test-Path $tempParent) {
        Remove-Item -Recurse -Force $tempParent
        Write-Host ""
        Write-Host "Temp folder removed: $tempParent" -ForegroundColor DarkGray
    }
} else {
    Write-Host ""
    Write-Host "Temp folder kept (-KeepTemp): $tempParent" -ForegroundColor DarkGray
}

# ---------- SUMMARY ----------

$total = $script:passCount + $script:failCount
Write-Host ""
Write-Host "============================================================" -ForegroundColor White
$summaryColor = if ($script:failCount -eq 0) { "Green" } else { "Red" }
Write-Host "SUMMARY   PASS: $($script:passCount) / $total   FAIL: $($script:failCount) / $total" -ForegroundColor $summaryColor
Write-Host "============================================================" -ForegroundColor White

exit $(if ($script:failCount -gt 0) { 1 } else { 0 })
