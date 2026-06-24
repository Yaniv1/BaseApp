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

# Keep a deterministic console window title (the task title) so the task
# manager can trace and re-focus this window when the task later moves to the
# Ready review state, and so a human can tell the windows apart. The Copilot
# CLI rewrites the console title to an auto-generated session name while it
# runs, so a fast initial assignment is not enough: a background thread keeps
# re-asserting the desired title via the Win32 console API for as long as the
# session is alive.
$titleKeeperStarted = $false
if (-not [string]::IsNullOrWhiteSpace($WindowTitle)) {
    try { $Host.UI.RawUI.WindowTitle = $WindowTitle } catch { }
    try {
        if (-not ('TaskWindowTitleKeeper' -as [type])) {
            Add-Type -TypeDefinition @'
using System;
using System.Threading;
using System.Runtime.InteropServices;

public static class TaskWindowTitleKeeper
{
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool SetConsoleTitle(string lpConsoleTitle);

    private static Thread _thread;
    private static volatile bool _run;

    public static void Start(string title)
    {
        Stop();
        _run = true;
        _thread = new Thread(() =>
        {
            while (_run)
            {
                try { SetConsoleTitle(title); } catch { }
                Thread.Sleep(750);
            }
        });
        _thread.IsBackground = true;
        _thread.Start();
    }

    public static void Stop()
    {
        _run = false;
    }
}
'@
        }
        [TaskWindowTitleKeeper]::Start($WindowTitle)
        $titleKeeperStarted = $true
    }
    catch { }
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

# Stop re-asserting the title and set it one last time so the ended session's
# window still carries the task title.
if ($titleKeeperStarted) {
    try { [TaskWindowTitleKeeper]::Stop() } catch { }
}
if (-not [string]::IsNullOrWhiteSpace($WindowTitle)) {
    try { $Host.UI.RawUI.WindowTitle = $WindowTitle } catch { }
}
