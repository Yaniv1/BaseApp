param(
    [string]$WorkspaceRoot,
    [string]$TaskId,
    [string]$PromptFile,
    [string]$TaskFile,
    [string]$CopilotCli,
    [string]$SessionName,
    [string]$WindowTitle,
    [string]$TaskBranch,
    [string]$Worktree,
    [string]$McpConfig,
    [string]$ReviewConfig,
    [switch]$EnableFullRead,
    [switch]$EnableFullEdit,
    [switch]$EnableFullExecution
)

Set-Location -LiteralPath $WorkspaceRoot

# Work each task in its own dedicated git worktree so that multiple task agents
# can run in parallel, each on its own short-lived branch, in a physically
# separate checkout directory. A worktree gives the branch its own working tree
# that shares the repository's object store with `main` but never collides with
# other tasks' files (unlike an in-place `git checkout`, which can only have one
# branch checked out per working tree at a time). The session then runs inside
# that worktree directory. This is best-effort: if the workspace is not a git
# repository or the worktree operation fails, a warning is emitted and the
# session falls back to running on the current branch in the main working tree.
$sessionRoot = $WorkspaceRoot
if (-not [string]::IsNullOrWhiteSpace($Worktree) -and -not [string]::IsNullOrWhiteSpace($TaskBranch)) {
    try {
        $insideRepo = (& git rev-parse --is-inside-work-tree 2>$null)
        if ($LASTEXITCODE -eq 0 -and $insideRepo -eq 'true') {
            if (Test-Path -LiteralPath $Worktree) {
                # The worktree directory already exists (e.g. a re-launch or a
                # review of an in-flight task) - reuse it as-is.
                Write-Host "Reusing existing worktree for '$TaskBranch' at '$Worktree'." -ForegroundColor Green
                $sessionRoot = $Worktree
            }
            else {
                $parent = Split-Path -Parent $Worktree
                if (-not [string]::IsNullOrWhiteSpace($parent) -and -not (Test-Path -LiteralPath $parent)) {
                    New-Item -ItemType Directory -Path $parent -Force | Out-Null
                }
                # Reuse an existing branch if one is already present (local or
                # remote-tracking), otherwise create the branch with the worktree.
                & git rev-parse --verify --quiet $TaskBranch *> $null
                $localBranchExists = ($LASTEXITCODE -eq 0)
                & git rev-parse --verify --quiet "origin/$TaskBranch" *> $null
                $remoteBranchExists = ($LASTEXITCODE -eq 0)
                if ($localBranchExists) {
                    & git worktree add $Worktree $TaskBranch | Out-Host
                }
                elseif ($remoteBranchExists) {
                    & git worktree add -b $TaskBranch $Worktree "origin/$TaskBranch" | Out-Host
                }
                else {
                    & git worktree add -b $TaskBranch $Worktree | Out-Host
                }
                if ($LASTEXITCODE -ne 0) {
                    Write-Warning "Unable to create worktree '$Worktree' for branch '$TaskBranch'; continuing in the main working tree."
                }
                else {
                    Write-Host "Working this task in its own worktree '$Worktree' on branch '$TaskBranch'." -ForegroundColor Green
                    $sessionRoot = $Worktree
                }
            }
        }
        else {
            Write-Warning "Workspace '$WorkspaceRoot' is not a git repository; skipping task worktree creation."
        }
    }
    catch {
        Write-Warning "Task worktree setup failed: $($_.Exception.Message). Continuing in the main working tree."
    }
}
elseif (-not [string]::IsNullOrWhiteSpace($TaskBranch)) {
    # Legacy fallback: no worktree configured, so check the branch out in place
    # on the main working tree. NOTE: this prevents truly parallel task agents
    # because a working tree can only hold one branch at a time.
    try {
        $insideRepo = (& git rev-parse --is-inside-work-tree 2>$null)
        if ($LASTEXITCODE -eq 0 -and $insideRepo -eq 'true') {
            $currentBranch = (& git rev-parse --abbrev-ref HEAD 2>$null)
            if ($currentBranch -eq $TaskBranch) {
                Write-Host "Already on task branch '$TaskBranch'." -ForegroundColor Green
            }
            elseif (-not [string]::IsNullOrWhiteSpace($Worktree) -and (Test-Path -LiteralPath $Worktree)) {
                $sessionRoot = $Worktree
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

# Run the worker session from inside the task's worktree (when one was created)
# so all of its file edits, commits, and tool invocations are scoped to the
# isolated checkout rather than the shared main working tree.
Set-Location -LiteralPath $sessionRoot

$reviewProcess = $null
$reviewJob = $null
if (-not [string]::IsNullOrWhiteSpace($ReviewConfig)) {
    $review = $ReviewConfig | ConvertFrom-Json
    $reviewScript = Join-Path $sessionRoot "scripts\code_review.py"
    if ($review.enabled -and (Test-Path -LiteralPath $reviewScript)) {
        # The console owns this job, not the Task Manager server. Closing or
        # forcibly terminating this worker console also terminates its companion.
        if ($IsWindows) {
            if (-not ('TaskReviewJob' -as [type])) {
                Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public sealed class TaskReviewJob : IDisposable {
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode)] static extern IntPtr CreateJobObject(IntPtr a, string n);
    [DllImport("kernel32.dll")] static extern bool SetInformationJobObject(IntPtr j, int c, IntPtr p, uint s);
    [DllImport("kernel32.dll")] static extern bool AssignProcessToJobObject(IntPtr j, IntPtr p);
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr h);
    [StructLayout(LayoutKind.Sequential)] struct Basic {
        public long processTime, jobTime; public uint flags; public UIntPtr min, max;
        public uint active; public UIntPtr affinity; public uint priority, scheduling;
    }
    [StructLayout(LayoutKind.Sequential)] struct Extended {
        public Basic basic; public ulong readOps, writeOps, otherOps, readBytes, writeBytes, otherBytes;
        public UIntPtr processLimit, jobLimit, peakProcess, peakJob;
    }
    IntPtr handle;
    public TaskReviewJob() {
        handle=CreateJobObject(IntPtr.Zero,null);
        var info=new Extended(); info.basic.flags=0x2000;
        int size=Marshal.SizeOf(info); IntPtr p=Marshal.AllocHGlobal(size);
        try {
            Marshal.StructureToPtr(info,p,false);
            if(handle==IntPtr.Zero || !SetInformationJobObject(handle,9,p,(uint)size) ||
               !AssignProcessToJobObject(handle,System.Diagnostics.Process.GetCurrentProcess().Handle))
                throw new InvalidOperationException("Unable to establish worker-owned review job.");
        } finally { Marshal.FreeHGlobal(p); }
    }
    public void Dispose() { if(handle!=IntPtr.Zero){CloseHandle(handle);handle=IntPtr.Zero;} }
}
'@
            }
            $reviewJob = [TaskReviewJob]::new()
        }
        $healthFile = Join-Path $sessionRoot "build\review\$TaskId\server.json"
        $instanceId = [guid]::NewGuid().ToString("N")
        $python = if ($review.python) { [string]$review.python } else { "python" }
        $reviewArgs = @($reviewScript, "--root", $sessionRoot, "--task-id", $TaskId,
            "--port", "0", "--worker-pid", "$PID", "--instance-id", $instanceId,
            "--health-file", $healthFile, "--mode", [string]$review.mode)
        foreach ($entry in @(@("layer","--layer"), @("architecture","--architecture"),
            @("requirements","--requirements"), @("results_root","--results-root"),
            @("status_queue","--status-queue"), @("baseline_ref","--baseline-ref"))) {
            $value = $review.($entry[0])
            if ($value) { $reviewArgs += @($entry[1], [string]$value) }
        }
        $quotedReviewArgs = $reviewArgs | ForEach-Object { '"' + ([string]$_ -replace '"', '\"') + '"' }
        $reviewProcess = Start-Process -FilePath $python -ArgumentList $quotedReviewArgs -WorkingDirectory $sessionRoot -PassThru -NoNewWindow
        $healthy = $false
        for ($attempt = 0; $attempt -lt 120; $attempt++) {
            if ($reviewProcess.HasExited) { break }
            if (Test-Path -LiteralPath $healthFile) {
                try {
                    $state = Get-Content -LiteralPath $healthFile -Raw | ConvertFrom-Json
                    if ($state.instance_id -eq $instanceId) {
                        $health = Invoke-RestMethod -Uri ($state.url + "health") -TimeoutSec 1
                        if ($health.task_id -eq $TaskId -and $health.root -eq $sessionRoot -and
                            $health.worker_pid -eq $PID -and $health.instance_id -eq $instanceId) {
                            $healthy = $true
                            break
                        }
                    }
                } catch { }
            }
            Start-Sleep -Milliseconds 250
        }
        if (-not $healthy) {
            if (-not $reviewProcess.HasExited) { Stop-Process -Id $reviewProcess.Id -Force }
            throw "Interactive review companion failed health verification for $TaskId."
        }
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
if (-not [string]::IsNullOrWhiteSpace($McpConfig) -and (Test-Path -LiteralPath $McpConfig)) {
    Write-Host "Status-queue MCP server enabled (config: $McpConfig)." -ForegroundColor Green
}
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
# Register the per-agent status-queue MCP server (stdio) so the worker can
# request task ledger status/comment updates via the 'enqueue_status_update'
# tool. The config augments the user's ~/.copilot/mcp-config.json for this
# session only and is bound to this agent's lifecycle.
if (-not [string]::IsNullOrWhiteSpace($McpConfig) -and (Test-Path -LiteralPath $McpConfig)) {
    $copilotArgs += @("--additional-mcp-config", "@$McpConfig")
}
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
try {
    & $CopilotCli @copilotArgs
}
finally {
    if ($null -ne $reviewProcess -and -not $reviewProcess.HasExited) {
        Stop-Process -Id $reviewProcess.Id -Force -ErrorAction SilentlyContinue
    }
    # Do not dispose the job while its owner (this console) is still running:
    # the OS releases the handle on console exit, including hard shutdown.
}

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
