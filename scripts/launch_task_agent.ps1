param(
    [string]$WorkspaceRoot,
    [string]$TaskId,
    [string]$PromptFile,
    [string]$TaskFile,
    [string]$CopilotCli,
    [string]$SessionName,
    [string]$WindowTitle,
    [switch]$EnableFullRead,
    [switch]$EnableFullEdit,
    [switch]$EnableFullExecution
)

Set-Location -LiteralPath $WorkspaceRoot

# Set a deterministic console window title so the task manager can trace and
# re-focus this window when the task later moves to the Ready review state.
if (-not [string]::IsNullOrWhiteSpace($WindowTitle)) {
    try { $Host.UI.RawUI.WindowTitle = $WindowTitle } catch { }
}

Write-Host "Starting Copilot CLI task session for $TaskId"
Write-Host "Prompt file: $PromptFile"
Write-Host "Task file:   $TaskFile"
Write-Host "Task file name: $(Split-Path -Leaf $TaskFile)"
Write-Host "Prompt file: $PromptFile"
Write-Host "Prompt file name: $(Split-Path -Leaf $PromptFile)"
Write-Host ""
Write-Host "=== Task Prompt ===" -ForegroundColor Cyan
Get-Content -LiteralPath $PromptFile | Out-Host
Write-Host ""
Write-Host "=== Launching Copilot CLI ===" -ForegroundColor Green
if ($EnableFullRead) {
    Write-Host "Full read permissions enabled for this session." -ForegroundColor Green
}
if ($EnableFullEdit) {
    Write-Host "Full edit permissions enabled for this session." -ForegroundColor Green
}
if ($EnableFullExecution) {
    Write-Host "Full execution permissions enabled for this session." -ForegroundColor Green
}
Write-Host ""

$copilotArgs = @("-i", $PromptFile)
if ($EnableFullRead) {
    $copilotArgs += @("--allow-all-tools", "--allow-all-paths", "--allow-all-urls")
}
if ($EnableFullEdit) {
    $copilotArgs += @("--allow-all")
}
if ($EnableFullExecution) {
    $copilotArgs += @("--allow-all-tools")
}
$quotedArgs = $copilotArgs | ForEach-Object { if ($_ -match '\s') { '"' + ($_ -replace '"', '\"') + '"' } else { $_ } }
Write-Host ("Executing: {0} {1}" -f $CopilotCli, ($quotedArgs -join ' ')) -ForegroundColor Yellow
& $CopilotCli @copilotArgs

Write-Host ""
Write-Host "Copilot CLI session ended." -ForegroundColor Yellow
