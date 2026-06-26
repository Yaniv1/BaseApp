param(
    [Parameter(Mandatory = $true)]
    [string]$RepoRoot,
    [Parameter(Mandatory = $true)]
    [string]$TaskFile,
    [Parameter(Mandatory = $true)]
    [string]$CommitMessage,
    # Ledger branch the task store is synced on. Defaults to the currently
    # checked-out branch (preserving the manual "Sync Git" / startup behavior of
    # syncing the current branch). The headless status-inbox sync passes the
    # configured ledger branch (APP.TASK_MANAGER.ledger_branch, default "main")
    # so ledger updates never land on a worker's task branch.
    [string]$Branch = ''
)

$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------------------
# Status-sync ledger isolation (BASE-REQ-014.12, feature 3.7)
#
# The task ledger (build/tasks/<app>.json) is committed and pushed in COMPLETE
# isolation from the worker's primary working tree. While a worker is active the
# primary tree is checked out on its short-lived task/<id> branch and holds
# UNCOMMITTED worker code/spec/test changes; touching it here (e.g. 'git add -A',
# 'commit -a', or a 'git pull --rebase --autostash') would sweep that work into
# the server's commits, push it ahead of the engineer's approval gate, or drop it
# via autostash. To avoid that entirely we never stage/commit/stash/rebase in the
# primary tree: we read the server-updated ledger content from it and apply the
# commit inside a dedicated, reusable DETACHED git worktree built from the latest
# origin/<branch> tip. A detached worktree never locks a branch, so it works no
# matter which branch the primary tree is on, and it only ever contains the
# ledger change, so any rebase needed to integrate a moved remote is harmless.
# ---------------------------------------------------------------------------

function Invoke-Git {
    param([string[]]$GitArgs, [string]$WorkTree = $null)
    if ($WorkTree) {
        $full = @('-C', $WorkTree) + $GitArgs
    }
    else {
        $full = $GitArgs
    }
    $output = & git @full 2>&1
    return [pscustomobject]@{
        ExitCode = $LASTEXITCODE
        Output   = ($output | Out-String).Trim()
    }
}

$repoPath = (Resolve-Path -Path $RepoRoot).Path
$taskRel = ($TaskFile -replace '\\', '/').TrimStart('/')

