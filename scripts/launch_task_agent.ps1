param(
    [string]$WorkspaceRoot,
    [string]$TaskId,
    [string]$PromptFile,
    [string]$TaskFile,
    [string]$CopilotCli,
    [string]$SessionName,
    [string]$WindowTitle,
    [string]$TaskBranch,
    [switch]$EnableFullRead,
    [switch]$EnableFullEdit,
    [switch]$EnableFullExecution
)

Set-Location -LiteralPath $WorkspaceRoot

# Work each task on its own short-lived, ad-hoc branch so that changes for
# multiple tasks worked on at the same time never mix together. The branch is
# created and checked out off the current HEAD before the worker session
# begins. Any uncommitted work in the tree is carried onto the new branch by
# the checkout. This is best-effort: if the workspace is not a git repository
# or the branch operation fails, a warning is emitted and the session still
# starts on the current branch.
if (-not [string]::IsNullOrWhiteSpace($TaskBranch)) {
    try {
        $insideRepo = (& git rev-parse --is-inside-work-tree 2>$null)
        if ($LASTEXITCODE -eq 0 -and $insideRepo -eq 'true') {
            $currentBranch = (& git rev-parse --abbrev-ref HEAD 2>$null)
            if ($currentBranch -eq $TaskBranch) {
                Write-Host "Already on task branch '$TaskBranch'." -ForegroundColor Green
            }
            else {
                & git rev-parse --verify --quiet $TaskBranch *> $null
                if ($LASTEXITCODE -eq 0) {
                    & git checkout $TaskBranch | Out-Host
                }
                else {
                    & git checkout -b $TaskBranch | Out-Host
                }
                if ($LASTEXITCODE -ne 0) {
                    Write-Warning "Unable to switch to task branch '$TaskBranch'; continuing on the current branch."
                }
                else {
                    Write-Host "Working this task on its own short-lived branch '$TaskBranch'." -ForegroundColor Green
                }
            }
        }
        else {
            Write-Warning "Workspace '$WorkspaceRoot' is not a git repository; skipping task branch creation."
        }
    }
    catch {
        Write-Warning "Task branch setup failed: $($_.Exception.Message). Continuing on the current branch."
    }
}

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
