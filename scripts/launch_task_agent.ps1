param(
    [string]$WorkspaceRoot,
    [string]$TaskId,
    [string]$PromptFile,
    [string]$TaskFile,
    [string]$CopilotCli,
    [string]$SessionName
)

Set-Location -LiteralPath $WorkspaceRoot

Write-Host "Starting Copilot CLI task session for $TaskId"
Write-Host "Prompt file: $PromptFile"
Write-Host "Task file:   $TaskFile"
Write-Host ""
Write-Host "=== Task Prompt ===" -ForegroundColor Cyan
Get-Content -LiteralPath $PromptFile | Out-Host
Write-Host ""
Write-Host "=== Launching Copilot CLI ===" -ForegroundColor Green
Write-Host "Workspace edit permissions enabled for this session." -ForegroundColor Green
Write-Host ""

$initialPrompt = Get-Content -Raw -LiteralPath $PromptFile
& $CopilotCli `
    -C $WorkspaceRoot `
    --add-dir $WorkspaceRoot `
    --allow-all `
    --name $SessionName `
    -i $initialPrompt

Write-Host ""
Write-Host "Copilot CLI session ended. Type exit to close." -ForegroundColor Yellow