Push-Location $repoPath
try {
    # Resolve the target ledger branch (default: current HEAD branch).
    if ([string]::IsNullOrWhiteSpace($Branch)) {
        $head = Invoke-Git @('rev-parse', '--abbrev-ref', 'HEAD')
        if ($head.ExitCode -ne 0) { throw "Unable to determine current branch: $($head.Output)" }
        $Branch = $head.Output.Trim()
        if ([string]::IsNullOrWhiteSpace($Branch) -or $Branch -eq 'HEAD') {
            throw "Could not resolve a target ledger branch (detached HEAD); pass -Branch explicitly."
        }
    }

    # Capture the server-updated ledger content from the PRIMARY working tree.
    $ledgerSource = Join-Path $repoPath $taskRel
    if (-not (Test-Path -LiteralPath $ledgerSource)) {
        throw "Ledger file not found in working tree: $ledgerSource"
    }
    $ledgerBytes = [System.IO.File]::ReadAllBytes($ledgerSource)

    # Locate (and create on first use) the dedicated ledger-sync worktree under
    # the git common dir so it persists and is reused across syncs.
    $commonDirResult = Invoke-Git @('rev-parse', '--git-common-dir')
    if ($commonDirResult.ExitCode -ne 0) { throw "Unable to resolve git common dir: $($commonDirResult.Output)" }
    $commonDir = $commonDirResult.Output.Trim()
    if (-not [System.IO.Path]::IsPathRooted($commonDir)) {
        $commonDir = (Resolve-Path -Path (Join-Path $repoPath $commonDir)).Path
    }
    $worktreePath = Join-Path $commonDir 'baseapp-ledger-sync'

    # Always refresh the remote tip for the target branch first.
    $fetch = Invoke-Git @('fetch', 'origin', $Branch)
    if ($fetch.ExitCode -ne 0) { throw "git fetch origin $Branch failed: $($fetch.Output)" }

    # Ensure the dedicated worktree exists and is healthy; (re)create otherwise.
    $needsCreate = $true
    if (Test-Path -LiteralPath $worktreePath) {
        $check = Invoke-Git @('rev-parse', '--is-inside-work-tree') $worktreePath
        if ($check.ExitCode -eq 0 -and $check.Output.Trim() -eq 'true') {
            $needsCreate = $false
        }
        else {
            # Stale/corrupt worktree dir: prune the registration and remove it.
            Invoke-Git @('worktree', 'remove', '--force', $worktreePath) | Out-Null
            Invoke-Git @('worktree', 'prune') | Out-Null
            if (Test-Path -LiteralPath $worktreePath) {
                Remove-Item -LiteralPath $worktreePath -Recurse -Force -ErrorAction SilentlyContinue
            }
        }
    }
    if ($needsCreate) {
        Invoke-Git @('worktree', 'prune') | Out-Null
        $add = Invoke-Git @('worktree', 'add', '--detach', $worktreePath, "origin/$Branch")
        if ($add.ExitCode -ne 0) { throw "Unable to create ledger-sync worktree: $($add.Output)" }
    }

    # Reset the isolated worktree to the latest remote tip (detached). This never
    # affects the primary working tree.
    $reset = Invoke-Git @('reset', '--hard', "origin/$Branch") $worktreePath
    if ($reset.ExitCode -ne 0) { throw "Unable to reset ledger-sync worktree to origin/${Branch}: $($reset.Output)" }
    Invoke-Git @('clean', '-fd') $worktreePath | Out-Null

    # Write the server-updated ledger content into the isolated worktree.
    $ledgerDest = Join-Path $worktreePath $taskRel
    $ledgerDestDir = Split-Path -Parent $ledgerDest
    if (-not (Test-Path -LiteralPath $ledgerDestDir)) {
        New-Item -ItemType Directory -Force -Path $ledgerDestDir | Out-Null
    }
    [System.IO.File]::WriteAllBytes($ledgerDest, $ledgerBytes)

    # Stage ONLY the ledger path and bail out early when nothing changed.
    $stage = Invoke-Git @('add', '--', $taskRel) $worktreePath
    if ($stage.ExitCode -ne 0) { throw "Unable to stage ledger in worktree: $($stage.Output)" }
    $staged = Invoke-Git @('status', '--porcelain', '--', $taskRel) $worktreePath
    if (-not $staged.Output) {
        Write-Output "No ledger changes to sync for $taskRel on $Branch"
        return
    }

    $commit = Invoke-Git @('commit', '-m', $CommitMessage, '--', $taskRel) $worktreePath
    if ($commit.ExitCode -ne 0) { throw "git commit failed for ${taskRel}: $($commit.Output)" }

    # Push the ledger commit to the target branch, retrying once after rebasing
    # the single ledger commit onto a moved remote tip. The rebase runs INSIDE
    # the isolated worktree, which contains only the ledger change, so it can
    # never disturb the worker's primary working tree.
    $pushed = $false
    for ($attempt = 0; $attempt -lt 2; $attempt++) {
        $push = Invoke-Git @('push', 'origin', "HEAD:refs/heads/$Branch") $worktreePath
        if ($push.ExitCode -eq 0) { $pushed = $true; break }

        $refetch = Invoke-Git @('fetch', 'origin', $Branch) $worktreePath
        if ($refetch.ExitCode -ne 0) { throw "git push failed and re-fetch failed for ${Branch}: $($push.Output); $($refetch.Output)" }
        $rebase = Invoke-Git @('rebase', "origin/$Branch") $worktreePath
        if ($rebase.ExitCode -ne 0) {
            Invoke-Git @('rebase', '--abort') $worktreePath | Out-Null
            throw "Unable to rebase ledger commit onto origin/$Branch (resolve manually): $($rebase.Output)"
        }
    }
    if (-not $pushed) { throw "git push failed for $taskRel on $Branch after retry." }

    Write-Output "Synced $taskRel to git on $Branch (isolated worktree)"
}
finally {
    Pop-Location
}
